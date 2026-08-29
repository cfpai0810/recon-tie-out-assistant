# =============================================================================
# pages/2_Reconciliation.py: the reconciliation surface
# =============================================================================
# Phase D: AI explanations (key-gated, conversation-style), sign-off action,
# and reconciliation-report PDF download. All deterministic matching and
# resolution controls from Phase C remain unchanged.
# =============================================================================

import html as _html
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import streamlit as st
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import (
    inject_css, break_badge_html, resolution_badge_html,
    DARK_BLUE, MUTED, RULE,
)
from streamlit_app.lib.key_gate import render_key_gate
from streamlit_app.lib.errors import friendly_message
from streamlit_app.lib.audit import record_run
from config import (
    BANK_STATEMENT_FILE, CASH_BOOK_FILE, ENTITY_NAME, TOLERANCE_ABS,
    TOLERANCE_REL, DATA_DIR,
)
from src.step1_load import load, load_raw_table
from src.step2_validate import validate
from src.step3_match import match
from src.step4_classify import classify
from src.step6_resolve import (
    _resolution_from_choice, compute_resolved_position, _figures_hash,
)
from src.step5_narrate import narrate, _build_ai_prompt, _templated_draft
from src.step7_record import build_pdf_bytes
from src.state import Break, BreakType, MatchType, ResolutionType, Resolution


# -- CSS ----------------------------------------------------------------------
st.markdown(inject_css(), unsafe_allow_html=True)

# -- Header -------------------------------------------------------------------
st.markdown(
    '<div class="sc-header">'
    '<h1>Reconciliation</h1>'
    '<p>Match the bank statement against the cash book, classify breaks, '
    'and resolve each difference with a clear audit trail.</p>'
    '</div>',
    unsafe_allow_html=True,
)

# -- Key gate (sidebar) -------------------------------------------------------
client = render_key_gate()
st.session_state["live_client"] = client


# =============================================================================
# DATA LOAD
# =============================================================================

@st.cache_data
def _load_sample():
    return load(str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))


@st.cache_data
def _reconcile(file_a, file_b, tolerance_abs=None, tolerance_rel=None):
    if tolerance_abs is None:
        tolerance_abs = TOLERANCE_ABS
    if tolerance_rel is None:
        tolerance_rel = TOLERANCE_REL
    state = load(file_a, file_b)
    state = validate(state)
    state = match(state, tolerance=tolerance_abs, tolerance_rel=tolerance_rel)
    state = classify(state)
    return state


try:
    loaded_state = _load_sample()
except ValueError as exc:
    st.error("Could not load the data files: {}".format(exc))
    st.stop()


# =============================================================================
# INPUT TABLES
# =============================================================================

st.markdown("#### {}, June 2026".format(ENTITY_NAME))
st.caption(
    "Two different real-world formats: a bank statement export and a cash "
    "book. The tool maps each into a common shape to reconcile across "
    "the difference.")

col_a, col_b = st.columns(2)

_raw_cols_a, _raw_rows_a = load_raw_table(str(BANK_STATEMENT_FILE))
_raw_cols_b, _raw_rows_b = load_raw_table(str(CASH_BOOK_FILE))

with col_a:
    st.markdown("**{}** ({} rows)".format(
        loaded_state.side_a_label, len(_raw_rows_a)))
    if _raw_rows_a:
        st.dataframe(
            pd.DataFrame(_raw_rows_a, columns=_raw_cols_a),
            use_container_width=True,
            hide_index=True,
            height=530,
        )

with col_b:
    st.markdown("**{}** ({} rows)".format(
        loaded_state.side_b_label, len(_raw_rows_b)))
    if _raw_rows_b:
        st.dataframe(
            pd.DataFrame(_raw_rows_b, columns=_raw_cols_b),
            use_container_width=True,
            hide_index=True,
            height=530,
        )

if loaded_state.problems:
    n_good = len(loaded_state.side_a) + len(loaded_state.side_b)
    st.warning(
        "{} row(s) could not be read and were skipped. "
        "The reconciliation will run on the {} rows that were "
        "successfully read.".format(
            len(loaded_state.problems), n_good))
    with st.expander("Skipped rows"):
        for p in loaded_state.problems:
            st.text("Side {}, row {}: {} ({})".format(
                p.side, p.row_number, p.message, p.column))

_MESSY_BANK = DATA_DIR / "messy_bank.csv"
_MESSY_BOOK = DATA_DIR / "messy_book.csv"

