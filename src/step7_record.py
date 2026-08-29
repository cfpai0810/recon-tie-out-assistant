# =============================================================================
# step7_record.py: Layer 8: Audit Record and PDF Report
# =============================================================================
#
# Responsibilities:
#   - record():            write the JSONL audit record and PDF report
#   - build_pdf_bytes():   generate the PDF reconciliation report in memory
#   - _audit_record():     build the audit record dict for serialisation
#
# Core rule: every figure in the report is computed deterministically
# by tested Python code. The AI commentary (where present) is advisory
# text only. The human sign-off is the final authority.
#
# Knows about: the full reconciliation state, ReportLab, PDF layout,
#   Decimal arithmetic, audit serialisation
# Does NOT know about: how records were loaded, how matches were found,
#   or how the AI drafted its explanations
# =============================================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from src.state import (
    BreakType, MatchType, ReconciliationState, ResolutionType,
)

# ── Shared theme palette ────────────────────────────────────────────

DARK_BLUE  = colors.HexColor("#1A3A5C")
MID_BLUE   = colors.HexColor("#2D6A9F")
LIGHT_BLUE = colors.HexColor("#EAF2FB")
BODY_DARK  = colors.HexColor("#222222")
MUTED      = colors.HexColor("#898781")
RULE       = colors.HexColor("#D3D1C7")
GREEN      = colors.HexColor("#1D6B0F")
AMBER      = colors.HexColor("#854F0B")
AMBER_BG   = colors.HexColor("#FAEEDA")
FLAG_RED   = colors.HexColor("#A32D2D")
NEAR_WHITE = colors.HexColor("#FBFAF7")
GREEN_BG   = colors.HexColor("#E8F5E9")
GREY_BG    = colors.HexColor("#F0F0EE")

_BREAK_BADGE = {
    BreakType.AMOUNT_MISMATCH:        (AMBER_BG, "#854F0B"),
    BreakType.REFERENCE_MISMATCH:     (LIGHT_BLUE, "#2D6A9F"),
    BreakType.UNMATCHED:              (colors.HexColor("#FFF0F0"), "#A32D2D"),
    BreakType.MANY_TO_ONE_SUGGESTION: (colors.HexColor("#EDE7F6"), "#5E35B1"),
}

PAGE_W = A4[0] - 4 * cm

# ── Paragraph styles ────────────────────────────────────────────────

S_TITLE = ParagraphStyle("Title", fontName="Helvetica-Bold", fontSize=16,
                         textColor=DARK_BLUE, leading=20)
S_H2 = ParagraphStyle("H2", fontName="Helvetica-Bold", fontSize=11,
                       textColor=DARK_BLUE, leading=14, spaceBefore=14)
S_BODY = ParagraphStyle("Body", fontName="Helvetica", fontSize=9,
                        textColor=BODY_DARK, leading=12)
S_BODY_INDENT = ParagraphStyle("BodyIndent", fontName="Helvetica", fontSize=9,
                                textColor=BODY_DARK, leading=12, leftIndent=14)
S_SMALL = ParagraphStyle("Small", fontName="Helvetica", fontSize=7,
                         textColor=MUTED, leading=9)
S_NUM = ParagraphStyle("Num", fontName="Helvetica", fontSize=9,
                       textColor=BODY_DARK, leading=12, alignment=TA_RIGHT)
S_HDR = ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=8,
                       textColor=DARK_BLUE, leading=10)
S_HDR_R = ParagraphStyle("HdrR", fontName="Helvetica-Bold", fontSize=8,
                          textColor=DARK_BLUE, leading=10, alignment=TA_RIGHT)
S_COMPRISING = ParagraphStyle("Comprising", fontName="Helvetica-Oblique",
                               fontSize=8, textColor=MUTED, leading=10)


