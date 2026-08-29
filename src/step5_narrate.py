# =============================================================================
# step5_narrate.py: Layer 6: AI Narration
# =============================================================================
#
# Responsibilities:
#   - narrate():           generate an explanation and follow-up for each break
#   - _templated_draft():  deterministic fallback when the AI is unavailable
#   - _build_ai_prompt():  construct the prompt for Claude
#   - _call_ai():          call the Claude API and parse the response
#
# Core rule: the AI explains breaks it did not choose. It never decides
# what is a break, never changes a figure, and if it fails, the break
# degrades to a templated sentence rather than disappearing.
#
# Knows about: a break and its figures, the prompt, the template library
# Does NOT know about: matching rules, resolution arithmetic, the PDF
# =============================================================================

from __future__ import annotations

import json

from src.state import Break, BreakType, Draft, ReconciliationState


_TEMPLATES = {
    BreakType.AMOUNT_MISMATCH: (
        "The amounts for reference {ref} differ between the two sides: "
        "{side_a_label} shows {a_amt:,.2f} and {side_b_label} shows {b_amt:,.2f}, "
        "a difference of {diff:,.2f}. This may indicate a data entry error, "
        "partial payment, or bank adjustment.",
        "Verify the correct amount with the source document and post a "
        "correcting entry if needed.",
    ),
    BreakType.REFERENCE_MISMATCH: (
        "An item of {a_amt:,.2f} appears on both sides but under different "
        "references: {a_ref} on {side_a_label} and {b_ref} on {side_b_label}. "
        "This is likely a transposed or mistyped reference.",
        "Confirm the correct reference and update the record on the side "
        "that carries the error.",
    ),
    BreakType.UNMATCHED: (
        "An item of {amt:,.2f} ({ref}) appears on {present_side} but has no "
        "counterpart on {missing_side}. {timing_hint}",
        "{follow_up_hint}",
    ),
    BreakType.MANY_TO_ONE_SUGGESTION: (
        "{count} items on {many_side} ({total:,.2f} total) may correspond to "
        "a single entry of {one_amt:,.2f} on {one_side}. "
        "The amounts match within tolerance.",
        "Review the grouping and confirm if the items represent a single "
        "batch deposit or payment. Reject if unrelated.",
    ),
}


def _templated_draft(brk: Break, side_a_label: str, side_b_label: str) -> Draft:
    """Generate a deterministic explanation from templates.

    Finance context: templates encode the most common reconciliation
    scenarios (a bank charge not in the books, an outstanding cheque,
    a receipt in transit). They use the break's own figures and
    references, so the explanation is always accurate even without the
    AI. A controller reading a templated draft gets the same quality of
    information as an AI draft, just in a more formulaic register.
    """
    if brk.break_type == BreakType.MANY_TO_ONE_SUGGESTION:
        if len(brk.side_b_records) > len(brk.side_a_records):
            many_side, one_side = side_b_label, side_a_label
            count = len(brk.side_b_records)
            total = sum(r.signed_amount for r in brk.side_b_records)
            one_amt = sum(r.signed_amount for r in brk.side_a_records)
        else:
            many_side, one_side = side_a_label, side_b_label
            count = len(brk.side_a_records)
            total = sum(r.signed_amount for r in brk.side_a_records)
            one_amt = sum(r.signed_amount for r in brk.side_b_records)
        return Draft(
            explanation=_TEMPLATES[BreakType.MANY_TO_ONE_SUGGESTION][0].format(
                count=count, many_side=many_side, total=total,
                one_amt=one_amt, one_side=one_side,
            ),
            follow_up=_TEMPLATES[BreakType.MANY_TO_ONE_SUGGESTION][1],
        )

    if brk.break_type == BreakType.AMOUNT_MISMATCH:
        ref = brk.side_a_records[0].reference if brk.side_a_records else "unknown"
        return Draft(
            explanation=_TEMPLATES[BreakType.AMOUNT_MISMATCH][0].format(
                ref=ref, a_amt=brk.side_a_amount, b_amt=brk.side_b_amount,
                diff=brk.difference, side_a_label=side_a_label,
                side_b_label=side_b_label,
            ),
            follow_up=_TEMPLATES[BreakType.AMOUNT_MISMATCH][1],
        )

    if brk.break_type == BreakType.REFERENCE_MISMATCH:
        a_ref = brk.side_a_records[0].reference if brk.side_a_records else "?"
        b_ref = brk.side_b_records[0].reference if brk.side_b_records else "?"
        return Draft(
            explanation=_TEMPLATES[BreakType.REFERENCE_MISMATCH][0].format(
                a_amt=brk.side_a_amount, a_ref=a_ref, b_ref=b_ref,
                side_a_label=side_a_label, side_b_label=side_b_label,
            ),
            follow_up=_TEMPLATES[BreakType.REFERENCE_MISMATCH][1],
        )

    if brk.side_a_records and not brk.side_b_records:
        r = brk.side_a_records[0]
        present_side = side_a_label
        missing_side = side_b_label
        if r.direction.value == "money_out":
            timing_hint = (
                "This may be a bank charge or fee not yet recorded in the books."
            )
            follow_up_hint = (
                "If this is a valid charge, book it in the cash book. "
                "If disputed, contact the bank."
            )
        else:
            timing_hint = (
                "This may be a receipt recorded by the bank but not yet "
                "entered in the cash book."
            )
            follow_up_hint = (
                "Confirm the receipt and post it to the cash book."
            )
    elif brk.side_b_records and not brk.side_a_records:
        r = brk.side_b_records[0]
        present_side = side_b_label
        missing_side = side_a_label
        if r.direction.value == "money_out":
            timing_hint = (
                "This looks like a payment recorded in the books but not yet "
                "cleared by the bank, a common timing difference."
            )
            follow_up_hint = (
                "If dated near month-end, accept as a timing difference "
                "that will clear next period. Otherwise investigate."
            )
        else:
            timing_hint = (
                "This may be a receipt recorded in the books but not yet "
                "credited by the bank."
            )
            follow_up_hint = (
                "Confirm with the bank that the deposit is in transit."
            )
    else:
        return Draft(explanation="Unmatched item.", follow_up="Investigate.")

    amt = abs(brk.difference)
    ref = r.reference

    return Draft(
        explanation=_TEMPLATES[BreakType.UNMATCHED][0].format(
            amt=amt, ref=ref, present_side=present_side,
            missing_side=missing_side, timing_hint=timing_hint,
        ),
        follow_up=_TEMPLATES[BreakType.UNMATCHED][1].format(
            follow_up_hint=follow_up_hint,
        ),
    )


