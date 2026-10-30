# =============================================================================
# lib/lock_lifecycle.py: lock persistence and the re-run guard
# =============================================================================
# The life of a break's resolution, as How it's built shows it: Open, Drafted,
# Resolved, Locked, with the step that moves it written on each arrow, and a
# dashed path from Locked back to Open for a lock whose figures have changed.
# Each state keeps the colours it had as an SVG (Open blue, Drafted green,
# Resolved amber, Locked green). Drawn in HTML by lib/flow_diagram.py, so it
# reads on a phone. Project 5's own words.
# =============================================================================

from streamlit_app.lib.flow_diagram import Stage, flow_html
from streamlit_app.lib.governance_layers import AMBER_BOX, BLUE_BOX, GREEN_BOX

LABEL = ("Lock persistence and re-run guard: a break goes from Open to Drafted "
         "(narrate), to Resolved (resolve), to Locked (persist); a locked break is "
         "flagged as stale and re-opened if figures change.")

STAGES = (
    Stage("Open", "", "", BLUE_BOX),
    Stage("Drafted", "", "", GREEN_BOX, arrow="narrate"),
    Stage("Resolved", "", "", AMBER_BOX, arrow="resolve"),
    Stage("Locked", "", "", GREEN_BOX, arrow="persist"),
)

BACK = "flagged as stale and re-opened if figures change"


def lock_lifecycle_html():
    """Return the lock lifecycle as HTML for st.markdown."""
    return flow_html(LABEL, STAGES, back=BACK)
