# =============================================================================
# config.py: Layer 1: Configuration
# =============================================================================
#
# Responsibilities:
#   - ROOT, DATA_DIR, OUTPUT_DIR:   file system paths
#   - TOLERANCE_ABS:                matching tolerance (0.02 absolute)
#   - TOLERANCE_REL:                relative tolerance (0, off by default)
#   - MAX_MANY_TO_ONE_ITEMS:        many-to-one combination cap
#   - MANY_TO_ONE_DATE_WINDOW_DAYS: many-to-one date window
#   - ENTITY_NAME:                  reporting entity label
#
# Core rule: every tunable threshold lives here, not in the step that
# uses it. Changing a bound never requires editing a step file.
#
# Knows about: file system layout and numeric bounds
# Does NOT know about: any step's logic or the data it processes
# =============================================================================

from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

BANK_STATEMENT_FILE = DATA_DIR / "bank_statement.csv"
CASH_BOOK_FILE = DATA_DIR / "cash_book.csv"

TOLERANCE_ABS = Decimal("0.02")
TOLERANCE_REL = Decimal("0")

MAX_MANY_TO_ONE_ITEMS = 4
MANY_TO_ONE_DATE_WINDOW_DAYS = 14

ENTITY_NAME = "Valencia Operations"
