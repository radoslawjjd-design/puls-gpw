"""Errors raised by broker-export parsers.

Kept in their own module so both the registry and the individual parsers can
import them without a circular dependency through ``src.brokers.__init__``.
"""


class BrokerImportError(Exception):
    """Base class for every failure while reading a broker export."""


class UnknownBrokerError(BrokerImportError):
    """The requested broker id has no registered parser."""


class BrokerParseError(BrokerImportError):
    """The file does not have the shape this parser expects.

    Raised instead of guessing. A broker export the user cannot edit is worth
    rejecting loudly — a silently misread row becomes a wrong position.
    """


class BrokerFileTooLargeError(BrokerImportError):
    """The file would cost more memory to read than the service can spare.

    Its own class rather than a ``BrokerParseError`` because the file may be
    perfectly well-formed: the objection is to its size after decompression, not
    its shape. The API layer maps this to 413 while its siblings map to 422.
    """
