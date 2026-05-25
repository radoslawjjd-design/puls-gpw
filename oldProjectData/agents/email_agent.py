"""
Agent emailowy — wysyła powiadomienia przez Gmail API.
Używa TEGO SAMEGO tokenu OAuth co drive_client.py (OAuth dla Gmail).
Jeden token, jeden refresh, zero konfliktów.

Obsługuje kilka typów emaili:
  - alert_krytyczny   — natychmiastowy alert (upadłość, przejęcie itp.)
  - alert_portfela    — nowe ogłoszenie spółki portfelowej
  - podsumowanie_dnia — codzienne podsumowanie portfela
  - raport_tygodnia   — tygodniowy raport
  - alert_dywidendy   — przypomnienie o dywidendzie
  - alert_brokera     — rekomendacja BUY/SELL/HOLD
"""
import base64
import html as _html
import logging
import re
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _esc(value) -> str:
    """PR#14 #6 fix (2026-04-20): HTML escape dla danych z external sources
    (Bankier scraper, Gemini output) wstawianych do email HTML.

    Eliminuje XSS/phishing vector — gdy Bankier zwróci `<script>` lub
    `</div><a href="evil">`, escapowanie zamieni na `&lt;script&gt;` itp.

    Akceptuje str/None/int/float — zwraca '' dla None, str dla reszty.
    """
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)

_gmail_service = None


def _get_gmail_service():
    """Buduje serwis Gmail używając WSPÓLNYCH OAuth credentials z drive_client."""
    global _gmail_service
    if _gmail_service is not None:
        return _gmail_service

    from googleapiclient.discovery import build

    from storage.drive_client import get_oauth_creds

    creds = get_oauth_creds()
    _gmail_service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    logger.info("Gmail API zainicjalizowany (współdzielony token OAuth)")
    return _gmail_service


def authorize():
    """
    Jednorazowa autoryzacja OAuth (Drive + Gmail w jednym tokenie).
    Uruchom lokalnie przed deployem:
        python -c "from storage.drive_client import get_oauth_service; get_oauth_service()"
    Potem wgraj token:
        gcloud secrets versions add drive-token --data-file=token.json --project oswiadczenia-gwp
    """
    from storage.drive_client import get_oauth_creds
    get_oauth_creds()
    logger.info("Autoryzacja OAuth zakończona sukcesem (Gmail)")


from config import EMAIL_DISCLAIMER

_DISCLAIMER_FOOTER = f"""
<div style="margin-top:24px;padding:14px 16px;border-top:1px solid #e5e7eb;
            font-size:11px;color:#9ca3af;line-height:1.5;text-align:center;">
    <p style="margin:0 0 4px 0;">
        {EMAIL_DISCLAIMER}
    </p>
    <p style="margin:0;">
        Źródło danych: komunikaty ESPI/EBI (Giełda Papierów Wartościowych w Warszawie).
    </p>
</div>"""

_DISCLAIMER_TEXT = f"\n---\n{EMAIL_DISCLAIMER} Źródło: komunikaty ESPI/EBI (GPW)."


def _send_email(to: str, subject: str, html_body: str, text_body: str = "") -> bool:
    """Wysyła email przez Gmail API. Automatycznie dodaje profesjonalną stopkę."""
    try:
        service = _get_gmail_service()

        # Dodaj stopkę disclaimer do każdego emaila
        if "</body>" in html_body:
            html_body = html_body.replace("</body>", f"{_DISCLAIMER_FOOTER}</body>")
        else:
            html_body += _DISCLAIMER_FOOTER

        if text_body:
            text_body += _DISCLAIMER_TEXT

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["To"]      = to

        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        raw     = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        message = service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        logger.info(f"Email wysłany: '{subject}' → {to} (ID: {message['id']})")
        return True

    except Exception as e:
        logger.error(f"Błąd wysyłania emaila '{subject}': {e}")
        return False


# ── Szablony emaili ────────────────────────────────────────────────────────────

def send_alert_krytyczny(
    to: str,
    spolka: str,
    typ_zdarzenia: str,
    tytul: str,
    podsumowanie: str,
    data: date,
) -> bool:
    """
    PILNY alert — upadłość, przejęcie, zawieszenie obrotu, delisting itp.
    Wysyłany natychmiast po wykryciu przez analyze.py.
    """
    subject = f"ADMIN 🚨 PILNE: {spolka} — {typ_zdarzenia} | {data.strftime('%d.%m.%Y')}"

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: #dc2626; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
        <h1 style="margin: 0; font-size: 22px;">🚨 ALERT KRYTYCZNY</h1>
        <p style="margin: 5px 0 0 0; opacity: 0.9;">{data.strftime('%d.%m.%Y')}</p>
    </div>
    <div style="background: #fef2f2; border: 2px solid #dc2626; padding: 20px; border-radius: 0 0 8px 8px;">
        <h2 style="color: #dc2626; margin-top: 0;">{spolka}</h2>
        <p><strong>Zdarzenie:</strong> {typ_zdarzenia}</p>
        <p><strong>Ogłoszenie:</strong> {tytul}</p>
        <hr style="border-color: #fca5a5;">
        <p style="color: #7f1d1d;">{podsumowanie}</p>
        <p style="font-size: 12px; color: #991b1b; margin-top: 20px;">
            ⚠️ Sprawdź sytuację natychmiast w BigQuery (tabela analyses) dla spółki {spolka}
        </p>
    </div>
    </body></html>
    """

    text = f"ALERT KRYTYCZNY: {spolka} — {typ_zdarzenia}\n{tytul}\n\n{podsumowanie}"
    return _send_email(to, subject, html, text)


def send_alert_portfela(
    to: str,
    spolka: str,
    tytul: str,
    sentiment: str,
    waga: str,
    podsumowanie: str,
    data: date,
) -> bool:
    """
    Alert o nowym ogłoszeniu spółki portfelowej.
    Wysyłany przez watcher.py co 10 minut.
    """
    sentiment_icon = {"pozytywny": "📈", "negatywny": "📉", "neutralny": "➡️"}.get(sentiment, "•")
    waga_color     = {"wysoka": "#dc2626", "srednia": "#d97706", "niska": "#6b7280"}.get(waga, "#6b7280")

    subject = f"ADMIN {sentiment_icon} {spolka}: {tytul[:60]} | {data.strftime('%d.%m.%Y')}"

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: #1e40af; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0; font-size: 18px;">{sentiment_icon} Nowe ogłoszenie portfelowe</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8; font-size: 13px;">{data.strftime('%d.%m.%Y')}</p>
    </div>
    <div style="background: #eff6ff; border: 1px solid #bfdbfe; padding: 20px; border-radius: 0 0 8px 8px;">
        <h3 style="color: #1e40af; margin-top: 0;">{spolka}</h3>
        <p style="font-weight: bold;">{tytul}</p>
        <p>
            <span style="color: {waga_color}; font-weight: bold;">Waga: {waga.upper()}</span>
            &nbsp;|&nbsp;
            Sentiment: {sentiment}
        </p>
        <hr style="border-color: #bfdbfe;">
        <p>{podsumowanie}</p>
    </div>
    </body></html>
    """

    text = f"Nowe ogłoszenie: {spolka}\n{tytul}\nWaga: {waga} | Sentiment: {sentiment}\n\n{podsumowanie}"
    return _send_email(to, subject, html, text)


def render_podsumowanie_public(
    date_str: str,
    liczba_ogloszen: int,
    top_pozytywne: list[dict],
    top_negatywne: list[dict],
) -> tuple[str, str]:
    """Renderuje PUBLIC podsumowanie — zwraca (subject, html). Bez wysyłki."""
    all_top = list(top_pozytywne) + list(top_negatywne)
    cards_html = ""
    for t in all_top:
        cards_html += f"""
        <div style="background:white;border:1px solid #e5e7eb;border-left:4px solid #6b7280;
                    border-radius:8px;padding:12px 16px;margin-bottom:8px;">
            <span style="font-size:14px;font-weight:bold;color:#111827;">{t.get('spolka','?')}</span>
            <div style="font-size:13px;color:#374151;margin-top:4px;">{t.get('tytul','?')}</div>
        </div>"""

    subject = f"PUBLIC 📊 Przegląd rynku | {date_str}"
    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: #059669; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">📊 Przegląd rynku GPW</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8;">{date_str}</p>
    </div>
    <div style="background: #f9fafb; border: 1px solid #e5e7eb; padding: 20px; border-radius: 0 0 8px 8px;">
        <div style="background: white; padding: 14px 20px; border-radius: 8px; border: 1px solid #e5e7eb; text-align: center; margin-bottom: 20px;">
            <div style="font-size: 28px; font-weight: bold; color: #111827;">📊 {liczba_ogloszen}</div>
            <div style="font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em;">ogłoszeń ESPI/EBI</div>
        </div>

        <h3 style="margin:0 0 12px 0;color:#374151;font-size:14px;text-transform:uppercase;letter-spacing:0.05em;">
            📋 Ogłoszenia dnia
        </h3>
        {cards_html}

        <div style="margin-top:16px;padding:10px;background:white;border-radius:6px;border:1px solid #e5e7eb;font-size:11px;color:#9ca3af;text-align:center;">
            Źródło: ESPI/EBI &nbsp;|&nbsp; Nie stanowi rekomendacji inwestycyjnej
        </div>
    </div>
    </body></html>
    """
    return subject, html


def send_podsumowanie_dnia(
    to: str,
    data: date,
    liczba_ogloszen: int,
    sentyment_score: float,
    spolki_portfelowe: list[dict],
    top_pozytywne: list[dict],
    top_negatywne: list[dict],
    alerty: list[str],
    podsumowanie_tekst: str,
    public: bool = False,
) -> bool:
    """
    Codzienne podsumowanie portfela.
    Wysyłane po summarize.py --mode both.

    public=False → PRIVATE: pełna wersja z sentymentem, rekomendacjami, alertami
    public=True  → PUBLIC:  surowe fakty bez ocen, rekomendacji i sentymentu
    """
    date_str = data.strftime('%d.%m.%Y')

    # ── Helper: tabela top N ogłoszeń ─────────────────────────────────────────
    def _top_table(items: list[dict], title: str, accent_color: str, bg_color: str, icon: str) -> str:
        if not items:
            return ""
        rows = ""
        for t in items:
            rows += f"""
                <tr>
                    <td style="padding:8px;border-bottom:1px solid #e5e7eb;font-weight:bold;white-space:nowrap;vertical-align:top;">{t.get('spolka','?')}</td>
                    <td style="padding:8px;border-bottom:1px solid #e5e7eb;font-size:13px;">
                        <div>{t.get('tytul','?')}</div>
                        <div style="color:#6b7280;font-size:12px;margin-top:3px;">{t.get('dlaczego_wazne','')}</div>
                    </td>
                </tr>"""
        return f"""
        <div style="margin-top:20px;">
            <h3 style="margin:0 0 8px 0;color:{accent_color};font-size:14px;text-transform:uppercase;letter-spacing:0.05em;">{icon} {title}</h3>
            <table style="width:100%;border-collapse:collapse;background:white;border-radius:6px;overflow:hidden;border:1px solid {bg_color};">
                <thead><tr style="background:{bg_color};">
                    <th style="padding:10px;text-align:left;font-size:13px;color:{accent_color};">Spółka</th>
                    <th style="padding:10px;text-align:left;font-size:13px;color:{accent_color};">Ogłoszenie</th>
                </tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>"""

    # ── PUBLIC — surowe fakty rynkowe (bez portfela, bez sentymentu) ────────
    if public:
        subject, html = render_podsumowanie_public(date_str, liczba_ogloszen, top_pozytywne, top_negatywne)
        return _send_email(to, subject, html)

    # ── PRIVATE — pełna wersja ────────────────────────────────────────────────
    score_color = "#16a34a" if sentyment_score > 0.1 else "#dc2626" if sentyment_score < -0.1 else "#6b7280"
    score_sign  = "+" if sentyment_score > 0 else ""

    spolki_html = ""
    for p in spolki_portfelowe:
        rec      = p.get("rekomendacja", "?")
        rec_icon = {
            "rozważ_zwiększenie":  "🟢",
            "obserwuj":            "🟡",
            "trzymaj":             "⚪",
            "rozważ_zmniejszenie": "🔴",
        }.get(rec, "•")
        sentyment    = p.get("sentyment_okresu", "?")
        uzasadnienie = p.get("uzasadnienie", "")
        wydarzenia   = p.get("kluczowe_wydarzenia", [])
        brak_ogloszen = p.get("liczba_ogloszen", 0) == 0

        wydarzenia_html = ""
        if wydarzenia:
            items = "".join(f"<li style='margin-bottom:2px;'>{w}</li>" for w in wydarzenia)
            wydarzenia_html = f"<ul style='margin:4px 0 0 0;padding-left:16px;font-size:12px;color:#6b7280;'>{items}</ul>"

        uzasadnienie_html = ""
        if uzasadnienie:
            uzasadnienie_html = f"<div style='font-size:12px;color:#374151;margin-top:4px;font-style:italic;'>{uzasadnienie}</div>"

        bg = "#f9fafb" if brak_ogloszen else "white"
        spolki_html += f"""
        <tr style="background:{bg};">
            <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;font-weight:bold;white-space:nowrap;vertical-align:top;">{p.get('spolka','?')}</td>
            <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top;">
                <div style="font-size:13px;">{"<span style='color:#9ca3af;font-style:italic;'>brak ogłoszeń</span>" if brak_ogloszen else sentyment}</div>
                {wydarzenia_html}
                {uzasadnienie_html}
            </td>
            <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;white-space:nowrap;vertical-align:top;font-size:13px;">{rec_icon} {rec}</td>
        </tr>"""

    top_html = (
        _top_table(top_pozytywne, "Top 5 pozytywnych", "#16a34a", "#dcfce7", "▲") +
        _top_table(top_negatywne, "Top 5 negatywnych", "#dc2626", "#fee2e2", "▼")
    )

    alerty_html = ""
    if alerty:
        alerty_html = "<div style='background:#fef3c7; border:1px solid #fcd34d; padding:12px; border-radius:6px; margin-top:16px;'>"
        alerty_html += "<strong>🚨 Alerty:</strong><ul style='margin:8px 0 0 0;'>"
        for a in alerty:
            alerty_html += f"<li>{a}</li>"
        alerty_html += "</ul></div>"

    subject = f"ADMIN 📊 Podsumowanie portfela | {date_str}"

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: #059669; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">📊 Podsumowanie portfela</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8;">{date_str}</p>
    </div>
    <div style="background: #f0fdf4; border: 1px solid #bbf7d0; padding: 20px; border-radius: 0 0 8px 8px;">
        <div style="display: flex; gap: 20px; margin-bottom: 16px;">
            <div style="background: white; padding: 12px 20px; border-radius: 6px; border: 1px solid #d1fae5; text-align: center;">
                <div style="font-size: 24px; font-weight: bold;">{liczba_ogloszen}</div>
                <div style="font-size: 12px; color: #6b7280;">ogłoszeń</div>
            </div>
            <div style="background: white; padding: 12px 20px; border-radius: 6px; border: 1px solid #d1fae5; text-align: center;">
                <div style="font-size: 24px; font-weight: bold; color: {score_color};">{score_sign}{sentyment_score:.2f}</div>
                <div style="font-size: 12px; color: #6b7280;">score sentymentu</div>
            </div>
        </div>

        <table style="width: 100%; border-collapse: collapse; background: white; border-radius: 6px; overflow: hidden;">
            <thead>
                <tr style="background: #d1fae5;">
                    <th style="padding: 10px; text-align: left;">Spółka</th>
                    <th style="padding: 10px; text-align: left;">Sentyment</th>
                    <th style="padding: 10px; text-align: left;">Rekomendacja</th>
                </tr>
            </thead>
            <tbody>{spolki_html}</tbody>
        </table>

        {top_html}

        {alerty_html}

        <div style="margin-top: 16px; padding: 12px; background: white; border-radius: 6px; border: 1px solid #d1fae5;">
            <p style="margin: 0; color: #374151;">{podsumowanie_tekst}</p>
        </div>
    </div>
    </body></html>
    """

    return _send_email(to, subject, html)

