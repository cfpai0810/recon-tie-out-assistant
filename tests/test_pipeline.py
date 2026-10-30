"""Tests for the reconciliation pipeline.

Covers: data loading, validation, all five matching rules, tie-breaks,
classification, totals, break identification, determinism, the narrate
fallback, all five resolution actions, lock persistence, the re-run guard,
and the audit/report output.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.state import (
    Break, BreakType, Direction, Draft, Match, MatchType,
    Record, ReconciliationState, Resolution, ResolutionType,
)


def _make_record(side, row, dt, ref, amount, desc=""):
    signed = Decimal(str(amount))
    return Record(
        row_id=f"{side}-{row}",
        side=side,
        date=dt if isinstance(dt, date) else date.fromisoformat(dt),
        reference=ref,
        description=desc,
        signed_amount=signed,
        abs_amount=abs(signed),
        direction=Direction.MONEY_IN if signed >= 0 else Direction.MONEY_OUT,
    )


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "reference", "amount", "description"])
        for r in rows:
            w.writerow(r)


# ── LOAD ──────────────────────────────────────────────────────────────

class TestLoad:
    def test_load_sample_data(self):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE
        from src.step1_load import load
        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        assert len(state.side_a) == 17
        assert len(state.side_b) == 20
        assert state.reconciliation_id

    def test_load_normalises_amounts(self):
        from config import BANK_STATEMENT_FILE
        from src.step1_load import load
        state = load(str(BANK_STATEMENT_FILE), str(BANK_STATEMENT_FILE))
        for r in state.side_a:
            assert r.abs_amount >= 0
            if r.signed_amount >= 0:
                assert r.direction == Direction.MONEY_IN
            else:
                assert r.direction == Direction.MONEY_OUT

    def test_load_dirty_amounts(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dirty.csv"
            _write_csv(p, [
                ("2026-06-01", "REF-1", "$1,234.56", "With currency symbol"),
                ("2026-06-02", "REF-2", "-999.00", "Negative"),
            ])
            state = load(str(p), str(p))
            amounts = {r.reference: r.signed_amount for r in state.side_a}
            assert amounts["REF-1"] == Decimal("1234.56")
            assert amounts["REF-2"] == Decimal("-999")

    def test_load_missing_column(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.csv"
            with open(p, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["date", "amount"])
                w.writerow(["2026-06-01", "100"])
            with pytest.raises(ValueError, match="missing required column"):
                load(str(p), str(p))

    def test_load_bad_date_collected_as_problem(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "baddate.csv"
            _write_csv(p, [
                ("not-a-date", "REF-1", "100.00", "Bad date"),
                ("2026-06-01", "REF-2", "200.00", "Good"),
            ])
            state = load(str(p), str(p))
            assert len(state.problems) == 2  # once per side
            assert len(state.side_a) == 1


# ── VALIDATE ──────────────────────────────────────────────────────────

class TestValidate:
    def test_blank_reference_flagged(self):
        from src.step2_validate import validate
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "", 100)]
        state.side_b = []
        state = validate(state)
        assert any(p.column == "reference" for p in state.problems)

    def test_empty_sides_flagged(self):
        from src.step2_validate import validate
        state = ReconciliationState()
        state = validate(state)
        assert any("no records" in p.message for p in state.problems)


# ── MATCH: EXACT ──────────────────────────────────────────────────────

class TestMatchExact:
    def test_exact_match(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", 100)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", 100)]
        state = match(state)
        assert len(state.matches) == 1
        assert state.matches[0].match_type == MatchType.EXACT

    def test_exact_match_requires_direction(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", 100)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", -100)]
        state = match(state)
        exact = [m for m in state.matches if m.match_type == MatchType.EXACT]
        assert len(exact) == 0


# ── MATCH: TOLERANCE ─────────────────────────────────────────────────

class TestMatchTolerance:
    def test_tolerance_match(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", -100.01)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", -100.00)]
        state = match(state, tolerance=0.02)
        assert len(state.matches) == 1
        assert state.matches[0].match_type == MatchType.TOLERANCE
        assert abs(state.matches[0].difference) == Decimal("0.01")

    def test_beyond_tolerance_is_amount_mismatch(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", -100.10)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", -100.00)]
        state = match(state, tolerance=0.02)
        amt = [m for m in state.matches if m.match_type == MatchType.AMOUNT_MISMATCH]
        assert len(amt) == 1


# ── MATCH: AMOUNT MISMATCH ───────────────────────────────────────────

class TestMatchAmountMismatch:
    def test_amount_mismatch_same_ref(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "INV-42", -3450)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "INV-42", -3500)]
        state = match(state, tolerance=0.02)
        assert state.matches[0].match_type == MatchType.AMOUNT_MISMATCH
        assert state.matches[0].difference == Decimal("50")


# ── MATCH: REFERENCE MISMATCH ────────────────────────────────────────

class TestMatchReferenceMismatch:
    def test_reference_mismatch_same_amount(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "TRF-8837", 1200)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "TRF-8873", 1200)]
        state = match(state, tolerance=0.02)
        ref = [m for m in state.matches if m.match_type == MatchType.REFERENCE_MISMATCH]
        assert len(ref) == 1

    def test_precedence_exact_over_ref_mismatch(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", 500)]
        state.side_b = [
            _make_record("B", 1, "2026-06-01", "REF-1", 500),
            _make_record("B", 2, "2026-06-01", "REF-X", 500),
        ]
        state = match(state)
        exact = [m for m in state.matches if m.match_type == MatchType.EXACT]
        assert len(exact) == 1
        assert exact[0].side_b_records[0].reference == "REF-1"


# ── MATCH: MANY-TO-ONE ───────────────────────────────────────────────

class TestMatchManyToOne:
    def test_many_to_one_basic(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-19", "DEP-BATCH", 1500)]
        state.side_b = [
            _make_record("B", 1, "2026-06-18", "DEP-101", 500),
            _make_record("B", 2, "2026-06-18", "DEP-102", 750),
            _make_record("B", 3, "2026-06-19", "DEP-103", 250),
        ]
        state = match(state, tolerance=0.02)
        m2o = [m for m in state.matches if m.match_type == MatchType.MANY_TO_ONE]
        assert len(m2o) == 1
        assert len(m2o[0].side_b_records) == 3
        assert m2o[0].difference == Decimal("0")

    def test_many_to_one_near_miss_no_match(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-19", "DEP-BATCH", 1500)]
        state.side_b = [
            _make_record("B", 1, "2026-06-18", "DEP-101", 500),
            _make_record("B", 2, "2026-06-18", "DEP-102", 750),
            _make_record("B", 3, "2026-06-19", "DEP-103", 251),
        ]
        state = match(state, tolerance=0.02)
        m2o = [m for m in state.matches if m.match_type == MatchType.MANY_TO_ONE]
        assert len(m2o) == 0

    def test_many_to_one_direction_must_match(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-19", "DEP-BATCH", 1500)]
        state.side_b = [
            _make_record("B", 1, "2026-06-18", "DEP-101", -500),
            _make_record("B", 2, "2026-06-18", "DEP-102", -750),
            _make_record("B", 3, "2026-06-19", "DEP-103", -250),
        ]
        state = match(state, tolerance=0.02)
        m2o = [m for m in state.matches if m.match_type == MatchType.MANY_TO_ONE]
        assert len(m2o) == 0

    def test_many_to_one_date_window(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-19", "DEP-BATCH", 1500)]
        state.side_b = [
            _make_record("B", 1, "2026-05-01", "DEP-101", 500),
            _make_record("B", 2, "2026-05-01", "DEP-102", 750),
            _make_record("B", 3, "2026-05-01", "DEP-103", 250),
        ]
        state = match(state, tolerance=0.02)
        m2o = [m for m in state.matches if m.match_type == MatchType.MANY_TO_ONE]
        assert len(m2o) == 0


# ── MATCH: DETERMINISM ───────────────────────────────────────────────

class TestMatchDeterminism:
    def test_same_input_same_output(self):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
        from src.step1_load import load
        from src.step3_match import match

        state1 = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state1 = match(state1, tolerance=TOLERANCE_ABS)
        state2 = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state2 = match(state2, tolerance=TOLERANCE_ABS)

        assert len(state1.matches) == len(state2.matches)
        for m1, m2 in zip(state1.matches, state2.matches):
            assert m1.match_type == m2.match_type
            a1 = [r.row_id for r in m1.side_a_records]
            a2 = [r.row_id for r in m2.side_a_records]
            assert a1 == a2

    def test_tie_break_closest_date(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-15", "REF-1", 100)]
        state.side_b = [
            _make_record("B", 1, "2026-06-01", "REF-1", 100),
            _make_record("B", 2, "2026-06-14", "REF-1", 100),
        ]
        state = match(state)
        exact = [m for m in state.matches if m.match_type == MatchType.EXACT]
        assert len(exact) == 1
        assert exact[0].side_b_records[0].row_id == "B-2"


# ── CLASSIFY ─────────────────────────────────────────────────────────

class TestClassify:
    def _run_pipeline(self):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state = validate(state)
        state = match(state, tolerance=TOLERANCE_ABS)
        return classify(state)

    def test_totals(self):
        state = self._run_pipeline()
        assert state.side_a_total == Decimal("-1219.51")
        assert state.side_b_total == Decimal("161.65")
        assert state.raw_difference == Decimal("-1381.16")

    def test_break_count(self):
        state = self._run_pipeline()
        assert len(state.breaks) == 8

    def test_break_types(self):
        state = self._run_pipeline()
        types = {b.break_type for b in state.breaks}
        assert BreakType.AMOUNT_MISMATCH in types
        assert BreakType.MANY_TO_ONE_SUGGESTION in types
        assert BreakType.REFERENCE_MISMATCH in types
        assert BreakType.UNMATCHED in types

    def test_breaks_explain_difference(self):
        state = self._run_pipeline()
        break_total = sum(b.difference for b in state.breaks)
        tol_absorbed = sum(
            (m.difference for m in state.matches
             if m.match_type == MatchType.TOLERANCE), Decimal("0")
        )
        assert break_total + tol_absorbed == state.raw_difference

    def test_clean_rec_no_breaks(self):
        from src.step3_match import match
        from src.step4_classify import classify
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", 500)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", 500)]
        state = match(state)
        state = classify(state)
        assert len(state.breaks) == 0
        assert state.raw_difference == Decimal("0")

    def test_stable_break_key_content_based(self):
        state = self._run_pipeline()
        keys = [b.stable_break_key for b in state.breaks]
        assert len(keys) == len(set(keys))
        for k in keys:
            assert len(k) == 16

    def test_unmatched_bank_charge(self):
        state = self._run_pipeline()
        fee = [b for b in state.breaks if
               b.side_a_records and b.side_a_records[0].reference == "CHG-Q2"]
        assert len(fee) == 1
        assert fee[0].break_type == BreakType.UNMATCHED
        assert fee[0].difference == Decimal("-30")

    def test_unmatched_cheque(self):
        state = self._run_pipeline()
        chq = [b for b in state.breaks if
               b.side_b_records and b.side_b_records[0].reference == "CHQ-1043"]
        assert len(chq) == 1
        assert chq[0].break_type == BreakType.UNMATCHED
        assert chq[0].difference == Decimal("1275")


# ── NARRATE ──────────────────────────────────────────────────────────

class TestNarrate:
    def _classified_state(self):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state = validate(state)
        state = match(state, tolerance=TOLERANCE_ABS)
        return classify(state)

    def test_templated_fallback_no_client(self):
        from src.step5_narrate import narrate
        state = self._classified_state()
        state = narrate(state, client=None)
        assert len(state.drafts) == len(state.breaks)
        for key, draft in state.drafts.items():
            assert draft.explanation
            assert draft.follow_up

    def test_amount_mismatch_draft(self):
        from src.step5_narrate import narrate
        state = self._classified_state()
        state = narrate(state, client=None)
        inv_brk = [b for b in state.breaks if
                   b.break_type == BreakType.AMOUNT_MISMATCH][0]
        draft = state.drafts[inv_brk.stable_break_key]
        assert "72" in draft.explanation

    def test_unmatched_timing_hint(self):
        from src.step5_narrate import narrate
        state = self._classified_state()
        state = narrate(state, client=None)
        chq_brk = [b for b in state.breaks if
                   b.side_b_records and
                   b.side_b_records[0].reference == "CHQ-1043"][0]
        draft = state.drafts[chq_brk.stable_break_key]
        assert "timing" in draft.explanation.lower()


# ── RESOLVE ──────────────────────────────────────────────────────────

class TestResolve:
    def _narrated_state(self):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step5_narrate import narrate
        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state = validate(state)
        state = match(state, tolerance=TOLERANCE_ABS)
        state = classify(state)
        return narrate(state)

    def test_all_five_actions(self):
        from src.step6_resolve import resolve
        state = self._narrated_state()
        answers = iter([
            ("A", "Adjust", 72.0),
            ("C", "Confirmed batch", None),
            ("D", "Immaterial", None),
            ("I", "Investigate charge", None),
            ("T", "Timing cheque", None),
            ("T", "Timing deposit", None),
            ("T", "Timing card takings", None),
            ("A", "Book interest", 12.15),
        ])
        state = resolve(
            state,
            ask=lambda b, d: next(answers),
            confirm=lambda e: True,
            output_dir=tempfile.mkdtemp(),
        )
        assert len(state.resolutions) == 8
        types = {r.resolution_type for r in state.resolutions.values()}
        assert ResolutionType.ADJUST in types
        assert ResolutionType.CONFIRM_MATCH in types
        assert ResolutionType.DISMISS in types
        assert ResolutionType.ACCEPT_AS_TIMING in types
        assert ResolutionType.INVESTIGATE in types

    def test_investigate_leaves_open(self):
        from src.step6_resolve import resolve
        state = self._narrated_state()
        state = resolve(
            state,
            ask=lambda b, d: ("I", "Need more info", None),
            confirm=lambda e: True,
            output_dir=tempfile.mkdtemp(),
        )
        for res in state.resolutions.values():
            assert res.resolution_type == ResolutionType.INVESTIGATE

    def test_resolved_position_ties_out(self):
        """The single most important correctness test: net-difference
        arithmetic ties out after a mixed set of resolutions."""
        from src.step6_resolve import resolve
        state = self._narrated_state()
        answers = iter([
            ("A", "Adjust", 72.0),         # amount mismatch diff=72
            ("C", "Confirmed batch", None), # many-to-one diff=0
            ("D", "Dismiss", None),        # ref mismatch diff=0
            ("A", "Book charge", -30.0),   # CHG-Q2 diff=-30
            ("T", "Timing", None),         # CHQ-1043 diff=1275
            ("T", "Timing", None),         # RCT-DEL diff=-450
            ("T", "Timing", None),         # RCT-0629 diff=-2260.30
            ("A", "Book interest", 12.15), # INT-JUN diff=12.15
        ])
        state = resolve(
            state,
            ask=lambda b, d: next(answers),
            confirm=lambda e: True,
            output_dir=tempfile.mkdtemp(),
        )
        # Adjust removes: 72 + (-30) + 12.15 = 54.15
        # Confirm removes: 0
        # Dismiss removes: 0
        # Timing stands: 1275 + (-450) + (-2260.30) = -1435.30
        # Resolved = -1381.16 - 72 - 0 - 0 - (-30) - 12.15 = -1435.31
        assert state.resolved_position == Decimal("-1435.31")

        timing_total = sum(
            b.difference for b in state.breaks
            if state.resolutions[b.stable_break_key].resolution_type
               == ResolutionType.ACCEPT_AS_TIMING
        )
        from src.state import MatchType
        tol = sum((m.difference for m in state.matches
                   if m.match_type == MatchType.TOLERANCE), Decimal("0"))
        assert state.resolved_position == timing_total + tol

    def test_unconfirmed_adjust_becomes_investigate(self):
        from src.step6_resolve import resolve
        state = self._narrated_state()
        state = resolve(
            state,
            ask=lambda b, d: ("A", "Try adjust", 50.0),
            confirm=lambda e: False,
            output_dir=tempfile.mkdtemp(),
        )
        for res in state.resolutions.values():
            assert res.resolution_type == ResolutionType.INVESTIGATE


# ── LOCK PERSISTENCE ─────────────────────────────────────────────────

class TestLockPersistence:
    def _resolve_all(self, output_dir):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step5_narrate import narrate
        from src.step6_resolve import resolve

        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state = validate(state)
        state = match(state, tolerance=TOLERANCE_ABS)
        state = classify(state)
        state = narrate(state)

        answers = iter([
            ("A", "Adjust", 72.0),
            ("C", "Confirmed batch", None),
            ("D", "Dismiss", None),
            ("A", "Book charge", -30.0),
            ("T", "Timing cheque", None),
            ("T", "Timing deposit", None),
            ("T", "Timing card takings", None),
            ("A", "Book interest", 12.15),
        ])
        return resolve(
            state,
            ask=lambda b, d: next(answers),
            confirm=lambda e: True,
            output_dir=output_dir,
        )

    def test_locks_saved_and_loaded(self):
        from src.step6_resolve import load_locks
        tmp = tempfile.mkdtemp()
        state = self._resolve_all(tmp)
        locks = load_locks(state.reconciliation_id, tmp)
        assert len(locks) >= 3  # investigate not saved

    def test_rerun_reproduces_locks(self):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step5_narrate import narrate
        from src.step6_resolve import resolve

        tmp = tempfile.mkdtemp()
        state1 = self._resolve_all(tmp)

        state2 = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state2 = validate(state2)
        state2 = match(state2, tolerance=TOLERANCE_ABS)
        state2 = classify(state2)
        state2 = narrate(state2)

        # On re-run, no ask should be called since all breaks are locked
        state2 = resolve(
            state2,
            ask=lambda b, d: pytest.fail("Should not be called"),
            confirm=lambda e: True,
            output_dir=tmp,
        )
        assert len(state2.resolutions) == len(state1.resolutions)
        assert state2.resolved_position == state1.resolved_position


# ── RECORD ───────────────────────────────────────────────────────────

class TestRecord:
    def _full_pipeline(self, output_dir):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step5_narrate import narrate
        from src.step6_resolve import resolve
        from src.step7_record import record

        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state = validate(state)
        state = match(state, tolerance=TOLERANCE_ABS)
        state = classify(state)
        state = narrate(state)
        state = resolve(
            state,
            ask=lambda b, d: ("T", "Timing", None),
            confirm=lambda e: True,
            output_dir=output_dir,
        )
        return record(state, output_dir=output_dir)

    def test_audit_record_written(self):
        tmp = tempfile.mkdtemp()
        self._full_pipeline(tmp)
        audit_path = Path(tmp) / "audit_log.jsonl"
        assert audit_path.exists()
        with audit_path.open() as fh:
            data = json.loads(fh.readline())
        assert data["reconciliation_id"]
        assert data["raw_difference"] == pytest.approx(-1381.16)
        assert len(data["breaks"]) == 8

    def test_pdf_report_written(self):
        tmp = tempfile.mkdtemp()
        self._full_pipeline(tmp)
        pdfs = list(Path(tmp).glob("reconciliation_report_*.pdf"))
        assert len(pdfs) == 1
        assert pdfs[0].stat().st_size > 1000

    def test_pdf_bytes_builder(self):
        from src.step7_record import build_pdf_bytes
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state = validate(state)
        state = match(state, tolerance=TOLERANCE_ABS)
        state = classify(state)
        pdf = build_pdf_bytes(state)
        assert pdf[:5] == b"%PDF-"


# ── END TO END ───────────────────────────────────────────────────────

class TestEndToEnd:
    def test_full_pipeline_no_crash(self):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step5_narrate import narrate
        from src.step6_resolve import resolve
        from src.step7_record import record

        tmp = tempfile.mkdtemp()
        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state = validate(state)
        state = match(state, tolerance=TOLERANCE_ABS)
        state = classify(state)
        state = narrate(state)
        state = resolve(
            state,
            ask=lambda b, d: ("I", "Skip", None),
            confirm=lambda e: True,
            output_dir=tmp,
        )
        state = record(state, output_dir=tmp)

        assert state.side_a_total == Decimal("-1219.51")
        assert state.side_b_total == Decimal("161.65")
        assert state.raw_difference == Decimal("-1381.16")
        assert len(state.breaks) == 8
        assert state.matched_count == 12

    def test_clean_reconciliation(self):
        """A reconciliation with no breaks produces zero difference."""
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step5_narrate import narrate
        from src.step6_resolve import resolve
        from src.step7_record import record

        with tempfile.TemporaryDirectory() as tmp:
            a_path = Path(tmp) / "a.csv"
            b_path = Path(tmp) / "b.csv"
            _write_csv(a_path, [
                ("2026-06-01", "REF-1", "100.00", "Item 1"),
                ("2026-06-02", "REF-2", "-50.00", "Item 2"),
            ])
            _write_csv(b_path, [
                ("2026-06-01", "REF-1", "100.00", "Item 1"),
                ("2026-06-02", "REF-2", "-50.00", "Item 2"),
            ])
            state = load(str(a_path), str(b_path))
            state = validate(state)
            state = match(state)
            state = classify(state)
            state = narrate(state)
            state = resolve(
                state,
                ask=lambda b, d: pytest.fail("No breaks expected"),
                confirm=lambda e: True,
                output_dir=tmp,
            )
            state = record(state, output_dir=tmp)

            assert state.raw_difference == Decimal("0")
            assert len(state.breaks) == 0
            assert state.resolved_position == Decimal("0")


# ── DIRTY AMOUNTS ───────────────────────────────────────────────────

class TestDirtyAmounts:
    """Amount coercion for messy real-world inputs."""

    def test_currency_dollar(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("$1,234.56") == Decimal("1234.56")

    def test_currency_euro_comma_decimal(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("€1.234,56") == Decimal("1234.56")

    def test_currency_pound(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("£1,234.56") == Decimal("1234.56")

    def test_parentheses_negative(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("(1,234.56)") == Decimal("-1234.56")

    def test_trailing_minus(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("1,234.56-") == Decimal("-1234.56")

    def test_comma_decimal_no_thousands(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("1234,56") == Decimal("1234.56")

    def test_european_thousands_and_decimal(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("1.234,56") == Decimal("1234.56")

    def test_spaces_as_thousands(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("1 234.56") == Decimal("1234.56")

    def test_thousands_comma_only(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("1,234") == Decimal("1234")

    def test_multiple_thousands_commas(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("1,234,567") == Decimal("1234567")

    def test_european_thousands_dots_only(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("1.234.567") == Decimal("1234567")

    def test_leading_plus(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("+1234.56") == Decimal("1234.56")

    def test_parentheses_with_currency(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("$(3,450.00)") == Decimal("-3450.00")

    def test_blank_raises(self):
        from src.step1_load import _parse_amount
        with pytest.raises(ValueError, match="Blank"):
            _parse_amount("")

    def test_none_raises(self):
        from src.step1_load import _parse_amount
        with pytest.raises(ValueError, match="Blank"):
            _parse_amount(None)

    def test_non_numeric_raises(self):
        from src.step1_load import _parse_amount
        with pytest.raises(ValueError, match="could not be understood"):
            _parse_amount("abc")

    def test_whitespace_only_raises(self):
        from src.step1_load import _parse_amount
        with pytest.raises(ValueError, match="Blank"):
            _parse_amount("   ")

    def test_zero(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("0.00") == Decimal("0.00")

    def test_small_decimal(self):
        from src.step1_load import _parse_amount
        assert _parse_amount("0.01") == Decimal("0.01")

    def test_loaded_dirty_amounts_file(self):
        """A file with formatted amounts loads correctly."""
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "fmt.csv"
            _write_csv(p, [
                ("2026-06-01", "REF-1", "$5,000.00", "Dollar amount"),
                ("2026-06-02", "REF-2", "(1,250.00)", "Accounting negative"),
                ("2026-06-03", "REF-3", "8500.00-", "Trailing minus"),
            ])
            state = load(str(p), str(p))
            amounts = {r.reference: r.signed_amount for r in state.side_a}
            assert amounts["REF-1"] == Decimal("5000")
            assert amounts["REF-2"] == Decimal("-1250")
            assert amounts["REF-3"] == Decimal("-8500")
            assert len(state.problems) == 0


# ── DIRTY DATES ─────────────────────────────────────────────────────

class TestDirtyDates:
    """Date parsing for varied formats."""

    def test_iso(self):
        from src.step1_load import _parse_date
        assert _parse_date("2026-06-01") == date(2026, 6, 1)

    def test_day_slash_month(self):
        from src.step1_load import _parse_date
        assert _parse_date("15/06/2026") == date(2026, 6, 15)

    def test_short_month_name_dash(self):
        from src.step1_load import _parse_date
        assert _parse_date("01-Jun-2026") == date(2026, 6, 1)

    def test_short_month_name_space(self):
        from src.step1_load import _parse_date
        assert _parse_date("01 Jun 2026") == date(2026, 6, 1)

    def test_full_month_name(self):
        from src.step1_load import _parse_date
        assert _parse_date("01 June 2026") == date(2026, 6, 1)

    def test_us_short_month(self):
        from src.step1_load import _parse_date
        assert _parse_date("Jun 01, 2026") == date(2026, 6, 1)

    def test_us_full_month(self):
        from src.step1_load import _parse_date
        assert _parse_date("June 01, 2026") == date(2026, 6, 1)

    def test_slash_month_name(self):
        from src.step1_load import _parse_date
        assert _parse_date("01/Jun/2026") == date(2026, 6, 1)

    def test_blank_raises(self):
        from src.step1_load import _parse_date
        with pytest.raises(ValueError, match="Blank"):
            _parse_date("")

    def test_none_raises(self):
        from src.step1_load import _parse_date
        with pytest.raises(ValueError, match="Blank"):
            _parse_date(None)

    def test_unparseable_raises(self):
        from src.step1_load import _parse_date
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_date("not-a-date")

    def test_loaded_mixed_date_formats(self):
        """A file with varied date formats loads correctly."""
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dates.csv"
            _write_csv(p, [
                ("2026-06-01", "REF-1", "100.00", "ISO date"),
                ("15/06/2026", "REF-2", "200.00", "Day-slash"),
                ("01-Jun-2026", "REF-3", "300.00", "Month name"),
            ])
            state = load(str(p), str(p))
            assert len(state.side_a) == 3
            assert state.side_a[0].date == date(2026, 6, 1)
            assert state.side_a[1].date == date(2026, 6, 15)
            assert state.side_a[2].date == date(2026, 6, 1)
            assert len(state.problems) == 0


# ── BLANK ROWS ──────────────────────────────────────────────────────

class TestBlankRows:
    """Blank and missing rows handled gracefully."""

    def test_blank_row_skipped(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "blank.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["date", "reference", "amount", "description"])
                w.writerow(["2026-06-01", "REF-1", "100.00", "Good"])
                w.writerow(["", "", "", ""])
                w.writerow(["2026-06-02", "REF-2", "200.00", "Also good"])
            state = load(str(p), str(p))
            assert len(state.side_a) == 2
            blank_probs = [p for p in state.problems
                          if "Blank row" in p.message]
            assert len(blank_probs) == 2  # once per side

    def test_blank_amount_is_problem(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "blankamt.csv"
            _write_csv(p, [
                ("2026-06-01", "REF-1", "", "Missing amount"),
                ("2026-06-02", "REF-2", "200.00", "Good"),
            ])
            state = load(str(p), str(p))
            assert len(state.side_a) == 1
            amt_probs = [p for p in state.problems
                        if p.column == "amount"]
            assert len(amt_probs) == 2  # once per side

    def test_blank_date_is_problem(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "blankdate.csv"
            _write_csv(p, [
                ("", "REF-1", "100.00", "Missing date"),
                ("2026-06-02", "REF-2", "200.00", "Good"),
            ])
            state = load(str(p), str(p))
            assert len(state.side_a) == 1
            date_probs = [p for p in state.problems
                         if p.column == "date"]
            assert len(date_probs) == 2  # once per side


# ── EXTRA / MISSING COLUMNS ────────────────────────────────────────

class TestColumnHandling:
    """Extra columns ignored; missing columns produce a clear error."""

    def test_extra_columns_ignored(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "extra.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["date", "reference", "amount",
                            "description", "category", "notes"])
                w.writerow(["2026-06-01", "REF-1", "100.00",
                            "Item 1", "revenue", "ignore me"])
            state = load(str(p), str(p))
            assert len(state.side_a) == 1
            assert state.side_a[0].signed_amount == Decimal("100")

    def test_missing_column_names_what_is_missing(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "nocols.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["date", "amount"])
                w.writerow(["2026-06-01", "100"])
            with pytest.raises(ValueError) as exc_info:
                load(str(p), str(p))
            msg = str(exc_info.value)
            assert "description" in msg
            assert "reference" in msg
            assert "Side A" in msg

    def test_missing_all_columns(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "empty.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["foo", "bar"])
                w.writerow(["1", "2"])
            with pytest.raises(ValueError, match="missing required column"):
                load(str(p), str(p))

    def test_case_insensitive_columns(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "upper.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["Date", "Reference", "Amount", "Description"])
                w.writerow(["2026-06-01", "REF-1", "100.00", "Test"])
            state = load(str(p), str(p))
            assert len(state.side_a) == 1


# ── ENCODING ────────────────────────────────────────────────────────

class TestEncoding:
    """Files with different encodings load correctly."""

    def test_utf8_bom(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bom.csv"
            content = ("﻿date,reference,amount,description\n"
                       "2026-06-01,REF-1,100.00,Test\n")
            p.write_text(content, encoding="utf-8")
            state = load(str(p), str(p))
            assert len(state.side_a) == 1

    def test_latin1_encoding(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "latin.csv"
            content = ("date,reference,amount,description\n"
                       "2026-06-01,Réf-1,100.00,Café\n")
            p.write_bytes(content.encode("latin-1"))
            state = load(str(p), str(p))
            assert len(state.side_a) == 1

    def test_whitespace_in_values_trimmed(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "spaces.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["date", "reference", "amount", "description"])
                w.writerow(["  2026-06-01  ", "  REF-1  ",
                            "  100.00  ", "  Padded  "])
            state = load(str(p), str(p))
            assert len(state.side_a) == 1
            assert state.side_a[0].reference == "REF-1"
            assert state.side_a[0].description == "Padded"


# ── MIXED DIRTY + CLEAN ────────────────────────────────────────────

class TestMixedDirtyClean:
    """A file with dirty and clean rows: reconciles on the good rows."""

    def test_dirty_rows_skipped_clean_reconciled(self):
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify

        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            _write_csv(a, [
                ("2026-06-01", "REF-1", "100.00", "Good A"),
                ("bad-date", "REF-2", "200.00", "Bad date"),
                ("2026-06-03", "REF-3", "$1,234.56", "Formatted amount"),
            ])
            _write_csv(b, [
                ("2026-06-01", "REF-1", "100.00", "Good B"),
                ("2026-06-03", "REF-3", "1234.56", "Plain amount"),
            ])
            state = load(str(a), str(b))
            assert len(state.side_a) == 2
            assert len(state.side_b) == 2
            assert len(state.problems) == 1

            state = validate(state)
            state = match(state)
            state = classify(state)
            assert state.raw_difference == Decimal("0")
            assert len(state.breaks) == 0

    def test_all_rows_bad_still_no_crash(self):
        from src.step1_load import load
        from src.step2_validate import validate
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "allbad.csv"
            _write_csv(p, [
                ("bad", "REF-1", "abc", "Both bad"),
                ("nope", "REF-2", "xyz", "Also bad"),
            ])
            state = load(str(p), str(p))
            assert len(state.side_a) == 0
            assert len(state.problems) == 4  # 2 per side
            state = validate(state)
            assert any("no records" in p.message for p in state.problems)


# ── V1: DECIMAL EXACTNESS ON FLOAT-TRICKY DATA ─────────────────────

class TestDecimalExactness:
    """V1: amounts that would accumulate float error stay exact."""

    def test_many_rows_exact_tieout(self):
        from src.step3_match import match
        from src.step4_classify import classify

        state = ReconciliationState()
        state.side_a = [
            _make_record("A", i, "2026-06-01", f"R-{i}", amt)
            for i, amt in enumerate([
                0.01, 0.03, 0.07, 0.11, 0.13, 0.17, 0.19, 0.23,
                0.29, 0.31, 0.37, 0.41, 0.43, 0.47, 0.53, 0.59,
                0.61, 0.67, 0.71, 0.73, 0.79, 0.83, 0.89, 0.97,
                1234.01, 5678.03, 9012.07, 3456.11, 7890.13, 2345.17,
            ], start=1)
        ]
        state.side_b = [
            _make_record("B", i, "2026-06-01", f"R-{i}", amt)
            for i, amt in enumerate([
                0.01, 0.03, 0.07, 0.11, 0.13, 0.17, 0.19, 0.23,
                0.29, 0.31, 0.37, 0.41, 0.43, 0.47, 0.53, 0.59,
                0.61, 0.67, 0.71, 0.73, 0.79, 0.83, 0.89, 0.97,
                1234.01, 5678.03, 9012.07, 3456.11, 7890.13, 2345.17,
            ], start=1)
        ]
        state = match(state)
        state = classify(state)
        assert state.raw_difference == Decimal("0")
        assert len(state.breaks) == 0
        assert state.side_a_total == state.side_b_total

    def test_float_tricky_difference_exact(self):
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step6_resolve import resolve

        state = ReconciliationState()
        state.side_a = [
            _make_record("A", 1, "2026-06-01", "R-1", 0.1),
            _make_record("A", 2, "2026-06-01", "R-2", 0.2),
        ]
        state.side_b = [
            _make_record("B", 1, "2026-06-01", "R-1", 0.1),
            _make_record("B", 2, "2026-06-01", "R-2", 0.2),
        ]
        state = match(state)
        state = classify(state)
        assert state.side_a_total == Decimal("0.3")
        assert state.raw_difference == Decimal("0")


# ── V2: ASYMMETRIC ADJUST SIGN ──────────────────────────────────────

class TestAsymmetricAdjust:
    """V2: adjusting a negative break moves the position correctly."""

    def test_negative_break_adjust_moves_toward_zero(self):
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step6_resolve import resolve, compute_resolved_position

        state = ReconciliationState()
        state.side_a = [
            _make_record("A", 1, "2026-06-01", "SALE-1", 1000),
            _make_record("A", 2, "2026-06-05", "FEE-1", -45),
        ]
        state.side_b = [
            _make_record("B", 1, "2026-06-01", "SALE-1", 1000),
        ]
        state = match(state)
        state = classify(state)

        assert state.raw_difference == Decimal("-45")
        fee_brk = state.breaks[0]
        assert fee_brk.difference == Decimal("-45")

        state = resolve(
            state,
            ask=lambda b, d: ("A", "Book fee", -45.0),
            confirm=lambda e: True,
            output_dir=tempfile.mkdtemp(),
        )
        assert state.resolved_position == Decimal("0")

    def test_positive_and_negative_adjust_oppose(self):
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step6_resolve import resolve

        state = ReconciliationState()
        state.side_a = [
            _make_record("A", 1, "2026-06-01", "INV-1", -500),
            _make_record("A", 2, "2026-06-02", "FEE-1", -30),
        ]
        state.side_b = [
            _make_record("B", 1, "2026-06-01", "INV-1", -450),
        ]
        state = match(state)
        state = classify(state)

        raw = state.raw_difference
        breaks_by_diff = {b.difference: b for b in state.breaks}
        pos_brk = [b for b in state.breaks if b.difference == Decimal("-50")][0]
        neg_brk = [b for b in state.breaks if b.difference == Decimal("-30")][0]

        answers = iter([
            ("A", "Correct invoice", -50.0),
            ("A", "Book fee", -30.0),
        ])
        state = resolve(
            state,
            ask=lambda b, d: next(answers),
            confirm=lambda e: True,
            output_dir=tempfile.mkdtemp(),
        )
        assert state.resolved_position == Decimal("0")


# ── V3: STABLE KEY COLLISION ────────────────────────────────────────

class TestStableKeyCollision:
    """V3: identical-content breaks get distinct keys."""

    def test_two_identical_unmatched_get_distinct_keys(self):
        from src.step3_match import match
        from src.step4_classify import classify

        state = ReconciliationState()
        state.side_a = [
            _make_record("A", 1, "2026-06-10", "FEE", -45),
            _make_record("A", 2, "2026-06-10", "FEE", -45),
        ]
        state.side_b = []
        state = match(state)
        state = classify(state)

        assert len(state.breaks) == 2
        keys = [b.stable_break_key for b in state.breaks]
        assert keys[0] != keys[1]

    def test_collision_resolutions_survive_independently(self):
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step6_resolve import resolve, load_locks

        state = ReconciliationState(reconciliation_id="collision-test")
        state.side_a = [
            _make_record("A", 1, "2026-06-10", "FEE", -45),
            _make_record("A", 2, "2026-06-10", "FEE", -45),
        ]
        state.side_b = []
        state = match(state)
        state = classify(state)

        answers = iter([
            ("D", "Dismiss first", None),
            ("I", "Investigate second", None),
        ])
        tmp = tempfile.mkdtemp()
        state = resolve(
            state,
            ask=lambda b, d: next(answers),
            confirm=lambda e: True,
            output_dir=tmp,
        )

        types = [state.resolutions[b.stable_break_key].resolution_type
                 for b in state.breaks]
        assert ResolutionType.DISMISS in types
        assert ResolutionType.INVESTIGATE in types

        locks = load_locks("collision-test", tmp)
        assert len(locks) == 1  # only dismiss is saved, investigate is not


# ── V4: ORPHANED ADJUSTMENT NO DOUBLE-CORRECTION ───────────────────

class TestOrphanedAdjustment:
    """V4: a break adjusted then fixed at source does not double-correct."""

    def test_orphaned_adjust_dropped(self):
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step6_resolve import resolve

        state1 = ReconciliationState(reconciliation_id="orphan-test")
        state1.side_a = [
            _make_record("A", 1, "2026-06-01", "INV-1", -3450),
        ]
        state1.side_b = [
            _make_record("B", 1, "2026-06-01", "INV-1", -3500),
        ]
        state1 = match(state1)
        state1 = classify(state1)
        assert len(state1.breaks) == 1
        assert state1.raw_difference == Decimal("50")

        tmp = tempfile.mkdtemp()
        state1 = resolve(
            state1,
            ask=lambda b, d: ("A", "Correct invoice", 50.0),
            confirm=lambda e: True,
            output_dir=tmp,
        )
        assert state1.resolved_position == Decimal("0")

        state2 = ReconciliationState(reconciliation_id="orphan-test")
        state2.side_a = [
            _make_record("A", 1, "2026-06-01", "INV-1", -3450),
        ]
        state2.side_b = [
            _make_record("B", 1, "2026-06-01", "INV-1", -3450),
        ]
        state2 = match(state2)
        state2 = classify(state2)

        assert len(state2.breaks) == 0
        assert state2.raw_difference == Decimal("0")

        state2 = resolve(
            state2,
            ask=lambda b, d: pytest.fail("No breaks expected"),
            confirm=lambda e: True,
            output_dir=tmp,
        )
        assert state2.resolved_position == Decimal("0")


# ── V5: DUPLICATE ROW MATCH DETERMINISM ─────────────────────────────

class TestDuplicateRowDeterminism:
    """V5: identical rows match deterministically for a given file."""

    def test_identical_rows_deterministic(self):
        from src.step3_match import match

        state1 = ReconciliationState()
        state1.side_a = [
            _make_record("A", 1, "2026-06-01", "REF-X", 100),
            _make_record("A", 2, "2026-06-01", "REF-X", 100),
        ]
        state1.side_b = [
            _make_record("B", 1, "2026-06-01", "REF-X", 100),
            _make_record("B", 2, "2026-06-02", "REF-X", 100),
        ]
        state1 = match(state1)

        state2 = ReconciliationState()
        state2.side_a = list(state1.side_a)
        state2.side_b = list(state1.side_b)
        state2 = match(state2)

        assert len(state1.matches) == len(state2.matches)
        for m1, m2 in zip(state1.matches, state2.matches):
            assert m1.match_type == m2.match_type
            assert ([r.row_id for r in m1.side_a_records]
                    == [r.row_id for r in m2.side_a_records])
            assert ([r.row_id for r in m1.side_b_records]
                    == [r.row_id for r in m2.side_b_records])

    def test_identical_rows_functional_equivalence_on_reorder(self):
        from src.step1_load import load
        from src.step3_match import match
        from src.step4_classify import classify

        with tempfile.TemporaryDirectory() as tmp:
            a1 = Path(tmp) / "a1.csv"
            a2 = Path(tmp) / "a2.csv"
            b = Path(tmp) / "b.csv"
            _write_csv(a1, [
                ("2026-06-01", "REF-X", "100.00", "First"),
                ("2026-06-01", "REF-X", "100.00", "Second"),
            ])
            _write_csv(a2, [
                ("2026-06-01", "REF-X", "100.00", "Second"),
                ("2026-06-01", "REF-X", "100.00", "First"),
            ])
            _write_csv(b, [
                ("2026-06-01", "REF-X", "100.00", "Match 1"),
                ("2026-06-01", "REF-X", "100.00", "Match 2"),
            ])

            s1 = load(str(a1), str(b))
            s1 = match(s1)
            s1 = classify(s1)

            s2 = load(str(a2), str(b))
            s2 = match(s2)
            s2 = classify(s2)

            assert len(s1.matches) == len(s2.matches)
            types1 = sorted(m.match_type.value for m in s1.matches)
            types2 = sorted(m.match_type.value for m in s2.matches)
            assert types1 == types2
            assert s1.raw_difference == s2.raw_difference

    def test_adjustment_decimal_exact_via_str(self):
        """R1: float-tricky adjustment through Decimal(str()) is exact."""
        from src.step6_resolve import _resolution_from_choice

        brk = Break(
            break_type=BreakType.AMOUNT_MISMATCH,
            side_a_records=[_make_record("A", 1, "2026-06-01", "X", 100)],
            side_b_records=[_make_record("B", 1, "2026-06-01", "X", 99.9)],
            side_a_amount=Decimal("100"),
            side_b_amount=Decimal("99.90"),
            difference=Decimal("0.10"),
        )
        res = _resolution_from_choice("A", "fix", 0.1, brk)
        assert res is not None
        assert res.adjustment_amount == Decimal("0.1")
        assert str(res.adjustment_amount) == "0.1"

    def test_occurrence_index_stable_across_reorder(self):
        """R2: near-identical breaks keep their keys on re-ordered re-run."""
        from src.step1_load import load
        from src.step3_match import match
        from src.step4_classify import classify
        from src.step6_resolve import save_locks, load_locks

        with tempfile.TemporaryDirectory() as tmp:
            a1 = Path(tmp) / "a_order1.csv"
            a2 = Path(tmp) / "a_order2.csv"
            b = Path(tmp) / "b_empty.csv"

            _write_csv(a1, [
                ("2026-06-01", "FEE", "45.00", "Wire fee alpha"),
                ("2026-06-01", "FEE", "45.00", "Wire fee beta"),
            ])
            _write_csv(a2, [
                ("2026-06-01", "FEE", "45.00", "Wire fee beta"),
                ("2026-06-01", "FEE", "45.00", "Wire fee alpha"),
            ])
            _write_csv(b, [
                ("2026-06-01", "OTHER", "999.00", "No match"),
            ])

            s1 = load(str(a1), str(b))
            s1 = match(s1)
            s1 = classify(s1)

            s2 = load(str(a2), str(b))
            s2 = match(s2)
            s2 = classify(s2)

            assert len(s1.breaks) >= 1
            assert len(s2.breaks) >= 1

            keys1 = {brk.stable_break_key for brk in s1.breaks}
            keys2 = {brk.stable_break_key for brk in s2.breaks}
            assert keys1 == keys2, f"Keys changed on reorder: {keys1} vs {keys2}"

            desc_by_key_1 = {}
            for brk in s1.breaks:
                recs = brk.side_a_records or brk.side_b_records
                desc_by_key_1[brk.stable_break_key] = recs[0].description
            desc_by_key_2 = {}
            for brk in s2.breaks:
                recs = brk.side_a_records or brk.side_b_records
                desc_by_key_2[brk.stable_break_key] = recs[0].description

            assert desc_by_key_1 == desc_by_key_2, \
                "Description-to-key mapping changed on reorder"

            s1.reconciliation_id = "reorder_test"
            for brk in s1.breaks:
                recs = brk.side_a_records or brk.side_b_records
                if recs[0].description == "Wire fee alpha":
                    s1.resolutions[brk.stable_break_key] = Resolution(
                        resolution_type=ResolutionType.DISMISS,
                        reviewer_text="Alpha dismissed",
                        timestamp="2026-06-01T00:00:00",
                        figures_hash="test",
                    )
                else:
                    s1.resolutions[brk.stable_break_key] = Resolution(
                        resolution_type=ResolutionType.ACCEPT_AS_TIMING,
                        reviewer_text="Beta timing",
                        timestamp="2026-06-01T00:00:00",
                        figures_hash="test",
                    )
            save_locks("reorder_test", s1.resolutions, tmp)

            locks = load_locks("reorder_test", tmp)
            for brk in s2.breaks:
                assert brk.stable_break_key in locks, \
                    f"Lock lost on reorder for {brk.stable_break_key}"
                recs = brk.side_a_records or brk.side_b_records
                lock = locks[brk.stable_break_key]
                if recs[0].description == "Wire fee alpha":
                    assert lock.resolution_type == ResolutionType.DISMISS
                else:
                    assert lock.resolution_type == ResolutionType.ACCEPT_AS_TIMING


# ── TIER 1: MAIN SAMPLE EDGE CASES ──────────────────────────────────

class TestTier1EdgeCases:
    """Tier 1: formatting edge cases present in the main sample data."""

    def test_currency_symbol_in_bank(self):
        from config import BANK_STATEMENT_FILE
        from src.step1_load import load
        state = load(str(BANK_STATEMENT_FILE), str(BANK_STATEMENT_FILE))
        rct = [r for r in state.side_a if r.reference == "RCT-0601"][0]
        assert rct.signed_amount == Decimal("1820.50")

    def test_thousands_separator_in_bank(self):
        from config import BANK_STATEMENT_FILE
        from src.step1_load import load
        state = load(str(BANK_STATEMENT_FILE), str(BANK_STATEMENT_FILE))
        rent = [r for r in state.side_a if r.reference == "DD-RENT"][0]
        assert rent.signed_amount == Decimal("-2400")

    def test_accounting_parentheses_in_book(self):
        from config import CASH_BOOK_FILE
        from src.step1_load import load
        state = load(str(CASH_BOOK_FILE), str(CASH_BOOK_FILE))
        chq = [r for r in state.side_a if r.reference == "CHQ-1043"][0]
        assert chq.signed_amount == Decimal("-1275")

    def test_month_name_date_in_book(self):
        from config import CASH_BOOK_FILE
        from src.step1_load import load
        state = load(str(CASH_BOOK_FILE), str(CASH_BOOK_FILE))
        rct = [r for r in state.side_a if r.reference == "RCT-0615"][0]
        assert rct.date == date(2026, 6, 15)

    def test_blank_reference_in_both(self):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE
        from src.step1_load import load
        from src.step2_validate import validate
        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        state = validate(state)
        blank_a = [r for r in state.side_a if r.reference == ""]
        blank_b = [r for r in state.side_b if r.reference == ""]
        assert len(blank_a) == 1
        assert blank_a[0].signed_amount == Decimal("-45.20")
        assert len(blank_b) == 1
        assert blank_b[0].signed_amount == Decimal("-45.20")

    def test_chrome_rows_skipped(self):
        from config import BANK_STATEMENT_FILE
        from src.step1_load import load
        state = load(str(BANK_STATEMENT_FILE), str(BANK_STATEMENT_FILE))
        assert len(state.side_a) == 17

    def test_native_columns_bank(self):
        from config import BANK_STATEMENT_FILE
        from src.step1_load import load_raw_table
        cols, rows = load_raw_table(str(BANK_STATEMENT_FILE))
        assert "Paid Out" in cols
        assert "Paid In" in cols
        assert "Balance" in cols
        assert len(rows) == 17

    def test_native_columns_book(self):
        from config import CASH_BOOK_FILE
        from src.step1_load import load_raw_table
        cols, rows = load_raw_table(str(CASH_BOOK_FILE))
        assert "Voucher" in cols
        assert "Receipts" in cols
        assert "Payments" in cols
        assert len(rows) == 20


# ── TIER 2: MESSY SAMPLE EDGE CASES ─────────────────────────────────

class TestTier2EdgeCases:
    """Tier 2: heavier formatting edge cases in the messy sample data."""

    def _bank_path(self):
        from config import DATA_DIR
        return str(DATA_DIR / "messy_bank.csv")

    def _book_path(self):
        from config import DATA_DIR
        return str(DATA_DIR / "messy_book.csv")

    def test_messy_files_load(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        assert len(state.side_a) == 15
        assert len(state.side_b) == 13

    def test_unparseable_rows_reported(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        load_problems = [p for p in state.problems
                         if p.column != "reference"]
        assert len(load_problems) == 3
        msgs = [p.message for p in load_problems]
        assert any("pending" in m for m in msgs)
        assert any("Blank row" in m for m in msgs)
        assert any("see note" in m for m in msgs)

    def test_unparseable_rows_skipped_not_processed(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        all_refs = {r.reference for r in state.side_a + state.side_b}
        assert "INV-2026-055" not in all_refs
        assert "ADJ-0729" not in all_refs

    def test_messy_pipeline_no_crash(self):
        from src.step1_load import load
        from src.step2_validate import validate
        from src.step3_match import match
        from src.step4_classify import classify
        state = load(self._bank_path(), self._book_path())
        state = validate(state)
        state = match(state)
        state = classify(state)
        assert state.matched_count >= 10

    def test_pound_currency_parsed(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        rct = [r for r in state.side_a if r.reference == "RCT-0701"][0]
        assert rct.signed_amount == Decimal("2340.80")

    def test_euro_comma_decimal_parsed(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        rct = [r for r in state.side_a if r.reference == "RCT-0708"][0]
        assert rct.signed_amount == Decimal("1890.50")

    def test_accounting_parentheses_in_paid_out(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        inv = [r for r in state.side_a if r.reference == "INV-2026-048"][0]
        assert inv.signed_amount == Decimal("-1150")

    def test_trailing_minus_parsed(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        rent = [r for r in state.side_a if r.reference == "DD-RENT"][0]
        assert rent.signed_amount == Decimal("-2400")

    def test_space_thousands_separator(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        wages = [r for r in state.side_a if r.reference == "FPO-WAGES"][0]
        assert wages.signed_amount == Decimal("-3180")

    def test_dollar_symbol_parsed(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        ins = [r for r in state.side_a if r.reference == "DD-INS"][0]
        assert ins.signed_amount == Decimal("-96.50")

    def test_leading_plus_parsed(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        inv = [r for r in state.side_a if r.reference == "INV-2026-051"][0]
        assert inv.signed_amount == Decimal("-475.60")

    def test_padded_whitespace_parsed(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        carter = [r for r in state.side_a if r.reference == "RCT-CARTER"][0]
        assert carter.signed_amount == Decimal("750")

    def test_extra_chrome_rows_skipped(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._bank_path())
        assert len(state.side_a) == 15

    def test_european_format_in_book(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        inv = [r for r in state.side_b if r.reference == "INV-2026-048"][0]
        assert inv.signed_amount == Decimal("-1150")

    def test_us_date_format_in_book(self):
        from src.step1_load import load
        state = load(self._bank_path(), self._book_path())
        rct = [r for r in state.side_b if r.reference == "RCT-0715"][0]
        assert rct.date == date(2026, 7, 15)


# ── FIELD OVERFLOW DETECTION ────────────────────────────────────────

class TestFieldOverflow:
    """A row with more fields than the header is reported, not silently
    trimmed."""

    def test_overflow_row_reported_as_problem(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "overflow.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                fh.write("date,reference,amount,description\n")
                fh.write("2026-06-01,REF-1,100.00,Good row\n")
                fh.write("2026-06-02,REF-2,200.00,Bad row,extra,fields\n")
                fh.write("2026-06-03,REF-3,300.00,Also good\n")
            state = load(str(p), str(p))
            assert len(state.side_a) == 2
            overflow = [p for p in state.problems
                        if "more fields" in p.message]
            assert len(overflow) == 2  # once per side
            assert overflow[0].column == "(structure)"

    def test_overflow_row_not_processed(self):
        from src.step1_load import load
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "overflow2.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                fh.write("date,reference,amount,description\n")
                fh.write("2026-06-01,REF-1,100.00,Good\n")
                fh.write("2026-06-02,REF-BAD,200.00,Overflow,x,y\n")
            state = load(str(p), str(p))
            refs = {r.reference for r in state.side_a}
            assert "REF-BAD" not in refs
            assert "REF-1" in refs


# ── RELATIVE TOLERANCE ─────────────────────────────────────────────────

class TestRelativeTolerance:
    """Tests for the combined absolute + relative tolerance."""

    def test_relative_zero_reproduces_absolute_only(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", -100.01)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", -100.00)]
        state = match(state, tolerance=Decimal("0.02"), tolerance_rel=Decimal("0"))
        assert len(state.matches) == 1
        assert state.matches[0].match_type == MatchType.TOLERANCE

    def test_relative_matches_large_amount(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", -1000000.00)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", -1000000.50)]
        state = match(state, tolerance=Decimal("0.02"), tolerance_rel=Decimal("0.001"))
        assert len(state.matches) == 1
        assert state.matches[0].match_type == MatchType.TOLERANCE

    def test_relative_large_amount_breaks_at_absolute_only(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", -1000000.00)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", -1000000.50)]
        state = match(state, tolerance=Decimal("0.02"), tolerance_rel=Decimal("0"))
        amt = [m for m in state.matches if m.match_type == MatchType.AMOUNT_MISMATCH]
        assert len(amt) == 1

    def test_small_item_uses_absolute_floor(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", -1.00)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", -1.01)]
        state = match(state, tolerance=Decimal("0.02"), tolerance_rel=Decimal("0.001"))
        assert state.matches[0].match_type == MatchType.TOLERANCE

    def test_relative_beyond_bound_is_mismatch(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-1", -1000000.00)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-1", -1002000.00)]
        state = match(state, tolerance=Decimal("0.02"), tolerance_rel=Decimal("0.001"))
        amt = [m for m in state.matches if m.match_type == MatchType.AMOUNT_MISMATCH]
        assert len(amt) == 1

    def test_relative_affects_rule4_reference_mismatch(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "REF-A", -500000.00)]
        state.side_b = [_make_record("B", 1, "2026-06-01", "REF-B", -500000.30)]
        state = match(state, tolerance=Decimal("0.02"), tolerance_rel=Decimal("0.001"))
        ref = [m for m in state.matches if m.match_type == MatchType.REFERENCE_MISMATCH]
        assert len(ref) == 1

    def test_relative_affects_many_to_one(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "BATCH", 100000.00)]
        state.side_b = [
            _make_record("B", 1, "2026-06-01", "R1", 60000.00),
            _make_record("B", 2, "2026-06-01", "R2", 40000.05),
        ]
        state = match(state, tolerance=Decimal("0.02"), tolerance_rel=Decimal("0.001"))
        m2o = [m for m in state.matches if m.match_type == MatchType.MANY_TO_ONE]
        assert len(m2o) == 1

    def test_tolerance_in_audit_record(self):
        from src.step7_record import _audit_record
        state = ReconciliationState()
        audit = _audit_record(state, tolerance_abs=0.05, tolerance_rel=0.001)
        assert audit["tolerance_abs"] == 0.05
        assert audit["tolerance_rel"] == 0.001

    def test_default_tolerance_in_audit(self):
        from src.step7_record import _audit_record
        state = ReconciliationState()
        audit = _audit_record(state)
        assert audit["tolerance_abs"] == 0.02
        assert audit["tolerance_rel"] == 0


# ── MANY-TO-ONE CONFIRMATION ─────────────────────────────────────────

class TestManyToOneConfirmation:
    """Many-to-one pairings are suggestions requiring reviewer confirmation."""

    def test_many_to_one_creates_suggestion_break(self):
        from src.step3_match import match
        from src.step4_classify import classify
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "BATCH", 900.00)]
        state.side_b = [
            _make_record("B", 1, "2026-06-01", "R1", 300.00),
            _make_record("B", 2, "2026-06-01", "R2", 250.00),
            _make_record("B", 3, "2026-06-01", "R3", 350.00),
        ]
        state = match(state, tolerance=Decimal("0.02"))
        state = classify(state)
        m2o_breaks = [b for b in state.breaks
                      if b.break_type == BreakType.MANY_TO_ONE_SUGGESTION]
        assert len(m2o_breaks) == 1
        assert m2o_breaks[0].difference == Decimal("0")

    def test_many_to_one_not_counted_as_clean_match(self):
        from src.step3_match import match
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "BATCH", 900.00)]
        state.side_b = [
            _make_record("B", 1, "2026-06-01", "R1", 300.00),
            _make_record("B", 2, "2026-06-01", "R2", 250.00),
            _make_record("B", 3, "2026-06-01", "R3", 350.00),
        ]
        state = match(state, tolerance=Decimal("0.02"))
        assert state.matched_count == 0

    def test_confirm_match_removes_from_position(self):
        from src.step6_resolve import compute_resolved_position
        state = ReconciliationState()
        state.raw_difference = Decimal("5.00")
        brk = Break(
            break_type=BreakType.MANY_TO_ONE_SUGGESTION,
            side_a_records=[_make_record("A", 1, "2026-06-01", "BATCH", 900.00)],
            side_b_records=[_make_record("B", 1, "2026-06-01", "R1", 895.00)],
            side_a_amount=Decimal("900.00"),
            side_b_amount=Decimal("895.00"),
            difference=Decimal("5.00"),
        )
        state.breaks = [brk]
        state.resolutions = {
            brk.stable_break_key: Resolution(
                resolution_type=ResolutionType.CONFIRM_MATCH,
                reviewer_text="Confirmed",
                figures_hash="test",
            )
        }
        pos = compute_resolved_position(state)
        assert pos == Decimal("0.00")

    def test_unconfirmed_m2o_stays_in_position(self):
        from src.step6_resolve import compute_resolved_position
        state = ReconciliationState()
        state.raw_difference = Decimal("5.00")
        brk = Break(
            break_type=BreakType.MANY_TO_ONE_SUGGESTION,
            side_a_records=[_make_record("A", 1, "2026-06-01", "BATCH", 900.00)],
            side_b_records=[_make_record("B", 1, "2026-06-01", "R1", 895.00)],
            side_a_amount=Decimal("900.00"),
            side_b_amount=Decimal("895.00"),
            difference=Decimal("5.00"),
        )
        state.breaks = [brk]
        state.resolutions = {}
        pos = compute_resolved_position(state)
        assert pos == Decimal("5.00")

    def test_rejected_m2o_stays_open(self):
        from src.step6_resolve import compute_resolved_position
        state = ReconciliationState()
        state.raw_difference = Decimal("5.00")
        brk = Break(
            break_type=BreakType.MANY_TO_ONE_SUGGESTION,
            side_a_records=[_make_record("A", 1, "2026-06-01", "BATCH", 900.00)],
            side_b_records=[_make_record("B", 1, "2026-06-01", "R1", 895.00)],
            side_a_amount=Decimal("900.00"),
            side_b_amount=Decimal("895.00"),
            difference=Decimal("5.00"),
        )
        state.breaks = [brk]
        state.resolutions = {
            brk.stable_break_key: Resolution(
                resolution_type=ResolutionType.INVESTIGATE,
                reviewer_text="Rejected",
                figures_hash="test",
            )
        }
        pos = compute_resolved_position(state)
        assert pos == Decimal("5.00")

    def test_resolution_from_choice_confirm(self):
        from src.step6_resolve import _resolution_from_choice
        brk = Break(
            break_type=BreakType.MANY_TO_ONE_SUGGESTION,
            side_a_records=[_make_record("A", 1, "2026-06-01", "BATCH", 900.00)],
            side_b_records=[_make_record("B", 1, "2026-06-01", "R1", 900.00)],
            side_a_amount=Decimal("900.00"),
            side_b_amount=Decimal("900.00"),
            difference=Decimal("0"),
        )
        res = _resolution_from_choice("C", "Confirmed", None, brk)
        assert res is not None
        assert res.resolution_type == ResolutionType.CONFIRM_MATCH

    def test_m2o_suggestion_in_break_counts(self):
        from src.step3_match import match
        from src.step4_classify import classify
        state = ReconciliationState()
        state.side_a = [_make_record("A", 1, "2026-06-01", "BATCH", 500.00)]
        state.side_b = [
            _make_record("B", 1, "2026-06-01", "R1", 200.00),
            _make_record("B", 2, "2026-06-01", "R2", 300.00),
        ]
        state = match(state, tolerance=Decimal("0.02"))
        state = classify(state)
        assert state.break_counts.get("many_to_one_suggestion", 0) == 1

    def test_confirm_match_lock_persisted(self):
        from src.step6_resolve import save_locks, load_locks
        import tempfile
        res = Resolution(
            resolution_type=ResolutionType.CONFIRM_MATCH,
            reviewer_text="Batch confirmed",
            figures_hash="abc123",
            timestamp="2026-06-30T15:00:00Z",
        )
        with tempfile.TemporaryDirectory() as td:
            save_locks("test", {"key1": res}, td)
            loaded = load_locks("test", td)
            assert "key1" in loaded
            assert loaded["key1"].resolution_type == ResolutionType.CONFIRM_MATCH

    def test_decompose_creates_individual_unmatched(self):
        brk = Break(
            break_type=BreakType.MANY_TO_ONE_SUGGESTION,
            side_a_records=[_make_record("A", 1, "2026-06-01", "BATCH", 900.00)],
            side_b_records=[
                _make_record("B", 1, "2026-06-01", "R1", 300.00),
                _make_record("B", 2, "2026-06-01", "R2", 250.00),
                _make_record("B", 3, "2026-06-01", "R3", 350.00),
            ],
            side_a_amount=Decimal("900.00"),
            side_b_amount=Decimal("900.00"),
            difference=Decimal("0"),
        )
        decomposed = []
        for r in brk.side_a_records:
            decomposed.append(Break(
                break_type=BreakType.UNMATCHED,
                side_a_records=[r], side_b_records=[],
                side_a_amount=r.signed_amount,
                side_b_amount=Decimal("0"),
                difference=r.signed_amount,
            ))
        for r in brk.side_b_records:
            decomposed.append(Break(
                break_type=BreakType.UNMATCHED,
                side_a_records=[], side_b_records=[r],
                side_a_amount=Decimal("0"),
                side_b_amount=r.signed_amount,
                difference=round(-r.signed_amount, 2),
            ))
        assert len(decomposed) == 4
        assert all(b.break_type == BreakType.UNMATCHED for b in decomposed)
        total_diff = sum(b.difference for b in decomposed)
        assert total_diff == brk.difference

    def test_decomposed_keys_are_unique(self):
        brk = Break(
            break_type=BreakType.MANY_TO_ONE_SUGGESTION,
            side_a_records=[_make_record("A", 1, "2026-06-01", "BATCH", 500.00)],
            side_b_records=[
                _make_record("B", 1, "2026-06-01", "R1", 200.00),
                _make_record("B", 2, "2026-06-01", "R2", 300.00),
            ],
            side_a_amount=Decimal("500.00"),
            side_b_amount=Decimal("500.00"),
            difference=Decimal("0"),
        )
        decomposed = []
        for r in brk.side_a_records:
            decomposed.append(Break(
                break_type=BreakType.UNMATCHED,
                side_a_records=[r], side_b_records=[],
                side_a_amount=r.signed_amount,
                side_b_amount=Decimal("0"),
                difference=r.signed_amount,
            ))
        for r in brk.side_b_records:
            decomposed.append(Break(
                break_type=BreakType.UNMATCHED,
                side_a_records=[], side_b_records=[r],
                side_a_amount=Decimal("0"),
                side_b_amount=r.signed_amount,
                difference=round(-r.signed_amount, 2),
            ))
        keys = [b.stable_break_key for b in decomposed]
        assert len(keys) == len(set(keys))


class TestDeploymentPins:
    """The web server stack the live app installs."""

    def test_item22_certifi_has_the_fixed_root_bundle(self):
        # certifi before 2026.10.18 trusts emSign ECC Root CA G3, whose
        # intermediates had compromised keys (GHSA-6q48-6c7w-9gw2).
        text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(
            encoding="utf-8-sig")
        assert "certifi==2026.10.19" in text.split()

    def test_item1_starlette_pinned_for_streamlit_1_60(self):
        """Streamlit 1.60 cannot serve images with starlette 1.8 or later (item 1)."""
        from importlib.metadata import version
        requirements = Path(__file__).resolve().parent.parent / "requirements.txt"
        assert "starlette==1.7.0" in requirements.read_text(encoding="utf-8-sig").split()
        if version("streamlit").startswith("1.60."):
            major, minor = (int(part) for part in version("starlette").split(".")[:2])
            assert (major, minor) < (1, 8)


# ── THE MODEL CALL ───────────────────────────────────────────────────

class _FakeClient:
    """Stands in for the Anthropic client: records each request, and either
    replies with the given text or raises the given error. No network."""

    def __init__(self, reply=None, error=None):
        self.calls = []
        self.messages = self
        self._reply, self._error = reply, error

    def create(self, **kwargs):
        from types import SimpleNamespace
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(content=[SimpleNamespace(text=self._reply)])


def _status_error(cls, status):
    import httpx
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("refused", response=httpx.Response(status, request=request), body=None)


def _classified_sample():
    from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE, TOLERANCE_ABS
    from src.step1_load import load
    from src.step2_validate import validate
    from src.step3_match import match
    from src.step4_classify import classify
    state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
    return classify(match(validate(state), tolerance=TOLERANCE_ABS))


class TestModelCall:
    """The model comes from config, and a failed call is recorded, never hidden."""

    def test_item20_model_comes_from_config(self):
        import config
        from src.step5_narrate import narrate
        client = _FakeClient(reply='{"explanation": "E.", "follow_up": "F."}')
        narrate(_classified_sample(), client)
        assert config.MODEL == "claude-sonnet-4-6"
        assert client.calls and {c["model"] for c in client.calls} == {config.MODEL}
        for path in list((ROOT / "src").glob("*.py")) + list((ROOT / "streamlit_app").rglob("*.py")):
            assert "claude-sonnet-4-2025" not in path.read_text(encoding="utf-8"), path

    def test_item20_model_drafts_are_labelled_model(self):
        from src.step5_narrate import narrate
        client = _FakeClient(reply='{"explanation": "E.", "follow_up": "F."}')
        state = narrate(_classified_sample(), client)
        assert state.drafts and {d.source for d in state.drafts.values()} == {"model"}
        assert state.narration_failure == ""

    def test_item20_rejected_key_is_recorded_and_template_labelled(self):
        import anthropic
        from src.step5_narrate import narrate
        client = _FakeClient(error=_status_error(anthropic.AuthenticationError, 401))
        state = narrate(_classified_sample(), client)
        assert state.narration_failure == "key_rejected"
        assert state.drafts and {d.source for d in state.drafts.values()} == {"template"}

    def test_item20_unreadable_reply_is_recorded(self):
        from src.step5_narrate import narrate
        state = narrate(_classified_sample(), _FakeClient(reply="Not the JSON asked for."))
        assert state.narration_failure == "unreadable_reply"
        assert {d.source for d in state.drafts.values()} == {"template"}

    @pytest.mark.parametrize("make, code", [
        (lambda a: _status_error(a.AuthenticationError, 401), "key_rejected"),
        (lambda a: _status_error(a.PermissionDeniedError, 403), "key_forbidden"),
        (lambda a: _status_error(a.NotFoundError, 404), "model_not_found"),
        (lambda a: _status_error(a.BadRequestError, 400), "bad_request"),
        (lambda a: _status_error(a.RateLimitError, 429), "rate_limited"),
        (lambda a: _status_error(a.InternalServerError, 500), "service_unavailable"),
        (lambda a: a.APIConnectionError(request=__import__("httpx").Request("POST", "https://x")),
         "service_unavailable"),
    ])
    def test_item20_each_failure_has_one_plain_message(self, make, code):
        import anthropic
        import config
        from src.step5_narrate import failure_code
        from streamlit_app.lib.errors import model_failure_message
        assert failure_code(make(anthropic)) == code
        message = model_failure_message(code)
        assert message.endswith("Code: `{}`".format(code))
        assert "Error" not in message and "Traceback" not in message
        if code in ("bad_request", "model_not_found"):
            assert "usually temporary" not in message
        if code == "model_not_found":
            assert config.MODEL in message

    def test_item12_cli_labels_template_text(self, monkeypatch, capsys):
        import main
        from src.step5_narrate import narrate

        class _Stop(Exception):
            pass

        def _no_input(prompt=""):
            raise _Stop

        state = narrate(_classified_sample())
        brk = state.breaks[0]
        monkeypatch.setattr("builtins.input", _no_input)
        with pytest.raises(_Stop):
            main._cli_ask(brk, state.drafts)
        out = capsys.readouterr().out
        assert "Explanation (template):" in out
        assert "AI explanation" not in out


# ── STEP 2: KEY GATE, ONE ERROR MAPPING, RETRY ──────────────────────

_TEST_KEY = "sk-ant-api03-" + "x" * 90
_HOME = ROOT / "streamlit_app" / "Home.py"
_RECON_PAGE = ROOT / "streamlit_app" / "views" / "2_Reconciliation.py"

# One stand-in SDK error per failure code, built without a network call.
_MODEL_ERRORS = [
    (lambda a: _status_error(a.AuthenticationError, 401), "key_rejected"),
    (lambda a: _status_error(a.PermissionDeniedError, 403), "key_forbidden"),
    (lambda a: _status_error(a.NotFoundError, 404), "model_not_found"),
    (lambda a: _status_error(a.BadRequestError, 400), "bad_request"),
    (lambda a: _status_error(a.RateLimitError, 429), "rate_limited"),
    (lambda a: _status_error(a.InternalServerError, 500), "service_unavailable"),
    (lambda a: a.APIConnectionError(request=__import__("httpx").Request("POST", "https://x")),
     "service_unavailable"),
]


class _StandInAnthropic:
    """Takes the place of anthropic.Anthropic while an AppTest runs the app,
    so no call reaches the network. While `failing` is set every call is
    refused with a 401; otherwise each call returns a well-formed draft."""

    failing = False

    def __init__(self, api_key=None, **kwargs):
        self.messages = self

    def create(self, **kwargs):
        from types import SimpleNamespace
        if type(self).failing:
            import anthropic
            raise _status_error(anthropic.AuthenticationError, 401)
        return SimpleNamespace(content=[SimpleNamespace(
            text='{"explanation": "Model text.", "follow_up": "Model step."}')])


@pytest.fixture
def stand_in_anthropic(monkeypatch):
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _StandInAnthropic)
    monkeypatch.setattr(_StandInAnthropic, "failing", False)
    return _StandInAnthropic


class TestKeyGate:
    """The gate says where the key is held, and the key outlives a page change."""

    def test_item2_key_gate_privacy_wording(self, stand_in_anthropic):
        from streamlit.testing.v1 import AppTest
        from streamlit_app.lib.errors import KEY_LOOKS_COMPLETE
        at = AppTest.from_file(str(_HOME), default_timeout=180).run()
        box = at.sidebar.text_input(key="byo_api_key")
        assert box.help == ("Held in the server's memory for this session only. "
                            "Never stored, never logged.")
        notes = " ".join(m.value for m in at.sidebar.markdown)
        assert ("Your key is held in the server's memory for your session only, used "
                "to make the call, and never stored or logged. If you would rather your "
                "key never leave your own machine, download the project from GitHub and "
                "run it locally.") in notes
        box.input(_TEST_KEY).run()
        assert [s.value for s in at.sidebar.success] == [KEY_LOOKS_COMPLETE]
        source = (ROOT / "streamlit_app" / "lib" / "key_gate.py").read_text(encoding="utf-8")
        for stale in ("never stored server-side", "browser", "Key accepted"):
            assert stale not in source

    def test_item4_key_entered_on_home_stays_in_effect_on_other_pages(self, stand_in_anthropic):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_HOME), default_timeout=180).run()
        at.sidebar.text_input(key="byo_api_key").input(_TEST_KEY).run()
        assert isinstance(at.session_state["live_client"], _StandInAnthropic)
        # A page without the gate, then the page that runs the model.
        at.switch_page("views/1_How_the_Model_Works.py").run()
        at.switch_page("views/2_Reconciliation.py").run()
        assert not at.exception
        assert isinstance(at.session_state["live_client"], _StandInAnthropic)
        assert at.sidebar.text_input(key="byo_api_key").value == _TEST_KEY
        at.button(key="btn_reconcile").click().run()
        assert at.button(key="btn_draft") is not None


class TestOneErrorMapping:
    """The Draft button's errors and narrate()'s recorded code use one mapping."""

    @pytest.mark.parametrize("make, code", _MODEL_ERRORS)
    def test_item17_draft_errors_give_the_same_plain_messages(self, make, code):
        import anthropic
        from src.step5_narrate import failure_code, narrate
        from streamlit_app.lib import errors
        exc = make(anthropic)
        assert errors.failure_code is failure_code
        assert failure_code(exc) == code
        state = narrate(_classified_sample(), _FakeClient(error=exc))
        assert state.narration_failure == code
        assert errors.friendly_message(exc) == errors.model_failure_message(code)
        # A wrapped SDK error still maps, through its cause.
        try:
            raise RuntimeError("drafting failed") from exc
        except RuntimeError as wrapped:
            assert errors.friendly_message(wrapped) == errors.model_failure_message(code)
        engine = (ROOT / "src" / "step5_narrate.py").read_text(encoding="utf-8")
        assert "import streamlit" not in engine and "from streamlit" not in engine

    def test_item55_other_failures_get_plain_messages(self):
        from streamlit_app.lib.claude_client import NoKeyError
        from streamlit_app.lib.errors import friendly_message
        generic = friendly_message(ValueError("boom"))
        no_key = friendly_message(NoKeyError("No API key provided."))
        assert generic == "Something went wrong running that request. Please try again."
        assert "in the sidebar" in no_key and "worked example" in no_key
        for message in (generic, no_key):
            assert "example prompts" not in message
            assert "above" not in message
            assert "Error" not in message and "boom" not in message


class TestRetryDrafts:
    """P5a: after a failed drafting run, Try again works without a reload."""

    def test_p5a_try_again_redrafts_and_keeps_the_resolutions(self, stand_in_anthropic):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180).run()
        at.sidebar.text_input(key="byo_api_key").input(_TEST_KEY).run()
        at.button(key="btn_reconcile").click().run()

        # Lock one resolution first: confirm the many-to-one suggestion. (A card
        # with a Resolution radio would leave that radio behind in AppTest's
        # element tree after the page's st.rerun, and the next run fails on it.)
        confirm = next(b for b in at.button if (b.key or "").startswith("m2o_confirm_"))
        locked = confirm.key[len("m2o_confirm_"):]
        confirm.click().run()
        assert locked in at.session_state["p5_resolutions"]

        stand_in_anthropic.failing = True
        at.button(key="btn_draft").click().run()
        assert at.session_state["p5_ai_failure"] == "key_rejected"
        assert any(w.value.endswith("Code: `key_rejected`") for w in at.warning)

        stand_in_anthropic.failing = False
        at.button(key="btn_draft_retry").click().run()
        assert "p5_ai_failure" not in at.session_state
        at.button(key="btn_draft").click().run()

        assert not at.exception
        assert at.session_state["p5_ai_failure"] == ""
        assert any(s.value.startswith("AI explanations drafted for all") for s in at.success)
        assert not [b for b in at.button if b.key == "btn_draft_retry"]
        assert at.session_state["reconciled"] is True
        assert locked in at.session_state["p5_resolutions"]


# ── STEP 2: PART ADJUSTMENTS, THE PDF'S REVIEW SENTENCE, SIDEBAR ────

def _pdf_unescape(match):
    """One PDF string escape: an octal code (a character such as é) or a
    character taken as it is."""
    code = match.group(1)
    return bytes([int(code, 8)]) if code[0] in b"01234567" else code


def _pdf_text(pdf):
    """The text of a ReportLab PDF, read with the standard library only."""
    import base64
    import re
    import zlib
    out = []
    for raw in re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        data = zlib.decompress(base64.a85decode(raw.strip().removesuffix(b"~>")))
        for s in re.findall(rb"\(((?:[^()\\]|\\.)*)\)\s*Tj", data):
            out.append(re.sub(rb"\\([0-7]{1,3}|.)", _pdf_unescape, s, flags=re.S)
                       .decode("latin-1"))
    return " ".join(" ".join(out).split())


def _inv_2214(state):
    return next(b for b in state.breaks
                if b.break_type == BreakType.AMOUNT_MISMATCH
                and b.side_a_records[0].reference == "INV-2214")


def _sample_resolved(inv_2214_adjustment):
    """The sample with every break resolved and nothing under investigation;
    the 72.00 INV-2214 mismatch is adjusted by the amount given."""
    from src.step6_resolve import _resolution_from_choice, compute_resolved_position
    state = _classified_sample()
    for brk in state.breaks:
        refs = {r.reference for r in brk.side_a_records + brk.side_b_records}
        if brk.break_type == BreakType.AMOUNT_MISMATCH:
            res = _resolution_from_choice("A", "Correct invoice", inv_2214_adjustment, brk)
        elif brk.break_type == BreakType.MANY_TO_ONE_SUGGESTION:
            res = _resolution_from_choice("C", "Batch confirmed", None, brk)
        elif brk.break_type == BreakType.REFERENCE_MISMATCH:
            res = _resolution_from_choice("D", "Same transfer", None, brk)
        elif "INT-JUN" in refs:
            res = _resolution_from_choice("A", "Book interest", 12.15, brk)
        elif "CHG-Q2" in refs:
            res = _resolution_from_choice("A", "Book charge", -30.0, brk)
        else:
            res = _resolution_from_choice("T", "Timing", None, brk)
        state.resolutions[brk.stable_break_key] = res
    state.resolved_position = compute_resolved_position(state)
    return state


class TestPartAdjustment:
    """An adjustment removes only the amount typed, never more than the break."""

    def test_item24_one_pound_leaves_71_unexplained_and_72_clears_it(self):
        from src.step6_resolve import adjusted_part
        from src.step7_record import build_pdf_bytes
        part, whole = _sample_resolved(1.00), _sample_resolved(72.00)
        brk = _inv_2214(part)
        assert brk.difference == Decimal("72.00")
        assert adjusted_part(brk, part.resolutions[brk.stable_break_key]) == Decimal("1.00")
        assert part.resolved_position == whole.resolved_position + Decimal("71.00")
        part_text = _pdf_text(build_pdf_bytes(part))
        assert "Remaining unexplained 71.00" in part_text
        assert "Not yet resolved 71.00" in part_text
        assert "Remaining unexplained 0.00" in _pdf_text(build_pdf_bytes(whole))

    def test_item24_adjustment_larger_than_the_break_is_refused(self, capsys):
        from src.step6_resolve import _resolution_from_choice, adjustment_refusal, resolve
        state = _classified_sample()
        brk = _inv_2214(state)
        assert adjustment_refusal(72.01, brk) == (
            "An adjustment of 72.01 is larger than this break of 72.00. "
            "Enter an amount no larger than the break.")
        assert adjustment_refusal(72.00, brk) is None
        assert _resolution_from_choice("A", "Too much", 100.0, brk) is None
        state = resolve(
            state,
            ask=lambda b, d: ("A", "Too much", 100.0) if b is brk else ("I", "Later", None),
            confirm=lambda e: True,
            output_dir=tempfile.mkdtemp(),
        )
        assert brk.stable_break_key not in state.resolutions
        assert "is larger than this break of 72.00" in capsys.readouterr().out

    def test_item24_page_refuses_an_adjustment_larger_than_the_break(self):
        from streamlit.testing.v1 import AppTest
        key = _inv_2214(_classified_sample()).stable_break_key
        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180).run()
        at.button(key="btn_reconcile").click().run()
        at.radio(key="action_" + key).set_value("Adjust").run()
        at.number_input(key="adj_amt_" + key).set_value(100.0).run()
        at.button(key="apply_a_" + key).click().run()
        assert not at.exception
        assert [e.value for e in at.error] == [
            "An adjustment of 100.00 is larger than this break of 72.00. "
            "Enter an amount no larger than the break."]
        assert key not in at.session_state["p5_resolutions"]
        assert at.session_state["p5_pending_adjust"] is None


class TestPdfReviewSentence:
    """The PDF claims a full human review only when one happened."""

    def test_item26_pdf_sentence_follows_the_real_state(self):
        from datetime import datetime, timezone
        from src.signoff import SignOff
        from src.step7_record import build_pdf_bytes
        reviewer = SignOff("A. Controller", datetime(2026, 6, 30, 15, 5, tzinfo=timezone.utc))
        done, open_state = _sample_resolved(72.00), _classified_sample()
        signed = _pdf_text(build_pdf_bytes(done, signoff=reviewer))
        unsigned = _pdf_text(build_pdf_bytes(done))
        unsigned_open = _pdf_text(build_pdf_bytes(open_state))
        signed_open = _pdf_text(build_pdf_bytes(open_state, signoff=reviewer))
        claim = "a human reviewer resolved every break and signed off the result."
        assert "advisory only: " + claim in signed
        assert "This is a working copy, not yet signed off by a reviewer." in unsigned
        assert ("This is a working copy, 8 open items, not yet signed off by a "
                "reviewer.") in unsigned_open
        assert "a human reviewer signed off the result with 8 open items." in signed_open
        for text in (unsigned, unsigned_open, signed_open):
            assert claim not in text


class TestSidebarOnArrival:
    def test_item53_sidebar_starts_auto(self):
        source = (ROOT / "streamlit_app" / "Home.py").read_text(encoding="utf-8")
        assert 'initial_sidebar_state="auto"' in source
        assert '"expanded"' not in source


# ── STEP 2: KEY GATE LAYOUT, WORKED EXAMPLE BUTTON, TABULAR FIGURES ─

class TestFirstVisit:
    """What a first-time visitor sees: a quiet key gate and one way in."""

    def test_ux1_key_gate_sits_in_a_closed_expander(self, stand_in_anthropic):
        from streamlit.testing.v1 import AppTest
        from streamlit_app.lib.errors import KEY_LOOKS_COMPLETE
        at = AppTest.from_file(str(_HOME), default_timeout=180).run()
        label = "Run it live with your own key (optional)"
        sidebar = list(at.sidebar.children.values())
        assert [(type(e).__name__, getattr(e, "label", None)) for e in sidebar[:1]] == [
            ("Expander", label)]
        assert sidebar[0].proto.expanded is False
        assert [type(e).__name__ for e in sidebar[0].children.values()] == [
            "Caption", "TextInput", "Caption", "Markdown"]
        assert sidebar[1].value == "Live mode is off. The worked examples need no key."
        assert len(sidebar) == 2
        assert all("Run it yourself" not in m.value for m in at.sidebar.markdown)

        at.sidebar.text_input(key="byo_api_key").input("not-a-key").run()
        gate = at.sidebar.expander[0]
        assert gate.label == label and len(gate.warning) == 1
        gate.text_input(key="byo_api_key").input(_TEST_KEY).run()
        gate = at.sidebar.expander[0]
        assert gate.label == label
        assert [s.value for s in gate.success] == [KEY_LOOKS_COMPLETE]
        assert list(at.sidebar.children.values())[-1].value == "Live mode is on for this session."

    def test_ux2_home_button_opens_the_worked_example_page_with_no_key(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_HOME), default_timeout=180).run()
        main = list(at.main.children.values())
        assert '<div class="sc-header">' in main[1].value
        assert main[2].label == "See the worked example"
        at.button(key="btn_worked_example").click().run()
        assert not at.exception
        assert any("<h1>Reconciliation</h1>" in m.value for m in at.markdown)
        assert [t.label for t in at.tabs] == ["View a worked example", "Run it yourself"]
        assert any(m.value.startswith("##### Breaks: all five resolution types")
                   for m in at.tabs[0].markdown)
        assert at.session_state["live_client"] is None


class TestTabularFigures:
    def test_ux4_tabular_figures_only_on_numeric_elements(self):
        import re
        from streamlit_app.lib.theme import inject_css
        rules = [(sel.split("*/")[-1].strip(), body)
                 for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", inject_css())]
        body_rule = next(body for sel, body in rules if sel.startswith("html, body"))
        assert "tnum" not in body_rule and '"cv05" 1' in body_rule
        # Lists, bold, italics and captions in Inter too, as Project 6's
        # theme sets them (5b fix round)
        body_sel = next(sel for sel, body in rules if sel.startswith("html, body"))
        assert body_sel.endswith("th, ul, ol, li, strong, em, caption")
        numeric = [sel for sel, body in rules if "tnum" in body]
        assert len(numeric) == 1
        selectors = [s.strip() for s in numeric[0].split(",")]
        assert selectors[:2] == ["table", "table *"]
        assert '[data-testid="stMetricValue"]' in selectors
        assert ".sc-metric .value" in selectors


# ── STEP 3: THE HOME BUTTON OPENS THE WORKED EXAMPLE TAB ────────────

def _tab_sets(node):
    """Every tab container under an AppTest node, in page order."""
    found = []
    for child in getattr(node, "children", {}).values():
        if getattr(child, "type", None) == "tab_container":
            found.append(child)
        found.extend(_tab_sets(child))
    return found


class TestExampleTab:
    """Home's button opens the Reconciliation page on its worked example tab,
    which is the first tab, so the sidebar link opens it too (UX2; since
    Step 5 there is no session flag and no default= argument)."""

    def test_ux2_button_opens_the_example_tab(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_HOME), default_timeout=180).run()
        at.button(key="btn_worked_example").click().run()
        assert not at.exception
        assert [t.label for t in at.tabs] == ["View a worked example", "Run it yourself"]
        tab_sets = _tab_sets(at.main)
        assert len(tab_sets) == 1
        assert tab_sets[0].proto.tab_container.default_tab_index == 0
        # The button only switches page: it leaves no flag behind.
        assert "p5_open_example" not in at.session_state
        assert at.session_state["live_client"] is None

    def test_ux2_sidebar_link_opens_the_example_tab_too(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180).run()
        assert _tab_sets(at.main)[0].proto.tab_container.default_tab_index == 0
        # After the button has been used once, the page link opens the same tab.
        at = AppTest.from_file(str(_HOME), default_timeout=180).run()
        at.button(key="btn_worked_example").click().run()
        at.switch_page("views/1_How_the_Model_Works.py").run()
        at.switch_page("views/2_Reconciliation.py").run()
        assert not at.exception
        assert _tab_sets(at.main)[0].proto.tab_container.default_tab_index == 0


# ── STEP 3: THE PAGE'S SIGN-OFF LINE COUNTS LIKE THE PDF ────────────

class TestSignOffCount:
    """The sign-off section counts open breaks as the PDF counts open items."""

    def test_fix_page_signoff_counts_like_the_pdf(self):
        from streamlit.testing.v1 import AppTest
        from src.step6_resolve import _resolution_from_choice
        from src.step7_record import build_pdf_bytes
        # INV-2214 adjusted by 1.00 of its 72.00, CHG-Q2 under investigation,
        # and one timing difference left with no resolution: three open items.
        state = _sample_resolved(1.00)
        chg = next(b for b in state.breaks
                   if any(r.reference == "CHG-Q2" for r in b.side_a_records))
        state.resolutions[chg.stable_break_key] = _resolution_from_choice(
            "I", "Ask the bank", None, chg)
        left = next(b for b in state.breaks
                    if state.resolutions[b.stable_break_key].resolution_type
                    == ResolutionType.ACCEPT_AS_TIMING)
        del state.resolutions[left.stable_break_key]
        pdf = _pdf_text(build_pdf_bytes(state))
        assert "This is a working copy, 3 open items, not yet signed off" in pdf
        assert "Remaining unexplained 1,316.00" in pdf

        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180)
        at.session_state["p5_resolutions"] = dict(state.resolutions)
        at.session_state["reconciled"] = True
        at.run()
        assert not at.exception
        # Since Step 5b the Sign-off section lists the open items, one line
        # each, and the amount the PDF calls "Remaining unexplained".
        section = _signoff_section(at)
        assert section[3] == "Outstanding before you sign:"
        assert _numbered(section[4]) == [
            "Amount mismatch INV-2214, difference 72.00: adjusted 1.00, 71.00 still "
            "unexplained.",
            "Unmatched CHG-Q2, difference -30.00: under investigation.",
            "Unmatched CHQ-1043, difference 1,275.00: not yet resolved."]
        assert section[5] == "Remaining unexplained: 1,316.00."

    def test_fix_example_not_fully_explained_while_investigating(self):
        # The worked example leaves CHG-Q2 under investigation: the position
        # panel must say so, never "fully explained" (Step 3b).
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180).run()
        assert not at.exception
        assert not [s.value for s in at.success if "fully explained" in s.value]
        assert [i.value for i in at.info if "under investigation" in i.value] == [
            "All breaks have a decision, but 1 item is still under investigation."]


# ── STEP 5a: LOOK AND LAYOUT, AS PROJECT 6 (V1 TO V5) ───────────────

_VIEWS = ROOT / "streamlit_app" / "views"

# Every page and the title its banner carries, in the navigation order.
_BANNERS = [
    (_HOME, "Reconciliation and Tie-Out Assistant"),
    (_VIEWS / "1_How_the_Model_Works.py", "How it works"),
    (_VIEWS / "2_Reconciliation.py", "Reconciliation"),
    (_VIEWS / "3_Your_Own_Data.py", "Your own data"),
    (_VIEWS / "4_How_Its_Built.py", "How it's built"),
]


@pytest.fixture
def app_shell(monkeypatch):
    """Runs Home.py and records what it hands to st.set_page_config and
    st.navigation: the page icon, and each page's address, title and icon."""
    import streamlit
    seen = {}
    navigation, set_page_config = streamlit.navigation, streamlit.set_page_config

    def _navigation(pages, *args, **kwargs):
        result = navigation(pages, *args, **kwargs)
        seen["pages"] = [(p.url_path, p.title, p.icon) for p in pages]
        return result

    def _set_page_config(*args, **kwargs):
        seen["config"] = kwargs
        return set_page_config(*args, **kwargs)

    monkeypatch.setattr(streamlit, "navigation", _navigation)
    monkeypatch.setattr(streamlit, "set_page_config", _set_page_config)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_HOME), default_timeout=180).run()
    assert not at.exception
    return at, seen


