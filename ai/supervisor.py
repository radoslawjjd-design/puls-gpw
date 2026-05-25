"""
Supervisor posta X — dwustopniowa walidacja:
  1. Techniczne checks (szybko, bez Gemini)
  2. Ocena Gemini (score 1–10, pass ≥ 6)

Jeśli Gemini niedostępny → fallback pass z score=7 (nie blokujemy pipeline).
Max GEMINI_MAX_RETRIES prób wygenerowania posta przed poddaniem się.
"""
import json
import logging
import re
from dataclasses import dataclass, field

from ai import client as gemini
from ai.prompts import SUPERVISOR_SYSTEM, SUPERVISOR_TEMPLATE
from config import GEMINI_MAX_RETRIES

logger = logging.getLogger(__name__)

_MIN_POST_LEN  = 40
_MAX_POST_LEN  = 320  # z marginesem na zastrzeżenie
_PASS_SCORE    = 6

_FORBIDDEN_PATTERNS = [
    re.compile(r"\bprawdopodobn\w+\b", re.IGNORECASE),
    re.compile(r"\bmożliw\w+\b", re.IGNORECASE),
    re.compile(r"\bspekuluj\w+\b", re.IGNORECASE),
    re.compile(r"\bprognozu\w+\b", re.IGNORECASE),
    re.compile(r"\bwg\s+mnie\b", re.IGNORECASE),
    re.compile(r"\bmoim\s+zdaniem\b", re.IGNORECASE),
]


@dataclass
class SupervisorResult:
    score:       int
    passed:      bool
    problems:    list[str] = field(default_factory=list)
    suggestions: str       = ""


def validate(xpost: str, ann: dict, content: str) -> SupervisorResult:
    """
    Waliduje xpost. Zwraca SupervisorResult z oceną.
    Nigdy nie rzuca wyjątku — błąd Gemini → fallback pass.
    """
    # Etap 1: techniczne checks
    tech_result = _technical_check(xpost)
    if not tech_result.passed:
        return tech_result

    # Etap 2: Gemini supervisor
    return _gemini_review(xpost, ann, content)


def _technical_check(xpost: str) -> SupervisorResult:
    problems = []

    if len(xpost) < _MIN_POST_LEN:
        problems.append(f"Post za krótki ({len(xpost)} znaków, min {_MIN_POST_LEN})")
    if len(xpost) > _MAX_POST_LEN:
        problems.append(f"Post za długi ({len(xpost)} znaków, max {_MAX_POST_LEN})")
    if not re.search(r'^\$[A-Z]{2,6}', xpost.strip()):
        problems.append("Post nie zaczyna się od $TICKER")
    if "#GPW" not in xpost:
        problems.append("Brak hashtagu #GPW")
    if "rekomendacji" not in xpost.lower():
        problems.append("Brak zastrzeżenia o rekomendacji")
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(xpost):
            problems.append(f"Zakazane słowo: {pattern.pattern}")
            break

    if problems:
        return SupervisorResult(
            score       = 3,
            passed      = False,
            problems    = problems,
            suggestions = "Napraw problemy techniczne: " + "; ".join(problems),
        )
    return SupervisorResult(score=7, passed=True)


def _gemini_review(xpost: str, ann: dict, content: str) -> SupervisorResult:
    prompt = SUPERVISOR_TEMPLATE.format(
        xpost           = xpost,
        company         = ann.get("company", ""),
        ticker          = ann.get("company", ""),
        title           = ann.get("title", ""),
        content_snippet = content[:800],
    )

    raw = gemini.generate(prompt, system=SUPERVISOR_SYSTEM, temperature=0.1, max_tokens=300)

    if raw is None:
        logger.warning("Supervisor: Gemini niedostępny — fallback pass score=7")
        return SupervisorResult(score=7, passed=True, suggestions="")

    parsed = _parse_supervisor_json(raw)
    if parsed is None:
        logger.warning(f"Supervisor: nie udało się sparsować JSON: {raw[:200]!r}")
        return SupervisorResult(score=7, passed=True, suggestions="")

    score    = max(1, min(10, int(parsed.get("score", 5))))
    problems = parsed.get("problemy", [])
    sugg     = parsed.get("sugestie", "")

    return SupervisorResult(
        score       = score,
        passed      = score >= _PASS_SCORE,
        problems    = problems if isinstance(problems, list) else [],
        suggestions = sugg or "",
    )


def _parse_supervisor_json(raw: str) -> dict | None:
    raw = raw.strip()
    # Gemini czasem owija JSON w ```json ... ``` — odtłuszczamy
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


def generate_with_supervisor(ann: dict, content: str) -> tuple[str | None, int | None]:
    """
    Generuje X-post z pętlą supervisora (max GEMINI_MAX_RETRIES prób).

    Zwraca (xpost, score) lub (None, None) gdy wszystkie próby nieudane.
    """
    from ai.prompts import XPOST_SYSTEM, XPOST_TEMPLATE

    suggestions_context = ""

    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        prompt = XPOST_TEMPLATE.format(
            company = ann.get("company", ""),
            ticker  = ann.get("company", ""),
            title   = ann.get("title", ""),
            content = content[:6000],
        )
        if suggestions_context:
            prompt += f"\n\nUWAGI SUPERVISORA DO POPRZEDNIEJ WERSJI:\n{suggestions_context}\nUWZGLĘDNIJ JE."

        xpost = gemini.generate(prompt, system=XPOST_SYSTEM, temperature=0.5, max_tokens=350)

        if xpost is None:
            logger.warning(f"Generowanie posta: Gemini niedostępny (próba {attempt}/{GEMINI_MAX_RETRIES})")
            continue

        xpost = xpost.strip()
        result = validate(xpost, ann, content)

        logger.info(
            f"Supervisor próba {attempt}/{GEMINI_MAX_RETRIES}: "
            f"score={result.score}, passed={result.passed}, "
            f"company={ann.get('company')}"
        )

        if result.passed:
            return xpost, result.score

        suggestions_context = result.suggestions
        if result.problems:
            logger.info(f"  Problemy: {'; '.join(result.problems)}")

    logger.warning(
        f"Supervisor: wszystkie {GEMINI_MAX_RETRIES} próby nieudane dla "
        f"{ann.get('company')} — {ann.get('title', '')[:60]}"
    )
    return None, None