def send_brak_ogloszen_portfela(
    to: str,
    data: date,
) -> bool:
    """
    Email wysyłany gdy brak ogłoszeń spółek portfelowych danego dnia.
    Służy jako potwierdzenie że pipeline działa poprawnie.
    """
    subject = f"ADMIN ✅ Pipeline OK — brak ogłoszeń portfela | {data.strftime('%d.%m.%Y')}"
    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: #059669; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">✅ Pipeline działa poprawnie</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8;">{data.strftime('%d.%m.%Y')}</p>
    </div>
    <div style="background: #f0fdf4; border: 1px solid #bbf7d0; padding: 20px; border-radius: 0 0 8px 8px;">
        <p style="color: #374151; font-size: 16px;">
            Spółki z Twojego portfela nie opublikowały żadnych ogłoszeń ESPI/EBI w tym dniu.
        </p>
        <div style="background: white; padding: 12px 16px; border-radius: 6px; border: 1px solid #d1fae5; color: #6b7280; font-size: 14px;">
            Scraping ✓ &nbsp;|&nbsp; Zapis BQ ✓ &nbsp;|&nbsp; Analiza ✓ &nbsp;|&nbsp; Podsumowanie ✓
        </div>
        <p style="color: #6b7280; font-size: 13px; margin-top: 16px;">
            Ogłoszenia innych spółek mogły zostać zapisane w BigQuery i przeanalizowane.
        </p>
    </div>
    </body></html>
    """
    return _send_email(to, subject, html)

def send_personalized_wallet(
    to: str,
    data: date,
    sentyment_score: float,
    spolki_portfelowe: list[dict],
    alerty: list[str],
    podsumowanie_tekst: str,
) -> bool:
    """
    PRIVATE-only email z analizą portfela inwestycyjnego.
    Wyodrębniony z podsumowania dnia — zawiera TYLKO sekcję portfelową.

    Zawiera:
    - Score sentymentu portfela
    - Tabelę spółek portfelowych z sentymentem, rekomendacjami, uzasadnieniami
    - Alerty
    - Podsumowanie brokera
    """
    date_str = data.strftime('%d.%m.%Y')
    score_color = "#16a34a" if sentyment_score > 0.1 else "#dc2626" if sentyment_score < -0.1 else "#6b7280"
    score_sign  = "+" if sentyment_score > 0 else ""

    spolki_html = ""
    for p in spolki_portfelowe:
        rec      = p.get("rekomendacja", "?")
        rec_icon = {
            "rozważ_zwiększenie":  "🟢",
            "obserwuj":            "🟡",
            "trzymaj":             "⚪",
            "rozważ_zmniejszenie": "🔴",
        }.get(rec, "•")
        sentyment    = p.get("sentyment_okresu", "?")
        uzasadnienie = p.get("uzasadnienie", "")
        wydarzenia   = p.get("kluczowe_wydarzenia", [])
        brak_ogloszen = p.get("liczba_ogloszen", 0) == 0

        wydarzenia_html = ""
        if wydarzenia:
            items = "".join(f"<li style='margin-bottom:2px;'>{w}</li>" for w in wydarzenia)
            wydarzenia_html = f"<ul style='margin:4px 0 0 0;padding-left:16px;font-size:12px;color:#6b7280;'>{items}</ul>"

        uzasadnienie_html = ""
        if uzasadnienie:
            uzasadnienie_html = f"<div style='font-size:12px;color:#374151;margin-top:4px;font-style:italic;'>{uzasadnienie}</div>"

        bg = "#f9fafb" if brak_ogloszen else "white"
        spolki_html += f"""
        <tr style="background:{bg};">
            <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;font-weight:bold;white-space:nowrap;vertical-align:top;">{p.get('spolka','?')}</td>
            <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top;">
                <div style="font-size:13px;">{"<span style='color:#9ca3af;font-style:italic;'>brak ogłoszeń</span>" if brak_ogloszen else sentyment}</div>
                {wydarzenia_html}
                {uzasadnienie_html}
            </td>
            <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;white-space:nowrap;vertical-align:top;font-size:13px;">{rec_icon} {rec}</td>
        </tr>"""

    alerty_html = ""
    if alerty:
        alerty_html = "<div style='background:#fef3c7; border:1px solid #fcd34d; padding:12px; border-radius:6px; margin-top:16px;'>"
        alerty_html += "<strong>🚨 Alerty:</strong><ul style='margin:8px 0 0 0;'>"
        for a in alerty:
            alerty_html += f"<li>{a}</li>"
        alerty_html += "</ul></div>"

    subject = f"ADMIN 💼 Portfel | {date_str}"

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: #7c3aed; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">💼 Portfel inwestycyjny</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8;">{date_str}</p>
    </div>
    <div style="background: #f5f3ff; border: 1px solid #c4b5fd; padding: 20px; border-radius: 0 0 8px 8px;">
        <div style="background: white; padding: 12px 20px; border-radius: 6px; border: 1px solid #ddd6fe; text-align: center; margin-bottom: 16px;">
            <div style="font-size: 24px; font-weight: bold; color: {score_color};">{score_sign}{sentyment_score:.2f}</div>
            <div style="font-size: 12px; color: #6b7280;">score sentymentu portfela</div>
        </div>

        <table style="width: 100%; border-collapse: collapse; background: white; border-radius: 6px; overflow: hidden;">
            <thead>
                <tr style="background: #ddd6fe;">
                    <th style="padding: 10px; text-align: left;">Spółka</th>
                    <th style="padding: 10px; text-align: left;">Sentyment</th>
                    <th style="padding: 10px; text-align: left;">Rekomendacja</th>
                </tr>
            </thead>
            <tbody>{spolki_html}</tbody>
        </table>

        {alerty_html}

        <div style="margin-top: 16px; padding: 12px; background: white; border-radius: 6px; border: 1px solid #ddd6fe;">
            <p style="margin: 0; color: #374151;">{podsumowanie_tekst}</p>
        </div>
    </div>
    </body></html>
    """

    return _send_email(to, subject, html)


def send_alert_dywidendy(
    to: str,
    spolka: str,
    kwota_na_akcje: float,
    data_ustalenia_prawa: date,
    data_wyplaty: date,
    dni_pozostalo: int,
) -> bool:
    """Przypomnienie o zbliżającej się dacie ustalenia prawa do dywidendy."""
    pilnosc = "🔴 JUTRO" if dni_pozostalo <= 1 else f"🟡 Za {dni_pozostalo} dni"
    subject = f"ADMIN {pilnosc}: Dywidenda {spolka} — {kwota_na_akcje:.2f} zł/akcję"

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: #7c3aed; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">💰 Przypomnienie o dywidendzie</h2>
    </div>
    <div style="background: #faf5ff; border: 1px solid #ddd6fe; padding: 20px; border-radius: 0 0 8px 8px;">
        <h3 style="color: #7c3aed; margin-top: 0;">{spolka}</h3>
        <p><strong>Kwota:</strong> {kwota_na_akcje:.2f} zł na akcję</p>
        <p><strong>Ostatni dzień zakupu:</strong> {data_ustalenia_prawa.strftime('%d.%m.%Y')} ({pilnosc})</p>
        <p><strong>Data wypłaty:</strong> {data_wyplaty.strftime('%d.%m.%Y')}</p>
        <p style="color: #6d28d9; font-weight: bold;">
            Aby otrzymać dywidendę musisz posiadać akcje przed {data_ustalenia_prawa.strftime('%d.%m.%Y')}.
        </p>
    </div>
    </body></html>
    """

    text = f"Dywidenda {spolka}: {kwota_na_akcje:.2f} zł/akcję\nOstatni dzień zakupu: {data_ustalenia_prawa}\nWypłata: {data_wyplaty}"
    return _send_email(to, subject, html, text)


def send_alert_brokera(
    to: str,
    spolka: str,
    rekomendacja: str,
    uzasadnienie: str,
    data: date,
) -> bool:
    """Alert o rekomendacji brokera BUY/SELL/HOLD."""
    icons = {
        "BUY":  ("🟢", "#16a34a"),
        "SELL": ("🔴", "#dc2626"),
        "HOLD": ("⚪", "#6b7280"),
    }
    icon, color = icons.get(rekomendacja.upper(), ("•", "#6b7280"))
    subject = f"ADMIN {icon} Broker: {rekomendacja.upper()} {spolka}"

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: {color}; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">{icon} Rekomendacja brokera: {rekomendacja.upper()}</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8;">{data.strftime('%d.%m.%Y')}</p>
    </div>
    <div style="padding: 20px; border: 1px solid #e5e7eb; border-radius: 0 0 8px 8px;">
        <h3 style="margin-top: 0;">{spolka}</h3>
        <p>{uzasadnienie}</p>
    </div>
    </body></html>
    """

    return _send_email(to, subject, html, uzasadnienie)


