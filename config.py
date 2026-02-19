import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
SILENT_IF_EMPTY    = os.getenv("SILENT_IF_EMPTY", "true").lower() == "true"

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")
if not TELEGRAM_CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID is not set in .env")

# ---------------------------------------------------------------------------
# Role filter — what job categories to track
#
# Set JOB_ROLES to one or more of the preset names (comma-separated),
# or list your own custom keywords directly.
#
# Built-in presets:
#   marketing    — marketing, growth, community, content, brand, devrel …
#   engineering  — solidity, smart contract, backend, frontend, developer …
#   legal        — legal, compliance, counsel, regulatory …
#   design       — designer, ux, ui, creative director …
#   product      — product manager, head of product …
#   operations   — ops manager, chief of staff, treasury …
#   bd           — business development, account executive, sales …
#   research     — researcher, analyst, cryptographer …
#   data         — data analyst, data scientist, analytics …
#
# Combine presets:    JOB_ROLES=marketing,bd
# Custom keywords:    JOB_ROLES=tokenomics,growth hacker,web3 pm
# ---------------------------------------------------------------------------
_raw_roles = os.getenv("JOB_ROLES", "marketing").lower()
JOB_ROLES  = [r.strip() for r in _raw_roles.split(",") if r.strip()]

# ---------------------------------------------------------------------------
# Location filter
#
#   remote   — only remote / worldwide / global jobs (default, recommended)
#   specific — jobs in your PREFERRED_LOCATIONS cities  (+ remote is always included)
#   any      — no location filter, show all jobs regardless of location
# ---------------------------------------------------------------------------
LOCATION_TYPE = os.getenv("LOCATION_TYPE", "remote").lower()

# Used when LOCATION_TYPE=specific  (e.g. "Dubai,Singapore,London")
_raw_locs          = os.getenv("PREFERRED_LOCATIONS", "")
PREFERRED_LOCATIONS = [loc.strip().lower() for loc in _raw_locs.split(",") if loc.strip()]
