from src.post_generator import GeneratedPost
from src.post_supervisor import validate_post

_TICKERS = ["PKO", "XTB", "PZU"]

# Valid 5-tweet thread: hook + 3 companies + closing (structure per Grok feedback)
_VALID_TWEETS = [
    "🚨 3 ESPI z GPW:\n▪ PKO Bank ($PKO) wyniki Q1\n▪ XTB ($XTB) wzrost klientów\n▪ PZU ($PZU) składka r/r\nKtóra spółka Cię interesuje?",
    "📊 PKO Bank ($PKO)\nZysk netto Q1: 120,1 mln PLN. Drugi kwartał z rzędu wzrostu.\nSkąd pochodzi ta poprawa — marża odsetkowa czy koszty?",
    "📊 XTB ($XTB)\nLiczba klientów +27% r/r. Rekordowy kwartał pod względem pozyskania.\nCzy wzrost jakościowy czy tylko liczba kont?",
    "📊 PZU ($PZU)\nSkładka przypisana brutto +18% r/r w Q1. Rekord techniczny.\nCzy wynik powtarzalny w kolejnych kwartałach?",
    "$PKO, $XTB czy $PZU — który ruch robi na Tobie największe wrażenie? Napisz w komentarzu!\n\n💾 Zapisz na później\nNie jest to rekomendacja inwestycyjna. #GPW #ESPI #SmallCaps",
]


def _post(*tweets: str) -> GeneratedPost:
    return GeneratedPost(tweets=list(tweets))


# ── Passing case ──────────────────────────────────────────────────────────────

def test_valid_post_approved():
    result = validate_post(_post(*_VALID_TWEETS), _TICKERS, expected_tweets=5)
    assert result.approved is True
    assert result.issues == []


# ── Tweet count enforcement ───────────────────────────────────────────────────

def test_wrong_tweet_count_rejected():
    # 3 companies → expected 5, but we give 6
    extra = list(_VALID_TWEETS) + ["extra tweet"]
    result = validate_post(_post(*extra), _TICKERS, expected_tweets=5)
    assert result.approved is False
    assert any("wrong tweet count" in issue for issue in result.issues)


def test_too_few_tweets_no_expected_rejected():
    result = validate_post(_post("tweet1", "tweet2"), _TICKERS)
    assert result.approved is False
    assert any("too short" in issue for issue in result.issues)


# ── Individual rule failures ──────────────────────────────────────────────────

def test_tweet_over_280_rejected():
    tweets = list(_VALID_TWEETS)
    tweets[1] = "A" * 281
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is False
    assert any("exceeds 280" in issue for issue in result.issues)


def test_missing_ticker_rejected():
    tweets = list(_VALID_TWEETS)
    tweets[1] = "📊 PKO Bank\nZysk netto Q1: 120,1 mln PLN. Dobry trend."
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is False
    assert any("$PKO" in issue for issue in result.issues)


def test_missing_gpw_hashtag_rejected():
    tweets = list(_VALID_TWEETS)
    tweets[-1] = "Który komunikat? #ESPI #SmallCaps Nie jest to rekomendacja inwestycyjna."
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is False
    assert any("#GPW" in issue for issue in result.issues)


def test_missing_disclaimer_rejected():
    tweets = list(_VALID_TWEETS)
    tweets[-1] = "Który komunikat? #GPW #ESPI #SmallCaps Dobry wpis."
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is False
    assert any("rekomendacj" in issue for issue in result.issues)


def test_truncated_tweet_rejected():
    tweets = list(_VALID_TWEETS)
    tweets[2] = "📊 XTB ($XTB)\nWzrost klientów o 27%..."
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is False
    assert any("truncated" in issue for issue in result.issues)


def test_truncated_tweet_ellipsis_rejected():
    tweets = list(_VALID_TWEETS)
    tweets[3] = "📊 PZU ($PZU)\nSkładka +18% r/r…"
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is False
    assert any("truncated" in issue for issue in result.issues)


# ── Investment advice detection ───────────────────────────────────────────────

def test_buy_signal_rejected():
    tweets = list(_VALID_TWEETS)
    tweets[1] = "📊 PKO Bank ($PKO)\n120,1 mln PLN zysku. Dynamiczny wzrost to sygnał do zakupu."
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is False
    assert any("investment advice" in issue for issue in result.issues)


def test_valuation_opinion_rejected():
    tweets = list(_VALID_TWEETS)
    tweets[2] = "📊 XTB ($XTB)\nWzrost 27%. Przy tej dynamice wycena wygląda tanio."
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is False
    assert any("investment advice" in issue for issue in result.issues)


def test_neutral_context_not_rejected():
    tweets = list(_VALID_TWEETS)
    tweets[1] = "📊 PKO Bank ($PKO)\n120,1 mln PLN zysku netto Q1. Dwa kwartały wzrostu — skąd pochodzi wynik?"
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is True


def test_kupuje_question_not_rejected():
    # "Kto kupuje?" is a factual question about who buys shares — not investment advice
    tweets = list(_VALID_TWEETS)
    tweets[2] = "📊 XTB ($XTB)\nEmisja 10 mln akcji za 34,7 mln PLN.\nKto kupuje te akcje i po co?"
    result = validate_post(_post(*tweets), _TICKERS, expected_tweets=5)
    assert result.approved is True
