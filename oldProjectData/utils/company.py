"""
Normalizacja nazw spółek giełdowych i matchowanie tickerów GPW.

Funkcje:
    normalize_company_name()  — usuwa sufiksy prawne, zwraca UPPERCASE
    match_ticker()            — dopasowuje tytuł ogłoszenia do tickera z company_list.json
    get_folder_name()         — główna funkcja: ticker lub znormalizowana nazwa
    normalize_title_key()     — normalizuje tytuł do klucza deduplikacji
"""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


def _strip_diacritics(text: str) -> str:
    """Usuwa polskie znaki diakrytyczne: Ą→A, Ę→E, Ś→S, Ź→Z, Ż→Z, Ó→O, Ń→N, Ć→C, Ł→L."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )

# ── Przyrostki prawne ──────────────────────────────────────────────────────────
_LEGAL_SUFFIXES = [
    r"\bS\.A\.",
    r"\bSA\b",
    r"\bSP\.\s*Z\s*O\.O\.",
    r"\bSP\.\s*Z\s*O\.O",
    r"\bSP\s*Z\s*O\.O\.",
    r"\bSP\s*Z\s*O\.O",
    r"\bSPÓŁKA\s+AKCYJNA",
    r"\bASI\b",
    r"\bFIZ\b",
    r"\bSFIO\b",
    r"\bFIO\b",
    r"\bTFI\b",
    r"\bDM\b",
    r"\bAD\b",
    r"\bN\.V\.",
    r"\bNV\b",
    r"\bSE\b",
    r"\bLTD\.?",
    r"\bLIMITED\b",
    r"\bPLC\.?",
    r"\bGMBH\b",
    r"\bAG\b",
    r"\bBV\b",
    r"\bSRL\b",
    r"\bSARL\b",
    r"\bASA\b",
    r"\bOY\b",
    r"\bAB\b(?!\s+S)",
    r"\bW\s+RESTRUKTURYZACJI\b",
    r"\bW\s+UPADŁOŚCI\b",
    r"\bW\s+LIKWIDACJI\b",
]

_SUFFIX_PATTERN = re.compile(
    r"[\s,]+(" + "|".join(_LEGAL_SUFFIXES) + r")[\s,]*$",
    re.IGNORECASE,
)

# ── Słownik wyjątków: znormalizowana nazwa → ticker ───────────────────────────
# Dla spółek gdzie automatyczny matching zawodzi (ticker to akronim lub zlepek)
# Klucze: UPPERCASE, bez sufiksów prawnych, bez znaków specjalnych
_TICKER_ALIASES: dict[str, str] = {
    # Banki
    "PKO BANK POLSKI":          "PKOBP",
    "PKO BP":                   "PKOBP",
    "POWSZECHNA KASA OSZCZEDNOSCI": "PKOBP",
    "BANK PEKAO":               "PEKAO",
    "PEKAO":                    "PEKAO",
    "ING BANK SLASKI":          "INGBSK",
    "ING BANK ŚLĄSKI":          "INGBSK",
    "BANK MILLENNIUM":          "MILLENNIUM",
    "BANK HANDLOWY":            "HANDLOWY",
    "BANK HANDLOWY W WARSZAWIE": "HANDLOWY",
    "SANTANDER BANK POLSKA":    "SANTANDER",
    "BANK SANTANDER":           "SANTANDER",
    "BNP PARIBAS BANK POLSKA":  "BNPPPL",
    "BNP PARIBAS":              "BNPPPL",
    "GETIN NOBLE BANK":         "GETIN",
    "GETIN BANK":               "GETIN",
    # Energetyka
    "POLSKA GRUPA ENERGETYCZNA": "PGE",
    "TAURON POLSKA ENERGIA":    "TAURONPE",
    "TAURON":                   "TAURONPE",
    "ENEA":                     "ENEA",
    # Media / telco
    "CYFROWY POLSAT":           "CYFRPLSAT",
    "ORANGE POLSKA":            "ORANGEPL",
    "WIRTUALNA POLSKA":         "WIRTUALNA",
    # Paliwa / chemia
    "PKN ORLEN":                "PKNORLEN",
    "ORLEN":                    "PKNORLEN",
    "LOTOS":                    "PKNORLEN",   # po fuzji
    "GRUPA AZOTY POLICE":       "POLICE",
    "AZOTY POLICE":             "POLICE",
    # Transport
    "PKP CARGO":                "PKPCARGO",
    # Inne
    "MLP GROUP":                "MLPGROUP",
    "ATM GRUPA":                "ATMGRUPA",
    "AC AUTOGAZ":               "ACAUTOGAZ",
    "BENEFIT SYSTEMS":          "BENEFIT",
    "OPONEO PL":                "OPONEO",
    "CD PROJEKT RED":           "CDPROJEKT",
    "SILVAIR REGS":             "SILVAIR",
    "GRENEVIA":                 "GRENEVIA",
    "ASSECO POLAND":            "ASSECOPOL",
    "ASSECO BUSINESS SOLUTIONS": "ASSECOBS",
    "ASSECO SEE":               "ASSECOSEE",
    # Nazwy spółek wymagające aliasów (nie matchują automatycznie)
    # 3-znakowe tickery (min 4 wymagane w strategii 5) — aliasy obowiązkowe
    "NTT SYSTEM":               "NTTSYSTEM",
    "NTTSYSTEM":                "NTTSYSTEM",   # idempotencja
    "DIGITAL NETWORK":          "DIGITANET",
    "CYBER FOLKS":              "CYBERFLKS",
    "PCC ROKITA":               "PCCROKITA",
    "CZERWONA TOREBKA":         "CZTOREBKA",
    "KDM SHIPPING":             "KDMSHIPNG",
    "MO-BRUK":                  "MOBRUK",
    "MOBRUK":                   "MOBRUK",
    "PJP MAKRUM":               "PJPMAKRUM",
    "BBI DEVELOPMENT":          "BBIDEV",
    "KINO POLSKA TV":           "KINOPOL",
    "KINOPOL":                  "KINOPOL",
    "NEW TECH CAPITAL":         "NTCAPITAL",
    "ZUK":                      "STAPORKOW",
    "DR MIELE COSMED GROUP":    "DMGROUP",
    "KSG AGRO":                 "KSGAGRO",
    "CPI FIM":                  "ORCOGROUP",
    "CITY SERVICE":             "CITYSERV",
    "ECB":                      "ECBSA",
    "LSI SOFTWARE":             "LSISOFT",
    "PBS FINANSE":              "PBSFINANSE",
    "VIGO PHOTONICS":           "VIGOPHOTN",
    "COAL ENERGY":              "COALENERG",
    "EKO EXPORT":               "EKOEXPORT",
    "MEX POLSKA":               "MEXPOLSKA",
    "PCC EXOL":                 "PCCEXOL",
}

# ── Ładowanie listy spółek ─────────────────────────────────────────────────────
# Limity
_MAX_COMPANY_NAME_LEN = 50        # max znaków znormalizowanej nazwy spółki
_MIN_TICKER_LEN_FOR_CONTAINS = 4  # min długość tickera w strategii whole-word (strategia 3-5)
_TITLE_KEY_LEN = 40               # max znaków klucza deduplikacji tytułu

_COMPANY_LIST_PATH = Path(__file__).resolve().parent.parent / "data" / "company_list.json"


@lru_cache(maxsize=1)
def _load_ticker_set() -> set[str]:
    """
    Wczytuje zestaw tickerów z data/company_list.json.
    Cache — wczytywane tylko raz na uruchomienie.
    """
    if not _COMPANY_LIST_PATH.exists():
        return set()
    try:
        with open(_COMPANY_LIST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {s["ticker"].upper() for s in data.get("spolki", [])}
    except Exception:
        return set()


# ── Normalizacja nazwy ─────────────────────────────────────────────────────────

def normalize_company_name(name: str) -> str:
    """
    Normalizuje nazwę spółki do formy kanonicznej:
    - Usuwa przyrostki prawne (S.A., SA, sp. z o.o. itp.)
    - UPPERCASE
    - Max 50 znaków
    """
    if not name:
        return "UNKNOWN"

    result = name.strip()

    for _ in range(3):
        new = _SUFFIX_PATTERN.sub("", result).strip().rstrip(",").strip()
        if new == result:
            break
        result = new

    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', result)
    result = re.sub(r'\s+', ' ', result).strip()
    result = result.upper()
    result = result[:_MAX_COMPANY_NAME_LEN].strip()

    return result or "UNKNOWN"


# ── Matchowanie tickera ────────────────────────────────────────────────────────

def match_ticker(title: str, company_raw: str | None = None) -> str | None:
    """
    Dopasowuje ogłoszenie do tickera GPW z company_list.json.

    Algorytm (w kolejności priorytetu):
    1. Słownik wyjątków (_TICKER_ALIASES) — dla spółek z akronimami
    2. Bezpośrednie dopasowanie znormalizowanej nazwy do tickera
    3. Znormalizowana nazwa bez spacji = ticker (CD PROJEKT → CDPROJEKT)
    4. Ticker jest prefiksem znormalizowanej nazwy (min 4 znaki)
    5. Pierwsze słowo = ticker (min 4 znaki)
    6. Ticker w tekście — całe słowo, min 4 znaki (zabezpieczenie przed BOS, MOJ, FON)

    Returns:
        ticker (np. "PASSUS") lub None jeśli brak dopasowania
    """
    tickers = _load_ticker_set()
    if not tickers:
        return None

    candidates = []

    # Część przed separatorem w tytule
    for sep in (": ", " — ", " – ", " - "):
        if sep in title:
            candidates.append(title.split(sep)[0].strip())
            break

    # Cały tytuł jako fallback
    candidates.append(title[:60])

    # company_raw jeśli podany
    if company_raw:
        candidates.append(company_raw)

    for candidate in candidates:
        result = _find_ticker_in_text(candidate, tickers)
        if result:
            return result

    return None


def _find_ticker_in_text(text: str, tickers: set[str]) -> str | None:
    """Szuka tickera w podanym tekście — różne strategie matchowania."""
    normalized = normalize_company_name(text)
    if not normalized or normalized == "UNKNOWN":
        return None

    # Strategia 0: słownik wyjątków (najwyższy priorytet)
    # Sprawdzamy pełną znormalizowaną nazwę i stopniowo skracamy
    if normalized in _TICKER_ALIASES:
        return _TICKER_ALIASES[normalized]

    # Wersja znormalizowana bez polskich znaków diakrytycznych (TARCZYŃSKI → TARCZYNSKI)
    normalized_nd = _strip_diacritics(normalized)

    # Sprawdź też częściowe dopasowania z aliasów (np. "PKO BP S.A. Raport" → "PKO BP")
    # Sortuj od najdłuższego klucza żeby uniknąć błędnych skróconych trafień
    # Krótkie aliasy (< 4 znaki) wymagają granicy słowa żeby uniknąć fałszywych trafień
    for alias_key in sorted(_TICKER_ALIASES, key=len, reverse=True):
        alias_nd = _strip_diacritics(alias_key)
        if len(alias_key) < 4:
            if re.search(r'(?<![A-Z0-9])' + re.escape(alias_nd) + r'(?![A-Z0-9])', normalized_nd):
                return _TICKER_ALIASES[alias_key]
        else:
            if alias_nd in normalized_nd:
                return _TICKER_ALIASES[alias_key]

    # Strategia 1: bezpośrednie dopasowanie (z i bez diakrytyków)
    if normalized in tickers:
        return normalized
    if normalized_nd in tickers:
        return normalized_nd

    # Strategia 2: nazwa bez spacji = ticker (CD PROJEKT → CDPROJEKT)
    no_space = normalized.replace(" ", "")
    if no_space in tickers:
        return no_space
    no_space_nd = normalized_nd.replace(" ", "")
    if no_space_nd in tickers:
        return no_space_nd

    # Strategia 3: ticker jest prefiksem znormalizowanej nazwy
    # np. "ALLEGRO EU" → ticker "ALLEGRO"
    for ticker in sorted(tickers, key=len, reverse=True):
        if len(ticker) >= _MIN_TICKER_LEN_FOR_CONTAINS:
            for norm in (normalized, normalized_nd):
                if norm.startswith(ticker):
                    rest = norm[len(ticker):]
                    if not rest or rest[0] == " ":
                        return ticker

    # Strategia 4: pierwsze słowo = ticker
    parts = normalized.split()
    if parts and len(parts[0]) >= _MIN_TICKER_LEN_FOR_CONTAINS and parts[0] in tickers:
        return parts[0]
    parts_nd = normalized_nd.split()
    if parts_nd and len(parts_nd[0]) >= _MIN_TICKER_LEN_FOR_CONTAINS and parts_nd[0] in tickers:
        return parts_nd[0]

    # Strategia 5: ticker jako całe słowo w tekście
    # Podniesiona granica zapobiega fałszywym trafieniom
    # dla krótkich tickerów jak BOS, MOJ, FON, INC, GPW w niepowiązanych tekstach
    text_upper = text.upper()
    for ticker in sorted(tickers, key=len, reverse=True):
        if len(ticker) >= _MIN_TICKER_LEN_FOR_CONTAINS and ticker in text_upper:
            if re.search(r'(?<![A-Z0-9])' + re.escape(ticker) + r'(?![A-Z0-9])', text_upper):
                return ticker

    return None


# ── Filtr funduszy ────────────────────────────────────────────────────────────

_FUND_PATTERN = re.compile(
    r"\b(FIZ|SFIO|FIO|ASI|TFI|FUNDUSZ\s+INWESTYCYJNY|ETF)\b",
    re.IGNORECASE,
)


def is_fund(title: str, company_raw: str | None = None) -> bool:
    """
    Zwraca True jeśli ogłoszenie pochodzi od funduszu (FIZ/SFIO/FIO/ASI/TFI/ETF).
    """
    for text in filter(None, [company_raw, title]):
        if _FUND_PATTERN.search(text):
            return True
    return False


def get_alternative_folder_names(folder_name: str) -> list[str]:
    """
    Zwraca listę alternatywnych nazw folderów dla danej spółki.
    Używane do wykrywania duplikatów w Drive (np. "NTT SYSTEM" vs "NTT").

    Jeśli folder_name to ticker (np. "NTT") → szuka aliasów które mapują na ten ticker.
    Jeśli folder_name to alias (np. "NTT SYSTEM") → zwraca ticker docelowy.
    """
    alternatives = []
    name_upper = folder_name.upper().strip()

    # Jeśli folder_name jest w aliasach → dodaj ticker docelowy
    if name_upper in _TICKER_ALIASES:
        ticker = _TICKER_ALIASES[name_upper]
        alternatives.append(ticker)

    # Jeśli folder_name jest tickerem → szukaj aliasów które na niego mapują
    tickers = _load_ticker_set()
    if name_upper in tickers or name_upper in _TICKER_ALIASES.values():
        target = name_upper
        for alias_key, alias_ticker in _TICKER_ALIASES.items():
            if alias_ticker == target and alias_key != name_upper:
                alternatives.append(alias_key)

    return alternatives


def get_folder_name(
    title: str,
    company_raw: str | None = None,
    ticker_hint: str | None = None,
) -> str:
    """
    Główna funkcja — zwraca nazwę folderu dla ogłoszenia.

    Próbuje match_ticker() → fallback na normalize_company_name().

    Args:
        title:       tytuł ogłoszenia (np. "PASSUS S.A.: Zwołanie NWZ")
        company_raw: surowa nazwa spółki wyciągnięta przez scraper
        ticker_hint: ticker z authoritative source (np. ?symbol= z URL Bankier);
                     gdy podany, nadpisuje wynik match_ticker()

    Returns:
        ticker GPW (np. "PASSUS") lub znormalizowana nazwa (fallback)
    """
    if ticker_hint:
        return ticker_hint.upper().strip()

    ticker = match_ticker(title, company_raw)
    if ticker:
        return ticker

    # Fallback — normalizuj company_raw lub część tytułu
    if company_raw:
        return normalize_company_name(company_raw)

    for sep in (": ", " — ", " – ", " - "):
        if sep in title:
            return normalize_company_name(title.split(sep)[0])

    return normalize_company_name(title[:_TITLE_KEY_LEN])


def normalize_title_key(title: str) -> str:
    """
    Normalizuje tytuł ogłoszenia do klucza deduplikacji.
    """
    return title.lower().strip()[:_TITLE_KEY_LEN]