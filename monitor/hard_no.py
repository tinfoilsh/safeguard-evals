"""Compatibility imports for monitor scripts run directly from any directory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hard_no_policy import (
    HARD_NO_CATEGORIES,
    HARD_NO_POLICY,
    JUDGE_REPLY_FORMAT,
    REVIEW_PREAMBLE,
)

__all__ = [
    "HARD_NO_CATEGORIES",
    "HARD_NO_POLICY",
    "JUDGE_REPLY_FORMAT",
    "REVIEW_PREAMBLE",
]
