# CLAUDE.md — Contexto del proyecto OrderFlow

> Este archivo le da a Claude Code el contexto completo del proyecto.
> Guárdalo en la raíz del repositorio. Claude Code lo lee automáticamente al arrancar.

---

## 0. INSTRUCCIONES DE COMPORTAMIENTO (LEER PRIMERO — SON LAS MÁS IMPORTANTES)

Este es un proyecto de **aprendizaje y portafolio**, no un proyecto para "terminar rápido".
El dueño del proyecto es un estudiante de Ingeniería de Sistemas que quiere conseguir una
práctica como Backend Developer. Su objetivo NO es tener la app funcionando; es **entender
cada decisión para poder defenderla en una entrevista técnica**.

Por lo tanto, cuando trabajes en este proyecto DEBES:

1. **Explicar ANTES de escribir código.** Di qué vas a hacer y por qué, antes de crear archivos.
2. **Explicar el PORQUÉ de cada decisión técnica.** Si usas una librería, un patrón o una
   estructura, justifica por qué esa y no otra, y menciona el costo/trade-off.
3. **No volcar código masivo de una sola vez.** Trabaja en pasos pequeños y verificables.
4. **Después de cada cambio, hacer que el usuario lo pruebe** antes de seguir.
5. **No avanzar a la siguiente fase hasta que la anterior funcione** y el usuario confirme
   que entendió.
6. **Explicar el código que generes**, línea por línea o bloque por bloque cuando sea nuevo.
7. **Preguntar si algo no quedó claro** en lugar de asumir.
8. Cuando un error ocurra, **explicar la causa raíz**, no solo el arreglo. Los errores son
   oportunidades de aprendizaje aquí.

Prioridad del proyecto: **Comprensión > Arquitectura > Calidad > Código > Cantidad de tecnologías.**
Nunca uses una tecnología solo para "tenerla". Cada una debe resolver un problema real y explicado.

---

## 1. QUÉ ES ORDERFLOW

**OrderFlow — Distributed Order Processing Platform.** Backend que simula una plataforma de
e-commerce. Su propósito real NO es vender: es **demostrar dominio de los problemas difíciles
de ingeniería backend** que hay detrás de procesar pedidos:

- Concurrencia (que dos usuarios no compren el mismo último producto).
- Idempotencia (que un reintento de red no cree dos pedidos).
- Transacciones y consistencia de datos.
- Procesamiento asíncrono con colas y workers.
- Caching con Redis.
- Outbox Pattern (no perder eventos si el servidor falla).
- Observabilidad, seguridad, testing, Docker, CI/CD, IaC y despliegue en AWS.

Actores del sistema: **Cliente** (compra), **Admin** (gestiona productos/inventario) y
**Sistema/Workers** (procesan eventos asíncronos).

---

## 2. ESTADO ACTUAL DEL PROYECTO

Las fases 0, 1 y 2 YA ESTÁN COMPLETAS. No las rehagas. Sirven de contexto:

- **Fase 0 (Diseño):** hecha. Problema, actores, casos de uso, requisitos y arquitectura definidos.
- **Fase 1 (Repositorio):** hecha. Monorepo con `frontend/` (Vite, sin tocar) y `backend/`.
  Estructura del monolito modular creada. GitHub Flow + Conventional Commits en uso.
- **Fase 2 (Backend base):** hecha. App FastAPI con `/health`, configuración por entorno
  (pydantic-settings), logging estructurado con request IDs (structlog) y manejo central de errores.

**La próxima fase a trabajar es la FASE 3 (PostgreSQL).**

> Nota de rutas: en el disco el proyecto está en `OrderFlow/Backend/` (con mayúsculas), pero el
> paquete Python interno es `orderflow` (minúscula). Respeta esas mayúsculas al escribir rutas.

---

## 3. STACK TECNOLÓGICO Y JUSTIFICACIÓN

Lenguaje: **Python 3.11+** (el dueño domina FastAPI; se eligió Python conscientemente).