class TestStep5aLook:
    """The series look, taken from Project 6 in the navy (Step 5a)."""

    def test_v1_primary_colour_is_the_navy(self):
        import tomllib
        from streamlit_app.lib.theme import DARK_BLUE
        config = tomllib.loads(
            (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
        assert config["theme"]["primaryColor"] == DARK_BLUE == "#1A3A5C"
        assert config["client"]["toolbarMode"] == "minimal"

    def test_v2_every_page_has_a_banner(self):
        import html
        from streamlit.testing.v1 import AppTest
        for path, title in _BANNERS:
            at = AppTest.from_file(str(path), default_timeout=180).run()
            assert not at.exception, path.name
            banners = [m.value for m in at.markdown if 'class="sc-header"' in m.value]
            assert len(banners) == 1, path.name
            assert banners[0].startswith(
                '<div class="sc-header"><h1>{}</h1><p>'.format(html.escape(title)))
            # The banner is the page's first element, after the page's CSS.
            assert list(at.main.children.values())[1].value == banners[0], path.name
        # Every banner goes through header_html: none is written out by hand.
        for path, _ in _BANNERS:
            source = path.read_text(encoding="utf-8")
            assert "header_html(" in source and "sc-header" not in source, path.name

    def test_v2_draft_and_final_chips(self):
        from streamlit_app.lib.theme import (
            DRAFT_CHIP, FINAL_CHIP, inject_css, status_chip_html,
        )
        assert status_chip_html("final") == '<span class="sc-status final">FINAL</span>'
        for status in ("draft", "", None, "FINAL", "signed"):
            assert status_chip_html(status) == '<span class="sc-status draft">DRAFT</span>'
        assert (DRAFT_CHIP, FINAL_CHIP) == ("#B45F06", "#2E6B3F")
        css = inject_css()
        assert ".sc-status.draft { background: #B45F06; }" in css
        assert ".sc-status.final { background: #2E6B3F; }" in css

    def test_v3_home_blocks_and_page_links(self, app_shell):
        from config import BANK_STATEMENT_FILE, CASH_BOOK_FILE
        from src.step1_load import load
        at, _ = app_shell
        main = list(at.main.children.values())
        assert main[1].value.startswith('<div class="sc-header">')
        assert main[2].label == "See the worked example"
        values = [getattr(e, "value", None) for e in main]
        headings = [v for v in values if isinstance(v, str) and v.startswith("####")]
        assert headings == ["#### What it does", "#### The governance promise", "#### Pages"]
        # The record counts Home quotes are the sample's.
        state = load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
        what = values[values.index("#### What it does") + 1]
        assert "({} and {} records, June 2026)".format(
            len(state.side_a), len(state.side_b)) == "(17 and 20 records, June 2026)"
        assert "(17 and 20 records, June 2026)" in what
        promise = values[values.index("#### The governance promise") + 1]
        assert [line.split("**")[1] for line in promise.splitlines()] == [
            "Python matches.", "A rule classifies.", "The model explains.",
            "The reviewer signs."]
        # One link per page except Home, in the navigation order, then the key
        # explainer (closed, cost figures kept), then one closing caption.
        start = values.index("#### Pages")
        links = main[start + 1:start + 5]
        assert [e.proto.page for e in links] == [
            "How_the_Model_Works", "Reconciliation", "Your_Own_Data", "How_Its_Built"]
        assert [e.proto.label.split(":")[0] for e in links] == [
            "How it works", "Reconciliation", "Your own data", "How it's built"]
        assert len(at.get("page_link")) == 4
        # They wrap rather than clip on a phone, as Project 6's do.
        from streamlit_app.lib.theme import inject_css
        assert ('[data-testid="stPageLink-NavLink"], [data-testid="stPageLink-NavLink"] * {\n'
                '        white-space: normal !important;') in inject_css()
        explainer = main[start + 5]
        assert explainer.label == "Using your own API key"
        assert explainer.proto.expanded is False
        assert any("costs about 2 US cents" in m.value for m in explainer.markdown)
        assert type(main[-2]).__name__ == "Divider"
        assert main[-1].value == ("Sample data only: Valencia Operations is a fictional "
                                  "company. All figures are illustrative.")
        assert not at.success and not at.warning

    def test_v4_titles_are_sentence_case_and_urls_unchanged(self, app_shell):
        # Every page address as published before Step 5 (main at 1f066d3).
        # Titles changed; file names and addresses did not.
        urls = ["", "How_the_Model_Works", "Reconciliation", "Your_Own_Data",
                "How_Its_Built"]
        _, seen = app_shell
        assert [url for url, _, _ in seen["pages"]] == urls
        titles = [title for _, title, _ in seen["pages"]]
        assert titles == ["Home", "How it works", "Reconciliation", "Your own data",
                          "How it's built"]
        assert all(t == t[0] + t[1:].lower() for t in titles)
        assert sorted(p.name for p in _VIEWS.glob("*.py")) == [
            "1_How_the_Model_Works.py", "2_Reconciliation.py", "3_Your_Own_Data.py",
            "4_How_Its_Built.py"]

    def test_v5_example_tab_first_and_no_open_example_flag(self):
        import re
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180).run()
        assert not at.exception
        assert [t.label for t in at.tabs] == ["View a worked example", "Run it yourself"]
        main = list(at.main.children.values())
        # The tab bar sits directly under the banner, selected on the example.
        assert main[1].value.startswith('<div class="sc-header">')
        assert main[2].type == "tab_container"
        assert main[2].proto.tab_container.default_tab_index == 0
        # The live run lives in "Run it yourself": the data preview, the
        # tolerance inputs and Reconcile; the example tab has none of them.
        example, live = at.tabs
        assert any(m.value == "#### Valencia Operations, June 2026" for m in live.markdown)
        assert [n.key for n in live.number_input] == ["tol_abs", "tol_rel_pct"]
        assert [b.key for b in live.button if b.key == "btn_reconcile"] == ["btn_reconcile"]
        assert not example.number_input
        assert not [b for b in example.button if b.key == "btn_reconcile"]
        for path in (ROOT / "streamlit_app").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "_open_example" not in source, path.name
            assert not re.search(r"st\.tabs\([^)]*default=", source), path.name

    def test_v5_page_icon_is_the_tool_page_icon(self, app_shell):
        _, seen = app_shell
        icons = {title: icon for _, title, icon in seen["pages"]}
        assert seen["config"]["page_icon"] == icons["Reconciliation"] == ":material/balance:"


# ── STEP 5b: THE SIGN-OFF SECTION, AS PROJECT 6 ─────────────────────

_SENTENCE = "Signing says you have reviewed the breaks and your decisions above."
_DRAFT_CHIP = '<span class="sc-status draft">DRAFT</span>'
_FINAL_CHIP = '<span class="sc-status final">FINAL</span>'
# The sample with no decisions: eight open breaks and what they leave
_SAMPLE_OPEN = [
    "Amount mismatch INV-2214, difference 72.00: not yet resolved.",
    "Many-to-one suggestion DEP-0629, RCT-W1, RCT-W2, RCT-W3, difference 0.00: "
    "not yet resolved.",
    "Reference mismatch TRF-998812, TRF-5567, difference 0.00: not yet resolved.",
    "Unmatched CHG-Q2, difference -30.00: not yet resolved.",
    "Unmatched CHQ-1043, difference 1,275.00: not yet resolved.",
    "Unmatched RCT-DEL, difference -450.00: not yet resolved.",
    "Unmatched RCT-0629, difference -2,260.30: not yet resolved.",
    "Unmatched INT-JUN, difference 12.15: not yet resolved.",
]
_SAMPLE_LEFT = "Remaining unexplained: -1,381.15."


def _signoff_section(at):
    """The Sign-off section's text, from its heading on ("Run it yourself")."""
    values = [m.value for m in at.tabs[1].markdown]
    return values[values.index("##### Sign-off"):]


def _numbered(markdown):
    """The lines of a numbered markdown list, without their numbers."""
    lines = markdown.splitlines()
    assert [line.split(". ", 1)[0] for line in lines] == [
        str(i) for i in range(1, len(lines) + 1)]
    return [line.split(". ", 1)[1] for line in lines]


@pytest.fixture
def downloads(monkeypatch):
    """Reads the file behind a download button: while the test runs, every
    file the page hands to one is kept by its media id."""
    from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
    files = {}
    load = MemoryMediaFileStorage.load_and_get_id

    def _load(self, data, mimetype, kind, filename=None):
        file_id = load(self, data, mimetype, kind, filename)
        files[file_id] = self.get_file(file_id).content
        return file_id

    monkeypatch.setattr(MemoryMediaFileStorage, "load_and_get_id", _load)

    def read(at, label):
        (button,) = [d for d in at.get("download_button") if d.proto.label == label]
        return files[button.proto.url.rsplit("/", 1)[-1].split(".")[0]]
    return read


def _sign(at, name="José Núñez", word="SIGN"):
    at.text_input(key="p5_reviewer").input(name).run()
    at.text_input(key="p5_sign").input(word).run()
    at.button(key="btn_signoff").click().run()
    assert not at.exception


def _boxes(at):
    return [(t.key, t.value) for t in at.text_input if t.key in ("p5_reviewer", "p5_sign")]


def _signoff_buttons(at):
    return [b for b in at.button if b.key == "btn_signoff"]


@pytest.fixture
def reconciled(stand_in_anthropic):
    """The page after Reconcile and Draft explanations, with the stand-in client."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180).run()
    at.sidebar.text_input(key="byo_api_key").input(_TEST_KEY).run()
    at.button(key="btn_reconcile").click().run()
    at.button(key="btn_draft").click().run()
    assert not at.exception
    assert any(s.value.startswith("AI explanations drafted for all") for s in at.success)
    return at


class TestStep5bSignOff:
    """The Sign-off section: the rule in src/signoff.py, drawn as in Project 6."""

    def test_5b_no_section_before_a_result(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180).run()
        assert not at.exception
        assert "##### Sign-off" not in [m.value for m in at.markdown]
        assert not _boxes(at) and not _signoff_buttons(at)
        # The worked example has its own report and no section to sign.
        assert not [m for m in at.tabs[0].markdown if "Sign-off" in m.value]

    def test_5b_from_run_to_final(self, downloads, reconciled):
        from src.signoff import (
            NAME_REFUSAL, SCOPE_NOTE, WORD_HELP, WORD_LABEL, WORD_REFUSAL, when_text,
        )
        at = reconciled
        live = at.tabs[1]
        # After the reconciled position, at the level of its sibling sections
        headings = [m.value for m in live.markdown if m.value.startswith("#")]
        assert headings[-2:] == ["##### Reconciled position", "##### Sign-off"]
        section = _signoff_section(at)
        assert section == ["##### Sign-off", _SENTENCE, _DRAFT_CHIP,
                           "Outstanding before you sign:", section[4], _SAMPLE_LEFT]
        assert _numbered(section[4]) == _SAMPLE_OPEN
        assert [(t.label, t.help) for t in live.text_input
                if t.key in ("p5_reviewer", "p5_sign")] == [
            ("Reviewer name", ""), (WORD_LABEL, WORD_HELP)]
        (button,) = _signoff_buttons(at)
        assert button.label == "Sign off" and button.proto.type == "primary"
        assert [c.value for c in live.caption].count(SCOPE_NOTE) == 1
        draft = _pdf_text(downloads(at, "Download the DRAFT PDF"))
        assert "Generated: " in draft and "DRAFT: not signed off WORKING COPY" in draft

        # Refused: no name, a wrong word, the word in lower case. Each stays DRAFT.
        for name, word, refusal in [("   ", "SIGN", NAME_REFUSAL),
                                    ("José Núñez", "SIGNED", WORD_REFUSAL),
                                    ("José Núñez", "sign", WORD_REFUSAL)]:
            _sign(at, name, word)
            assert [w.value for w in at.tabs[1].warning] == [refusal]
            assert _signoff_section(at)[2] == _DRAFT_CHIP
            assert "p5_signed_off" not in at.session_state
            assert "recon_audit" not in at.session_state

        # Signed: FINAL, the signed line, read only, the FINAL PDF
        _sign(at, "José Núñez", " SIGN ")
        signed = at.session_state["p5_signed_off"]
        when = when_text(signed.signed_at)
        line = "Signed off by José Núñez on {}.".format(when)
        assert _signoff_section(at) == [
            "##### Sign-off", _SENTENCE, "{}&nbsp; {}".format(_FINAL_CHIP, line)]
        assert not at.tabs[1].warning
        assert not _boxes(at) and not _signoff_buttons(at)
        assert [c.value for c in at.tabs[1].caption].count(SCOPE_NOTE) == 1
        assert not [d for d in at.get("download_button")
                    if d.proto.label == "Download the DRAFT PDF"]
        final = _pdf_text(downloads(at, "Download the FINAL PDF"))
        assert "FINAL: signed off by José Núñez on {} RECONCILED WITH 8 OPEN ITEMS".format(
            when) in final
        assert line in final and "DRAFT: not signed off" not in final

        # The session audit row carries the sign-off and what it acknowledged
        (row,) = at.session_state["recon_audit"]
        assert {k: row[k] for k in ("status", "signed_off_by", "signed_off_at")} == {
            "status": "final", "signed_off_by": "José Núñez",
            "signed_off_at": signed.signed_at.isoformat()}
        assert row["acknowledged_at_sign_off"] == _SAMPLE_OPEN + [_SAMPLE_LEFT]
        assert row["break_count"] == 8

    def test_5b_a_second_signoff_is_refused(self, reconciled):
        from src.signoff import ALREADY_SIGNED
        at = reconciled
        _sign(at)
        signed = at.session_state["p5_signed_off"]
        result = at.session_state["p5_signed_for"]
        # The page offers no second Sign off.
        assert not _signoff_buttons(at) and not _boxes(at)
        # And the rule refuses one: the same result, signed again while a
        # sign-off for it stands (a second click that lands after the first).
        at.number_input(key="tol_abs").set_value(0.03).run()
        at.number_input(key="tol_abs").set_value(0.02).run()
        at.text_input(key="p5_sign").input("SIGN").run()
        at.session_state["p5_signed_off"] = signed
        at.session_state["p5_signed_for"] = result
        at.button(key="btn_signoff").click().run()
        assert not at.exception
        assert [w.value for w in at.tabs[1].warning] == [ALREADY_SIGNED]
        assert at.session_state["p5_signed_off"] == signed
        assert len(at.session_state["recon_audit"]) == 1
        assert _signoff_section(at)[2].startswith(_FINAL_CHIP)

    def test_5b_a_decision_changed_after_signoff_is_a_new_draft(self, downloads, reconciled):
        at = reconciled
        _sign(at)
        confirm = next(b for b in at.button if (b.key or "").startswith("m2o_confirm_"))
        confirm.click().run()
        assert not at.exception
        section = _signoff_section(at)
        assert section[2] == _DRAFT_CHIP
        assert _numbered(section[4]) == [_SAMPLE_OPEN[0]] + _SAMPLE_OPEN[2:]
        # The name stays filled in; SIGN must be typed again.
        assert _boxes(at) == [("p5_reviewer", "José Núñez"), ("p5_sign", "")]
        assert "p5_signed_off" not in at.session_state
        assert "DRAFT: not signed off" in _pdf_text(downloads(at, "Download the DRAFT PDF"))
        _sign(at)
        assert len(at.session_state["recon_audit"]) == 2
        assert at.session_state["recon_audit"][0]["acknowledged_at_sign_off"] == (
            [_SAMPLE_OPEN[0]] + _SAMPLE_OPEN[2:] + [_SAMPLE_LEFT])

    def test_5b_a_tolerance_changed_after_signoff_is_a_new_draft(self, downloads, reconciled):
        from src.signoff import STALE_CAPTION
        at = reconciled
        _sign(at, "A. Reviewer")
        # Reconcile pressed again on the same inputs: the same result, still FINAL
        at.button(key="btn_reconcile").click().run()
        assert _signoff_section(at)[2].startswith(_FINAL_CHIP)
        # The page recomputes from the inputs as they stand, so a changed
        # tolerance gives a new DRAFT at once, never a stale one.
        at.number_input(key="tol_abs").set_value(0.03).run()
        assert not at.exception
        assert _signoff_section(at)[2] == _DRAFT_CHIP
        assert _boxes(at) == [("p5_reviewer", "A. Reviewer"), ("p5_sign", "")]
        (button,) = _signoff_buttons(at)
        assert not button.disabled
        assert STALE_CAPTION not in [c.value for c in at.caption]
        # Back to the first tolerance: still a DRAFT until signed again
        at.number_input(key="tol_abs").set_value(0.02).run()
        assert _signoff_section(at)[2] == _DRAFT_CHIP
        assert "Tolerance: 0.02 (absolute)" in _pdf_text(
            downloads(at, "Download the DRAFT PDF"))

    def test_5b_new_explanations_after_signoff_are_a_new_draft(self, stand_in_anthropic):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180).run()
        at.sidebar.text_input(key="byo_api_key").input(_TEST_KEY).run()
        at.button(key="btn_reconcile").click().run()
        _sign(at)                                   # signed on the template's text
        at.button(key="btn_draft").click().run()    # the model's text replaces it
        assert not at.exception
        assert _signoff_section(at)[2] == _DRAFT_CHIP
        assert _boxes(at) == [("p5_reviewer", "José Núñez"), ("p5_sign", "")]

    def test_5b_names_print_safely(self, downloads, reconciled):
        import html
        from src.signoff import NAME_LIMIT, clean_name, pdf_name, when_text
        at = reconciled
        typed = "Zoë 李 <b>R&D</b>\x07" + "x" * 100
        _sign(at, typed)
        signed = at.session_state["p5_signed_off"]
        assert signed.name == clean_name(typed) and len(signed.name) == NAME_LIMIT
        assert signed.name.startswith("Zoë 李 <b>R&D</b>xxx")
        # On screen as typed, escaped; in the PDF as its fonts print it, never as markup
        assert _signoff_section(at)[2] == "{}&nbsp; {}".format(
            _FINAL_CHIP, html.escape(signed.line))
        final = "".join(_pdf_text(downloads(at, "Download the FINAL PDF")).split())
        status = "FINAL: signed off by {} on {}".format(
            pdf_name(signed.name), when_text(signed.signed_at))
        assert status.startswith("FINAL: signed off by Zoë ? <b>R&D</b>xxx")
        assert "".join(status.split()) in final

    def test_5b_nothing_outstanding_signs_as_reconciled(self, downloads):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180)
        at.session_state["p5_resolutions"] = dict(_sample_resolved(72.00).resolutions)
        at.session_state["reconciled"] = True
        at.run()
        section = _signoff_section(at)
        assert section == ["##### Sign-off", _SENTENCE, _DRAFT_CHIP, "Nothing is outstanding."]
        _sign(at)
        assert at.session_state["recon_audit"][0]["acknowledged_at_sign_off"] == []
        final = _pdf_text(downloads(at, "Download the FINAL PDF"))
        assert "RECONCILED Remaining unexplained 0.00" in final

    def test_5b_worked_example_is_signed_by_a_controller(self, downloads):
        # The owner's decision: the example is signed by the series' sample
        # reviewer. Otherwise its PDF reads as on main (0f28fca).
        import hashlib
        import re
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(_RECON_PAGE), default_timeout=180).run()
        text = _pdf_text(downloads(at, "Download example report (PDF)"))
        status = "FINAL: signed off by A. Controller on 30 June 2026 at 15:05 UTC"
        line = "Signed off by A. Controller on 30 June 2026 at 15:05 UTC."
        assert status + " RECONCILED WITH 1 OPEN ITEM Remaining unexplained" in text
        assert line + " This reconciliation was prepared" in text
        assert "Signed off: " not in text and "DRAFT: not signed off" not in text
        # The rest as on main, apart from the time it was generated. Page
        # furniture (the footers, the repeated table header) is left out: the
        # status line moves one row of the breaks table to page 2.
        as_on_main = text.replace(status + " ", "").replace(
            line, "Signed off: 30 June 2026 15:05 UTC")
        body = re.sub(r"Bank Statement vs Cash Book Page \d+ "
                      r"|Type Side A Side B Difference Resolution ", "", as_on_main)
        body = re.sub(r"Generated: \d{1,2} [A-Za-z]+ \d{4} \d\d:\d\d UTC",
                      "Generated: <time>", body)
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
            "0d11bdd956c73819bcc0a287e7a7dd7d175b75daa94fa875a8913409d56ed20f")
        # The example tab shows no sign-off text on screen.
        assert not [m for m in at.tabs[0].markdown if "igned off" in m.value]

    def test_5b_cli_pdf_is_unchanged(self, tmp_path):
        # The CLI has no sign-off: its PDF carries no status line and reads
        # exactly as on main (0f28fca) apart from the time it was generated.
        import hashlib
        import re
        from config import TOLERANCE_ABS, TOLERANCE_REL
        from src.step5_narrate import narrate
        from src.step7_record import record
        record(narrate(_classified_sample()), tmp_path,
               tolerance_abs=float(TOLERANCE_ABS), tolerance_rel=float(TOLERANCE_REL))
        (pdf,) = tmp_path.glob("*.pdf")
        text = _pdf_text(pdf.read_bytes())
        assert "DRAFT: not signed off" not in text and "FINAL:" not in text
        assert "Generated: " in text and " WORKING COPY, NOT YET SIGNED OFF " in text
        masked = re.sub(r"Generated: \d{1,2} [A-Za-z]+ \d{4} \d\d:\d\d UTC",
                        "Generated: <time>", text)
        assert masked.count("Generated: <time>") == 2
        assert hashlib.sha256(masked.encode("utf-8")).hexdigest() == (
            "578189bfb3a7eef482b916b27708e7b6242871af6668994fb775f2065fdb5935")

    def test_5b_audit_record_is_pure(self):
        # Built without Streamlit running: the row and its sign-off, nothing else
        from datetime import datetime, timezone
        from src.signoff import sign_off
        from streamlit_app.lib.audit import build_audit_record
        signed, _ = sign_off("A. Reviewer", "SIGN",
                             now=datetime(2026, 10, 21, 9, 5, tzinfo=timezone.utc))
        row = build_audit_record(_classified_sample(), signed, ["One open item."])
        assert row["status"] == "final" and row["signed_off_by"] == "A. Reviewer"
        assert row["signed_off_at"] == "2026-10-21T09:05:00+00:00"
        assert row["acknowledged_at_sign_off"] == ["One open item."]
        assert row["break_count"] == 8 and row["reconciliation_id"]

    def test_5b_wording(self, app_shell):
        at, _ = app_shell
        values = [getattr(e, "value", "") for e in at.main.children.values()]
        what = values[values.index("#### What it does") + 1]
        promise = values[values.index("#### The governance promise") + 1]
        assert what.splitlines()[-1] == (
            "- You decide every resolution, and the PDF report is a DRAFT until a named "
            "reviewer signs it.")
        assert promise.splitlines()[-1] == (
            "- **The reviewer signs.** Nothing is final until a named reviewer reads it "
            "and signs.")
        stale = ["working copy until you sign", "until a reviewer reads it",
                 "Sign off reconciliation", "Sign off and downloads",
                 "stops at flagging for review", "no sign-off step", "nobody approves"]
        for path in [ROOT / "README.md", *(ROOT / "streamlit_app").rglob("*.py"),
                     *(ROOT / "src").glob("*.py")]:
            text = path.read_text(encoding="utf-8")
            assert not [s for s in stale if s in text], path.name


# ── STEP 6a: THE COLD-START DEEP LINK ───────────────────────────────

class TestStep6aDeepLink:
    """A page opened as the first request runs inside Home, at its published address."""

    def test_6a_no_pages_folder_under_the_app(self):
        # A folder named pages/ beside Home.py switches on Streamlit's older
        # multipage mode: a page opened as a fresh server's first request runs
        # without Home, with file names in the sidebar. The views live in views/.
        app = ROOT / "streamlit_app"
        assert not [p for p in app.rglob("*") if p.is_dir() and p.name.lower() == "pages"]
        assert sorted(p.name for p in (app / "views").glob("*.py")) == [
            "1_How_the_Model_Works.py", "2_Reconciliation.py", "3_Your_Own_Data.py",
            "4_How_Its_Built.py"]

    def test_6a_every_page_address_is_pinned(self, app_shell):
        # The addresses are published (the README, posts), so each st.Page in
        # Home.py names its own with url_path: a renamed or moved file cannot
        # move it. Home is the default page, at /.
        import ast
        tree = ast.parse(_HOME.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Page":
                kwargs = {k.arg: k.value.value for k in node.keywords
                          if isinstance(k.value, ast.Constant)}
                found.append((node.lineno, kwargs.get("url_path"), kwargs["title"]))
        assert [(url, title) for _, url, title in sorted(found)] == [
            ("How_the_Model_Works", "How it works"),
            ("Reconciliation", "Reconciliation"),
            ("Your_Own_Data", "Your own data"),
            ("How_Its_Built", "How it's built"),
            (None, "Home"),
        ]
        _, seen = app_shell
        assert [url for url, _, _ in seen["pages"]] == [
            "", "How_the_Model_Works", "Reconciliation", "Your_Own_Data", "How_Its_Built"]


# ── STEP 6c: THE DIAGRAMS, READABLE ON A PHONE ──────────────────────
#
# The three diagrams on How it's built were SVG in fixed-height frames, so on a
# phone their text shrank to about 4 px and a blank gap followed. They are now
# HTML lists drawn by lib/flow_diagram.py, the series' shared renderer, which
# Step 6c extends for this page: words on the arrows, a path back from the last
# stage to the first, layers with no arrows, and a larger row minimum, which
# the seven boxes ask for.
# The page tests read How it's built as AppTest renders it; the renderer tests
# call it directly.

from html.parser import HTMLParser

_BLUE, _GREEN, _AMBER = ("#2D6A9F", "#EAF2FB"), ("#1D6B0F", "#EAF3DE"), ("#854F0B", "#FAEEDA")

# Each diagram as the SVG drew it: every box's words in order as (title, text,
# foot, words on the arrow into it), and its stroke and fill. The words the SVG
# set above four pipeline boxes are each box's foot now.
_PIPELINE_6C = {
    "label": "The seven phases in order: Load, from the CSV files; Validate, with row "
             "checks; Match, by 5 rules, deterministic; Classify, into typed breaks, "
             "deterministic; Narrate, an AI draft, optional; Resolve, a human choice, "
             "you decide; and Record, the PDF and audit.",
    "ol": "fd-flow fd-row680", "li": "fd-stage",
    "stages": [("Load", "CSV files", None, None),
               ("Validate", "row checks", None, None),
               ("Match", "5 rules", "deterministic", None),
               ("Classify", "typed breaks", "deterministic", None),
               ("Narrate", "AI draft", "optional", None),
               ("Resolve", "human choice", "you decide", None),
               ("Record", "PDF, audit", None, None)],
    "colours": [_BLUE, _BLUE, _BLUE, _BLUE, _GREEN, _AMBER, _BLUE],
    "back": None,
}
_LAYERS_6C = {
    "label": "Three layers, three levels of trust, with the trust boundary between "
             "them: deterministic Python does all matching, classification, and "
             "arithmetic, with the same result from the same inputs; AI narration, "
             "optional and on request, explains each break in plain language and does "
             "not decide what is a break; and the human reviewer, the authority, "
             "chooses the resolution per break, confirms adjustments and signs off.",
    "ol": "fd-layers", "li": "fd-layer",
    "stages": [("Deterministic Python",
                "All matching, classification, and arithmetic. Same inputs, same result.",
                None, None),
               ("AI narration (optional, on request)",
                "Explains each break in plain language. Does not decide what is a break.",
                None, None),
               ("Human reviewer (authority)",
                "Chooses resolution per break. Confirms adjustments. Signs off.",
                None, None)],
    "colours": [_BLUE, _GREEN, _AMBER],
    "back": None,
}
_LIFECYCLE_6C = {
    "label": "Lock persistence and re-run guard: a break goes from Open to Drafted "
             "(narrate), to Resolved (resolve), to Locked (persist); a locked break is "
             "flagged as stale and re-opened if figures change.",
    "ol": "fd-flow fd-worded fd-loop", "li": "fd-stage",
    "stages": [("Open", None, None, None),
               ("Drafted", None, None, "narrate"),
               ("Resolved", None, None, "resolve"),
               ("Locked", None, None, "persist")],
    "colours": [_BLUE, _GREEN, _AMBER, _GREEN],
    "back": "flagged as stale and re-opened if figures change",
}


class _Diagram6c(HTMLParser):
    """One diagram's HTML read back: the list's class and accessible name, each
    stage's class, colours and parts by class, the path back's words, and the
    heading, note and colour key if any."""

    def __init__(self, markup):
        super().__init__()
        self.ol, self.label, self.stages, self.back = None, None, [], None
        self.head, self.note, self.key = None, None, []
        self._open = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "ol" and self.ol is None:
            self.ol, self.label = attrs.get("class"), attrs.get("aria-label")
        if tag == "li" and {"fd-stage", "fd-layer"} & set(classes):
            style = dict(part.split(":") for part in attrs["style"].split(";"))
            self.stages.append({"class": attrs["class"],
                                "colours": (style["border-color"], style["background"])})
        if tag == "p" and "fd-return" in classes:
            self.back = ""
        self._open.append(classes)

    def handle_endtag(self, tag):
        self._open.pop()

    def handle_data(self, data):
        classes = self._open[-1] if self._open else []
        if ["fd-return"] in self._open:
            self.back += data
            return
        words = data.strip()
        if not words:
            return
        if ["fd-key"] in self._open:
            self.key.append(words)
        elif "fd-head" in classes:
            self.head = words
        elif "fd-note" in classes:
            self.note = words
        elif classes and classes[0] in ("fd-zone", "fd-arrow", "fd-title", "fd-text",
                                        "fd-foot"):
            self.stages[-1][classes[0]] = words


@pytest.fixture(scope="class")
def how_its_built_6c():
    """How it's built as AppTest renders it, and its diagrams read back in page
    order."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_HOME), default_timeout=180).run()
    at.switch_page("views/4_How_Its_Built.py").run()
    assert not at.exception
    flows = [_Diagram6c(m.value) for m in at.main.markdown
             if m.value.startswith('<figure class="fd">')]
    return at, flows


def _check_6c(flow, expected):
    """A diagram has the expected name, kind, words in order, and colours."""
    assert flow.label == expected["label"]
    assert flow.ol == expected["ol"]
    assert [s["class"] for s in flow.stages] == [expected["li"]] * len(expected["stages"])
    assert [(s.get("fd-title"), s.get("fd-text"), s.get("fd-foot"), s.get("fd-arrow"))
            for s in flow.stages] == expected["stages"]
    assert [s["colours"] for s in flow.stages] == expected["colours"]
    assert flow.back == expected["back"]
    assert (flow.head, flow.note, flow.key) == (None, None, [])


class TestStep6cDiagrams:
    """The three diagrams on How it's built, as HTML lists readable on a phone."""

    def test_6c_diagrams_are_html_lists_not_frames(self, how_its_built_6c):
        import re
        at, flows = how_its_built_6c
        assert not at.get("iframe"), "no fixed-height frame left on the page"
        assert len(flows) == 3, "the three diagrams are HTML lists on the page"
        # Each sits where its frame was, just under its lead-in, and the two
        # captions that followed a frame follow its list, unchanged
        values = [getattr(e, "value", None) for e in at.main.children.values()]
        figures = [i for i, v in enumerate(values)
                   if isinstance(v, str) and v.startswith('<figure class="fd">')]
        leads = ["**The work happens in separated layers.**",
                 "**Three layers, three levels of trust.**",
                 "#### Lock persistence and re-run guard"]
        assert figures == [values.index(lead) + 1 for lead in leads]
        assert values[figures[0] + 1] == (
            "The seven phases in order. In the web app, narration is optional "
            "and the controller reviews the breaks before choosing to invoke it.")
        assert values[figures[1] + 1] == (
            "The trust boundary runs between these layers. The model reads what "
            "Python has classified and writes prose; it never touches the matching "
            "or the arithmetic.")
        # The diagram CSS is in inject_css: stacked on a phone, a row from 640
        # px, and no text in a diagram under 13 px
        from streamlit_app.lib import flow_diagram, theme
        css = flow_diagram.flow_css(theme.DARK_BLUE, theme.MUTED, theme.RULE)
        assert css in theme.inject_css()
        assert "@media (min-width: 640px)" in css and "ol.fd-flow" in css
        sizes = [int(s) for s in re.findall(r"font-size: (\d+)px", css)]
        assert sizes and min(sizes) >= 13
        # The renderer imports nothing from an app, so it copies byte for byte,
        # and the page draws no SVG of its own
        source = Path(flow_diagram.__file__).read_text(encoding="utf-8")
        assert "import streamlit_app" not in source and "from streamlit_app" not in source
        page = (_VIEWS / "4_How_Its_Built.py").read_text(encoding="utf-8")
        assert "components" not in page and "<svg" not in page

    def test_6c_pipeline_seven_phases_in_order(self, how_its_built_6c):
        _, flows = how_its_built_6c
        assert len(flows) == 3, "the three diagrams are HTML lists on the page"
        _check_6c(flows[0], _PIPELINE_6C)

    def test_6c_governance_layers_in_order(self, how_its_built_6c):
        _, flows = how_its_built_6c
        assert len(flows) == 3, "the three diagrams are HTML lists on the page"
        _check_6c(flows[1], _LAYERS_6C)

    def test_6c_lock_lifecycle_in_order_with_its_path_back(self, how_its_built_6c):
        _, flows = how_its_built_6c
        assert len(flows) == 3, "the three diagrams are HTML lists on the page"
        _check_6c(flows[2], _LIFECYCLE_6C)


class TestStep6cRenderer:
    """lib/flow_diagram.py, extended for this page: an existing call draws as
    before, and each new kind is asked for by a new optional field or argument."""

    _A, _B = ("#1A3A5C", "#EAF2FB"), ("#2D6A9F", "#E6F1FB")

    def _stages(self, n=3, arrows=()):
        from streamlit_app.lib.flow_diagram import Role, Stage
        roles = [Role(*self._A), Role(*self._B)]
        return tuple(Stage(f"STAGE {i + 1}", f"text {i + 1}", "", roles[i % 2],
                           arrow=arrows[i] if i < len(arrows) and arrows[i] else "")
                     for i in range(n))

    def test_6c_existing_calls_draw_exactly_as_before(self):
        # The two strings below are what the unextended renderer (sha256
        # c7f576770aabe6da, the one Projects 1 to 4 share) draws for the same
        # calls, kept verbatim: no new class, no new markup.
        from streamlit_app.lib.flow_diagram import Boundary, Role, Stage, flow_html
        a, b = Role(*self._A), Role(*self._B)
        stages = (Stage("FIRST STEP", "Check it & sign", "the person", a),
                  Stage("SECOND", "Kept as is", "", b), Stage("THIRD", "", "the record", b))
        assert flow_html('Name: "it" & more', stages) == (
            '<figure class="fd"><ol class="fd-flow" aria-label="Name: &quot;it&quot; &amp; '
            'more"><li class="fd-stage" style="border-color:#1A3A5C;background:#EAF2FB">'
            '<span class="fd-title" style="color:#1A3A5C">FIRST STEP</span> <span class="'
            'fd-text">Check it &amp; sign</span> <span class="fd-foot" style="color:#1A3A5C'
            '">the person</span></li><li class="fd-stage" style="border-color:#2D6A9F;'
            'background:#E6F1FB"><span class="fd-title" style="color:#2D6A9F">SECOND</span>'
            ' <span class="fd-text">Kept as is</span></li><li class="fd-stage" style="'
            'border-color:#2D6A9F;background:#E6F1FB"><span class="fd-title" style="color:'
            '#2D6A9F">THIRD</span> <span class="fd-foot" style="color:#2D6A9F">the record'
            '</span></li></ol></figure>')
        assert flow_html("Name", stages, heading="Heading", note="A note.",
                         key=((a, "One"), (b, "Two")),
                         boundary=Boundary(1, "Zone one", "Zone two")) == (
            '<figure class="fd"><p class="fd-head">Heading</p><ol class="fd-flow fd-zoned" '
            'aria-label="Name"><li class="fd-stage" style="border-color:#1A3A5C;background:'
            '#EAF2FB"><span class="fd-zone">Zone one</span> <span class="fd-title" style="'
            'color:#1A3A5C">FIRST STEP</span> <span class="fd-text">Check it &amp; sign'
            '</span> <span class="fd-foot" style="color:#1A3A5C">the person</span></li><li '
            'class="fd-stage fd-cross" style="border-color:#2D6A9F;background:#E6F1FB">'
            '<span class="fd-zone">Zone two</span> <span class="fd-title" style="color:'
            '#2D6A9F">SECOND</span> <span class="fd-text">Kept as is</span></li><li class="'
            'fd-stage" style="border-color:#2D6A9F;background:#E6F1FB"><span class="'
            'fd-title" style="color:#2D6A9F">THIRD</span> <span class="fd-foot" style="'
            'color:#2D6A9F">the record</span></li></ol><p class="fd-note">A note.</p><ul '
            'class="fd-key"><li><span class="fd-swatch" aria-hidden="true" style="'
            'border-color:#1A3A5C;background:#EAF2FB"></span> One</li><li><span class="'
            'fd-swatch" aria-hidden="true" style="border-color:#2D6A9F;background:#E6F1FB">'
            '</span> Two</li></ul></figure>')

    def test_6c_css_keeps_every_rule_and_only_adds(self):
        import hashlib
        import re
        from streamlit_app.lib import flow_diagram, theme
        css = flow_diagram.flow_css(theme.DARK_BLUE, theme.MUTED, theme.RULE)
        # The first 4313 characters are the unextended renderer's CSS for these
        # colours, byte for byte (Project 1 renders the same, as flow_css.css)
        assert hashlib.sha256(css[:4313].encode("utf-8")).hexdigest() == (
            "81a1a1b943aa80de9d8cad8d415117d3da8492014ae6d2d55ae494b457ba5232")
        assert css[4312:4314] == "\n\n"
        # Everything after adds rules, each under a class no earlier diagram has
        new = {"fd-worded", "fd-arrow", "fd-loop", "fd-return", "fd-nobr", "fd-layers",
               "fd-layer"} | {f"fd-row{width}" for width in flow_diagram.ROW_MINS}
        added = re.sub(r"/\*.*?\*/", "", css[4313:], flags=re.S)
        rules = re.findall(r"([^{}]+)\{[^{}]*\}", added)
        assert len(rules) == 20 + 10 * len(flow_diagram.ROW_MINS)
        for selectors in rules:
            for selector in selectors.split(","):
                assert new & set(re.findall(r"\.(fd-[a-z0-9]+)", selector)), selector
        sizes = [int(s) for s in re.findall(r"font-size: (\d+)px", added)]
        assert sizes and min(sizes) >= 13

    def test_6c_words_on_an_arrow(self):
        from streamlit_app.lib.flow_diagram import ARROW_GAP, flow_css, flow_html
        html = flow_html("Name", self._stages(3, arrows=(None, "go on", "")))
        flow = _Diagram6c(html)
        assert flow.ol == "fd-flow fd-worded"
        assert [s.get("fd-arrow") for s in flow.stages] == [None, "go on", None]
        # The words come first in their stage's item, before its title
        assert ('<li class="fd-stage" style="border-color:#2D6A9F;background:#E6F1FB">'
                '<span class="fd-arrow">go on</span> <span class="fd-title"') in html
        # There is no arrow into the first stage, so no words for one
        with pytest.raises(ValueError):
            flow_html("Name", self._stages(2, arrows=("before the first",)))
        css = flow_css("#000000", "#777777", "#CCCCCC")
        assert ".fd ol.fd-flow .fd-arrow {" in css and "white-space: nowrap;" in css
        assert f"margin: 0 0 0 {ARROW_GAP}px;" in css and ARROW_GAP == 64

    def test_6c_a_path_back_from_the_last_stage_to_the_first(self):
        from streamlit_app.lib.flow_diagram import flow_css, flow_html
        html = flow_html("Name", self._stages(3), back="sent back if re-checked")
        flow = _Diagram6c(html)
        assert flow.ol == "fd-flow fd-loop" and flow.back == "sent back if re-checked"
        # After the list, with the number of stages for the CSS, and each
        # hyphenated word kept on one line
        assert html.endswith(
            '</ol><p class="fd-return" style="--fd-n:3">sent back if '
            '<span class="fd-nobr">re-checked</span></p></figure>')
        with pytest.raises(ValueError):
            flow_html("Name", self._stages(1), back="nowhere to go")
        css = flow_css("#000000", "#777777", "#CCCCCC")
        assert ".fd ol.fd-flow.fd-loop::before {" in css and ".fd p.fd-return {" in css
        assert "var(--fd-n)" in css and ".fd .fd-nobr { white-space: nowrap; }" in css

    def test_6c_layers_with_no_arrows(self):
        from streamlit_app.lib.flow_diagram import Boundary, flow_css, flow_html
        html = flow_html("Name", self._stages(3), layers=True)
        flow = _Diagram6c(html)
        assert flow.ol == "fd-layers" and "fd-flow" not in html
        assert [s["class"] for s in flow.stages] == ["fd-layer"] * 3
        assert [s["fd-title"] for s in flow.stages] == ["STAGE 1", "STAGE 2", "STAGE 3"]
        for kwargs in ({"back": "back"}, {"row_min": 680},
                       {"boundary": Boundary(1, "one", "two")}):
            with pytest.raises(ValueError):
                flow_html("Name", self._stages(3), layers=True, **kwargs)
        with pytest.raises(ValueError):
            flow_html("Name", self._stages(3, arrows=(None, "on")), layers=True)
        css = flow_css("#000000", "#777777", "#CCCCCC")
        assert ".fd ol.fd-layers > li.fd-layer {" in css and "gap: 8px;" in css

    def test_6c_a_larger_row_minimum(self):
        from streamlit_app.lib.flow_diagram import ROW_MINS, Boundary, flow_css, flow_html
        # The widths on offer are fixed, so the CSS is fixed: 660 px (Project
        # 4's governance flow) and 680 px (this page's seven phases)
        assert ROW_MINS == (660, 680)
        css = flow_css("#000000", "#777777", "#CCCCCC")
        for width in ROW_MINS:
            html = flow_html("Name", self._stages(7), row_min=width)
            assert _Diagram6c(html).ol == f"fd-flow fd-row{width}"
            # Stacked from 600 px up to the minimum, a boundary included; from
            # there a row in which no box is narrower than its longest word
            assert f"@container fd (min-width: 600px) and (width < {width}px) {{" in css
            assert f".fd ol.fd-flow.fd-row{width} > li.fd-cross::after {{" in css
            assert (f"@container fd (min-width: {width}px) {{\n"
                    f"        .fd ol.fd-flow.fd-row{width} > li.fd-stage "
                    f"{{ min-width: auto; }}") in css
            zoned = flow_html("Name", self._stages(3), row_min=width,
                              boundary=Boundary(1, "one", "two"))
            assert _Diagram6c(zoned).ol == f"fd-flow fd-zoned fd-row{width}"
        for width in (600, 640, 700, "660", 660.0, True):
            with pytest.raises(ValueError):
                flow_html("Name", self._stages(7), row_min=width)
        with pytest.raises(ValueError):
            flow_html("Name", self._stages(7), row_min=680, back="back")
        with pytest.raises(ValueError):
            flow_html("Name", self._stages(7, arrows=(None, "on")), row_min=680)
