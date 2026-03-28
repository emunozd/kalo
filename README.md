# KALO — Kaloric Assistant

Personal calorie tracking assistant for Telegram. Log meals and workouts by day, estimate calories from food photos using AI vision, calculate your BMR, and stay on track with your daily calorie goal.

---

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI + SQLAlchemy async |
| Database | PostgreSQL 16 |
| Bot | python-telegram-bot 21 |
| Vision | Multimodal LLM (OpenAI-compatible endpoint) |
| Email OTP | Brevo (transactional) |
| Containers | Docker + Docker Compose |
| Reverse proxy | Caddy (separate repo) |

---

## Project structure

```
kalo/
├── app/
│   ├── core/
│   │   ├── config.py          ← pydantic-settings
│   │   ├── database.py        ← SQLAlchemy async session
│   │   └── deps.py            ← JWT auth dependency
│   ├── models/
│   │   └── models.py          ← ORM (6 tables)
│   ├── routers/
│   │   ├── auth.py            ← OTP + JWT + Telegram linking
│   │   ├── perfil.py          ← BMR, weight, height
│   │   ├── calorias.py        ← calorie entries CRUD (date-aware)
│   │   ├── ejercicio.py       ← workout entries CRUD (date-aware)
│   │   ├── foto.py            ← photo → kcal via LLM Vision
│   │   └── resumen.py         ← daily and weekly balance
│   ├── schemas/
│   │   └── schemas.py         ← Pydantic I/O models
│   └── services/
│       ├── brevo_client.py    ← OTP email delivery
│       ├── vision_client.py   ← LLM Vision client (photo → kcal)
│       └── resumen_service.py ← daily summary upsert helper
├── bot/
│   ├── __init__.py
│   ├── main.py                ← handlers + ConversationHandlers
│   └── agent.py               ← free-text intent classifier
├── db/
│   └── init/
│       └── 01_schema.sql      ← full schema + triggers + upsert function
├── docker-compose.yml
├── Dockerfile                 ← kalo-api image
├── Dockerfile.bot             ← kalo-bot image
├── requirements.txt
├── requirements.bot.txt
└── .env.example
```

---

## Requirements

