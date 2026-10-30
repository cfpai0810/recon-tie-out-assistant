# =============================================================================
# lib/governance_layers.py: three layers, three levels of trust
# =============================================================================
# The governance diagram on How it's built: deterministic Python, the optional
# AI narration and the human reviewer, as three layers one above another with
# no arrows between them. Each layer keeps the colours it had as an SVG: blue
# for Python, green for the narration, amber for the reviewer, and the same
# three colours serve the pipeline and the lock lifecycle. Drawn in HTML by
# lib/flow_diagram.py, so it reads on a phone. Project 5's own words.
# =============================================================================

from streamlit_app.lib.flow_diagram import Role, Stage, flow_html
from streamlit_app.lib.theme import AMBER, AMBER_BG, GREEN, LIGHT_BLUE, MID_BLUE

BLUE_BOX = Role(MID_BLUE, LIGHT_BLUE)    # deterministic Python
GREEN_BOX = Role(GREEN, "#EAF3DE")       # the AI narration
AMBER_BOX = Role(AMBER, AMBER_BG)        # the human reviewer

LABEL = ("Three layers, three levels of trust, with the trust boundary between "
         "them: deterministic Python does all matching, classification, and "
         "arithmetic, with the same result from the same inputs; AI narration, "
         "optional and on request, explains each break in plain language and does "
         "not decide what is a break; and the human reviewer, the authority, "
         "chooses the resolution per break, confirms adjustments and signs off.")

LAYERS = (
    Stage("Deterministic Python",
          "All matching, classification, and arithmetic. Same inputs, same result.",
          "", BLUE_BOX),
    Stage("AI narration (optional, on request)",
          "Explains each break in plain language. Does not decide what is a break.",
          "", GREEN_BOX),
    Stage("Human reviewer (authority)",
          "Chooses resolution per break. Confirms adjustments. Signs off.",
          "", AMBER_BOX),
)


def governance_layers_html():
    """Return the three governance layers as HTML for st.markdown."""
    return flow_html(LABEL, LAYERS, layers=True)
