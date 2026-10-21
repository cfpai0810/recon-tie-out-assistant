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

from streamlit_app.lib.theme import header_html, inject_css
from streamlit_app.lib.key_gate import render_key_gate

PAGES_DIR = Path(__file__).resolve().parent / "pages"

# The pages, built once so Home's links and the navigation share them. Titles
# are in sentence case; each address still comes from its file name.
HOW_IT_WORKS = st.Page(str(PAGES_DIR / "1_How_the_Model_Works.py"),
                       title="How it works", icon=":material/menu_book:")
RECONCILIATION = st.Page(str(PAGES_DIR / "2_Reconciliation.py"),
                         title="Reconciliation", icon=":material/balance:")
OWN_DATA = st.Page(str(PAGES_DIR / "3_Your_Own_Data.py"),
                   title="Your own data", icon=":material/upload_file:")
HOW_ITS_BUILT = st.Page(str(PAGES_DIR / "4_How_Its_Built.py"),
                        title="How it's built", icon=":material/build:")


def _home_page():
    """Render the landing page content."""
    st.markdown(inject_css(), unsafe_allow_html=True)
    st.markdown(header_html(
        "Reconciliation and Tie-Out Assistant",
        "A reconciliation tool for a finance controller: it checks whether a bank "
        "statement and a cash book agree, and helps the reviewer resolve each "
        "difference."),
        unsafe_allow_html=True)

    # -- The worked example, one click away and needing no key: the
    # Reconciliation page opens on it, its first tab.
    if st.button("See the worked example", key="btn_worked_example"):
        st.switch_page(RECONCILIATION)

    # -- The key gate (sidebar): returns a per-session client or None ---------
    client = render_key_gate()
    st.session_state["live_client"] = client

    st.markdown("#### What it does")
    st.markdown(
        "- Reads a bank statement and a cash book (17 and 20 records, June 2026).\n"
        "- Matches them by five rules: exact key, within tolerance, amount mismatch, "
        "reference mismatch, many-to-one. Unmatched or mismatched items become typed "
        "breaks.\n"
        "- On request the model explains each break and suggests a follow-up.\n"
        "- You decide every resolution, and the PDF report is a DRAFT until a named "
        "reviewer signs it.")

    st.markdown("#### The governance promise")
    st.markdown(
        "- **Python matches.** Matching and reconciliation arithmetic run without a key.\n"
        "- **A rule classifies.** Each break has a type by rule.\n"
        "- **The model explains.** Suggestions only; you decide every resolution.\n"
        "- **The reviewer signs.** Nothing is final until a named reviewer reads it and "
        "signs.")

    st.markdown("#### Pages")
    st.page_link(HOW_IT_WORKS, label="How it works: the matching rules, what makes a "
                 "break, and how the reconciled position is computed")
    st.page_link(RECONCILIATION, label="Reconciliation: a worked example that needs no "
                 "API key, and the tool itself")
    st.page_link(OWN_DATA, label="Your own data: the file format, how to run it "
                 "locally, and what stays private")
    st.page_link(HOW_ITS_BUILT, label="How it's built: the deterministic matching "
                 "core, the AI narration boundary, the lock persistence model and the "
                 "review stance")

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
            "per break, eight calls on the sample data. At Sonnet's current "
            "rate of about three US dollars per million input tokens and "
            "fifteen per million output tokens, a run on the sample data "
            "costs about 2 US cents. That is an estimate: the app does not "
            "record token counts.")
        st.caption(
            "Your key is used only for your session and is not stored by "
            "this site. On the web the data stays sample-only; to work on "
            "your own numbers, run the project locally from GitHub.")

    st.divider()
    st.caption("Sample data only: Valencia Operations is a fictional company. "
               "All figures are illustrative.")

    DATA_DIR = ROOT / "data"
    expected = ["bank_statement.csv", "cash_book.csv"]
    missing = [f for f in expected if not (DATA_DIR / f).exists()]
    if missing:
        st.warning(
            "The sample data is incomplete. Missing: " + ", ".join(missing) +
            ". The tool page needs these files in the project's data folder.")


# -- Multipage navigation (Streamlit >= 1.36) ---------------------------------
st.set_page_config(
    page_title="Reconciliation Assistant",
    page_icon=":material/balance:",
    layout="centered",
    initial_sidebar_state="auto",
)

pg = st.navigation([
    st.Page(_home_page, title="Home", icon=":material/home:"),
    HOW_IT_WORKS,
    RECONCILIATION,
    OWN_DATA,
    HOW_ITS_BUILT,
])

pg.run()
