"""
Runtime configuration — all values from environment variables.
Secrets are injected by Cloud Run from Secret Manager; non-secrets have sensible defaults.
"""
import os

# ── Scraper ────────────────────────────────────────────────────────────────────
BANKIER_BASE_URL = "https://www.bankier.pl/gielda/komunikaty-espi-ebi?page={page}"
MAX_PAGES_BANKIER = 5
HTTP_TIMEOUT      = 30   # seconds per request
HTTP_RETRIES      = 3
REQUEST_DELAY     = 0.5  # seconds between requests (polite crawling)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

# How far back (minutes) to look for new announcements.
# 20-min buffer covers 15-min cron + clock skew + Bankier CDN cache lag.
WINDOW_MINUTES = int(os.environ.get("WINDOW_MINUTES", "20"))

# ── Gemini ─────────────────────────────────────────────────────────────────────
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL       = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-preview-05-20")
GEMINI_TIMEOUT     = int(os.environ.get("GEMINI_TIMEOUT", "60"))
GEMINI_MAX_RETRIES = 3  # supervisor loop max attempts

# ── BigQuery ──────────────────────────────────────────────────────────────────
BQ_PROJECT = os.environ.get("BQ_PROJECT", "puls-gpw")
BQ_DATASET  = os.environ.get("BQ_DATASET", "espi_ebi")
BQ_TABLE    = os.environ.get("BQ_TABLE", "announcements")

# ── Email / SMTP ──────────────────────────────────────────────────────────────
SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
OWNER_EMAIL   = os.environ.get("OWNER_EMAIL", "")