def send_digest(
    to: str,
    data: date,
    dywidendy: list[dict],
    wyniki: list[dict],
    public: bool = False,
    _render_only: bool = False,
) -> bool | tuple[str, str]:
    """
    Pigułka tematyczna: dywidendy + wyniki finansowe dnia.
    Wysyłana tylko gdy jest co najmniej jedna dywidenda LUB wynik finansowy.

    public=False → PRIVATE: pełna wersja z sentymentem przy wynikach
    public=True  → PUBLIC:  surowe fakty — dywidendy + wyniki bez sentymentu
    """
    date_str = data.strftime('%d.%m.%Y')

    # ── Sekcja dywidend (wspólna — to surowe fakty) ─────────────────────────────
    def _fmt_zmiana(a: dict) -> str:
        if not a:
            return ""
        parts = []
        if a.get("rekord"):
            parts.append("<span style='color:#dc2626;font-weight:bold;'>🏆 REKORD!</span>")
        if a.get("pierwsza"):
            parts.append("<span style='color:#7c3aed;'>pierwsza dywidenda</span>")
        if a.get("zmiana_rr") is not None:
            sign  = "+" if a["zmiana_rr"] > 0 else ""
            color = "#16a34a" if a["zmiana_rr"] > 0 else "#dc2626"
            parts.append(f"<span style='color:{color};font-weight:bold;'>{sign}{a['zmiana_rr']}% r/r</span>")
        if a.get("trend"):
            parts.append(f"<span style='color:#6b7280;'>trend: {a['trend']}</span>")
        return " &nbsp;|&nbsp; ".join(parts)

    def _fmt_historia(historia: list[dict]) -> str:
        if not historia:
            return "<em>brak danych historycznych</em>"
        rows = "".join(
            f"<tr><td style='padding:2px 8px;'>{h['rok']}</td>"
            f"<td style='padding:2px 8px;font-weight:bold;'>{h['dywidenda']:.2f} zł</td>"
            f"<td style='padding:2px 8px;color:#6b7280;'>"
            f"{h['stopa_proc']:.1f}%" if h.get('stopa_proc') else ""
            "</td></tr>"
            for h in historia
        )
        return (
            f"<table style='font-size:12px;border-collapse:collapse;'>"
            f"<tr><th style='padding:2px 8px;color:#6b7280;text-align:left;'>Rok</th>"
            f"<th style='padding:2px 8px;color:#6b7280;text-align:left;'>Dywidenda</th>"
            f"<th style='padding:2px 8px;color:#6b7280;text-align:left;'>Stopa</th></tr>"
            f"{rows}</table>"
        )

    dyw_html = ""
    for d in dywidendy:
        kwota_str = f"<strong>{d['kwota']:.2f} zł/akcję</strong>" if d.get("kwota") else ""
        analiza   = d.get("analiza") or {}
        zmiana    = _fmt_zmiana(analiza)
        prev_str  = ""
        if analiza.get("poprzednia"):
            prev_str = f"Poprzednia: {analiza['poprzednia']:.2f} zł"
        if analiza.get("lat_historii"):
            prev_str += f" &nbsp;·&nbsp; Historia: {analiza['lat_historii']} lat"

        dyw_html += f"""
        <div style="border:1px solid #e5e7eb;border-left:4px solid #16a34a;
                    border-radius:6px;padding:14px;margin-bottom:10px;">
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">
                <span style="font-size:15px;font-weight:bold;">{_esc(d['spolka'])}</span>
                <span style="font-size:14px;">{kwota_str}</span>
            </div>
            <div style="font-size:13px;color:#374151;margin-bottom:4px;">{_esc(d['title'])}</div>
            {f'<div style="margin:6px 0;">{zmiana}</div>' if zmiana else ''}
            {f'<div style="font-size:12px;color:#6b7280;margin-bottom:8px;">{_esc(prev_str)}</div>' if prev_str else ''}
            {_fmt_historia(d.get("historia", []))}
            {f'<div style="font-size:12px;color:#6b7280;margin-top:8px;">{_esc(d["opis"])}</div>' if d.get("opis") else ''}
        </div>"""

    # ── Sekcja wyników finansowych ───────────────────────────────────────────────
    wyn_html = ""
    if public:
        # PUBLIC — bez sentymentu, neutralny border
        for w in wyniki:
            kluczowy = w.get("kluczowy") or w.get("opis") or ""
            wyn_html += f"""
            <div style="border:1px solid #e5e7eb;border-left:4px solid #6b7280;
                        border-radius:6px;padding:14px;margin-bottom:10px;">
                <div style="margin-bottom:6px;">
                    <span style="font-size:15px;font-weight:bold;">{_esc(w['spolka'])}</span>
                </div>
                <div style="font-size:13px;color:#374151;margin-bottom:6px;">{_esc(w['title'])}</div>
                {f'<div style="font-size:12px;color:#6b7280;font-style:italic;">{_esc(kluczowy[:250])}</div>' if kluczowy else ''}
            </div>"""
    else:
        # PRIVATE — z sentymentem i kolorowymi ikonami
        _sent_colors = {"pozytywny": "#16a34a", "negatywny": "#dc2626", "neutralny": "#6b7280"}
        _sent_icons  = {"pozytywny": "🟢", "negatywny": "🔴", "neutralny": "⚪"}
        for w in wyniki:
            color = _sent_colors.get(w["sentiment"], "#6b7280")
            icon  = _sent_icons.get(w["sentiment"], "⚪")
            kluczowy = w.get("kluczowy") or w.get("opis") or ""
            wyn_html += f"""
            <div style="border:1px solid #e5e7eb;border-left:4px solid {color};
                        border-radius:6px;padding:14px;margin-bottom:10px;">
                <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:6px;">
                    <span style="font-size:15px;font-weight:bold;">{w['spolka']}</span>
                    <span style="font-size:12px;color:{color};">{icon} {w['sentiment']}</span>
                </div>
                <div style="font-size:13px;color:#374151;margin-bottom:6px;">{w['title']}</div>
                {f'<div style="font-size:12px;color:#6b7280;font-style:italic;">{kluczowy[:250]}</div>' if kluczowy else ''}
            </div>"""

    # ── Buduj email ──────────────────────────────────────────────────────────────
    dyw_section = ""
    if dywidendy:
        dyw_section = f"""
        <h3 style="margin:20px 0 10px;color:#166534;">
            💰 Dywidendy ({len(dywidendy)} {"spółka" if len(dywidendy) == 1 else "spółki" if len(dywidendy) < 5 else "spółek"})
        </h3>
        {dyw_html}"""

    wyn_section = ""
    if wyniki:
        wyn_section = f"""
        <h3 style="margin:20px 0 10px;color:#1e40af;">
            📊 Wyniki finansowe ({len(wyniki)} {"ogłoszenie" if len(wyniki) == 1 else "ogłoszenia" if len(wyniki) < 5 else "ogłoszeń"})
        </h3>
        {wyn_html}"""

    subject = f"PUBLIC 📋 Pigułka | {date_str}" if public else f"ADMIN 📋 Pigułka | {date_str}"

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:8px;">
    <div style="background:#1e40af;color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">📋 Pigułka tematyczna</h2>
        <p style="margin:4px 0 0 0;opacity:0.85;font-size:13px;">
            {date_str}
            &nbsp;|&nbsp; {len(dywidendy)} dywidend
            &nbsp;|&nbsp; {len(wyniki)} wyników finansowych
        </p>
    </div>
    <div style="border:1px solid #e5e7eb;border-top:none;padding:20px;border-radius:0 0 8px 8px;">
        {dyw_section}
        {wyn_section}
    </div>
    </body></html>
    """
    if _render_only:
        return subject, html
    return _send_email(to, subject, html)


def _strip_sentiment_markers(text: str) -> str:
    """
    Usuwa markery sentymentu z tekstu tweeta dla wersji PUBLIC.

    Usuwane elementy:
    - Linia sentymentu: ▲28 ▼4 ●67 | 99 ogłoszeń
    - Nagłówki sekcji: 📈 POZYTYWNE/Pozytywne, 📉 NEGATYWNE/Negatywne, 💡 KOMENTARZ
    - Prefiksy ▲/▼/● przed wpisami (ale zachowuje resztę linii)
    - Linia "Sentyment dnia: ..."
    - Statystyki "X pozytywnych / Y negatywnych / Z neutralnych"
    """
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()

        # Usuń linię sentymentu: "▲28 ▼4 ●67 | 99 ogłoszeń" lub "▲94 ▼93 ●316 🧵"
        # lub "GPW 23.03 | ▲94 ▼93 ●316 🧵"
        if re.search(r'▲\d+\s*▼\d+\s*●\d+', stripped):
            continue

        # Usuń nagłówki sekcji sentymentu (stare i nowe warianty)
        if re.match(r'^📈\s*(POZYTYWNE|Pozytywne)', stripped):
            continue
        if re.match(r'^📉\s*(NEGATYWNE|Negatywne)', stripped):
            continue
        if re.match(r'^💡\s*KOMENTARZ', stripped):
            continue
        if re.match(r'^📈\s*TOP\s+POZYTYWNE', stripped):
            continue
        if re.match(r'^📉\s*TOP\s+NEGATYWNE', stripped):
            continue

        # Usuń "Sentyment dnia:" i linie z "pozytywnych / negatywnych / neutralnych"
        if "Sentyment dnia" in stripped:
            continue
        if re.search(r'pozytywnych\s*/\s*▼.*negatywnych\s*/\s*●.*neutralnych', stripped):
            continue

        # Usuń prefiksy ▲/▼/● z początku linii (ale zachowaj resztę)
        line = re.sub(r'^(\s*)[▲▼●]\s*', r'\1', line)

        cleaned.append(line)

    # Usuń nadmiarowe puste linie (max 2 z rzędu)
    result = "\n".join(cleaned)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def _gemini_review_html(gemini_review: str | None) -> str:
    """Sekcja z komentarzem Gemini na końcu emaila xpost preview."""
    if not gemini_review:
        return ""
    import html as html_mod
    escaped = html_mod.escape(gemini_review).replace("\n", "<br>")
    return f"""
    <div style="margin-top:16px;padding:14px;background:#f0f4ff;
                border:1px solid #bfdbfe;border-radius:6px;font-size:12px;color:#1e3a5f;">
        <strong>🤖 Weryfikacja Gemini</strong>
        <div style="margin-top:8px;line-height:1.6;">{escaped}</div>
    </div>"""


def _format_tweet_html(text: str) -> str:
    """
    Formatuje tekst tweeta do HTML w emailu preview.
    - $TICKER → pogrubiony + kolorowy badge
    - **bold** → <strong>
    - ▲/▼ → kolorowe markery
    - Zachowuje emoji i newlines
    """
    import html as html_mod
    escaped = html_mod.escape(text)

    # **bold** → <strong>
    escaped = re.sub(
        r'\*\*(.+?)\*\*',
        r'<strong>\1</strong>',
        escaped,
    )

    # $TICKER → kolorowy badge
    escaped = re.sub(
        r'\$([A-ZĄĆĘŁŃÓŚŹŻ][A-ZĄĆĘŁŃÓŚŹŻ0-9]{1,})\b',
        r'<span style="color:#0369a1;font-weight:bold;">$\1</span>',
        escaped,
    )

    # ▲ → zielony, ▼ → czerwony
    escaped = escaped.replace(
        '▲',
        '<span style="color:#16a34a;font-weight:bold;">▲</span>',
    )
    escaped = escaped.replace(
        '▼',
        '<span style="color:#dc2626;font-weight:bold;">▼</span>',
    )

    # ● → szary (neutralne ogłoszenia)
    escaped = escaped.replace(
        '●',
        '<span style="color:#6b7280;font-weight:bold;">●</span>',
    )

    # ⚠️ → pomarańczowy
    escaped = escaped.replace(
        '⚠️',
        '<span style="color:#d97706;font-weight:bold;">⚠️</span>',
    )

    return escaped


def _render_tier_stats_html(tier_stats: dict) -> str:
    """Renderuje blok statystyk tier selekcji do wstawienia w emailu."""
    if not tier_stats:
        return ""
    tier_labels = {1: "T1 portfel", 2: "T2 WIG20", 3: "T3 mid-cap"}
    parts = []
    for tier_num in sorted(tier_stats.keys()):
        s = tier_stats[tier_num]
        avail = s.get("available", 0)
        selected = s.get("selected", 0)
        tickers = s.get("tickers") or []
        label = tier_labels.get(tier_num, f"T{tier_num} reszta")
        ticker_str = f" • {', '.join(t for t in tickers if t)}" if tickers else ""
        color = "#16a34a" if selected > 0 else "#9ca3af"
        parts.append(
            f"<span style='color:{color};'><b>{label}:</b> {selected}/{avail}{ticker_str}</span>"
        )
    return (
        "<div style='font-size:11px;color:#6b7280;background:#f3f4f6;"
        "border-radius:6px;padding:8px 12px;margin-bottom:12px;'>"
        "🎯 Tier selekcja: &nbsp;"
        + " &nbsp;|&nbsp; ".join(parts)
        + "</div>"
    )


def send_xpost_preview(
    to: str,
    data: date,
    window: str,
    post_data: dict,
    quality_warning: bool = False,
    quality_score: int | None = None,
    public: bool = False,
    gemini_review: str | None = None,
) -> bool:
    """
    Wysyła email z podglądem postów X przed publikacją.

    public=False → PRIVATE: karty tweetów + validation score + quality warning + tryb preview
    public=True  → PUBLIC:  tylko treść tweetów (to co pójdzie na X) + gemini review
    """
    date_str = data.strftime('%d.%m.%Y')
    window_labels = {
        "premarket":    ("🌙 Premarket",   "00:00–08:45", "#6366f1"),
        "morning":      ("🌅 Sesja",       "08:46–12:59", "#0ea5e9"),
        "afternoon":    ("🌤️ Popołudnie",  "13:00–17:29", "#f59e0b"),
        "afterhours":   ("🌇 Po sesji",    "17:30–23:59", "#ef4444"),
        "daily_thread": ("🌆 Podsumowanie","Cały dzień",  "#7c3aed"),
        "agenda":       ("📅 Agenda",      "15:00",       "#059669"),
    }
    label, okno_str, color = window_labels.get(window, ("📊", "", "#374151"))

    tweets    = post_data.get("tweets", [])
    is_thread = post_data.get("is_thread", False)
    total     = len(tweets)

    # ── PUBLIC — tylko treść tweetów (bez markerów sentymentu) ──────────────
    if public:
        tweet_cards = ""
        for i, tweet_text in enumerate(tweets, 1):
            # Strip sentiment markers z treści tweeta
            clean_text = _strip_sentiment_markers(tweet_text)

            thread_badge = (
                f"<span style='background:#e0f2fe;color:#0369a1;padding:2px 8px;"
                f"border-radius:10px;font-size:11px;font-weight:bold;margin-left:8px;'>"
                f"Tweet {i}/{total}</span>"
            ) if is_thread else ""

            tweet_cards += f"""
            <div style="background:#f9fafb;border:1px solid #e5e7eb;border-left:4px solid {color};
                        border-radius:8px;padding:16px;margin-bottom:12px;">
                <div style="margin-bottom:10px;">
                    <span style="font-size:13px;font-weight:bold;color:{color};">
                        𝕏 Post{thread_badge}
                    </span>
                </div>
                <div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7;
                            color:#111827;word-break:break-word;margin:0;">{_format_tweet_html(clean_text).replace(chr(10), '<br>')}</div>
            </div>"""

        # F6.4 hotfix: czytelna nazwa okna w subject (user feedback —
        # samo "🌙" zamiast "Premarket" jest mało czytelne w skrzynce).
        window_subject_names = {
            "premarket":    "Premarket",
            "morning":      "Sesja",
            "afternoon":    "Popołudnie",
            "afterhours":   "Po sesji",
            "daily_thread": "Daily Thread",
            "agenda":       "Agenda",
            "saturday":     "Tydzień (sobota)",
            "sunday":       "Makro (niedziela)",
            "weekly_dividends": "Dywidendy tygodnia",
            "weekly_agenda":    "Agenda tygodnia",
            "index_daily":      "Indeksy GPW",
            "quotes":           "Cytaty",
        }
        win_name = window_subject_names.get(window, window.title())
        score_tag = f" ⭐{quality_score}/10" if quality_score is not None else ""
        thread_tag = f" 🧵{total}" if is_thread else ""
        subject = f"PUBLIC 𝕏 {label} {win_name}{thread_tag}{score_tag} | {date_str}"
        tier_stats_html = _render_tier_stats_html(post_data.get("tier_stats", {}))
        html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;padding:8px;">
        <div style="background:{color};color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">𝕏 {label}</h2>
            <p style="margin:4px 0 0 0;opacity:0.85;font-size:13px;">
                {date_str} &nbsp;|&nbsp; {okno_str}
                {"&nbsp;|&nbsp; 🧵 Wątek " + str(total) + " tweetów" if is_thread else ""}
                {("&nbsp;|&nbsp; ⭐ " + str(quality_score) + "/10") if quality_score is not None else ""}
            </p>
        </div>
        <div style="border:1px solid #e5e7eb;border-top:none;padding:20px;border-radius:0 0 8px 8px;">
            {tier_stats_html}
            {tweet_cards}
            {_gemini_review_html(gemini_review)}
        </div>
        </body></html>
        """
        return _send_email(to, subject, html)

    # ── PRIVATE — pełna wersja z quality info ────────────────────────────────
    liczba = post_data.get("liczba_analiz", 0)
    score_tag = f"⭐ {quality_score}/10 | " if quality_score is not None else ""
    subject = (
        f"ADMIN ⚠️ X Preview {score_tag}{label} | {date_str}"
        if quality_warning
        else f"ADMIN ✅ X Preview {score_tag}{label} | {date_str}"
    )

    tweet_cards = ""
    for i, tweet_text in enumerate(tweets, 1):
        chars       = len(tweet_text)
        char_color  = "#dc2626" if chars > 270 else "#16a34a" if chars <= 240 else "#f59e0b"
        thread_badge = (
            f"<span style='background:#e0f2fe;color:#0369a1;padding:2px 8px;"
            f"border-radius:10px;font-size:11px;font-weight:bold;margin-left:8px;'>"
            f"Tweet {i}/{total}</span>"
        ) if is_thread else ""

        tweet_cards += f"""
        <div style="background:#f9fafb;border:1px solid #e5e7eb;border-left:4px solid {color};
                    border-radius:8px;padding:16px;margin-bottom:12px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                <span style="font-size:13px;font-weight:bold;color:{color};">
                    𝕏 Post{thread_badge}
                </span>
                <span style="font-size:11px;color:{char_color};font-weight:bold;">
                    {chars}/270 znaków
                </span>
            </div>
            <div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7;
                        color:#111827;word-break:break-word;margin:0;">{_format_tweet_html(tweet_text).replace(chr(10), '<br>')}</div>
        </div>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;padding:8px;">
    <div style="background:{color};color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">🐦 X Preview — {label}</h2>
        <p style="margin:4px 0 0 0;opacity:0.85;font-size:13px;">
            {date_str} &nbsp;|&nbsp; {okno_str}
            &nbsp;|&nbsp; {liczba} ogłoszeń
            {"&nbsp;|&nbsp; 🧵 Wątek " + str(total) + " tweetów" if is_thread else ""}
        </p>
    </div>

    <div style="border:1px solid #e5e7eb;border-top:none;padding:20px;border-radius:0 0 8px 8px;">

        {"" if not quality_warning else f'''
        <div style="background:#fef2f2;border:2px solid #dc2626;border-radius:6px;
                    padding:12px 16px;margin-bottom:12px;font-size:13px;color:#dc2626;font-weight:bold;">
            ⚠️ NISKA JAKOŚĆ PO 2 PRÓBACH — score {quality_score}/10
            <span style="font-weight:normal;color:#374151;display:block;margin-top:4px;font-size:12px;">
                Post wysłany mimo niskiej jakości (supervisor odrzucił obie wersje).
                Rozważ ręczną korektę przed publikacją.
            </span>
        </div>
        '''}
        <div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:6px;
                    padding:10px 14px;margin-bottom:16px;font-size:12px;color:#92400e;">
            ⚠️ <strong>TRYB PREVIEW</strong> — posty NIE zostały opublikowane na X.
            Zapisano w BigQuery (tabela xposts). Po weryfikacji włącz publikację przez X API.
        </div>

        {tweet_cards}

        {_gemini_review_html(gemini_review)}

        <div style="margin-top:16px;padding:12px;background:#f0fdf4;
                    border:1px solid #bbf7d0;border-radius:6px;font-size:12px;color:#166534;">
            ✅ Zapisano: <code>BQ/xposts/{data.strftime('%Y-%m-%d')}_{window}</code>
        </div>
    </div>
    </body></html>
    """

    return _send_email(to, subject, html)