def _audit_record(state: ReconciliationState, *,
                  tolerance_abs: float = 0.02,
                  tolerance_rel: float = 0) -> dict:
    """Build the audit record dict for one reconciliation run.

    Finance context: the audit record captures the full state of the
    reconciliation at a point in time: totals, matches, breaks with
    their resolutions, and the resolved position. It is append-only
    (each run adds a line) so the history of review is preserved.
    """
    now = datetime.now(timezone.utc).isoformat()
    matches_summary = []
    for m in state.matches:
        matches_summary.append({
            "type": m.match_type.value,
            "side_a": [r.reference for r in m.side_a_records],
            "side_b": [r.reference for r in m.side_b_records],
            "difference": m.difference,
        })

    breaks_summary = []
    for b in state.breaks:
        entry = {
            "break_type": b.break_type.value,
            "stable_break_key": b.stable_break_key,
            "side_a_refs": [r.reference for r in b.side_a_records],
            "side_b_refs": [r.reference for r in b.side_b_records],
            "side_a_amount": b.side_a_amount,
            "side_b_amount": b.side_b_amount,
            "difference": b.difference,
        }
        draft = state.drafts.get(b.stable_break_key)
        if draft:
            entry["explanation"] = draft.explanation
            entry["follow_up"] = draft.follow_up

        res = state.resolutions.get(b.stable_break_key)
        if res:
            entry["resolution"] = {
                "type": res.resolution_type.value,
                "text": res.reviewer_text,
                "adjustment_amount": res.adjustment_amount,
                "adjustment_side": res.adjustment_side,
                "timestamp": res.timestamp,
                "figures_hash": res.figures_hash,
            }
        breaks_summary.append(entry)

    return {
        "run_id": now,
        "reconciliation_id": state.reconciliation_id,
        "side_a_label": state.side_a_label,
        "side_b_label": state.side_b_label,
        "side_a_records": len(state.side_a),
        "side_b_records": len(state.side_b),
        "side_a_total": state.side_a_total,
        "side_b_total": state.side_b_total,
        "raw_difference": state.raw_difference,
        "resolved_position": state.resolved_position,
        "matched_count": state.matched_count,
        "break_counts": state.break_counts,
        "tolerance_abs": tolerance_abs,
        "tolerance_rel": tolerance_rel,
        "problems": len(state.problems),
        "matches": matches_summary,
        "breaks": breaks_summary,
    }


