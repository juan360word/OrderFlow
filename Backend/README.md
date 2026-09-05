# OrderFlow — Backend

Distributed order processing platform. The interesting part of this project is
not the CRUD; it is the set of problems underneath it: concurrency, idempotency,
transactional consistency, and the security of an API that handles credentials
and stock.

Phases 0–6 are implemented: design, repository layout, FastAPI base,
PostgreSQL + Alembic, authentication, products, and inventory with real
concurrency control.

---

## Quick start

```bash
cp .env.example .env                  # then set a real SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"

make install                          # uv sync
make up                               # PostgreSQL + Redis in Docker
make migrate                          # apply the schema
make run                              # http://localhost:8000/docs
make test                             # 96 tests, real database
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
│   └── dependencies.py  DI, current user, RBAC
├── db/                  declarative base, mixins, model registry
├── modules/             business modules
│   ├── auth/            users, roles, sessions
│   ├── products/        catalogue
│   └── inventory/       stock, reservations, concurrency control
└── shared/              cross-module primitives (pagination)
```

Each module is layered `router → service → repository → database`.

**The rule that keeps the boundaries real:** a module talks to another module
only through its `service`. Never its repository, never its tables. The one
cross-module call today is `products → inventory` (creating a product seeds its
stock row), and it goes through `InventoryService`.

Why a monolith and not microservices: the hard problems here — reserving the
last unit, keeping stock and orders consistent — are *transactional*. Inside one
database they are a transaction. Across services they are a distributed saga
with compensating actions. The module boundaries are drawn so that a split
remains possible later, if it is ever justified.

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
selects one) and both are proven by the same tests:

```bash
make test-concurrency
```

Those tests open several real database sessions and run them with
`asyncio.gather`, so the operations genuinely interleave inside PostgreSQL.
One of them, `test_naive_implementation_oversells`, **asserts that the naive
version is broken** — the safe implementations only mean something if the race
they prevent is demonstrably real.

Trade-off between the two strategies:

* **Atomic conditional UPDATE** (default) — one round trip, lock held for
  microseconds. The whole decision must fit in a `WHERE` clause.
* **Pessimistic `FOR UPDATE`** — two round trips, lock held for the duration of
  the application logic, and other buyers queue. In exchange, any amount of
  logic can run while the row is locked.

---

## Security

| Concern | Approach |
|---|---|
| Password storage | Argon2id (memory-hard, OWASP first choice), per-hash salt, transparent cost upgrade on login |
| Sessions | 15-minute access JWT + 7-day refresh token stored **hashed**, rotated on every use, with reuse detection that revokes the whole family |
| Token forgery | Pinned algorithm (no `alg: none`), issuer + audience + `typ` claim verified |
| User enumeration | Identical response and comparable timing for unknown user, wrong password and disabled account |
| Brute force | Account lockout after 5 failures; the counter is incremented with an atomic `SET attempts = attempts + 1` |
| Authorization | Declared in the route signature (`AdminUser`), never buried in a handler |
| Mass assignment | `extra="forbid"` on every input schema — `{"role": "admin"}` is a 422 |
| SQL injection | Parameterised queries only; LIKE metacharacters in search input are escaped |
| Error leakage | RFC 9457 problem responses with a correlation ID; stack traces go to the logs only |
| Log leakage | structlog processor redacts password/token/secret keys |
| Resource exhaustion | Request body cap, page-size cap, statement and lock timeouts |
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

Money is `NUMERIC`, never a float: binary floating point cannot represent `0.10`
exactly, so `19.99 + 0.01` stops being `20.00`.

Timestamps are `timestamptz` written by the database clock, not by the
application — application clocks drift and differ between replicas.

---

## Testing

```
tests/
├── conftest.py                       fixtures, factories, truncation isolation
├── test_health.py                    probes, request IDs, security headers
├── core/test_security.py             hashing, JWT, production hardening
└── modules/
    ├── auth/test_auth_api.py         enumeration, lockout, rotation, reuse
    ├── products/test_products_api.py CRUD, RBAC, injection, precision
    └── inventory/
        ├── test_inventory_api.py     reserve / confirm / release lifecycle
        └── test_concurrency.py       real parallel sessions
```

Tests run against a real PostgreSQL, never SQLite: the behaviour under test
(CHECK constraints, `FOR UPDATE`, CITEXT, native enums) only exists in
PostgreSQL. Isolation is by `TRUNCATE` rather than a rolled-back wrapping
transaction, because the concurrency tests need several independent connections
committing against each other.

---

## Operations

```bash
make migrate                       # apply migrations
make revision m="add orders"       # autogenerate, then review by hand
uv run alembic downgrade -1        # roll back one revision
uv run alembic check               # fail if models drifted from migrations
make reset-db                      # DESTRUCTIVE: drop the volume, rebuild
```

An autogenerated migration is a **draft**. Alembic cannot infer extensions,
seed data, or `DROP TYPE` for a native enum — all three are hand-added in the
baseline revision, and CI runs `alembic check` to catch model drift.

Probes: `GET /health` (liveness, touches nothing) and `GET /health/ready`
(readiness, checks the database and returns 503 when degraded).

---

## Next phases

7. Orders — the transactional core, composing auth + products + inventory.
8. Idempotency keys — generalising what `inventory_reservations` does today.
9. Redis — cache-aside on the catalogue, rate limiting.
10. Background jobs — the expired-hold sweeper (`release_expired_holds` is
    already implemented and waiting for a scheduler).
11. Outbox pattern — never lose an event when the process dies.
