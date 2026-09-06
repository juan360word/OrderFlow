# OrderFlow — Backend

Distributed order processing platform. The interesting part is not the CRUD; it
is the set of problems underneath it: concurrency, idempotency, transactional
consistency, asynchronous delivery guarantees, and the security of an API that
handles credentials, stock and money.

Phases 0–13 are implemented.

---

## Quick start

```bash
cp .env.example .env                  # then set a real SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"

make install                          # uv sync
make up                               # PostgreSQL, Redis and LocalStack
make migrate                          # apply the schema
make queues                           # create the SQS queues
make run                              # http://localhost:8000/docs
make test                             # 230 tests against real services
```

Or run the whole system in containers:

```bash
make up-all                           # api + relay + worker + db + cache + queue
```

`make help` lists every target.

---

## Architecture

A **modular monolith**: one deployment, hard internal boundaries.

```
src/orderflow/
├── main.py              application factory, middleware, lifespan
├── api/                 health probes, v1 router aggregation
├── core/                infrastructure — no business logic lives here
│   ├── config.py        typed settings, refuses unsafe production config
│   ├── logging.py       structlog JSON + secret redaction
│   ├── errors.py        error hierarchy → RFC 9457 problem+json
│   ├── middleware.py    request IDs, body limits, security headers
│   ├── security.py      Argon2id hashing, JWT issue/verify
│   ├── database.py      async engine, session, unit of work
│   ├── cache.py         Redis cache-aside, stampede control
│   ├── rate_limit.py    fixed-window limiter (Lua, atomic)
│   ├── messaging.py     SQS publisher/consumer, queue + DLQ setup
│   └── dependencies.py  DI, current user, RBAC
├── db/                  declarative base, mixins, model registry
├── modules/             business modules
│   ├── auth/            users, roles, sessions
│   ├── products/        catalogue
│   ├── inventory/       stock, reservations, concurrency control
│   ├── orders/          the transactional core
│   ├── idempotency/     safe retries
│   └── outbox/          transactional outbox + consumer dedup
├── worker/              separate processes: outbox relay, queue consumer
└── shared/              cross-module primitives (events, pagination)
```

Each module is layered `router → service → repository → database`.

**The rule that keeps the boundaries real:** a module talks to another module
only through its `service`. Never its repository, never its tables. The
dependencies point one way — `orders → products`, `orders → inventory`,
`products → inventory` — so there are no import cycles and any module could be
extracted later.

Why a monolith and not microservices: the hard problems here — reserving the
last unit, keeping an order and its stock consistent — are *transactional*.
Inside one database they are a transaction. Across services they are a
distributed saga with compensating actions.

---

## The core problem: overselling

With one unit in stock and two simultaneous buyers, the naive implementation
sells it twice:

```
T1: SELECT available -> 1          T2: SELECT available -> 1
T1: 1 >= 1, proceed                T2: 1 >= 1, proceed
T1: UPDATE available = 0           T2: UPDATE available = 0
```

A transaction alone does **not** fix this. PostgreSQL's default isolation level
(READ COMMITTED) guarantees each *statement* sees a consistent snapshot — not
that a value read earlier is still true when you write it.

Three defences, in order:

| Layer | Mechanism | Role |
|---|---|---|
| Application | `UPDATE ... WHERE quantity_available >= :qty` | Check and write in one indivisible statement |
| Application | `SELECT ... FOR UPDATE` | Serialise the read-modify-write when the decision needs more logic |
| Database | `CHECK (quantity_available >= 0)` | Last line of defence — code can be wrong, a constraint cannot |

Both application strategies are implemented (`INVENTORY_LOCKING_STRATEGY`
selects one) and both are proven by the same tests. One of those tests,
`test_naive_implementation_oversells`, **asserts that the naive version is
broken** — the safe implementations only mean something if the race they
prevent is demonstrably real.

**Deadlocks.** A multi-line order locks several rows. If order A takes `[X, Y]`
and order B takes `[Y, X]`, each holds what the other needs. `OrderService`
sorts lines by product id before reserving, so every transaction takes locks in
the same sequence and the cycle is impossible.

---

## Idempotency: surviving a retry

Networks deliver *at least once*. A client that times out cannot tell whether
the server never saw the request or processed it and lost the reply, so it
retries. Without protection, that retry places a second order.

`POST /orders` accepts an `Idempotency-Key` header:

```bash
curl -X POST localhost:8000/api/v1/orders \
  -H "Idempotency-Key: 5f3a…" -H "Authorization: Bearer …" \
  -d '{"items":[{"product_id":"…","quantity":2}]}'
```

A repeat returns the original order with `Idempotent-Replay: true`.

What makes it work:

* **`UNIQUE (user_id, key)`.** The same constraint does two jobs. It deduplicates,
  and it serialises: in PostgreSQL an `INSERT` colliding with a *still
  uncommitted* one blocks until that transaction finishes. Two simultaneous
  retries cannot both proceed.
