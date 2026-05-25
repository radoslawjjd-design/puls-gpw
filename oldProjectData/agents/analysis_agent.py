import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path

import sentry_sdk
from bs4 import XMLParsedAsHTMLWarning

from agents.prompts import (
    ANALYSIS_PORTFOLIO_TEMPLATE,
    ANALYSIS_SYSTEM,
    MACRO_CONTEXT_SECTION,
    PROFILE_CONTEXT_SECTION,
    XHTML_REPAIR_TEMPLATE,
)
from config import (
    GEMINI_MAX_RETRIES,
    GEMINI_RETRY_DELAYS,
    GEMINI_RETRY_DELAYS_QUOTA,
    GEMINI_WORKER_TIMEOUT,
    PORTFOLIO_COMPANIES,
    VERTEX_LOCATION,
    VERTEX_MODEL,
    VERTEX_PROJECT,
)
from utils.langfuse_trim import trim_content_for_analysis

# ── Wyciszenie ostrzeżeń parsera ───────────────────────────────────────────────
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)

# ── Konfiguracja Vertex AI ─────────────────────────────────────────────────────
_VERTEX_PROJECT   = VERTEX_PROJECT
_VERTEX_LOCATION  = VERTEX_LOCATION
_VERTEX_MODEL     = VERTEX_MODEL
_CREDENTIALS_FILE = Path(__file__).resolve().parent.parent / "vertex_credentials.json"

_MAX_RETRIES        = GEMINI_MAX_RETRIES         # z config.py
_RETRY_DELAYS       = GEMINI_RETRY_DELAYS        # ogólne błędy (timeout, 5xx)
_RETRY_DELAYS_QUOTA = GEMINI_RETRY_DELAYS_QUOTA  # 429 RESOURCE_EXHAUSTED


def _is_quota_error(exc: BaseException) -> bool:
    """True jeśli wyjątek to Vertex AI 429 RESOURCE_EXHAUSTED (rate limit).

    429 to transient — Vertex quota window odblokuje się za chwilę. Pipeline
    obsługuje skipem; nie logujemy ERROR (Sentry by mailował dla zwykłego
    rate limitu, który nie jest bugiem w naszym kodzie).
    """
    msg = str(exc).lower()
    return ("429" in msg) or ("resource exhausted" in msg) or ("rate limit" in msg)

_PORTFOLIO_TICKERS = {t.upper().replace(".PL", "").strip() for t in PORTFOLIO_COMPANIES}
# Skompilowane wzorce do dopasowania tickerów po granicy słowa
_PORTFOLIO_PATTERNS = [
    re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE)
    for t in _PORTFOLIO_TICKERS
]


_gemini_client = None

# ── Parent-side analysis cache pre-creation (fix 2026-04-23) ────────────────────
# PROBLEM: każdy subprocess worker tworzył NOWY CachedContent → 850+ duplikaty/dzień
# × storage fee = ~180 PLN/mies extra. FIX: parent pre-tworzy cache raz per proces,
# przekazuje nazwę workers przez input.json. Worker dodaje `cached_content=name`
# do per-call generation_config (genai SDK, Faza 5 cleanup 2026-04-27).


def _get_analysis_cache_name() -> str | None:
    """Zwraca nazwę pre-utworzonego analysis cache (parent-side singleton).

    Lazy: pierwsze wywołanie tworzy cache via genai SDK (`vertex_client._build_analysis_cache`);
    kolejne zwracają singleton. Graceful: None gdy build failed — worker wtedy
    fallbackuje do create własnego cache albo do non-cache promptu.
    """
    try:
        from agents.vertex_client import get_or_create_analysis_cache
        cache_obj = get_or_create_analysis_cache()
        if cache_obj is None:
            return None
        return getattr(cache_obj, "name", None)
    except Exception as e:
        logger.warning(f"Parent-side cache pre-creation failed: {e}")
        return None