if _MESSY_BANK.exists() and _MESSY_BOOK.exists():
    with st.expander("Messy data sample (Tier 2 edge cases)"):
        st.caption(
            "A second pair of files with heavier formatting: pound, "
            "dollar, and euro currency symbols, European comma-decimal "
            "notation, trailing minus signs, accounting parentheses in "
            "debit/credit columns, leading plus signs, padded whitespace, "
            "space-separated thousands, and extra chrome rows.")
        _mc_a, _mr_a = load_raw_table(str(_MESSY_BANK))
        _mc_b, _mr_b = load_raw_table(str(_MESSY_BOOK))
        mc1, mc2 = st.columns(2)
        with mc1:
            st.markdown("**Messy bank** ({} rows)".format(len(_mr_a)))
            if _mr_a:
                st.dataframe(
                    pd.DataFrame(_mr_a, columns=_mc_a),
                    use_container_width=True,
                    hide_index=True,
                )
        with mc2:
            st.markdown("**Messy book** ({} rows)".format(len(_mr_b)))
            if _mr_b:
                st.dataframe(
                    pd.DataFrame(_mr_b, columns=_mc_b),
                    use_container_width=True,
                    hide_index=True,
                )
        if st.button("Reconcile messy data", key="btn_reconcile_messy"):
            try:
                messy_state = _reconcile(str(_MESSY_BANK), str(_MESSY_BOOK))
                st.success(
                    "Loaded: {} bank rows, {} book rows. "
                    "{} clean matches, {} breaks, raw difference {:,.2f}.".format(
                        len(messy_state.side_a), len(messy_state.side_b),
                        messy_state.matched_count, len(messy_state.breaks),
                        messy_state.raw_difference))
            except ValueError as exc:
                st.error("Could not reconcile: {}".format(exc))

st.markdown("---")


# =============================================================================
# SHARED HELPERS
# =============================================================================

def _render_summary(state):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            '<div class="sc-metric">'
            '<div class="label">{} total</div>'
            '<div class="value">{:,.2f}</div>'
            '</div>'.format(state.side_a_label, state.side_a_total),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            '<div class="sc-metric">'
            '<div class="label">{} total</div>'
            '<div class="value">{:,.2f}</div>'
            '</div>'.format(state.side_b_label, state.side_b_total),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            '<div class="sc-metric">'
            '<div class="label">Raw difference (A − B)</div>'
            '<div class="value">{:,.2f}</div>'
            '</div>'.format(state.raw_difference),
            unsafe_allow_html=True,
        )

    st.write("")

    n_breaks = len(state.breaks)
    bc = state.break_counts

    m2o_count = bc.get("many_to_one_suggestion", 0)
    non_m2o_breaks = n_breaks - m2o_count

    match_parts = ["{} clean matches".format(state.matched_count)]
    if m2o_count:
        match_parts.append("{} many-to-one suggestion{}".format(
            m2o_count, "s" if m2o_count != 1 else ""))
    if non_m2o_breaks == 0 and m2o_count == 0:
        match_parts.append("no breaks")
    elif non_m2o_breaks > 0:
        break_parts = []
        for bt_val, label in [
            ("amount_mismatch", "amount mismatch"),
            ("reference_mismatch", "reference mismatch"),
            ("unmatched", "unmatched"),
        ]:
            count = bc.get(bt_val, 0)
            if count:
                break_parts.append("{} {}".format(
                    count, label if count == 1 else label + "es"
                    if bt_val != "unmatched" else label))
        match_parts.append("{} break{}: {}".format(
            non_m2o_breaks, "s" if non_m2o_breaks != 1 else "",
            ", ".join(break_parts)))

    st.markdown("**{}.**".format(". ".join(match_parts)))


def _render_break_transactions(brk, state):
    if brk.side_a_records and brk.side_b_records:
        ca, cb = st.columns(2)
        with ca:
            st.markdown("**Side A** ({})".format(state.side_a_label))
            for r in brk.side_a_records:
                st.text("{} {} {:>12,.2f}\n{}".format(
                    r.date, r.reference, r.signed_amount, r.description))
        with cb:
            st.markdown("**Side B** ({})".format(state.side_b_label))
            for r in brk.side_b_records:
                st.text("{} {} {:>12,.2f}\n{}".format(
                    r.date, r.reference, r.signed_amount, r.description))
    elif brk.side_a_records:
        st.markdown("**Side A** ({})".format(state.side_a_label))
        for r in brk.side_a_records:
            st.text("{} {} {:>12,.2f}\n{}".format(
                r.date, r.reference, r.signed_amount, r.description))
        st.caption("No counterpart on side B ({}).".format(state.side_b_label))
    elif brk.side_b_records:
        st.markdown("**Side B** ({})".format(state.side_b_label))
        for r in brk.side_b_records:
            st.text("{} {} {:>12,.2f}\n{}".format(
                r.date, r.reference, r.signed_amount, r.description))
        st.caption("No counterpart on side A ({}).".format(state.side_a_label))

    fig_parts = []
    if brk.side_a_records:
        fig_parts.append("A: {:,.2f}".format(brk.side_a_amount))
    if brk.side_b_records:
        fig_parts.append("B: {:,.2f}".format(brk.side_b_amount))
    fig_parts.append("Difference: {:,.2f}".format(brk.difference))
    st.caption(" | ".join(fig_parts))


