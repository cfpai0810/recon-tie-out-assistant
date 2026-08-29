# =============================================================================
# Home.py: the app shell and landing page (Streamlit entry point)
# =============================================================================
# Run locally: streamlit run streamlit_app/Home.py
# Streamlit >= 1.36 uses st.navigation + st.Page for multipage apps.
# =============================================================================

import sys
sys.dont_write_bytecode = True

from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css
from streamlit_app.lib.key_gate import render_key_gate


def _home_page():
    """Render the landing page content."""
    st.markdown(inject_css(), unsafe_allow_html=True)

    # -- Header ---------------------------------------------------------------
    st.markdown(
        '<div class="sc-header">'
        '<h1>Reconciliation and Tie-Out Assistant</h1>'
        '<p>A reconciliation tool for a finance controller: it checks '
        'whether two records of the same transactions, a bank statement '
        'and a cash book, agree. Where they do not, it classifies the '
        'differences (called breaks) and helps the reviewer resolve each '
        'one with a clear audit trail.</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    # -- The key gate (sidebar): returns a per-session client or None ---------
    client = render_key_gate()
    st.session_state["live_client"] = client

    # -- What this does -------------------------------------------------------
    st.markdown("#### What this does")
    st.write(
        "The tool works in two layers. First, deterministic Python matches "
        "every transaction between the bank statement and the cash book "
        "using five rules (exact key, within tolerance, amount mismatch, "
        "reference mismatch, many-to-one). Unmatched or mismatched items "
        "become typed breaks. Then, on request, an AI layer explains each "
        "break and suggests a follow-up action.")
    st.write(
        "The matching, classification, and reconciliation arithmetic run "
        "without an API key. The break tables, the reconciled position, "
        "and a worked example are all free to explore. Only the AI "
        "explanations need an Anthropic API key.")

    st.markdown("---")

    # -- Pages ----------------------------------------------------------------
    st.markdown("#### Pages")
    st.write(
        "**How it works** explains the tool in plain terms: the matching "
        "rules, what makes a break, how the reconciled position is "
        "computed, and what the output means for a reviewer.")
    st.write(
        "**Reconciliation** is the tool itself. It loads the sample bank "
        "statement and cash book, runs the matching and classification "
        "pipeline, and presents the breaks for resolution. A worked "
        "example runs at the default settings with no API key.")
    st.write(
        "**Your own data** explains how to run the reconciliation on your "
        "own bank statement and cash book locally: the file format, how "
        "to run it, and what stays private.")
    st.write(
        "**How it's built** is the technical and governance story: the "
        "deterministic matching core, the AI narration boundary, the "
        "lock persistence model, and the review stance.")

    st.markdown("---")

    # -- Sample-data status ---------------------------------------------------
    st.markdown("#### The sample company")
    st.write(
        "Everything here runs on Valencia Operations, a synthetic company "
        "invented for the demo. Its bank statement and cash book describe "
        "one month of transactions for June 2026, with 17 bank records and "
        "20 book records. Because the company and its numbers are entirely "
        "fictional, nothing you do on this site touches real or personal "
        "information.")

    DATA_DIR = ROOT / "data"
    expected = ["bank_statement.csv", "cash_book.csv"]
    present = [f for f in expected if (DATA_DIR / f).exists()]
    if len(present) == len(expected):
        st.success(
            "Sample dataset loaded, {} of {} files present. "
            "All figures on this site are illustrative.".format(
                len(present), len(expected)))
    else:
        missing = [f for f in expected if f not in present]
        st.warning(
            "The sample data is incomplete. Missing: " + ", ".join(missing) +
            ". The tool page needs these files in the project's data folder.")

    # -- Using your own API key -----------------------------------------------
    with st.expander("Using your own API key"):
        st.write(
            "The matching and reconciliation on the tool page are free and "
            "need no key. To get AI explanations for each break, you supply "
            "your own Anthropic API key, and each run bills your Anthropic "
            "account directly. This is not a free AI service; it is your key "
            "and your usage.")
        st.write(
            "To get a key, sign up at the Anthropic Console "
            "(console.anthropic.com), add a payment method under Settings, "
            "then open Settings and API keys and create a key. The key is "
            "shown once, so copy it when it is created. Paste it into the "
            "sidebar here to enable AI explanations.")
        st.write(
            "What it costs. Each run makes one short call to Claude Sonnet "
            "to narrate the breaks. At Sonnet's current rate of about three "
            "US dollars per million input tokens and fifteen per million "
            "output tokens, a single reconciliation is well under one US "
            "cent in practice.")
        st.caption(
            "Your key is used only for your session and is not stored by "
            "this site. On the web the data stays sample-only; to work on "
            "your own numbers, run the project locally from GitHub.")

    st.divider()
    st.caption("Sample data only. All figures are illustrative.")


# -- Multipage navigation (Streamlit >= 1.36) ---------------------------------
st.set_page_config(
    page_title="Reconciliation Assistant",
    page_icon="•",
    layout="centered",
    initial_sidebar_state="expanded",
)

PAGES_DIR = Path(__file__).resolve().parent / "pages"

pg = st.navigation([
    st.Page(_home_page, title="Home", icon=":material/home:"),
    st.Page(str(PAGES_DIR / "1_How_the_Model_Works.py"),
            title="How It Works", icon=":material/menu_book:"),
    st.Page(str(PAGES_DIR / "2_Reconciliation.py"),
            title="Reconciliation", icon=":material/balance:"),
    st.Page(str(PAGES_DIR / "3_Your_Own_Data.py"),
            title="Your Own Data", icon=":material/upload_file:"),
    st.Page(str(PAGES_DIR / "4_How_Its_Built.py"),
            title="How It's Built", icon=":material/build:"),
])

pg.run()
