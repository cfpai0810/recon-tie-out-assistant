# =============================================================================
# step3_match.py: Layer 4: Transaction Matching
# =============================================================================
#
# Responsibilities:
#   - match():                      apply all five rules in precedence order
#   - _rule_1_exact():              same reference, exact amount
#   - _rule_2_tolerance():          same reference, amount within tolerance
#   - _rule_3_amount_mismatch():    same reference, amount beyond tolerance
#   - _rule_4_reference_mismatch(): same amount, different reference
#   - _rule_5_many_to_one():        several items summing to one
#
# Core rule: matching is fully deterministic. Same inputs always produce
# the same pairings. Nothing here calls the language model.
#
# Knows about: the two record lists, amount/date/reference, tolerance
#   and many-to-one bounds
# Does NOT know about: how a break is resolved, the AI, the PDF
# =============================================================================

from __future__ import annotations

from decimal import Decimal
from itertools import combinations

from config import MAX_MANY_TO_ONE_ITEMS, MANY_TO_ONE_DATE_WINDOW_DAYS
from src.state import Match, MatchType, Record, ReconciliationState


def _tie_break_key(candidate: Record, anchor: Record) -> tuple:
    date_diff = abs((candidate.date - anchor.date).days)
    amt_diff = abs(candidate.abs_amount - anchor.abs_amount)
    return (date_diff, amt_diff, candidate.reference, candidate.abs_amount, candidate.row_id)


def _amounts_within_tolerance(a: float, b: float, tolerance: float,
                              tolerance_rel: float = 0) -> bool:
    bound = max(tolerance, tolerance_rel * max(abs(a), abs(b)))
    return abs(a - b) <= bound


def _amounts_match_exact(a: float, b: float) -> bool:
    return a == b


def _direction_matches(a: Record, b: Record) -> bool:
    return a.direction == b.direction


def _rule_1_exact(pool_a: list[Record], pool_b: list[Record]) -> list[Match]:
    """Same reference AND exactly equal amount.

    Finance context: this is the gold-standard match. When a bank
    transaction carries the same reference as the book entry and the
    amounts agree to the penny, the pairing is certain. Most clean
    reconciliations are resolved entirely by this rule.
    """
    matches = []
    used_a: set[str] = set()
    used_b: set[str] = set()

    ref_to_b: dict[str, list[Record]] = {}
    for r in pool_b:
        ref_to_b.setdefault(r.reference, []).append(r)

    for a in sorted(pool_a, key=lambda r: r.sort_key()):
        if a.row_id in used_a:
            continue
        candidates = [
            b for b in ref_to_b.get(a.reference, [])
            if b.row_id not in used_b
            and _direction_matches(a, b)
            and _amounts_match_exact(a.abs_amount, b.abs_amount)
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda c: _tie_break_key(c, a))
        matches.append(Match(
            match_type=MatchType.EXACT,
            side_a_records=[a],
            side_b_records=[best],
            difference=Decimal("0"),
        ))
        used_a.add(a.row_id)
        used_b.add(best.row_id)

    return matches, used_a, used_b


def _rule_2_tolerance(pool_a: list[Record], pool_b: list[Record],
                      tolerance: float,
                      tolerance_rel: float = 0) -> list[Match]:
    """Same reference, amounts differ by no more than tolerance.

    Finance context: penny differences arise from rounding in different
    systems (the bank rounds a foreign-currency conversion differently
    from the company's ERP). A tolerance match is still high-confidence
    because the reference agrees.
    """
    matches = []
    used_a: set[str] = set()
    used_b: set[str] = set()

    ref_to_b: dict[str, list[Record]] = {}
    for r in pool_b:
        ref_to_b.setdefault(r.reference, []).append(r)

    for a in sorted(pool_a, key=lambda r: r.sort_key()):
        if a.row_id in used_a:
            continue
        candidates = [
            b for b in ref_to_b.get(a.reference, [])
            if b.row_id not in used_b
            and _direction_matches(a, b)
            and not _amounts_match_exact(a.abs_amount, b.abs_amount)
            and _amounts_within_tolerance(a.abs_amount, b.abs_amount,
                                         tolerance, tolerance_rel)
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda c: _tie_break_key(c, a))
        diff = a.signed_amount - best.signed_amount
        matches.append(Match(
            match_type=MatchType.TOLERANCE,
            side_a_records=[a],
            side_b_records=[best],
            difference=diff,
        ))
        used_a.add(a.row_id)
        used_b.add(best.row_id)

    return matches, used_a, used_b