def _render_position_panel(state):
    def _dec(v):
        return v if isinstance(v, Decimal) else Decimal(str(v))

    timing = adjust = dismiss = investigate = unresolved = Decimal("0")
    confirmed_m2o = Decimal("0")
    for brk in state.breaks:
        diff = _dec(brk.difference)
        res = state.resolutions.get(brk.stable_break_key)
        if res is None:
            unresolved += diff
        elif res.resolution_type == ResolutionType.ACCEPT_AS_TIMING:
            timing += diff
        elif res.resolution_type == ResolutionType.ADJUST:
            adjust += diff
        elif res.resolution_type == ResolutionType.DISMISS:
            dismiss += diff
        elif res.resolution_type == ResolutionType.CONFIRM_MATCH:
            confirmed_m2o += diff
        elif res.resolution_type == ResolutionType.INVESTIGATE:
            investigate += diff

    tol_absorbed = round(sum(
        (_dec(m.difference) for m in state.matches
         if m.match_type == MatchType.TOLERANCE),
        Decimal("0"),
    ), 2)

    resolved_position = compute_resolved_position(state)

    def _row(label, value, indent=False, bold=False, bt=False, bb=False):
        s1 = "padding:5px 0;"
        s2 = "padding:5px 0; text-align:right; font-feature-settings:'tnum' 1;"
        if indent:
            s1 += " padding-left:16px;"
        if bold:
            s1 += " font-weight:600;"
            s2 += " font-weight:600;"
        if bt:
            s1 += " border-top:2px solid {};".format(RULE)
            s2 += " border-top:2px solid {};".format(RULE)
        if bb:
            s1 += " border-bottom:1px solid {};".format(RULE)
            s2 += " border-bottom:1px solid {};".format(RULE)
        return ('<tr><td style="{}">{}</td>'
                '<td style="{}">{:,.2f}</td></tr>'.format(
                    s1, label, s2, value))

    section_hdr = (
        '<tr><td colspan="2" style="padding:10px 0 4px; font-style:italic;'
        ' color:{};">Comprising:</td></tr>'.format(MUTED))

    html_rows = [
        _row("Raw difference (A − B)", state.raw_difference,
             bold=True, bb=True),
        _row("Adjustments applied", -adjust, indent=True),
        _row("Items dismissed", -dismiss, indent=True),
        _row("Confirmed many-to-one matches", -confirmed_m2o, indent=True),
        _row("Resolved position", resolved_position, bold=True, bt=True),
        section_hdr,
        _row("Timing differences (outstanding)", timing, indent=True),
        _row("Under investigation", investigate, indent=True),
        _row("Not yet resolved", unresolved, indent=True),
        _row("Tolerance absorbed in matches", tol_absorbed, indent=True),
    ]

    table = (
        '<table style="width:100%; max-width:500px; border-collapse:collapse;'
        ' font-family:Inter,sans-serif;">'
        + "".join(html_rows) + '</table>')
    st.markdown(table, unsafe_allow_html=True)

    n_resolved = sum(
        1 for b in state.breaks
        if b.stable_break_key in state.resolutions)
    n_total = len(state.breaks)

    if n_total == 0:
        st.success("No breaks. The two sides match.")
    elif n_resolved == n_total:
        st.success("All breaks resolved. The difference is fully explained.")
    else:
        still = n_total - n_resolved
        st.info("{} of {} break{} resolved. {} still to resolve.".format(
            n_resolved, n_total, "s" if n_total != 1 else "", still))


# =============================================================================
# RESOLUTION STATE (session-scoped)
# =============================================================================

_RES_KEY = "p5_resolutions"
_PENDING_KEY = "p5_pending_adjust"
_DRAFTS_KEY = "p5_ai_drafts"
_PROMPTS_KEY = "p5_ai_prompts"
_SIGNED_OFF_KEY = "p5_signed_off"
_REJECTED_M2O_KEY = "p5_rejected_m2o"

