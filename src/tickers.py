"""What a GPW/NewConnect ticker is allowed to look like.

One definition, shared by the profile parser, both `companies` write paths and the
post generator, so the four cannot drift apart on what they accept (PUL-102).

Deliberately import-free: `db.bigquery` needs it, and `tach.toml` records that db
should have no upward dependencies. A module with no imports of its own is the
cheapest possible version of that violation.
"""

import re

# The hyphen and the length are both grounded in the real table rather than
# guessed. `CREOTECH-PDA` (12) and `CRQUANTUM-PDA` (13) are bankier symbols for
# allotment rights, correctly extracted today; the ticket's suggested
# `[A-Z0-9]{1,10}` would have rejected them along with the junk and turned an
# unrelated quirk into a fresh outage. 16 leaves headroom without meaning
# anything on its own — length is not what separates a ticker from a brand word.
# Case and character set are: `Żabka` and `przejęty` fail on lowercase whatever
# the bound.
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,15}$")


def is_valid_ticker(value: str | None) -> bool:
    """True when `value` has the shape of an exchange ticker.

    Shape only — this says nothing about whether the instrument exists. Its job is
    to separate `ZAB` from `Żabka` and `przejęty`, which is exactly the failure
    that put a brand name into three tables and a cashtag onto X.
    """
    return bool(value) and bool(_TICKER_RE.match(value))
