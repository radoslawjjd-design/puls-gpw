"""Shared Gemini client singleton for all modules."""
import logging
import os
import threading

import google.genai as genai

logger = logging.getLogger(__name__)

GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

_genai_client: genai.Client | None = None
_genai_lock = threading.Lock()


def get_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        with _genai_lock:
            if _genai_client is None:
                _genai_client = genai.Client(
                    vertexai=True,
                    project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                    location=os.environ.get("GOOGLE_CLOUD_REGION", "europe-central2"),
                )
                logger.info("Gemini client initialized, model: %s", GEMINI_MODEL)
    return _genai_client