def _rule_3_amount_mismatch(pool_a: list[Record], pool_b: list[Record],
                            tolerance: float,
                            tolerance_rel: float = 0) -> list[Match]:
    """Same reference, amounts differ by MORE than tolerance.

    Finance context: when two systems carry the same reference but the
    amounts disagree materially, something was mis-keyed, partially paid,
    or adjusted on one side. The matcher pairs them so the break card
    shows both figures and the delta, rather than orphaning them as two
    unrelated items.
    """
    matches = []
    used_a: set[str] = set()
    used_b: set[str] = set()

    ref_to_b: dict[str, list[Record]] = {}
    for r in pool_b:
        ref_to_b.setdefault(r.reference, []).append(r)

    for a in sorted(pool_a, key=lambda r: r.sort_key()):
        if a.row_id in used_a:
            continue
        candidates = [
            b for b in ref_to_b.get(a.reference, [])
            if b.row_id not in used_b
            and _direction_matches(a, b)
            and not _amounts_within_tolerance(a.abs_amount, b.abs_amount,
                                             tolerance, tolerance_rel)
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda c: _tie_break_key(c, a))
        diff = a.signed_amount - best.signed_amount
        matches.append(Match(
            match_type=MatchType.AMOUNT_MISMATCH,
            side_a_records=[a],
            side_b_records=[best],
            difference=diff,
        ))
        used_a.add(a.row_id)
        used_b.add(best.row_id)

    return matches, used_a, used_b


def _rule_4_reference_mismatch(pool_a: list[Record], pool_b: list[Record],
                               tolerance: float,
                               tolerance_rel: float = 0) -> list[Match]:
    """Same amount (within tolerance), different reference.

    Finance context: banks and companies often use different reference
    systems for the same payment (the bank assigns an FPO number, the
    company uses an invoice number). When the amounts agree but the
    references differ, this rule pairs them and flags the mismatch for
    review.
    """
    matches = []
    used_a: set[str] = set()
    used_b: set[str] = set()

    for a in sorted(pool_a, key=lambda r: r.sort_key()):
        if a.row_id in used_a:
            continue
        candidates = [
            b for b in pool_b
            if b.row_id not in used_b
            and _direction_matches(a, b)
            and b.reference != a.reference
            and _amounts_within_tolerance(a.abs_amount, b.abs_amount,
                                         tolerance, tolerance_rel)
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda c: _tie_break_key(c, a))
        diff = a.signed_amount - best.signed_amount
        matches.append(Match(
            match_type=MatchType.REFERENCE_MISMATCH,
            side_a_records=[a],
            side_b_records=[best],
            difference=diff,
        ))
        used_a.add(a.row_id)
        used_b.add(best.row_id)

    return matches, used_a, used_b


def _rule_5_many_to_one(pool_a: list[Record], pool_b: list[Record],
                        tolerance: float,
                        tolerance_rel: float = 0) -> list[Match]:
    """Several cash-book (side B) items summing to one bank (side A) item.

    Finance context: batch deposits are common, a company lodges several
    cheques or card takings in one bank deposit. The bank shows one line;
    the book shows several. This rule finds combinations of book items
    whose sum matches the bank amount.

    Bounded: combinations of up to MAX_MANY_TO_ONE_ITEMS items within
    a MANY_TO_ONE_DATE_WINDOW_DAYS date window.
    """
    matches = []
    used_a: set[str] = set()
    used_b: set[str] = set()

    for a in sorted(pool_a, key=lambda r: r.sort_key()):
        if a.row_id in used_a:
            continue

        eligible_b = [
            b for b in pool_b
            if b.row_id not in used_b
            and _direction_matches(a, b)
            and abs((b.date - a.date).days) <= MANY_TO_ONE_DATE_WINDOW_DAYS
        ]

        found = False
        for size in range(2, min(MAX_MANY_TO_ONE_ITEMS, len(eligible_b)) + 1):
            best_combo = None
            best_key = None
            for combo in combinations(eligible_b, size):
                total = sum(r.abs_amount for r in combo)
                if _amounts_within_tolerance(total, a.abs_amount,
                                             tolerance, tolerance_rel):
                    combo_key = (
                        abs(total - a.abs_amount),
                        tuple(sorted(r.sort_key() for r in combo)),
                    )
                    if best_key is None or combo_key < best_key:
                        best_combo = combo
                        best_key = combo_key

            if best_combo is not None:
                b_list = list(best_combo)
                diff = a.signed_amount - sum(r.signed_amount for r in b_list)
                matches.append(Match(
                    match_type=MatchType.MANY_TO_ONE,
                    side_a_records=[a],
                    side_b_records=b_list,
                    difference=diff,
                ))
                used_a.add(a.row_id)
                for b in b_list:
                    used_b.add(b.row_id)
                found = True
                break

        if found:
            continue

    return matches, used_a, used_b


