"""Unit tests for NPS normal-exit date helpers."""

from __future__ import annotations

import datetime

from api.services.nps_exit_projection import nps_normal_exit_date


def test_nps_normal_exit_sixtieth_birthday() -> None:
    dob = datetime.date(1990, 6, 15)
    assert nps_normal_exit_date(dob) == datetime.date(2050, 6, 15)


def test_nps_normal_exit_feb_29_clamps_when_sixtieth_year_not_leap() -> None:
    # 2040 is a leap year; 2100 is not (divisible by 100, not by 400).
    dob = datetime.date(2040, 2, 29)
    assert nps_normal_exit_date(dob) == datetime.date(2100, 2, 28)
