"""
Scraper danych spółek z Bankier.pl.
Pobiera: opis działalności, sektor, konkurencję, dywidendy, wyniki finansowe.
Używany przez profile.py do budowania profili spółek portfelowych.
"""
import logging
import re

from utils.timezone import today_warsaw

logger = logging.getLogger(__name__)

BANKIER_BASE = "https://www.bankier.pl/gielda/notowania/akcje"


def _get(url: str) -> str | None:
    """Pobiera stronę przez scraper/base.py i zwraca tekst."""
    from scraper.base import get
    resp = get(url)
    if resp is None:
        return None
    return resp.text


def _parse_basic_info(html: str) -> dict:
    """Parsuje sekcję podstawowych informacji i opis działalności."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    result = {}

    # Sektor — szukaj tylko w <li> (pomijaj kontenery i label-only <span>)
    for item in soup.find_all("li", class_=re.compile(r"data-list__item")):
        text = item.get_text(separator=" ", strip=True)
        if "Makrosektor" in text:
            val = text.replace("Makrosektor", "").strip()
            if val and len(val) < 100:
                result["makrosektor"] = val
        if "Sektor/branża" in text or "Sektor" in text:
            val = text.replace("Sektor/branża", "").replace("Sektor", "").strip()
            if val and len(val) < 100:
                result["sektor"] = val
        if "Prezes" in text:
            val = text.replace("Prezes", "").strip()
            if val and len(val) < 100:
                result["prezes"] = val
        if "Zatrudnienie" in text:
            val = text.replace("Zatrudnienie", "").strip()
            if val and len(val) < 100:
                result["zatrudnienie"] = val

    # Opis działalności — szukaj sekcji z opisem
    desc_box = soup.find(class_=re.compile(r"description"))
    if desc_box:
        # Szukaj tekstu PL (przed EN)
        full_text = desc_box.get_text(separator=" ", strip=True)
        # Odetnij angielską wersję jeśli zaczyna się od "ENGThe" lub podobne
        if "ENG" in full_text:
            result["opis_pl"] = full_text.split("ENG")[0].strip()
            result["opis_en"] = full_text.split("ENG")[1].replace("The", "The ", 1).strip()
        elif "EN" in full_text and len(full_text) > 200:
            result["opis_pl"] = full_text[:len(full_text)//2].strip()
        else:
            result["opis_pl"] = full_text

    # Jeśli nie znaleziono przez klasę — szukaj przez tekst
    if "opis_pl" not in result:
        for tag in soup.find_all(["p", "div", "section"]):
            text = tag.get_text(strip=True)
            if len(text) > 100 and any(w in text.lower() for w in ["spółka", "grupa", "specjalizuje", "oferuje", "świadczy"]):
                if "ENGThe" in text or "ENG" in text:
                    result["opis_pl"] = text.split("ENG")[0].strip()
                else:
                    result["opis_pl"] = text
                break

    # Konkurencja
    competitors = []
    for link in soup.find_all("a", href=re.compile(r"/gielda/notowania/akcje/")):
        name = link.get_text(strip=True)
        if name and len(name) > 2 and name not in competitors:
            competitors.append(name)
    if competitors:
        result["konkurencja_sektorowa"] = competitors[:20]

    return result


def _parse_dividends(html: str) -> list[dict]:
    """Parsuje historię dywidend."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    dividends = []

    # Szukaj tabel z dywidendami
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows[1:]:  # Pomiń header
            cells = row.find_all(["td", "th"])
            if len(cells) >= 4:
                try:
                    rok     = cells[0].get_text(strip=True)
                    dzien   = cells[1].get_text(strip=True)
                    kwota   = cells[2].get_text(strip=True)
                    stopa   = cells[3].get_text(strip=True)
                    status  = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    wyplata = cells[6].get_text(strip=True) if len(cells) > 6 else ""

                    if rok and kwota and any(c.isdigit() for c in kwota):
                        dividends.append({
                            "rok":           rok,
                            "dzien_dywidendy": dzien,
                            "kwota_na_akcje": kwota,
                            "stopa_dywidendy": stopa,
                            "status":        status,
                            "data_wyplaty":  wyplata,
                        })
                except Exception:
                    continue

    return dividends