* **`ON CONFLICT DO NOTHING`.** A plain `INSERT` would raise, and an error
  poisons the whole PostgreSQL transaction — we could no longer read the stored
  response. This returns zero rows instead.
* **The stored response, not a marker.** Recording only "seen this key" would
  make the retry return an empty acknowledgement and the client would never
  learn its order id.
* **A request fingerprint.** The same key with a different body is rejected with
  422, so a changed basket cannot silently receive the old answer.
* **Rollback on failure.** The key row lives in the request transaction, so a
  failed order frees the key. A key that survived a failure would make a
  transient error permanent.

Note that `POST /orders` is *not* naturally idempotent, which is why the caller
must supply the identity. `GET /products/1` and `DELETE` need no key.

---

## Cache and rate limiting

`GET /products/{id}` is served cache-aside: look in Redis, on a miss read
PostgreSQL and populate with a TTL.

* **Invalidation** is an explicit `DEL` on every write — delete, not overwrite,
  because two concurrent updates could otherwise leave the cache holding the
  losing value.
* **Stampede** control: TTL jitter so keys written together do not expire
  together, plus a short `SET NX` single-flight lock so one reader rebuilds and
  the rest read through instead of queueing.
* **Stock is deliberately not cached.** It changes on every purchase, and a
  stale count would promise availability that no longer exists.
* **Redis is optional.** Every cache operation degrades to a miss if it is down;
  the request still succeeds.

Login and registration are rate limited per IP with a fixed window, implemented
as a Lua script so `INCR` and `EXPIRE` are atomic. Issued as two round trips, a
crash between them leaves a counter with no expiry that blocks the caller
forever.

**The limiter fails open.** If Redis is down, requests are allowed. Failing
closed would turn a cache outage into a total outage. That is only defensible
because login has a second, independent defence that does not need Redis: the
per-account lockout counter in PostgreSQL.

---

## Asynchronous delivery: the dual write problem

Sending a confirmation email inside `POST /orders` makes the customer wait for
something irrelevant to their purchase. Publishing an event instead lets the API
answer as soon as the order is durable.

But this is broken:

```
1. COMMIT the order in PostgreSQL
2. publish OrderCreated to SQS
3. the process dies between 1 and 2
```

The order exists, the event does not, and nothing can detect it. Reversing the
order is no better. **No ordering of two independent systems is atomic.**

The **transactional outbox** removes the second system from the critical path.
The event is written to `outbox_events` *in the same transaction as the order*,
so the database's own atomicity covers both. A separate relay process moves rows
to SQS and retries with exponential backoff until it succeeds.

```
POST /orders ──┬─ orders            ┐
               ├─ order_items       │ one transaction
               ├─ inventory         │
               └─ outbox_events     ┘
                        │
                  OutboxRelay ──► SQS ──► EventConsumer
                                            │
                                      processed_events (dedup)
```

**What it guarantees, precisely:** the event is published **at least once**. If
the relay dies after SQS accepts the message but before marking the row, the row
is still pending and will be published again. Exactly-once delivery is not
achievable, which is why the consumer deduplicates by inserting into
`processed_events` before doing any work — and does it in the same transaction
as the handler, so a failure rolls the claim back and the event is genuinely
retried.

Both modes are implemented and `EVENT_DELIVERY_MODE` selects one.
`test_direct_dispatch_loses_the_event_when_the_broker_is_down` demonstrates the
failure; `test_the_outbox_keeps_the_event_when_the_broker_is_down` demonstrates
the fix under identical conditions.

Retries and the dead letter queue are configured on the queue itself: after
`SQS_MAX_RECEIVE_COUNT` failed attempts SQS moves the message to the DLQ.
Without that, a message the worker can never process — a poison pill — would be
redelivered forever.

The worker runs as its **own process**, not a thread in the API: a wedged
consumer cannot add latency to a request, and a backlog is absorbed by
`docker compose up -d --scale worker=4`.

---

## Security

| Concern | Approach |
|---|---|
| Password storage | Argon2id (memory-hard, OWASP first choice), per-hash salt, transparent cost upgrade on login |
| Sessions | 15-minute access JWT + 7-day refresh token stored **hashed**, rotated on every use, with reuse detection that revokes the whole family |
| Token forgery | Pinned algorithm (no `alg: none`), issuer + audience + `typ` claim verified |
| User enumeration | Identical response and comparable timing for unknown user, wrong password and disabled account |
| Brute force | Redis rate limit per IP, plus per-account lockout after 5 failures incremented atomically in SQL |
| Authorization | Declared in the route signature (`AdminUser`), never buried in a handler |
| IDOR | Ownership is a `WHERE` clause; a non-owner gets 404, not 403 — a 403 would confirm the resource exists |
| Mass assignment | `extra="forbid"` on every input schema — `{"role": "admin"}` is a 422 |
| SQL injection | Parameterised queries only; LIKE metacharacters in search input are escaped |
| Error leakage | RFC 9457 problem responses with a correlation ID; stack traces go to the logs only |
| Log leakage | structlog processor redacts password/token/secret keys; rate-limit keys store a digest, not the client IP |
| Resource exhaustion | Request body cap, page-size cap, statement and lock timeouts, bounded basket size |
| Transport | Security headers on every response; HSTS in production |
| Configuration | Production boot fails on debug mode, default passwords, wildcard hosts or wildcard CORS |
| Container | Multi-stage build, non-root user, no build tools in the runtime image |

