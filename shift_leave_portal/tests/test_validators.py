"""Input validation helpers."""

from __future__ import annotations

import pytest

from utils import validators as v
from utils.security import neutralize_spreadsheet_formula


@pytest.mark.parametrize("raw, expected", [("Sara.Ali", "sara.ali"), (" omar_h ", "omar_h"), ("a-1", "a-1")])
def test_valid_usernames(raw, expected):
    assert v.validate_username(raw) == expected


@pytest.mark.parametrize("raw", ["", "ab", ".sara", "sara.", "sara ali", "sara@ali", "x" * 33, "سارة"])
def test_invalid_usernames(raw):
    with pytest.raises(ValueError):
        v.validate_username(raw)


def test_employee_id_and_email():
    assert v.validate_employee_id(" emp-101 ") == "EMP-101"
    with pytest.raises(ValueError):
        v.validate_employee_id("EMP 101")
    assert v.validate_email_address("Sara.Ali@Example.COM") == "sara.ali@example.com"
    assert v.validate_email_address("") is None
    with pytest.raises(ValueError):
        v.validate_email_address("not-an-email")


def test_sanitize_text():
    assert v.sanitize_text("  hello\u0000   world ​", max_length=50) == "hello world"
    assert v.sanitize_text("line1\r\n\r\n\r\n\r\nline2", max_length=50, multiline=True) == "line1\n\nline2"
    assert v.sanitize_text("ﬁle", max_length=10) == "file"  # NFKC
    with pytest.raises(ValueError):
        v.sanitize_text("x" * 11, max_length=10)
    with pytest.raises(ValueError):
        v.required_text("   ", max_length=10)


def test_team_and_ticket():
    assert v.validate_team(" Technical Support ") == "Technical Support"
    with pytest.raises(ValueError):
        v.validate_team("<b>Team</b>")
    assert v.validate_ticket_reference("INC-123/45") == "INC-123/45"
    with pytest.raises(ValueError):
        v.validate_ticket_reference("INC;DROP TABLE")


def test_formula_neutralisation():
    assert neutralize_spreadsheet_formula("=1+1") == "'=1+1"
    assert neutralize_spreadsheet_formula("@SUM(A1)") == "'@SUM(A1)"
    assert neutralize_spreadsheet_formula("normal") == "normal"
    assert neutralize_spreadsheet_formula(5) == 5
