# =============================================================================
# step1_load.py: Layer 2: Data Loading and Normalisation
# =============================================================================
#
# Responsibilities:
#   - load():              read both CSVs and normalise into Records
#   - _parse_amount():     coerce formatted amounts (currency, parens, commas)
#   - _parse_date():       coerce dates across 11 format variants
#   - _detect_schema():    detect bank, book, or standard column layout
#   - _normalise_rows():   row-level parsing with Problem reporting
#   - load_raw_table():    read a CSV in its native format for display
#
# Core rule: every parseable row becomes a Record; every unparseable row
# becomes a reported Problem. Nothing is silently dropped.
#
# Knows about: CSV structure, date formats, amount formats, column schemas
# Does NOT know about: matching rules, break types, resolutions, the AI, the PDF
# =============================================================================

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from src.state import Direction, Record, ReconciliationState


_REQUIRED_COLUMNS = {"date", "reference", "amount", "description"}

_KNOWN_HEADERS = {
    "date", "reference", "amount", "description",
    "value date", "type", "details", "paid out", "paid in", "balance",
    "voucher", "particulars", "receipts", "payments",
}


# -- Amount parsing -----------------------------------------------------------

def _parse_amount(raw: str | None) -> Decimal:
    """Parse a numeric amount from potentially formatted input.

    Handles currency symbols, thousands separators (comma, dot, or space),
    parentheses for negatives (accounting style), trailing minus, and both
    comma-decimal and dot-decimal conventions.

    Finance context: real-world bank exports carry amounts in many formats,
    including currency symbols, accounting parentheses for negatives, trailing
    minus signs, European comma-decimal notation, and space-separated
    thousands. This parser handles all of them so the tool works with
    unmodified bank downloads.
    """
    if not raw or not raw.strip():
        raise ValueError("Blank amount")

    s = raw.strip()

    # Strip everything except digits, dots, commas, parens, signs
    s = re.sub(r"[^\d.,()+-]", "", s)

    if not s:
        raise ValueError(f"Amount could not be understood: {raw.strip()!r}")

    negative = False

    if s.startswith("-"):
        negative = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]

    if s.startswith("(") and s.endswith(")"):
        negative = not negative
        s = s[1:-1]

    if s.startswith("-"):
        negative = not negative
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]

    if s.endswith("-"):
        negative = not negative
        s = s[:-1]

    s = re.sub(r"[^\d.,]", "", s)

    if not s or not any(c.isdigit() for c in s):
        raise ValueError(f"Amount could not be understood: {raw.strip()!r}")

    last_comma = s.rfind(",")
    last_dot = s.rfind(".")

    if last_comma >= 0 and last_dot >= 0:
        if last_comma > last_dot:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif last_comma >= 0:
        parts = s.split(",")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            s = s.replace(",", "")
        else:
            idx = s.rfind(",")
            s = s[:idx].replace(",", "") + "." + s[idx + 1:]
    elif last_dot >= 0 and s.count(".") > 1:
        parts = s.split(".")
        if all(len(p) == 3 for p in parts[1:]):
            s = s.replace(".", "")
        else:
            raise ValueError(
                f"Amount could not be understood: {raw.strip()!r}")

    value = Decimal(s)
    return -value if negative else value


# -- Date parsing -------------------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d/%b/%Y",
)


def _parse_date(raw: str | None) -> date:
    if not raw or not raw.strip():
        raise ValueError("Blank date")
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {raw!r}")


# -- Schema detection --------------------------------------------------------