def _get_client():
    """Singleton Gemini client dla XHTML repair — generation_config=None.

    UWAGA: świadoma duplikacja vs `vertex_client.get_gemini_client()`.
    Centralny vertex_client wymusza `response_mime_type="application/json"`
    co dla XHTML self-healing (`_repair_xhtml_with_gemini`) byłoby błędne —
    ta funkcja zwraca PLAIN TEXT, nie JSON. Pełny dedupe wymaga wyciągnięcia
    osobnego helpera `get_gemini_client_for_text()` w vertex_client (BACKLOG).

    Migracja USE_GENAI_SDK: documentation/VERTEX_AI_MIGRATION.md
    """
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    from google.oauth2 import service_account

    from agents._gemini_adapter import make_gemini_model

    creds = service_account.Credentials.from_service_account_file(
        str(_CREDENTIALS_FILE),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    _gemini_client = make_gemini_model(
        project=_VERTEX_PROJECT,
        location=_VERTEX_LOCATION,
        model_name=_VERTEX_MODEL,
        credentials=creds,
        generation_config=None,  # legacy behavior: brak eksplicit config (XHTML repair = text, NIE JSON)
    )
    return _gemini_client


def is_portfolio_company(company: str) -> bool:
    return any(pat.search(company) for pat in _PORTFOLIO_PATTERNS)


def _build_macro_section(macro_context: dict | None, date_str: str) -> str:
    """
    Buduje sekcję kontekstu makroekonomicznego do wstrzyknięcia w prompt.
    Jeśli brak danych makro — zwraca pusty string.
    """
    if not macro_context:
        return ""

    def fmt_val(d: dict, name: str) -> str:
        cena = d.get("cena", "?")
        zm   = d.get("zmiana_proc")
        kurs = d.get("kurs")
        val  = kurs if kurs else cena
        if zm is not None:
            return f"{name}={val} ({zm:+.1f}%)"
        return f"{name}={val}"

    def fmt_kurs(d: dict, name: str) -> str:
        kurs = d.get("kurs") or d.get("cena", "?")
        zm   = d.get("zmiana_proc")
        if zm is not None:
            return f"{name}={kurs} ({zm:+.1f}%)"
        return f"{name}={kurs}"

    indeksy = macro_context.get("indeksy", {})
    surowce = macro_context.get("surowce", {})
    waluty  = macro_context.get("waluty", {})
    makro   = macro_context.get("makro_pl", {})

    indeksy_str = " | ".join(
        fmt_val(v, k) for k, v in indeksy.items()
    ) or "brak danych"

    surowce_str = " | ".join(
        fmt_val(v, k) for k, v in surowce.items()
    ) or "brak danych"

    # Waluty — preferuj NBP kursy (bardziej oficjalne), pomiń duplikaty
    waluty_show = {}
    for k, v in waluty.items():
        base = k.replace("_NBP", "")
        if "_NBP" in k or base not in waluty_show:
            waluty_show[base] = v
    waluty_str = " | ".join(
        fmt_kurs(v, k) for k, v in waluty_show.items()
    ) or "brak danych"

    stopa_nbp = makro.get("stopy_procentowe", {}).get("stopa_referencyjna_nbp", "?")
    inflacja  = makro.get("inflacja", {}).get("inflacja_cpi_rdr", "?")

    return MACRO_CONTEXT_SECTION.format(
        date     = date_str,
        indeksy  = indeksy_str,
        surowce  = surowce_str,
        waluty   = waluty_str,
        stopa_nbp = stopa_nbp,
        inflacja  = inflacja,
    )



def _build_profile_section(company_profile: dict | None) -> str:
    """
    Buduje sekcję profilu spółki do wstrzyknięcia w prompt.
    Jeśli brak profilu — zwraca pusty string.
    """
    if not company_profile:
        return ""

    # BQ może zwracać nullable kolumny jako None (nie missing) — dict.get zwraca
    # wtedy None, nie default. Wszędzie używaj `or default` po .get().
    exp_wal  = company_profile.get("ekspozycja_walutowa") or {}
    exp_sur  = company_profile.get("wrazliwosc_na_surowce") or {}
    exp_stop = company_profile.get("wrazliwosc_na_stopy_procentowe") or {}

    return PROFILE_CONTEXT_SECTION.format(
        ticker          = company_profile.get("ticker") or "?",
        sektor          = company_profile.get("sektor") or "?",
        model_biznesowy = company_profile.get("model_biznesowy") or "?",
        waluty          = ", ".join(exp_wal.get("waluty_wazne") or []) or "brak",
        typ_ekspozycji  = exp_wal.get("typ_ekspozycji") or "?",
        surowce         = ", ".join(exp_sur.get("surowce_wazne") or []) or "brak",
        poziom_surowce  = exp_sur.get("poziom") or "?",
        poziom_stopy    = exp_stop.get("poziom") or "?",
        dywidendy       = company_profile.get("polityka_dywidendowa") or "brak danych",
        charakterystyka = (company_profile.get("charakterystyka_dla_analizy") or "")[:200],
    )


def analyze_announcement(
    file_info: dict,
    content_text: str,
    force_mode: str | None = None,
    macro_context: dict | None = None,
    company_profile: dict | None = None,
) -> dict | None:
    """
    Wywołuje Gemini przez Vertex AI i zwraca słownik z analizą ogłoszenia.

    file_info:      {title, company, date, source, ...}
    content_text:   treść ogłoszenia (max ~8000 znaków)
    force_mode:     None (auto) | "general" | "portfolio"
    macro_context:  dane makro z macro.py (opcjonalne)
    company_profile: profil spółki z profile.py (opcjonalne)
    """
    company  = file_info.get("company", "")
    date_str = str(file_info.get("date", ""))

    # Ustal tryb analizy
    if force_mode == "portfolio":
        mode = "portfolio"
    elif force_mode == "general":
        mode = "general"
    else:
        mode = "portfolio" if is_portfolio_company(company) else "general"

    # Smart trim treści: head + tail z zachowaniem krytycznych sekcji.
    # Long ESPI/EBI (sustainability, audit reports — 20K+ chars) miały krytyczne
    # info na końcu gubione przez naive [:15000].
    # - mode=general (~95% calls): 8K cap = ~50% redukcja dla long-form
    # - mode=portfolio (~5% calls, 8 spółek): 12K cap = głębsza analiza dla
    #   decyzji inwestycyjnych gdzie stawka = 2000 PLN capital per pozycja.
    #   Dodatkowy koszt: +~0.05 PLN/mies, jakość znacząco wyższa dla portfelowych.
    content_cap = 12_000 if mode == "portfolio" else 8_000
    content_text = trim_content_for_analysis(content_text, cap=content_cap)

    # Zbuduj sekcje kontekstu
    macro_section   = _build_macro_section(macro_context, date_str)
    profile_section = _build_profile_section(company_profile)

    # Wybierz template i sformatuj prompt.
    # CACHE OPTIMIZATION: dla mode='general' używamy split STATIC/DYNAMIC + Vertex
    # explicit cache (~12 PLN/mies oszczędność). Mode 'portfolio' rzadszy (~5%) →
    # zostaje na klasycznej ścieżce (full_prompt) bez cache.
    use_analysis_cache = (mode == "general")
    if use_analysis_cache:
        # Per-call prompt = TYLKO dynamic part (static + system są w cache)
        from agents.prompts import ANALYSIS_GENERAL_DYNAMIC
        full_prompt = ANALYSIS_GENERAL_DYNAMIC.format(
            title           = file_info.get("title", ""),
            company         = company,
            date            = date_str,
            source          = file_info.get("source", ""),
            content         = content_text,
            macro_section   = macro_section,
            profile_section = profile_section,
        )
    else:
        # Portfolio: full template (system + portfolio_template) bez cache
        full_prompt = (
            f"{ANALYSIS_SYSTEM}\n\n"
            + ANALYSIS_PORTFOLIO_TEMPLATE.format(
                title           = file_info.get("title", ""),
                company         = company,
                date            = date_str,
                source          = file_info.get("source", ""),
                content         = content_text,
                macro_section   = macro_section,
                profile_section = profile_section,
            )
        )

    # Wywołaj Gemini przez subprocess z twardym timeoutem z config (2026-04-23:
    # skrócony z 120s do GEMINI_WORKER_TIMEOUT=100s pod presję Vertex 429).
    # subprocess.run(timeout=...) killuje proces na poziomie OS — niezawodne.
    _WORKER_TIMEOUT = GEMINI_WORKER_TIMEOUT
    _WORKER_PATH    = Path(__file__).resolve().parent / "gemini_worker.py"

    # Pre-twórz cache RAZ w parent process (fix 2026-04-23) — worker reuse przez
    # nazwę. None → graceful fallback (worker sam stworzy, stare zachowanie).
    cache_name_for_worker: str | None = None
    if use_analysis_cache:
        cache_name_for_worker = _get_analysis_cache_name()

    raw = ""
    for attempt in range(_MAX_RETRIES):
        input_file  = None
        output_file = None
        try:
            # Zapisz prompt do pliku tymczasowego
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                             delete=False, encoding="utf-8") as f:
                input_file = f.name
                json.dump({
                    "prompt":           full_prompt,
                    "vertex_project":   _VERTEX_PROJECT,
                    "vertex_location":  _VERTEX_LOCATION,
                    "vertex_model":     _VERTEX_MODEL,
                    "credentials_file": str(_CREDENTIALS_FILE),
                    # thinking_budget=1024: cap "myślenia" Gemini 2.5 do 1024 tokens
                    # (vs ~3000 default). Dla analizy ogłoszeń wystarczy: model nadal
                    # robi solidną klasyfikację + ekstrakcję, tylko mniej "rozważa
                    # alternatywy". ~70% redukcja kosztu thinking dla agent=analysis
                    # (najczęściej wywoływany agent, ~700 calls/dzień).
                    "thinking_budget":  1024,
                    # use_analysis_cache: dla mode='general' worker uzywa
                    # GenerativeModel.from_cached_content() zamiast nowego klienta.
                    # ANALYSIS_SYSTEM + ANALYSIS_GENERAL_STATIC są w cache (~1862 tok),
                    # cached tokens kosztują 4x mniej ($0.075/1M vs $0.30/1M).
                    # Worker traktuje brak cache (None) gracefully — fallback do
                    # standardowego call, ale wtedy musi dostać FULL prompt nie dynamic.
                    "use_analysis_cache": use_analysis_cache,
                    # cache_name: nazwa pre-utworzonego cache (parent-side singleton).
                    # Worker WRAP przez CachedContent(name) — NIE tworzy nowego.
                    # Eliminuje storage duplikację (~180 PLN/mies). None → fallback.
                    "cache_name":       cache_name_for_worker,
                    # Langfuse metadata — forwardowane do trace w worker
                    "metadata": {
                        "agent":            "analysis",
                        "ticker":           company,
                        "mode":             mode,
                        "date":             date_str,
                        "source":           file_info.get("source", ""),
                        "content_length":   len(content_text),
                        "has_macro":        bool(macro_context),
                        "has_profile":      bool(company_profile),
                        "attempt":          attempt + 1,
                        "cache_used":       use_analysis_cache,
                    },
                }, f)

            # Plik wyjściowy
            output_fd, output_file = tempfile.mkstemp(suffix=".json")
            os.close(output_fd)

            # Uruchom worker z twardym timeoutem.
            # Wynik nie jest używany — worker zapisuje JSON do output_file,
            # a sukces weryfikuje json.loads() poniżej. .run() blokuje do końca.
            worker_result = subprocess.run(
                [sys.executable, str(_WORKER_PATH), input_file, output_file],
                timeout=_WORKER_TIMEOUT,
                capture_output=True,
                check=False,
            )

            # Forward worker stderr do Cloud Run logs (diagnostyka cache, etc).
            # Worker loguje [WARNING]/[INFO] do stderr, a capture_output=True
            # przechwytuje → bez tego forwardowania tracimy sygnały diagnostyczne.
            if worker_result.stderr:
                stderr_str = worker_result.stderr.decode("utf-8", errors="replace").strip()
                if stderr_str:
                    logger.info(f"Worker stderr ({company}): {stderr_str[:1500]}")

            # Wczytaj wynik
            output_data = json.loads(Path(output_file).read_text(encoding="utf-8"))

            if "error" in output_data:
                raise RuntimeError(output_data["error"])

            raw = output_data["raw"].strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            analysis = json.loads(raw)
            if not isinstance(analysis, dict):
                logger.error(f"Gemini zwrócił nie-dict JSON ({company}): {type(analysis).__name__}")
                return None

            # Pydantic schema validation — fail-fast jeśli Gemini pominął wymagane
            # pole lub zwrócił złą wartość enum. Bez tego błędy wyłaziły dopiero
            # u konsumentów (digest, broker_agent) z trudnym do diagnozy stack-tracem.
            from agents.analysis_schema import validate_analysis_dict
            analysis = validate_analysis_dict(analysis, company=company)
            if analysis is None:
                # Helper już zalogował szczegóły błędu walidacji
                return None

            analysis["_analysis_mode"]       = mode
            analysis["_has_macro_context"]   = bool(macro_context)
            analysis["_has_profile_context"] = bool(company_profile)
            logger.info(
                f"Analiza [{mode}"
                f"{'|makro' if macro_context else ''}"
                f"{'|profil' if company_profile else ''}"
                f"] OK: {company} — "
                f"sentiment: {analysis.get('sentiment')}, "
                f"waga: {analysis.get('waga_informacji')}"
            )
            return analysis

        except subprocess.TimeoutExpired:
            logger.warning(
                f"Gemini timeout ({company}), próba {attempt + 1}/{_MAX_RETRIES} "
                f"po {_WORKER_TIMEOUT}s. "
                f"{'Retry...' if attempt < _MAX_RETRIES - 1 else 'Pomijam plik.'}"
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(5)

        except json.JSONDecodeError as e:
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                logger.warning(
                    f"Błąd parsowania JSON z Gemini ({company}), próba {attempt + 1}/{_MAX_RETRIES}: {e}. "
                    f"Retry za {delay}s..."
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"Błąd parsowania JSON z Gemini ({company}) po {_MAX_RETRIES} próbach: {e}"
                    f"\nOdpowiedź: {raw[:300]}"
                )
                return None

        except Exception as e:
            is_quota = _is_quota_error(e)
            if attempt < _MAX_RETRIES - 1:
                # 429: dłuższy backoff (quota window potrzebuje czasu na odblokowanie).
                # Inne błędy (timeout, 5xx): krótki — fail-fast & skip.
                delays = _RETRY_DELAYS_QUOTA if is_quota else _RETRY_DELAYS
                delay = delays[min(attempt, len(delays) - 1)]
                logger.warning(
                    f"Błąd Vertex AI ({company}), próba {attempt + 1}/{_MAX_RETRIES}: {e}. "
                    f"Retry za {delay}s..."
                )
                time.sleep(delay)
            else:
                # 429 to transient (rate limit, nie bug) → WARNING, Sentry nie mailuje.
                # Inne błędy → ERROR, diagnostyczne.
                final_log = logger.warning if is_quota else logger.error
                final_log(f"Błąd Vertex AI ({company}) po {_MAX_RETRIES} próbach: {e}")
                return None

        finally:
            # Zawsze usuń pliki tymczasowe
            for f in [input_file, output_file]:
                if f:
                    try:
                        os.unlink(f)
                    except Exception:
                        pass

    logger.error(f"Gemini ({company}) — wszystkie {_MAX_RETRIES} próby nieudane")
    return None


def prepare_content_from_bytes(data: bytes, content_type: str) -> str:
    if content_type == "txt":
        return data if isinstance(data, str) else data.decode("utf-8", errors="replace")

    if content_type == "xhtml":
        return _extract_xhtml(data)

    if content_type == "pdf":
        return _extract_pdf(data)

    return ""


# ── CSS line pattern — wieloliniowe reguły pdf2htmlEX ────────────────────────
# Obecny problem: pdf2htmlEX generuje CSS jako osobne linie:
#   .t1{            ← otwierająca
#   position:absolute;  ← właściwość
#   top:100px;      ← właściwość
#   }               ← zamykająca
# Każda linia osobno NIE pasuje do prostego r"\.?\w+\{[^}]*\}" → CSS przelatuje.
_CSS_LINE = re.compile(
    r"^[\.\#][\w\-]+\s*\{?"           # .class{ lub #id{  (otwierająca)
    r"|^\s*@[\w\-]+"                   # @font-face, @media, @keyframes
    r"|^\s*[\w\-]+\s*:\s*[^;]*;\s*$"  # CSS property: value;  (właściwość)
    r"|^\s*\}\s*$"                     # samotne }
    r"|^\s*\{\s*$"                     # samotne {
    r"|\bposition\s*:\s*absolute\b"    # position:absolute (w każdym kontekście)
    r"|\bfont-family\s*:"              # font-family:
    r"|\bpdf2htmlEX\b"                 # nagłówki generatora
    r"|\bfont-size\s*:"                # font-size: (inline CSS residue)
    r"|\bline-height\s*:"              # line-height:
    r"|\bcolor\s*:\s*#[0-9a-fA-F]"    # color: #hex
    r"|\btransform\s*:"                # transform: (pdf2htmlEX positioning)
    r"|\bleft\s*:\s*\d+\.?\d*p[xt]"   # left: 100px / left: 10pt
    r"|\btop\s*:\s*\d+\.?\d*p[xt]"    # top: 100px / top: 10pt
    r"|\bwidth\s*:\s*\d+\.?\d*p[xt]"  # width: 100px
    r"|\bheight\s*:\s*\d+\.?\d*p[xt]" # height: 100px
    r"|\bbottom\s*:\s*\d+\.?\d*p[xt]" # bottom: 100px
    r"|\bright\s*:\s*\d+\.?\d*p[xt]"  # right: 100px
    r"|^\s*[a-f0-9]{6,}\s*$"          # samotne hex stringi (np. kolory)
)


def _is_garbage_line(line: str) -> bool:
    """Zwraca True jeśli linia to prawdopodobnie CSS/śmieć, nie tekst merytoryczny."""
    if _CSS_LINE.search(line):
        return True
    # Ratio testu: jeśli < 20% znaków alfanumerycznych → liczby/hex/CSS
    alpha = sum(c.isalpha() for c in line)
    if len(line) > 20 and alpha / len(line) < 0.20:
        return True
    # Linie wyglądające jak reszta inline CSS po strip tagów
    # np. "position:absolute; left:100px; top:200px;"
    css_props = sum(1 for _ in re.finditer(r'\w+\s*:\s*[\w#\.\-]+', line))
    if css_props >= 3 and alpha / max(len(line), 1) < 0.40:
        return True
    return False


def _repair_xhtml_with_gemini(partial_text: str) -> str:
    """
    Fallback self-healing: wysyła częściowo oczyszczony tekst XHTML do Gemini
    z prośbą o wyciągnięcie treści merytorycznej.

    Wywoływany gdy parser pdf2htmlEX zwraca < 100 znaków po filtrowaniu CSS.
    partial_text: linie po strip tagów (CSS bloki usunięte), max 8000 znaków.
    Zwraca czysty tekst lub "" gdy Gemini nie wyciągnie treści / rzuci wyjątek.

    Fix #6 (2026-04-22): observability — 1 retry na transient error +
    Sentry alert na finalny failure + Langfuse generation trace (gdy SDK
    aktywne). Wcześniej: jedno wywołanie, log.warning, brak alertu.
    """
    _REPAIR_MIN_INPUT  = 50
    _REPAIR_INPUT_LIMIT  = 8000
    _REPAIR_MIN_OUTPUT = 50
    _MAX_ATTEMPTS = 2  # 1 oryginalne + 1 retry

    if not partial_text or len(partial_text) < _REPAIR_MIN_INPUT:
        return ""

    from agents.vertex_client import _get_langfuse_client

    prompt = XHTML_REPAIR_TEMPLATE.format(content=partial_text[:_REPAIR_INPUT_LIMIT])
    langfuse = _get_langfuse_client()

    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            client = _get_client()
            if langfuse is not None:
                with langfuse.start_as_current_observation(
                    as_type="generation",
                    name="xhtml_repair_call",
                    model=_VERTEX_MODEL,
                    input=prompt[:2000],
                    metadata={"agent": "xhtml_self_healing", "attempt": attempt + 1},
                ) as gen:
                    response = client.generate_content(prompt)
                    repaired = response.text.strip() if response.text else ""
                    gen.update(output=repaired[:500] if repaired else "(empty)")
            else:
                response = client.generate_content(prompt)
                repaired = response.text.strip() if response.text else ""

            if len(repaired) < _REPAIR_MIN_OUTPUT:
                return ""
            logger.info(
                f"XHTML self-healing: naprawiono {len(repaired)} znaków przez Gemini "
                f"(próba {attempt + 1}/{_MAX_ATTEMPTS})"
            )
            return repaired[:15000]
        except Exception as e:
            last_exc = e
            if attempt < _MAX_ATTEMPTS - 1:
                logger.warning(
                    f"XHTML self-healing: próba {attempt + 1}/{_MAX_ATTEMPTS} "
                    f"nieudana ({e}), retry..."
                )
                continue
            # Final failure → Sentry + log
            logger.warning(
                f"XHTML self-healing: Gemini repair nieudany po {_MAX_ATTEMPTS} próbach: {e}"
            )
            try:
                sentry_sdk.capture_exception(last_exc)
            except Exception as se:
                logger.warning(f"Sentry capture failed: {se}")
            return ""
    return ""


def _extract_xhtml(data: bytes) -> str:
    """
    Ekstrakcja tekstu z plików XHTML (w tym pdf2htmlEX).

    Pliki pdf2htmlEX (np. raporty roczne NTT, TOYA) mają 400 KB – 8 MB.
    Pierwsze 2 KB to wyłącznie <style> block z CSS — body zaczyna się dalej.

    Strategia:
    - pdf2htmlEX → regex usunięcia <style>/<head>/<script> + strip tagów.
      Regex jest O(n) i działa na całym pliku niezależnie od rozmiaru.
      BeautifulSoup na 8 MB z html5lib byłby zbyt wolny.
    - Zwykły XHTML (bez pdf2htmlEX) → BeautifulSoup + get_text().
    """
    # Wykryj pdf2htmlEX — generator zostawia ślad w pierwszych ~500 bajtach
    raw_prefix = data[:4000].decode("utf-8", errors="ignore")
    is_pdf2html = "pdf2htmlEX" in raw_prefix

    if is_pdf2html:
        # ── Szybka ścieżka regex dla dużych plików pdf2htmlEX ────────────────
        html_str = data.decode("utf-8", errors="ignore")

        # 1. Usuń bloki <style>...</style> (CSS zajmuje nawet 300 KB)
        html_str = re.sub(
            r"<style[^>]*>.*?</style>", " ", html_str,
            flags=re.DOTALL | re.IGNORECASE
        )
        # 2. Usuń <script>, <head>, komentarze HTML
        html_str = re.sub(
            r"<script[^>]*>.*?</script>", " ", html_str,
            flags=re.DOTALL | re.IGNORECASE
        )
        html_str = re.sub(
            r"<head[^>]*>.*?</head>", " ", html_str,
            flags=re.DOTALL | re.IGNORECASE
        )
        html_str = re.sub(r"<!--.*?-->", " ", html_str, flags=re.DOTALL)

        # 3. Usuń atrybuty style= z tagów ZANIM je stripniesz
        #    (bez tego "style=position:absolute;left:100px" wycieknie jako tekst)
        html_str = re.sub(r'\s+style="[^"]*"', '', html_str, flags=re.IGNORECASE)
        html_str = re.sub(r"\s+style='[^']*'", '', html_str, flags=re.IGNORECASE)

        # 4. Zamień pozostałe tagi na spację (zachowaj tekst węzłów)
        html_str = re.sub(r"<[^>]+>", " ", html_str)

        # 5. Zdekoduj encje HTML (&amp; &nbsp; itp.)
        import html as _html
        html_str = _html.unescape(html_str)

        # 6. Normalizuj białe znaki
        lines = [
            ln.strip()
            for ln in re.split(r"[\n\r]+", re.sub(r"[ \t]+", " ", html_str))
            if ln.strip()
        ]

    else:
        # ── Standardowy parser BeautifulSoup dla mniejszych plików ───────────
        try:
            from bs4 import BeautifulSoup
            try:
                soup = BeautifulSoup(data, "html5lib")
            except Exception:
                soup = BeautifulSoup(data, "html.parser")
            for tag in soup.find_all(["style", "script", "head", "noscript", "link"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        except Exception as e:
            logger.warning(f"Błąd parsowania XHTML: {e}")
            return ""

    # ── Filtrowanie śmieciowych linii CSS (defense in depth) ─────────────────
    # partial_text przechwycony przed filtrem — używany przez self-healing repair
    partial_text = "\n".join(lines)

    clean = [ln for ln in lines if len(ln) >= 3 and not _is_garbage_line(ln)]
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(clean))

    if len(result) < 100:
        logger.warning("XHTML: brak treści merytorycznej po filtrowaniu CSS")
        if is_pdf2html:
            logger.info("XHTML self-healing: próba naprawy przez Gemini")
            repaired = _repair_xhtml_with_gemini(partial_text)
            if repaired:
                return repaired
        return ""

    return result[:15000]


def _extract_pdf(data: bytes) -> str:
    """
    Ekstrakcja tekstu z PDF z mądrym limitem stron.

    Dla krótkich PDF (≤ 15 stron): czytamy wszystko.
    Dla długich (> 15 stron — np. 58-stronicowe raporty roczne TOYA):
      - Pierwsze 8 stron (omówienie zarządu, kluczowe dane)
      - Ostatnie 4 strony (często tabele finansowe / wybrane wskaźniki)
      - Adnotacja ile stron pominięto
    Limit znaków: 12 000 (Gemini 2.5 Flash — 1M tokenów, 12k ≈ 3k tokenów).
    """
    import io
    import warnings as _warnings
    _warnings.filterwarnings("ignore", category=UserWarning, module="pypdf")

    try:
        from pypdf import PdfReader
    except ImportError:
        logger.info("pypdf nie zainstalowane — analiza PDF ograniczona")
        return f"[Plik PDF — {len(data)} bajtów]"

    try:
        reader    = PdfReader(io.BytesIO(data))
        n_pages   = len(reader.pages)
        MAX_SHORT = 15   # próg "długi dokument"
        HEAD_PAGES = 8   # pierwsze strony
        TAIL_PAGES = 4   # ostatnie strony

        if n_pages <= MAX_SHORT:
            pages_text = [reader.pages[i].extract_text() or "" for i in range(n_pages)]
            note = ""
        else:
            head = [reader.pages[i].extract_text() or "" for i in range(HEAD_PAGES)]
            tail_start = max(HEAD_PAGES, n_pages - TAIL_PAGES)
            tail = [reader.pages[i].extract_text() or "" for i in range(tail_start, n_pages)]
            skipped = tail_start - HEAD_PAGES
            note = f"\n\n[--- Pominięto {skipped} stron (str. {HEAD_PAGES+1}–{tail_start}) ---]\n\n"
            pages_text = head + [note] + tail
            logger.info(
                f"PDF {n_pages} stron → czytam {HEAD_PAGES} + {len(tail)} "
                f"(pominięto {skipped} środkowych)"
            )

        return "\n".join(pages_text)[:12000]

    except Exception as e:
        logger.warning(f"Błąd wyciągania tekstu z PDF: {e}")
        return ""