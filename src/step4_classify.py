# =============================================================================
# step4_classify.py: Layer 5: Break Classification
# =============================================================================
#
# Responsibilities:
#   - classify():          compute side totals, raw difference, typed breaks
#
# Core rule: every penny of the raw difference is accounted for by the
# sum of break differences plus tolerance absorbed in clean matches.
# This identity is tested and must hold exactly (Decimal arithmetic,
# no floats).
#
# Knows about: matches, records, break types, Decimal arithmetic
# Does NOT know about: how a break is explained or resolved, the AI, the PDF
# =============================================================================

from __future__ import annotations

import hashlib
from decimal import Decimal

from src.state import Break, BreakType, Match, MatchType, ReconciliationState


def classify(state: ReconciliationState) -> ReconciliationState:
    """Compute typed breaks and reconciliation totals.

    Finance context: the raw difference is the headline number in any
    reconciliation: how far apart the two sides are before any items
    are explained. Each Break carries a type that tells the reviewer
    what kind of investigation is needed: an amount mismatch means one
    side has the wrong figure; a reference mismatch means the same
    payment was recorded under different codes; an unmatched item means
    one side has a transaction the other does not. These types drive
    different review actions and different resolution paths. Each break
    carries a stable key derived from its content (not its position),
    so a resolution recorded today survives unchanged when the
    reconciliation is re-run tomorrow with updated data, provided the
    underlying transaction has not changed.
    """

    state.side_a_total = sum((r.signed_amount for r in state.side_a), Decimal("0"))
    state.side_b_total = sum((r.signed_amount for r in state.side_b), Decimal("0"))
    state.raw_difference = round(state.side_a_total - state.side_b_total, 2)

    matched_a_ids: set[str] = set()
    matched_b_ids: set[str] = set()
    breaks: list[Break] = []

    for m in state.matches:
        for r in m.side_a_records:
            matched_a_ids.add(r.row_id)
        for r in m.side_b_records:
            matched_b_ids.add(r.row_id)

        if m.match_type == MatchType.AMOUNT_MISMATCH:
            a_amt = sum(r.signed_amount for r in m.side_a_records)
            b_amt = sum(r.signed_amount for r in m.side_b_records)
            breaks.append(Break(
                break_type=BreakType.AMOUNT_MISMATCH,
                side_a_records=list(m.side_a_records),
                side_b_records=list(m.side_b_records),
                side_a_amount=a_amt,
                side_b_amount=b_amt,
                difference=round(a_amt - b_amt, 2),
            ))

        elif m.match_type == MatchType.REFERENCE_MISMATCH:
            a_amt = sum(r.signed_amount for r in m.side_a_records)
            b_amt = sum(r.signed_amount for r in m.side_b_records)
            breaks.append(Break(
                break_type=BreakType.REFERENCE_MISMATCH,
                side_a_records=list(m.side_a_records),
                side_b_records=list(m.side_b_records),
                side_a_amount=a_amt,
                side_b_amount=b_amt,
                difference=round(a_amt - b_amt, 2),
            ))

        elif m.match_type == MatchType.MANY_TO_ONE:
            a_amt = sum(r.signed_amount for r in m.side_a_records)
            b_amt = sum(r.signed_amount for r in m.side_b_records)
            breaks.append(Break(
                break_type=BreakType.MANY_TO_ONE_SUGGESTION,
                side_a_records=list(m.side_a_records),
                side_b_records=list(m.side_b_records),
                side_a_amount=a_amt,
                side_b_amount=b_amt,
                difference=round(a_amt - b_amt, 2),
            ))

    unmatched_a = [r for r in state.side_a if r.row_id not in matched_a_ids]
    for r in unmatched_a:
        breaks.append(Break(
            break_type=BreakType.UNMATCHED,
            side_a_records=[r],
            side_b_records=[],
            side_a_amount=r.signed_amount,
            side_b_amount=Decimal("0"),
            difference=r.signed_amount,
        ))

    unmatched_b = [r for r in state.side_b if r.row_id not in matched_b_ids]
    for r in unmatched_b:
        breaks.append(Break(
            break_type=BreakType.UNMATCHED,
            side_a_records=[],
            side_b_records=[r],
            side_a_amount=Decimal("0"),
            side_b_amount=r.signed_amount,
            difference=round(-r.signed_amount, 2),
        ))

    breaks.sort(key=lambda b: (
        b.break_type.value,
        b.side_a_records[0].date if b.side_a_records
        else b.side_b_records[0].date,
        b.stable_break_key,
    ))

    key_counts: dict[str, int] = {}
    for b in breaks:
        key_counts[b.stable_break_key] = key_counts.get(b.stable_break_key, 0) + 1
    if any(c > 1 for c in key_counts.values()):
        seen: dict[str, int] = {}
        for b in breaks:
            k = b.stable_break_key
            idx = seen.get(k, 0)
            seen[k] = idx + 1
            if key_counts[k] > 1:
                raw = f"{k}:{idx}"
                b.stable_break_key = hashlib.sha256(raw.encode()).hexdigest()[:16]

    state.breaks = breaks
    state.break_counts = {}
    for b in breaks:
        state.break_counts[b.break_type.value] = (
            state.break_counts.get(b.break_type.value, 0) + 1
        )

    state.resolved_position = state.raw_difference

    clean = sum(
        1 for m in state.matches
        if m.match_type in (MatchType.EXACT, MatchType.TOLERANCE)
    )

    print(f"  [OK] Side A total: {state.side_a_total:>12,.2f}")
    print(f"  [OK] Side B total: {state.side_b_total:>12,.2f}")
    print(f"  [OK] Raw difference (A - B): {state.raw_difference:>12,.2f}")
    print(f"  [OK] Clean matches: {clean}")
    print(f"  [OK] Breaks: {len(breaks)}")
    for bt, count in sorted(state.break_counts.items()):
        print(f"       - {bt}: {count}")

    return state