def _find_header(text: str) -> tuple[int, list[str]]:
    """Locate the CSV header row, skipping chrome/metadata rows.

    Returns (number_of_chrome_rows, remaining_text_lines).
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            cells = next(csv.reader(io.StringIO(line)))
        except StopIteration:
            continue
        normalised = {c.strip().lower() for c in cells if c.strip()}
        hits = normalised & _KNOWN_HEADERS
        if len(hits) >= 2 and "date" in hits:
            return i, lines[i:]
    return 0, lines


def _detect_schema(fieldnames: list[str]) -> str:
    norm = {f.strip().lower() for f in fieldnames}
    if {"paid out", "paid in"} & norm or {"paid_out", "paid_in"} & norm:
        return "bank"
    if "receipts" in norm or "payments" in norm:
        return "book"
    if _REQUIRED_COLUMNS <= norm:
        return "standard"
    return "standard"


def _col(fieldnames: list[str], target: str) -> str | None:
    """Find original column name matching target (case-insensitive)."""
    for f in fieldnames:
        if f.strip().lower() == target:
            return f
    return None


# -- CSV reading --------------------------------------------------------------

def _read_csv_text(path: str) -> str:
    """Read CSV file text, tolerating UTF-8 BOM and latin-1."""
    p = Path(path)
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot read {path}: unsupported file encoding")


def _parse_two_column_amount(
    row: dict, col_in: str | None, col_out: str | None,
) -> Decimal:
    """Parse amount from separate debit/credit columns.

    The column position determines the sign: the "in" column is positive,
    the "out" column is negative. Any embedded sign in the raw value (from
    parentheses, trailing minus, etc.) is absorbed by abs() so the column
    position always wins.
    """
    raw_in = (row.get(col_in, "") or "").strip() if col_in else ""
    raw_out = (row.get(col_out, "") or "").strip() if col_out else ""

    if raw_in and raw_out:
        return abs(_parse_amount(raw_in)) - abs(_parse_amount(raw_out))
    elif raw_in:
        return abs(_parse_amount(raw_in))
    elif raw_out:
        return -abs(_parse_amount(raw_out))
    else:
        raise ValueError("Both amount columns are blank")


def _normalise_rows(path: str, side: str) -> tuple[list[Record], list[dict]]:
    """Read a CSV into normalised Records.

    Returns (records, problems) where problems is a list of dicts
    describing rows that could not be read.
    """
    records: list[Record] = []
    problems: list[dict] = []

    text = _read_csv_text(path)
    chrome_count, body_lines = _find_header(text)
    body_text = "\n".join(body_lines)

    reader = csv.DictReader(io.StringIO(body_text))

    if reader.fieldnames is None:
        raise ValueError(f"Empty or unreadable CSV: {path}")

    schema = _detect_schema(reader.fieldnames)

    if schema == "bank":
        col_date = _col(reader.fieldnames, "date")
        col_ref = _col(reader.fieldnames, "details")
        col_desc = _col(reader.fieldnames, "type")
        col_in = _col(reader.fieldnames, "paid in")
        col_out = _col(reader.fieldnames, "paid out")
        if not col_date or not col_in or not col_out:
            raise ValueError(
                f"Side {side} file looks like a bank statement but is "
                f"missing Date, Paid Out, or Paid In columns."
            )
    elif schema == "book":
        col_date = _col(reader.fieldnames, "date")
        col_ref = _col(reader.fieldnames, "voucher")
        col_desc = _col(reader.fieldnames, "particulars")
        col_in = _col(reader.fieldnames, "receipts")
        col_out = _col(reader.fieldnames, "payments")
        if not col_date or not col_in or not col_out:
            raise ValueError(
                f"Side {side} file looks like a cash book but is "
                f"missing Date, Receipts, or Payments columns."
            )
    else:
        normalised_fields = {f.strip().lower(): f for f in reader.fieldnames}
        missing = _REQUIRED_COLUMNS - set(normalised_fields.keys())
        if missing:
            raise ValueError(
                f"Side {side} file is missing required column(s): "
                f"{', '.join(sorted(missing))}. "
                f"Expected columns: date, reference, amount, description."
            )
        col_date = normalised_fields["date"]
        col_ref = normalised_fields["reference"]
        col_desc = normalised_fields["description"]
        col_amt = normalised_fields["amount"]
        col_in = None
        col_out = None

    if chrome_count > 0:
        print(f"  [OK] Skipped {chrome_count} chrome row(s) in "
              f"{Path(path).name}")

    header_offset = chrome_count + 1
    for row_num, row in enumerate(reader, start=header_offset + 1):
        if None in row and isinstance(row[None], list):
            problems.append({
                "side": side, "row": row_num,
                "column": "(structure)",
                "message": (
                    "Row has more fields than the header "
                    "(possible stray delimiter or unquoted comma), skipped"
                ),
            })
            continue

        vals = [(v or "").strip() for v in row.values() if v is not None]
        if not any(vals):
            problems.append({
                "side": side, "row": row_num,
                "column": "(all)", "message": "Blank row, skipped",
            })
            continue

        try:
            dt = _parse_date(row.get(col_date))
        except (ValueError, KeyError) as exc:
            problems.append({
                "side": side, "row": row_num,
                "column": "date", "message": str(exc),
            })
            continue

        try:
            if schema in ("bank", "book"):
                signed = _parse_two_column_amount(row, col_in, col_out)
            else:
                signed = _parse_amount(row.get(col_amt))
        except (ValueError, KeyError) as exc:
            problems.append({
                "side": side, "row": row_num,
                "column": "amount", "message": str(exc),
            })
            continue

        ref = (row.get(col_ref) or "").strip() if col_ref else ""
        desc = (row.get(col_desc) or "").strip() if col_desc else ""

        abs_amount = abs(signed)
        direction = Direction.MONEY_IN if signed >= 0 else Direction.MONEY_OUT

        row_id = f"{side}-{row_num}"
        records.append(Record(
            row_id=row_id,
            side=side,
            date=dt,
            reference=ref,
            description=desc,
            signed_amount=signed,
            abs_amount=abs_amount,
            direction=direction,
        ))

    return records, problems


def load_raw_table(path: str) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV and return its native columns and rows for display.

    Finance context: the input tables page shows the data exactly as the
    user uploaded it, in the bank's or book's own column layout, so the
    reviewer can see what the tool is working with before normalisation.
    Chrome rows are skipped but amounts and dates are NOT normalised.
    """
    text = _read_csv_text(path)
    _, body_lines = _find_header(text)
    body_text = "\n".join(body_lines)
    reader = csv.DictReader(io.StringIO(body_text))
    if reader.fieldnames is None:
        return [], []
    cols = list(reader.fieldnames)
    rows = []
    for row in reader:
        if None in row and isinstance(row[None], list):
            continue
        vals = [(v or "").strip() for v in row.values() if v is not None]
        if not any(vals):
            continue
        rows.append({c: (row.get(c) or "").strip() for c in cols})
    return cols, rows


