"""Month arithmetic and the date the label method switches from the paper's to Jev's."""

from __future__ import annotations

import calendar
from datetime import date

# Filings on or after this date are labeled by Jev; earlier ones are frozen paper rows.
METHOD_SWITCH = date(2026, 10, 1)


def month_end(d: date) -> date:
    """Return the last day of `d`'s month."""
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    """Return the first day of the month `n` months after `d`'s month (n may be negative)."""
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, 1)


def previous_month(d: date) -> date:
    """Return the first day of the month before `d`'s month."""
    return add_months(d, -1)


def parse_month(s: str) -> date:
    """Parse 'YYYY-MM' into the first day of that month."""
    return date.fromisoformat(f"{s}-01")


def rescan_window(run_month: date, n: int = 3) -> list[tuple[date, date]]:
    """Return (first, last) day of the `n` months ending at `run_month`, oldest first.

    Months before METHOD_SWITCH are dropped: they belong to the paper and are never rescanned.
    """
    firsts = (add_months(run_month, k) for k in range(-(n - 1), 1))
    return [(f, month_end(f)) for f in firsts if f >= METHOD_SWITCH]