def _call_gemini_sanitize(items: list[dict], sugestie: str, problemy: list[str]) -> list[dict] | None:
    """Wywołuje Gemini z PUBLIC_SANITIZE_TEMPLATE. Zwraca oczyszczone items lub None."""
    import json as json_mod

    from agents.prompts import PUBLIC_SANITIZE_TEMPLATE
    from agents.vertex_client import call_gemini_json

    items_json = json_mod.dumps(
        [{"spolka": i.get("spolka", "?"), "tytul": i.get("tytul", "")} for i in items],
        ensure_ascii=False,
    )
    prompt = PUBLIC_SANITIZE_TEMPLATE.format(
        sugestie=sugestie or "brak",
        problemy="\n".join(f"- {p}" for p in problemy) if problemy else "brak",
        items_json=items_json,
    )

    result = call_gemini_json(
        prompt,
        max_retries=1,
        metadata={
            "agent":        "public_sanitizer",
            "items_count":  len(items),
            "problems_count": len(problemy),
        },
        # Phase 4 re-enabled 2026-04-23: thinking_budget=512 (2x bufor vs 256).
        # Sanitizer to deterministyczne text replacement — thinking raczej niepotrzebny,
        # ale bufor na edge case (np. ambiguous problem).
        # model_override=Flash-Lite: 3-6x taniej.
        # max_output_tokens=16384: safety net dla N=20 items × 500 tok = ~10K.
        thinking_budget=512,
        model_override="gemini-2.5-flash-lite",
        max_output_tokens=16384,
    )
    if isinstance(result, list) and all(isinstance(r, dict) for r in result):
        return result
    logger.warning(f"Gemini sanitize zwrócił nieoczekiwany format: {type(result)}")
    return None


def sanitize_public_content(
    items: list[dict],
    suggestions: str,
    problemy: list[str],
) -> list[dict]:
    """
    Gemini przepisuje tytuły ogłoszeń na neutralne wersje.
    Fallback: zwraca oryginał jeśli Gemini zawiedzie.
    """
    result = _call_gemini_sanitize(items, suggestions, problemy)
    if result:
        logger.info(f"Sanityzacja PUBLIC: {len(result)} elementów przepisanych przez Gemini")
        return result
    logger.warning("Sanityzacja PUBLIC: Gemini fallback → zwracam oryginał")
    return items


