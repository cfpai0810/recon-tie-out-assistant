# =============================================================================
# state.py: Shared Type Definitions
# =============================================================================
#
# Responsibilities:
#   - Direction, MatchType, BreakType, ResolutionType:  pipeline enums
#   - Record, Match, Break, Draft, Resolution, Problem: value objects
#   - ReconciliationState:                              pipeline state container
#
# Core rule: pure data definitions with no business logic. Every step
# imports from here; this module imports from nothing in src/.
#
# Knows about: nothing (pure data definitions, no imports from src/)
# Does NOT know about: any step's logic
# =============================================================================

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class Direction(Enum):
    MONEY_IN = "money_in"
    MONEY_OUT = "money_out"


class MatchType(Enum):
    EXACT = "exact"
    TOLERANCE = "tolerance"
    AMOUNT_MISMATCH = "amount_mismatch"
    REFERENCE_MISMATCH = "reference_mismatch"
    MANY_TO_ONE = "many_to_one"


class BreakType(Enum):
    AMOUNT_MISMATCH = "amount_mismatch"
    REFERENCE_MISMATCH = "reference_mismatch"
    UNMATCHED = "unmatched"
    MANY_TO_ONE_SUGGESTION = "many_to_one_suggestion"


class ResolutionType(Enum):
    ACCEPT_AS_TIMING = "accept_as_timing"
    ADJUST = "adjust"
    INVESTIGATE = "investigate"
    DISMISS = "dismiss"
    CONFIRM_MATCH = "confirm_match"


@dataclass
class Record:
    row_id: str
    side: str
    date: date
    reference: str
    description: str
    signed_amount: Decimal
    abs_amount: Decimal
    direction: Direction

    def sort_key(self):
        return (self.reference, self.abs_amount, str(self.date), self.row_id)


@dataclass
class Match:
    match_type: MatchType
    side_a_records: list[Record]
    side_b_records: list[Record]
    difference: Decimal = Decimal("0")


@dataclass
class Break:
    break_type: BreakType
    side_a_records: list[Record]
    side_b_records: list[Record]
    side_a_amount: Decimal
    side_b_amount: Decimal
    difference: Decimal
    stable_break_key: str = ""

    def __post_init__(self):
        if not self.stable_break_key:
            self.stable_break_key = self._compute_stable_key()

    def _compute_stable_key(self):
        tuples = []
        for r in self.side_a_records:
            tuples.append(("A", r.reference, f"{r.signed_amount:.2f}",
                           str(r.date), r.description))
        for r in self.side_b_records:
            tuples.append(("B", r.reference, f"{r.signed_amount:.2f}",
                           str(r.date), r.description))
        tuples.sort()
        raw = "|".join(":".join(t) for t in tuples)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Draft:
    explanation: str
    follow_up: str


@dataclass
class Resolution:
    resolution_type: ResolutionType
    reviewer_text: str
    adjustment_amount: Decimal | None = None
    adjustment_side: str | None = None
    timestamp: str = ""
    figures_hash: str = ""


@dataclass
class Problem:
    side: str
    row_number: int
    column: str
    message: str


@dataclass
class ReconciliationState:
    reconciliation_id: str = ""
    side_a: list[Record] = field(default_factory=list)
    side_b: list[Record] = field(default_factory=list)
    side_a_label: str = "Bank Statement"
    side_b_label: str = "Cash Book"
    matches: list[Match] = field(default_factory=list)
    breaks: list[Break] = field(default_factory=list)
    drafts: dict[str, Draft] = field(default_factory=dict)
    resolutions: dict[str, Resolution] = field(default_factory=dict)
    raw_difference: Decimal = Decimal("0")
    side_a_total: Decimal = Decimal("0")
    side_b_total: Decimal = Decimal("0")
    matched_count: int = 0
    break_counts: dict[str, int] = field(default_factory=dict)
    resolved_position: Decimal = Decimal("0")
    problems: list[Problem] = field(default_factory=list)