| Necesidad | Elección | Por qué |
|---|---|---|
| Framework web | FastAPI | Async nativo, genera OpenAPI solo, ya lo domina el dueño |
| Servidor ASGI | Uvicorn | Estándar de FastAPI |
| Gestor de deps/entorno | **uv** | Unifica venv+pip, rapidísimo, genera `uv.lock` (builds reproducibles) |
| Validación / config | Pydantic v2 + pydantic-settings | Valida inputs y variables de entorno con tipos (fail fast) |
| ORM | SQLAlchemy 2.0 (async) + asyncpg | Control fino de transacciones y `SELECT ... FOR UPDATE` para concurrencia |
| Migraciones | Alembic | Versiona el esquema de la BD |
| Base de datos | PostgreSQL | Transacciones ACID, constraints, locks — el corazón de la consistencia |
| Cache | Redis | Cache-aside, rate limiting, idempotency keys |
| Mensajería | AWS SQS (LocalStack en local) | Desacoplar procesamiento asíncrono |
| Testing | pytest + pytest-asyncio + httpx | Unit, integration, API, concurrencia, idempotencia |
| Logging | structlog | Logs JSON + correlation IDs |
| Contenedores | Docker + Docker Compose | Entorno reproducible |
| IaC | Terraform | Infra como código |
| CI/CD | GitHub Actions | Pipeline automático |

> Regla: si introduces una tecnología nueva, explica primero qué problema resuelve.

---

## 4. ARQUITECTURA

**Monolito modular.** Un solo despliegue, pero internamente dividido en módulos con fronteras
claras. Se eligió sobre microservicios PORQUE los problemas centrales del proyecto (concurrencia,
transacciones, consistencia) son más limpios de resolver dentro de un único límite transaccional;
los microservicios los complicarían (transacciones distribuidas, sagas). Los módulos tienen
fronteras claras para dejar la puerta abierta a separarlos en el futuro si hiciera falta.

**Regla de oro de la arquitectura:** un módulo se comunica con otro SOLO a través de la capa
`service` del otro módulo. Nunca accede directamente al `repository` o a las tablas de otro módulo.

**src layout:** el código vive en `src/orderflow/` para aislar el paquete y evitar imports
accidentales en tests.

Estructura actual:

```
OrderFlow/
├── frontend/                    # Vite (NO TOCAR por ahora)
├── backend/
│   ├── pyproject.toml           # config del proyecto + dependencias
│   ├── uv.lock                  # versiones exactas (reproducibilidad)
│   ├── .env                     # secretos locales (NO se sube a git)
│   ├── .env.example             # plantilla de variables (SÍ se sube)
│   ├── src/
│   │   └── orderflow/
│   │       ├── __init__.py
│   │       ├── main.py          # app FastAPI, middleware, error handler, /health
│   │       ├── core/            # infraestructura compartida (NO lógica de negocio)
│   │       │   ├── config.py    # pydantic-settings
│   │       │   ├── logging.py   # structlog
│   │       │   ├── middleware.py# request_id + tiempo de respuesta
│   │       │   └── errors.py    # jerarquía de errores (AppError, NotFoundError, ConflictError)
│   │       ├── modules/         # módulos de negocio (auth, products, orders, inventory...)
│   │       └── shared/          # dominio común entre módulos
│   └── tests/                   # espeja la estructura de src/
```

**Estructura interna de cada módulo** (capas — se repetirá en cada uno):

```
modules/<nombre>/
├── router.py       # endpoints HTTP (capa web)
├── service.py      # lógica de negocio (reglas) — puerta pública del módulo
├── repository.py   # acceso a datos (habla con Postgres)
├── schemas.py      # validación entrada/salida (Pydantic)
└── models.py       # tablas (SQLAlchemy)
```

Flujo de una petición: `router → service → repository → BD`. La respuesta vuelve por el mismo camino.

---

## 5. CONVENCIONES DE TRABAJO

**Git — GitHub Flow:**
- `main` siempre desplegable. Nunca se trabaja directo sobre ella.
- Una rama por tarea: `feature/...`, `fix/...`, `chore/...`.
- Cada tarea se fusiona vía Pull Request con `gh pr create`.
- Merge con `--squash --delete-branch` para mantener el historial limpio.

**Conventional Commits:** `tipo(alcance): descripción en presente`
- Tipos: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `perf`.
- Ejemplo: `feat(orders): add idempotency key validation`

**Seguridad (aplica siempre):** nunca hardcodear secretos; todo por variables de entorno.
El `.env` nunca se sube; el `.env.example` sí (como plantilla). Validar todos los inputs.
Nunca filtrar stack traces ni detalles internos al cliente.

**Comandos base del entorno:**
- Instalar dependencia: `uv add <paquete>`
- Sincronizar entorno: `uv sync`
- Correr el servidor: `uv run uvicorn orderflow.main:app --reload`
- Correr algo en el entorno: `uv run <comando>`

---

## 6. PLAN DE FASES 0–6 (QUÉ Y POR QUÉ)