if _RES_KEY not in st.session_state:
    st.session_state[_RES_KEY] = {}
if _PENDING_KEY not in st.session_state:
    st.session_state[_PENDING_KEY] = None
if _REJECTED_M2O_KEY not in st.session_state:
    st.session_state[_REJECTED_M2O_KEY] = {}


def _decompose_m2o(brk):
    """Decompose a many-to-one suggestion into individual unmatched breaks."""
    breaks = []
    for r in brk.side_a_records:
        breaks.append(Break(
            break_type=BreakType.UNMATCHED,
            side_a_records=[r],
            side_b_records=[],
            side_a_amount=r.signed_amount,
            side_b_amount=Decimal("0"),
            difference=r.signed_amount,
        ))
    for r in brk.side_b_records:
        breaks.append(Break(
            break_type=BreakType.UNMATCHED,
            side_a_records=[],
            side_b_records=[r],
            side_a_amount=Decimal("0"),
            side_b_amount=r.signed_amount,
            difference=round(-r.signed_amount, 2),
        ))
    return breaks


def _apply_m2o_rejections(state):
    """Replace rejected many-to-one suggestions with individual unmatched breaks."""
    rejected = st.session_state.get(_REJECTED_M2O_KEY, {})
    if not rejected:
        return state
    new_breaks = []
    for brk in state.breaks:
        if brk.stable_break_key in rejected:
            new_breaks.extend(rejected[brk.stable_break_key])
        else:
            new_breaks.append(brk)
    state.breaks = new_breaks
    state.break_counts = {}
    for b in state.breaks:
        state.break_counts[b.break_type.value] = (
            state.break_counts.get(b.break_type.value, 0) + 1)
    return state


def _apply_session_resolutions(state):
    state.resolutions = dict(st.session_state[_RES_KEY])
    state.resolved_position = compute_resolved_position(state)
    return state


def _lock_resolution(key, resolution):
    st.session_state[_RES_KEY][key] = resolution


# =============================================================================
# AI EXPLANATIONS (Phase D)
# =============================================================================

def _get_draft(brk, state):
    """Return (draft, is_ai) from session-stored AI drafts or templated."""
    ai_drafts = st.session_state.get(_DRAFTS_KEY, {})
    if brk.stable_break_key in ai_drafts:
        draft = ai_drafts[brk.stable_break_key]
        templated = _templated_draft(
            brk, state.side_a_label, state.side_b_label)
        is_ai = (draft.explanation != templated.explanation)
        return draft, is_ai
    return _templated_draft(brk, state.side_a_label, state.side_b_label), False


def _draft_explanations(state, client, tolerance_abs=None, tolerance_rel=None):
    """Call the real narrate phase; store drafts and prompts in session."""
    narrate_state = _reconcile(
        str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE),
        tolerance_abs=tolerance_abs, tolerance_rel=tolerance_rel)
    narrate_state = narrate(narrate_state, client)
    st.session_state[_DRAFTS_KEY] = dict(narrate_state.drafts)
    prompts = {}
    for brk in narrate_state.breaks:
        prompts[brk.stable_break_key] = _build_ai_prompt(
            brk, narrate_state.side_a_label, narrate_state.side_b_label)
    st.session_state[_PROMPTS_KEY] = prompts


def _render_explanation(brk, state):
    """Render the explanation section on a break card with governance."""
    draft, is_ai = _get_draft(brk, state)
    if is_ai:
        st.caption("AI explanation")
    else:
        st.caption("Templated explanation")
    st.markdown(
        '<div class="sc-narrative">{}</div>'.format(
            _html.escape(draft.explanation)),
        unsafe_allow_html=True)
    st.caption("Suggested follow-up: {}".format(draft.follow_up))
    if is_ai:
        prompts = st.session_state.get(_PROMPTS_KEY, {})
        prompt = prompts.get(brk.stable_break_key)
        if prompt:
            with st.expander("What the model was asked"):
                st.code(prompt, language=None)


# =============================================================================
# WORKBENCH
# =============================================================================

def _render_workbench(state):
    if not state.breaks:
        st.success("Reconciled, no breaks. Every transaction matched cleanly.")
        return

    open_breaks = [b for b in state.breaks
                   if b.stable_break_key not in state.resolutions]
    resolved_breaks = [b for b in state.breaks
                       if b.stable_break_key in state.resolutions]

    if open_breaks:
        st.markdown("**{} break{} to resolve**".format(
            len(open_breaks), "s" if len(open_breaks) != 1 else ""))
        for brk in open_breaks:
            _render_open_break(brk, state)
    elif state.breaks:
        st.success("All breaks resolved.")

    if resolved_breaks:
        with st.expander("Resolved ({})".format(len(resolved_breaks)),
                         expanded=False):
            for brk in resolved_breaks:
                _render_resolved_compact(brk, state)


