# =============================================================================
# lib/theme.py: one visual identity, shared with the PDF
# =============================================================================
# The exact palette the PDFs already use (recon/record.py), so the web app
# and the report it produces read as a single product.
#
# Typography: Source Serif 4 for headings (institutional authority) and Inter
# for body, UI, and data (screen-first, with tabular figures so numbers align
# in tables). Both are free Google Fonts, loaded by CSS import.
# =============================================================================

# Core palette, identical to the PDF (recon/record.py)
DARK_BLUE  = "#1A3A5C"
MID_BLUE   = "#2D6A9F"
LIGHT_BLUE = "#EAF2FB"
GREEN      = "#1D6B0F"
AMBER      = "#854F0B"
AMBER_BG   = "#FAEEDA"
FLAG_RED   = "#A32D2D"
BODY_DARK  = "#1A1A19"
MUTED      = "#898781"
RULE       = "#D3D1C7"
NEAR_WHITE = "#FBFAF7"

# Break-type badge colours (background, text)
BREAK_BADGE = {
    "amount_mismatch":        (AMBER_BG,    AMBER),
    "reference_mismatch":     (LIGHT_BLUE,  MID_BLUE),
    "unmatched":              ("#FFF0F0",   FLAG_RED),
    "many_to_one_suggestion": ("#EDE7F6",   "#5E35B1"),
}

# Resolution badge colours
RESOLUTION_BADGE = {
    "accept_as_timing": (LIGHT_BLUE,  MID_BLUE),
    "adjust":           ("#EAF3DE",   GREEN),
    "investigate":      (AMBER_BG,    AMBER),
    "dismiss":          ("#F0F0EE",   MUTED),
    "confirm_match":    ("#EAF3DE",   GREEN),
}

# Font families (referenced in CSS and available to the SVG diagram)
SERIF = "'Source Serif 4', Georgia, 'Times New Roman', serif"
SANS  = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"


def inject_css():
    """Return the app's CSS, including the font imports. Kept in one place so
    every page is consistent."""
    return f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&display=swap');

      /* Body, UI, and data: Inter, with tabular figures so numbers align */
      html, body, [class*="css"], .stMarkdown, .stText, p, span, div, label,
      input, button, table, td, th {{
        font-family: {SANS};
        font-feature-settings: "tnum" 1, "cv05" 1;
      }}

      /* Headings: Source Serif 4 for institutional authority */
      h1, h2, h3, h4 {{
        font-family: {SERIF};
        color: {DARK_BLUE};
        letter-spacing: -0.01em;
        font-weight: 600;
      }}

      /* The app header band */
      .sc-header {{
        background: {DARK_BLUE};
        color: white;
        padding: 22px 26px;
        border-radius: 10px;
        margin-bottom: 8px;
      }}
      .sc-header h1 {{
        font-family: {SERIF};
        color: white; margin: 0; font-size: 1.7rem; font-weight: 700;
        letter-spacing: -0.02em;
      }}
      .sc-header p  {{
        font-family: {SANS};
        color: #AACCEE; margin: 6px 0 0; font-size: 0.92rem; font-weight: 400;
      }}

      /* Status badge (break type, resolution type) */
      .sc-badge {{
        display: inline-block;
        padding: 2px 10px;
        border-radius: 20px;
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        font-family: {SANS};
      }}

      /* Honest key-disclosure note */
      .sc-keynote {{
        font-size: 0.8rem;
        color: {MUTED};
        border-left: 2px solid {RULE};
        padding-left: 10px;
        margin-top: 6px;
        font-family: {SANS};
      }}

      /* Narrative callout, matches the portfolio pattern */
      .sc-narrative {{
        background: {LIGHT_BLUE};
        border-left: 3px solid {MID_BLUE};
        border-radius: 4px;
        padding: 14px 18px;
        margin: 8px 0;
        font-size: 0.92rem;
        line-height: 1.6;
        color: {BODY_DARK};
        font-family: {SANS};
      }}

      /* Summary metric card */
      .sc-metric {{
        background: {NEAR_WHITE};
        border: 1px solid {RULE};
        border-radius: 8px;
        padding: 14px 18px;
        text-align: center;
      }}
      .sc-metric .label {{
        font-size: 0.78rem;
        color: {MUTED};
        font-weight: 500;
        margin-bottom: 4px;
      }}
      .sc-metric .value {{
        font-size: 1.3rem;
        font-weight: 700;
        color: {DARK_BLUE};
        font-family: {SANS};
        font-feature-settings: "tnum" 1;
      }}
    </style>
    """


def break_badge_html(break_type):
    """Return an HTML badge for a break type."""
    import html as _html
    label = str(break_type).replace("_", " ").upper()
    key = str(break_type).lower()
    if key not in BREAK_BADGE:
        return _html.escape(label)
    bg, fg = BREAK_BADGE[key]
    return (f'<span class="sc-badge" style="background:{bg};color:{fg};">'
            f'{label}</span>')


def resolution_badge_html(resolution_type):
    """Return an HTML badge for a resolution type."""
    import html as _html
    label = str(resolution_type).replace("_", " ").upper()
    key = str(resolution_type).lower()
    if key not in RESOLUTION_BADGE:
        return _html.escape(label)
    bg, fg = RESOLUTION_BADGE[key]
    return (f'<span class="sc-badge" style="background:{bg};color:{fg};">'
            f'{label}</span>')
