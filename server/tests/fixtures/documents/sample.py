"""Extended cost of a BOM line (synthetic sample)."""


def extended_cost(qty: int, unit_price: float, scrap_rate: float = 0.015) -> float:
    """Cost including scrap."""
    return round(qty * unit_price * (1 + scrap_rate), 4)


class Line:
    def __init__(self, cpn: str, qty: int) -> None:
        self.cpn = cpn
        self.qty = qty
