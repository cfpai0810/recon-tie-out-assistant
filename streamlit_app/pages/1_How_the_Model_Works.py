# =============================================================================
# pages/1_How_the_Model_Works.py: user-facing explanation of the tool
# =============================================================================

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_app.lib.theme import inject_css

st.markdown(inject_css(), unsafe_allow_html=True)


# -- Title --------------------------------------------------------------------
st.markdown("### How it works")
st.write(
    "A plain-language guide to what the tool does, how it matches "
    "transactions, what makes a break, and what the output means for "
    "you as a reviewer.")

st.markdown("---")


# -- The problem --------------------------------------------------------------
st.markdown("#### The problem")
st.write(
    "At month-end a finance controller reconciles the bank statement "
    "(the bank's record of transactions on your account) against the "
    "cash book (your own internal record of those same transactions). "
    "In practice that means comparing the two lists line by line, "
    "matching pairs, identifying differences, and deciding what to do "
    "about each one. With dozens "
    "or hundreds of transactions, the risk is not that something goes "
    "unseen but that review time is spread too thinly across items that "
    "turn out to be routine.")
st.write(
    "This tool automates the matching step and classifies the breaks "
    "so the controller's time goes where it matters: the transactions "
    "that genuinely need investigation.")

st.markdown("---")


# -- The two layers -----------------------------------------------------------
st.markdown("#### Two layers: match, then narrate")
st.write(
    "The tool works in two layers, and the distinction matters.")

st.markdown("**Layer 1: Matching and classification** "
            "(deterministic, instant, free)")
st.write(
    "Python matches every transaction between the two sides using "
    "five rules in strict precedence: exact key match, within-tolerance "
    "match, amount mismatch (same reference, different amount), reference "
    "mismatch (same amount, different reference), and many-to-one "
    "(several items on one side summing to one item on the other). "
    "Unmatched or mismatched items become typed breaks. This step uses "
    "no AI: the rules are fixed, the arithmetic is transparent, and "
    "the same data always produces the same result.")

st.markdown("**Layer 2: AI narration** "
            "(on request, uses your API key)")
st.write(
    "Once you have the breaks, you can ask the AI to explain each one. "
    "For every break it reads the transaction details Python has already "
    "matched and classified, and writes a plain-language explanation of "
    "what likely happened and what the reviewer should do. This step "
    "makes one call to Claude and uses your Anthropic API key.")

st.info(
    "The AI never decides what is a break. It only interprets items "
    "that the matching engine has already classified. It cannot remove "
    "a break or hide a transaction from the review.")

st.markdown("---")


# -- The five matching rules --------------------------------------------------
st.markdown("#### The five matching rules")
st.write(
    "Transactions are matched in strict precedence. Each rule is tried "
    "in order; once a transaction is matched it is removed from the pool "
    "for later rules.")

st.markdown("**Rule 1: Exact key match.**")
st.write(
    "Same reference, same direction (both receipts or both payments), "
    "and exactly equal amounts. This is the cleanest match.")

st.markdown("**Rule 2: Within tolerance.**")
st.write(
    "Same reference, same direction, and amounts differ by no more than "
    "the tolerance bound. The bound is the larger of the absolute "
    "threshold (default 0.02) and the relative threshold (default 0, "
    "off) applied to the larger amount. Rounding differences land here.")

st.markdown("**Rule 3: Amount mismatch.**")
st.write(
    "Same reference, same direction, but amounts differ by more than the "
    "tolerance. This becomes a break: the transactions are paired but the "
    "amounts do not agree.")

st.write(
    "**A note on amount errors across reference systems.** "
    "The amount-mismatch rule pairs two entries that share a reference "
    "but differ in amount. If the two sides use different reference "
    "systems (for example a bank transaction code on one side and a "
    "voucher number on the other), an amount error cannot be paired "
    "this way and appears instead as two unmatched items, one on each "
    "side. This is deliberate: pairing purely on a similar amount "
    "without a shared reference would risk matching two unrelated "
    "transactions.")

st.markdown("**Rule 4: Reference mismatch.**")
st.write(
    "Same amount (within tolerance), same direction, but different "
    "references. This becomes a break: the amounts agree but the "
    "references do not, so the pairing may be wrong.")

st.markdown("**Rule 5: Many-to-one.**")
st.write(
    "Several items on one side sum to one item on the other, within "
    "tolerance, same direction, and within a date window. Bounded at "
    "four items and 14 days to prevent false positives. Many-to-one "
    "pairings are presented as suggestions for the reviewer to confirm "
    "or reject, not as automatic matches.")

st.markdown("---")


# -- Worked example -----------------------------------------------------------
st.markdown("#### Worked example")
st.write(
    "How each rule fires, with illustrative numbers. All figures below "
    "are teaching examples, not the sample data.")

st.markdown("**Rule 1: Exact key** :white_check_mark:")
st.markdown(
    "| Side | Reference | Date | Amount |\n"
    "|------|-----------|------|-------:|\n"
    "| Bank (A) | INV-100 | 10 Jun | -500.00 |\n"
    "| Book (B) | INV-100 | 10 Jun | -500.00 |")
