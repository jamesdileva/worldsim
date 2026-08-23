"""Derived market prices and price-driven trade sizing (Sprint 32).

Philosophy matches derived eras: prices are a PURE FUNCTION of current
world state (aggregate resource availability across living settlements),
recomputed on demand. Nothing is persisted, so prices can never
desynchronize from the world they describe.

Trade mechanics upgrade:
- Resource direction chosen by VALUATION GAP (difference in local
  scarcity) instead of raw stockpile difference — goods flow toward
  where they are most valued.
- Transfer size scales with the gap (bounded), so desperate settlements
  receive larger shipments than well-supplied partners.
- Era III donors keep their +25% shipment bonus (Sprint 31).
"""

from __future__ import annotations

from .settlement import Settlement

TRADE_RESOURCES: tuple[str, ...] = ("food", "wood", "stone", "metal")

# Base price per resource when availability sits at REFERENCE_AVAILABILITY.
# Metal stays pricier: it has no production building yet.
BASE_PRICES: dict[str, float] = {
    "food": 1.0, "wood": 1.0, "stone": 1.0, "metal": 2.0,
}
REFERENCE_AVAILABILITY = 20.0   # units per settlement at which base holds
PRICE_FLOOR = 0.25              # gluts never make things free
PRICE_CEILING = 8.0             # desperation never makes them infinite

# Trade sizing (multiples of the old fixed unit transfer).
BASE_TRADE_UNITS = 1.0
MAX_TRADE_GAP = 4.0             # valuation-gap ratio saturating size
MAX_TRADE_UNITS = 4.0           # hard cap before era bonus


def settlement_availability(settlement: Settlement, resource: str) -> float:
    """Per-capita availability of one resource for pricing purposes."""
    pop = max(1, settlement.population)
    if resource == "food":
        return settlement.food_stock / pop
    return max(0.0, settlement.resource_inventory.get(resource, 0.0)) / pop


def resource_price(sim, resource: str) -> float:
    """World market price: base scaled by inverse mean availability."""
    living = [s for s in sim.settlements if s.is_alive]
    if not living:
        return BASE_PRICES[resource]
    mean_avail = (
        sum(settlement_availability(s, resource) for s in living)
        / len(living)
    )
    raw = BASE_PRICES[resource] * REFERENCE_AVAILABILITY / (
        REFERENCE_AVAILABILITY + mean_avail * 4.0)
    return round(min(PRICE_CEILING, max(PRICE_FLOOR, raw)), 3)


def market_prices(sim) -> dict[str, float]:
    return {r: resource_price(sim, r) for r in TRADE_RESOURCES}


def valuation_gap(sim, donor_settlement_id: str, receiver_settlement_id: str,
                  resource: str) -> float:
    """How much MORE the receiver values the resource than the donor.

    Positive gaps invite shipments; magnitude drives transfer size."""
    by_id = {s.id: s for s in sim.settlements}
    donor = by_id.get(donor_settlement_id)
    receiver = by_id.get(receiver_settlement_id)
    if donor is None or receiver is None or donor.id == receiver.id:
        return 0.0
    # Availability ratio (donor surplus vs receiver scarcity), bounded
    # so no single settlement can dominate sizing. Deficits clamp to
    # zero: negative stocks (collapse) must not divide by ~zero.
    d_avail = max(0.0, settlement_availability(donor, resource))
    r_avail = max(0.0, settlement_availability(receiver, resource))
    return (d_avail - r_avail) / (min(d_avail, r_avail) + 1.0)


def best_trade(sim, source, dest) -> tuple[str, float] | None:
    """(resource, gap) with the largest positive valuation gap from source
    toward dest; None when nothing is worth shipping."""
    best: tuple[str, float] | None = None
    for resource in TRADE_RESOURCES:
        gap = valuation_gap(sim, source.id, dest.id, resource)
        if gap <= 0.0:
            continue
        if best is None or gap > best[1]:
            best = (resource, gap)
    return best


def transfer_units(gap: float, donor_is_era3: bool) -> float:
    """Gap-scaled shipment size (linear in gap, hard-capped); Era III
    ships 25% more on top of the cap."""
    units = min(MAX_TRADE_UNITS, BASE_TRADE_UNITS * (1.0 + max(0.0, gap)))
    if donor_is_era3:
        units *= 1.25
    return units
