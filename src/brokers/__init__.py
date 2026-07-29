"""Broker-export parsers and the registry the import UI reads its list from.

Adding a broker means writing a parser that returns a ``BrokerImport`` and
adding one entry to ``_REGISTRY`` — nothing else in the app needs to change.
"""

from collections.abc import Callable

from src.brokers.errors import BrokerImportError, BrokerParseError, UnknownBrokerError
from src.brokers.xtb import BrokerImport, parse_xtb_export

# id -> (human label, parser). The id is what the client sends; the label is what
# the dropdown shows.
_REGISTRY: dict[str, tuple[str, Callable[[bytes], BrokerImport]]] = {
    "xtb": ("XTB", parse_xtb_export),
}


def list_brokers() -> list[dict]:
    """Brokers the import supports, in the order the dropdown should show them."""
    return [{"id": broker_id, "label": label} for broker_id, (label, _) in _REGISTRY.items()]


def get_parser(broker_id: str) -> Callable[[bytes], BrokerImport]:
    """Return the parser for ``broker_id``, or raise ``UnknownBrokerError``."""
    entry = _REGISTRY.get((broker_id or "").strip().lower())
    if entry is None:
        raise UnknownBrokerError(f"nieobslugiwany dom maklerski: {broker_id!r}")
    return entry[1]


__all__ = [
    "BrokerImport",
    "BrokerImportError",
    "BrokerParseError",
    "UnknownBrokerError",
    "get_parser",
    "list_brokers",
]
