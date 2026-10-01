"""Split math. All money is handled in integer cents so shares always add up."""
from __future__ import annotations


def to_cents(x) -> int:
    return int(round(float(x) * 100))


def fmt(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _distribute(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Split `total` cents in proportion to weights; remainder cents go to the
    largest fractional parts so the sum is exact."""
    wsum = sum(weights.values())
    if wsum <= 0:
        raise ValueError("Nothing to split.")
    raw = {k: total * w / wsum for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    rem = total - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:rem]:
        out[k] += 1
    return out


def equal_split(total_cents: int, people: list[str]) -> dict[str, int]:
    return _distribute(total_cents, {p: 1 for p in people})


def itemized_split(items: list[dict], tax_cents: int, tip_cents: int,
                   all_people: list[str]) -> dict[str, int]:
    """items: [{"name", "price" (dollars), "people": [names] or [] for everyone}].
    Tax and tip are shared in proportion to each person's item subtotal."""
    base = {p: 0 for p in all_people}
    for it in items:
        price = to_cents(it["price"])
        people = it.get("people") or all_people
        for p, c in equal_split(price, people).items():
            base[p] += c
    extras = tax_cents + tip_cents
    if sum(base.values()) == 0:
        raise ValueError("No item prices found.")
    shares = _distribute(extras, base) if extras else {p: 0 for p in base}
    return {p: base[p] + shares[p] for p in base if base[p] + shares[p] > 0}


def check(shares: dict[str, int], total_cents: int) -> None:
    if sum(shares.values()) != total_cents:
        raise ValueError(
            f"Shares add to {fmt(sum(shares.values()))}, receipt total is {fmt(total_cents)}.")
