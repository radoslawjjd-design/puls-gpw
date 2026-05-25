"""
Główny pipeline puls-gpw.

Wywołanie co 15 min przez Cloud Scheduler → Cloud Run Job → main.py → run()

Kolejność etapów:
  1. Scrape    — zbierz ogłoszenia z ostatnich WINDOW_MINUTES minut (Bankier.pl)
  2. Dedup     — odrzuć już przetworzone (BigQuery)
  3. Per ogłoszenie:
       a. Pobierz treść (PDF/HTML)
       b. Wygeneruj X-post + supervisor loop (max 3 próby)
       c. Zapisz do BigQuery
       d. Wyślij email
"""
import logging
import zoneinfo
from datetime import datetime, timedelta

from config import WINDOW_MINUTES
from notifier import email as notifier
from parser.content import extract_content
from scraper.bankier import collect_bankier
from storage import bigquery as bq
from ai.supervisor import generate_with_supervisor

logger = logging.getLogger(__name__)

WARSAW_TZ = zoneinfo.ZoneInfo("Europe/Warsaw")


def run() -> None:
    logger.info("=== puls-gpw pipeline start ===")

    try:
        bq.ensure_table_exists()
    except Exception as e:
        logger.error(f"BQ ensure_table_exists błąd: {e}")
        notifier.send_error("BQ setup failed", str(e))
        return

    since = datetime.now(WARSAW_TZ) - timedelta(minutes=WINDOW_MINUTES)
    logger.info(f"Okno: od {since.strftime('%Y-%m-%d %H:%M')} Warsaw")

    # ── Etap 1: Scraping ──────────────────────────────────────────────────────
    try:
        announcements = collect_bankier(since)
    except Exception as e:
        logger.error(f"Scraper crash: {e}")
        notifier.send_error("Scraper failed", str(e))
        return

    if not announcements:
        logger.info("Brak nowych ogłoszeń — pipeline end")
        return

    logger.info(f"Scraped: {len(announcements)} ogłoszeń")

    # ── Etap 2: Dedup ─────────────────────────────────────────────────────────
    new_announcements = []
    for ann in announcements:
        ann_id = bq.announcement_id(ann)
        if bq.is_duplicate(ann_id):
            logger.debug(f"Duplikat (skip): {ann['company']} — {ann['title'][:50]}")
        else:
            new_announcements.append(ann)

    if not new_announcements:
        logger.info(f"Wszystkie {len(announcements)} ogłoszeń już przetworzone — pipeline end")
        return

    logger.info(f"Nowe ogłoszenia do przetworzenia: {len(new_announcements)}")

    # ── Etap 3: Per ogłoszenie ────────────────────────────────────────────────
    processed = 0
    errors    = 0

    for ann in new_announcements:
        company = ann.get("company", "?")
        title   = ann.get("title", "")[:60]

        try:
            # a. Treść
            content = extract_content(ann)
            if not content:
                logger.warning(f"Brak treści: {company} — {title}")

            # b. X-post + supervisor
            xpost, score = generate_with_supervisor(ann, content)

            # c. BigQuery
            email_sent = False
            if xpost and score is not None:
                # d. Email (tylko gdy post przeszedł supervisora)
                email_sent = notifier.send_xpost(ann, xpost, score)

            bq.save_announcement(ann, content, xpost, score, email_sent=email_sent)
            processed += 1

            logger.info(
                f"OK: {company} — score={score}, email={email_sent}, "
                f"title={title}"
            )

        except Exception as e:
            errors += 1
            logger.error(f"Błąd przetwarzania {company}: {e}", exc_info=True)
            # Nie przerywamy pipeline — idziemy do następnego ogłoszenia

    logger.info(
        f"=== pipeline end: processed={processed}, errors={errors}, "
        f"total={len(new_announcements)} ==="
    )

    if errors > 0:
        notifier.send_error(
            f"Pipeline: {errors} błędów",
            f"processed={processed}, errors={errors}, total={len(new_announcements)}\n"
            f"Sprawdź logi Cloud Run Job.",
        )
