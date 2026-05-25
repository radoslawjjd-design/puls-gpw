"""
Gemini API client — direct API key (nie Vertex AI).

Eksponuje generate(prompt) -> str | None.
Timeout wymuszony przez signal alarm (Unix) lub przez parametr timeout modelu.
"""
import logging
import time

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TIMEOUT

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY nie ustawiony")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def generate(
    prompt:      str,
    system:      str | None = None,
    temperature: float      = 0.4,
    max_tokens:  int        = 600,
) -> str | None:
    """
    Wywołuje Gemini i zwraca tekst odpowiedzi.
    Zwraca None przy błędzie (timeout, rate limit, itp.).
    """
    client = _get_client()

    contents: list = []
    if system:
        contents.append(types.Content(role="user", parts=[types.Part(text=system)]))
        contents.append(types.Content(role="model", parts=[types.Part(text="Rozumiem.")]))
    contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))

    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
    )

    for attempt in range(1, 3):
        try:
            response = client.models.generate_content(
                model    = GEMINI_MODEL,
                contents = contents,
                config   = config,
            )
            return response.text
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "quota" in err or "rate" in err:
                wait = 30 * attempt
                logger.warning(f"Gemini rate limit (próba {attempt}/2), czekam {wait}s: {e}")
                time.sleep(wait)
            else:
                logger.error(f"Gemini błąd (próba {attempt}/2): {e}")
                if attempt == 1:
                    time.sleep(5)

    return None