st.caption(
    "Match: same reference, date, and amount. The cleanest pairing.")

st.write("")
st.markdown("**Rule 2: Within tolerance** :white_check_mark:")
st.markdown(
    "| Side | Reference | Date | Amount |\n"
    "|------|-----------|------|-------:|\n"
    "| Bank (A) | INV-101 | 11 Jun | -320.00 |\n"
    "| Book (B) | INV-101 | 11 Jun | -320.01 |")
st.caption(
    "Match: same reference, 0.01 difference is within the 0.02 absolute "
    "tolerance. The difference is absorbed.")

st.write("")
st.markdown("**Rule 3: Amount mismatch** :warning:")
st.markdown(
    "| Side | Reference | Date | Amount |\n"
    "|------|-----------|------|-------:|\n"
    "| Bank (A) | INV-102 | 12 Jun | -1,000.00 |\n"
    "| Book (B) | INV-102 | 12 Jun | -1,090.00 |")
st.caption(
    "Break: same reference, but 90.00 difference is beyond tolerance. "
    "Flagged as an amount mismatch for review.")

st.write("")
st.markdown("**Rule 4: Reference mismatch** :warning:")
st.markdown(
    "| Side | Reference | Date | Amount |\n"
    "|------|-----------|------|-------:|\n"
    "| Bank (A) | TFR-556 | 13 Jun | -750.00 |\n"
    "| Book (B) | TRF-889 | 14 Jun | -750.00 |")
st.caption(
    "Break: same amount, but references and dates differ. The date "
    "need not match; the matcher prefers the closest date as a "
    "tie-break, but does not require it. Flagged as a reference "
    "mismatch for review.")

st.write("")
st.markdown("**Rule 5: Many-to-one** :mag: (suggestion, needs confirmation)")
st.markdown(
    "| Side | Reference | Date | Amount |\n"
    "|------|-----------|------|-------:|\n"
    "| Bank (A) | DEP-BATCH | 14 Jun | +900.00 |\n"
    "| Book (B) | RCT-1 | 13 Jun | +300.00 |\n"
    "| Book (B) | RCT-2 | 13 Jun | +250.00 |\n"
    "| Book (B) | RCT-3 | 14 Jun | +350.00 |")
st.caption(
    "Suggestion: three book receipts (300 + 250 + 350 = 900) sum to one "
    "bank deposit, within the 14-day window. Presented for reviewer "
    "confirmation.")

st.write("")
st.info(
    "The tolerance in Rule 2 is adjustable. Both an absolute threshold "
    "(default 0.02) and a relative threshold (default 0, off) are "
    "available; the effective bound is whichever is larger. With a "
    "relative tolerance set, a large-amount pair that Rule 3 would break "
    "at the default can instead match under Rule 2 if within the relative "
    "allowance (for example, a 0.50 difference on a 1,000,000.00 item is "
    "within 0.1%).")

st.markdown("---")


# -- What makes a break -------------------------------------------------------
st.markdown("#### What makes a break")
st.write(
    "A break is any transaction that is not cleanly matched. There are "
    "four types.")
st.write(
    "- **Amount mismatch.** Two transactions share a reference but "
    "disagree on the amount. The break records the difference.\n"
    "- **Reference mismatch.** Two transactions agree on the amount "
    "but have different references. The break records the pairing.\n"
    "- **Unmatched.** A transaction on one side has no counterpart "
    "on the other. The break records the full amount.\n"
    "- **Many-to-one suggestion.** Several items on one side sum to "
    "a single item on the other. Presented for reviewer confirmation, "
    "not as an automatic match.")

st.markdown("---")


# -- The five resolution actions -----------------------------------------------
st.markdown("#### Resolving a break")
st.write(
    "Each break is resolved with one of five actions.")
st.write(
    "- **Accept as timing.** The item is a timing difference: a "
    "transaction recorded by one side but not yet by the other, which "
    "will clear in a future period. It stands in the net difference.\n"
    "- **Adjust.** An adjusting entry is booked. The break's "
    "contribution is removed from the difference.\n"
    "- **Investigate.** Left open for follow-up. The break stays in "
    "the difference.\n"
    "- **Dismiss.** Immaterial, removed from the difference.\n"
    "- **Confirm match.** Applies to many-to-one suggestions only. "
    "The reviewer accepts the grouping and the difference is removed.")

st.markdown("---")


# -- The reconciled position --------------------------------------------------
st.markdown("#### The reconciled position")
st.write(
    "The raw difference is the simple arithmetic: bank total minus book "
    "total. As each break is resolved, the reconciled position updates. "
    "Timing differences stand (the reviewer accepts them). Adjustments "
    "and dismissals reduce the difference. Investigations leave it open. "
    "When all breaks are resolved, the reconciled position shows the "
    "net effect of the reviewer's decisions.")

st.divider()
st.caption("Sample data only. All figures are illustrative.")
