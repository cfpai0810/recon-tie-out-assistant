# Reconciliation and Tie-Out Assistant

Match a bank statement against a cash book, classify the breaks
(differences that need resolution), resolve each one, and produce a
report you can hand to a reviewer. The matching
and classification are deterministic Python. An optional AI layer explains
each break in plain language, but the numbers are never the model's to
decide.

**Live demo:** [recon-tie-out-assistant.streamlit.app](https://recon-tie-out-assistant.streamlit.app)

The data in this repository is illustrative sample data for a fictional
entity.

---

## What it does

You supply two CSV files: a bank statement (the bank's record of
transactions on your account, side A) and a cash book (your own
internal record of those same transactions, side B). The tool loads both, matches every transaction it can,
classifies the differences as typed breaks, and walks you through
resolving each one. Optionally, an AI narrates each break in plain
language and suggests a follow-up. The output is a PDF reconciliation
report, a JSONL audit trail, and a lock file that preserves your
resolutions across re-runs.

Two ways to use it:

**CLI.** Run `python main.py` for a guided interactive session. The
pipeline loads, validates, matches, classifies, narrates, resolves,
and records in sequence. You answer each break at the terminal and get
a PDF and audit file in `output/`.

**Web app.** Run `streamlit run streamlit_app/Home.py` for a five-page
Streamlit app. A worked example runs at the default settings with no
API key. To get AI explanations, enter your own Anthropic key in the
sidebar.

---

## The two data files

The tool accepts three CSV layouts. The sample files use the first two
(a realistic bank export and a cash book); the third is a simpler
bring-your-own format. Column names are matched case-insensitively;
extra columns are ignored. The two sides do not need to use the same
layout.

**Bank statement** (the sample's `bank_statement.csv`):
Date, Value Date, Type, Details, Paid Out, Paid In, Balance. Metadata
rows above the header (the account line and opening balance) are
skipped automatically. Paid Out and Paid In are combined into one
signed amount.

**Cash book** (the sample's `cash_book.csv`):
Date, Voucher, Particulars, Receipts, Payments. Receipts and Payments
are combined into one signed amount; Voucher is the reference.

**Standard format** (bring your own):
date, reference, amount, description. A single signed amount column
(positive = receipt, negative = payment).

All three layouts are normalised to these four internal fields:

| Field | Meaning | Example |
|-------|---------|---------|
| `date` | Transaction date | `2026-06-15` |
| `reference` | Transaction identifier | `RCT-0601` |
| `amount` | Signed amount (positive = receipt) | `-2,400.00` |
| `description` | Free text | `Grafton Estates rent` |

See the Your Own Data page in the web app for example rows in each
layout.

### Accepted formats

**Amounts.** Currency symbols, thousands separators (comma, dot, or
space), accounting parentheses `(1,234.56)`, trailing minus `1,234.56-`,
and European comma-decimal `1.234,56` are all handled. A value that
cannot be parsed is skipped with a note, not silently treated as zero.

**Dates.** ISO (`2026-06-15`), day-first (`15/06/2026`), month-first
(`06/15/2026`), short month name (`15-Jun-2026`, `15 Jun 2026`), full
month name (`15 June 2026`), US with comma (`Jun 15, 2026`), and
slash-month (`15/Jun/2026`).

**Encoding.** UTF-8 (with or without BOM) and latin-1. Whitespace
around values is trimmed.

---

## The design rule

The language model never decides what is a break. It reads the breaks
that Python has already classified and explains them in plain language.
Every match, every break type, every reconciled position, and every
resolution is computed deterministically. The same inputs always produce
the same result.

The model's output is framed as a hypothesis for the controller to
verify. The resolution decision is always the controller's.

---

## The five matching rules

The matcher runs the rules in strict precedence. Each rule removes
matched items from the pool, so later rules only see what earlier rules
could not resolve.

| Precedence | Rule | What it matches |
|:---:|-------|-----------------|
| 1 | Exact key | Same reference, same date, same amount |
| 2 | Within tolerance | Same reference, amount difference within the tolerance bound |
| 3 | Amount mismatch | Same reference, different amount |
| 4 | Reference mismatch | Same amount (within tolerance), different reference |
| 5 | Many-to-one | Up to 4 items on one side sum to a single item on the other, within a 14-day window (presented as a suggestion for reviewer confirmation) |

Tie-breaks are content-based, never load-order: closest date, smallest
amount difference, lowest (reference, abs_amount) lexically. Output is
identical regardless of row order in the input files.

### Worked example

All figures below are teaching examples, not the sample data.

**Rule 1: Exact key** (match)

| Side | Reference | Date | Amount |
|------|-----------|------|-------:|
| Bank (A) | INV-100 | 10 Jun | -500.00 |
| Book (B) | INV-100 | 10 Jun | -500.00 |

Same reference, date, and amount. The cleanest pairing.

**Rule 2: Within tolerance** (match)

| Side | Reference | Date | Amount |
|------|-----------|------|-------:|
| Bank (A) | INV-101 | 11 Jun | -320.00 |
| Book (B) | INV-101 | 11 Jun | -320.01 |

Same reference, 0.01 difference is within the 0.02 absolute tolerance.
The difference is absorbed.

**Rule 3: Amount mismatch** (break)

| Side | Reference | Date | Amount |
|------|-----------|------|-------:|
| Bank (A) | INV-102 | 12 Jun | -1,000.00 |
| Book (B) | INV-102 | 12 Jun | -1,090.00 |

Same reference, but 90.00 difference is beyond tolerance. Flagged as an
amount mismatch for review.

**Rule 4: Reference mismatch** (break)

| Side | Reference | Date | Amount |
|------|-----------|------|-------:|
| Bank (A) | TFR-556 | 13 Jun | -750.00 |
| Book (B) | TRF-889 | 14 Jun | -750.00 |

Same amount, but references and dates differ. The date need not match;
the matcher prefers the closest date as a tie-break, but does not
require it. Flagged as a reference mismatch for review.

**Rule 5: Many-to-one** (suggestion, needs confirmation)

| Side | Reference | Date | Amount |
|------|-----------|------|-------:|
| Bank (A) | DEP-BATCH | 14 Jun | +900.00 |
| Book (B) | RCT-1 | 13 Jun | +300.00 |
| Book (B) | RCT-2 | 13 Jun | +250.00 |
| Book (B) | RCT-3 | 14 Jun | +350.00 |

Three book receipts (300 + 250 + 350 = 900) sum to one bank deposit,
within the 14-day window. Presented as a suggestion for the reviewer to
confirm or reject, not as an automatic match.

The tolerance in Rule 2 is adjustable. Both an absolute threshold
(default 0.02) and a relative threshold (default 0, off) are available;
the effective bound is whichever is larger. With a relative tolerance
set, a large-amount pair that Rule 3 would break at the default can
instead match under Rule 2 if within the relative allowance (for
example, a 0.50 difference on a 1,000,000.00 item is within 0.1%).

**How matching handles amount errors.** The amount-mismatch rule pairs
two entries that share a reference but differ in amount, and flags the
difference. If the two sides use different reference systems (for
example a bank transaction code on one side and a voucher number on
the other), an amount error cannot be paired this way and appears
instead as two unmatched items, one on each side. This is deliberate:
pairing purely on a similar amount without a shared reference would
risk matching two unrelated transactions. When you reconcile files
whose references do not correspond, expect genuine amount errors to
surface as pairs of unmatched items rather than as amount mismatches.

---

## The five resolution actions

Each break is resolved by the controller with one of five actions.

| Action | Effect on reconciled position |
|--------|-------------------------------|
| Accept as timing | No change; both sides will clear in a later period |
| Adjust | Subtract the break difference; an adjusting entry is booked |
| Dismiss | Subtract the break difference; the item is immaterial |
| Investigate | No change; the break stays open for follow-up |
| Confirm match | Subtract the break difference; the many-to-one grouping is accepted |

The reconciled position starts at the raw difference (side A total minus
side B total) and is adjusted by the resolution actions. The tolerance
absorbed by within-tolerance matches is also shown.

---

## How a request becomes a result

```
bank_statement.csv    cash_book.csv
       |                    |
       v                    v
    step1_load (CSV to typed records, coerce amounts and dates)
                |
                v
    step2_validate (blank references, empty sides, row-level warnings)
                |
                v
    step3_match (5 rules in precedence, deterministic tie-breaks)
                |
                v
    step4_classify (typed breaks, side totals, raw difference)
                |
                v
    step5_narrate (AI explains each break, or templated fallback)
                |
                v
    step6_resolve (controller chooses action per break, locks persist)
                |
                v
    step7_record (PDF report, JSONL audit trail, lock file)
```

---

## Lock persistence and re-run guard

Locked resolutions are saved to a JSON file keyed by a stable SHA-256
hash of the break's content (side, reference, signed amount, date). On
re-run the resolve phase loads existing locks and checks a figures hash
(SHA-256 of the side totals and difference). If the figures match, locks
are reproduced automatically. If they have changed, a stale-lock warning
is raised and the break is re-opened. Orphaned locks are flagged and
skipped.

---

## Governance note

The AI narration is explicitly advisory. The narrate phase instructs the
model to explain each break and suggest a follow-up, but the resolution
decision is the controller's. The intent-confirm gate requires explicit
confirmation before an adjusting entry is locked. The worked example on
the web app shows both what the model was asked and what it returned, so
the boundary between deterministic output and model output is visible.

The tool is an aid to the reconciliation, not a replacement for it.

---

## How to run

```bash
git clone https://github.com/cfpai0810/recon-tie-out-assistant.git
cd recon-tie-out-assistant
python -m venv venv
venv\Scripts\Activate.ps1        # Windows PowerShell
pip install -r requirements.txt
```

### CLI

Place your two CSV files in `data/` (or update the paths in `config.py`),
then:

```bash
python main.py
```

Reports and data files are written to `output/`.

### Web app

```bash
streamlit run streamlit_app/Home.py
```

The app runs on sample data out of the box. To get AI explanations,
enter your own Anthropic API key in the sidebar.

### API key

The matching and reconciliation need no key. Only the AI explanations
require one.

Add your key to a `.env` file:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Or, on the web app, paste it into the sidebar. The key is used only for
your session and is not stored.

---

## Project structure

```
main.py                          CLI orchestrator: seven-phase pipeline
config.py                        Layer 1: file paths, tolerance, entity name

src/
  state.py                       Shared type definitions (enums, dataclasses, state)
  step1_load.py                  Layer 2: CSV to typed records, amount/date coercion
  step2_validate.py              Layer 3: row-level checks, empty-side guard
  step3_match.py                 Layer 4: five matching rules in precedence
  step4_classify.py              Layer 5: typed breaks, totals, raw difference
  step5_narrate.py               Layer 6: AI explanations or templated fallback
  step6_resolve.py               Layer 7: resolution actions, lock persistence
  step7_record.py                Layer 8: PDF report, JSONL audit trail

streamlit_app/
  Home.py                        Entry point, landing page, API key gate
  lib/
    theme.py                     Palette, CSS, badge helpers
    key_gate.py                  Sidebar API key input
    claude_client.py             BYO-key client builder
    audit.py                     Session audit: build_audit_record, record_run
    errors.py                    Friendly API error messages
  pages/
    1_How_the_Model_Works.py     Plain-language explanation of the tool
    2_Reconciliation.py          The tool: matching, breaks, resolution, PDF
    3_Your_Own_Data.py           Schema, accepted formats, run commands, limits
    4_How_Its_Built.py           Technical and governance detail

data/
  bank_statement.csv             17 bank records (side A), sample month
  cash_book.csv                  20 book records (side B), sample month

tests/
  test_pipeline.py               141 tests across 28 test classes

output/                          Generated files (gitignored)
```

---

## Audit trail

Every run appends a JSONL record to `output/audit_log.jsonl` capturing
totals, matches, breaks, resolutions, and the resolved position. Locked
resolutions are saved to a separate JSON file keyed by a SHA-256 hash of the
break's content, so a re-run reproduces prior review decisions automatically
when the figures still match. Stale locks (where the underlying figures have
changed) are flagged for re-review rather than silently reapplied.

---

## Test suite

141 tests across 28 test classes, no real API calls:

```bash
pytest tests/test_pipeline.py -v
```

The test classes cover loading, matching, classification, narration,
resolution, lock persistence, stale-lock detection, PDF generation,
audit records, amount coercion (currency, parentheses, trailing minus,
comma-decimal, spaces), date coercion (11 formats), blank rows,
missing/extra columns, encoding fallback, and mixed dirty/clean files.

---

## Tech stack

Python 3.11 . pandas . Anthropic Claude API . Streamlit . reportlab .
python-dotenv . pytest

---

## Related projects

| # | Project | Status |
|---|---------|--------|
| 1 | AI Variance Commentary Engine | Complete |
| 2 | Driver-Based Rolling Forecast Pipeline | Complete |
| 3 | Anomaly Detection and Alert Agent | Complete |
| 4 | NL Scenario Modelling Copilot | Complete |
| 5 | Reconciliation and Tie-Out Assistant | This project |
| 6 | Board Pack Generator | Complete |
| 7 | Planning-to-Warehouse-to-LLM Pipeline | Planned |
| 8 | 13-Week Cash Flow Forecasting Agent | Planned |
| 9 | Multi-Entity Consolidation and FX Engine | Planned |
| 10 | AI Governance Playbook | Planned |
