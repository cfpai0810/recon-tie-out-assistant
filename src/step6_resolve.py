# =============================================================================
# step6_resolve.py: Layer 7: Break Resolution
# =============================================================================
#
# Responsibilities:
#   - resolve():                    present each open break for human decision
#   - load_locks():                 load locked resolutions from the JSON store
#   - save_locks():                 persist locked resolutions for re-runs
#   - compute_resolved_position():  compute the reconciled bottom line
#
# Core rule: every resolution is a human decision. The adjustment does
# not reach the position until the reviewer confirms it.
#
# Knows about: breaks, resolution types, Decimal arithmetic, the lock store
# Does NOT know about: matching rules, the AI drafting, the PDF
# =============================================================================

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from src.state import (
    Break, ReconciliationState, Resolution, ResolutionType,
)


def _figures_hash(brk: Break) -> str:
    raw = f"{brk.side_a_amount:.2f}|{brk.side_b_amount:.2f}|{brk.difference:.2f}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _resolution_from_choice(choice: str, text: str,
                            adjustment_amount: float | None,
                            brk: Break) -> Resolution | None:
    now = datetime.now(timezone.utc).isoformat()
    fh = _figures_hash(brk)

    if choice == "T":
        return Resolution(
            resolution_type=ResolutionType.ACCEPT_AS_TIMING,
            reviewer_text=text,
            timestamp=now,
            figures_hash=fh,
        )
    elif choice == "A":
        if adjustment_amount is None:
            return None
        adj = (adjustment_amount if isinstance(adjustment_amount, Decimal)
               else Decimal(str(adjustment_amount)))
        return Resolution(
            resolution_type=ResolutionType.ADJUST,
            reviewer_text=text,
            adjustment_amount=adj,
            adjustment_side="B",
            timestamp=now,
            figures_hash=fh,
        )
    elif choice == "D":
        return Resolution(
            resolution_type=ResolutionType.DISMISS,
            reviewer_text=text,
            timestamp=now,
            figures_hash=fh,
        )
    elif choice == "I":
        return Resolution(
            resolution_type=ResolutionType.INVESTIGATE,
            reviewer_text=text,
            timestamp=now,
            figures_hash=fh,
        )
    elif choice == "C":
        return Resolution(
            resolution_type=ResolutionType.CONFIRM_MATCH,
            reviewer_text=text,
            timestamp=now,
            figures_hash=fh,
        )
    return None


def _lock_store_path(recon_id: str, output_dir: str | Path = "output") -> Path:
    d = Path(output_dir) / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"locks_{recon_id}.json"


def load_locks(recon_id: str, output_dir: str | Path = "output") -> dict[str, Resolution]:
    """Load locked resolutions from the JSON store.

    Finance context: lock persistence is how the tool remembers prior
    review decisions. When a reconciliation is re-run with updated data,
    the lock store lets the tool skip breaks that have already been
    reviewed and whose figures have not changed. A stale lock (where the
    underlying figures moved) is flagged for re-review rather than
    silently reapplied.
    """
    p = _lock_store_path(recon_id, output_dir)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as fh:
        data = json.load(fh)
    locks = {}
    for key, entry in data.items():
        adj = entry.get("adjustment_amount")
        locks[key] = Resolution(
            resolution_type=ResolutionType(entry["resolution_type"]),
            reviewer_text=entry["reviewer_text"],
            adjustment_amount=Decimal(str(adj)) if adj is not None else None,
            adjustment_side=entry.get("adjustment_side"),
            timestamp=entry["timestamp"],
            figures_hash=entry["figures_hash"],
        )
    return locks


def save_locks(recon_id: str, resolutions: dict[str, Resolution],
               output_dir: str | Path = "output") -> Path:
    """Save locked resolutions to the JSON store.

    Finance context: only definitive resolutions (timing, adjust,
    dismiss, confirm match) are persisted. Investigate items are
    deliberately excluded because they represent open questions that
    should be re-presented on the next run.
    """
    p = _lock_store_path(recon_id, output_dir)
    data = {}
    for key, res in resolutions.items():
        if res.resolution_type == ResolutionType.INVESTIGATE:
            continue
        data[key] = {
            "resolution_type": res.resolution_type.value,
            "reviewer_text": res.reviewer_text,
            "adjustment_amount": (float(res.adjustment_amount)
                                  if res.adjustment_amount is not None else None),
            "adjustment_side": res.adjustment_side,
            "timestamp": res.timestamp,
            "figures_hash": res.figures_hash,
        }
    with p.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return p


