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
