# =============================================================================
# lib/pipeline_flow.py: the seven phases (How it's built)
# =============================================================================
# The pipeline as a flow of seven boxes, in Project 5's own words: blue for the
# deterministic phases, green for the optional AI narration, amber for the
# reviewer's choice (the colours of lib/governance_layers.py). The words the
# SVG set above four boxes (deterministic, optional, you decide) are now each
# box's foot. Drawn in HTML by lib/flow_diagram.py. Seven boxes need more room
# for a row than five, so the diagram asks for a row minimum of 680 px: a row
# of boxes no narrower than their longest word from there, the phases stacked
# below that, so every word sits whole at every width.
# =============================================================================

from streamlit_app.lib.flow_diagram import Stage, flow_html
from streamlit_app.lib.governance_layers import AMBER_BOX, BLUE_BOX, GREEN_BOX

LABEL = ("The seven phases in order: Load, from the CSV files; Validate, with row "
         "checks; Match, by 5 rules, deterministic; Classify, into typed breaks, "
         "deterministic; Narrate, an AI draft, optional; Resolve, a human choice, "
         "you decide; and Record, the PDF and audit.")

STAGES = (
    Stage("Load", "CSV files", "", BLUE_BOX),
    Stage("Validate", "row checks", "", BLUE_BOX),
    Stage("Match", "5 rules", "deterministic", BLUE_BOX),
    Stage("Classify", "typed breaks", "deterministic", BLUE_BOX),
    Stage("Narrate", "AI draft", "optional", GREEN_BOX),
    Stage("Resolve", "human choice", "you decide", AMBER_BOX),
    Stage("Record", "PDF, audit", "", BLUE_BOX),
)


def pipeline_flow_html():
    """Return the seven phases as HTML for st.markdown."""
    return flow_html(LABEL, STAGES, row_min=680)
