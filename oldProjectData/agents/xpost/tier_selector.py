"""
Tier-based company selector dla xpost pipeline.

Logika kaskadowa (od 2026-05-14):
  Tier 1 → Tier 2 → Tier 3 → Tier 4 (reszta)

Sloty (max 5) są wypełniane tier po tierze. W ramach każdego tieru
newsy są rankowane przez score_news() — identyczna heurystyka co główny pipeline.

Config: XPOST_HOT_TIERS w config.py (dict[int, set[str]]).
"""
from __future__ import annotations

from agents.xpost.scoring import rank_news


def select_by_tiers(
    news_list: list[dict],
    tiers: dict[int, set[str]],
    max_slots: int = 5,
) -> list[dict]:
    """
    Wybiera do `max_slots` newsów z listy, priorytetyzując wyższe tiery.

    Args:
        news_list: lista news-dictów (pola: spolka, tytul, kluczowe_fakty, ...)
        tiers: dict {numer_tieru: set tickerów}, np. {1: {"DGN","PAS"}, 2: {"PKO"}}
               Tier 4 (reszta) jest zawsze obecny domyślnie.
        max_slots: maksymalna liczba newsów w outpucie (domyślnie 5).

    Returns:
        Lista news-dictów, posortowana tier-by-tier (T1 pierwsze),
        w ramach tieru — malejąco wg score_news().
    """
    if not news_list:
        return []

    sorted_tier_nums = sorted(tiers.keys())
    tier4_key = (max(sorted_tier_nums) + 1) if sorted_tier_nums else 1

    # Grupuj newsy po tierze
    buckets: dict[int, list[dict]] = {t: [] for t in sorted_tier_nums}
    buckets[tier4_key] = []

    for news in news_list:
        spolka = (news.get("spolka") or "").strip().upper()
        placed = False
        for tier_num in sorted_tier_nums:
            if spolka in tiers[tier_num]:
                buckets[tier_num].append(news)
                placed = True
                break
        if not placed:
            buckets[tier4_key].append(news)

    # Wypełniaj sloty kaskadowo
    result: list[dict] = []
    remaining = max_slots

    for tier_num in sorted(buckets.keys()):
        if remaining <= 0:
            break
        bucket = buckets[tier_num]
        if not bucket:
            continue
        ranked = rank_news(bucket)
        take = min(len(ranked), remaining)
        result.extend(n for n, _ in ranked[:take])
        remaining -= take

    return result


def select_by_tiers_with_stats(
    news_list: list[dict],
    tiers: dict[int, set[str]],
    max_slots: int = 5,
) -> tuple[list[dict], dict[int, dict]]:
    """
    Jak select_by_tiers(), ale dodatkowo zwraca statystyki per tier.

    Returns:
        (selected_news, stats) gdzie stats = {
            tier_num: {
                "available": int,   # ile newsów było w tym tierze
                "selected":  int,   # ile zostało wybranych (po cap slotów)
                "tickers":   list[str],  # tickery wybranych newsów
            }
        }
    """
    if not news_list:
        sorted_tier_nums = sorted(tiers.keys())
        tier4_key = (max(sorted_tier_nums) + 1) if sorted_tier_nums else 1
        empty_stats = {t: {"available": 0, "selected": 0, "tickers": []} for t in sorted_tier_nums}
        empty_stats[tier4_key] = {"available": 0, "selected": 0, "tickers": []}
        return [], empty_stats

    sorted_tier_nums = sorted(tiers.keys())
    tier4_key = (max(sorted_tier_nums) + 1) if sorted_tier_nums else 1

    buckets: dict[int, list[dict]] = {t: [] for t in sorted_tier_nums}
    buckets[tier4_key] = []

    for news in news_list:
        spolka = (news.get("spolka") or "").strip().upper()
        placed = False
        for tier_num in sorted_tier_nums:
            if spolka in tiers[tier_num]:
                buckets[tier_num].append(news)
                placed = True
                break
        if not placed:
            buckets[tier4_key].append(news)

    result: list[dict] = []
    stats: dict[int, dict] = {}
    remaining = max_slots

    for tier_num in sorted(buckets.keys()):
        bucket = buckets[tier_num]
        ranked = rank_news(bucket)
        take = min(len(ranked), remaining) if remaining > 0 else 0
        selected = [n for n, _ in ranked[:take]]
        result.extend(selected)
        remaining -= take
        stats[tier_num] = {
            "available": len(bucket),
            "selected": take,
            "tickers": [n.get("spolka", "") for n in selected],
        }

    return result, stats