Trabaja UNA fase a la vez. Dentro de cada fase, pasos pequeños y verificables.
Explica el porqué de cada pieza. No avances sin que el usuario confirme que funciona y entendió.

### FASE 0 — Diseño ✅ (hecha)
Problema, actores, casos de uso, requisitos funcionales y no funcionales, arquitectura y stack.

### FASE 1 — Repositorio ✅ (hecha)
Monorepo, estructura del monolito modular, GitHub Flow, Conventional Commits.

### FASE 2 — Backend base ✅ (hecha)
FastAPI + `/health`; configuración por entorno; logging estructurado con request IDs;
manejo central de errores. Todo vive en `core/`.

### FASE 3 — PostgreSQL (SIGUIENTE)
**Objetivo:** pasar de "servidor que responde ok" a "sistema con datos reales".
**Qué construir:**
- Levantar PostgreSQL en Docker con un `docker-compose.yml` (primer uso real de Docker;
  se corre la BD en contenedor para no ensuciar la máquina y ser reproducible).
- Conectar SQLAlchemy 2.0 en modo **async** con asyncpg (engine, session, dependencia de FastAPI).
- Configurar **Alembic** para migraciones (versionar el esquema).
- Diseñar el esquema justificando CADA tabla, relación, constraint e índice. Tablas objetivo del
  proyecto completo: `users`, `roles`, `products`, `inventory`, `orders`, `order_items`,
  `idempotency_keys`, `outbox_events` (se irán creando por fases, no todas de golpe).
**Por qué:** PostgreSQL da transacciones ACID, foreign keys, unique constraints y locks. Es la base
sobre la que se construyen la concurrencia (Fase 6), las transacciones de pedidos (Fase 7),
la idempotencia (Fase 8) y el outbox (Fase 11). Las invariantes críticas (ej. stock nunca negativo)
se garantizan en la BD, no solo en el código.
**Explicar al usuario:** qué es una migración y por qué versionar el esquema; por qué async;
diferencia entre engine y session; qué es una constraint y por qué proteger invariantes en la BD.

### FASE 4 — Authentication
**Qué construir:** primer módulo real (`modules/auth`) con la estructura en capas completa.
Registro, login, JWT, hashing de contraseñas con bcrypt, roles (customer/admin) y middleware/
dependencia de autorización. Tablas `users` y `roles`.
**Por qué:** es el primer módulo end-to-end (router→service→repository→models→schemas), así que
sirve de plantilla para todos los demás. Cubre seguridad: nunca guardar contraseñas en texto plano,
JWT para autenticación sin estado, autorización por roles.
**Explicar:** por qué se hashea (y qué es un salt); por qué JWT y qué lleva dentro; diferencia
entre autenticación y autorización; cómo protege la dependencia de FastAPI los endpoints.

### FASE 5 — Products
**Qué construir:** módulo `modules/products` con CRUD, validaciones (Pydantic), persistencia en
PostgreSQL y tests. Solo admin puede crear/modificar; cualquiera puede consultar.
**Por qué:** consolida el patrón de módulo en capas y deja el catálogo listo para que Orders e
Inventory lo usen. Aquí más adelante (Fase 9) entrará el cache-aside con Redis en `GET /products/:id`.
**Explicar:** por qué validar en el borde con Pydantic; cómo se separan las reglas (service) del
acceso a datos (repository); por qué la autorización por rol va en la capa correcta.

### FASE 6 — Inventory (LA FASE CLAVE DEL PROYECTO)
**Qué construir:** módulo `modules/inventory` que reserva, libera y confirma stock, con
**control de concurrencia real**. Tabla `inventory`.
**El problema central:** si `stock = 1` y dos usuarios compran a la vez, el sistema NO debe permitir
stock negativo. Solo una operación puede reservar el último producto.
**Cómo resolverlo (y explicarlo a fondo):**
- Transacciones para atomicidad.
- Locking pesimista con `SELECT ... FOR UPDATE`, o un `UPDATE` condicional atómico
  (`... WHERE stock >= cantidad`), explicando el trade-off entre ambos enfoques.
- Un CHECK constraint en la BD (`stock >= 0`) como última línea de defensa.
**Tests obligatorios:** pruebas de concurrencia que lancen compras simultáneas del mismo producto y
verifiquen que el stock nunca queda negativo y que solo una gana.
**Por qué importa:** esta es LA fase que diferencia el portafolio de "otro CRUD". Es la que hay que
saber explicar mejor en la entrevista. No la trates como una fase más.
**Explicar:** qué es una condición de carrera; diferencia entre locking pesimista y optimista;
por qué la transacción sola no basta sin el lock o el update condicional; por qué la constraint en
BD es la garantía final.

