"""Tell an unadjusted stooq download apart from an adjusted one (PUL-96).

stooq's per-symbol history takes an `o=` bitmask that disables each adjustment class;
`o=1111111` returns the prices actually quoted. Nothing inside the resulting CSV says
which variant it is, so a file fetched without the parameter parses perfectly and would
be written as a repair — replacing dividend-adjusted values with the same dividend-
adjusted values, now stamped with a `source` that claims otherwise. That is worse than
leaving the rows alone, because it destroys the evidence that they are wrong.

Two tempting checks do not work, both measured on BAC:

* **Fractional volume.** True of the bulk archive (`d_pl_txt` scales volume by the same
  factor as price, and a fractional share count is impossible), but *not* of a
  per-symbol download — that view rounds the scaled volume to whole shares. On
  2025-08-29 the adjusted download reports 6 708 and the raw one 6 486; both integers.
* **Tick compliance.** RTS 11 ticks reach 0.001 on lower-priced names, so "too many
  decimals" flags legitimate quotes. This is what invalidated the first detector built
  for this ticket.

What does work is comparing against a series already known to be adjusted. The bulk
archive is exactly that, and its fractional volumes identify precisely which of its rows
carry a factor. On those dates a raw download must differ; one that agrees is adjusted.
"""

_PRICE_DECIMALS = 2

# Two closes are "the same value" within a tenth of a grosz. A dividend factor moves a
# price by whole groszy (BAC's 3.42% is ~11 gr at 3.20), while the raw download's own
# float round-trip noise is ~1e-6 — so this sits far from both edges.
_SAME_VALUE_TOL = 0.001

# Below this share of agreeing rows the candidate is a different series. Kept at a bare
# majority rather than something stricter because low-priced names have days where the
# adjusted and raw values genuinely round together and cannot discriminate.
_ADJUSTED_IF_AGREEMENT_ABOVE = 0.5


class AdjustedSeriesError(RuntimeError):
    """The candidate matches the known-adjusted reference — it is not the raw series."""


class UnverifiableSeriesError(RuntimeError):
    """Nothing in the inputs can decide the question, so the file must not be written."""


def normalise_close(value: float) -> float:
    """Round a raw close back onto the tick grid.

    stooq produces the unadjusted value by dividing out the factor, which leaves float
    round-trip noise: 3.2799993915735 is 3.28. Verified across all 574 BAC rows — every
    one resolves cleanly, so this rounds noise away rather than papering over a
    mismatch.
    """
    return round(value, _PRICE_DECIMALS)


def adjusted_dates(bulk_rows: list[dict]) -> set[str]:
    """Dates the bulk archive proves are adjusted, via a fractional share count.

    Rows after a ticker's most recent ex-dividend date have a factor of 1.0 and whole
    volumes; they are raw in the bulk archive too, so they can never discriminate and
    are deliberately excluded.
    """
    dates = set()
    for row in bulk_rows:
        volume = row.get("volume")
        if volume is None:
            continue
        if abs(volume - round(volume)) > 1e-9:
            dates.add(row["date"])
    return dates


def assert_unadjusted(candidate_rows: list[dict], bulk_rows: list[dict]) -> None:
    """Raise unless `candidate_rows` is demonstrably a different series to the archive.

    Raises `UnverifiableSeriesError` when the two share no date the archive marks
    adjusted — silence is not evidence, and an unverifiable file is refused rather than
    assumed good.
    """
    reference = {r["date"]: r["close"] for r in bulk_rows if r.get("close") is not None}
    comparable = adjusted_dates(bulk_rows)
    overlap = [r for r in candidate_rows if r["date"] in comparable and r.get("close") is not None]

    if not overlap:
        raise UnverifiableSeriesError(
            "no overlap with a date the bulk archive marks adjusted, so this file "
            "cannot be shown to be the raw series; refusing to write it"
        )

    agreeing = sum(
        1 for r in overlap if abs(r["close"] - reference[r["date"]]) <= _SAME_VALUE_TOL
    )
    if agreeing / len(overlap) > _ADJUSTED_IF_AGREEMENT_ABOVE:
        raise AdjustedSeriesError(
            f"{agreeing} of {len(overlap)} closes match the dividend-adjusted bulk "
            "archive, so this download carries the same adjustment. Re-download with "
            "o=1111111 in the stooq URL to disable it."
        )
