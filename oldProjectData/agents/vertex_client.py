"""
Współdzielony klient Vertex AI (Gemini) — singleton.
Jeden punkt inicjalizacji zamiast duplikacji w każdym agencie.

Użycie:
    from agents.vertex_client import get_gemini_client, call_gemini_json
"""
import atexit
import json
import logging
import os
import threading
from pathlib import Path

from config import IS_CLOUD, PROJECT_ID, VERTEX_LOCATION, VERTEX_MODEL, VERTEX_PROJECT
from utils.langfuse_trim import get_cloud_run_release, trim_prompt_for_trace

logger = logging.getLogger(__name__)

_CREDENTIALS_FILE = Path(__file__).resolve().parent.parent / "vertex_credentials.json"

_client      = None
_client_lock = threading.Lock()

# ── Langfuse (LLM observability) — lazy singleton ────────────────────────────
# None = not yet initialized, False = checked and unavailable, client = ready.
_langfuse_client      = None
_langfuse_client_lock = threading.Lock()


def _get_langfuse_client():
    """
    Singleton Langfuse client — lazy, thread-safe, graceful.

    Returns:
        Langfuse client instance gdy env vars są ustawione i SDK jest
        zainstalowany; None w każdym innym przypadku (brak env vars,
        ImportError, crash przy init — Langfuse cicho wyłączony).

    Env vars ustawiane przez bootstrap._init_langfuse() (Cloud Run) albo
    przez .env / manual export (lokalnie).
    """
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client if _langfuse_client is not False else None

    with _langfuse_client_lock:
        if _langfuse_client is not None:
            return _langfuse_client if _langfuse_client is not False else None

        public = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret = os.environ.get("LANGFUSE_SECRET_KEY")
        host   = os.environ.get("LANGFUSE_HOST")

        if not (public and secret and host):
            _langfuse_client = False  # "checked, unavailable" marker
            return None

        try:
            from langfuse import Langfuse
        except ImportError:
            logger.warning("Langfuse: SDK not installed — tracing disabled")
            _langfuse_client = False
            return None

        try:
            _langfuse_client = Langfuse(
                public_key=public,
                secret_key=secret,
                host=host,
                environment=os.environ.get("LANGFUSE_ENVIRONMENT", "local"),
                release=get_cloud_run_release(),
            )
            # Cloud Run Jobs są krótkożyjące — ostatni batch trace'ów musi
            # zdążyć wyjść przed container shutdown. atexit czeka aż kolejka
            # wewnętrzna Langfuse SDK się opróżni (~sekundy).
            def _langfuse_shutdown():
                try:
                    _langfuse_client.shutdown()
                except Exception as exc:
                    logger.warning(f"Langfuse shutdown failed: {exc}")
            atexit.register(_langfuse_shutdown)
            logger.info(f"Langfuse client initialized (host={host})")
            return _langfuse_client
        except Exception as e:
            logger.warning(f"Langfuse client init failed — tracing disabled: {e}")
            _langfuse_client = False
            return None


