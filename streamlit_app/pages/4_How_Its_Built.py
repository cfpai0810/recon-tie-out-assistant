# =============================================================================
# pages/4_How_Its_Built.py: the technical and governance story
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
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css, DARK_BLUE, MID_BLUE, MUTED, SANS

st.markdown(inject_css(), unsafe_allow_html=True)


# -- Title --------------------------------------------------------------------
st.markdown("### How it's built")
st.write(
    "This tool was built on one idea: an AI system that touches financial "
    "numbers has to earn trust before it earns time. The sections below "
    "explain how that idea shapes the design, first for a finance reader "
    "and then in technical detail.")

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

_PIPELINE_SVG = f"""
<svg viewBox="0 0 780 120" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;max-width:780px;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <rect x="2" y="30" width="86" height="60" rx="6" fill="#EAF2FB" stroke="#2D6A9F" stroke-width="1.5"/>
  <rect x="112" y="30" width="86" height="60" rx="6" fill="#EAF2FB" stroke="#2D6A9F" stroke-width="1.5"/>
  <rect x="222" y="30" width="86" height="60" rx="6" fill="#EAF2FB" stroke="#2D6A9F" stroke-width="1.5"/>
  <rect x="332" y="30" width="86" height="60" rx="6" fill="#EAF2FB" stroke="#2D6A9F" stroke-width="1.5"/>
  <rect x="442" y="30" width="86" height="60" rx="6" fill="#EAF3DE" stroke="#1D6B0F" stroke-width="1.5"/>
  <rect x="552" y="30" width="86" height="60" rx="6" fill="#FAEEDA" stroke="#854F0B" stroke-width="1.5"/>
  <rect x="662" y="30" width="86" height="60" rx="6" fill="#EAF2FB" stroke="#2D6A9F" stroke-width="1.5"/>
  <text x="45" y="55" text-anchor="middle" fill="#1A3A5C" font-size="11" font-weight="bold">Load</text>
  <text x="45" y="72" text-anchor="middle" fill="{MUTED}" font-size="8">CSV files</text>
  <text x="155" y="55" text-anchor="middle" fill="#1A3A5C" font-size="11" font-weight="bold">Validate</text>
  <text x="155" y="72" text-anchor="middle" fill="{MUTED}" font-size="8">row checks</text>
  <text x="265" y="55" text-anchor="middle" fill="#1A3A5C" font-size="11" font-weight="bold">Match</text>
  <text x="265" y="72" text-anchor="middle" fill="{MUTED}" font-size="8">5 rules</text>
  <text x="375" y="55" text-anchor="middle" fill="#1A3A5C" font-size="11" font-weight="bold">Classify</text>
  <text x="375" y="72" text-anchor="middle" fill="{MUTED}" font-size="8">typed breaks</text>
  <text x="485" y="55" text-anchor="middle" fill="#1D6B0F" font-size="11" font-weight="bold">Narrate</text>
  <text x="485" y="72" text-anchor="middle" fill="{MUTED}" font-size="8">AI draft</text>
  <text x="595" y="55" text-anchor="middle" fill="#854F0B" font-size="11" font-weight="bold">Resolve</text>
  <text x="595" y="72" text-anchor="middle" fill="{MUTED}" font-size="8">human choice</text>
  <text x="705" y="55" text-anchor="middle" fill="#1A3A5C" font-size="11" font-weight="bold">Record</text>
  <text x="705" y="72" text-anchor="middle" fill="{MUTED}" font-size="8">PDF, audit</text>
  <line x1="90" y1="60" x2="110" y2="60" stroke="{MID_BLUE}" stroke-width="1.5" marker-end="url(#ap)"/>
  <line x1="200" y1="60" x2="220" y2="60" stroke="{MID_BLUE}" stroke-width="1.5" marker-end="url(#ap)"/>
  <line x1="310" y1="60" x2="330" y2="60" stroke="{MID_BLUE}" stroke-width="1.5" marker-end="url(#ap)"/>
  <line x1="420" y1="60" x2="440" y2="60" stroke="{MID_BLUE}" stroke-width="1.5" marker-end="url(#ap)"/>
  <line x1="530" y1="60" x2="550" y2="60" stroke="{MID_BLUE}" stroke-width="1.5" marker-end="url(#ap)"/>
  <line x1="640" y1="60" x2="660" y2="60" stroke="{MID_BLUE}" stroke-width="1.5" marker-end="url(#ap)"/>
  <text x="265" y="18" text-anchor="middle" fill="{MID_BLUE}" font-size="9">deterministic</text>
  <text x="375" y="18" text-anchor="middle" fill="{MID_BLUE}" font-size="9">deterministic</text>
  <text x="485" y="18" text-anchor="middle" fill="#1D6B0F" font-size="9">optional</text>
  <text x="595" y="18" text-anchor="middle" fill="#854F0B" font-size="9">you decide</text>
  <defs><marker id="ap" viewBox="0 0 10 10" refX="10" refY="5"
    markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{MID_BLUE}"/></marker></defs>
</svg>
"""

