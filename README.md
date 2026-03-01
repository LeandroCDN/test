# BTC/ETH 5-Min Polymarket Bot

Automated trading bot for Polymarket BTC and ETH 5-minute Up/Down markets, with a local dashboard for monitoring and control.

## Architecture

```
app/
  bot/          Trading logic (bot.py, strategy.py)
  services/     Trader, market discovery, state store, bot manager
  api/          FastAPI backend (routes, main)
  config.py     All tunable parameters
web/            React + Vite dashboard
scripts/        CLI launcher
tests/          API & lifecycle tests
```

## Quick Start (Local)

### 1. Backend

```bash
# Create virtual environment (recommended)
python -m venv .venv

# Activate
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the API server
uvicorn app.api.main:app --reload --port 8000
```

The API is now live at `http://localhost:8000`.

### 2. Frontend

```bash
cd web
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

### 3. Bot (CLI standalone, optional)

If you prefer running the bot from terminal without the dashboard:

```bash
cd app
python bot/bot.py          # live trading
python bot/bot.py --dry    # dry run
```

Or from the project root:

```bash
python scripts/run_bot.py [--dry-run]
```

## Dashboard Usage

1. Start the API server (`uvicorn app.api.main:app --port 8000`)
2. Start the frontend (`cd web && npm run dev`)
3. Open the dashboard in your browser
4. Click **Start** to launch the bot worker, or **Dry Run** for simulation
5. Use **Pause Entry** / **Resume Entry** to temporarily skip new trades
6. Use **Force Redeem** to trigger an immediate redemption cycle
7. Click **Stop** to gracefully shut down the worker

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/status` | Full status snapshot |
| GET | `/metrics` | Trading statistics |
| GET | `/events?after=ID&limit=N` | Event feed |
| POST | `/worker/start` | Start bot worker |
| POST | `/worker/stop` | Stop bot worker |
| POST | `/worker/pause-entry` | Pause new entries |
| POST | `/worker/resume-entry` | Resume entries |
| POST | `/worker/force-redeem` | Force redemption cycle |
| GET | `/settings` | Current persisted settings + restart requirement |
| PUT | `/settings` | Save settings (Safe Mode: apply on next worker start) |

## Configuration

Default parameters live in `app/config.py`.

Runtime settings are managed through API/UI and persisted in `runtime_settings.json` (project root).
This project uses **Safe Mode** for config updates: settings are saved immediately but only applied after worker restart.

Configurable groups:

- Dynamic entry profiles, odds ranges, bet sizing
- Stop-loss settings
- Auto-redeem configuration
- Polling intervals and log verbosity

## Environment Variables

Create a `.env` file in the project root:

```
PK=your_private_key
SIGNATURE_TYPE=0
FUNDER=your_funder_address
POLY_BUILDER_API_KEY=...
POLY_BUILDER_SECRET=...
POLY_BUILDER_PASSPHRASE=...
```

## Tests

```bash
pip install pytest httpx
pytest tests/ -v
```

## Future Deployment

The architecture is designed for easy split deployment:

- **Worker + API**: deploy to a dedicated server
- **Frontend**: deploy to Vercel, pointing `VITE_API_URL` to the server API

No API contract changes needed — just update the base URL.


python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# terminal 1 (backend)
uvicorn app.api.main:app --reload --port 8000

# terminal 2 (frontend)
cd web
npm install
# crear web/.env.local con:
# VITE_API_URL=http://localhost:8000
npm run dev