def _reconciliation_id(file_a: str, file_b: str) -> str:
    combined = f"{Path(file_a).name}|{Path(file_b).name}"
    return hashlib.sha256(combined.encode()).hexdigest()[:12]


def load(file_a: str, file_b: str) -> ReconciliationState:
    """Read the two input files and normalise into Records.

    Finance context: the two sides of a reconciliation (typically a bank
    statement and the company's own cash book) record the same underlying
    cash movements but in different formats and with different sign
    conventions. The bank shows Paid Out / Paid In columns; the book
    shows Receipts / Payments. This function reads each file in its
    native schema, determines the sign from column position (not from
    embedded signs, which vary between banks), normalises every row to
    a common Record shape with a signed amount, and reports any rows it
    cannot parse. The output is two flat lists of Records ready for
    matching.
    """
    side_a, problems_a = _normalise_rows(file_a, "A")
    side_b, problems_b = _normalise_rows(file_b, "B")

    from src.state import Problem
    problems = []
    for p in problems_a + problems_b:
        problems.append(Problem(
            side=p["side"], row_number=p["row"],
            column=p["column"], message=p["message"],
        ))

    state = ReconciliationState(
        reconciliation_id=_reconciliation_id(file_a, file_b),
        side_a=side_a,
        side_b=side_b,
        side_a_label=Path(file_a).stem.replace("_", " ").title(),
        side_b_label=Path(file_b).stem.replace("_", " ").title(),
        problems=problems,
    )

    print(f"  [OK] Side A loaded: {len(side_a)} records from "
          f"{Path(file_a).name}")
    print(f"  [OK] Side B loaded: {len(side_b)} records from "
          f"{Path(file_b).name}")
    if problems:
        print(f"  [!]  {len(problems)} row(s) could not be read")

    return state
