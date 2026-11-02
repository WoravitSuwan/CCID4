import datetime as dt

import pytest

from apps.core import thai_format as tf


def test_digits():
    assert tf.to_thai_digits("2569 ซอย 12/3") == "๒๕๖๙ ซอย ๑๒/๓"
    assert tf.to_arabic_digits("๐๘๑") == "081"
    assert tf.to_thai_digits(None) == ""


def test_month_names():
    assert tf.thai_month_name(2) == "กุมภาพันธ์"
    assert tf.thai_month_name(12) == "ธันวาคม"
    assert tf.thai_month_abbr(10) == "ต.ค."
    with pytest.raises(ValueError):
        tf.thai_month_name(13)


def test_years():
    assert tf.ce_to_be(2026) == 2569
    assert tf.ce_to_be(dt.date(2026, 2, 9)) == 2569
    assert tf.be_to_ce(2569) == 2026
    assert tf.normalize_year_to_ce(2569) == 2026
    assert tf.normalize_year_to_ce(2026) == 2026
    assert tf.be_year_2(dt.date(2026, 2, 9)) == "๖๙"
    assert tf.be_year_2(2043) == "๘๖"
    assert tf.be_year_2(2057, thai_digits=False) == "00"


def test_full_date():
    assert tf.thai_date_full(dt.date(2026, 2, 9)) == "๙ กุมภาพันธ์ ๒๕๖๙"
    assert tf.thai_date_full(dt.date(2026, 2, 9), thai_digits=False) == "9 กุมภาพันธ์ 2569"
    assert tf.thai_date_short(dt.date(2026, 2, 9)) == "๙ ก.พ. ๖๙"


def test_time():
    assert tf.thai_time(dt.time(9, 0)) == "๐๙.๐๐"
    assert tf.thai_time(dt.time(13, 30), thai_digits=False) == "13.30"
    assert tf.thai_datetime_full(dt.datetime(2026, 2, 23, 9, 30)) == "๒๓ กุมภาพันธ์ ๒๕๖๙ เวลา ๐๙.๓๐ น."