def _render_open_break(brk, state):
    key = brk.stable_break_key

    if st.session_state[_PENDING_KEY] == key:
        _render_pending_confirm(brk, state)
        return

    with st.container(border=True):
        st.markdown(break_badge_html(brk.break_type.value),
                    unsafe_allow_html=True)
        _render_break_transactions(brk, state)

        if brk.break_type == BreakType.MANY_TO_ONE_SUGGESTION:
            _render_m2o_controls(brk, key, state)
            return

        _render_explanation(brk, state)

        action = st.radio(
            "Resolution",
            ["Accept as timing", "Adjust", "Investigate", "Dismiss"],
            key="action_{}".format(key),
            horizontal=True,
        )

        if action == "Accept as timing":
            _controls_timing(brk, key)
        elif action == "Adjust":
            _controls_adjust(brk, key)
        elif action == "Investigate":
            _controls_investigate(brk, key)
        elif action == "Dismiss":
            _controls_dismiss(brk, key)


def _controls_timing(brk, key):
    presets = [
        "Deposit in transit",
        "Outstanding cheque",
        "In-transit transfer",
        "End-of-month cutoff",
        "Other",
    ]
    reason = st.selectbox("Reason", presets, key="timing_pre_{}".format(key))
    if reason == "Other":
        reason = st.text_input(
            "Describe the timing difference",
            key="timing_txt_{}".format(key),
        ) or "Timing difference"
    if st.button("Accept as timing", key="apply_t_{}".format(key)):
        res = _resolution_from_choice("T", reason, None, brk)
        if res:
            _lock_resolution(key, res)
            st.rerun()


def _controls_adjust(brk, key):
    amount = st.number_input(
        "Adjustment amount",
        value=float(brk.difference),
        key="adj_amt_{}".format(key),
        format="%.2f",
    )
    desc = st.text_input(
        "Description of the adjusting entry",
        key="adj_txt_{}".format(key),
        placeholder="What the adjusting entry is for",
    ) or "Adjusting entry"
    if st.button("Apply adjustment", key="apply_a_{}".format(key)):
        res = _resolution_from_choice("A", desc, amount, brk)
        if res:
            st.session_state[_PENDING_KEY] = key
            st.session_state["p5_pending_res_{}".format(key)] = res
            st.rerun()


def _render_pending_confirm(brk, state):
    key = brk.stable_break_key
    res = st.session_state.get("p5_pending_res_{}".format(key))
    if res is None:
        st.session_state[_PENDING_KEY] = None
        st.rerun()
        return

    with st.container(border=True):
        st.markdown(break_badge_html(brk.break_type.value),
                    unsafe_allow_html=True)
        _render_break_transactions(brk, state)

        st.warning(
            "**Confirm adjustment.** Book an entry of {:,.2f} to the "
            "{}: {}".format(
                res.adjustment_amount, state.side_b_label,
                res.reviewer_text))

        c1, c2 = st.columns(2)
        if c1.button("Confirm adjustment", key="confirm_{}".format(key),
                      type="primary"):
            _lock_resolution(key, res)
            st.session_state[_PENDING_KEY] = None
            del st.session_state["p5_pending_res_{}".format(key)]
            st.rerun()
        if c2.button("Cancel", key="cancel_{}".format(key)):
            st.session_state[_PENDING_KEY] = None
            del st.session_state["p5_pending_res_{}".format(key)]
            st.rerun()


def _controls_investigate(brk, key):
    note = st.text_input(
        "Investigation note (optional)",
        key="inv_txt_{}".format(key),
        placeholder="What needs further investigation",
    )
    if st.button("Mark for investigation", key="apply_i_{}".format(key)):
        text = note if note else "Marked for investigation"
        res = _resolution_from_choice("I", text, None, brk)
        if res:
            _lock_resolution(key, res)
            st.rerun()


def _controls_dismiss(brk, key):
    presets = [
        "Immaterial rounding difference",
        "Known system variance",
        "Below investigation threshold",
        "Other",
    ]
    reason = st.selectbox("Reason", presets, key="dism_pre_{}".format(key))
    if reason == "Other":
        reason = st.text_input(
            "Describe why this break is immaterial",
            key="dism_txt_{}".format(key),
        ) or "Dismissed as immaterial"
    if st.button("Dismiss", key="apply_d_{}".format(key)):
        res = _resolution_from_choice("D", reason, None, brk)
        if res:
            _lock_resolution(key, res)
            st.rerun()


