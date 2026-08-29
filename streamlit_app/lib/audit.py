# =============================================================================
# lib/audit.py: session audit for reconciliation runs
# =============================================================================
# Tracks reconciliation runs within the Streamlit session for governance.
# The CLI's recon/record.py handles the full JSONL audit and PDF report;
# this module tracks the lighter session-level record shown in the web UI.
#
# build_audit_record() is a pure function (no Streamlit dependency) so it can
# be tested in the main test suite.
# =============================================================================

from datetime import datetime, timezone

AUDIT_KEY = "recon_audit"


def build_audit_record(state):
    """Build one audit record from a completed reconciliation state."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reconciliation_id": state.reconciliation_id,
        "side_a_label": state.side_a_label,
        "side_b_label": state.side_b_label,
        "side_a_records": len(state.side_a),
        "side_b_records": len(state.side_b),
        "side_a_total": state.side_a_total,
        "side_b_total": state.side_b_total,
        "raw_difference": state.raw_difference,
        "resolved_position": state.resolved_position,
        "matched_count": state.matched_count,
        "break_count": len(state.breaks),
        "break_counts": state.break_counts,
        "resolutions": len(state.resolutions),
    }


def record_run(state):
    """Record one reconciliation run in the Streamlit session audit trail."""
    import streamlit as st
    record = build_audit_record(state)
    if AUDIT_KEY not in st.session_state:
        st.session_state[AUDIT_KEY] = []
    st.session_state[AUDIT_KEY].insert(0, record)
    return record


def get_runs():
    """Return the session audit trail (newest first), or an empty list."""
    import streamlit as st
    return st.session_state.get(AUDIT_KEY, [])
