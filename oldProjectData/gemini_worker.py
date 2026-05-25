"""
gemini_worker.py — Worker subprocess dla wywołań Gemini.

Uruchamiany przez analysis_agent.py jako osobny proces z timeoutem.
Czyta prompt z pliku wejściowego, wywołuje Gemini, zapisuje wynik JSON do pliku wyjściowego.

Użycie:
    python gemini_worker.py <input_file> <output_file>

input_file:  JSON z kluczami: prompt, vertex_project, vertex_location, vertex_model,
             credentials_file, metadata (opcjonalne — dict dla Langfuse trace)
output_file: JSON z wynikiem analizy lub {"error": "..."}
"""
import atexit
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.langfuse_trim import get_cloud_run_release, trim_prompt_for_trace  # noqa: E402


def _init_langfuse_in_worker():
    """
    Inicjalizuje Langfuse klienta w subprocess worker.

    Subprocess dziedziczy env vars (LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST/
    ENVIRONMENT) z parent process (main.py → analysis_agent → subprocess.run).
    Jeśli env vars są ustawione (parent zrobił bootstrap.load_secrets() w
    Cloud Run), tworzymy klienta + rejestrujemy atexit shutdown żeby ostatni
    batch trace'ów zdążył wyjść przed końcem subprocess.

    Returns:
        Langfuse instance gdy env vars + SDK dostępne, None w pozostałych.
        Wszystkie błędy są cicho połykane — tracing nigdy nie blokuje analizy.
    """
    public = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("LANGFUSE_SECRET_KEY")
    host   = os.environ.get("LANGFUSE_HOST")
    if not (public and secret and host):
        return None

    try:
        from langfuse import Langfuse
    except ImportError:
        return None

    try:
        client = Langfuse(
            public_key=public,
            secret_key=secret,
            host=host,
            environment=os.environ.get("LANGFUSE_ENVIRONMENT", "local"),
            release=get_cloud_run_release(),
        )
        atexit.register(lambda: _safe_shutdown(client))
        return client
    except Exception:
        return None


def _safe_shutdown(client):
    """atexit hook — flush + shutdown z tłumieniem błędów."""
    try:
        client.shutdown()
    except Exception:
        pass