def _parse_financials(html: str) -> dict:
    """Parsuje wyniki finansowe — P/E, EV/EBITDA, przychody."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    result = {}

    # Szukaj tabel z wskaźnikami
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                value = cells[1].get_text(strip=True)
                if "p/e" in label or "cena/zysk" in label:
                    result["pe_ratio"] = value
                elif "ev/ebitda" in label:
                    result["ev_ebitda"] = value
                elif "przychód" in label or "przychody" in label:
                    result["przychody"] = value
                elif "zysk netto" in label:
                    result["zysk_netto"] = value
                elif "kapitalizacja" in label:
                    result["kapitalizacja"] = value
                elif "p/bv" in label or "cena/wartość" in label:
                    result["pb_ratio"] = value

    return result


def _parse_recommendations(html: str) -> list[dict]:
    """Parsuje rekomendacje analityków."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    recs = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) >= 3:
                try:
                    data    = cells[0].get_text(strip=True)
                    typ     = cells[1].get_text(strip=True)  # Kupuj/Trzymaj/Sprzedaj
                    cena_d  = cells[2].get_text(strip=True)  # Cena docelowa
                    dom     = cells[3].get_text(strip=True) if len(cells) > 3 else ""

                    if data and typ and any(t in typ for t in ["Kupuj", "Trzymaj", "Sprzedaj", "Akumuluj", "Redukuj"]):
                        recs.append({
                            "data":           data,
                            "rekomendacja":   typ,
                            "cena_docelowa":  cena_d,
                            "dom_maklerski":  dom,
                        })
                except Exception:
                    continue

    return recs[:5]  # Ostatnie 5 rekomendacji


def scrape_basic_info(ticker: str) -> dict:
    """
    Pobiera TYLKO podstawowe dane spółki z Bankier (1 HTTP call).
    Zwraca: {ticker, sektor, makrosektor, opis_pl, ...} — minimum dla profilu sektorowego.
    """
    data = {"ticker": ticker}
    url = f"{BANKIER_BASE}/{ticker}/podstawowe-dane"
    html = _get(url)
    if html:
        basic = _parse_basic_info(html)
        data.update(basic)
        logger.info(f"  Basic info {ticker}: sektor={basic.get('sektor', '?')}")
    else:
        logger.warning(f"  Brak dostępu do strony {ticker}")
    return data


def scrape_company_data(ticker: str) -> dict:
    """
    Główna funkcja — scrapuje wszystkie dostępne dane spółki z Bankier.
    Zwraca słownik ze wszystkimi danymi lub pusty dict przy błędzie.
    """
    logger.info(f"Scrapy Bankier dla {ticker}...")
    data = {"ticker": ticker, "zrodlo_bankier": True, "data_scrape": str(today_warsaw())}

    # 1. Podstawowe dane i opis
    url_basic = f"{BANKIER_BASE}/{ticker}/podstawowe-dane"
    html = _get(url_basic)
    if html:
        basic = _parse_basic_info(html)
        data.update(basic)
        logger.info(f"  Podstawowe dane: sektor={basic.get('sektor','?')}, opis={'tak' if 'opis_pl' in basic else 'brak'}")
    else:
        logger.warning(f"  Brak dostępu do strony podstawowej {ticker}")

    # 2. Dywidendy
    url_div = f"{BANKIER_BASE}/{ticker}/dywidendy"
    html = _get(url_div)
    if html:
        divs = _parse_dividends(html)
        data["historia_dywidend"] = divs
        logger.info(f"  Dywidendy: {len(divs)} rekordów")

    # 3. Wyniki finansowe
    url_fin = f"{BANKIER_BASE}/{ticker}/wyniki-finansowe"
    html = _get(url_fin)
    if html:
        fins = _parse_financials(html)
        data["wskazniki_finansowe"] = fins
        logger.info(f"  Wskaźniki finansowe: {list(fins.keys())}")

    # 4. Rekomendacje
    url_rec = f"{BANKIER_BASE}/{ticker}/rekomendacje"
    html = _get(url_rec)
    if html:
        recs = _parse_recommendations(html)
        data["rekomendacje_analitykow"] = recs
        logger.info(f"  Rekomendacje: {len(recs)} rekordów")

    return data