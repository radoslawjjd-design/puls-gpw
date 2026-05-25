"""
Langfuse utilities — smart trim + Cloud Run release tag.

Smart trim: Hobby tier Langfuse dopuszcza ~128K chars per observation,
ale stary wrapper truncował do `prompt[:10000]` co gubiło tail długich
promptów (summarize weekly ~60-80K). Debugowanie zawsze widziało tylko
początek i mijało końcowe sekcje (np. sektory pre-built, instrukcje JSON).

Release tag: Cloud Run Jobs nie mają K_REVISION (to env var Services).
Zamiast tego używamy CLOUD_RUN_JOB / CLOUD_RUN_EXECUTION.
"""
from __future__ import annotations

MAX_TRACE_CHARS = 50_000
_TRUNCATION_MARKER = "\n\n[...TRUNCATED...]\n\n"


def trim_prompt_for_trace(prompt: str | None) -> str:
    """Zwraca prompt przycięty do MAX_TRACE_CHARS z zachowaniem head + tail.

    - prompt <= MAX_TRACE_CHARS → zwracany bez zmian
    - prompt > MAX_TRACE_CHARS → head[:N/2] + marker + tail[-N/2:]
    - None / "" → "" (defensywnie)
    """
    if not prompt:
        return ""

    if len(prompt) <= MAX_TRACE_CHARS:
        return prompt

    half = MAX_TRACE_CHARS // 2
    return prompt[:half] + _TRUNCATION_MARKER + prompt[-half:]


# ── Smart trim dla CONTENT analysis (krytyczny dla kosztu Gemini) ────────────

_CONTENT_TRUNCATION_MARKER = "\n\n[treść skrócona — pominięto środkowy fragment]\n\n"
DEFAULT_CONTENT_CAP = 8_000


def trim_content_for_analysis(text: str | None, cap: int = DEFAULT_CONTENT_CAP) -> str:
    """Smart trim treści ogłoszenia dla Gemini analysis prompt.

    Cel: zmniejszyć token cost long-form PDF (sustainability, audit reports
    20K+ chars) zachowując krytyczne info z początku I końca dokumentu.

    Strategia:
      - text <= cap → bez zmian
      - text > cap → head[:cap/2] + marker + tail[-cap/2:]

    Tail jest kluczowy bo długie ESPI/EBI często mają wnioski/podsumowania
    finansowe na końcu (po procedurach formalnych w środku). Naive truncation
    [:cap] gubił te ostatnie sekcje.

    Kontrast z trim_prompt_for_trace: ten cap jest na CONTENT (~3K tokens
    Gemini input), tamten na całym promcie do trace observability (~50K).
    """
    if not text:
        return ""

    if len(text) <= cap:
        return text

    half = cap // 2
    return text[:half] + _CONTENT_TRUNCATION_MARKER + text[-half:]


def get_cloud_run_release() -> str:
    """Zwraca release tag odpowiedni dla aktualnego środowiska Cloud Run.

    Cloud Run **Services** → K_REVISION (np. "my-service-00042-abc")
    Cloud Run **Jobs** → CLOUD_RUN_JOB (np. "prod-main-job")
    Lokalnie → "local"

    K_REVISION ma priorytet (Services), potem CLOUD_RUN_JOB (Jobs),
    fallback "local".
    """
    import os

    k_revision = os.environ.get("K_REVISION")
    if k_revision:
        return k_revision

    cloud_run_job = os.environ.get("CLOUD_RUN_JOB")
    if cloud_run_job:
        return cloud_run_job

    return "local"
