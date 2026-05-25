"""
agents/_gemini_adapter.py — google-genai SDK adapter.

Centralna fabryka klientów Gemini. Po Faza 5 cleanup 2026-04-27 legacy
`vertexai.generative_models` SDK zostało całkowicie usunięte (deadline Google:
2026-06-24). Wszystkie 3 call sites (vertex_client.py, analysis_agent.py,
gemini_worker.py) dostają adapter wokół google-genai z legacy-kompatybilnym
API `.generate_content(prompt) -> response`.

Kontrakt response object:
    response.text                           # str | None
    response.usage_metadata                  # obj
        .prompt_token_count                  # int
        .candidates_token_count              # int
    response.candidates[0].finish_reason     # enum z .name (str)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class GenAIModelAdapter:
    """Adapter wrappuje google-genai Client + nazwę modelu + config
    i eksponuje legacy-kompatybilne API `.generate_content(prompt)`.

    Obiekt tworzony raz (singleton per-client) — config budujemy raz
    w __init__, żeby nie rebuildować go per-call.
    """

    __slots__ = ("_client", "_model_name", "_config")

    def __init__(
        self,
        client: Any,
        model_name: str,
        generation_config: dict[str, Any] | None,
    ) -> None:
        self._client = client
        self._model_name = model_name
        self._config = self._build_config(generation_config)

    @staticmethod
    def _build_config(generation_config: dict[str, Any] | None) -> Any:
        """dict → types.GenerateContentConfig (albo None jeśli brak config).

        Tłumaczy sub-objects:
        - `thinking_config` dict {thinking_budget, include_thoughts} →
          `types.ThinkingConfig` (Phase 4 thinking_budget caps, re-enabled 2026-04-23).
        """
        if not generation_config:
            return None
        from google.genai import types

        config_dict = dict(generation_config)  # copy — nie mutuj input
        thinking = config_dict.get("thinking_config")
        if isinstance(thinking, dict):
            config_dict["thinking_config"] = types.ThinkingConfig(**thinking)
        return types.GenerateContentConfig(**config_dict)

    def generate_content(
        self,
        prompt: str,
        *,
        generation_config: dict[str, Any] | None = None,
    ) -> Any:
        """Legacy-kompatybilne API.

        Mapuje na: client.models.generate_content(model=..., contents=..., config=...)
        Zwraca google.genai.types.GenerateContentResponse który ma:
        - .text (property → Optional[str])
        - .usage_metadata (prompt_token_count, candidates_token_count)
        - .candidates[0].finish_reason (enum z .name)

        Args:
            prompt: tekst do wysłania modelowi.
            generation_config: opcjonalny per-call override dictu config.
                Gdy podany → buduje NOWY `types.GenerateContentConfig` dla tego
                wywołania (bez modyfikowania baked-in). Gdy None → używa baked-in
                config z __init__ (cached, bez rebuildowania).
        """
        if generation_config is not None:
            config = self._build_config(generation_config)
        else:
            config = self._config
        return self._client.models.generate_content(
            model=self._model_name,
            contents=prompt,
            config=config,
        )


def _build_genai_client(
    *,
    project: str,
    location: str,
    credentials: Any,
) -> Any:
    """Buduje `google.genai.Client` w trybie Vertex AI.

    Wyodrębnione żeby testy mogły mockować inicjalizację bez uderzania
    w realną sieć / credentials.

    http_options.timeout (ms, 2026-04-23): hard SDK cap per call — zapobiega
    hangom przy Vertex 429 / sieciowych stallach (incident prod-xpost-morning
    8+ min zawis po "AFC is enabled"). Wartość z config.GEMINI_HTTP_TIMEOUT_MS.
    """
    from google import genai
    from google.genai import types

    import config
    http_options = types.HttpOptions(timeout=config.GEMINI_HTTP_TIMEOUT_MS)
    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        credentials=credentials,
        http_options=http_options,
    )


def make_gemini_model(
    *,
    project: str,
    location: str,
    model_name: str,
    credentials: Any,
    generation_config: dict[str, Any] | None = None,
) -> Any:
    """Factory — buduje adapter google-genai dla modelu Gemini.

    Args:
        project, location, model_name: standardowe parametry Vertex AI.
        credentials: google.oauth2.service_account.Credentials.
        generation_config: opcjonalny dict {temperature, max_output_tokens, ...}.

    Returns:
        `GenAIModelAdapter` — eksponuje `.generate_content(prompt) -> response`.
    """
    logger.info(
        f"Gemini SDK: google-genai — model={model_name} @ {location}"
    )
    client = _build_genai_client(
        project=project,
        location=location,
        credentials=credentials,
    )
    return GenAIModelAdapter(
        client=client,
        model_name=model_name,
        generation_config=generation_config,
    )
