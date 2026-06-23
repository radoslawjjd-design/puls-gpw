import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.parser import ParsedContent, parse_announcement
from src.scraper import Announcement

_WARSAW = ZoneInfo("Europe/Warsaw")
_ANN_URL = "https://www.bankier.pl/notowania/TEST/komunikaty/1"
_ANN = Announcement(
    title="TEST announcement",
    espi_code="TEST",
    bankier_url=_ANN_URL,
    published_at=datetime.datetime(2026, 6, 6, 10, 0, tzinfo=_WARSAW),
    source="espi",
)
_ANN_ID = "test-id-001"

# Seauid2 text must be >= 100 chars
_SEAUID2_TEXT = "Spółka | Wartość | Opis | " + "a" * 80

_HTML_SEAUID2 = f"""\
<!DOCTYPE html><html><body>
<table class="seauid2"><tr><td>{_SEAUID2_TEXT}</td></tr></table>
</body></html>"""

_HTML_PDF_ONLY = """\
<!DOCTYPE html><html><body>
<a href="/docs/report.pdf">Download report</a>
</body></html>"""

_HTML_HTML_FALLBACK = """\
<!DOCTYPE html><html><body>
<section class="o-article-content">
AI-generated summary of the announcement content here.
<br>
Actual announcement content that is well over fifty characters long to pass validation.
</section>
</body></html>"""

_HTML_EMPTY = "<!DOCTYPE html><html><body></body></html>"

_HTML_WITH_PROFILE = f"""\
<!DOCTYPE html><html><body>
<table class="seauid2"><tr><td>{_SEAUID2_TEXT}</td></tr></table>
<a href="/notowania/TESTCOMP/profile/quote.html">Profil spółki</a>
</body></html>"""

_HTML_PROFILE_PAGE = """\
<!DOCTYPE html><html><body>
<section id="quotes-profile-header-box" data-isin="PLTST0000011" data-symbol="TST">
<span class="a-heading__suffix -blue -with-dot">Test Company (TST)</span>
</section>
</body></html>"""

_HTML_FIVE_PDFS = """\
<!DOCTYPE html><html><body>
<a href="/docs/report1.pdf">PDF 1</a>
<a href="/docs/report2.pdf">PDF 2</a>
<a href="/docs/report3.pdf">PDF 3</a>
<a href="/docs/report4.pdf">PDF 4</a>
<a href="/docs/report5.pdf">PDF 5</a>
</body></html>"""

_HTML_BLOCKED_PDF = """\
<!DOCTYPE html><html><body>
<a href="/docs/regulamin.pdf">Regulamin</a>
<a href="/docs/report.pdf">Report</a>
</body></html>"""

_HTML_SEAUID2_WITH_PDF = f"""\
<!DOCTYPE html><html><body>
<table class="seauid2"><tr><td>{_SEAUID2_TEXT}</td></tr></table>
<a href="/docs/attachment.pdf">Załącznik</a>
</body></html>"""


def _mock_resp(html: str) -> MagicMock:
    m = MagicMock()
    m.text = html
    return m


def _make_pdf_mock(text: str) -> MagicMock:
    page = MagicMock()
    page.extract_text.return_value = text
    reader = MagicMock()
    reader.pages = [page]
    return reader


def test_seauid2_path():
    with (
        patch("src.parser.get", return_value=_mock_resp(_HTML_SEAUID2)),
        patch("src.parser.download_binary") as mock_dl,
    ):
        result = parse_announcement(_ANN, _ANN_ID)

    assert result.announcement_id == _ANN_ID
    assert result.parsed_content is not None
    assert len(result.parsed_content) >= 100
    mock_dl.assert_not_called()


def test_pdf_path_no_seauid2():
    mock_pdf_text = "Financial data from PDF " * 20

    with (
        patch("src.parser.get", return_value=_mock_resp(_HTML_PDF_ONLY)),
        patch("src.parser.download_binary", return_value=b"%PDF fake") as mock_dl,
        patch("src.parser.pypdf.PdfReader", return_value=_make_pdf_mock(mock_pdf_text)),
    ):
        result = parse_announcement(_ANN, _ANN_ID)

    assert result.parsed_content is not None
    assert "Financial data" in result.parsed_content
    mock_dl.assert_called_once()


