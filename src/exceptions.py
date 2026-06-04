"""Pipeline exception hierarchy for puls-gpw.

Each pipeline stage raises its specific subtype; the top-level handler in
main.py catches PipelineStageError (or any Exception) and sends an alert.
"""


class PipelineStageError(Exception):
    """Base class for all pipeline stage failures. Catch-all for pipeline errors."""


class ScraperError(PipelineStageError):
    """Raised when the scraper fails to fetch or parse announcements from Bankier.pl."""


class ParserError(PipelineStageError):
    """Raised when the content parser fails to extract text from PDF or HTML."""


class AnalysisError(PipelineStageError):
    """Raised when Gemini analysis fails or the supervisor gate exhausts all retries."""


class NotificationError(PipelineStageError):
    """Raised when email delivery of the X-style post fails."""
