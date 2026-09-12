from alphaforge.cost.enums import CostReasonCode


class CostError(Exception):
    """Base error for the broker-independent Phase 6 cost model."""


class CostValidationError(CostError):
    """Fail-closed error carrying a deterministic machine-readable reason code."""

    def __init__(self, message: str, reason_code: CostReasonCode) -> None:
        super().__init__(message)
        self.reason_code = reason_code
