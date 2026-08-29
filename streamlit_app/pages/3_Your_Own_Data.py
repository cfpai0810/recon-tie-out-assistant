# =============================================================================
# pages/3_Your_Own_Data.py: running the reconciliation on your own data
# =============================================================================

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css

st.markdown(inject_css(), unsafe_allow_html=True)


st.markdown("### Your own data")
st.write(
    "How to run the reconciliation (the two-side matching check) on "
    "your own bank statement and cash book. Everything runs locally on "
    "your machine; nothing is uploaded.")

st.markdown("---")


# -- The two data files -------------------------------------------------------
st.markdown("#### The two data files")
st.write(
    "The tool needs two CSV files: one for the bank statement (side A) "
    "and one for the cash book (side B). Three column layouts are "
    "detected automatically.")

st.markdown("**Bank statement format**")
st.write(
    "Columns: Date, Value Date, Type, Details, Paid Out, Paid In, "
    "Balance. Metadata rows above the header (account info, opening "
    "balance) are skipped automatically.")

st.markdown("**Cash book format**")
st.write(
    "Columns: Date, Voucher, Particulars, Receipts, Payments.")

st.markdown("**Standard format**")
st.write(
    "Columns: date, reference, amount, description. A single signed "
    "amount column (positive = receipt, negative = payment).")

st.write(
    "Column names are matched case-insensitively. Extra columns are "
    "ignored. The file encoding should be UTF-8 (with or without BOM); "
    "latin-1 also works. The two sides do not need to use the same "
    "format.")

st.markdown("---")


# -- Example rows -------------------------------------------------------------
st.markdown("#### Example rows")

st.write("**Bank statement (Paid Out / Paid In columns)**")
st.code(
    "Date,Value Date,Type,Details,Paid Out,Paid In,Balance\n"
    "01/06/2026,01/06/2026,BGC,RCT-0601,,1820.50,14320.50\n"
    "05/06/2026,05/06/2026,SO,DD-RENT,2400.00,,11890.50",
    language="text",
)

st.write("**Cash book (Receipts / Payments columns)**")
st.code(
    "Date,Voucher,Particulars,Receipts,Payments\n"
    "01/06/2026,RCT-0601,Card takings 01 Jun,1820.50,\n"
    "05/06/2026,DD-RENT,Grafton Estates rent,,2400.00",
    language="text",
)

st.write("**Standard (single Amount column)**")
st.code(
    "date,reference,amount,description\n"
    "2026-06-01,DEP-001,5000.00,Opening deposit\n"
    "2026-06-03,INV-2026-031,-1250.00,Supplier payment Acme Ltd",
    language="text",
)

st.write(
    "The two descriptions do not need to match; only reference, date, "
    "and amount are used for matching.")

st.markdown("---")


# -- Accepted formats ---------------------------------------------------------
st.markdown("#### Accepted formats")

st.markdown("**Amounts**")
st.write(
    "Currency symbols, thousands separators (comma, dot, or space), "
    "accounting parentheses like `(1,234.56)`, trailing minus like "
    "`1,234.56-`, and European comma-decimal like `1.234,56` are all "
    "handled. A value that genuinely cannot be parsed is skipped with a "
    "note; it is not silently treated as zero.")

st.markdown("**Dates**")
st.write(
    "ISO (`2026-06-15`), day-first (`15/06/2026`), month-first "
    "(`06/15/2026`), short month name (`15-Jun-2026`, `15 Jun 2026`), "
    "full month name (`15 June 2026`), US with comma (`Jun 15, 2026`), "
    "and slash-month (`15/Jun/2026`). Rows with dates that cannot be "
    "parsed are skipped and listed.")

st.markdown("**Encoding**")
st.write(
    "UTF-8, UTF-8 with BOM, and latin-1. Whitespace around values is "
    "trimmed.")

st.markdown("---")


# -- Running it locally -------------------------------------------------------
st.markdown("#### Running it locally")

st.markdown("**Clone and install**")
st.code(
    "git clone https://github.com/cfpai0810/recon-tie-out-assistant.git\n"
    "cd recon-tie-out-assistant\n"
    "python -m venv venv\n"
    "venv\\Scripts\\Activate.ps1        # Windows PowerShell\n"
    "pip install -r requirements.txt",
    language="bash",
)

st.markdown("**Run the CLI**")
st.write(
    "Place your two CSV files in the `data/` folder (or update the paths "
    "in `config.py`). Then:")
st.code(
    "python main.py",
    language="bash",
)
st.write(
    "The CLI walks you through each break interactively, asks for your "
    "resolution, and writes a PDF report and an audit trail to `output/`.")

st.markdown("**Run the web app**")
st.code(
    "streamlit run streamlit_app/Home.py",
    language="bash",
)
st.write(
    "The web app runs on the sample data out of the box. To get AI "
    "explanations, enter your own Anthropic API key in the sidebar.")

st.markdown("---")


# -- What the AI key costs ----------------------------------------------------
st.markdown("#### What the AI key costs")
st.write(
    "The matching and reconciliation need no key. Only the AI "
    "explanations require one. Each run makes one short call to Claude "
    "Sonnet per break. At current Sonnet rates, a single reconciliation "
    "is well under one US cent in practice.")

st.markdown("---")


# -- Honest limits ------------------------------------------------------------
st.markdown("#### Honest limits")
st.write(
    "This tool is built for month-end bank-to-book reconciliations of "
    "the kind a finance controller runs routinely. It handles messy "
    "exports well, but it has boundaries.")

st.markdown(
    "- **Two sides only.** The tool reconciles one bank statement "
    "against one cash book. It does not do multi-entity consolidation "
    "or three-way matching.\n"
    "- **Three fields for matching.** The matcher uses date, reference, "
    "and amount. It does not use description for matching, and it cannot "
    "match on fields that are not present.\n"
    "- **Many-to-one up to four items.** The many-to-one rule matches "
    "up to four items on one side to a single item on the other, within "
    "a 14-day window. Splits larger than four are not attempted.\n"
    "- **No multi-currency.** All amounts are treated as the same "
    "currency. If your data has mixed currencies, split them into "
    "separate files first.\n"
    "- **Tolerance is 0.02.** The within-tolerance rule absorbs "
    "differences up to 0.02. This is set in `config.py` and can be "
    "changed, but the tool does not ask you at runtime.\n"
    "- **AI is advisory.** The AI writes an explanation and suggests a "
    "follow-up, but it does not choose the resolution. If you run "
    "without an API key, you get templated explanations instead.")

st.markdown("---")


# -- What stays private -------------------------------------------------------
st.markdown("#### What stays private")
st.write(
    "When you run locally, your data never leaves your machine. The "
    "matching and classification are pure Python with no network calls. "
    "The only external call is the optional AI narration, which sends "
    "the break details (not the full dataset) to the Anthropic API "
    "using your own key.")
st.write(
    "On the hosted web app, only the sample data is available. To "
    "reconcile your own files, run the project locally from GitHub.")

st.divider()
st.caption("Sample data only. All figures are illustrative.")
