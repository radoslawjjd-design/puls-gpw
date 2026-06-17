"""Shared Gemini client singleton for all modules."""
import logging
import mimetypes
import os
import threading
from dataclasses import dataclass

import google.genai as genai
import json5
from pydantic import BaseModel, ConfigDict, ValidationError

from src.exceptions import AnalysisError

logger = logging.getLogger(__name__)

GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
# Misreading a financial figure is higher-stakes than a missed news classification,
# so portfolio extraction uses a more capable tier than the bulk text pipeline above.
GEMINI_VISION_MODEL: str = os.environ.get("GEMINI_VISION_MODEL", "gemini-2.5-flash")

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


_EXTRACTION_SYSTEM_PROMPT = """Jesteś analitykiem odczytującym zrzuty ekranu z portfela
maklerskiego XTB. Wszystkie podane obrazy traktuj jako fragmenty JEDNEGO portfela z tego
samego momentu (np. różne zakładki lub przewinięcia tego samego ekranu) — NIE jako kolejne
obserwacje w czasie. Scal informacje ze wszystkich obrazów w JEDEN odczyt:
- całkowitą wartość portfela (total_value) i jej walutę (currency)
- listę pozycji (positions): ticker, wartość (value), udział procentowy (pct) — zdeduplikowaną
  po tickerze; jeśli ten sam ticker pojawia się na więcej niż jednym obrazie, użyj odczytu z
  obrazu, na którym jest czytelniejszy/pełniejszy

WAŻNE — niektóre obrazy mogą być ekranem przeglądu/dashboardu konta XTB, na którym widać
kilka kart pod-kont na raz (np. "IKE", "IKZE", "Plany Inwestycyjne", "Moje Transakcje") z ich
sumami. Takie karty pod-kont NIE są pozycjami giełdowymi — nigdy nie dodawaj ich do listy
positions, nawet jeśli wyglądają jak wiersz z wartością. Rozpoznaj prawdziwą pozycję po tym,
że ma instrument (np. "Akcje"/"ETF") oraz liczbę jednostek i cenę zakupu (format
"<liczba> @ <cena>"). Jeśli wśród podanych obrazów jest zarówno ekran dashboardu (z kartami
pod-kont) JAK I ekran listy pozycji danego pod-konta (zakładka "Otwarte" z wierszami
instrumentów), total_value odczytaj z nagłówka ekranu listy pozycji tego pod-konta — NIE z
dużej liczby na ekranie dashboardu, bo to suma wszystkich pod-kont razem, nie tego jednego
portfela.

Jeśli jakiegoś pola nie da się odczytać z wysoką pewnością (np. obraz jest rozmyty,
przycięty lub liczba jest nieczytelna), dodaj nazwę tego pola do uncertain_fields.
Gdy wszystko jest czytelne, uncertain_fields ma być pustą listą.

Pole "pct" (udział procentowy pozycji) jest OPCJONALNE — wiele zrzutów XTB go nie
pokazuje. Jeśli go nie widzisz dla danej pozycji, zwróć pct jako null (nie zgaduj) i
dodaj "<ticker>.pct" do uncertain_fields.

Zwróć TYLKO JEDEN obiekt JSON — NIGDY listę/tablicę, nawet jeśli podano kilka obrazów:
{"total_value": <float>, "currency": "<str>", "positions": [{"ticker": "<str>", "value": <float>, "pct": <float|null>}, ...], "uncertain_fields": ["<str>", ...]}
"""


class _PortfolioPositionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticker: str
    value: float
    pct: float | None = None


class _PortfolioExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total_value: float
    currency: str
    positions: list[_PortfolioPositionResponse] = []
    uncertain_fields: list[str] = []


@dataclass
class PortfolioPosition:
    ticker: str
    value: float
    pct: float | None


@dataclass
class PortfolioExtraction:
    """Result of `extract_portfolio_snapshot` — `uncertain_fields` is empty when
    extraction was fully confident."""
    total_value: float
    currency: str
    positions: list[PortfolioPosition]
    uncertain_fields: list[str]


def extract_portfolio_snapshot(image_paths: list[str]) -> PortfolioExtraction:
    """Extract portfolio totals and positions from one or more XTB screenshots.

    Raises AnalysisError if the model's response can't be parsed into the
    expected shape (the caller has no portfolio data to fall back to).
    """
    parts = []
    try:
        for path in image_paths:
            with open(path, "rb") as f:
                data = f.read()
            mime_type, _ = mimetypes.guess_type(path)
            if mime_type is None:
                logger.warning("Could not guess mime type for %s, defaulting to image/png", path)
            parts.append(genai.types.Part.from_bytes(data=data, mime_type=mime_type or "image/png"))
    except OSError as exc:
        raise AnalysisError(f"Portfolio extraction failed to read screenshot: {exc}") from exc

    client = get_client()
    try:
        response = client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=parts,
            config=genai.types.GenerateContentConfig(
                system_instruction=_EXTRACTION_SYSTEM_PROMPT,
                response_mime_type="application/json",
            ),
        )
        data = json5.loads(response.text)
        if isinstance(data, list):
            # Despite the prompt's explicit "always one object" instruction, Gemini
            # sometimes still returns one object per input image (PUL-39 manual
            # round-trip finding). Fall back to the last element as the most
            # complete reading rather than crashing.
            logger.warning(
                "Portfolio extraction got a JSON array instead of one object (%d items); "
                "using the last element",
                len(data),
            )
            data = data[-1]
        parsed = _PortfolioExtractionResponse.model_validate(data)
    except (ValidationError, ValueError) as exc:
        raise AnalysisError(f"Portfolio extraction failed to parse response: {exc}") from exc
    except Exception as exc:
        raise AnalysisError(f"Portfolio extraction call failed: {exc}") from exc

    return PortfolioExtraction(
        total_value=parsed.total_value,
        currency=parsed.currency,
        positions=[
            PortfolioPosition(ticker=p.ticker, value=p.value, pct=p.pct)
            for p in parsed.positions
        ],
        uncertain_fields=parsed.uncertain_fields,
    )