def send_personalized_xpost_digest(
    to: str,
    data: date,
    window: str,
    tickers: list[str],
    time_from: str | None = None,
    time_to: str | None = None,
) -> bool:
    """
    Personalizowany email per subskrybent: podsumowania TYLKO spółek z jego listy.
    Ładuje analizy z BQ dla tickers + time window. Brak analiz = brak emaila (return False).
    """
    from storage.bq_client import get_bq_client
    bq = get_bq_client()

    # Ładuj analizy dla spółek subskrybenta z tego okna
    analyses = []
    for ticker in tickers:
        rows = bq.load_analyses_for_period(
            date_from=data, date_to=data,
            company_filter=ticker,
            time_from=time_from, time_to=time_to,
        )
        analyses.extend(rows)

    if not analyses:
        logger.info(f"Personalized digest: brak analiz dla {tickers} → skip email do {to}")
        return False

    # Grupuj analizy per spółka
    by_company: dict[str, list[dict]] = {}
    for a in analyses:
        company = a.get("company", "?")
        by_company.setdefault(company, []).append(a)

    date_str = data.strftime('%d.%m.%Y')
    window_labels = {
        "premarket": "Premarket", "morning": "Sesja",
        "afternoon": "Popołudnie", "afterhours": "Po sesji",
        "daily_thread": "Podsumowanie dnia",
    }
    window_label = window_labels.get(window, window)

    # Buduj HTML karty per spółka
    cards_html = ""
    for company, company_analyses in by_company.items():
        items_html = ""
        for a in company_analyses:
            temat = a.get("temat", "")
            podsumowanie = a.get("podsumowanie", "")
            fakty = a.get("kluczowe_fakty", [])
            if isinstance(fakty, str):
                fakty = [fakty]
            fakty_html = "".join(f"<li>{f}</li>" for f in fakty[:5]) if fakty else ""

            items_html += f"""
            <div style="padding:8px 0;border-bottom:1px solid #f3f4f6;">
                <div style="font-weight:bold;color:#111827;">{temat}</div>
                <div style="font-size:13px;color:#374151;margin-top:4px;">{podsumowanie}</div>
                {"<ul style='margin:4px 0 0 0;padding-left:16px;font-size:12px;color:#6b7280;'>" + fakty_html + "</ul>" if fakty_html else ""}
            </div>"""

        cards_html += f"""
        <div style="background:white;border:1px solid #e5e7eb;border-left:4px solid #0369a1;
                    border-radius:8px;padding:16px;margin-bottom:12px;">
            <h3 style="margin:0 0 8px 0;color:#0369a1;font-size:16px;">{company}</h3>
            <div style="font-size:12px;color:#9ca3af;margin-bottom:8px;">
                {len(company_analyses)} ogłoszeń w oknie {window_label}
            </div>
            {items_html}
        </div>"""

    subject = f"📊 Twoje spółki — {window_label} | {date_str}"
    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;padding:8px;">
    <div style="background:#0369a1;color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">📊 Twoje spółki — {window_label}</h2>
        <p style="margin:4px 0 0 0;opacity:0.85;font-size:13px;">
            {date_str} &nbsp;|&nbsp; {len(analyses)} ogłoszeń &nbsp;|&nbsp;
            {len(by_company)} spółek z Twojej listy
        </p>
    </div>
    <div style="border:1px solid #e5e7eb;border-top:none;padding:20px;border-radius:0 0 8px 8px;
                background:#f9fafb;">
        {cards_html}
    </div>
    </body></html>
    """

    logger.info(
        f"Personalized digest: {len(analyses)} analiz, "
        f"{len(by_company)} spółek → {to}"
    )
    return _send_email(to, subject, html)


def send_xpost_poor_quality_alert(
    to: str,
    data: date,
    window: str,
    post_data: dict,
    validation_result,
) -> bool:
    """
    Wysyła alert email gdy post X nie przeszedł walidacji (score ≤ 6).
    Zawiera: baner niska jakość, ocenę Gemini, problemy, sugestie, treść posta.
    """
    window_labels = {
        "premarket":    ("🌙 Premarket",    "#6366f1"),
        "morning":      ("🌅 Sesja",        "#0ea5e9"),
        "afternoon":    ("🌤️ Popołudnie",   "#f59e0b"),
        "afterhours":   ("🌇 Po sesji",     "#ef4444"),
        "daily_thread": ("🌆 Podsumowanie", "#7c3aed"),
    }
    label, color = window_labels.get(window, ("📊", "#374151"))
    score   = validation_result.score
    attempt = getattr(validation_result, "attempt", 1)
    tweets  = post_data.get("tweets", [])
    is_thread = post_data.get("is_thread", False)

    subject = (
        f"ADMIN ⚠️ X Niska jakość ({score}/10) | {label} | {data.strftime('%d.%m.%Y')}"
        + (f" [próba {attempt}]" if attempt > 1 else "")
    )

    # Problemy
    problemy_html = "".join(
        f"<li style='margin:4px 0;'>{p}</li>"
        for p in (validation_result.problemy or ["–"])
    )

    # Długości tweetów
    char_rows = ""
    for i, tweet in enumerate(tweets, 1):
        n = len(tweet)
        badge_color = "#dc2626" if n < 300 or n > 2000 else "#d97706" if n < 500 else "#16a34a"
        label_t = f"Tweet {i}/{len(tweets)}" if is_thread else "Post"
        char_rows += (
            f"<tr><td style='padding:4px 8px;'>{label_t}</td>"
            f"<td style='padding:4px 8px;text-align:right;font-family:monospace;"
            f"color:{badge_color};font-weight:bold;'>{n} znaków</td></tr>"
        )

    # Treść posta
    tweet_cards = ""
    for i, tweet_text in enumerate(tweets, 1):
        thread_label = f"Tweet {i}/{len(tweets)} — " if is_thread else ""
        tweet_cards += f"""
        <div style="background:#f9fafb;border:1px solid #e5e7eb;border-left:3px solid #dc2626;
                    border-radius:6px;padding:14px;margin-bottom:10px;">
            <div style="font-size:11px;color:#6b7280;margin-bottom:8px;">
                {thread_label}{len(tweet_text)} znaków
            </div>
            <pre style="font-family:Arial,sans-serif;font-size:13px;line-height:1.6;
                        color:#374151;white-space:pre-wrap;word-break:break-word;margin:0;">{tweet_text}</pre>
        </div>"""

    regeneration_note = (
        "<div style='background:#fef3c7;border:1px solid #fcd34d;padding:10px 14px;"
        "border-radius:6px;font-size:12px;color:#92400e;margin-top:12px;'>"
        "🔄 <strong>Trwa automatyczna regeneracja</strong> z uwzględnieniem powyższych sugestii. "
        "Wynik pojawi się w kolejnym emailu preview."
        "</div>"
    )

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:8px;">

    <div style="background:#dc2626;color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">⚠️ X Post — NISKA JAKOŚĆ</h2>
        <p style="margin:4px 0 0 0;opacity:0.85;font-size:13px;">
            {label} | {data.strftime('%d.%m.%Y')}
            {"&nbsp;|&nbsp; Próba " + str(attempt) + "/2" if attempt > 1 else ""}
        </p>
    </div>

    <div style="border:1px solid #e5e7eb;border-top:none;padding:20px;border-radius:0 0 8px 8px;">

        <!-- Ocena -->
        <div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;
                    padding:16px;margin-bottom:16px;">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
                <span style="font-size:36px;font-weight:bold;color:#dc2626;">{score}/10</span>
                <div>
                    <div style="font-weight:bold;color:#1f2937;">Ocena Gemini Supervisor</div>
                    <div style="font-size:13px;color:#6b7280;">{validation_result.uzasadnienie}</div>
                </div>
            </div>
        </div>

        <!-- Problemy -->
        <h3 style="margin:16px 0 8px 0;color:#dc2626;font-size:14px;">❌ Problemy</h3>
        <ul style="margin:0;padding-left:20px;font-size:13px;color:#374151;">
            {problemy_html}
        </ul>

        <!-- Sugestie -->
        <h3 style="margin:16px 0 8px 0;color:#d97706;font-size:14px;">💡 Sugestie poprawek</h3>
        <div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:6px;
                    padding:12px;font-size:13px;color:#374151;">
            {validation_result.sugestie or "–"}
        </div>

        <!-- Długości -->
        <h3 style="margin:16px 0 8px 0;color:#374151;font-size:14px;">📏 Długości</h3>
        <table style="font-size:12px;border-collapse:collapse;">
            {char_rows}
        </table>

        <!-- Treść -->
        <h3 style="margin:16px 0 8px 0;color:#374151;font-size:14px;">📝 Treść posta</h3>
        {tweet_cards}

        {regeneration_note}

    </div>
    </body></html>
    """

    return _send_email(to, subject, html)


def render_macro_public(
    date_str: str,
    macro_html: str,
    top_pozytywne: list[dict],
    top_negatywne: list[dict],
) -> tuple[str, str]:
    """Renderuje PUBLIC makro email — zwraca (subject, html). Bez wysyłki."""
    all_top = list(top_pozytywne) + list(top_negatywne)
    cards_html = ""
    for t in all_top:
        cards_html += f"""
        <div style="background:white;border:1px solid #e5e7eb;border-left:4px solid #6b7280;
                    border-radius:8px;padding:12px 16px;margin-bottom:8px;">
            <span style="font-size:14px;font-weight:bold;color:#111827;">{t.get('spolka','?')}</span>
            <div style="font-size:13px;color:#374151;margin-top:4px;">{t.get('tytul','?')}</div>
        </div>"""
    top_html = f"""
    <div style="margin-top:16px;">
        <h3 style="margin:0 0 12px 0;color:#374151;font-size:14px;text-transform:uppercase;letter-spacing:0.05em;">📋 Ogłoszenia dnia</h3>
        {cards_html}
    </div>""" if all_top else ""

    subject = f"PUBLIC 📈 Przegląd makro | {date_str}"
    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 650px; margin: 0 auto;">
    <div style="background: #1e40af; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">📈 Przegląd makro</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8;">{date_str}</p>
    </div>
    <div style="background: #eff6ff; border: 1px solid #bfdbfe; padding: 20px; border-radius: 0 0 8px 8px;">
        {macro_html}
        {top_html}
    </div>
    </body></html>
    """
    return subject, html


def send_macro_bez_ogloszen(
    to: str,
    data: date,
    komentarz_gemini: dict,
    top_pozytywne: list[dict],
    top_negatywne: list[dict],
    macro: dict | None,
    public: bool = False,
) -> bool:
    """
    Email wysyłany gdy spółki portfelowe nie miały ogłoszeń danego dnia.

    public=False → PRIVATE: komentarz Gemini + sentyment + top pozytywne/negatywne
    public=True  → PUBLIC:  tylko dane makro + neutralna lista najważniejszych ogłoszeń
    """
    date_str = data.strftime('%d.%m.%Y')

    # ── Sekcja makro (wspólna) ───────────────────────────────────────────────
    macro_html = ""
    if macro:
        indeksy = macro.get("indeksy", {})
        waluty  = macro.get("waluty", {})
        surowce = macro.get("surowce", {})

        def fmt_row(name, val_dict):
            cena = val_dict.get("cena") or val_dict.get("kurs", "—")
            zm   = val_dict.get("zmiana_proc")
            if zm is not None:
                color = "#16a34a" if zm > 0 else "#dc2626" if zm < 0 else "#6b7280"
                sign  = "+" if zm > 0 else ""
                return f"<tr><td style='padding:4px 8px;font-size:13px;'>{name}</td><td style='padding:4px 8px;font-size:13px;text-align:right;font-weight:bold;'>{cena}</td><td style='padding:4px 8px;font-size:13px;text-align:right;color:{color};'>{sign}{zm:.2f}%</td></tr>"
            return f"<tr><td style='padding:4px 8px;font-size:13px;'>{name}</td><td style='padding:4px 8px;font-size:13px;text-align:right;font-weight:bold;' colspan='2'>{cena}</td></tr>"

        indeksy_rows = "".join(fmt_row(k, v) for k, v in indeksy.items())
        waluty_show = {}
        for k, v in waluty.items():
            base = k.replace("_NBP", "")
            if "_NBP" in k or base not in waluty_show:
                waluty_show[base] = v
        waluty_rows  = "".join(fmt_row(k, v) for k, v in list(waluty_show.items())[:4])
        surowce_rows = "".join(fmt_row(k, v) for k, v in surowce.items())

        macro_html = f"""
        <div style="margin-top: 20px;">
            <h3 style="margin: 0 0 8px 0; color: #1e40af; font-size: 14px; text-transform: uppercase; letter-spacing: 0.05em;">📊 Dane makro</h3>
            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px;">
                <div style="background: white; border-radius: 6px; border: 1px solid #e5e7eb; overflow: hidden;">
                    <div style="background: #f3f4f6; padding: 6px 8px; font-size: 12px; font-weight: bold; color: #374151;">Indeksy</div>
                    <table style="width:100%; border-collapse:collapse;">{indeksy_rows}</table>
                </div>
                <div style="background: white; border-radius: 6px; border: 1px solid #e5e7eb; overflow: hidden;">
                    <div style="background: #f3f4f6; padding: 6px 8px; font-size: 12px; font-weight: bold; color: #374151;">Waluty</div>
                    <table style="width:100%; border-collapse:collapse;">{waluty_rows}</table>
                </div>
                <div style="background: white; border-radius: 6px; border: 1px solid #e5e7eb; overflow: hidden;">
                    <div style="background: #f3f4f6; padding: 6px 8px; font-size: 12px; font-weight: bold; color: #374151;">Surowce</div>
                    <table style="width:100%; border-collapse:collapse;">{surowce_rows}</table>
                </div>
            </div>
        </div>"""

    # ── Helper: tabela top ogłoszeń ──────────────────────────────────────────
    def _top_table_macro(items: list[dict], title: str, accent: str, bg: str, icon: str) -> str:
        if not items:
            return ""
        rows = ""
        for t in items:
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #e5e7eb;font-weight:bold;white-space:nowrap;vertical-align:top;">{t.get('spolka','?')}</td>
                <td style="padding:8px;border-bottom:1px solid #e5e7eb;font-size:13px;">
                    <div>{t.get('tytul','?')}</div>
                    <div style="color:#6b7280;font-size:12px;margin-top:3px;">{t.get('dlaczego_wazne','')}</div>
                </td>
            </tr>"""
        return f"""
        <div style="margin-top:16px;">
            <h3 style="margin:0 0 8px 0;color:{accent};font-size:14px;text-transform:uppercase;letter-spacing:0.05em;">{icon} {title}</h3>
            <table style="width:100%;border-collapse:collapse;background:white;border-radius:6px;border:1px solid {bg};overflow:hidden;">
                <thead><tr style="background:{bg};">
                    <th style="padding:10px;text-align:left;font-size:13px;color:{accent};">Spółka</th>
                    <th style="padding:10px;text-align:left;font-size:13px;color:{accent};">Ogłoszenie</th>
                </tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>"""

    # ── PUBLIC — dane makro + neutralna lista ogłoszeń (bez dlaczego_wazne) ─
    if public:
        subject, html = render_macro_public(date_str, macro_html, top_pozytywne, top_negatywne)
        return _send_email(to, subject, html)

    # ── PRIVATE — pełna wersja z Gemini ──────────────────────────────────────
    nastroj = komentarz_gemini.get("nastroj", "neutralny")
    nastroj_icon = {"pozytywny": "🟢", "negatywny": "🔴", "neutralny": "⚪"}.get(nastroj, "⚪")
    czynniki_html = "".join(
        f"<li style='margin-bottom:4px;'>{c}</li>"
        for c in komentarz_gemini.get("kluczowe_czynniki", [])
    )
    uwaga = komentarz_gemini.get("na_co_uwazac", "")

    komentarz_html = f"""
    <div style="margin-top: 20px; background: white; border-radius: 6px; border: 1px solid #dbeafe; padding: 16px;">
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px;">
            <span style="font-size: 18px;">{nastroj_icon}</span>
            <strong style="color: #1e40af;">Komentarz Gemini dla portfela</strong>
        </div>
        <p style="color: #374151; margin: 0 0 12px 0; line-height: 1.6;">{komentarz_gemini.get('komentarz', '')}</p>
        {"<ul style='margin:0 0 10px 0; padding-left:20px; color:#374151; font-size:13px;'>" + czynniki_html + "</ul>" if czynniki_html else ""}
        {"<p style='margin:0; font-size:13px; color:#dc2626;'><strong>⚠️ Na co uważać:</strong> " + uwaga + "</p>" if uwaga else ""}
    </div>"""

    top_html = (
        _top_table_macro(top_pozytywne, "Top 5 pozytywnych", "#16a34a", "#dcfce7", "▲") +
        _top_table_macro(top_negatywne, "Top 5 negatywnych", "#dc2626", "#fee2e2", "▼")
    )

    subject = f"ADMIN 📈 Komentarz makro — brak ogłoszeń portfela | {date_str}"

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 650px; margin: 0 auto;">
    <div style="background: #1e40af; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">📈 Brak ogłoszeń portfela</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8;">{date_str} — Komentarz makro</p>
    </div>
    <div style="background: #eff6ff; border: 1px solid #bfdbfe; padding: 20px; border-radius: 0 0 8px 8px;">
        <p style="color: #374151; margin: 0 0 4px 0;">
            Żadna ze spółek portfelowych nie opublikowała dziś ogłoszeń ESPI/EBI.
            Poniżej komentarz rynkowy oparty na danych makro.
        </p>
        {macro_html}
        {komentarz_html}
        {top_html}
    </div>
    </body></html>
    """

    return _send_email(to, subject, html)


def send_watchlist_tygodniowy(
    to: str,
    data_od: date,
    data_do: date,
    watchlist: dict,
) -> bool:
    """
    Tygodniowa watchlista — top 5 kandydatów inwestycyjnych z całego GPW.
    Wysyłana w niedzielę po watchlist.py.
    """
    subject = f"ADMIN 🔭 Watchlista | {data_od.strftime('%d.%m')}–{data_do.strftime('%d.%m.%Y')}"

    CONVICTION_STYLE = {
        "WYSOKA":  ("🟢", "#16a34a", "#f0fdf4", "#bbf7d0"),
        "SREDNIA": ("🟡", "#d97706", "#fffbeb", "#fde68a"),
        "NISKA":   ("🔵", "#2563eb", "#eff6ff", "#bfdbfe"),
    }
    HORYZONT_LABEL = {
        "krotkoterminowy":  "Krótki",
        "sredniookresowy":  "Średni",
        "dlugoterminowy":   "Długi",
    }

    picks_html = ""
    for i, pick in enumerate(watchlist.get("top_picks", []), 1):
        conviction = pick.get("conviction", "SREDNIA").upper()
        icon, text_color, bg_color, border_color = CONVICTION_STYLE.get(
            conviction, ("•", "#6b7280", "#f9fafb", "#e5e7eb")
        )
        horyzont = HORYZONT_LABEL.get(pick.get("horyzont", ""), pick.get("horyzont", "—"))
        sentiment = pick.get("sentiment_tygodnia", "neutralny")
        sent_icon = {"pozytywny": "▲", "negatywny": "▼", "mieszany": "↔"}.get(sentiment, "●")

        wydarzenia = "".join(
            f"<li style='margin-bottom:3px;font-size:13px;'>{w}</li>"
            for w in pick.get("kluczowe_wydarzenia", [])
        )

        picks_html += f"""
        <div style="background:{bg_color};border:1px solid {border_color};border-radius:8px;padding:16px;margin-bottom:12px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                <div>
                    <span style="font-size:20px;font-weight:bold;color:{text_color};">{i}. {pick.get('ticker','?')}</span>
                    <span style="font-size:14px;color:#6b7280;margin-left:8px;">{pick.get('spolka','')}</span>
                </div>
                <div style="text-align:right;">
                    <span style="background:white;border:1px solid {border_color};padding:3px 8px;border-radius:4px;font-size:12px;font-weight:bold;color:{text_color};">{icon} {conviction}</span>
                </div>
            </div>
            <div style="font-size:12px;color:#6b7280;margin-bottom:10px;">
                Sentyment tygodnia: <strong>{sent_icon} {sentiment}</strong> &nbsp;|&nbsp;
                Ogłoszeń: <strong>{pick.get('liczba_ogloszen_tygodniu',0)}</strong> &nbsp;|&nbsp;
                Horyzont: <strong>{horyzont}</strong>
            </div>
            <p style="margin:0 0 8px 0;color:#374151;line-height:1.5;"><strong>💡 Dlaczego warto:</strong> {pick.get('dlaczego_warto','')}</p>
            {"<ul style='margin:4px 0 8px 16px;padding:0;'>" + wydarzenia + "</ul>" if wydarzenia else ""}
            <p style="margin:0;font-size:12px;color:#6b7280;"><strong>⚠️ Ryzyka:</strong> {pick.get('ryzyka','—')}</p>
        </div>"""

    do_obserwacji_html = ""
    if watchlist.get("do_obserwacji"):
        tickers = " &nbsp;•&nbsp; ".join(
            f"<strong>{t}</strong>" for t in watchlist["do_obserwacji"]
        )
        do_obserwacji_html = f"""
        <div style="background:white;border:1px solid #e5e7eb;border-radius:6px;padding:12px;margin-top:16px;">
            <span style="font-size:13px;color:#6b7280;">👀 Również warte obserwacji: {tickers}</span>
        </div>"""

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 650px; margin: 0 auto;">
    <div style="background: #7c3aed; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">🔭 Watchlista tygodniowa</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8;">{data_od.strftime('%d.%m')}–{data_do.strftime('%d.%m.%Y')} &nbsp;|&nbsp; {watchlist.get('liczba_ogloszen',0)} ogłoszeń &nbsp;|&nbsp; {watchlist.get('liczba_spolek',0)} spółek</p>
    </div>
    <div style="background: #faf5ff; border: 1px solid #ddd6fe; padding: 20px; border-radius: 0 0 8px 8px;">

        <div style="background:white;border:1px solid #ddd6fe;border-radius:6px;padding:12px;margin-bottom:20px;">
            <p style="margin:0;color:#374151;font-size:14px;line-height:1.6;">{watchlist.get('podsumowanie_tygodnia','')}</p>
            {"<p style='margin:8px 0 0 0;font-size:13px;color:#6b7280;'>📊 Kontekst makro: " + watchlist.get('makro_kontekst','') + "</p>" if watchlist.get('makro_kontekst') else ""}
        </div>

        <h3 style="margin: 0 0 12px 0; color: #7c3aed; font-size: 14px; text-transform: uppercase; letter-spacing: 0.05em;">🏆 Top 5 kandydatów tygodnia</h3>
        {picks_html}
        {do_obserwacji_html}
    </div>
    </body></html>
    """

    return _send_email(to, subject, html)