- Docker Desktop
- Telegram bot token ([@BotFather](https://t.me/BotFather))
- Brevo account (OTP email delivery)
- Any OpenAI-compatible `/v1/chat/completions` endpoint with vision support (OpenAI GPT-4o, Ollama + LLaVA, AIBase, etc.)

---

## Configuration

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Database
POSTGRES_DB=kalo
POSTGRES_USER=kalo_user
POSTGRES_PASSWORD=changeme
POSTGRES_PORT=5432

# API
API_PORT=8000
JWT_SECRET=change_me_in_production

# Brevo
BREVO_API_KEY=xkeysib-...
BREVO_FROM_EMAIL=noreply@yourdomain.com
BREVO_FROM_NAME=KALO

# LLM Vision
LLM_VISION_URL=https://api.openai.com/v1/chat/completions
LLM_VISION_API_KEY=sk-...
LLM_VISION_MODEL=gpt-4o

# Telegram
TELEGRAM_TOKEN=123456:ABC-DEF...
```

---

## Getting started

```bash
# 1. Create the external volume (first time only)
docker volume create kalo_pgdata

# 2. Start all services
docker compose up -d

# 3. Follow logs
docker compose logs -f bot
docker compose logs -f api
```

Services start in order:
1. `kalo-postgres` — waits for healthcheck before proceeding
2. `kalo-api` — FastAPI on the configured port
3. `kalo-bot` — Telegram bot with long-polling

### Rebuild after changes

```bash
# Bot only
docker compose build bot && docker compose up -d bot

# API only
docker compose build api && docker compose up -d api

# Everything
docker compose build && docker compose up -d
```

---

## API reference

### Auth (passwordless)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/solicitar-codigo` | Send 6-digit OTP to email via Brevo |
| `POST` | `/auth/verificar-codigo` | Verify OTP and return JWT |
| `POST` | `/auth/vincular-telegram` | Link telegram_id to authenticated user |
| `DELETE` | `/auth/desvincular-telegram` | Unlink Telegram (history preserved) |
| `GET` | `/auth/token-telegram/{telegram_id}` | Bot retrieves JWT by telegram_id |

### Profile / BMR

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/perfil` | View current profile and calculated BMR |
| `POST` | `/perfil` | Create or update profile (recalculates BMR) |

BMR uses the revised Harris-Benedict equation. `daily_goal = BMR × activity_factor`.

### Calorie entries

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/calorias` | Log a meal manually with date |
| `GET` | `/calorias?fecha=YYYY-MM-DD` | List entries for a specific day |
| `GET` | `/calorias/historial?desde=...&hasta=...` | History grouped by date |
| `DELETE` | `/calorias/{id}` | Delete entry (summary auto-updated) |

### Photo → Calories

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/foto/preview` | Analyze image with LLM, return estimate (nothing saved) |
| `POST` | `/foto/confirmar` | Save the user-confirmed entry |

### Workout entries

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/ejercicio` | Log a workout with date and calories burned |
| `GET` | `/ejercicio?fecha=YYYY-MM-DD` | List workouts for a specific day |
| `GET` | `/ejercicio/historial?desde=...&hasta=...` | History grouped by date |
| `DELETE` | `/ejercicio/{id}` | Delete entry (summary auto-updated) |

### Daily summary

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/resumen/dia?fecha=YYYY-MM-DD` | Full calorie balance for a given day |
| `GET` | `/resumen/semana?desde=...&hasta=...` | Daily summaries over a date range |

`kcal_available = goal - consumed + burned` — stored as a `GENERATED` column in Postgres.

---

## Telegram bot

### Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and command list |
| `/vincular` | Link KALO account via email OTP |
| `/desvincular` | Unlink Telegram (history preserved) |
| `/perfil` | View or register physical profile and BMR |
| `/calorias` | Log a meal manually |
| `/ejercicio` | Log a workout |
| `/resumen` | Today's calorie balance |
| `/historial` | Last 7 days summary |
| `/borrar <id>` | Delete a specific entry |
| `/cancelar` | Cancel any ongoing conversation |

### Free-text agent

The bot accepts plain text and classifies the intent:

| Example | Detected intent |
|---|---|
| "I had rice with chicken and a salad" | → asks for kcal, then logs it |
| "Cycled for 30 min and burned 250 kcal" | → logs workout directly |
| "How many calories do I have left today?" | → shows daily summary |
| "What did I eat yesterday?" | → shows history |
| "What's my BMR?" | → shows profile |

### Photo flow

```
User sends a photo of their plate
        ↓
Bot downloads image → calls POST /foto/preview
        ↓
LLM Vision estimates kcal (utensil used as size reference)
        ↓
Bot shows estimate + confidence level (HIGH / MEDIUM / LOW)
        ↓
User confirms, adjusts, or cancels
        ↓
Bot saves via POST /foto/confirmar and shows updated balance
```

### Auth flow

```
/vincular
    ↓
User enters email
    ↓
API creates user if new + sends OTP via Brevo
    ↓
User enters 6-digit code
    ↓
API verifies OTP → returns JWT → bot links telegram_id
    ↓
Going forward: bot auto-fetches JWT via GET /auth/token-telegram/{id}
```

---

## Database

### Tables

| Table | Purpose |
|---|---|
| `usuarios` | Passwordless registration. Unique email. Optional telegram_id. |
| `codigos_otp` | 6-digit OTP, 10-min TTL, single use. |
| `perfiles` | Physical data + calculated BMR + daily kcal goal. |
| `registros_calorias` | Each meal/snack. `fecha` (DATE) + `registrado_en` (TIMESTAMPTZ). |
| `registros_ejercicio` | Workouts. `fecha` + `registrado_en` + `kcal_quemadas`. |
| `resumenes_diarios` | Daily balance per user. `kcal_disponibles` is a GENERATED STORED column. |

### Date handling in entries

Every calorie and workout entry carries **two timestamps**:

- `fecha` (DATE) — the day the activity belongs to. Users can log yesterday's meals retroactively.
- `registrado_en` (TIMESTAMPTZ) — the exact moment the entry was created.

This allows grouping by day correctly, tracking what time of day meals occurred, and logging past days without losing auditability.

### upsert_resumen_diario function

Called automatically from the API after every INSERT or DELETE on calorie/workout entries. Recomputes totals from scratch and does `INSERT ... ON CONFLICT DO UPDATE` on the daily summary row.

---

## Resource limits

| Service | CPU limit | RAM limit | CPU reserved | RAM reserved |
|---|---|---|---|---|
| kalo-postgres | 1.0 | 512 MB | 0.25 | 128 MB |
| kalo-api | 0.75 | 256 MB | 0.10 | 64 MB |
| kalo-bot | 0.50 | 128 MB | 0.05 | 32 MB |

---

## Timezone

All containers run on `America/Bogota`. Set in `docker-compose.yml`:

```yaml
environment:
  TZ: America/Bogota
  PGTZ: America/Bogota   # postgres only
```

---

## Multi-user

The system supports multiple concurrent users. Every entry (meal, workout, summary) is tied to `usuario_id`. Unlinking Telegram does not delete any data — it only removes the `telegram_id` association.