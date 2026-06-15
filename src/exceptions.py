"""Pipeline exception hierarchy for puls-gpw.

Each pipeline stage raises its specific subtype; the top-level handler in
main.py catches PipelineStageError (or any Exception) and sends an alert.
"""


class PipelineStageError(Exception):
    """Base class for all pipeline stage failures. Catch-all for pipeline errors."""


class BigQueryError(PipelineStageError):
    """Raised when a BigQuery operation (query, insert, update) fails."""


class ScraperError(PipelineStageError):
    """Raised when the scraper fails to fetch or parse announcements from Bankier.pl."""


class ParserError(PipelineStageError):
    """Raised when the content parser fails to extract text from PDF or HTML."""


class AnalysisError(PipelineStageError):
    """Raised when Gemini analysis fails or the supervisor gate exhausts all retries."""


class NotificationError(PipelineStageError):
    """Raised when email delivery of the X-style post fails."""


class XPublisherError(PipelineStageError):
    """Raised when X publishing fails with nothing posted.

    Covers missing OAuth credentials and a failure on the first tweet
    (status `failed` — no half-thread is live on X).
    """


class XPublishPartialError(XPublisherError):
    """Raised when a thread fails mid-publish with ≥1 tweet already live on X.

    Carries the ids posted before the failure so the caller can record a
    `partial` status. `published_ids` is non-empty by construction.
    """

    def __init__(self, published_ids: list[str], cause: Exception):
        self.published_ids = published_ids
        self.cause = cause
        super().__init__(
            f"X thread failed mid-publish after {len(published_ids)} tweet(s): {cause}"
        )