def build_pdf_bytes(state: ReconciliationState, *,
                    signed_off_at: str | None = None,
                    tolerance_abs: float = 0.02,
                    tolerance_rel: float = 0) -> bytes:
    """Build the PDF reconciliation report in memory.

    Finance context: this is the formal deliverable. The report shows a
    status banner (reconciled, reconciled with open items, or working
    copy), the remaining unexplained headline number, the full
    reconciliation statement (raw difference through to resolved
    position), and a break-by-break detail table with resolutions. A
    controller files this report as evidence that the reconciliation was
    performed.
    """

    # ── Compute figures (matching the web position panel) ────────────
    def _dec(v):
        return v if isinstance(v, Decimal) else Decimal(str(v))

    timing = adjust = dismiss = investigate = unresolved = Decimal("0")
    confirmed_m2o = Decimal("0")
    for brk in state.breaks:
        diff = _dec(brk.difference)
        res = state.resolutions.get(brk.stable_break_key)
        if res is None:
            unresolved += diff
        elif res.resolution_type == ResolutionType.ACCEPT_AS_TIMING:
            timing += diff
        elif res.resolution_type == ResolutionType.ADJUST:
            adjust += diff
        elif res.resolution_type == ResolutionType.DISMISS:
            dismiss += diff
        elif res.resolution_type == ResolutionType.CONFIRM_MATCH:
            confirmed_m2o += diff
        elif res.resolution_type == ResolutionType.INVESTIGATE:
            investigate += diff

    tol_absorbed = round(sum(
        (_dec(m.difference) for m in state.matches
         if m.match_type == MatchType.TOLERANCE),
        Decimal("0"),
    ), 2)

    remaining = investigate + unresolved
    open_count = sum(
        1 for b in state.breaks
        if state.resolutions.get(b.stable_break_key) is None
        or state.resolutions[b.stable_break_key].resolution_type
           == ResolutionType.INVESTIGATE
    )

    # ── Status ───────────────────────────────────────────────────────
    if signed_off_at and open_count == 0:
        banner_text = "RECONCILED"
        banner_bg, banner_fg = GREEN_BG, GREEN
    elif signed_off_at:
        n_word = f"{open_count} OPEN ITEM" + ("S" if open_count != 1 else "")
        banner_text = f"RECONCILED WITH {n_word}"
        banner_bg, banner_fg = AMBER_BG, AMBER
    else:
        banner_text = "WORKING COPY, NOT YET SIGNED OFF"
        banner_bg, banner_fg = GREY_BG, MUTED

    remaining_hex = "#1D6B0F" if remaining == 0 else "#854F0B"
    remaining_fg = GREEN if remaining == 0 else AMBER

    s_banner = ParagraphStyle(
        "s_banner", fontName="Helvetica-Bold", fontSize=13,
        textColor=banner_fg, alignment=TA_CENTER, leading=17)
    s_hl_label = ParagraphStyle(
        "s_hl_label", fontName="Helvetica", fontSize=8,
        textColor=MUTED, alignment=TA_CENTER, leading=10)
    s_hl_num = ParagraphStyle(
        "s_hl_num", fontName="Helvetica-Bold", fontSize=18,
        textColor=remaining_fg, alignment=TA_CENTER, leading=22)

    # ── Page footer ──────────────────────────────────────────────────
    side_label = f"{state.side_a_label} vs {state.side_b_label}"

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(2 * cm, 1.6 * cm, A4[0] - 2 * cm, 1.6 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(2 * cm, 1.1 * cm, side_label)
        canvas.drawRightString(
            A4[0] - 2 * cm, 1.1 * cm, f"Page {doc.page}")
        canvas.restoreState()

    # ── Story ────────────────────────────────────────────────────────
    story = []
    now = datetime.now(timezone.utc).strftime("%d %B %Y %H:%M UTC")

    # Title block
    story.append(Paragraph("Reconciliation Report", S_TITLE))
    story.append(Spacer(1, 4))
    story.append(Paragraph(side_label, S_BODY))
    story.append(Paragraph(f"Generated: {now}", S_SMALL))
    story.append(Spacer(1, 14))

    # ── TIER 1: status banner ────────────────────────────────────────
    banner = Table(
        [[Paragraph(banner_text, s_banner)]],
        colWidths=[PAGE_W],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), banner_bg),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("BOX", (0, 0), (-1, -1), 0.5, banner_fg),
    ]))
    story.append(banner)
    story.append(Spacer(1, 12))

    # ── TIER 1: remaining unexplained headline ───────────────────────
    hl = Table([
        [Paragraph("Remaining unexplained", s_hl_label)],
        [Paragraph(f"{remaining:,.2f}", s_hl_num)],
    ], colWidths=[PAGE_W * 0.4])
    hl.hAlign = "CENTER"
    hl.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.append(hl)
    story.append(Spacer(1, 18))

    # ── TIER 2: reconciliation statement ─────────────────────────────
    story.append(Paragraph("Reconciliation Statement", S_H2))
    story.append(Spacer(1, 6))

    COL_L = PAGE_W * 0.62
    COL_R = PAGE_W * 0.38

    def _lbl(text, bold=False, indent=False):
        s = S_BODY_INDENT if indent else S_BODY
        return Paragraph(f"<b>{text}</b>" if bold else text, s)

    def _val(v, bold=False, color=None):
        t = f"{v:,.2f}"
        if bold:
            t = f"<b>{t}</b>"
        if color:
            t = f'<font color="{color}">{t}</font>'
        return Paragraph(t, S_NUM)

    rec_data = [
        [_lbl(f"{state.side_a_label} total"),
         _val(state.side_a_total)],
        [_lbl(f"{state.side_b_label} total"),
         _val(state.side_b_total)],
        [_lbl("Raw difference (A - B)", bold=True),
         _val(state.raw_difference, bold=True)],
        ["", ""],
        [_lbl("Adjustments applied", indent=True),
         _val(-adjust)],
        [_lbl("Items dismissed", indent=True),
         _val(-dismiss)],
        [_lbl("Confirmed many-to-one matches", indent=True),
         _val(-confirmed_m2o)],
        [_lbl("Resolved position", bold=True),
         _val(state.resolved_position, bold=True)],
        ["", ""],
        [Paragraph("<i>Comprising:</i>", S_COMPRISING), ""],
        [_lbl("Timing differences (outstanding)", indent=True),
         _val(timing)],
        [_lbl("Under investigation", indent=True),
         _val(investigate)],
        [_lbl("Not yet resolved", indent=True),
         _val(unresolved)],
        [_lbl("Tolerance absorbed in matches", indent=True),
         _val(tol_absorbed)],
        [Paragraph(
             f'<b><font color="{remaining_hex}">'
             f'Remaining unexplained</font></b>', S_BODY),
         _val(remaining, bold=True, color=remaining_hex)],
    ]

    rec_table = Table(rec_data, colWidths=[COL_L, COL_R])
    rec_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, RULE),
        ("LINEBELOW", (0, 6), (-1, 6), 0.5, RULE),
        ("TOPPADDING", (0, 3), (-1, 3), 1),
        ("BOTTOMPADDING", (0, 3), (-1, 3), 1),
        ("TOPPADDING", (0, 8), (-1, 8), 1),
        ("BOTTOMPADDING", (0, 8), (-1, 8), 1),
        ("LINEABOVE", (0, 14), (-1, 14), 1.5, RULE),
        ("TOPPADDING", (0, 14), (-1, 14), 5),
        ("BOTTOMPADDING", (0, 14), (-1, 14), 5),
    ]))
    story.append(rec_table)
    story.append(Spacer(1, 18))

    # ── TIER 2: breaks detail ────────────────────────────────────────
    if state.breaks:
        story.append(Paragraph("Breaks", S_H2))
        story.append(Spacer(1, 6))

        BK_COLS = [PAGE_W * 0.15, PAGE_W * 0.18, PAGE_W * 0.18,
                   PAGE_W * 0.12, PAGE_W * 0.37]

        _BRK_LABEL = {
            BreakType.AMOUNT_MISMATCH: "Amount mismatch",
            BreakType.REFERENCE_MISMATCH: "Ref. mismatch",
            BreakType.UNMATCHED: "Unmatched",
            BreakType.MANY_TO_ONE_SUGGESTION: "Many-to-one",
        }
        _RES_LABEL = {
            ResolutionType.ACCEPT_AS_TIMING: "Timing",
            ResolutionType.ADJUST: "Adjusted",
            ResolutionType.INVESTIGATE: "Investigate",
            ResolutionType.DISMISS: "Dismissed",
            ResolutionType.CONFIRM_MATCH: "Confirmed",
        }

        brk_rows = [[
            Paragraph("Type", S_HDR),
            Paragraph("Side A", S_HDR),
            Paragraph("Side B", S_HDR),
            Paragraph("Difference", S_HDR_R),
            Paragraph("Resolution", S_HDR),
        ]]
        per_cell = []

        for i, brk in enumerate(state.breaks):
            ri = i + 1
            bg, fg_hex = _BREAK_BADGE.get(
                brk.break_type, (LIGHT_BLUE, "#2D6A9F"))

            type_text = _BRK_LABEL.get(
                brk.break_type,
                brk.break_type.value.replace("_", " ").title())

            a_refs = (", ".join(r.reference for r in brk.side_a_records)
                      or "(none)")
            b_refs = (", ".join(r.reference for r in brk.side_b_records)
                      or "(none)")

            res = state.resolutions.get(brk.stable_break_key)
            if res:
                rl = _RES_LABEL.get(
                    res.resolution_type,
                    res.resolution_type.value.replace("_", " ").title())
                parts = [f"<b>{rl}</b>"]
                if res.reviewer_text:
                    safe = (res.reviewer_text
                            .replace("&", "&amp;").replace("<", "&lt;"))
                    parts.append(
                        f'<br/><font size="7" color="#898781">'
                        f'<i>{safe}</i></font>')
                if res.adjustment_amount is not None:
                    parts.append(
                        f'<br/><font size="7" color="#898781">'
                        f'Adj: {res.adjustment_amount:,.2f}'
                        f' ({res.adjustment_side})</font>')
                res_cell = Paragraph("".join(parts), S_BODY)
            else:
                draft = state.drafts.get(brk.stable_break_key)
                if draft:
                    safe = (draft.explanation
                            .replace("&", "&amp;").replace("<", "&lt;"))
                    res_cell = Paragraph(
                        f'<b>Open</b><br/>'
                        f'<font size="7" color="#898781">'
                        f'<i>{safe}</i></font>', S_BODY)
                else:
                    res_cell = Paragraph("<b>Open</b>", S_BODY)

            brk_rows.append([
                Paragraph(
                    f'<font color="{fg_hex}"><b>{type_text}</b></font>',
                    S_BODY),
                Paragraph(a_refs, S_BODY),
                Paragraph(b_refs, S_BODY),
                _val(brk.difference),
                res_cell,
            ])

            per_cell.append(("BACKGROUND", (0, ri), (0, ri), bg))
            if i % 2 == 1:
                per_cell.append(
                    ("BACKGROUND", (1, ri), (-1, ri), NEAR_WHITE))

        brk_styles = [
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), DARK_BLUE),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, DARK_BLUE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ] + per_cell

        for i in range(len(state.breaks)):
            brk_styles.append(
                ("LINEBELOW", (0, i + 1), (-1, i + 1), 0.25, RULE))

        brk_table = Table(brk_rows, colWidths=BK_COLS, repeatRows=1)
        brk_table.setStyle(TableStyle(brk_styles))
        story.append(brk_table)
        story.append(Spacer(1, 18))

    # ── TIER 3: sign-off, governance, metadata ───────────────────────
    sign_items = []
    sign_items.append(Paragraph("Sign-off", S_H2))
    sign_items.append(Spacer(1, 6))

    if signed_off_at:
        sign_items.append(Paragraph(
            f"<b>Signed off:</b> {signed_off_at}", S_BODY))
    else:
        s_muted = ParagraphStyle(
            "s_sign_muted", fontName="Helvetica-Oblique",
            fontSize=9, textColor=MUTED, leading=12)
        sign_items.append(Paragraph(
            "Working copy, not yet signed off.", s_muted))

    sign_items.append(Spacer(1, 14))
    sign_items.append(Paragraph(
        "This reconciliation was prepared using the Reconciliation and "
        "Tie-Out Assistant. All matching, classification, and arithmetic "
        "is computed deterministically in tested Python code. "
        "AI commentary (where present) is advisory only: a human reviewer "
        "resolved every break and signed off the result.",
        S_SMALL,
    ))
    sign_items.append(Spacer(1, 6))
    sign_items.append(Paragraph(
        f"Generated: {now}  |  "
        f"Tolerance: {tolerance_abs:.2f} (absolute)"
        f"{', ' + f'{tolerance_rel:.4%} (relative)' if tolerance_rel else ''}  |  "
        f"Clean matches: {state.matched_count}  |  "
        f"Breaks: {len(state.breaks)}",
        S_SMALL,
    ))

    story.append(KeepTogether(sign_items))

    # ── Build ────────────────────────────────────────────────────────
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def record(state: ReconciliationState,
           output_dir: Path | str = "output", *,
           tolerance_abs: float = 0.02,
           tolerance_rel: float = 0) -> ReconciliationState:
    """Write the audit record and PDF report.

    Finance context: the reconciliation report is an audit artefact. It
    must show the complete trail from raw difference to resolved position,
    with every break itemised and every resolution attributed to a human
    reviewer. The report is not a summary; it is the formal record that
    the reconciliation was performed, reviewed, and signed off. The JSONL
    audit log is the machine-readable counterpart, designed for re-run
    detection and historical queries. The PDF is the human-readable
    counterpart, formatted for filing.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Audit record
    audit = _audit_record(state, tolerance_abs=tolerance_abs,
                          tolerance_rel=tolerance_rel)
    audit_path = output_dir / "audit_log.jsonl"
    class _Enc(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)

    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(audit, cls=_Enc) + "\n")
    print(f"  [OK] Audit record appended: {audit_path}")

    # PDF report
    pdf_bytes = build_pdf_bytes(state, tolerance_abs=tolerance_abs,
                                tolerance_rel=tolerance_rel)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pdf_path = output_dir / f"reconciliation_report_{ts}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    print(f"  [OK] PDF report: {pdf_path} ({len(pdf_bytes) / 1024:.1f} KB)")

    return state
