# Web3 Job Bot

A Telegram bot that scrapes **15 Web3 job sources every 6 hours** and delivers filtered, deduplicated job listings straight to your Telegram — for any role you want.

> Built for Web3 job hunters. Set it up once, never miss a relevant role again.

---

## What it does

- Scrapes **1,200+ raw jobs** across 15 sources every 6 hours automatically
- Filters for **any role you configure** — marketing, engineering, legal, design, product, and more
- Supports **remote-only**, **specific cities**, or **any location**
- **Deduplicates** so you never see the same job twice across runs
- Sorts by **newest first**
- Sends results directly to **your private Telegram chat**

---

## Job Sources (15 total)

| Type | Source |
|------|--------|
| RSS | cryptocurrencyjobs.co, remote3.co |
| HTML scrape | web3.career, cryptojobslist.com, blockace.io, crypto.jobs |
| JSON API | Greenhouse (13 companies), Lever (6 companies), RemoteOK |
| Telegram channels | @web3hiring, @cryptojobsdaily, @cryptojobslist |

**Greenhouse companies:** Coinbase, Consensys, Alchemy, Ripple, Fireblocks, BitGo, Gemini, Nansen, Ava Labs, Paradigm, Messari, Figment, Solana Foundation

**Lever companies:** Binance, 1inch, CertiK, Anchorage Digital, Ledger, Aave

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/jobs` | Fetch all current matching jobs |
| `/new` | Show only jobs you haven't seen yet |
| `/twitter` | X profiles of companies currently hiring |
| `/clear` | Delete all bot messages in the chat |
| `/help` | Show available commands |

---

## Customization

Everything is configured via a single `.env` file — no code changes needed.

### Job Role Presets

Set `JOB_ROLES` to one (or more) of the built-in presets:

| Preset | Matches titles containing… |
|--------|---------------------------|
| `marketing` | marketing, growth, community, content, brand, GTM, devrel, partnerships, KOL… |
| `engineering` | engineer, developer, solidity, smart contract, backend, frontend, full stack… |
| `legal` | legal, compliance, counsel, regulatory, policy… |
| `design` | designer, UX, UI, creative director, motion designer… |
| `product` | product manager, head of product, product lead, product owner… |
| `operations` | operations manager, chief of staff, finance manager, treasury… |
| `bd` | business development, account executive, sales, revenue, partner manager… |
| `research` | researcher, analyst, cryptographer, economist… |
| `data` | data analyst, data scientist, analytics engineer, BI… |

**Examples in `.env`:**
```
JOB_ROLES=engineering              # single preset
JOB_ROLES=marketing,bd             # multiple presets
JOB_ROLES=solidity,rust,zkp        # custom keywords
JOB_ROLES=engineering,solidity     # preset + custom
```

### Location Modes

Set `LOCATION_TYPE` to one of:

| Mode | Behavior |
|------|----------|
| `remote` | Only show remote / worldwide / global jobs *(default)* |
| `specific` | Show jobs in your preferred cities + remote jobs |
| `any` | No location filter — show everything |

**For `specific` mode**, also set:
```
PREFERRED_LOCATIONS=Dubai,Singapore,London
```

---

## Setup (5 minutes)

### 1. Get the code

```bash
git clone https://github.com/Charubak/Web3-Job-Bot.git
cd Web3-Job-Bot
```

### 2. Install dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Create a Telegram bot

1. Open Telegram and message **[@BotFather](https://t.me/BotFather)**
2. Send `/newbot` and follow the prompts
3. Copy the **token** it gives you (looks like `123456:ABC-DEF…`)

### 4. Get your Chat ID

1. Message your new bot anything (e.g. "hi")
2. Visit this URL in your browser (replace `YOUR_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
3. Find `"chat":{"id": 123456789}` — that number is your Chat ID

### 5. Configure

```bash
cp .env.example .env
```

Open `.env` and fill in your values:
```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789
JOB_ROLES=marketing          # or engineering, legal, design, etc.
LOCATION_TYPE=remote         # or specific, or any
```

### 6. Test (no Telegram required)

```bash
python3 scraper.py --dry-run
```

This fetches and filters jobs without sending anything to Telegram — useful to verify your role/location settings are working.

### 7. Run the bot

```bash
python3 bot.py
```

The bot will:
- Run a scrape immediately on startup
- Re-scrape every 6 hours automatically
- Respond to Telegram commands in real time

---

## Cloud Deployment (Fly.io — free tier, runs 24/7)

This is the recommended way to run the bot so it works even when your computer is off.

### Prerequisites

- [Sign up for Fly.io](https://fly.io) (free, requires a card for verification)
- Install the CLI: `brew install flyctl` (macOS) or see [fly.io/docs/hands-on/install-flyctl](https://fly.io/docs/hands-on/install-flyctl/)

### Deploy

```bash
fly auth login
fly launch --no-deploy        # creates the app, say NO to Postgres/Redis prompts
fly volumes create job_data --size 1 --region sin   # persistent storage for dedup DB
fly secrets set TELEGRAM_BOT_TOKEN=your_token TELEGRAM_CHAT_ID=your_chat_id JOB_ROLES=marketing LOCATION_TYPE=remote
fly deploy
```

**To add specific locations:**
```bash
fly secrets set LOCATION_TYPE=specific PREFERRED_LOCATIONS="Dubai,Singapore"
fly deploy
```

### Useful commands

```bash
fly logs -a web3-job-bot      # view live logs
fly status -a web3-job-bot    # check app status
fly deploy                    # redeploy after changes
```

---

## Project Structure

```
├── bot.py              # Telegram bot + built-in 6h scheduler
├── scraper.py          # Standalone scraper (fetch → filter → dedup → notify)
├── boards.py           # All 15 job board adapters
├── filters.py          # Keyword, location, and age filtering (with role presets)
├── notifier.py         # Telegram message sender, sorts by recency
├── storage.py          # SQLite deduplication
├── config.py           # Loads all settings from .env
├── company_handles.py  # Maps company names to X/Twitter handles
├── Dockerfile          # For Fly.io / Docker deployment
├── fly.toml            # Fly.io config (Singapore region, 256MB, persistent volume)
├── .env.example        # Template — copy to .env and fill in
└── requirements.txt    # Dependencies
```

---

## Dependencies

```
feedparser        — RSS parsing
httpx             — HTTP requests
beautifulsoup4    — HTML scraping
python-dotenv     — .env file loading
schedule          — Built-in 6-hour scraping scheduler
```

---

## FAQ

**Can I track multiple role types at once?**
Yes — `JOB_ROLES=marketing,bd` will show both marketing and business development roles.

**Can I use my own keywords instead of the presets?**
Yes — `JOB_ROLES=tokenomics,web3 pm,growth hacker` uses your custom keywords directly.

**What if I want jobs in a specific city?**
Set `LOCATION_TYPE=specific` and `PREFERRED_LOCATIONS=Dubai,Singapore` (or any cities you want). Remote jobs are always included alongside.

**Will I get duplicate notifications?**
No — the bot tracks every job it has sent you in a local SQLite database and never sends the same listing twice.

**Does it cost anything?**
No. All job boards scraped are free. Fly.io has a free tier sufficient to run this 24/7. Telegram bots are free.

**How do I stop it on Fly.io?**
```bash
fly scale count 0 -a web3-job-bot   # pause
fly scale count 1 -a web3-job-bot   # resume
```