---

## 7. CÓMO EMPEZAR CADA SESIÓN

1. Lee este archivo y confirma en qué fase va el proyecto (revisa qué módulos existen en `modules/`).
2. Antes de escribir código, explica el plan de la sub-tarea y su porqué.
3. Trabaja en una rama (`feature/phase-N-...`), en pasos pequeños.
4. Haz que el usuario pruebe cada paso.
5. Cierra la fase con commit convencional, PR y squash merge.
6. No avances de fase sin confirmación explícita.

Recuerda: el éxito de este proyecto se mide por cuánto ENTIENDE el usuario, no por cuánto código se
genera.


----------------------------------------------------

# CLAUDE.md — OrderFlow · Fases 7 a 13

> Continuación del contexto de OrderFlow. Cubre de la Fase 7 (Orders) a la Fase 13 (Docker).
> Puedes anexar este contenido al final de tu `CLAUDE.md` principal, o mantenerlo como archivo
> aparte y referenciarlo cuando llegues a estas fases.

---

## RECORDATORIO DE COMPORTAMIENTO (SIGUE VIGENTE)

Este sigue siendo un proyecto de **aprendizaje y portafolio**. En cada fase:
explica ANTES de escribir código; justifica el PORQUÉ y el trade-off de cada decisión;
avanza en pasos pequeños y verificables; haz que el usuario pruebe cada paso; explica el
código nuevo; no avances de fase sin confirmación de que funciona y se entendió. El éxito se
mide por comprensión, no por cantidad de código. La Fase 11 (Outbox) y todo lo asíncrono son
conceptualmente difíciles: ve especialmente despacio ahí.

**Contexto asumido:** las fases 0–6 están completas (backend base, PostgreSQL, auth, products,
inventory con control de concurrencia). Estas fases construyen encima de eso.

---

## NUMERACIÓN DEL BLOQUE

- Fase 7 — Orders
- Fase 8 — Idempotency
- Fase 9 — Redis
- Fase 10 — Messaging (SQS)
- Fase 11 — Outbox Pattern
- Fase 12 — Testing
- Fase 13 — Docker

---

## FASE 7 — Orders (el caso de uso central)

**Objetivo:** implementar la creación de pedidos, que es donde convergen la transacción, el
inventario y (en fases siguientes) la idempotencia y el outbox.

**Qué construir:**
- Módulo `modules/orders` con la estructura en capas (router/service/repository/schemas/models).
- Tablas `orders` y `order_items` (un pedido tiene muchos ítems — relación uno-a-muchos con FK).
- Estados del pedido como una **máquina de estados** explícita, ej.:
  `PENDING → CONFIRMED → CANCELLED` (define las transiciones válidas y prohíbe las inválidas).
- Endpoint `POST /orders`: dentro de UNA transacción, reserva el stock (llamando al **service**
  del módulo Inventory, nunca a su repository) y crea el pedido + sus ítems de forma atómica.
- Endpoints `GET /orders`, `GET /orders/:id`, `POST /orders/:id/cancel`.
- Cancelación: solo permitida en estados válidos; libera el stock reservado.

**Por qué:** crear un pedido toca varias tablas y varios módulos a la vez. Si algo falla a medias
(se reservó stock pero no se guardó el pedido, o al revés), los datos quedan inconsistentes. La
transacción garantiza "todo o nada" (atomicidad). Esta es la razón real por la que existe una
transacción — no es decoración.

**Explicar al usuario:** qué es una máquina de estados y por qué modelar los estados explícitamente
evita bugs; por qué la creación del pedido y la reserva de stock van en la MISMA transacción; cómo
un módulo llama a otro solo por su capa `service` (la frontera de la arquitectura); qué pasa si la
transacción falla (rollback total).

---

## FASE 8 — Idempotency

**Objetivo:** que `POST /orders` sea seguro ante reintentos. Si el cliente reenvía la misma
solicitud (por un timeout de red, por ejemplo), NO se deben crear dos pedidos.

**Qué construir:**
- El cliente envía un header `Idempotency-Key: <valor-único>` en `POST /orders`.
- Tabla `idempotency_keys` que guarda la clave, el resultado de la primera operación y su estado.
- Lógica: si la clave no existe → procesa y guarda el resultado bajo esa clave. Si ya existe →
  devuelve el resultado guardado sin volver a crear nada.
