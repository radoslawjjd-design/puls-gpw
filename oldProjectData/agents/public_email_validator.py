"""
Walidator PUBLIC emaili — sprawdza neutralność treści przed wysyłką.

Flow:
  validate_public_email(html, email_type) → PublicEmailValidation
    1. Regex check na zabronione frazy sentymentowe — szybki, bez AI
    2. Gemini supervisor — głębsza ocena neutralności (score 1-10)

Progi:
  Regex fail  → passed=False (Gemini nie wywoływany)
  score > 6   → passed=True
  score ≤ 6   → passed=False → alert email

Używany w: summarize.py, digest.py (przed wysyłką PUBLIC emaila)
"""
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Zabronione frazy sentymentowe ────────────────────────────────────────────
# Kontekst: PUBLIC emaile muszą być neutralne — czyste fakty, zero ocen.
# Regex matchuje w stripped HTML (plain text).

_FORBIDDEN_PATTERNS = [
    r"dla inwestor[óo]w",
    r"sygna[łl]\w*\s+(dla|o|że)",         # "sygnał dla...", "sygnałem o..."
    r"pozytywn\w+\s+(sygna|perspektyw|wynik|odbi[oe]r|wpływ|zmian|tend|ocen)",
    r"negatywn\w+\s+(sygna|perspektyw|wynik|odbi[oe]r|wpływ|zmian|tend|ocen)",
    r"rekomendacj\w+\s+(kupn|sprzeda|działan)",  # ale nie "rekomendacji inwestycyjnej" (disclaimer)
    r"warto\s+(kupić|sprzedać|obserwować|rozważyć)",
    r"okazj[aę]\s+inwestycyjn",
    r"wzrost\w*\s+kursu",
    r"spadk\w*\s+kursu",
    r"sentyment\w*",
    r"byc[zi]\w+",
    r"niedźwiedz\w+",
    r"hoss[aęy]",
    r"bess[aęy]",
    r"perspektyw\w+\s+wzrost\w*",
    r"potencja[łl]\w+\s+wzrost\w*",
    r"ryzyk\w+\s+p[łl]ynno[śs]ciow\w*",
    r"obaw\w+\s+o\s+stabilno[śs]",
    r"presj[aęą]\s+(koszt|cen|spadk)",
    r"rozwa[żz]\w*\s+(zwiększeni|zmniejszeni|pozycj)",
]

_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_PATTERNS), re.IGNORECASE)

# Strip HTML tags do plain text
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")


# ── Gemini prompt ────────────────────────────────────────────────────────────

_VALIDATOR_SYSTEM = """Jesteś supervisorem jakości PUBLIC emaili finansowych GPW.
Oceniasz czy email jest NEUTRALNY — czy zawiera wyłącznie fakty bez ocen, sentymentu, rekomendacji.
Zwracasz WYŁĄCZNIE poprawny JSON bez komentarzy."""

_VALIDATOR_TEMPLATE = """
=== EMAIL DO OCENY ===
Typ: {email_type}

{plain_text}

=== KRYTERIA OCENY NEUTRALNOŚCI (skala 1–10) ===

1. BRAK SENTYMENTU (waga 40%)
   - Czy email zawiera słowa wartościujące: "pozytywny", "negatywny", "dobry", "zły"?
   - Czy opisy zdarzeń są czysto faktyczne bez interpretacji?
   - "Spółka podpisała kontrakt 30 mln EUR" = dobrze
   - "Spółka podpisała kontrakt co jest pozytywnym sygnałem" = źle

2. BRAK REKOMENDACJI INWESTYCYJNYCH (waga 30%)
   - Czy email sugeruje kupno/sprzedaż/obserwowanie?
   - "rozważ zwiększenie pozycji", "warto kupić" → FAIL
   - Nazwy korporacyjne OK: "rekomenduje wypłatę dywidendy" = dozwolone

3. BRAK INFORMACJI PORTFELOWYCH (waga 20%)
   - Czy email zawiera "portfel", "rekomendacja", sentyment score?
   - Brak konkretnych akcji przypisanych do portfela

4. NEUTRALNOŚĆ JĘZYKA (waga 10%)
   - Czy opisy są pozbawione emocji i prognoz?
   - "zmniejszona strata" = fakt = OK
   - "świadczy o silnej kondycji" = ocena = źle
   - "otwiera drogę do dalszego rozwoju" = prognoza = źle
   - "niepewne perspektywy" = ocena = źle

=== PROCEDURA OCENY ===
Oceń KAŻDE kryterium osobno w skali 0-10, pomnóż przez wagę, zsumuj:
- Kryterium 1 (×0.4): [ocena] × 0.4 = [wynik]
- Kryterium 2 (×0.3): [ocena] × 0.3 = [wynik]
- Kryterium 3 (×0.2): [ocena] × 0.2 = [wynik]
- Kryterium 4 (×0.1): [ocena] × 0.1 = [wynik]
- SUMA = score (zaokrąglij do int)

Progi:
- score 8-10: email jest neutralny, czyste fakty
- score 7:    drobne sugestie ale akceptowalny
- score 5-6:  email zawiera ukryty sentyment lub oceny
- score 1-4:  jawne rekomendacje lub sentyment

JSON:
{{
  "score": <int 1-10>,
  "uzasadnienie": "<krótka ocena>",
  "problemy": ["<problem 1>", "<problem 2>"],
  "sugestie": "<co zmienić>"
}}"""


