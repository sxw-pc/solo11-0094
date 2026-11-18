"""Typed errors used at the CLI boundary."""


class TzGateError(Exception):
    """Base class for expected, user-facing failures."""


class ContractError(TzGateError):
    """The declarative contract is missing, malformed or unsafe."""


class RulebookError(TzGateError):
    """The local timezone rulebook is malformed or inconsistent."""


class InputError(TzGateError):
    """A lake directory or file cannot be read safely."""


class OutputError(TzGateError):
    """An output path is invalid or would write into an input."""


class TimestampParseError(TzGateError):
    """A timestamp value does not match the declared format."""


class DiscoveryError(TzGateError):
    """Partition discovery failed for a dataset."""


class LedgerError(TzGateError):
    """The SQLite ledger is absent or unreadable."""


class ConsoleError(TzGateError):
    """The read-only console cannot be started."""


class SettlementError(TzGateError):
    """A transfer-subsidy clearing input or invariant is invalid."""


class StaleSettlementError(SettlementError):
    """A cached settlement no longer matches the verified lake or contract."""