def _render_m2o_controls(brk, key, state):
    st.info(
        "**Match suggestion.** The matcher found that the items on side B "
        "sum to the single item on side A (within tolerance and the "
        "14-day date window). Review the pairing and confirm or reject.")
    c1, c2 = st.columns(2)
    if c1.button("Confirm match", key="m2o_confirm_{}".format(key),
                  type="primary"):
        res = _resolution_from_choice(
            "C", "Many-to-one match confirmed by reviewer", None, brk)
        if res:
            _lock_resolution(key, res)
            st.rerun()
    if c2.button("Reject", key="m2o_reject_{}".format(key)):
        decomposed = _decompose_m2o(brk)
        st.session_state[_REJECTED_M2O_KEY][key] = decomposed
        if key in st.session_state[_RES_KEY]:
            del st.session_state[_RES_KEY][key]
        st.rerun()


def _render_resolved_compact(brk, state):
    key = brk.stable_break_key
    res = state.resolutions.get(key)
    if not res:
        return
    brk_badge = break_badge_html(brk.break_type.value)
    res_badge = resolution_badge_html(res.resolution_type.value)
    detail = _html.escape(res.reviewer_text)
    if res.adjustment_amount is not None:
        detail = "Adjusted {:,.2f} on {}. {}".format(
            res.adjustment_amount, state.side_b_label, detail)
    st.markdown("{} {} {}".format(brk_badge, res_badge, detail),
                unsafe_allow_html=True)


# =============================================================================
# WORKED EXAMPLE: pre-computed resolutions (all five types)
# =============================================================================

@st.cache_data
def _build_example_resolutions(breaks):
    resolutions = {}
    for brk in breaks:
        fh = _figures_hash(brk)
        if brk.break_type.value == "amount_mismatch":
            resolutions[brk.stable_break_key] = Resolution(
                resolution_type=ResolutionType.ADJUST,
                reviewer_text=(
                    "Correct Thornton Bakery invoice to 819.00 per "
                    "supplier credit note"),
                adjustment_amount=Decimal("72"),
                adjustment_side="B",
                timestamp="2026-06-30T15:00:00+00:00",
                figures_hash=fh,
            )
        elif brk.break_type.value == "reference_mismatch":
            resolutions[brk.stable_break_key] = Resolution(
                resolution_type=ResolutionType.DISMISS,
                reviewer_text=(
                    "Different reference formats for same transfer, "
                    "amounts agree, immaterial"),
                timestamp="2026-06-30T15:01:00+00:00",
                figures_hash=fh,
            )
        elif brk.break_type.value == "unmatched" and brk.side_a_records:
            ref = brk.side_a_records[0].reference
            if ref == "CHG-Q2":
                resolutions[brk.stable_break_key] = Resolution(
                    resolution_type=ResolutionType.INVESTIGATE,
                    reviewer_text=(
                        "Verify quarterly bank charge with account "
                        "manager"),
                    timestamp="2026-06-30T15:02:00+00:00",
                    figures_hash=fh,
                )
            else:
                resolutions[brk.stable_break_key] = Resolution(
                    resolution_type=ResolutionType.ADJUST,
                    reviewer_text="Record bank interest in cash book",
                    adjustment_amount=Decimal("12.15"),
                    adjustment_side="B",
                    timestamp="2026-06-30T15:06:00+00:00",
                    figures_hash=fh,
                )
        elif brk.break_type.value == "unmatched" and brk.side_b_records:
            ref = brk.side_b_records[0].reference
            if ref == "CHQ-1043":
                resolutions[brk.stable_break_key] = Resolution(
                    resolution_type=ResolutionType.ACCEPT_AS_TIMING,
                    reviewer_text=(
                        "Outstanding cheque, will clear next period"),
                    timestamp="2026-06-30T15:03:00+00:00",
                    figures_hash=fh,
                )
            elif ref == "RCT-DEL":
                resolutions[brk.stable_break_key] = Resolution(
                    resolution_type=ResolutionType.ACCEPT_AS_TIMING,
                    reviewer_text=(
                        "Delgado cheque lodged but not yet credited"),
                    timestamp="2026-06-30T15:04:00+00:00",
                    figures_hash=fh,
                )
            else:
                resolutions[brk.stable_break_key] = Resolution(
                    resolution_type=ResolutionType.ACCEPT_AS_TIMING,
                    reviewer_text=(
                        "Card takings banked after period end, "
                        "will clear next statement"),
                    timestamp="2026-06-30T15:05:00+00:00",
                    figures_hash=fh,
                )
        elif brk.break_type.value == "many_to_one_suggestion":
            resolutions[brk.stable_break_key] = Resolution(
                resolution_type=ResolutionType.CONFIRM_MATCH,
                reviewer_text=(
                    "Batch deposit confirmed: book items sum to "
                    "bank deposit within tolerance"),
                timestamp="2026-06-30T15:06:00+00:00",
                figures_hash=fh,
            )
    return resolutions