---

## Data model

| Table | Purpose | Key guarantees |
|---|---|---|
| `roles` | Permission sets | `UNIQUE(name)`, seeded by migration |
| `users` | Accounts | `CITEXT UNIQUE` email, `ON DELETE RESTRICT` role FK |
| `refresh_tokens` | Revocable sessions | Hash only, rotation chain, partial index on live rows |
| `products` | Catalogue | `NUMERIC(12,2)` prices (never float), soft delete |
| `inventory` | Stock counters | `CHECK (quantity_available >= 0)`, split available/reserved |
| `inventory_reservations` | Hold ledger | `UNIQUE(reference, product_id)` → idempotent reserve |
| `orders` | Order headers | Status/timestamp consistency enforced by CHECK |
| `order_items` | Order lines | Price snapshot, `subtotal` a generated column |
| `idempotency_keys` | Safe retries | `UNIQUE(user_id, key)` → dedup *and* serialisation |
| `outbox_events` | Staged events | Written in the business transaction; relayed separately |
| `processed_events` | Consumer dedup | PK `(event_id, consumer)` → exactly-once processing |

Money is `NUMERIC`, never a float: binary floating point cannot represent `0.10`
exactly, so `19.99 + 0.01` stops being `20.00`. `order_items.subtotal` is
`GENERATED ALWAYS AS (quantity * unit_price) STORED` — the database keeps it
consistent, no code path can drift.

Order lines snapshot the SKU, name and price at purchase time. Referencing the
live product would let a price change rewrite the history of an old order.

---

## Testing

```
tests/
├── conftest.py                       fixtures, factories, backend selection
├── unit/                             pure logic — no DB, no Redis, no HTTP
├── core/                             security primitives, cache, rate limiting
├── worker/                           consumer retries, dedup, DLQ path
└── modules/{auth,products,inventory,orders,idempotency,outbox}/
```

The pyramid, by marker:

| Marker | Count | What it proves |
|---|---:|---|
| `unit` | 59 | Business rules, in 0.15s, with nothing running |
| `api` | 93 | Full HTTP path through middleware, auth and routing |
| `integration` | 78 | Real PostgreSQL and Redis behaviour |
| `concurrency` | 15 | Races actually resolve correctly |
| `idempotency` | 12 | A retry does the work exactly once |

```bash
make test                    # everything
uv run pytest -m unit        # no services needed at all
make test-concurrency        # the races
make test-containers         # throwaway containers via testcontainers
```

Two things worth knowing about how this suite is built:

* **Real services, never mocks.** The behaviour under test — CHECK constraints,
  `FOR UPDATE`, `ON CONFLICT`, CITEXT, Lua atomicity — only exists in the real
  thing. A SQLite stand-in would make the concurrency tests pass while proving
  nothing. `ORDERFLOW_TEST_BACKEND=testcontainers` starts throwaway containers;
  the default reuses the compose stack.
* **Migrations, not `create_all`.** The schema under test is produced by running
  the real migration chain, extensions and seed rows included. A `create_all`
  suite passes happily while the migrations are broken.

Isolation is by `TRUNCATE` rather than a rolled-back wrapping transaction,
because the concurrency tests need several independent connections committing
against each other. For the same reason the test engine uses `NullPool`: a
shared pool could silently serialise "concurrent" sessions and make those tests
vacuous.

---

## Operations

```bash
make migrate                       # apply migrations
make revision m="add refunds"      # autogenerate, then review by hand
uv run alembic downgrade -1        # roll back one revision
uv run alembic check               # fail if models drifted from migrations
make reset-db                      # DESTRUCTIVE: drop the volume, rebuild
make queues                        # create the SQS queues and DLQ
make relay / make worker           # run either worker locally
```

An autogenerated migration is a **draft**. Alembic cannot infer extensions, seed
data, or `DROP TYPE` for a native enum — all three are hand-added, and CI runs
`alembic check` plus a full downgrade/upgrade round trip.

Probes: `GET /health` (liveness, touches nothing) and `GET /health/ready`
(readiness, checks PostgreSQL and Redis, returns 503 when degraded).

---

## What is deliberately not done yet

* Payments are simulated; `POST /orders/{id}/confirm` stands in for the provider
  callback and is admin-only.
* The notification handler logs instead of sending. The phase was about the
  delivery machinery around it, not the provider integration.
* No AWS deployment, Terraform or CI/CD to a real environment — that is the next
  block of work.
