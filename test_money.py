import pytest
from money import format_sek, parse_sek


def test_parse():
    assert parse_sek("45.50") == 4550
    assert parse_sek("100") == 10000
    assert parse_sek("0.01") == 1


def test_parse_rejects_bad_input():
    for bad in ["0", "-5", "1.005", "abc", "NaN"]:
        with pytest.raises(ValueError):
            parse_sek(bad)


def test_format():
    assert format_sek(4550) == "45.50"
    assert format_sek(5) == "0.05"
