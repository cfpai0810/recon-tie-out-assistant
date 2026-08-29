# =============================================================================
# step2_validate.py: Layer 3: Data Validation
# =============================================================================
#
# Responsibilities:
#   - validate():          check both record sets and collect row-level problems
#
# Core rule: validation never rejects a record. It adds warnings that
# travel with the state and appear in the review.
#
# Knows about: record shape, what makes a reference useful for matching
# Does NOT know about: matching logic, break types, resolutions, the AI, the PDF
# =============================================================================

from __future__ import annotations

from src.state import Problem, ReconciliationState


def validate(state: ReconciliationState) -> ReconciliationState:
    """Validate records and collect row-level problems.

    Finance context: a reconciliation is only as good as its inputs. A
    blank reference forces the matcher to rely on amount and date alone,
    which is weaker and can produce false matches. An empty side means
    one dataset failed to load entirely. This layer reports these issues
    as Problems without blocking the pipeline, so the reviewer sees the
    warnings alongside the results. These warnings appear in the break
    cards so the reviewer understands why a match may be uncertain.
    """
    new_problems: list[Problem] = []

    for label, records in [("A", state.side_a), ("B", state.side_b)]:
        for r in records:
            if not r.reference:
                new_problems.append(Problem(
                    side=label,
                    row_number=int(r.row_id.split("-")[-1]),
                    column="reference",
                    message="Blank reference (matching will use amount and date)",
                ))

    if not state.side_a:
        new_problems.append(Problem(
            side="A", row_number=0,
            column="(all)", message="Side A has no records",
        ))
    if not state.side_b:
        new_problems.append(Problem(
            side="B", row_number=0,
            column="(all)", message="Side B has no records",
        ))

    state.problems.extend(new_problems)

    ok_count = len(state.side_a) + len(state.side_b)
    prob_count = len(new_problems)
    print(f"  [OK] Validated {ok_count} records, {prob_count} new issue(s)")

    return state