def get_gemini_client():
    """
    Singleton Gemini client — inicjalizuje Vertex AI raz.
    Thread-safe (lock na inicjalizację).

    Zwracany obiekt eksponuje `.generate_content(prompt) -> response` — może
    to być legacy `vertexai.generative_models.GenerativeModel` albo adapter
    shim wokół nowego `google-genai` SDK. Decyzja sterowana `USE_GENAI_SDK`
    w config (env var). Migracja: documentation/VERTEX_AI_MIGRATION.md
    """
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client

        from google.oauth2 import service_account

        from agents._gemini_adapter import make_gemini_model

        if IS_CLOUD:
            from google.cloud import secretmanager
            sm_client = secretmanager.SecretManagerServiceClient()
            name      = f"projects/{PROJECT_ID}/secrets/vertex-credentials/versions/latest"
            response  = sm_client.access_secret_version(request={"name": name})
            sa_info   = json.loads(response.payload.data.decode("utf-8"))
            creds     = service_account.Credentials.from_service_account_info(
                sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        else:
            creds = service_account.Credentials.from_service_account_file(
                str(_CREDENTIALS_FILE),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )

        _client = make_gemini_model(
            project=VERTEX_PROJECT,
            location=VERTEX_LOCATION,
            model_name=VERTEX_MODEL,
            credentials=creds,
            generation_config={
                "temperature": 0.2,
                "max_output_tokens": 65535,   # Gemini 2.5 Flash max output
                "response_mime_type": "application/json",
            },
        )
        return _client


# ── Per-model client cache (Flash-Lite, Pro itp. dla wybranych agentów) ──────

_clients_by_model: dict = {}
_clients_by_model_lock = threading.Lock()


def _build_client_for_model(model_name: str):
    """Buduje nowy klient dla podanego modelu — bez cache, bez singleton.

    Używane przez get_gemini_client_for_model(); wyodrębnione dla testów
    (mockowalne). Replikuje credentials loading z get_gemini_client().
    """
    from google.oauth2 import service_account

    from agents._gemini_adapter import make_gemini_model

    if IS_CLOUD:
        from google.cloud import secretmanager
        sm_client = secretmanager.SecretManagerServiceClient()
        name      = f"projects/{PROJECT_ID}/secrets/vertex-credentials/versions/latest"
        response  = sm_client.access_secret_version(request={"name": name})
        sa_info   = json.loads(response.payload.data.decode("utf-8"))
        creds     = service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            str(_CREDENTIALS_FILE),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

    return make_gemini_model(
        project=VERTEX_PROJECT,
        location=VERTEX_LOCATION,
        model_name=model_name,
        credentials=creds,
        generation_config={
            "temperature": 0.2,
            "max_output_tokens": 65535,
            "response_mime_type": "application/json",
        },
    )


# ── Explicit prompt cache dla analysis (Vertex CachedContent) ────────────────
# ANALYSIS_SYSTEM (~1862 tokens) jest identyczny dla wszystkich ~700 analysis calls
# dziennie. Cached tokens kosztują 4x mniej ($0.075/1M vs $0.30/1M). Singleton
# per proces — TTL 1h, lazy rebuild gdy expire/error.

_analysis_cache = None
_analysis_cache_lock = threading.Lock()
_ANALYSIS_CACHE_TTL_MINUTES = 60  # default 1h, balance entre cache hit i refresh cost


def _build_analysis_cache():
    """Tworzy nowy CachedContent dla ANALYSIS_SYSTEM + STATIC template.

    Faza 5 cleanup 2026-04-27: migracja na google-genai SDK
    (`client.caches.create` + `CreateCachedContentConfig`). Stary `vertexai.preview.caching`
    usunięty. Per-call wysyłamy DYNAMIC część przez generate_content z
    `cached_content=cache.name` w GenerateContentConfig.

    Wyodrębnione żeby testy mogły mockować bez uderzania Vertex API.
    """
    from google.genai import types as genai_types
    from google.oauth2 import service_account

    from agents._gemini_adapter import _build_genai_client
    from agents.prompts import ANALYSIS_GENERAL_STATIC, ANALYSIS_SYSTEM

    if IS_CLOUD:
        from google.cloud import secretmanager
        sm_client = secretmanager.SecretManagerServiceClient()
        name      = f"projects/{PROJECT_ID}/secrets/vertex-credentials/versions/latest"
        response  = sm_client.access_secret_version(request={"name": name})
        sa_info   = json.loads(response.payload.data.decode("utf-8"))
        creds     = service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            str(_CREDENTIALS_FILE),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

    client = _build_genai_client(
        project=VERTEX_PROJECT,
        location=VERTEX_LOCATION,
        credentials=creds,
    )

    return client.caches.create(
        model=VERTEX_MODEL,
        config=genai_types.CreateCachedContentConfig(
            system_instruction=ANALYSIS_SYSTEM,
            contents=[ANALYSIS_GENERAL_STATIC],
            ttl=f"{_ANALYSIS_CACHE_TTL_MINUTES * 60}s",
            display_name=f"analysis-cache-{VERTEX_MODEL}",
        ),
    )


def get_or_create_analysis_cache():
    """Zwraca singleton CachedContent dla analysis prompts.

    Lazy: pierwszy call buduje cache i zwraca, kolejne reuse.
    Graceful: gdy build failuje (API error, quota itp.) → zwraca None.
    Caller (subprocess worker) musi obsłużyć None — fallback do call bez cache.

    TTL 1h: po wygaśnięciu Vertex zwróci 404 przy próbie użycia → caller
    łapie błąd i triggeruje rebuild (na razie — nie ma w tym fixu).
    """
    global _analysis_cache
    if _analysis_cache is not None:
        return _analysis_cache

    with _analysis_cache_lock:
        if _analysis_cache is not None:
            return _analysis_cache

        try:
            _analysis_cache = _build_analysis_cache()
            logger.info(f"Analysis cache created: {_analysis_cache.name}")
            # Print TEZ do stderr — subprocess (gemini_worker) nie ma logging.basicConfig
            # wiec logger.info nie widac w Cloud Run logs. Print do stderr jest forwardowany.
            import sys
            print(f"[CACHE] Build OK: {_analysis_cache.name}", file=sys.stderr)
            return _analysis_cache
        except Exception as e:
            logger.warning(f"Analysis cache build failed — calls go without cache: {e}")
            # Print do stderr z FULL exception info (type + message) — diagnostyka.
            import sys
            import traceback
            print(f"[CACHE] BUILD EXCEPTION: {type(e).__name__}: {e}", file=sys.stderr)
            print(f"[CACHE] Traceback (last frames):\n{traceback.format_exc()[-1000:]}", file=sys.stderr)
            return None


def reset_analysis_cache() -> None:
    """Wymuś rebuild przy następnym get_or_create — np. po expire/404."""
    global _analysis_cache
    with _analysis_cache_lock:
        _analysis_cache = None


def get_gemini_client_for_model(model_name: str):
    """Zwraca klient Gemini dla podanego modelu (cached per model_name).

    Pozwala wybrać tańszy/inny model per call site:
      - "gemini-2.5-flash-lite" dla validators/sanitizer (3-6x taniej)
      - "gemini-2.5-pro" dla najtrudniejszych decyzji (8x DROŻSZE)

    Główny domyślny klient (Flash) wciąż przez get_gemini_client() —
    używa singleton _client. Ten cache jest osobny dla pozostałych modeli.
    """
    if model_name in _clients_by_model:
        return _clients_by_model[model_name]

    with _clients_by_model_lock:
        if model_name in _clients_by_model:
            return _clients_by_model[model_name]
        client = _build_client_for_model(model_name)
        _clients_by_model[model_name] = client
        return client


def parse_gemini_json(raw: str) -> dict | list:
    """
    Parsuje odpowiedź Gemini do JSON.
    Obsługuje markdown code blocks, trailing text, itp.
    """
    text = raw.strip()

    # Usuń markdown code block jeśli obecny
    if text.startswith("```"):
        # Znajdź koniec bloku
        parts = text.split("```")
        if len(parts) >= 3:
            # ```json\n{...}\n``` → weź środkową część
            text = parts[1]
        else:
            # Tylko otwarcie ``` bez zamknięcia
            text = text[3:]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    return json.loads(text)


def _build_per_call_config(
    thinking_budget: int | None,
    max_output_tokens_override: int | None = None,
) -> dict | None:
    """Buduje generation_config override na poziomie wywołania.

    Phase 4 thinking_budget caps (re-enabled 2026-04-23) + Faza 5 cleanup
    2026-04-27: legacy vertexai SDK usunięty, thinking_config zawsze przechodzi
    przez google-genai adapter (`types.ThinkingConfig`).

    `max_output_tokens_override` per-agent zapobiega runaway generation.

    Zwraca None gdy brak override (model używa baked-in default 65535),
    inaczej dict z config.
    """
    if thinking_budget is None and max_output_tokens_override is None:
        return None  # Brak override — użyj baked-in (65535)

    result: dict = {
        "temperature": 0.2,
        "response_mime_type": "application/json",
    }
    if max_output_tokens_override is not None:
        result["max_output_tokens"] = max_output_tokens_override
    if thinking_budget is not None:
        result["thinking_config"] = {"thinking_budget": thinking_budget}
    return result


def _call_gemini_json_inner(
    prompt: str, max_retries: int = 2,  # max_retries=2 → 3 prób total (1 initial + 2 retry)
    thinking_budget: int | None = None,
    model_override: str | None = None,
    max_output_tokens: int | None = None,
) -> tuple[dict | list | None, int, int, int, int, str]:
    """
    Wewnętrzny wrapper na Gemini — nie trace'uje, tylko wykonuje call + retry.

    Args:
        prompt: prompt do Gemini.
        max_retries: liczba retry dla błędów API/JSON.
        thinking_budget: cap "thinking" tokens. None = auto. 0 = off.
        model_override: zmień model per call (np. "gemini-2.5-flash-lite" dla
            tanszych zadan validation/sanitization). None = default Flash.

    Returns:
        (result, input_tokens, output_tokens, cached_tokens, thoughts_tokens, finish_reason)
    """
    delays = [5, 15, 30]
    raw = ""
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    thoughts_tokens = 0
    finish_reason = "UNKNOWN"
    per_call_config = _build_per_call_config(thinking_budget, max_output_tokens)

    for attempt in range(max_retries + 1):
        try:
            if model_override is not None:
                client = get_gemini_client_for_model(model_override)
            else:
                client = get_gemini_client()
            if per_call_config is not None:
                response = client.generate_content(prompt, generation_config=per_call_config)
            else:
                response = client.generate_content(prompt)

            # Loguj finish_reason żeby wykryć MAX_TOKENS / SAFETY / RECITATION
            try:
                candidate     = response.candidates[0]
                finish_reason = candidate.finish_reason.name  # np. "STOP", "MAX_TOKENS", "SAFETY"
                usage         = getattr(response, "usage_metadata", None)
                if usage is not None:
                    input_tokens  = getattr(usage, "prompt_token_count", 0) or 0
                    output_tokens = getattr(usage, "candidates_token_count", 0) or 0
                    # cached_content_token_count: Gemini 2.5+ na Vertex zwraca ile
                    # z input_tokens pokryło się z cache'a (implicit lub explicit).
                    # Stare SDK / brak cache → pole nie istnieje albo 0.
                    cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
                    # thoughts_token_count: Gemini 2.5+ "thinking" tokeny.
                    # Wystawiane bezpośrednio w google-cloud-aiplatform >=1.100.
                    # FALLBACK dla starszych SDK (1.71.1 w prod Docker): pole nie
                    # istnieje, ale total_token_count = prompt + candidates + thoughts.
                    # Wnioskujemy z różnicy. Vertex AI billing potwierdza thinking
                    # nawet gdy SDK go nie wystawia (kosztują tyle samo co output).
                    thoughts_tokens = getattr(usage, "thoughts_token_count", 0) or 0
                    if thoughts_tokens == 0:
                        total = getattr(usage, "total_token_count", 0) or 0
                        inferred = total - input_tokens - output_tokens
                        if inferred > 0:
                            thoughts_tokens = inferred
                if finish_reason != "STOP":
                    logger.warning(
                        f"Gemini finish_reason={finish_reason} "
                        f"(output_tokens={output_tokens}) — odpowiedź może być niepełna"
                    )
                else:
                    logger.debug(
                        f"Gemini STOP OK, output_tokens={output_tokens}, "
                        f"thoughts_tokens={thoughts_tokens}"
                    )
            except Exception:
                pass  # usage_metadata może nie być dostępny — nie przerywaj

            raw = response.text.strip()
            return (
                parse_gemini_json(raw),
                input_tokens, output_tokens, cached_tokens, thoughts_tokens,
                finish_reason,
            )

        except json.JSONDecodeError as e:
            raw_preview = raw[:800] if raw else "(brak)"

            # Nie retry'uj jeśli finish_reason = MAX_TOKENS — powtarzanie tego samego
            # promptu da ten sam wynik. Zamiast tego loguj i zwróć None.
            try:
                _fr = response.candidates[0].finish_reason.name
            except Exception:
                _fr = None
            if _fr == "MAX_TOKENS":
                logger.error(
                    f"Gemini MAX_TOKENS — odpowiedź obcięta, retry nie pomoże. "
                    f"Odpowiedź ({len(raw)} znaków): {raw_preview}"
                )
                return None, input_tokens, output_tokens, cached_tokens, thoughts_tokens, "MAX_TOKENS"

            if attempt < max_retries:
                delay = delays[min(attempt, len(delays) - 1)]
                logger.warning(
                    f"Błąd parsowania JSON z Gemini, próba {attempt + 1}/{max_retries + 1}: {e}. "
                    f"Retry za {delay}s...\nOdpowiedź ({len(raw)} znaków): {raw_preview}"
                )
                import time
                time.sleep(delay)
            else:
                logger.error(
                    f"Błąd parsowania JSON z Gemini po {max_retries + 1} próbach: {e}\n"
                    f"Odpowiedź ({len(raw)} znaków): {raw_preview}"
                )
                return None, input_tokens, output_tokens, cached_tokens, thoughts_tokens, "JSON_DECODE_ERROR"

        except Exception as e:
            # PR#11 CRITICAL #1 fix (2026-04-20): rozróżnienie auth/transient.
            # Wcześniej generic Exception retry'owało WSZYSTKO — auth errors
            # (PermissionDenied, Unauthenticated) marnowały 60s na 3 próby
            # mimo że credential nigdy nie naprawi się sam.
            _fatal = False
            try:
                from google.api_core import exceptions as gapi_exc
                _FATAL_TYPES = (
                    gapi_exc.PermissionDenied      # 403
                    | gapi_exc.Unauthenticated     # 401
                    | gapi_exc.Forbidden           # 403
                    | gapi_exc.InvalidArgument     # 400 (np. zły schema)
                    | gapi_exc.NotFound            # 404
                    | gapi_exc.FailedPrecondition  # 400
                )
                if isinstance(e, _FATAL_TYPES):
                    _fatal = True
            except ImportError:
                pass  # bez google.api_core fallback do retry generic

            if _fatal:
                logger.error(
                    f"Vertex AI FATAL ({type(e).__name__}): {e}. "
                    f"Brak retry — sprawdź credentials/quota/IAM."
                )
                return None, input_tokens, output_tokens, cached_tokens, thoughts_tokens, "API_FATAL"

            if attempt < max_retries:
                delay = delays[min(attempt, len(delays) - 1)]
                logger.warning(
                    f"Błąd Vertex AI ({type(e).__name__}), próba {attempt + 1}/{max_retries + 1}: {e}. "
                    f"Retry za {delay}s..."
                )
                import time
                time.sleep(delay)
            else:
                logger.error(f"Błąd Vertex AI po {max_retries + 1} próbach: {e}")
                return None, input_tokens, output_tokens, cached_tokens, thoughts_tokens, "API_ERROR"

    return None, input_tokens, output_tokens, cached_tokens, thoughts_tokens, finish_reason


def call_gemini_json(
    prompt: str,
    max_retries: int = 2,
    metadata: dict | None = None,
    thinking_budget: int | None = None,
    model_override: str | None = None,
    max_output_tokens: int | None = None,
) -> dict | list | None:
    """
    Wywołuje Gemini i zwraca sparsowany JSON.

    Args:
        prompt:          tekst promptu do Gemini.
        max_retries:     liczba prób retry przy błędach (domyślnie 2 = 3 próby razem).
        metadata:        opcjonalny dict do Langfuse trace.
        thinking_budget: cap "thinking" tokens Gemini 2.5+ (aktualnie IGNOROWANY
                         na SDK 1.71.1 — patrz BACKLOG).
        model_override:  zmień model per call (np. "gemini-2.5-flash-lite").
        max_output_tokens: per-call cap max output. None = baked-in default 65535.
                         Safety net przeciwko runaway generation dla agentów
                         gdzie wiemy że odpowiedź jest krótka (validators, xpost).

    Returns:
        Sparsowany JSON (dict/list) lub None gdy wszystkie próby zawiodły.
    """
    langfuse_client = _get_langfuse_client()
    metadata_with_budget = dict(metadata or {})
    if thinking_budget is not None:
        metadata_with_budget["thinking_budget"] = thinking_budget
    if model_override is not None:
        metadata_with_budget["model_override"] = model_override
    if max_output_tokens is not None:
        metadata_with_budget["max_output_tokens_override"] = max_output_tokens

    effective_model = model_override or VERTEX_MODEL

    if langfuse_client is None:
        # Langfuse wyłączony — wywołaj bezpośrednio, bez trace.
        result, _, _, _, _, _ = _call_gemini_json_inner(
            prompt, max_retries,
            thinking_budget=thinking_budget,
            model_override=model_override,
            max_output_tokens=max_output_tokens,
        )
        return result

    # Langfuse aktywny — owij w generation span.
    try:
        model_params = {
            "temperature": 0.2,
            "max_output_tokens": max_output_tokens if max_output_tokens is not None else 65535,
            "response_mime_type": "application/json",
        }
        if thinking_budget is not None:
            model_params["thinking_budget"] = thinking_budget

        with langfuse_client.start_as_current_observation(
            as_type="generation",
            name="gemini_json_call",
            model=effective_model,
            input=trim_prompt_for_trace(prompt),
            metadata=metadata_with_budget,
            model_parameters=model_params,
        ) as generation:
            (
                result, input_tokens, output_tokens, cached_tokens,
                thoughts_tokens, finish_reason,
            ) = _call_gemini_json_inner(
                prompt, max_retries,
                thinking_budget=thinking_budget,
                model_override=model_override,
                max_output_tokens=max_output_tokens,
            )

            update_kwargs = {
                "usage_details": {
                    "input":    input_tokens,
                    "output":   output_tokens,
                    "cached":   cached_tokens,
                    "thoughts": thoughts_tokens,
                    "total":    input_tokens + output_tokens + thoughts_tokens,
                },
            }
            if result is None:
                update_kwargs["level"] = "ERROR"
                update_kwargs["status_message"] = f"Gemini failed: {finish_reason}"
            else:
                update_kwargs["output"] = result
                if finish_reason != "STOP":
                    update_kwargs["level"] = "WARNING"
                    update_kwargs["status_message"] = finish_reason

            generation.update(**update_kwargs)
            return result
    except Exception as e:
        # Langfuse SDK się wywalił w trakcie trace'owania — nigdy nie blokuj Gemini.
        logger.warning(f"Langfuse trace failed — falling back to raw call: {e}")
        result, _, _, _, _, _ = _call_gemini_json_inner(
            prompt, max_retries,
            thinking_budget=thinking_budget,
            model_override=model_override,
            max_output_tokens=max_output_tokens,
        )
        return result