# ── Wynik walidacji ──────────────────────────────────────────────────────────

@dataclass
class PublicEmailValidation:
    score: int
    passed: bool
    forbidden_found: list[str] = field(default_factory=list)
    problemy: list[str] = field(default_factory=list)
    sugestie: str = ""
    uzasadnienie: str = ""
    email_type: str = ""
    validator_fallback: bool = False


# ── Regex check ──────────────────────────────────────────────────────────────

def check_forbidden_phrases(html: str) -> list[str]:
    """
    Sprawdza HTML za zabronione frazy sentymentowe.
    Zwraca listę znalezionych fraz (puste = OK).
    """
    plain = _STRIP_TAGS_RE.sub(" ", html)
    plain = re.sub(r"\s+", " ", plain)
    return [m.group() for m in _FORBIDDEN_RE.finditer(plain)]


# ── Główna walidacja ─────────────────────────────────────────────────────────

def validate_public_email(html: str, email_type: str) -> PublicEmailValidation:
    """
    Waliduje PUBLIC email: regex + Gemini supervisor.

    Args:
        html:       wyrenderowany HTML emaila
        email_type: "podsumowanie" | "digest" | "macro" | "xpost_preview"

    Returns:
        PublicEmailValidation
    """
    from agents.vertex_client import call_gemini_json

    # ── 1. Regex check ───────────────────────────────────────────────────────
    forbidden = check_forbidden_phrases(html)
    if forbidden:
        logger.warning(
            f"PUBLIC {email_type}: REGEX FAIL — znaleziono {len(forbidden)} "
            f"zabronionych fraz: {forbidden[:5]}"
        )
        return PublicEmailValidation(
            score=0,
            passed=False,
            forbidden_found=forbidden,
            problemy=[f"Zabroniona fraza: '{f}'" for f in forbidden],
            sugestie="Usuń frazy sentymentowe z szablonu PUBLIC emaila.",
            email_type=email_type,
        )

    # ── 2. Gemini supervisor ─────────────────────────────────────────────────
    plain_text = _STRIP_TAGS_RE.sub(" ", html)
    plain_text = re.sub(r"\s+", " ", plain_text).strip()

    prompt = _VALIDATOR_SYSTEM + "\n\n" + _VALIDATOR_TEMPLATE.format(
        email_type=email_type,
        plain_text=plain_text[:3000],
    )

    try:
        raw = call_gemini_json(
            prompt,
            max_retries=1,
            metadata={
                "agent":      "public_validator",
                "email_type": email_type,
            },
            # Phase 4 re-enabled 2026-04-23: thinking_budget=768 (50% bufor vs 512).
            # model_override=Flash-Lite: 3-6x taniej dla validation tasks.
            # max_output_tokens=8192: Flash-Lite lower thinking ratio, validator visible
            # output ~500 tok + thinking capped 768 = 8K safe.
            thinking_budget=768,
            model_override="gemini-2.5-flash-lite",
            max_output_tokens=8192,
        )
        if not raw or "score" not in raw:
            raise ValueError("Brak pola 'score' w odpowiedzi Gemini")

        score = max(1, min(10, int(raw.get("score", 5))))
        passed = score > 6

        result = PublicEmailValidation(
            score=score,
            passed=passed,
            uzasadnienie=str(raw.get("uzasadnienie", "")),
            problemy=[str(p) for p in raw.get("problemy", [])],
            sugestie=str(raw.get("sugestie", "")),
            email_type=email_type,
        )

        logger.info(
            f"PUBLIC {email_type} walidacja: score={score}/10 → "
            f"{'✓ OK' if passed else '✗ FAIL'} | {result.uzasadnienie[:80]}"
        )
        return result

    except Exception as e:
        # PR#11 CRITICAL #3 fix (2026-04-20): fail-CLOSED zamiast fail-open.
        # Wcześniej passed=True przy Gemini error → PUBLIC mailing omijał całkowicie
        # compliance (defense-in-depth bypass). Teraz blokujemy + alert do Sentry.
        logger.error(f"Błąd walidatora Gemini PUBLIC email: {e}", exc_info=True)
        logger.warning(
            "⚠️ Walidacja Gemini niedostępna — PUBLIC email ZABLOKOWANY (fail-closed)"
        )
        try:
            import sentry_sdk
            sentry_sdk.capture_message(
                f"public_email_validator FAIL-CLOSED: Gemini niedostępny ({e}). "
                f"PUBLIC email type={email_type} został zablokowany. "
                f"Sprawdź Vertex AI quota / network / credentials.",
                level="error",
            )
        except ImportError:
            pass

        return PublicEmailValidation(
            score=0,
            passed=False,
            email_type=email_type,
            uzasadnienie=f"Walidator niedostępny ({e}) — PUBLIC email ZABLOKOWANY (fail-closed)",
            validator_fallback=True,
        )