def test_html_fallback_path():
    with (
        patch("src.parser.get", return_value=_mock_resp(_HTML_HTML_FALLBACK)),
        patch("src.parser.download_binary") as mock_dl,
    ):
        result = parse_announcement(_ANN, _ANN_ID)

    assert result.parsed_content is not None
    assert "Actual announcement content" in result.parsed_content
    mock_dl.assert_not_called()


def test_all_paths_fail():
    with (
        patch("src.parser.get", return_value=_mock_resp(_HTML_EMPTY)),
        patch("src.parser.download_binary") as mock_dl,
    ):
        result = parse_announcement(_ANN, _ANN_ID)

    assert result.parsed_content is None
    assert result.ticker is None
    assert result.company is None
    assert result.hop_url is None
    assert result.isin is None
    mock_dl.assert_not_called()


def test_pdf_char_cap():
    long_text = "x" * 20_000

    with (
        patch("src.parser.get", return_value=_mock_resp(_HTML_PDF_ONLY)),
        patch("src.parser.download_binary", return_value=b"%PDF fake"),
        patch("src.parser.pypdf.PdfReader", return_value=_make_pdf_mock(long_text)),
        patch("src.parser._MAX_CHARS", 100),
    ):
        result = parse_announcement(_ANN, _ANN_ID)

    assert result.parsed_content is not None
    assert len(result.parsed_content) <= 100


def test_max_pdfs_limit():
    # _find_pdf_links caps at _MAX_PDFS links before download; download_binary is called once per collected link.
    with (
        patch("src.parser.get", return_value=_mock_resp(_HTML_FIVE_PDFS)),
        patch("src.parser.download_binary", return_value=b"%PDF fake") as mock_dl,
        patch("src.parser.pypdf.PdfReader", return_value=_make_pdf_mock("PDF content")),
        patch("src.parser._MAX_PDFS", 3),
    ):
        parse_announcement(_ANN, _ANN_ID)

    assert mock_dl.call_count == 3


def test_ticker_company_extracted():
    with (
        patch("src.parser.get", return_value=_mock_resp(_HTML_WITH_PROFILE)),
        patch("src.company_profile.get", return_value=_mock_resp(_HTML_PROFILE_PAGE)),
    ):
        result = parse_announcement(_ANN, _ANN_ID)

    assert result.ticker == "TST"
    assert result.company == "Test Company"
    assert result.isin == "PLTST0000011"
    assert result.hop_url is not None and "profile/quote.html" in result.hop_url


def test_ticker_missing_gracefully():
    with patch("src.parser.get", return_value=_mock_resp(_HTML_SEAUID2)):
        result = parse_announcement(_ANN, _ANN_ID)

    assert result.ticker is None
    assert result.company is None
    assert result.hop_url is None
    assert result.isin is None


def test_seauid2_pdf_combination():
    """Combination branch: seauid2 present AND pdf link found — both merged."""
    pdf_text = "PDF attachment data " * 10
    with (
        patch("src.parser.get", return_value=_mock_resp(_HTML_SEAUID2_WITH_PDF)),
        patch("src.parser.download_binary", return_value=b"%PDF fake") as mock_dl,
        patch("src.parser.pypdf.PdfReader", return_value=_make_pdf_mock(pdf_text)),
    ):
        result = parse_announcement(_ANN, _ANN_ID)

    assert result.parsed_content is not None
    mock_dl.assert_called_once()
    assert _SEAUID2_TEXT[:50] in result.parsed_content
    assert "PDF attachment data" in result.parsed_content


def test_blocked_pdf_filtered():
    with (
        patch("src.parser.get", return_value=_mock_resp(_HTML_BLOCKED_PDF)),
        patch("src.parser.download_binary", return_value=b"%PDF fake") as mock_dl,
        patch("src.parser.pypdf.PdfReader", return_value=_make_pdf_mock("Report content")),
    ):
        parse_announcement(_ANN, _ANN_ID)

    assert mock_dl.call_count == 1
    called_url = mock_dl.call_args[0][0]
    assert "report.pdf" in called_url
    assert "regulamin" not in called_url