def _build_ai_prompt(brk: Break, side_a_label: str, side_b_label: str) -> str:
    """Construct the prompt that asks Claude to explain a single break.

    Finance context: the prompt gives the AI the break type, the figures
    from both sides, and the transaction details, then asks for an
    explanation and a follow-up action. The instruction 'do not include
    any numbers that are not already in the data' prevents the AI from
    inventing figures that could mislead a reviewer.
    """
    lines = [
        "You are a bank reconciliation assistant. Given the break below, write:",
        "1. A plain-language explanation of the likely cause (one paragraph).",
        "2. A drafted follow-up action (one sentence).",
        "",
        f"Break type: {brk.break_type.value}",
        f"Side A ({side_a_label}) amount: {brk.side_a_amount:,.2f}",
        f"Side B ({side_b_label}) amount: {brk.side_b_amount:,.2f}",
        f"Difference (A - B): {brk.difference:,.2f}",
    ]

    for r in brk.side_a_records:
        lines.append(
            f"  {side_a_label} record: {r.date} | {r.reference} | "
            f"{r.signed_amount:,.2f} | {r.description}"
        )
    for r in brk.side_b_records:
        lines.append(
            f"  {side_b_label} record: {r.date} | {r.reference} | "
            f"{r.signed_amount:,.2f} | {r.description}"
        )

    lines.extend([
        "",
        'Respond in JSON: {"explanation": "...", "follow_up": "..."}',
        "Do not include any numbers that are not already in the data above.",
    ])
    return "\n".join(lines)


def _call_ai(client, prompt: str) -> Draft | None:
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(text)
        return Draft(
            explanation=data.get("explanation", ""),
            follow_up=data.get("follow_up", ""),
        )
    except Exception:
        return None


def narrate(state: ReconciliationState, client=None) -> ReconciliationState:
    """Generate an explanation and follow-up for each break.

    Finance context: a reconciliation break is a number; a reviewer
    needs words. The explanation tells the controller what probably
    happened (a timing difference, a data-entry error, a bank charge
    not yet booked) and what to do next. This is the only phase that
    calls the language model. If a Claude client is provided, each
    break gets an AI-drafted explanation; if not (or if the call
    fails), it gets a templated one. The AI drafts are advisory only:
    they describe the break the engine already found, they never
    decide what IS a break, and no number they emit becomes a figure
    in the reconciliation.
    """
    drafts: dict[str, Draft] = {}

    for brk in state.breaks:
        if brk.stable_break_key in state.resolutions:
            continue

        draft = None
        if client is not None:
            prompt = _build_ai_prompt(brk, state.side_a_label, state.side_b_label)
            draft = _call_ai(client, prompt)

        if draft is None:
            draft = _templated_draft(brk, state.side_a_label, state.side_b_label)

        drafts[brk.stable_break_key] = draft

    state.drafts = drafts

    ai_count = 0
    tmpl_count = len(drafts)
    if client is not None:
        ai_count = sum(1 for _ in drafts)
        tmpl_count = len(state.breaks) - ai_count

    print(f"  [OK] Drafted {len(drafts)} explanation(s)")

    return state