def match(state: ReconciliationState, tolerance: Decimal = Decimal("0.02"),
          tolerance_rel: Decimal = Decimal("0")) -> ReconciliationState:
    """Apply the five matching rules in precedence order.

    Finance context: the core of any reconciliation is matching, pairing
    each transaction on one side with its counterpart on the other. Real
    data rarely matches perfectly: references may differ between systems,
    amounts may be off by a penny from rounding, or several book entries
    may represent one bank line (a batch deposit). The rules run
    strongest-first so that a certain match (same reference, exact
    amount) is never stolen by a weaker rule. Each matched pair is
    removed from the pool before the next rule runs. Whatever remains
    unmatched after all five rules becomes a break for the reviewer.

    Args:
        state: pipeline state with side_a and side_b record lists populated.
        tolerance: absolute amount difference allowed for within-tolerance
            matches (default 0.02).
        tolerance_rel: relative tolerance as a fraction of the larger
            amount (default 0, off). The effective bound is
            max(tolerance, tolerance_rel * max(|a|, |b|)).

    Returns:
        The state with matches populated and matched_count set. Unmatched
        records remain in side_a / side_b for the classify step.
    """
    pool_a = list(state.side_a)
    pool_b = list(state.side_b)
    all_matches: list[Match] = []
    all_used_a: set[str] = set()
    all_used_b: set[str] = set()

    rules = [
        ("Exact-key",          lambda pa, pb: _rule_1_exact(pa, pb)),
        ("Within-tolerance",   lambda pa, pb: _rule_2_tolerance(pa, pb, tolerance, tolerance_rel)),
        ("Amount-mismatch",    lambda pa, pb: _rule_3_amount_mismatch(pa, pb, tolerance, tolerance_rel)),
        ("Reference-mismatch", lambda pa, pb: _rule_4_reference_mismatch(pa, pb, tolerance, tolerance_rel)),
        ("Many-to-one",        lambda pa, pb: _rule_5_many_to_one(pa, pb, tolerance, tolerance_rel)),
    ]

    for rule_name, rule_fn in rules:
        remaining_a = [r for r in pool_a if r.row_id not in all_used_a]
        remaining_b = [r for r in pool_b if r.row_id not in all_used_b]

        new_matches, used_a, used_b = rule_fn(remaining_a, remaining_b)
        all_matches.extend(new_matches)
        all_used_a.update(used_a)
        all_used_b.update(used_b)

        if new_matches:
            print(f"  [OK] {rule_name}: {len(new_matches)} match(es)")

    unmatched_a = [r for r in pool_a if r.row_id not in all_used_a]
    unmatched_b = [r for r in pool_b if r.row_id not in all_used_b]

    if unmatched_a or unmatched_b:
        print(f"  [!]  Unmatched: {len(unmatched_a)} on side A, {len(unmatched_b)} on side B")

    all_matches.sort(key=lambda m: (
        m.match_type.value,
        m.side_a_records[0].date if m.side_a_records else m.side_b_records[0].date,
        m.side_a_records[0].reference if m.side_a_records else m.side_b_records[0].reference,
    ))

    state.matches = all_matches
    state.matched_count = sum(
        1 for m in all_matches
        if m.match_type in (MatchType.EXACT, MatchType.TOLERANCE)
        and m.side_a_records and m.side_b_records
    )

    return state