def send_broker_raport(
    to: str,
    data_od: date,
    data_do: date,
    report: dict,
) -> bool:
    """
    Tygodniowy raport agenta broker — ocena portfela + rekomendacje zakupów.
    Wysyłany w niedzielę po watchlist-job.
    """
    subject = f"ADMIN 🐢 Żółw (Standard) | {data_od.strftime('%d.%m')}–{data_do.strftime('%d.%m.%Y')}"

    CONV_STYLE = {
        "WYSOKA":  ("🟢", "#16a34a", "#f0fdf4", "#bbf7d0"),
        "SREDNIA": ("🟡", "#d97706", "#fffbeb", "#fde68a"),
        "NISKA":   ("🔵", "#2563eb", "#eff6ff", "#bfdbfe"),
    }
    REK_STYLE = {
        "TRZYMAJ":  ("✅", "#16a34a", "#f0fdf4"),
        "OBSERWUJ": ("⚠️",  "#d97706", "#fffbeb"),
        "SPRZEDAJ": ("🔴", "#dc2626", "#fef2f2"),
    }
    SENT_COLOR = {
        "pozytywny": "#16a34a",
        "neutralny": "#2563eb",
        "negatywny": "#dc2626",
        "mieszany":  "#d97706",
    }
    header_color = SENT_COLOR.get(report.get("sentyment_rynku", "neutralny"), "#2563eb")

    # ── Sekcja ocen posiadanych pozycji ──────────────────────────────────────
    oceny_html = ""
    for o in report.get("ocena_portfela", []):
        rek   = o.get("rekomendacja", "TRZYMAJ")
        icon, color, bg = REK_STYLE.get(rek, ("•", "#6b7280", "#f9fafb"))
        alerty = o.get("alerty", [])
        alerty_html = (
            "".join(f"<li style='color:#dc2626;font-size:12px;'>{a}</li>" for a in alerty)
            if alerty else ""
        )
        oceny_html += f"""
        <div style="border:1px solid {color}33;border-radius:6px;padding:12px;
                    margin-bottom:8px;background:{bg};">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <strong style="color:#1f2937;">{icon} {o.get('ticker','?')} — {o.get('spolka','?')}</strong>
                <span style="background:{color};color:white;padding:2px 10px;
                             border-radius:12px;font-size:12px;font-weight:bold;">{rek}</span>
            </div>
            <p style="margin:6px 0 0 0;font-size:13px;color:#374151;">{o.get('uzasadnienie','')}</p>
            {"<ul style='margin:4px 0 0 0;padding-left:16px;'>" + alerty_html + "</ul>" if alerty_html else ""}
        </div>"""

    if not oceny_html:
        oceny_html = "<p style='color:#6b7280;font-size:13px;'>Brak posiadanych pozycji do oceny.</p>"

    # ── Sekcja rekomendowanych zakupów ────────────────────────────────────────
    zakupy_html = ""
    for z in report.get("rekomendacje_zakupu", []):
        conv  = z.get("conviction", "NISKA")
        icon, color, bg, border = CONV_STYLE.get(conv, ("•", "#6b7280", "#f9fafb", "#e5e7eb"))
        horyzont = {"krotkoterminowy": "Krótki", "sredniookresowy": "Średni",
                    "dlugoterminowy": "Długi"}.get(z.get("horyzont", ""), z.get("horyzont", "?"))
        zakupy_html += f"""
        <div style="border:1px solid {border};border-radius:8px;padding:14px;
                    margin-bottom:10px;background:{bg};">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                <div>
                    <strong style="font-size:16px;color:#1f2937;">{icon} {z.get('ticker','?')}</strong>
                    <span style="color:#6b7280;margin-left:8px;font-size:13px;">{z.get('spolka','?')}</span>
                </div>
                <div style="text-align:right;">
                    <div style="background:{color};color:white;padding:3px 12px;
                                border-radius:12px;font-size:13px;font-weight:bold;display:inline-block;">
                        {z.get('kwota_pln',0):.0f} PLN
                    </div>
                    <div style="font-size:11px;color:#6b7280;margin-top:3px;">{conv} przekonanie &nbsp;|&nbsp; {horyzont}</div>
                </div>
            </div>
            <p style="margin:0 0 6px 0;font-size:13px;color:#374151;">{z.get('uzasadnienie','')}</p>
            <p style="margin:0;font-size:12px;color:#6b7280;">⚠️ {z.get('ryzyka','')}</p>
        </div>"""

    if not zakupy_html:
        zakupy_html = "<p style='color:#6b7280;font-size:13px;'>Brak rekomendacji zakupowych w tym tygodniu.</p>"

    # ── Sekcja rekomendacji krótkoterminowych (z głównego raportu) ────────────
    short_html = ""
    for s in report.get("rekomendacje_krotkoterminowe", []):
        conv  = s.get("conviction", "NISKA")
        icon, color, bg, border = CONV_STYLE.get(conv, ("•", "#6b7280", "#f9fafb", "#e5e7eb"))
        short_html += f"""
        <div style="border:1px solid {border};border-radius:8px;padding:14px;
                    margin-bottom:10px;background:{bg};border-left:4px solid #d97706;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                <div>
                    <strong style="font-size:16px;color:#1f2937;">⚡ {s.get('ticker','?')}</strong>
                    <span style="color:#6b7280;margin-left:8px;font-size:13px;">{s.get('spolka','?')}</span>
                </div>
                <div style="text-align:right;">
                    <div style="background:#d97706;color:white;padding:3px 12px;
                                border-radius:12px;font-size:13px;font-weight:bold;display:inline-block;">
                        {s.get('kwota_pln',0):.0f} PLN
                    </div>
                    <div style="font-size:11px;color:#6b7280;margin-top:3px;">{conv} &nbsp;|&nbsp; {s.get('horyzont_dni',0)} dni</div>
                </div>
            </div>
            <p style="margin:0 0 6px 0;font-size:13px;color:#374151;">{s.get('uzasadnienie','')}</p>
            <p style="margin:0 0 4px 0;font-size:12px;color:#d97706;">🎯 Katalizator: {s.get('katalizator','')}</p>
            <p style="margin:0;font-size:12px;color:#6b7280;">⚠️ {s.get('ryzyka','')}</p>
        </div>"""

    short_section_html = ""
    if short_html:
        short_section_html = f"""
        <h3 style="margin:20px 0 12px 0;color:#1f2937;font-size:14px;
                   text-transform:uppercase;letter-spacing:0.05em;">
            ⚡ Rekomendacje krótkoterminowe
        </h3>
        {short_html}"""

    # ── Do obserwacji ────────────────────────────────────────────────────────
    do_obs = report.get("do_obserwacji", [])
    do_obs_html = ""
    if do_obs:
        badges = " ".join(
            f"<span style='background:#e5e7eb;color:#374151;padding:4px 10px;"
            f"border-radius:12px;font-size:12px;margin:2px;display:inline-block;'>{t}</span>"
            for t in do_obs
        )
        do_obs_html = f"""
        <div style="margin-top:16px;">
            <p style="font-size:13px;color:#6b7280;margin-bottom:6px;">👁 Do obserwacji:</p>
            {badges}
        </div>"""

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
    <div style="background:{header_color};color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">🤖 Raport Brokera</h2>
        <p style="margin:4px 0 0 0;opacity:0.9;">
            {data_od.strftime('%d.%m')}–{data_do.strftime('%d.%m.%Y')} &nbsp;|&nbsp;
            gotówka po zakupach: <strong>{report.get('gotowka_po_zakupach_pln',0):.0f} PLN</strong>
        </p>
    </div>
    <div style="background:white;border:1px solid #e5e7eb;padding:20px;">

        <div style="background:#f9fafb;border-radius:6px;padding:12px;margin-bottom:20px;">
            <p style="margin:0;color:#374151;font-size:14px;line-height:1.6;">
                {report.get('komentarz_tygodnia','')}
            </p>
        </div>

        <h3 style="margin:0 0 12px 0;color:#1f2937;font-size:14px;
                   text-transform:uppercase;letter-spacing:0.05em;">
            📊 Ocena posiadanych pozycji
        </h3>
        {oceny_html}

        <h3 style="margin:20px 0 12px 0;color:#1f2937;font-size:14px;
                   text-transform:uppercase;letter-spacing:0.05em;">
            💰 Rekomendowane zakupy
        </h3>
        {zakupy_html}
        {short_section_html}
        {do_obs_html}

    </div>
    <div style="background:#f3f4f6;padding:10px 20px;border-radius:0 0 8px 8px;
                font-size:11px;color:#9ca3af;text-align:center;">
        Raport wygenerowany przez Gemini AI na podstawie ogłoszeń ESPI/EBI.
        Nie stanowi porady inwestycyjnej.
    </div>
    </body></html>
    """

    return _send_email(to, subject, html)


def send_broker_short_raport(
    to: str,
    data_od: date,
    data_do: date,
    report: dict,
) -> bool:
    """
    Raport Broker Short — osobna analiza krótkoterminowa (1-4 tygodnie).
    Osobny portfel, osobna strategia.
    """
    subject = f"ADMIN 🐇 Zając (Short) | {data_od.strftime('%d.%m')}–{data_do.strftime('%d.%m.%Y')}"

    CONV_STYLE = {
        "WYSOKA":  ("🟢", "#16a34a", "#f0fdf4", "#bbf7d0"),
        "SREDNIA": ("🟡", "#d97706", "#fffbeb", "#fde68a"),
    }
    REK_STYLE = {
        "TRZYMAJ":  ("✅", "#16a34a", "#f0fdf4"),
        "OBSERWUJ": ("⚠️",  "#d97706", "#fffbeb"),
        "ZAMKNIJ":  ("🔴", "#dc2626", "#fef2f2"),
        "SPRZEDAJ": ("🔴", "#dc2626", "#fef2f2"),
    }
    SENT_COLOR = {
        "pozytywny": "#16a34a",
        "neutralny": "#2563eb",
        "negatywny": "#dc2626",
        "mieszany":  "#d97706",
    }
    header_color = SENT_COLOR.get(report.get("sentyment_rynku", "neutralny"), "#d97706")

    # ── Oceny pozycji ────────────────────────────────────────────────────────
    oceny_html = ""
    for o in report.get("ocena_portfela", []):
        rek   = o.get("rekomendacja", "TRZYMAJ")
        icon, color, bg = REK_STYLE.get(rek, ("•", "#6b7280", "#f9fafb"))
        oceny_html += f"""
        <div style="border:1px solid {color}33;border-radius:6px;padding:12px;
                    margin-bottom:8px;background:{bg};">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <strong style="color:#1f2937;">{icon} {o.get('ticker','?')} — {o.get('spolka','?')}</strong>
                <span style="background:{color};color:white;padding:2px 10px;
                             border-radius:12px;font-size:12px;font-weight:bold;">{rek}</span>
            </div>
            <p style="margin:6px 0 0 0;font-size:13px;color:#374151;">{o.get('uzasadnienie','')}</p>
        </div>"""
    if not oceny_html:
        oceny_html = "<p style='color:#6b7280;font-size:13px;'>Brak otwartych pozycji krótkoterminowych.</p>"

    # ── Rekomendacje zakupu ──────────────────────────────────────────────────
    zakupy_html = ""
    for z in report.get("rekomendacje_zakupu", []):
        conv  = z.get("conviction", "SREDNIA")
        icon, color, bg, border = CONV_STYLE.get(conv, ("•", "#6b7280", "#f9fafb", "#e5e7eb"))
        zakupy_html += f"""
        <div style="border:1px solid {border};border-radius:8px;padding:14px;
                    margin-bottom:10px;background:{bg};border-left:4px solid #d97706;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                <div>
                    <strong style="font-size:16px;color:#1f2937;">⚡ {z.get('ticker','?')}</strong>
                    <span style="color:#6b7280;margin-left:8px;font-size:13px;">{z.get('spolka','?')}</span>
                </div>
                <div style="text-align:right;">
                    <div style="background:#d97706;color:white;padding:3px 12px;
                                border-radius:12px;font-size:13px;font-weight:bold;display:inline-block;">
                        {z.get('kwota_pln',0):.0f} PLN
                    </div>
                    <div style="font-size:11px;color:#6b7280;margin-top:3px;">{conv} &nbsp;|&nbsp; {z.get('horyzont_dni', z.get('katalizator',''))}</div>
                </div>
            </div>
            <p style="margin:0 0 6px 0;font-size:13px;color:#374151;">{z.get('uzasadnienie','')}</p>
            <p style="margin:0 0 4px 0;font-size:12px;color:#d97706;">🎯 Katalizator: {z.get('katalizator','')}</p>
            <p style="margin:0;font-size:12px;color:#6b7280;">⚠️ {z.get('ryzyka','')}</p>
        </div>"""
    if not zakupy_html:
        zakupy_html = "<p style='color:#6b7280;font-size:13px;'>Brak rekomendacji krótkoterminowych.</p>"

    # ── Do obserwacji ────────────────────────────────────────────────────────
    do_obs = report.get("do_obserwacji", [])
    do_obs_html = ""
    if do_obs:
        badges = " ".join(
            f"<span style='background:#e5e7eb;color:#374151;padding:4px 10px;"
            f"border-radius:12px;font-size:12px;margin:2px;display:inline-block;'>{t}</span>"
            for t in do_obs
        )
        do_obs_html = f"""
        <div style="margin-top:16px;">
            <p style="font-size:13px;color:#6b7280;margin-bottom:6px;">👁 Do obserwacji:</p>
            {badges}
        </div>"""

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
    <div style="background:{header_color};color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">⚡ Broker Short (1-4 tyg.)</h2>
        <p style="margin:4px 0 0 0;opacity:0.9;">
            {data_od.strftime('%d.%m')}–{data_do.strftime('%d.%m.%Y')} &nbsp;|&nbsp;
            gotówka po zakupach: <strong>{report.get('gotowka_po_zakupach_pln',0):.0f} PLN</strong>
        </p>
    </div>
    <div style="background:white;border:1px solid #e5e7eb;padding:20px;">

        <div style="background:#fffbeb;border-radius:6px;padding:12px;margin-bottom:20px;border-left:4px solid #d97706;">
            <p style="margin:0;color:#374151;font-size:14px;line-height:1.6;">
                {report.get('komentarz_tygodnia','')}
            </p>
        </div>

        <h3 style="margin:0 0 12px 0;color:#1f2937;font-size:14px;
                   text-transform:uppercase;letter-spacing:0.05em;">
            📊 Pozycje krótkoterminowe
        </h3>
        {oceny_html}

        <h3 style="margin:20px 0 12px 0;color:#1f2937;font-size:14px;
                   text-transform:uppercase;letter-spacing:0.05em;">
            ⚡ Rekomendacje krótkoterminowe
        </h3>
        {zakupy_html}
        {do_obs_html}

    </div>
    <div style="background:#f3f4f6;padding:10px 20px;border-radius:0 0 8px 8px;
                font-size:11px;color:#9ca3af;text-align:center;">
        Raport Broker Short — Gemini AI na podstawie ogłoszeń ESPI/EBI. Horyzont 1-4 tygodnie.
        Nie stanowi porady inwestycyjnej.
    </div>
    </body></html>
    """

    return _send_email(to, subject, html)