components.html(_PIPELINE_SVG, height=130, scrolling=False)
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

_GOVERNANCE_SVG = f"""
<svg viewBox="0 0 600 192" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;max-width:600px;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <rect x="2" y="2" width="596" height="56" rx="6" fill="#EAF2FB" stroke="{MID_BLUE}" stroke-width="1.5"/>
  <text x="16" y="24" fill="{DARK_BLUE}" font-size="12" font-weight="bold">Deterministic Python</text>
  <text x="16" y="44" fill="{MUTED}" font-size="10">All matching, classification, and arithmetic. Same inputs, same result.</text>
  <rect x="2" y="66" width="596" height="56" rx="6" fill="#EAF3DE" stroke="#1D6B0F" stroke-width="1.5"/>
  <text x="16" y="88" fill="#1D6B0F" font-size="12" font-weight="bold">AI narration (optional, on request)</text>
  <text x="16" y="108" fill="{MUTED}" font-size="10">Explains each break in plain language. Does not decide what is a break.</text>
  <rect x="2" y="130" width="596" height="56" rx="6" fill="#FAEEDA" stroke="#854F0B" stroke-width="1.5"/>
  <text x="16" y="152" fill="#854F0B" font-size="12" font-weight="bold">Human reviewer (authority)</text>
  <text x="16" y="172" fill="{MUTED}" font-size="10">Chooses resolution per break. Confirms adjustments. Signs off.</text>
</svg>
"""

components.html(_GOVERNANCE_SVG, height=200, scrolling=False)
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
_LIFECYCLE_SVG = f"""
<svg viewBox="0 0 600 100" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;max-width:600px;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <rect x="2" y="18" width="100" height="38" rx="6" fill="#EAF2FB" stroke="{MID_BLUE}" stroke-width="1.5"/>
  <text x="52" y="42" text-anchor="middle" fill="{DARK_BLUE}" font-size="11" font-weight="bold">Open</text>
  <rect x="146" y="18" width="100" height="38" rx="6" fill="#EAF3DE" stroke="#1D6B0F" stroke-width="1.5"/>
  <text x="196" y="42" text-anchor="middle" fill="#1D6B0F" font-size="11" font-weight="bold">Drafted</text>
  <rect x="290" y="18" width="100" height="38" rx="6" fill="#FAEEDA" stroke="#854F0B" stroke-width="1.5"/>
  <text x="340" y="42" text-anchor="middle" fill="#854F0B" font-size="11" font-weight="bold">Resolved</text>
  <rect x="434" y="18" width="100" height="38" rx="6" fill="#EAF3DE" stroke="#1D6B0F" stroke-width="1.5"/>
  <text x="484" y="42" text-anchor="middle" fill="#1D6B0F" font-size="11" font-weight="bold">Locked</text>
  <line x1="104" y1="37" x2="144" y2="37" stroke="{MID_BLUE}" stroke-width="1.5" marker-end="url(#al)"/>
  <line x1="248" y1="37" x2="288" y2="37" stroke="{MID_BLUE}" stroke-width="1.5" marker-end="url(#al)"/>
  <line x1="392" y1="37" x2="432" y2="37" stroke="{MID_BLUE}" stroke-width="1.5" marker-end="url(#al)"/>
  <text x="124" y="12" text-anchor="middle" fill="{MUTED}" font-size="8">narrate</text>
  <text x="268" y="12" text-anchor="middle" fill="{MUTED}" font-size="8">resolve</text>
  <text x="412" y="12" text-anchor="middle" fill="{MUTED}" font-size="8">persist</text>
  <path d="M 484 58 L 484 80 L 52 80 L 52 58" fill="none" stroke="{MUTED}" stroke-width="1" stroke-dasharray="4,3" marker-end="url(#al_m)"/>
  <text x="268" y="76" text-anchor="middle" fill="{MUTED}" font-size="8">flagged as stale and re-opened if figures change</text>
  <defs>
    <marker id="al" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{MID_BLUE}"/></marker>
    <marker id="al_m" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{MUTED}"/></marker>
  </defs>
</svg>
"""

components.html(_LIFECYCLE_SVG, height=100, scrolling=False)

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
