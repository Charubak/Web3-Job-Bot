# Web3 Marketing Job Hunter Bot

A Telegram bot that scrapes 15 web3 job sources every 6 hours and delivers filtered remote marketing roles straight to your Telegram. Runs 24/7 on Fly.io.

## What it does

- Scrapes 1,200+ raw jobs across 15 sources every 6 hours
- Filters for marketing, growth, community, content, brand, DevRel, and GTM roles
- Only shows remote jobs (or Dubai / Singapore / Hong Kong)
- Deduplicates so you never see the same job twice
- Sorts by newest first
- Sends results directly to your Telegram

## Sources

| Type | Source |
|------|--------|
| RSS | cryptocurrencyjobs.co, remote3.co |
| HTML scrape | web3.career, cryptojobslist.com, blockace.io, crypto.jobs |
| JSON API | Greenhouse (13 companies), Lever (5 companies + Aave), RemoteOK |
| Telegram channels | @web3hiring, @cryptojobsdaily, @cryptojobslist |

**Greenhouse companies:** Coinbase, Consensys, Alchemy, Ripple, Fireblocks, BitGo, Gemini, Nansen, Ava Labs, Paradigm, Messari, Figment, Solana Foundation

**Lever companies:** Binance, 1inch, CertiK, Anchorage Digital, Ledger, Aave

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/jobs` | Fetch all current web3 marketing jobs |
| `/new` | Show only jobs you haven't seen yet |
| `/twitter` | X profiles of companies currently hiring for marketing |
| `/clear` | Delete all bot messages in the chat |
| `/help` | Show available commands |

## Filters

- **Title must match:** marketing, growth, community, content, brand, GTM, partnerships, KOL, social media, communications, PR, product marketing, etc.
- **Location:** Remote, worldwide, global, Dubai, Singapore, Hong Kong only — US-restricted roles excluded
- **Age:** Jobs older than 45 days are excluded
- **Dedup:** Same job won't appear twice across runs

## Project Structure

```
├── bot.py              # Telegram bot + built-in 6h scheduler (cloud entrypoint)
├── scraper.py          # Standalone scraper (fetch → filter → dedup → notify)
├── boards.py           # All 15 job board adapters
├── filters.py          # Keyword, location, and age filtering logic
├── notifier.py         # Telegram message sender, sorts by recency
├── storage.py          # SQLite deduplication
├── company_handles.py  # Maps company names to X/Twitter handles
├── config.py           # Loads credentials from .env
├── Dockerfile          # For Fly.io deployment
├── fly.toml            # Fly.io config (Singapore region, 256MB, persistent volume)
└── requirements.txt    # Dependencies
```

## Setup

### 1. Install dependencies
```bash
pip3 install -r requirements.txt
```

### 2. Configure credentials
```bash
cp .env.example .env
```
Fill in your `.env`:
```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Create a Telegram bot via [@BotFather](https://t.me/BotFather) to get your token. Get your chat ID by messaging the bot then visiting `https://api.telegram.org/bot<TOKEN>/getUpdates`.

### 3. Test locally
```bash
python3 scraper.py --dry-run
```

### 4. Run the bot locally
```bash
python3 bot.py
```

## Cloud Deployment (Fly.io)

Runs 24/7 for free on Fly.io's free tier.

```bash
brew install flyctl
fly auth signup
fly launch
fly volumes create job_data --size 1 --region sin
fly secrets set TELEGRAM_BOT_TOKEN=your_token TELEGRAM_CHAT_ID=your_chat_id
fly deploy
```

Check logs:
```bash
fly logs
```

## Dependencies

- `feedparser` — RSS parsing
- `httpx` — HTTP requests
- `beautifulsoup4` — HTML scraping
- `python-dotenv` — Environment variable loading
- `schedule` — Built-in 6-hour scraping scheduler
