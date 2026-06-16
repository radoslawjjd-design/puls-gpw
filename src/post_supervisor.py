"""Rule-based supervisor for generated X thread posts."""
import re
from dataclasses import dataclass, field

from src.post_generator import GeneratedPost

# Phrases that constitute implicit or explicit investment recommendations (MAR/MiFID risk).
_INVESTMENT_ADVICE_PATTERNS = [
    r"sygnał do zakupu",
    r"czas (na|do) zakup",
    r"warto (kupić|nabyć|dokupić)",
    r"okazja inwestycyjna",
    r"\b(kupuj|sprzedaj|trzymaj)\b",
    r"wyjdź z pozycji",
    r"nie panikuj",
    r"dobry moment (na|do)",
    r"wycena (wygląda|jest) (tanio|drogo|atrakcyjn)",
    r"niedowartościowan",
    r"przewartościowan",
    r"przed wynikami warto",
]

_ADVICE_RE = re.compile("|".join(_INVESTMENT_ADVICE_PATTERNS), re.IGNORECASE)


@dataclass
class ValidationResult:
    approved: bool
    issues: list[str] = field(default_factory=list)


def validate_post(
    post: GeneratedPost,
    tickers: list[str],
    expected_tweets: int | None = None,
) -> ValidationResult:
    """Apply deterministic rules to a GeneratedPost. No Gemini call.

    expected_tweets: if provided, enforces exact tweet count (= n_companies + 2).
    """
    issues: list[str] = []

    n = len(post.tweets)

    if expected_tweets is not None:
        if n != expected_tweets:
            issues.append(f"wrong tweet count: got {n}, expected {expected_tweets}")
    elif n < 3:
        issues.append(f"thread too short: {n} tweets (min 3)")

    for i, tweet in enumerate(post.tweets):
        if len(tweet) > 280:
            issues.append(f"tweet {i + 1} exceeds 280 chars ({len(tweet)})")

    body = "\n".join(post.tweets[1:-1]) if n >= 2 else ""
    for ticker in tickers:
        # Body tweets reference the ticker parenthesized — ( $TICKER ): each body tweet is
        # about one company and carries that company's single cashtag (one per tweet is
        # within X's per-post limit). Match `(TICKER)` (tolerant of spaces / an optional $)
        # so the bare company name (e.g. "PKO" inside "PKO Bank") does not count as the ref.
        if not re.search(rf"\(\s*\$?{re.escape(ticker)}\s*\)", body):
            issues.append(f"missing ({ticker}) in body tweets")

    last = post.tweets[-1] if post.tweets else ""
    if "#GPW" not in last:
        issues.append("missing #GPW in last tweet")

    if "rekomendacj" not in last.lower():
        issues.append("missing disclaimer ('rekomendacj') in last tweet")

    for i, tweet in enumerate(post.tweets):
        if tweet.rstrip().endswith("...") or tweet.rstrip().endswith("…"):
            issues.append(f"tweet {i + 1} appears truncated (ends with '...' or '…')")

    full_text = "\n".join(post.tweets)
    match = _ADVICE_RE.search(full_text)
    if match:
        issues.append(f"investment advice detected: \"{match.group(0)}\"")

    return ValidationResult(approved=len(issues) == 0, issues=issues)
