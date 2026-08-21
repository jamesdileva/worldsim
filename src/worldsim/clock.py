"""Simulation clock: ticks, seasons, years (Sprint 6).

128 ticks = 1 season, 512 ticks = 1 year (detailed_sprint_plan.md Sprint 6).
"""

from __future__ import annotations

TICKS_PER_SEASON = 128
SEASONS_PER_YEAR = 4
TICKS_PER_YEAR = TICKS_PER_SEASON * SEASONS_PER_YEAR

SEASON_NAMES = ("spring", "summer", "autumn", "winter")


def season_index(tick: int) -> int:
    return (tick // TICKS_PER_SEASON) % SEASONS_PER_YEAR


def season_name(tick: int) -> str:
    return SEASON_NAMES[season_index(tick)]


def year_of(tick: int) -> int:
    return tick // TICKS_PER_YEAR


def describe(tick: int) -> str:
    return f"year {year_of(tick)}, {season_name(tick)} (tick {tick})"