def compute_resolved_position(state: ReconciliationState) -> Decimal:
    """Compute the resolved position from the raw difference and resolutions.

    Finance context: the resolved position is the reconciliation's
    bottom line. It starts at the raw difference and is adjusted by each
    resolution: timing differences stand (they are disclosed but not
    removed), adjustments and dismissals reduce the difference, and
    items under investigation stay open. A fully reconciled position of
    zero (or equal to timing plus tolerance) means every penny is
    accounted for.
    """
    def _dec(v):
        return v if isinstance(v, Decimal) else Decimal(str(v))

    position = _dec(state.raw_difference)

    for brk in state.breaks:
        key = brk.stable_break_key
        res = state.resolutions.get(key)
        if res is None:
            continue

        diff = _dec(brk.difference)
        if res.resolution_type == ResolutionType.ACCEPT_AS_TIMING:
            pass
        elif res.resolution_type == ResolutionType.ADJUST:
            if res.adjustment_amount is not None:
                position -= diff
        elif res.resolution_type == ResolutionType.DISMISS:
            position -= diff
        elif res.resolution_type == ResolutionType.CONFIRM_MATCH:
            position -= diff
        elif res.resolution_type == ResolutionType.INVESTIGATE:
            pass

    return round(position, 2)


def resolve(state: ReconciliationState, *, ask: Callable, confirm: Callable,
            output_dir: str | Path = "output") -> ReconciliationState:
    """Run the resolution loop over open breaks.

    Finance context: resolution is where a human applies judgement. The
    five actions map to standard accounting treatments: Timing (the
    item is a genuine timing difference that will clear next period,
    it stands in the net difference and is disclosed), Adjust (the
    reviewer posts a correcting entry, the adjustment is removed from
    the net difference), Investigate (the item needs follow-up, it
    stays open and is flagged), Dismiss (the item is immaterial or
    explained, its contribution is removed). The resolved position is
    the number the controller signs off: the raw difference minus
    adjustments and dismissals, equalling the sum of timing
    differences plus tolerance absorbed in clean matches. Locked
    resolutions from prior runs are reproduced automatically (matched
    by the break's content hash), so a re-run only asks about new or
    changed breaks.

    Args:
        state: pipeline state with breaks populated.
        ask: callback that presents a break to the reviewer and returns
            (choice, text, adjustment_amount) where choice is one of
            T (timing), A (adjust), D (dismiss), I (investigate),
            C (confirm match, for many-to-one suggestions).
        confirm: callback that asks the reviewer to confirm an adjusting
            entry; returns True to proceed, False to downgrade to
            Investigate.
        output_dir: directory for the lock store (default "output").

    Returns:
        The state with resolutions populated, resolved_position computed,
        and locks persisted to the JSON store.
    """

    existing_locks = load_locks(state.reconciliation_id, output_dir)
    current_break_keys = {b.stable_break_key for b in state.breaks}

    for key, lock in existing_locks.items():
        if key not in current_break_keys:
            print(f"  [!]  Orphaned lock: {key} (break no longer exists)")
            continue

        brk = next(b for b in state.breaks if b.stable_break_key == key)
        current_fh = _figures_hash(brk)
        if current_fh != lock.figures_hash:
            print(
                f"  [!]  STALE LOCK on {key}: figures changed since "
                f"resolution on {lock.timestamp}. Re-review required."
            )
            continue

        state.resolutions[key] = lock
        print(f"  [OK] Reproduced lock: {key} ({lock.resolution_type.value})")

    open_breaks = [
        b for b in state.breaks
        if b.stable_break_key not in state.resolutions
    ]

    if not open_breaks:
        print("  [OK] No open breaks to resolve")
    else:
        print(f"  [--] {len(open_breaks)} break(s) to resolve")

    for brk in open_breaks:
        choice, text, adj_amount = ask(brk, state.drafts)
        res = _resolution_from_choice(choice, text, adj_amount, brk)
        if res is None:
            continue

        if res.resolution_type == ResolutionType.ADJUST and res.adjustment_amount is not None:
            desc = (
                f"Adjust {res.adjustment_side} by {res.adjustment_amount:,.2f}: {text}"
            )
            if not confirm(desc):
                res = Resolution(
                    resolution_type=ResolutionType.INVESTIGATE,
                    reviewer_text="Entry not confirmed, left for investigation",
                    timestamp=res.timestamp,
                    figures_hash=res.figures_hash,
                )

        state.resolutions[brk.stable_break_key] = res

    state.resolved_position = compute_resolved_position(state)

    locked = sum(
        1 for r in state.resolutions.values()
        if r.resolution_type != ResolutionType.INVESTIGATE
    )
    investigated = sum(
        1 for r in state.resolutions.values()
        if r.resolution_type == ResolutionType.INVESTIGATE
    )

    save_locks(state.reconciliation_id, state.resolutions, output_dir)

    print(f"  [OK] Resolved position: {state.resolved_position:>12,.2f}")
    print(f"       Locked: {locked}, Under investigation: {investigated}")

    return state