def _render_example_break(brk, state, res, draft, is_ai=False):
    with st.container(border=True):
        brk_badge = break_badge_html(brk.break_type.value)
        res_badge = resolution_badge_html(res.resolution_type.value)
        st.markdown("{} → {}".format(brk_badge, res_badge),
                    unsafe_allow_html=True)
        _render_break_transactions(brk, state)

        if is_ai:
            st.caption("AI explanation")
        else:
            st.caption("Templated explanation")
        st.markdown(
            '<div class="sc-narrative">{}</div>'.format(
                _html.escape(draft.explanation)),
            unsafe_allow_html=True)
        if is_ai:
            prompts = st.session_state.get(_PROMPTS_KEY, {})
            prompt = prompts.get(brk.stable_break_key)
            if prompt:
                with st.expander("What the model was asked"):
                    st.code(prompt, language=None)

        label = res.resolution_type.value.replace("_", " ").title()
        detail = _html.escape(res.reviewer_text)
        if res.adjustment_amount is not None:
            st.markdown(
                "**Resolution:** {} {:,.2f} on {}. {}".format(
                    label, res.adjustment_amount, state.side_b_label,
                    detail))
        else:
            st.markdown("**Resolution:** {}. {}".format(label, detail))


# =============================================================================
# TABS
# =============================================================================

st.markdown("#### Reconciliation")
st.caption(
    "Run the matching and classification pipeline, then review and "
    "resolve each break. The AI explanations are optional and need an "
    "API key.")

tab_live, tab_example = st.tabs(
    ["Run it yourself", "View a worked example"])