- Expiración de claves (TTL) cuando tenga sentido.
- Protección contra dos solicitudes concurrentes con la misma clave (unique constraint en la BD +
  manejo del conflicto).

**Por qué:** las redes no son confiables. Un cliente puede no recibir la respuesta aunque el
servidor sí procesó la petición, y reintentar. Sin idempotencia, ese reintento duplica el pedido
(y el cobro). Es un problema real de sistemas de pagos y pedidos.

**Explicar al usuario:** por qué "al menos una vez" es la realidad de las redes; cómo la unique
constraint sobre la clave previene duplicados incluso con dos requests simultáneas (conecta con la
concurrencia de la Fase 6); qué guardar exactamente (el resultado, no solo la marca); por qué el TTL;
la diferencia entre reintentar una operación idempotente vs una que no lo es.

---

## FASE 9 — Redis (cache y rate limiting)

**Objetivo:** reducir carga sobre PostgreSQL en lecturas frecuentes y proteger endpoints sensibles.

**Qué construir:**
- **Cache-Aside Pattern** en `GET /products/:id`:
  1. Buscar en Redis. 2. Si está (hit) → devolver. 3. Si no (miss) → leer de PostgreSQL,
  guardar en Redis con TTL, devolver.
- Invalidación: cuando un producto se actualiza (Fase 5), borrar/actualizar su entrada en cache.
- **Rate limiting**: `POST /auth/login` máximo 5 intentos por minuto por IP → si se supera, HTTP 429.
  Aplicar a otros endpoints donde tenga sentido.

**Por qué:** algunos productos se consultan miles de veces. Golpear la BD en cada lectura la satura.
Redis (en memoria) sirve esas lecturas en microsegundos y descarga la base. El rate limiting protege
contra fuerza bruta y abuso.

**Explicar al usuario:** el flujo del cache-aside paso a paso; qué es un TTL y por qué; el problema de
**invalidación** (datos viejos en cache tras un update) y cómo resolverlo; el **cache stampede**
(muchos misses simultáneos golpeando la BD a la vez) y cómo mitigarlo; qué datos conviene cachear y
cuáles NO (datos que cambian mucho o que deben ser siempre exactos).

---

## FASE 10 — Messaging (SQS / procesamiento asíncrono)

**Objetivo:** sacar del camino de la petición el trabajo que no necesita ser síncrono
(notificaciones, emails), para que `POST /orders` responda rápido.

**Qué construir:**
- SQS emulado localmente con **LocalStack** (no gastar en AWS durante el desarrollo).
- **Producer:** al crear un pedido, publicar un evento `OrderCreated` a una cola.
- **Consumer / Worker:** un proceso separado que lee de la cola y procesa el evento
  (ej. Notification Service, Email Service simulado).
- **Retries** ante fallos y una **Dead Letter Queue (DLQ)** para mensajes que fallan repetidamente.
- Manejo de errores en el worker.

**Por qué:** enviar un email dentro de `POST /orders` haría al cliente esperar por algo que no le
importa para confirmar su pedido. Desacoplar con una cola hace la respuesta rápida y el sistema
resiliente: si el servicio de email está caído, el mensaje espera en la cola en vez de romper el pedido.

**Explicar al usuario:** síncrono vs asíncrono y cuándo cada uno; qué es un producer y un consumer;
por qué el worker es un PROCESO SEPARADO de la API; entrega "al menos una vez" y visibility timeout;
qué es una DLQ y por qué evita que un mensaje "veneno" bloquee la cola; cómo se relaciona esto con la
idempotencia de la Fase 8 (un mensaje puede llegar dos veces).

---

## FASE 11 — Outbox Pattern (la fase conceptualmente más difícil)

**Objetivo:** garantizar que un evento importante (ej. `OrderCreated`) nunca se pierda, incluso si
el servidor se cae justo después de guardar el pedido.

**El problema que resuelve (dual write):**
1. Se guarda el pedido en PostgreSQL (transacción commit).
2. El servidor intenta publicar el evento a SQS.
3. El servidor falla ANTES de publicar.
4. Resultado: el pedido existe pero el evento nunca salió. Inventario y notificaciones nunca se
   enteran. Estado inconsistente.
No se puede escribir atómicamente en DOS sistemas distintos (BD y cola) a la vez. Ese es el
"dual write problem".

