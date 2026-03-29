"""
Statutory PPF (India) maturity — shared by CSV parsers and the holdings API.

Rule (simplified): a PPF account matures **15 years** after the **end of the
financial year** in which the **first subscription** (your first contribution)
was made. Indian FY runs **1 Apr – 31 Mar**, so “end of FY” is always **31 March**.

References: PPF scheme / RBI FAQs (maturity = 15 years from end of FY of
initial subscription). This module does not model extensions (blocks of 5 years).
"""

from __future__ import annotations

import datetime


def indian_fy_end_containing(d: datetime.date) -> datetime.date:
    """Return 31 March that closes the Indian financial year containing ``d``.

    FY = 1 Apr (year Y) through 31 Mar (year Y+1). Examples:
    - 5 Apr 2020 → 31 Mar 2021
    - 10 Mar 2020 → 31 Mar 2020
    """
    if d.month > 3 or (d.month == 3 and d.day == 31):
        # On 31 Mar we are still the FY that *ended* that day (1 Apr prev year – 31 Mar this year).
        if d.month == 3 and d.day == 31:
            return d
        # Apr 1 … Dec 31: FY ends next year’s March 31
        return datetime.date(d.year + 1, 3, 31)
    # Jan 1 … Mar 30: FY ends this year’s March 31
    return datetime.date(d.year, 3, 31)


def ppf_statutory_maturity_date(first_subscription: datetime.date) -> datetime.date:
    """Maturity date = FY-end containing first subscription + 15 years."""
    fy_end = indian_fy_end_containing(first_subscription)
    return fy_end.replace(year=fy_end.year + 15)
