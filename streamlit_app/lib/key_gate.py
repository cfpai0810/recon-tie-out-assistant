# =============================================================================
# lib/key_gate.py : the bring-your-own-key gate (foundation component)
# =============================================================================
# Renders the API-key input, holds the key in the server's memory (session
# state) for the live session only, and builds a per-session Anthropic client.
# The key is never written to disk and never logged. It is kept under a
# non-widget key so it stays in effect on every page for the rest of the
# session. This is reused by every tool page.
#
# Honest disclosure (per the master plan): "used only for your session, never
# stored", not "never leaves your device", because the key does reach the
# server to make the Claude call. Users who want their key to never leave
# their machine are pointed to the GitHub download.
# =============================================================================

import streamlit as st

from streamlit_app.lib.claude_client import build_client, looks_like_anthropic_key
from streamlit_app.lib.errors import KEY_LOOKS_COMPLETE, friendly_message

_KEPT = "anthropic_api_key"   # survives page changes; the widget's own state does not


def _keep_key():
    st.session_state[_KEPT] = st.session_state.get("byo_api_key", "")


def render_key_gate():
    """Render the key input in the sidebar. Returns the per-session client, or
    None if no valid key is present yet. Safe to call on every page."""
    st.sidebar.markdown("### Run it yourself")
    st.sidebar.caption(
        "Paste your Anthropic API key to run a live analysis. "
        "Don't have one? Use the example output on the main page, no key needed.")

    if _KEPT in st.session_state:
        st.session_state["byo_api_key"] = st.session_state[_KEPT]
    key = st.sidebar.text_input(
        "Anthropic API key",
        type="password",
        placeholder="sk-ant-...",
        key="byo_api_key",
        on_change=_keep_key,
        help="Held in the server's memory for this session only. Never stored, never logged.",
    )

    st.sidebar.caption(
        "Running a live analysis uses your own Anthropic API key and bills "
        "your Anthropic account a small amount per run. The worked examples "
        "are free and need no key.")

    st.sidebar.markdown(
        '<div class="sc-keynote">Your key is held in the server\'s memory for '
        'your session only, used to make the call, and never stored or logged. '
        'If you would rather your key never leave your own machine, download '
        'the project from GitHub and run it locally.</div>',
        unsafe_allow_html=True,
    )

    if not key:
        return None

    if not looks_like_anthropic_key(key):
        st.sidebar.warning(
            "That does not look like a complete Anthropic key. It should start "
            "with sk-ant- and be the full string.")
        return None

    try:
        client = build_client(key)
    except Exception as exc:
        st.sidebar.error(friendly_message(exc))
        return None

    st.sidebar.success(KEY_LOOKS_COMPLETE)
    return client


def has_live_client(client):
    """Small helper for pages to branch on live vs example mode."""
    return client is not None
