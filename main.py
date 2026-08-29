"""Reconciliation and Tie-Out Assistant: CLI orchestrator.

Runs the seven-phase pipeline: load, validate, match, classify, narrate,
resolve, record.  Each phase reads the ReconciliationState and returns it
enriched; main.py wires them together.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from decimal import Decimal

from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS, TOLERANCE_REL, OUTPUT_DIR
from src.state import Break, BreakType
from src.step1_load import load
from src.step2_validate import validate
from src.step3_match import match
from src.step4_classify import classify
from src.step5_narrate import narrate
from src.step6_resolve import resolve
from src.step7_record import record


def _cli_ask(brk, drafts):
    """Interactive prompt for the CLI resolve loop."""
    print(f"\n{'=' * 60}")
    print(f"Break: {brk.break_type.value}")
    if brk.side_a_records:
        for r in brk.side_a_records:
            print(f"  {r.side}: {r.date}  {r.reference:20s}  {r.signed_amount:>12,.2f}  {r.description}")
    if brk.side_b_records:
        for r in brk.side_b_records:
            print(f"  {r.side}: {r.date}  {r.reference:20s}  {r.signed_amount:>12,.2f}  {r.description}")
    print(f"  Difference: {brk.difference:>12,.2f}")

    draft = drafts.get(brk.stable_break_key)
    if draft:
        print(f"\n  AI explanation: {draft.explanation}")
        print(f"  Suggested follow-up: {draft.follow_up}")

    if brk.break_type == BreakType.MANY_TO_ONE_SUGGESTION:
        print("\n  ** MATCH SUGGESTION: review the pairing above. **")
        print("\nOptions:")
        print("  [C] Confirm match")
        print("  [R] Reject (items become individual unmatched breaks)")
        choice = input("\nChoose [C/R]: ").strip().upper()
        text = input("Reason / note: ").strip()
        if not text:
            text = ("Many-to-one match confirmed by reviewer"
                    if choice == "C"
                    else "Many-to-one suggestion rejected")
        return choice, text, None

    print("\nResolution options:")
    print("  [T] Accept as timing difference")
    print("  [A] Adjust (book an adjusting entry)")
    print("  [I] Investigate (leave open)")
    print("  [D] Dismiss as immaterial")
    choice = input("\nChoose [T/A/I/D]: ").strip().upper()
    text = input("Reason / note: ").strip()

    adjustment_amount = None
    if choice == "A":
        amt = input("Adjustment amount (signed, posts to cash book): ").strip()
        try:
            adjustment_amount = float(amt)
        except ValueError:
            print("  Invalid amount, defaulting to investigate.")
            choice = "I"

    return choice, text, adjustment_amount


def _cli_confirm(entry_description: str) -> bool:
    """Confirm an adjusting entry before locking."""
    print(f"\n  Proposed entry: {entry_description}")
    answer = input("  Confirm this entry? [Y/N]: ").strip().upper()
    return answer == "Y"


def main():
    print("=" * 60)
    print("RECONCILIATION AND TIE-OUT ASSISTANT")
    print("=" * 60)

    file_a = str(BANK_STATEMENT_FILE)
    file_b = str(CASH_BOOK_FILE)

    print(f"\nSide A: {Path(file_a).name}")
    print(f"Side B: {Path(file_b).name}")

    print("\n--- Phase 1: LOAD ---")
    state = load(file_a, file_b)

    print("\n--- Phase 2: VALIDATE ---")
    state = validate(state)

    print("\n--- Phase 3: MATCH ---")
    state = match(state, tolerance=TOLERANCE_ABS, tolerance_rel=TOLERANCE_REL)

    print("\n--- Phase 4: CLASSIFY ---")
    state = classify(state)

    print("\n--- Phase 5: NARRATE ---")
    state = narrate(state)

    print("\n--- Phase 6: RESOLVE ---")
    state = resolve(state, ask=_cli_ask, confirm=_cli_confirm,
                    output_dir=OUTPUT_DIR)

    rejected_m2o = [
        b for b in state.breaks
        if b.break_type == BreakType.MANY_TO_ONE_SUGGESTION
        and b.stable_break_key not in state.resolutions
    ]
    if rejected_m2o:
        print(f"\n  [--] Decomposing {len(rejected_m2o)} rejected "
              f"many-to-one suggestion(s) into individual items")
        new_breaks = []
        rejected_keys = {b.stable_break_key for b in rejected_m2o}
        for brk in state.breaks:
            if brk.stable_break_key in rejected_keys:
                for r in brk.side_a_records:
                    new_breaks.append(Break(
                        break_type=BreakType.UNMATCHED,
                        side_a_records=[r], side_b_records=[],
                        side_a_amount=r.signed_amount,
                        side_b_amount=Decimal("0"),
                        difference=r.signed_amount,
                    ))
                for r in brk.side_b_records:
                    new_breaks.append(Break(
                        break_type=BreakType.UNMATCHED,
                        side_a_records=[], side_b_records=[r],
                        side_a_amount=Decimal("0"),
                        side_b_amount=r.signed_amount,
                        difference=round(-r.signed_amount, 2),
                    ))
            else:
                new_breaks.append(brk)
        state.breaks = new_breaks
        state = resolve(state, ask=_cli_ask, confirm=_cli_confirm,
                        output_dir=OUTPUT_DIR)

    print("\n--- Phase 7: RECORD ---")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state = record(state, output_dir=OUTPUT_DIR,
                   tolerance_abs=float(TOLERANCE_ABS),
                   tolerance_rel=float(TOLERANCE_REL))

    print("\n" + "=" * 60)
    print("Pipeline complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
