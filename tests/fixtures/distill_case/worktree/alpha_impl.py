"""合成夹具（合成域 alpha）：仅用于验证蒸馏机制，不含任何物理域语义。"""

THRESHOLD = 0.15
VALUE = 1.0


def relative_error(actual: float, reference: float) -> float:
    if reference == 0:
        raise ValueError("reference must be non-zero")
    return abs(actual - reference) / abs(reference)


def within_threshold(actual: float, reference: float) -> bool:
    return relative_error(actual, reference) < THRESHOLD
