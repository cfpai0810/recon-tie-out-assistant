# =============================================================================
# views/4_How_Its_Built.py: the technical and governance story
# =============================================================================
# Two audiences on one page. Part 1 is for a finance leader: why an
# AI-assisted reconciliation tool can be trusted (the separated layers,
# the deterministic matching, the audit trail). Part 2 is for a technical
# evaluator: the pipeline, the matching rules, the lock persistence, and
# the safety properties. Matches the section order of Projects 1, 3, and 4
# so the portfolio reads as a family.
# =============================================================================

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import header_html, inject_css
from streamlit_app.lib.governance_layers import governance_layers_html
from streamlit_app.lib.lock_lifecycle import lock_lifecycle_html
from streamlit_app.lib.pipeline_flow import pipeline_flow_html

st.markdown(inject_css(), unsafe_allow_html=True)


# -- Banner -------------------------------------------------------------------
st.markdown(header_html(
    "How it's built",
    "This tool was built on one idea: an AI system that touches financial "
    "numbers has to earn trust before it earns time."),
    unsafe_allow_html=True)
st.write(
    "The sections below explain how that idea shapes the design, first for "
    "a finance reader and then in technical detail.")

# -- Part 1: the trust story (finance reader) ---------------------------------
st.markdown("---")
st.markdown("#### Why you can trust the numbers")

st.write(
    "The tool never lets the language model decide what is a break "
    "(a difference between the two sides that needs resolution). "
    "That is the whole idea. A language model is good at reading a set "
    "of classified breaks and explaining them in clear prose. It is not "
    "a matching engine, and it is not asked to be one. Every match and "
    "every break is computed by deterministic Python, the same way a "
    "spreadsheet formula would compute it, and the same inputs always "
    "produce the same result.")

st.markdown("**The work happens in separated layers.**")

st.markdown(pipeline_flow_html(), unsafe_allow_html=True)
st.caption(
    "The seven phases in order. In the web app, narration is optional "
    "and the controller reviews the breaks before choosing to invoke it.")

st.write(
    "You load the two data files; Python matches every transaction with "
    "five deterministic rules and classifies the breaks by type; the "
    "controller reviews each break and chooses a resolution action; "
    "optionally, the AI explains each break in plain language; and the "
    "output is a PDF report, a JSONL audit trail, and a lock file that "
    "persists resolutions across re-runs.")

st.markdown("**Three layers, three levels of trust.**")

st.markdown(governance_layers_html(), unsafe_allow_html=True)
st.caption(
    "The trust boundary runs between these layers. The model reads what "
    "Python has classified and writes prose; it never touches the matching "
    "or the arithmetic.")

st.markdown("**Every run leaves a record.**")
st.write(
    "Each run writes an audit record with the reconciliation ID (a hash "
    "of the two filenames), every match, every break, every resolution, "
    "and a figures hash that detects stale locks on re-run. The lock "
    "file preserves resolutions so a re-run reproduces prior decisions "
    "without re-asking, and warns if the underlying figures have "
    "changed.")

st.info(
    "In one line: the model reads the classified breaks and writes the "
    "explanation; Python does all the matching and arithmetic; you "
    "decide how to resolve each break. The AI is advisory, and the "
    "matching is deterministic and checkable.")


# -- Part 2: the technical detail (engineer / evaluator) ----------------------
st.markdown("---")
st.markdown("#### The technical detail")

st.write(
    "The system is a seven-phase pipeline. Each phase takes the central "
    "ReconciliationState and returns it enriched. The boundary between "
    "the model and the deterministic code is deliberate and strict.")

st.markdown("**The seven phases.**")
st.write(
    "**Load** reads the two CSV files and normalises them into typed "
    "records. **Validate** checks for blank references and empty sides. "
    "**Match** runs the five matching rules in strict precedence, "
    "removing matched items from the pool. **Classify** converts "
    "mismatches to typed breaks and computes the side totals and raw "
    "difference. **Narrate** asks the AI for a plain-language explanation "
    "of each break (with a templated fallback if no key is available). "
    "**Resolve** walks the reviewer through each open break with five "
    "resolution actions. **Record** writes the audit trail and PDF "
    "report.")

st.markdown("---")


# -- Determinism and lineage --------------------------------------------------
st.markdown("#### Determinism and lineage")
st.write(
    "The matching engine is a pure function: it takes the two record "
    "lists and the tolerance, and returns the matches and the unmatched "
    "pool. No randomness, no network calls, no side effects. The same "
    "inputs always produce the same matches.")
st.write(
    "Tie-breaks are content-based, never load-order: (a) closest date, "
    "(b) smallest amount difference, (c) lowest (reference, abs_amount) "
    "lexically. This means the output is identical regardless of the "
    "row order in the input files.")
st.write(
    "Break identities are stable SHA-256 hashes of the sorted content "
    "tuples (side, reference, signed_amount, date). A break keeps the "
    "same key across re-runs as long as the underlying transactions are "
    "unchanged.")

st.markdown("---")


# -- Lock persistence ---------------------------------------------------------
st.markdown("#### Lock persistence and re-run guard")
st.markdown(lock_lifecycle_html(), unsafe_allow_html=True)

st.write(
    "Locked resolutions (everything except INVESTIGATE) are saved to a "
    "JSON file keyed by the stable break identity. On re-run, the "
    "resolve phase loads existing locks and checks the figures hash. "
    "If the figures match, the lock is reproduced automatically. If "
    "they have changed, a stale-lock warning is raised and the break "
    "is re-opened for review. Orphaned locks (breaks that no longer "
    "exist) are flagged and skipped.")

st.markdown("---")


# -- Governance stance --------------------------------------------------------
st.markdown("#### Flag-for-review governance")
st.write(
    "The AI's output is explicitly framed as hypotheses for a human to "
    "verify. The narrate phase instructs the model to explain each "
    "break and suggest a follow-up, but the resolution decision is "
    "always the controller's. The intent-confirm gate requires explicit "
    "confirmation before an adjusting entry is locked.")
st.write(
    "The tool is an aid to the reconciliation, not a replacement for it. "
    "The controller decides what needs action.")

st.divider()
st.caption("Sample data only. All figures are illustrative.")
