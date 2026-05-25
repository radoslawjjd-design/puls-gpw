"""
Normalizacja nazw spółek giełdowych i matchowanie tickerów GPW.

Algorytm match_ticker() próbuje 6 strategii w kolejności priorytetu:
  0. Słownik wyjątków (_TICKER_ALIASES) — spółki z akronimami/myląco brzmiącymi nazwami
  1. Bezpośrednie dopasowanie znormalizowanej nazwy do tickera
  2. Nazwa bez spacji = ticker (CD PROJEKT → CDPROJEKT)
  3. Ticker jest prefiksem znormalizowanej nazwy
  4. Pierwsze słowo = ticker
  5. Ticker jako całe słowo w tekście (min 4 znaki — bariera przed BOS, MOJ, FON)

Zródło tickerów: data/company_list.json (załadowany raz, lru_cache).
"""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


def _strip_diacritics(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


_LEGAL_SUFFIXES = [
    r"\bS\.A\.", r"\bSA\b", r"\bSP\.\s*Z\s*O\.O\.", r"\bSP\.\s*Z\s*O\.O",
    r"\bSP\s*Z\s*O\.O\.", r"\bSP\s*Z\s*O\.O", r"\bSPÓŁKA\s+AKCYJNA",
    r"\bASI\b", r"\bFIZ\b", r"\bSFIO\b", r"\bFIO\b", r"\bTFI\b", r"\bDM\b",
    r"\bAD\b", r"\bN\.V\.", r"\bNV\b", r"\bSE\b", r"\bLTD\.?", r"\bLIMITED\b",
    r"\bPLC\.?", r"\bGMBH\b", r"\bAG\b", r"\bBV\b", r"\bSRL\b", r"\bSARL\b",
    r"\bASA\b", r"\bOY\b", r"\bAB\b(?!\s+S)",
    r"\bW\s+RESTRUKTURYZACJI\b", r"\bW\s+UPADŁOŚCI\b", r"\bW\s+LIKWIDACJI\b",
]

_SUFFIX_PATTERN = re.compile(
    r"[\s,]+(" + "|".join(_LEGAL_SUFFIXES) + r")[\s,]*$",
    re.IGNORECASE,
)

_TICKER_ALIASES: dict[str, str] = {
    "PKO BANK POLSKI":            "PKOBP",
    "PKO BP":                     "PKOBP",
    "POWSZECHNA KASA OSZCZEDNOSCI": "PKOBP",
    "BANK PEKAO":                 "PEKAO",
    "PEKAO":                      "PEKAO",
    "ING BANK SLASKI":            "INGBSK",
    "ING BANK ŚLĄSKI":            "INGBSK",
    "BANK MILLENNIUM":            "MILLENNIUM",
    "BANK HANDLOWY":              "HANDLOWY",
    "BANK HANDLOWY W WARSZAWIE":  "HANDLOWY",
    "SANTANDER BANK POLSKA":      "SANTANDER",
    "BANK SANTANDER":             "SANTANDER",
    "BNP PARIBAS BANK POLSKA":    "BNPPPL",
    "BNP PARIBAS":                "BNPPPL",
    "GETIN NOBLE BANK":           "GETIN",
    "GETIN BANK":                 "GETIN",
    "POLSKA GRUPA ENERGETYCZNA":  "PGE",
    "TAURON POLSKA ENERGIA":      "TAURONPE",
    "TAURON":                     "TAURONPE",
    "ENEA":                       "ENEA",
    "CYFROWY POLSAT":             "CYFRPLSAT",
    "ORANGE POLSKA":              "ORANGEPL",
    "WIRTUALNA POLSKA":           "WIRTUALNA",
    "PKN ORLEN":                  "PKNORLEN",
    "ORLEN":                      "PKNORLEN",
    "LOTOS":                      "PKNORLEN",
    "GRUPA AZOTY POLICE":         "POLICE",
    "AZOTY POLICE":               "POLICE",
    "PKP CARGO":                  "PKPCARGO",
    "MLP GROUP":                  "MLPGROUP",
    "ATM GRUPA":                  "ATMGRUPA",
    "AC AUTOGAZ":                 "ACAUTOGAZ",
    "BENEFIT SYSTEMS":            "BENEFIT",
    "OPONEO PL":                  "OPONEO",
    "CD PROJEKT RED":             "CDPROJEKT",
    "SILVAIR REGS":               "SILVAIR",
    "GRENEVIA":                   "GRENEVIA",
    "ASSECO POLAND":              "ASSECOPOL",
    "ASSECO BUSINESS SOLUTIONS":  "ASSECOBS",
    "ASSECO SEE":                 "ASSECOSEE",
    "NTT SYSTEM":                 "NTTSYSTEM",
    "NTTSYSTEM":                  "NTTSYSTEM",
    "DIGITAL NETWORK":            "DIGITANET",
    "CYBER FOLKS":                "CYBERFLKS",
    "PCC ROKITA":                 "PCCROKITA",
    "CZERWONA TOREBKA":           "CZTOREBKA",
    "KDM SHIPPING":               "KDMSHIPNG",
    "MO-BRUK":                    "MOBRUK",
    "MOBRUK":                     "MOBRUK",
    "PJP MAKRUM":                 "PJPMAKRUM",
    "BBI DEVELOPMENT":            "BBIDEV",
    "KINO POLSKA TV":             "KINOPOL",
    "KINOPOL":                    "KINOPOL",
    "NEW TECH CAPITAL":           "NTCAPITAL",
    "ZUK":                        "STAPORKOW",
    "DR MIELE COSMED GROUP":      "DMGROUP",
    "KSG AGRO":                   "KSGAGRO",
    "CPI FIM":                    "ORCOGROUP",
    "CITY SERVICE":               "CITYSERV",
    "ECB":                        "ECBSA",
    "LSI SOFTWARE":               "LSISOFT",
    "PBS FINANSE":                "PBSFINANSE",
    "VIGO PHOTONICS":             "VIGOPHOTN",
    "COAL ENERGY":                "COALENERG",
    "EKO EXPORT":                 "EKOEXPORT",
    "MEX POLSKA":                 "MEXPOLSKA",
    "PCC EXOL":                   "PCCEXOL",
}

_MAX_COMPANY_NAME_LEN = 50
_MIN_TICKER_LEN_FOR_CONTAINS = 4
_TITLE_KEY_LEN = 40

_COMPANY_LIST_PATH = Path(__file__).resolve().parent.parent / "data" / "company_list.json"

_FUND_PATTERN = re.compile(
    r"\b(FIZ|SFIO|FIO|ASI|TFI|FUNDUSZ\s+INWESTYCYJNY|ETF)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _load_ticker_set() -> set[str]:
    if not _COMPANY_LIST_PATH.exists():
        return set()
    try:
        with open(_COMPANY_LIST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {s["ticker"].upper().removesuffix(".PL") for s in data.get("spolki", [])}
    except Exception:
        return set()


def normalize_company_name(name: str) -> str:
    if not name:
        return "UNKNOWN"
    result = name.strip()
    for _ in range(3):
        new = _SUFFIX_PATTERN.sub("", result).strip().rstrip(",").strip()
        if new == result:
            break
        result = new
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', result)
    result = re.sub(r'\s+', ' ', result).strip().upper()
    return result[:_MAX_COMPANY_NAME_LEN].strip() or "UNKNOWN"


def match_ticker(title: str, company_raw: str | None = None) -> str | None:
    tickers = _load_ticker_set()
    if not tickers:
        return None

    candidates = []
    for sep in (": ", " — ", " – ", " - "):
        if sep in title:
            candidates.append(title.split(sep)[0].strip())
            break
    candidates.append(title[:60])
    if company_raw:
        candidates.append(company_raw)

    for candidate in candidates:
        result = _find_ticker_in_text(candidate, tickers)
        if result:
            return result
    return None


def _find_ticker_in_text(text: str, tickers: set[str]) -> str | None:
    normalized = normalize_company_name(text)
    if not normalized or normalized == "UNKNOWN":
        return None

    normalized_nd = _strip_diacritics(normalized)

    # Strategia 0: słownik wyjątków (najwyższy priorytet)
    if normalized in _TICKER_ALIASES:
        return _TICKER_ALIASES[normalized]
    for alias_key in sorted(_TICKER_ALIASES, key=len, reverse=True):
        alias_nd = _strip_diacritics(alias_key)
        if len(alias_key) < 4:
            if re.search(r'(?<![A-Z0-9])' + re.escape(alias_nd) + r'(?![A-Z0-9])', normalized_nd):
                return _TICKER_ALIASES[alias_key]
        else:
            if alias_nd in normalized_nd:
                return _TICKER_ALIASES[alias_key]

    # Strategia 1: bezpośrednie dopasowanie
    if normalized in tickers:
        return normalized
    if normalized_nd in tickers:
        return normalized_nd

    # Strategia 2: nazwa bez spacji = ticker
    for norm in (normalized, normalized_nd):
        no_space = norm.replace(" ", "")
        if no_space in tickers:
            return no_space

    # Strategia 3: ticker jest prefiksem nazwy
    for ticker in sorted(tickers, key=len, reverse=True):
        if len(ticker) >= _MIN_TICKER_LEN_FOR_CONTAINS:
            for norm in (normalized, normalized_nd):
                if norm.startswith(ticker):
                    rest = norm[len(ticker):]
                    if not rest or rest[0] == " ":
                        return ticker

    # Strategia 4: pierwsze słowo = ticker
    for norm in (normalized, normalized_nd):
        parts = norm.split()
        if parts and len(parts[0]) >= _MIN_TICKER_LEN_FOR_CONTAINS and parts[0] in tickers:
            return parts[0]

    # Strategia 5: ticker jako całe słowo w tekście (podniesiona granica: min 4 znaki)
    text_upper = text.upper()
    for ticker in sorted(tickers, key=len, reverse=True):
        if len(ticker) >= _MIN_TICKER_LEN_FOR_CONTAINS and ticker in text_upper:
            if re.search(r'(?<![A-Z0-9])' + re.escape(ticker) + r'(?![A-Z0-9])', text_upper):
                return ticker

    return None


def is_fund(title: str, company_raw: str | None = None) -> bool:
    for text in filter(None, [company_raw, title]):
        if _FUND_PATTERN.search(text):
            return True
    return False


def get_folder_name(
    title: str,
    company_raw: str | None = None,
    ticker_hint: str | None = None,
) -> str:
    if ticker_hint:
        return ticker_hint.upper().strip()
    ticker = match_ticker(title, company_raw)
    if ticker:
        return ticker
    if company_raw:
        return normalize_company_name(company_raw)
    for sep in (": ", " — ", " – ", " - "):
        if sep in title:
            return normalize_company_name(title.split(sep)[0])
    return normalize_company_name(title[:_TITLE_KEY_LEN])


def normalize_title_key(title: str) -> str:
    return title.lower().strip()[:_TITLE_KEY_LEN]