**Qué construir:**
- Tabla `outbox_events`.
- Al crear el pedido, escribir el evento en `outbox_events` DENTRO DE LA MISMA TRANSACCIÓN que el
  pedido. Así, o se guardan ambos o ninguno (atomicidad garantizada por la BD).
- Un **worker/poller** separado que lee eventos pendientes de `outbox_events` y los publica a SQS,
  marcándolos como publicados.
- Reintentos si la publicación falla.
- **Consumidores idempotentes**: como el mismo evento puede publicarse más de una vez, el consumidor
  debe deduplicar (ej. por un ID de evento) para no procesar dos veces.

**Qué garantiza y qué NO:** garantiza que el evento se publicará **al menos una vez** (nunca se pierde).
NO garantiza "exactamente una vez" — por eso los consumidores DEBEN ser idempotentes.

**Explicar al usuario:** el dual write problem con el ejemplo de arriba; por qué meter el evento en la
misma transacción lo resuelve; el rol del poller; por qué "al menos una vez" y no "exactamente una vez";
cómo se logra la deduplicación en el consumidor; cómo esto cierra el círculo con las fases 8 y 10.

---

## FASE 12 — Testing (estrategia real, no de adorno)

**Objetivo:** una estrategia de testing que cubra desde reglas de negocio hasta los problemas
difíciles (concurrencia e idempotencia).

**Qué construir (con pytest + pytest-asyncio + httpx + testcontainers):**
- **Unit tests:** reglas de negocio, validaciones y services aislados (sin BD real).
- **Integration tests:** contra PostgreSQL y Redis REALES levantados con testcontainers (no mocks),
  para probar que las transacciones, constraints e índices se comportan como se espera.
- **API tests:** peticiones HTTP end-to-end con httpx contra la app.
- **Concurrency tests:** lanzar múltiples compras simultáneas del mismo producto y verificar que el
  stock nunca queda negativo y que solo una operación gana (valida la Fase 6).
- **Idempotency tests:** enviar varias requests con la misma `Idempotency-Key` y verificar que se
  crea un solo pedido (valida la Fase 8).

**Por qué:** los tests de concurrencia e idempotencia son los que DEMUESTRAN que los problemas
difíciles están realmente resueltos. Un revisor que vea un test que reproduce la condición de carrera
y la verifica sabe que entiendes el problema de verdad. Son oro de portafolio.

**Explicar al usuario:** la pirámide de tests (muchos unit, menos integration, pocos end-to-end);
por qué usar Postgres real (testcontainers) en integration en vez de mocks; cómo se simula
concurrencia en un test; qué se está afirmando exactamente en cada tipo de test.

---

## FASE 13 — Docker (entorno completo reproducible)

**Objetivo:** que cualquiera pueda clonar el repo y levantar todo el sistema con un comando.

**Qué construir:**
- **Dockerfile** para la API (idealmente multi-stage: una etapa construye, otra corre, para una
  imagen final ligera). Otro para el worker si conviene (o el mismo con distinto comando de arranque).
- **docker-compose.yml** que orqueste todos los servicios: API, PostgreSQL, Redis, worker y LocalStack
  (SQS). Este compose reemplaza al que se usó solo para la BD en la Fase 3, ahora con el sistema completo.
- Meta: `docker compose up` levanta el entorno funcionando de punta a punta.

**Por qué:** elimina el "en mi máquina funciona". Empaqueta la app con sus dependencias exactas y
define toda la topología del sistema como código. Es también el paso previo al despliegue en la nube
(fases posteriores en AWS/ECS).

**Explicar al usuario:** diferencia entre el Dockerfile (define la imagen de UNA app) y docker-compose
(orquesta VARIOS servicios juntos); qué es un multi-stage build y por qué reduce el tamaño de la imagen;
por qué el worker corre como servicio aparte en el compose; cómo se conectan los servicios entre sí por
red interna; qué variables de entorno inyecta el compose.

---

## AL CERRAR ESTE BLOQUE

Tras la Fase 13, el proyecto ya es un sistema backend distribuido completo corriendo localmente:
concurrencia, idempotencia, cache, colas, outbox, testing serio y todo dockerizado. Ese es el
núcleo defendible del portafolio. Las fases siguientes (CI/CD, AWS, Terraform, deployment,
observabilidad, README y preparación de entrevista) son la capa cloud/infra que corona el proyecto.

Recordatorio final: NO generes todo de golpe. Una fase a la vez, explicando y verificando. La
comprensión del usuario es el objetivo.