with tab_live:
    tc1, tc2 = st.columns(2)
    with tc1:
        tol_abs_input = st.number_input(
            "Absolute tolerance",
            value=float(TOLERANCE_ABS),
            min_value=0.0,
            step=0.01,
            format="%.2f",
            key="tol_abs",
            help="Maximum absolute difference for a within-tolerance match. "
                 "0 means exact amounts only.",
        )
    with tc2:
        tol_rel_pct = st.number_input(
            "Relative tolerance %",
            value=float(TOLERANCE_REL) * 100,
            min_value=0.0,
            step=0.1,
            format="%.2f",
            key="tol_rel_pct",
            help="Percentage of the larger amount. 0 means off (absolute "
                 "only). The effective bound is whichever is larger.",
        )
    tol_abs_val = Decimal(str(tol_abs_input))
    tol_rel_val = Decimal(str(tol_rel_pct / 100)) if tol_rel_pct else Decimal("0")

    if tol_abs_input > 100:
        st.warning(
            "Absolute tolerance {:,.2f} is unusually large. Values above "
            "a few pounds risk matching unrelated transactions.".format(
                tol_abs_input))
    if tol_rel_pct > 1:
        st.warning(
            "Relative tolerance {:.2f}% is above 1%. On a reconciliation, "
            "this is unusually permissive and risks false matches on "
            "large amounts.".format(tol_rel_pct))

    reconcile_clicked = st.button("Reconcile", type="primary",
                                  key="btn_reconcile")
    if reconcile_clicked:
        st.session_state["reconciled"] = True

    if st.session_state.get("reconciled"):
        try:
            recon_state = _reconcile(
                str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE),
                tolerance_abs=tol_abs_val, tolerance_rel=tol_rel_val)
        except ValueError as exc:
            st.error("Could not reconcile: {}".format(exc))
            st.stop()
        recon_state = _apply_m2o_rejections(recon_state)
        recon_state = _apply_session_resolutions(recon_state)

        st.markdown("##### Match summary")
        _render_summary(recon_state)

        tol_note = "Tolerance: {:.2f} (absolute)".format(float(tol_abs_val))
        if tol_rel_val:
            tol_note += ", {:.2f}% (relative)".format(float(tol_rel_val) * 100)
        st.caption(tol_note)

        st.markdown("---")

        st.markdown("##### Breaks")
        _render_workbench(recon_state)

        st.markdown("---")

        # -- AI explanations (Phase D) ------------------------------------
        st.markdown("##### AI explanations")
        if _DRAFTS_KEY in st.session_state:
            n_ai = sum(
                1 for brk in recon_state.breaks
                if _get_draft(brk, recon_state)[1])
            n_total = len(recon_state.breaks)
            if n_ai == n_total:
                st.success(
                    "AI explanations drafted for all {} breaks.".format(
                        n_total))
            elif n_ai > 0:
                st.success(
                    "{} of {} breaks received AI explanations; the rest "
                    "use templated fallbacks.".format(n_ai, n_total))
            else:
                st.info(
                    "Explanations drafted (templated fallback; the AI "
                    "call did not succeed for any break).")
        elif client:
            if st.button("Draft explanations", key="btn_draft"):
                with st.spinner(
                        "Drafting AI explanations for {} breaks...".format(
                            len(recon_state.breaks))):
                    try:
                        _draft_explanations(recon_state, client,
                                            tolerance_abs=tol_abs_val,
                                            tolerance_rel=tol_rel_val)
                        st.rerun()
                    except Exception as exc:
                        st.error(friendly_message(exc))
        else:
            st.info(
                "Paste an API key in the sidebar to draft AI "
                "explanations. The workbench uses templated "
                "explanations without a key.")

        st.markdown("---")

        st.markdown("##### Reconciled position")
        _render_position_panel(recon_state)

        st.markdown("---")

        # -- Sign off and downloads (Phase D) -----------------------------
        st.markdown("##### Sign off and downloads")

        signed_off_at = st.session_state.get(_SIGNED_OFF_KEY)

        if signed_off_at:
            st.success(
                "Reconciliation signed off: {}".format(signed_off_at))
        else:
            n_unresolved = sum(
                1 for b in recon_state.breaks
                if b.stable_break_key not in recon_state.resolutions)
            if n_unresolved > 0:
                st.info(
                    "{} break{} still open. You can still sign off; "
                    "the report will note them as unresolved.".format(
                        n_unresolved,
                        "s" if n_unresolved != 1 else ""))
            if st.button("Sign off reconciliation", key="btn_signoff",
                          type="primary"):
                ts = datetime.now(timezone.utc).strftime(
                    "%d %B %Y %H:%M UTC")
                st.session_state[_SIGNED_OFF_KEY] = ts
                record_run(recon_state)
                st.rerun()

        for brk in recon_state.breaks:
            draft, _ = _get_draft(brk, recon_state)
            recon_state.drafts[brk.stable_break_key] = draft

        pdf_bytes = build_pdf_bytes(
            recon_state, signed_off_at=signed_off_at,
            tolerance_abs=float(tol_abs_val),
            tolerance_rel=float(tol_rel_val))
        st.download_button(
            "Download reconciliation report (PDF)",
            data=pdf_bytes,
            file_name="reconciliation_report.pdf",
            mime="application/pdf",
            key="dl_pdf_live",
        )

    else:
        st.info(
            "Click Reconcile to run the matching and classification "
            "pipeline on the sample data. No API key needed.")


with tab_example:
    st.caption(
        "A worked example on the sample data at the default settings. "
        "No API key needed. The matching is deterministic: the same "
        "input always produces the same result.")

    try:
        example_state = _reconcile(
            str(BANK_STATEMENT_FILE), str(CASH_BOOK_FILE))
    except ValueError as exc:
        st.error("Could not reconcile: {}".format(exc))
        st.stop()

    st.markdown("##### Match summary")
    _render_summary(example_state)

    st.markdown("---")

    st.markdown("##### Breaks: all five resolution types applied")

    example_resolutions = _build_example_resolutions(example_state.breaks)
    example_state.resolutions = example_resolutions
    example_state.resolved_position = compute_resolved_position(
        example_state)

    for brk in example_state.breaks:
        res = example_resolutions.get(brk.stable_break_key)
        draft, is_ai = _get_draft(brk, example_state)
        if res:
            _render_example_break(brk, example_state, res, draft, is_ai)

    st.markdown("---")

    st.markdown("##### Reconciled position")
    _render_position_panel(example_state)

    st.markdown("---")

    st.markdown("##### Reconciliation report")

    for brk in example_state.breaks:
        draft, _ = _get_draft(brk, example_state)
        example_state.drafts[brk.stable_break_key] = draft

    example_pdf = build_pdf_bytes(
        example_state, signed_off_at="30 June 2026 15:05 UTC")
    st.download_button(
        "Download example report (PDF)",
        data=example_pdf,
        file_name="reconciliation_report_example.pdf",
        mime="application/pdf",
        key="dl_pdf_example",
    )


st.divider()
st.caption("Sample data only. All figures are illustrative.")
