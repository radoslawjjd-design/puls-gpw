"""
Pure format helpers dla xpost generatorów.

Extracted z agents/xpost_agent.py (Faza 4 redesignu).
Zero zewnętrznych zależności (Gemini, BQ) — tylko string processing.
"""
from __future__ import annotations

import json
import re

# ── Formatowanie top spółek / list ─────────────────────────────────────────────

def _fmt_top(items: list[dict], n: int) -> str:
    """Formatuje top N ogłoszeń jako flat list (bez etykiet sentymentu)."""
    lines = []
    for item in items[:n]:
        ticker   = item.get("spolka", "?")
        tytul    = (item.get("tytul") or "")[:300]
        dlaczego = (item.get("dlaczego_wazne") or "")[:300]
        fakty    = item.get("kluczowe_fakty") or []
        line = f"• {ticker}: {tytul}"
        if fakty:
            line += "\n  FAKTY: " + " | ".join(str(f)[:150] for f in fakty[:5])
        elif dlaczego:
            line += f"\n  → {dlaczego}"
        lines.append(line)
    return "\n".join(lines) if lines else "brak danych"


def _fmt_list(items: list, n: int, key: str | None = None) -> str:
    """Formatuje listę stringów lub dictów."""
    if not items:
        return "brak danych"
    result = []
    for item in items[:n]:
        text = item.get(key, str(item)) if isinstance(item, dict) and key else str(item)
        result.append(f"• {text[:150]}")
    return "\n".join(result)


def _fmt_index(data: dict, name: str) -> str:
    """Formatuje wiersz indeksu/surowca/waluty."""
    info = data.get(name, {})
    cena = info.get("cena", "?")
    zmiana = info.get("zmiana_proc") or 0
    sign = "+" if zmiana >= 0 else ""
    return f"{name}: {cena} ({sign}{zmiana}%)"


# ── TLD suffix stripping (anti-autolink na X) ──────────────────────────────────

_TLD_SUFFIX_RE = re.compile(r"\b([A-Z][a-zA-Z]+)\.(eu|pl|com)\b")


def _strip_tld_suffix(s: str) -> str:
    """Strip .eu/.pl/.com suffix from CapitalizedCompany names.

    X/Twitter auto-links 'Allegro.eu', 'Onet.pl' etc. as domains even when
    they're just company names. Remove the suffix so X doesn't link them.
    Only strips when preceded by an uppercase letter (company name pattern),
    leaves lowercase domains alone (real URLs blocked via XPOST_FORBIDDEN_WORDS).
    """
    return _TLD_SUFFIX_RE.sub(r"\1", s)


def _strip_trailing_ticker_hashtags(tweet: str) -> str:
    """Strip lines containing ONLY #TICKER hashtags that appear AFTER the disclaimer (⚖️).

    Agenda posts should end with:
      #GPW #giełda #FinTwit
      ⚖️ Nie stanowi rekomendacji...

    but Gemini sometimes appends ticker hashtags (#PKOBP #LPP ...) after the disclaimer.
    Those duplicate content hashtags already present in the post body, so we cut them.
    Footer "#GPW #giełda #FinTwit" stays because it precedes the disclaimer.
    """
    if not tweet or "⚖️" not in tweet:
        return tweet
    lines = tweet.split("\n")
    result = []
    after_disclaimer = False
    for line in lines:
        stripped = line.strip()
        if after_disclaimer and stripped and all(w.startswith("#") for w in stripped.split()):
            continue  # drop pure-hashtag line after disclaimer
        result.append(line)
        if "⚖️" in line:
            after_disclaimer = True
    return "\n".join(result).rstrip()


# ── Tweet extraction (str/dict/stringified-JSON → clean str) ──────────────────

def _extract_tweet(t) -> str:
    """Normalizuje tweet z Gemini: str/dict/stringified-JSON → czysty str.

    Dodatkowo usuwa .eu/.pl/.com sufix z nazw spółek (np. 'Allegro.eu' → 'Allegro')
    żeby X nie autolinkował ich jako domen.
    """
    if isinstance(t, dict):
        raw = t.get("text") or t.get("content") or str(t)
        return _strip_tld_suffix(raw)
    if isinstance(t, str):
        s = t.strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    return _strip_tld_suffix(parsed.get("text") or parsed.get("content") or s)
            except (json.JSONDecodeError, ValueError):
                pass
            return _strip_tld_suffix(s)
        return _strip_tld_suffix(s)
    return _strip_tld_suffix(str(t))


# ── Merge sentiment-split announcements → flat list ───────────────────────────

def _merge_top_announcements(
    top_pozytywne: list[dict],
    top_negatywne: list[dict],
    n: int,
) -> list[dict]:
    """
    Scala top_pozytywne i top_negatywne w jedną flat listę (interleaved).
    Usunięcie etykiet sentymentu z danych wejściowych → Gemini nie jest
    prowokowane do grupowania po sentymencie.
    """
    merged = []
    max_len = max(len(top_pozytywne), len(top_negatywne))
    for i in range(max_len):
        if i < len(top_pozytywne):
            merged.append(top_pozytywne[i])
        if i < len(top_negatywne):
            merged.append(top_negatywne[i])
    return merged[:n]
