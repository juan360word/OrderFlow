# OrderFlow

A distributed order-processing platform: a modular-monolith REST API in
FastAPI, with asynchronous event delivery through a transactional outbox and a
queue consumer, plus a React storefront on top of it.

The interesting problems here are on the server. Selling a physical item is a
concurrency problem wearing a CRUD costume — two customers can buy the last
unit at the same instant, a client can retry a payment request it never saw the
answer to, a message broker can go down between writing an order and announcing
it. This repository is an attempt to answer those cases explicitly, in code,
with tests that reproduce them rather than assume they do not happen.

> The frontend exists so the API can be used by a human. The engineering
> weight is in `Backend/`.

---

## Table of contents

- [Architecture](#architecture)
- [The problems this solves](#the-problems-this-solves)
  - [Overselling under concurrency](#1-overselling-under-concurrency)
  - [Retried requests placing duplicate orders](#2-retried-requests-placing-duplicate-orders)
  - [The dual-write problem](#3-the-dual-write-problem)
  - [At-least-once delivery](#4-at-least-once-delivery)
  - [Illegal order transitions](#5-illegal-order-transitions)
  - [Authentication that survives a stolen token](#6-authentication-that-survives-a-stolen-token)
- [Domain model](#domain-model)
- [API](#api)
- [Running it](#running-it)
- [Testing](#testing)
- [Quality gates](#quality-gates)
- [Project layout](#project-layout)
- [Design decisions worth defending](#design-decisions-worth-defending)

---

## Architecture

A **modular monolith**, not microservices. The modules — `auth`, `products`,
`inventory`, `orders`, `idempotency`, `outbox` — are separated the way services
would be: each owns its tables, exposes a service class, and may only reach
another module through that class, never through its repository or its tables.
The dependency graph points one way (`orders → products → inventory`) and never
back.

The payoff is that placing an order — pricing the basket, reserving stock
across several products, writing the order, staging its event — happens inside
**one database transaction**. Across a service boundary the same guarantee
needs a saga, compensating actions and a lot of hope. Because the boundaries
are real, any module could still be extracted later; because they are not yet
network calls, the atomicity is free today.

```
                       ┌──────────────┐
   React SPA  ────────▶│  FastAPI     │  synchronous:
   (Vite, TS)          │  api         │  auth · catalogue · stock · orders
                       └──────┬───────┘
                              │ one transaction per request
                              ▼
                    ┌────────────────────┐        ┌──────────┐
                    │  PostgreSQL 17     │◀──────▶│  Redis   │ cache-aside
                    │  · business tables │        │          │ rate limits
                    │  · outbox_events   │        └──────────┘
                    │  · processed_events│
                    └─────────┬──────────┘
                              │ polled
                              ▼
                       ┌──────────────┐        ┌──────────────┐
                       │  relay       │───────▶│  SQS         │
                       │  (publisher) │        │ (LocalStack) │
                       └──────────────┘        └──────┬───────┘
                                                      │
                                               ┌──────▼───────┐
                                               │  worker      │ idempotent
                                               │  (consumer)  │ handlers
                                               └──────────────┘
```

**Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL 17 ·
Redis · SQS · Alembic · Pydantic v2 · structlog · pytest · Docker Compose.
Dependencies are managed with `uv`.

---

## The problems this solves

### 1. Overselling under concurrency

One unit left, two simultaneous buyers. Read-then-write loses here: both
transactions read `quantity_available = 1`, both decide it is enough, both
write `0`, and two customers own the same item.

Stock is therefore never decremented from a value the application read.
The default strategy is a **conditional atomic update** — the check and the
write are the same statement, and the database decides:

```sql
UPDATE inventory
   SET quantity_available = quantity_available - :qty
 WHERE product_id = :pid
   AND quantity_available >= :qty        -- the guard lives inside the write
```

Zero rows updated means insufficient stock, atomically and without a lock held
across a round trip. A `pessimistic` strategy using `SELECT … FOR UPDATE` is
also implemented and switchable via `INVENTORY_LOCKING_STRATEGY`, because the
comparison is the point: the same test suite passes against both.

**Deadlocks** are prevented separately. An order for products A and B, racing
an order for B and A, would have each transaction holding the row the other
needs. Reservations are therefore always taken **sorted by product id**, so a
cycle cannot form.

Reserving is not selling. Stock moves `available → reserved → sold`, so a
pending order holds units without them having left the warehouse, and
cancelling it puts them back — released if the order was pending, restocked
with a compensating movement if it had already been confirmed. Every movement
is written to a reservation ledger, so the counters can always be explained.

Covered by `tests/modules/inventory/test_concurrency.py` and
`tests/modules/orders/test_order_concurrency.py`, which spawn genuinely
concurrent sessions rather than simulating them.

### 2. Retried requests placing duplicate orders

A client sends `POST /orders`, the connection drops, and the client has no way
to know whether the order exists. Retrying may charge the customer twice; not
retrying may lose the sale.

`POST /orders` accepts an **`Idempotency-Key` header**. The key is claimed by
inserting a row with a unique constraint — the claim is the insert, so two
concurrent retries cannot both win — and the response of the first successful
call is stored against it. A repeat returns the original order with
`Idempotent-Replay: true`, without reserving stock a second time.

The stored key is bound to a fingerprint of the request. Reusing the same key
with a *different* payload is a client bug, and it is rejected with 422 rather
than silently returning the wrong order. A request that fails does not burn the
key, so a genuine retry after an error still works.

### 3. The dual-write problem

An order is committed to PostgreSQL, then an event is published to the broker.
If the broker is unreachable in between, the order exists and nothing
downstream will ever hear about it — no confirmation email, no fulfilment. The
two writes are not atomic and no amount of retry logic makes them so.

The fix is the **transactional outbox**: the event is written to
`outbox_events` in the *same transaction* as the order. Either both land or
neither does. A separate **relay** process then polls that table and publishes
to SQS, marking each row as it goes, with exponential backoff and an attempt
budget after which an event is parked as failed instead of consuming the relay
forever.

This trades immediate delivery for guaranteed delivery — the event may arrive
a second later, but it cannot vanish. `EVENT_DELIVERY_MODE=direct` disables the
outbox for comparison, and `tests/modules/outbox/test_outbox.py` proves the
difference by taking the broker down mid-flight.

### 4. At-least-once delivery

SQS delivers a message at least once, so a consumer that is not idempotent will
eventually apply the same event twice.

Before doing any work, the consumer **claims the event id in
`processed_events`**. A claim that fails means the event was already handled and
the message is simply acknowledged. The claim and the handler share one
transaction, so a handler that raises rolls the claim back and the message
returns to the queue for a genuine retry. Malformed messages are rejected
outright instead of being retried forever.

### 5. Illegal order transitions

An order's lifecycle is an explicit state machine (`pending → confirmed →
cancelled`, with a terminal `cancelled`) rather than a status column anyone can
set. Every transition goes through `assert_can_transition`, and the error tells
the caller which moves *are* legal.

The race that matters is a cancel arriving at the same time as a confirm:
without protection both would read `pending`, both would pass the check, and
the stock would be released *and* shipped. Transitions therefore load the order
`FOR UPDATE`, so the second one sees the first one's result.

### 6. Authentication that survives a stolen token

Passwords are hashed with **Argon2id**. Access tokens are short-lived JWTs (15
minutes) and refresh tokens are long-lived, rotated on every use, and stored
only as a keyed hash — a database leak does not hand over usable sessions.

A JWT is a snapshot and cannot know that an account was disabled thirty seconds
after it was issued, so the user row is still loaded on every authenticated
request: one indexed primary-key lookup is the right price for being able to
revoke. Changing the password ends every session, and `logout-all` exists for
the same reason.

Authorization is declared in the route signature (`AdminUser`) rather than
checked inside the handler, so the permission of every endpoint is visible
without reading its body, and a forgotten check shows up in the diff.

Around all of it: login and order rate limits in Redis, request body size
limits, `nosniff`/CSP/HSTS security headers, trusted-host and CORS allowlists,
and errors in **RFC 7807 Problem Details** format carrying a `request_id` that
matches the structured logs.

---

## Domain model

Fourteen tables. The ones worth knowing:

| Table | Notes |
|---|---|
| `users`, `roles`, `refresh_tokens` | Accounts, RBAC, rotating sessions |
| `products`, `product_images` | Catalogue; pictures typed from their own bytes, never the browser's claim |
| `inventory`, `inventory_reservations` | Counters plus the ledger that explains them |
| `orders`, `order_items` | Line items freeze SKU, name and unit price at purchase time |
| `order_shipping_addresses` | Delivery address as a snapshot of the order, not a link to a profile |
| `idempotency_keys` | Claimed slots and stored responses |
| `outbox_events`, `processed_events` | Guaranteed delivery in, exactly-once effects out |

Two habits run through the schema. **Money and history are frozen, not
referenced**: an order item keeps the price it was sold at, so re-pricing a
product cannot rewrite what a customer paid. And **the database enforces what
must never be false** — `CHECK` constraints for non-negative stock, ISO-4217
currencies and status/timestamp agreement, `ON DELETE RESTRICT` so a product
that appears in an order cannot be erased. Application bugs cannot corrupt what
the schema refuses to store.

Primary keys are UUIDs: `/orders/1` and `/orders/2` leak sales volume and
invite enumeration.

---

## API

Interactive documentation runs at `/docs` (Swagger UI) and `/redoc`. The OpenAPI
schema is generated from the code, so it cannot drift from it.

| Method | Path | Access |
|---|---|---|
| `POST` | `/api/v1/auth/register` · `/login` · `/refresh` · `/logout` · `/logout-all` · `/change-password` | public / authenticated |
| `GET` | `/api/v1/auth/me` | authenticated |
| `GET` | `/api/v1/products` · `/products/{id}` · `/products/{id}/image` | public |
| `POST` `PATCH` `DELETE` | `/api/v1/products` and `/products/{id}` (+ `/image`) | admin |
| `GET` | `/api/v1/inventory/{product_id}` | public |
| `POST` | `/api/v1/inventory/reservations` (+ `/confirm`, `/release`), `/inventory/{id}/adjust` | admin |
| `POST` `GET` | `/api/v1/orders`, `/orders`, `/orders/{id}` | authenticated (owner-scoped) |
| `POST` | `/api/v1/orders/{id}/cancel` · `/confirm` | owner / admin |
| `GET` | `/health` · `/health/ready` | public |

Reads of the catalogue are public; writes are admin-only. Orders are scoped to
their owner by a `WHERE` clause rather than a check after the fact, and a
non-owner gets **404 rather than 403** — a 403 would confirm that the order
exists, which is exactly what an IDOR probe is looking for.

Deleting a product is two operations behind one verb: the default withdraws it
from sale (a soft delete, because order history points at the row), while
`?permanent=true` erases it and lets the database refuse if it was ever
ordered.

---

## Running it

### Everything in Docker

```bash
cd Backend
cp .env.example .env          # then set SECRET_KEY
docker compose up -d --build  # api, relay, worker, postgres, redis, localstack
docker compose exec api python -m orderflow.seed
```

The API is on `http://localhost:8000` (`/docs` for Swagger). Migrations run
automatically as their own compose service before the API starts.

### Backend locally, infrastructure in Docker

```bash
cd Backend
make install      # uv sync --all-extras
make up           # postgres, redis, localstack only
make migrate
make seed         # demo accounts
make run          # uvicorn with hot reload, port 8000

make relay        # in a second terminal
make worker       # in a third
```

`make help` lists every target.

### Frontend

```bash
cd Frontend
npm install
npm run dev       # http://localhost:5173, proxies /api to :8000
```

Demo accounts created by `make seed`: `admin@orderflow.io` and
`cliente@orderflow.io`.

---

## Testing

**301 tests**, all against real PostgreSQL and Redis. Behaviour under test —
`CHECK` constraints, row locks, `CITEXT`, Lua scripts, deadlock ordering — does
not exist in a mock, and a suite that mocks the database proves only that the
mocks agree with each other.

```bash
make test                # the whole suite
make test-concurrency    # only the genuinely concurrent tests
make test-containers     # against throwaway containers (testcontainers)
```

Tests are labelled so a category can be run on its own (`pytest -m unit`).
Labels overlap, since an API test is also an integration test:

| Marker | Tests | What it means |
|---|---|---|
| `unit` | 59 | Pure logic. No database, no network |
| `integration` | 85 | Real PostgreSQL and Redis |
| `api` | 157 | End-to-end HTTP through the full middleware stack |
| `concurrency` | 15 | Spawns genuinely concurrent database sessions |
| `idempotency` | 12 | Proves a retried request does the work once |

The tests worth reading first are the ones that reproduce the failures the
architecture exists to prevent: `test_order_concurrency.py` (two buyers, one
unit; opposing multi-line orders that would deadlock), `test_idempotency.py` (a
retry that must not reserve stock twice), and `test_outbox.py` (the broker
falls over and the event survives anyway).

---

## Quality gates

```bash
make check    # lint + typecheck + tests, exactly what CI runs
```

- **`ruff`** with a curated rule set — correctness, plus the security (`S`) and
  async (`ASYNC`) rules that matter for a service handling money and
  credentials.
- **`mypy --strict`**, no untyped definitions, `warn_unreachable` on.
- **GitHub Actions** runs the same three steps against real PostgreSQL and
  Redis service containers on every push and pull request.

---

## Project layout

```
Backend/
├── src/orderflow/
│   ├── core/            config, database, security, cache, rate limiting,
│   │                    middleware, error contract, structured logging
│   ├── db/              declarative base, naming conventions, mixins
│   ├── modules/
│   │   ├── auth/        users, roles, tokens, sessions
│   │   ├── products/    catalogue and pictures
│   │   ├── inventory/   stock, reservations, the concurrency strategies
│   │   ├── orders/      pricing, the state machine, shipping addresses
│   │   ├── idempotency/ claimed keys and stored responses
│   │   └── outbox/      staged events and the relay's batch logic
│   ├── worker/          queue consumer, outbox relay, queue bootstrap
│   └── shared/          domain events, pagination
├── migrations/          Alembic revisions
├── tests/               unit · integration · api · concurrency · idempotency
└── docker-compose.yml   db · cache · queue · migrate · api · relay · worker

Frontend/
└── src/                 React 19 + TypeScript, React Query, React Hook Form
                         + Zod, Framer Motion. Catalogue, cart, checkout with
                         shipping details, order history, and admin screens for
                         products, inventory and metrics.
```

Every module follows the same four-file shape — `models` (tables), `repository`
(queries), `service` (rules), `router` (HTTP) — plus `schemas` for its
contracts. Business rules live in the service layer and nowhere else, so a
rule is enforced identically whether it is reached from HTTP, from the worker,
or from a test.

---

## Design decisions worth defending

Some of these are trade-offs rather than obvious wins. The reasoning is in the
code, next to the decision, but the summary:

- **Modular monolith over microservices.** The atomicity of placing an order is
  worth more than independent deployability at this scale, and the module
  boundaries keep the door open.
- **Offset pagination, capped at 100.** It is what an admin table with page
  numbers needs. Keyset pagination is better for infinite scroll and cannot
  jump to page 47. The cap is the security-relevant part: without it,
  `?limit=1000000` is a one-request denial of service.
- **Cache-aside on the product read path, with stock deliberately excluded.**
  A product is read far more often than it changes; stock changes on every
  purchase, and a stale count would promise availability that no longer exists.
  Invalidation deletes the key rather than overwriting it, so two concurrent
  updates cannot leave the loser's value behind.
- **UUID primary keys**, at the cost of 16 bytes and some index fragmentation,
  to avoid enumerable ids.
- **Fixed-window rate limiting.** Simple and cheap, and it admits its flaw:
  the true short-term ceiling is twice the limit across a window boundary. A
  sliding window is the upgrade when that matters.
- **Server-side timestamps** (`now()`, `onupdate`), because application clocks
  drift and rows written by a migration would otherwise have none.
- **Comments explain why, not what.** Anything surprising — a lock order, a
  soft delete, a 404 that could have been a 403 — carries the reasoning that
  makes it deliberate rather than accidental.