def main():
    if len(sys.argv) != 3:
        print("Użycie: gemini_worker.py <input_file> <output_file>", file=sys.stderr)
        sys.exit(1)

    input_path  = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    # Wczytaj dane wejściowe
    try:
        with open(input_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        output_path.write_text(json.dumps({"error": f"Błąd wczytywania input: {e}"}))
        sys.exit(1)

    prompt           = data["prompt"]
    vertex_project   = data["vertex_project"]
    vertex_location  = data["vertex_location"]
    vertex_model     = data["vertex_model"]
    credentials_file = data["credentials_file"]
    metadata         = data.get("metadata", {})  # opcjonalne — dict dla Langfuse
    # thinking_budget: cap "myślenia" Gemini 2.5+ (Phase 4). Po Faza 5 cleanup
    # 2026-04-27 (genai SDK cache path) działa ZAWSZE — także gdy cache aktywny.
    thinking_budget_in = data.get("thinking_budget")
    # use_analysis_cache: cache zawiera ANALYSIS_SYSTEM + ANALYSIS_GENERAL_STATIC
    # (~1862 tokens). Per-call wysyłamy TYLKO dynamic prompt. Graceful fallback:
    # gdy cache unavailable → dorabiamy full prompt (system + static + dynamic).
    use_analysis_cache = data.get("use_analysis_cache", False)
    # cache_name: nazwa pre-utworzonego CachedContent (parent-side singleton).
    # Gdy podany → przekazujemy do generation_config["cached_content"], BEZ
    # tworzenia nowego cache. Eliminuje duplikację storage (fix 2026-04-23).
    # Brak cache_name → fallback do get_or_create_analysis_cache() (backward compat).
    cache_name = data.get("cache_name")

    # Resolve cache name (genai SDK path — Faza 5 cleanup 2026-04-27).
    # Po migracji nie mamy już `vertexai.preview` ani `from_cached_content` —
    # cache_name jest przekazywany do generate_content() w generation_config.
    cache_name_for_call: str | None = None
    if use_analysis_cache:
        if cache_name:
            cache_name_for_call = cache_name
            print(f"[CACHE] Using pre-created cache: {cache_name}", file=sys.stderr)
        else:
            # Backward compat (testy / brak parent-side pre-create).
            try:
                from agents.vertex_client import get_or_create_analysis_cache
                cache_obj = get_or_create_analysis_cache()
                if cache_obj is not None:
                    cache_name_for_call = getattr(cache_obj, "name", None)
                    print(f"[CACHE] get_or_create OK: {cache_name_for_call}", file=sys.stderr)
                else:
                    print("[CACHE] get_or_create returned None (build failed)", file=sys.stderr)
            except Exception as cache_err:
                print(f"[CACHE] EXCEPTION in get_or_create: "
                      f"{type(cache_err).__name__}: {cache_err}", file=sys.stderr)

    cache_used = bool(cache_name_for_call)  # Dla Langfuse metadata + decyzji o prompt

    # Build adapter (single path — no more legacy CachedContent branch).
    try:
        from google.oauth2 import service_account

        from agents._gemini_adapter import make_gemini_model

        creds = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

        # Gdy cache miał być użyty ale się nie udał → dorabiamy full prompt
        # (parent wysłał tylko dynamic część zakładając że cache uzupełni resztę).
        if use_analysis_cache and not cache_used:
            from agents.prompts import ANALYSIS_GENERAL_STATIC, ANALYSIS_SYSTEM
            prompt = f"{ANALYSIS_SYSTEM}\n\n{ANALYSIS_GENERAL_STATIC}\n{prompt}"
            print("[CACHE] Fallback — dorabiam full prompt (system + static + dynamic)", file=sys.stderr)

        client = make_gemini_model(
            project=vertex_project,
            location=vertex_location,
            model_name=vertex_model,
            credentials=creds,
            generation_config=None,
        )
    except Exception as e:
        output_path.write_text(json.dumps({"error": f"Błąd inicjalizacji Vertex AI: {e}"}))
        sys.exit(1)

    # Wzbogac metadata o info czy cache faktycznie użyty (dla Langfuse filter)
    metadata = dict(metadata)
    metadata["cache_used"] = cache_used

    # Langfuse — opcjonalny tracer LLM call. Graceful jeśli niedostępny.
    langfuse_client = _init_langfuse_in_worker()

    def _call_gemini_and_capture() -> tuple[str, int, int, int, int, str]:
        """Wewnętrzny helper: wywołuje Gemini, zwraca (raw_text, in_tok, out_tok,
        cached_tok, thoughts_tok, finish).

        cached_tok = ile z input_tokens zostało pokryte przez cache Vertex
        (Gemini 2.5+ w usage_metadata.cached_content_token_count). 0 gdy brak
        cache hit lub stary SDK.

        thoughts_tok = tokeny "thinking" Gemini 2.5+ (default ON). Droższe
        ($3.50/1M) niż regular output.
        """
        # Po Faza 5 cleanup 2026-04-27: cache + thinking_budget współistnieją
        # (oba przez genai GenerateContentConfig). Per-call config dict budowany
        # tylko gdy któreś z pól wymaga override.
        gen_config: dict | None = None
        if cache_name_for_call or thinking_budget_in is not None:
            gen_config = {}
            if cache_name_for_call:
                gen_config["cached_content"] = cache_name_for_call
            if thinking_budget_in is not None:
                gen_config["thinking_config"] = {"thinking_budget": thinking_budget_in}

        if gen_config is not None:
            response = client.generate_content(prompt, generation_config=gen_config)
        else:
            response = client.generate_content(prompt)
        raw_text = response.text.strip()
        # Ekstrakcja usage + finish_reason (opcjonalne — jeśli dostępne).
        input_tokens = output_tokens = cached_tokens = thoughts_tokens = 0
        finish_reason = "UNKNOWN"
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                input_tokens  = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage, "candidates_token_count", 0) or 0
                cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
                thoughts_tokens = getattr(usage, "thoughts_token_count", 0) or 0
                # FALLBACK dla starszych SDK (google-cloud-aiplatform 1.71.1
                # w prod Docker): pole thoughts_token_count nie istnieje,
                # ale total_token_count = prompt + candidates + thoughts.
                # Bez tego thoughts=0 zawsze, mimo że Vertex billing pokazuje
                # tysiące thinking tokens (~80% kosztu rachunku).
                if thoughts_tokens == 0:
                    total = getattr(usage, "total_token_count", 0) or 0
                    inferred = total - input_tokens - output_tokens
                    if inferred > 0:
                        thoughts_tokens = inferred
            finish_reason = response.candidates[0].finish_reason.name
        except Exception:
            pass
        return raw_text, input_tokens, output_tokens, cached_tokens, thoughts_tokens, finish_reason

    # Wywołaj Gemini — jeśli się zawiesi, parent subprocess.run(timeout=120) nas zabije.
    # Langfuse trace jest opcjonalny — jego błąd NIGDY nie blokuje wywołania Gemini.
    raw: str = ""
    traced = False
    if langfuse_client is not None:
        try:
            with langfuse_client.start_as_current_observation(
                as_type="generation",
                name="gemini_json_call",
                model=vertex_model,
                input=trim_prompt_for_trace(prompt),
                metadata=metadata,
                model_parameters={
                    "temperature": 0.2,
                    "response_mime_type": "application/json",
                },
            ) as generation:
                raw, in_tok, out_tok, cached_tok, thoughts_tok, finish = _call_gemini_and_capture()
                update_kwargs = {
                    "output": raw[:5000],
                    "usage_details": {
                        "input":    in_tok,
                        "output":   out_tok,
                        "cached":   cached_tok,
                        "thoughts": thoughts_tok,
                        "total":    in_tok + out_tok + thoughts_tok,
                    },
                }
                if finish != "STOP":
                    update_kwargs["level"] = "WARNING"
                    update_kwargs["status_message"] = finish
                generation.update(**update_kwargs)
                traced = True
        except Exception as e:
            # Langfuse SDK padł — loguj i kontynuuj BEZ trace'owania.
            print(f"[WARNING] Langfuse trace failed — falling back to raw call: {e}",
                  file=sys.stderr)

    if not traced:
        # Langfuse wyłączony lub trace się nie udał — wywołaj Gemini bez tracingu.
        try:
            raw, _, _, _, _, _ = _call_gemini_and_capture()
        except Exception as e:
            output_path.write_text(json.dumps({"error": f"Błąd Gemini: {e}"}))
            sys.exit(1)

    # Zapisz surową odpowiedź
    output_path.write_text(json.dumps({"raw": raw}), encoding="utf-8")
    sys.exit(0)


if __name__ == "__main__":
    main()
