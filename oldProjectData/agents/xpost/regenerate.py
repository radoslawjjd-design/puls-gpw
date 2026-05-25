"""
Regeneracja posta z uwzględnieniem sugestii walidatora supervisora.

Extracted z agents/xpost_agent.py (Faza 4 krok 11/N — finalny element).
"""
from __future__ import annotations

import logging

from agents.xpost.intraday import generate_xpost

logger = logging.getLogger(__name__)


def regenerate_with_suggestions(
    validation_result,
    **generate_kwargs,
) -> dict:
    """
    Regeneruje post z uwzględnieniem sugestii z walidacji.

    Args:
        validation_result: ValidationResult z xpost_validator
        **generate_kwargs: te same argumenty co generate_xpost()

    Returns:
        Nowy dict {"is_thread": ..., "tweets": [...]}
    """
    score    = validation_result.score
    problemy = "\n".join(f"- {p}" for p in validation_result.problemy) or "–"
    sugestie = validation_result.sugestie or "–"

    suggestions_context = (
        f"Ocena poprzedniej wersji: {score}/10\n"
        f"Problemy:\n{problemy}\n\n"
        f"Co poprawić:\n{sugestie}"
    )

    logger.info(
        f"Regeneracja posta z sugestiami supervisora "
        f"(poprzednia ocena: {score}/10)"
    )
    return generate_xpost(
        suggestions_context=suggestions_context,
        **generate_kwargs,
    )