def send_public_email_quality_alert(
    to: str,
    email_type: str,
    validation,
) -> bool:
    """
    Alert o niskiej jakości PUBLIC emaila — wysyłany gdy walidator wykryje
    sentyment, rekomendacje lub inne zabronione treści.

    validation: PublicEmailValidation z agents/public_email_validator.py
    """
    from datetime import date as date_cls

    date_str = date_cls.today().strftime('%d.%m.%Y')

    forbidden_html = ""
    if validation.forbidden_found:
        items = "".join(
            f"<li style='margin-bottom:4px;'><code>{f}</code></li>"
            for f in validation.forbidden_found
        )
        forbidden_html = f"""
        <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:12px;margin-bottom:16px;">
            <strong style="color:#991b1b;">🚫 Zabronione frazy znalezione w treści:</strong>
            <ul style="margin:8px 0 0 0;padding-left:20px;color:#991b1b;">{items}</ul>
        </div>"""

    problemy_html = ""
    if validation.problemy:
        items = "".join(f"<li style='margin-bottom:4px;'>{p}</li>" for p in validation.problemy)
        problemy_html = f"""
        <div style="margin-bottom:16px;">
            <strong>Problemy:</strong>
            <ul style="margin:8px 0 0 0;padding-left:20px;">{items}</ul>
        </div>"""

    sugestie_html = ""
    if validation.sugestie:
        sugestie_html = f"""
        <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:12px;margin-bottom:16px;">
            <strong>💡 Sugestie:</strong>
            <p style="margin:4px 0 0 0;color:#1e40af;">{validation.sugestie}</p>
        </div>"""

    subject = f"ADMIN ⚠️ PUBLIC Alert — {email_type} {validation.score}/10 | {date_str}"

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: #dc2626; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin: 0;">⚠️ PUBLIC Email — Niska jakość</h2>
        <p style="margin: 4px 0 0 0; opacity: 0.8;">{email_type} | {date_str}</p>
    </div>
    <div style="background: #fef2f2; border: 1px solid #fecaca; padding: 20px; border-radius: 0 0 8px 8px;">
        <div style="background:white;padding:14px 20px;border-radius:8px;border:1px solid #fecaca;text-align:center;margin-bottom:20px;">
            <div style="font-size:28px;font-weight:bold;color:#dc2626;">{validation.score}/10</div>
            <div style="font-size:12px;color:#6b7280;text-transform:uppercase;">score neutralności</div>
        </div>

        {forbidden_html}
        {problemy_html}
        {sugestie_html}

        <div style="font-size:12px;color:#6b7280;margin-top:12px;">
            {"⚠️ Email PUBLIC został WSTRZYMANY." if not validation.passed else "Email PUBLIC wysłany mimo uwag."}
        </div>
    </div>
    </body></html>
    """

    return _send_email(to, subject, html)


# ── Agenda tygodniowa (niedziela 18:00) ──────────────────────────────────────

_TYPE_EMOJI = {
    "Wyniki spółek": "📊", "WZA": "🏛️", "Dywidendy": "💰",
    "Rynek pierwotny": "🔔", "Debiuty": "🔔", "Splity": "✂️",
    "Wezwania": "📢", "Wycofania": "⏹️", "Zawieszenia": "⏸️",
    "Zmiany w indeksach": "🔄", "Dni wolne": "📅",
}

_POLISH_DAYS_UPPER = {
    0: "PONIEDZIAŁEK", 1: "WTOREK", 2: "ŚRODA",
    3: "CZWARTEK", 4: "PIĄTEK", 5: "SOBOTA", 6: "NIEDZIELA",
}


def _fmt_dividend_card(d: dict) -> str:
    """Formatuje kartę dywidendy z historią i analizą (reuse w agenda i retrospekcji)."""
    kwota = d.get("dywidenda")
    stopa = d.get("stopa_proc")
    kwota_str = f"<strong>{kwota:.2f} zł/akcję</strong>" if kwota else ""
    stopa_str = f"(stopa {stopa}%)" if stopa else ""
    ustalenia = d.get("data_ustalenia", "—")
    wyplaty = d.get("data_wyplaty", "—")

    analiza = d.get("analiza") or {}
    analiza_parts = []
    if analiza.get("rekord"):
        analiza_parts.append("<span style='color:#dc2626;font-weight:bold;'>🏆 REKORD!</span>")
    if analiza.get("zmiana_rr") is not None:
        sign = "+" if analiza["zmiana_rr"] > 0 else ""
        color = "#16a34a" if analiza["zmiana_rr"] > 0 else "#dc2626"
        analiza_parts.append(
            f"<span style='color:{color};font-weight:bold;'>kwota {sign}{analiza['zmiana_rr']}% r/r</span>"
        )
    if analiza.get("trend"):
        analiza_parts.append(f"<span style='color:#6b7280;'>trend: {analiza['trend']}</span>")
    analiza_html = " &nbsp;|&nbsp; ".join(analiza_parts)

    historia = d.get("historia") or []
    hist_rows = ""
    for h in historia[:3]:
        yr = h.get("rok", "")
        dyw = h.get("dywidenda")
        st = h.get("stopa_proc")
        dyw_str = f"{dyw:.2f} zł" if dyw else "—"
        st_str = f"{st:.1f}%" if st else ""
        hist_rows += (
            f"<tr><td style='padding:2px 8px;'>{yr}</td>"
            f"<td style='padding:2px 8px;font-weight:bold;'>{dyw_str}</td>"
            f"<td style='padding:2px 8px;color:#6b7280;'>{st_str}</td></tr>"
        )
    hist_html = ""
    if hist_rows:
        hist_html = (
            "<table style='font-size:12px;border-collapse:collapse;margin-top:6px;'>"
            "<tr><th style='padding:2px 8px;color:#6b7280;text-align:left;'>Za rok</th>"
            "<th style='padding:2px 8px;color:#6b7280;text-align:left;'>Dywidenda</th>"
            "<th style='padding:2px 8px;color:#6b7280;text-align:left;'>Stopa</th></tr>"
            f"{hist_rows}</table>"
        )

    return f"""
    <div style="border:1px solid #e5e7eb;border-left:4px solid #16a34a;
                border-radius:6px;padding:14px;margin-bottom:10px;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
            <span style="font-size:15px;font-weight:bold;">{_esc(d.get('ticker', ''))}</span>
            <span style="font-size:14px;margin-left:12px;">{kwota_str} {stopa_str}</span>
        </div>
        <div style="font-size:12px;color:#6b7280;margin-bottom:6px;">
            Ustalenie: {_esc(ustalenia)} &nbsp;|&nbsp; Wypłata: {_esc(wyplaty)}
        </div>
        {f'<div style="margin:6px 0;">{analiza_html}</div>' if analiza_html else ''}
        {hist_html}
    </div>"""


def send_agenda_tygodniowa(
    to: str,
    week_start: date,
    week_end: date,
    events: list[dict],
    dividends: list[dict],
) -> bool:
    """
    Email z agendą na nadchodzący tydzień (pon-pt) + dywidendami.
    Wysyłany w niedzielę o 18:00 do ADMIN.
    """
    from collections import defaultdict
    from datetime import timedelta

    ws = week_start.strftime("%d.%m")
    we = week_end.strftime("%d.%m")
    subject = f"ADMIN 📅 Agenda GPW | {ws}-{we}.{week_end.strftime('%Y')}"

    # ── Events grouped by day ──
    by_date: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        by_date[ev["data"]].append(ev)

    # Generate all 5 weekdays
    days_html = ""
    current = week_start
    while current <= week_end:
        d_str = current.strftime("%Y-%m-%d")
        day_name = _POLISH_DAYS_UPPER.get(current.weekday(), "")
        d_short = current.strftime("%d.%m")

        day_events = by_date.get(d_str, [])
        if day_events:
            items = ""
            for ev in day_events:
                emoji = _TYPE_EMOJI.get(ev.get("typ", ""), "📋")
                ticker = _esc(ev.get("ticker", ""))
                opis = _esc(ev.get("opis", ""))
                ticker_str = f"<strong>{ticker}</strong> — " if ticker else ""
                items += f"""
                <div style="padding:4px 0;font-size:13px;">
                    {emoji} {ticker_str}{opis}
                </div>"""
            events_block = items
        else:
            events_block = """
            <div style="padding:4px 0;font-size:13px;color:#9ca3af;font-style:italic;">
                (brak zaplanowanych wydarzeń)
            </div>"""

        days_html += f"""
        <div style="margin-bottom:16px;">
            <div style="font-size:14px;font-weight:bold;color:#0d9488;
                        border-bottom:2px solid #0d9488;padding-bottom:4px;margin-bottom:8px;">
                📆 {day_name} {d_short}
            </div>
            {events_block}
        </div>"""

        current += timedelta(days=1)

    # ── Dividends section ──
    div_section = ""
    if dividends:
        div_cards = "".join(_fmt_dividend_card(d) for d in dividends)
        count = len(dividends)
        label = "dywidenda" if count == 1 else "dywidendy" if count < 5 else "dywidend"
        div_section = f"""
        <div style="margin-top:20px;padding-top:16px;border-top:2px solid #e5e7eb;">
            <h3 style="margin:0 0 12px;color:#166534;">
                💰 Dywidendy w tym tygodniu ({count} {label})
            </h3>
            {div_cards}
        </div>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:8px;">
    <div style="background:#0d9488;color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">📅 Agenda GPW — tydzień {ws}–{we}</h2>
        <p style="margin:4px 0 0 0;opacity:0.85;font-size:13px;">
            {len(events)} wydarzeń &nbsp;|&nbsp; {len(dividends)} dywidend
        </p>
    </div>
    <div style="border:1px solid #e5e7eb;border-top:none;padding:20px;border-radius:0 0 8px 8px;">
        {days_html}
        {div_section}
    </div>
    </body></html>
    """
    return _send_email(to, subject, html)


# ── Dywidendy tygodniowe — retrospekcja (sobota 15:00) ──────────────────────

def send_dywidendy_tygodniowe(
    to: str,
    week_start: date,
    week_end: date,
    dividends: list[dict],
) -> bool:
    """
    Email z podsumowaniem dywidend z minionego tygodnia (retrospekcja).
    Wysyłany w sobotę o 15:00 do ADMIN.
    """
    ws = week_start.strftime("%d.%m")
    we = week_end.strftime("%d.%m")

    subject = f"ADMIN 💰 Dywidendy GPW | podsumowanie tygodnia {ws}-{we}"

    div_cards = "".join(_fmt_dividend_card(d) for d in dividends)
    count = len(dividends)
    label = "dywidenda" if count == 1 else "dywidendy" if count < 5 else "dywidend"

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:8px;">
    <div style="background:#166534;color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">💰 Dywidendy GPW — podsumowanie tygodnia</h2>
        <p style="margin:4px 0 0 0;opacity:0.85;font-size:13px;">
            {ws}–{we} &nbsp;|&nbsp; {count} {label}
        </p>
    </div>
    <div style="border:1px solid #e5e7eb;border-top:none;padding:20px;border-radius:0 0 8px 8px;">
        {div_cards}
    </div>
    </body></html>
    """
    return _send_email(to, subject, html)


