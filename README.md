# Ghostfolio Companion Bot

A Telegram bot that acts as a companion to your [Ghostfolio](https://github.com/ghostfolio/ghostfolio) instance — self-hosted or cloud. Import broker CSVs, track your portfolio, and manage accounts — all from Telegram.

> **Works with any Ghostfolio instance.** The bot connects via the Ghostfolio REST API — point it at your self-hosted server or at [ghostfol.io](https://ghostfol.io). Primarily designed for self-hosted setups where you control your data.

---

## Features

- **Portfolio summary** — current value, P&L, and top holdings at a glance
- **Performance dashboard** — gain/loss across today, this week, month, YTD, 1 year, and all time in one message
- **CSV import** — upload a broker export and the bot auto-detects the format, resolves symbols via Yahoo Finance, deduplicates against existing activities, and imports
- **Backups** — export your full Ghostfolio data on demand or on a schedule; send to Telegram or save locally, with optional encryption
- **Account management** — create Ghostfolio accounts directly from Telegram
- **Access control** — restrict the bot to a whitelist of Telegram user IDs

### Supported brokers

| Broker | Type | Notes |
|--------|------|-------|
| Revolut Stocks | CSV | BUY, SELL, DIVIDEND |
| Revolut Savings | CSV | INTEREST |
| Revolut Crypto | CSV | BUY, SELL |
| DEGIRO | CSV | Dutch & English exports; fee pairing |
| IBKR (Interactive Brokers) | CSV | Trades & Dividends exports |
| MyInvestor | XLS | Spanish broker |
| Delta | CSV | Crypto portfolio tracker |

> **Want to add a broker?** See [Adding a parser](#adding-a-parser).

---

## Quick start

### Prerequisites

- Python 3.12+
- A running [Ghostfolio](https://github.com/ghostfolio/ghostfolio) instance
- A Telegram bot token (see below)
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`

#### Creating a Telegram bot

1. Open Telegram and search for **[@BotFather](https://t.me/BotFather)**, then send `/start`.
2. Send `/newbot` and follow the prompts — choose a display name (e.g. `My Ghostfolio Bot`) and a username ending in `bot` (e.g. `mygf_bot`).
3. BotFather replies with your **bot token** — a string like `123456789:ABCdef...`. Copy it.
4. To find your **Telegram user ID**, send `/start` to [@userinfobot](https://t.me/userinfobot) — it replies with your numeric ID. Add it to `TELEGRAM_ALLOWED_USERS` so only you can use the bot.

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/ghostfolio-bot.git
cd ghostfolio-bot
uv sync
```

### 2. Configure

```bash
cp .env.sample .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your-bot-token-from-botfather
TELEGRAM_ALLOWED_USERS=123456789         # your Telegram user ID
GHOSTFOLIO_URL=http://localhost:3333     # your Ghostfolio URL
GHOSTFOLIO_ACCESS_TOKEN=your-token       # Settings → Security → Access token
GHOSTFOLIO_ACCOUNT_ID=your-account-uuid  # account to import activities into
IMPORT_MODE=direct                       # "direct" or "json"
```

### 3. Run

```bash
uv run ghostfolio-bot
```

### 4. Available commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and command list |
| `/portfolio` | Portfolio summary with P&L and top holdings |
| `/performance` | Performance by period: today, week, month, YTD, 1 year, all time |
| `/dividends` | Dividend history: this month, this year, all-time, and a 12-month bar chart |
| `/import` | Upload a broker CSV/XLS to import activities |
| `/backup` | Export all Ghostfolio data to local file, Telegram, or S3 |
| `/restore` | List available local backups |
| `/create_account` | Create a new Ghostfolio account |
| `/help` | Show all available commands |

---

## Docker

Make sure you have a `.env` file configured (see [Configure](#2-configure)), then:

```bash
# Start the bot in the background
docker compose up -d

# Build the image and start (use this after code changes)
docker compose up -d --build

# View logs
docker compose logs -f

# Stop the bot
docker compose down
```

The container restarts automatically on failure or system reboot (`restart: unless-stopped`). Ghostfolio itself is external — just point `GHOSTFOLIO_URL` at your instance.

> If your Ghostfolio runs in Docker on the same machine, set `GHOSTFOLIO_URL=http://host.docker.internal:3333` so the bot can reach it.

The compose file mounts a `./backups` directory from the host into the container so local backups survive container restarts and image updates:

```yaml
volumes:
  - ./backups:/app/backups
```

---

## Backups

Set `BACKUP_STORAGE` in `.env` to control where backups go:

| Value | Behaviour |
|-------|-----------|
| `telegram` | Bot sends you the backup as a `.json.gz` file in chat (default, no extra setup) |
| `local` | Saved to `BACKUP_LOCAL_DIR` on the server (persisted via Docker volume) |

**Automatic backups** — set a schedule to run backups without any manual action:

```env
BACKUP_SCHEDULE=daily      # every day at 2am
BACKUP_SCHEDULE=weekly     # every Monday at 2am
BACKUP_SCHEDULE=monthly    # 1st of each month at 2am
BACKUP_SCHEDULE=quarterly  # 1st of Jan, Apr, Jul, Oct at 2am
```

**Encryption** — optionally encrypt backups before saving or sending. The file will be saved as `.json.gz.enc` and can only be opened with the key.

1. Generate a key:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. Add it to `.env`:
   ```env
   BACKUP_ENCRYPTION_KEY=your-generated-key
   ```

Keep the key somewhere safe — without it the backup cannot be decrypted.

Backups are compressed with gzip before storing/sending.

**Decrypting a backup manually** — if you need to inspect or restore a `.json.gz.enc` file outside the bot:

```python
from cryptography.fernet import Fernet
import gzip, json

BACKUP_ENCRYPTION_KEY = "your-key-from-.env"

with open("ghostfolio_backup_2026-01-01_000000.json.gz.enc", "rb") as f:
    encrypted = f.read()

json_bytes = gzip.decompress(Fernet(BACKUP_ENCRYPTION_KEY.encode()).decrypt(encrypted))
data = json.loads(json_bytes)
print(json.dumps(data, indent=2))
```

Or as a one-liner to save the result to a file:

```bash
python3 -c "
from cryptography.fernet import Fernet; import gzip, sys
key = 'YOUR_BACKUP_ENCRYPTION_KEY'
raw = open(sys.argv[1], 'rb').read()
print(gzip.decompress(Fernet(key.encode()).decrypt(raw)).decode())
" ghostfolio_backup_2026-01-01_000000.json.gz.enc > backup.json
```

---

## Import mode

Set `IMPORT_MODE` in `.env`:

| Value | Behaviour |
|-------|-----------|
| `direct` | Activities are posted straight to Ghostfolio via the API |
| `json` | Generates a JSON file you can import manually via the Ghostfolio UI |

---

## How CSV import works

1. Upload a CSV (or XLS) file to the bot
2. The bot auto-detects the broker by matching the header against known signatures
3. Symbols are resolved via Yahoo Finance (ISIN → ticker), with an on-disk cache
4. Existing activities are fetched from Ghostfolio and duplicates are filtered out
5. A preview shows new vs skipped activities — confirm to import

---

## Development

```bash
# Install with dev dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Lint (required — auto-fixable with --fix)
uv run ruff check .

# Type check (optional but appreciated)
uv run mypy bot
```

### Adding a parser

1. Create `bot/parsers/mybroker.py`
2. Inherit from `BrokerParser` and decorate with `@register_parser`
3. Set `NAME`, `HEADER_SIGNATURE`, `DELIMITER`, and implement `parse()`
4. Add an import in `bot/parsers/__init__.py`
5. Add tests in `tests/test_mybroker.py`

```python
from bot.parsers.base import BrokerParser, register_parser

@register_parser
class MyBrokerParser(BrokerParser):
    NAME = "MyBroker"
    HEADER_SIGNATURE = "Date,Type,Symbol,Quantity,Price,Fee,Currency"

    def parse(self, csv_text: str) -> list[dict]:
        ...
```

See [`bot/parsers/revolut/savings.py`](bot/parsers/revolut/savings.py) for a minimal working example.

---

## Contributing

Contributions are welcome! Please open an issue before submitting a pull request for significant changes.

1. Fork the repo and create a feature branch
2. Make sure `ruff check .` passes (required) — run `ruff check . --fix` to auto-fix most issues
3. Add or update tests for your change
4. Type annotations are appreciated but not required to get a PR merged
5. Open a PR with a clear description

---

## License

[AGPL-3.0](LICENSE) — any modified version deployed as a network service must also be open source.

---

## Acknowledgements

Inspired by [export-to-ghostfolio](https://github.com/dickwolff/export-to-ghostfolio) — the TypeScript reference implementation for broker CSV → Ghostfolio import.

---

## Disclaimer

This project is provided as-is, without any warranty of any kind. Use it at your own risk. The authors are not responsible for any data loss, corruption, or unintended changes to your Ghostfolio instance.