# ── Weekly cost report (niedziela 19:00, ADMIN only) ─────────────────────────


def _fmt_pln(value: float) -> str:
    """Format PLN — 2 decimals z kropką (consistent z resztą kodu)."""
    return f"{value:.2f}"


def _fmt_delta_pct(delta_pct: float) -> str:
    """Format delta z arrow + kolor — '↑+25.5%' (red) / '↓-12.0%' (green)."""
    if delta_pct > 0:
        return f'<span style="color:#dc2626;">↑+{delta_pct:.1f}%</span>'
    if delta_pct < 0:
        return f'<span style="color:#16a34a;">↓{delta_pct:.1f}%</span>'
    return '<span style="color:#6b7280;">→0.0%</span>'


def render_weekly_cost_report(report) -> tuple[str, str]:
    """Renderuje (subject, html) bez wysyłki — używane przez dry-run.

    Args:
        report: agents.cost_report_builder.CostReport.

    Returns:
        tuple (subject, html) — gotowe do `_send_email`.
    """
    ws = report.week_start.strftime("%d.%m")
    we = report.week_end.strftime("%d.%m.%Y")
    subject_marker = "⚠" if report.has_any_anomaly else "✓"
    subject = f"ADMIN 💸 Koszty Vertex AI {subject_marker} | {ws}-{we}"

    delta_pct = report.delta_vs_prev_week_pct
    delta_html = _fmt_delta_pct(delta_pct)

    # ── Anomaly section (top, prominent gdy aktywne) ──
    anomaly_block = ""
    if report.has_any_anomaly:
        items = []
        if report.has_weekly_anomaly:
            items.append(
                f"<li><strong>Wzrost tygodniowy:</strong> "
                f"+{delta_pct:.1f}% vs poprzedni tydzień "
                f"(próg: +{report.anomaly_threshold_pct:.0f}%)</li>"
            )
        for a in report.anomalies:
            items.append(
                f"<li><strong>{_esc(a['sku'])}</strong>: "
                f"max {a['value']:.2f} PLN/dzień > próg {a['threshold']:.2f} PLN "
                f"(mean 4w: {a['mean_4w']:.2f}, σ: {a['stdev_4w']:.2f})</li>"
            )
        anomaly_block = f"""
        <div style="background:#fef3c7;border:1px solid #f59e0b;padding:12px 16px;
                    border-radius:6px;margin-bottom:16px;">
            <h3 style="margin:0 0 8px;color:#92400e;">⚠ Wykryto anomalie</h3>
            <ul style="margin:0;padding-left:20px;font-size:13px;color:#78350f;">
                {''.join(items)}
            </ul>
        </div>"""

    # ── Per SKU table ──
    sku_rows = ""
    for sku, cost_pln in sorted(report.per_sku.items(), key=lambda x: -x[1]):
        sku_rows += f"""
        <tr>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;">{_esc(sku)}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;text-align:right;">
                {_fmt_pln(cost_pln)} PLN
            </td>
        </tr>"""

    # ── Per agent table ──
    agent_rows = ""
    for agent, cost_pln in sorted(report.per_agent.items(), key=lambda x: -x[1]):
        agent_rows += f"""
        <tr>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;">{_esc(agent)}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;text-align:right;">
                {_fmt_pln(cost_pln)} PLN
            </td>
        </tr>"""
    if not agent_rows:
        agent_rows = """
        <tr><td colspan="2" style="padding:6px 8px;color:#9ca3af;font-style:italic;">
            (brak danych z Langfuse)
        </td></tr>"""

    # ── Top calls table ──
    top_rows = ""
    for call in report.top_calls:
        trace_id = _esc(call.get("trace_id") or call.get("id", "—"))
        agent = _esc(call.get("agent", "—"))
        cost_pln = call.get("cost_pln", 0.0)
        top_rows += f"""
        <tr>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-family:monospace;font-size:11px;">
                {trace_id}
            </td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;">{agent}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;text-align:right;">
                {_fmt_pln(cost_pln)} PLN
            </td>
        </tr>"""
    if not top_rows:
        top_rows = """
        <tr><td colspan="3" style="padding:6px 8px;color:#9ca3af;font-style:italic;">
            (brak observations)
        </td></tr>"""

    # ── Reconciliation note ──
    recon_color = "#16a34a" if report.reconciliation_delta_pct < 15 else "#dc2626"
    recon_block = f"""
    <p style="margin:8px 0 0;font-size:12px;color:#6b7280;">
        Reconciliation BQ vs Langfuse: BQ={_fmt_pln(report.total_pln)} PLN,
        Langfuse={_fmt_pln(report.langfuse_total_pln)} PLN,
        Δ=<span style="color:{recon_color};">{report.reconciliation_delta_pct:.1f}%</span>
    </p>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;padding:8px;">
    <div style="background:#1e40af;color:white;padding:16px 20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">💸 Koszty Vertex AI {subject_marker}</h2>
        <p style="margin:4px 0 0 0;opacity:0.85;font-size:13px;">
            Tydzień {ws}–{we} &nbsp;|&nbsp; {report.analyses_count} analiz
        </p>
    </div>
    <div style="border:1px solid #e5e7eb;border-top:none;padding:20px;border-radius:0 0 8px 8px;">
        {anomaly_block}

        <div style="background:#f9fafb;padding:16px;border-radius:6px;margin-bottom:16px;">
            <table style="width:100%;font-size:14px;">
                <tr>
                    <td style="padding:4px 0;color:#6b7280;">Total tydzień:</td>
                    <td style="padding:4px 0;text-align:right;">
                        <strong style="font-size:18px;">{_fmt_pln(report.total_pln)} PLN</strong>
                    </td>
                </tr>
                <tr>
                    <td style="padding:4px 0;color:#6b7280;">vs poprzedni tydzień:</td>
                    <td style="padding:4px 0;text-align:right;">
                        {_fmt_pln(report.total_pln_prev_week)} PLN
                        &nbsp; {delta_html}
                    </td>
                </tr>
                <tr>
                    <td style="padding:4px 0;color:#6b7280;">Cost per analiza:</td>
                    <td style="padding:4px 0;text-align:right;">
                        {_fmt_pln(report.cost_per_analysis_pln)} PLN
                    </td>
                </tr>
            </table>
        </div>

        <h3 style="margin:16px 0 8px;color:#1e40af;">Per SKU (BQ billing — autorytatywne)</h3>
        <table style="width:100%;font-size:13px;border-collapse:collapse;">
            {sku_rows}
        </table>

        <h3 style="margin:20px 0 8px;color:#1e40af;">Per agent (Langfuse attribution)</h3>
        <table style="width:100%;font-size:13px;border-collapse:collapse;">
            {agent_rows}
        </table>

        <h3 style="margin:20px 0 8px;color:#1e40af;">Top {len(report.top_calls)} najdroższych wywołań</h3>
        <table style="width:100%;font-size:13px;border-collapse:collapse;">
            <thead>
                <tr style="background:#f3f4f6;">
                    <th style="padding:6px 8px;text-align:left;">Trace ID</th>
                    <th style="padding:6px 8px;text-align:left;">Agent</th>
                    <th style="padding:6px 8px;text-align:right;">Koszt</th>
                </tr>
            </thead>
            <tbody>
                {top_rows}
            </tbody>
        </table>

        {recon_block}
    </div>
    </body></html>
    """
    return subject, html


def send_weekly_cost_report(to: str, report) -> bool:
    """Wysyła tygodniowy raport kosztów do ADMIN. Wrapper na render + _send_email."""
    subject, html = render_weekly_cost_report(report)
    return _send_email(to, subject, html)