import pytest
import yaml

from artha import prep
from artha.market_calendar import is_trading_day, next_trading_day
from datetime import date

ALLOWED = {"RELIANCE", "TCS", "SBIN"}


def test_parse_valid_reply_with_chatter_around_json():
    text = 'Here you go:\n```json\n{"checked": ["RELIANCE","TCS","SBIN"], "exclude": [{"symbol": "TCS.NS", "reason": "Q2 results today", "source": "https://x"}]}\n```'
    excl, unchecked = prep.parse_exclusions(text, ALLOWED)
    assert excl == {"TCS": {"reason": "Q2 results today", "source": "https://x"}}
    assert unchecked == []


def test_parse_flags_unchecked_symbols():
    _, unchecked = prep.parse_exclusions('{"checked": ["TCS"], "exclude": []}', ALLOWED)
    assert unchecked == ["RELIANCE", "SBIN"]


@pytest.mark.parametrize("text", [
    "no json here",
    '{"exclude": [{"symbol": "FAKECO", "reason": "x", "source": "y"}]}',   # unknown ticker
    '{"exclude": [{"symbol": "TCS", "reason": "", "source": "y"}]}',        # no reason
    '{"exclude": [{"symbol": "TCS", "reason": "x"}]}',                      # no source
    '{"something": 1}',
])
def test_parse_rejects_untrustworthy_replies(text):
    with pytest.raises(Exception):
        prep.parse_exclusions(text, ALLOWED)


def test_write_exclusions_is_valid_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "CONFIG_DIR", tmp_path)
    prep.write_exclusions(date(2026, 11, 9), {"TCS": {"reason": 'results: "Q2"', "source": "https://x"}}, "ok")
    doc = yaml.safe_load((tmp_path / "exclusions.yaml").read_text())
    assert doc["excluded"][0]["symbol"] == "TCS" and doc["for_date"] == "2026-11-09"


def test_calendar():
    assert is_trading_day(date(2026, 11, 9))          # Monday
    assert not is_trading_day(date(2026, 11, 10))     # Diwali-Balipratipada
    assert not is_trading_day(date(2026, 11, 8))      # Sunday
    assert next_trading_day(date(2026, 11, 9)) == date(2026, 11, 11)
    assert next_trading_day(date(2026, 11, 6)) == date(2026, 11, 9)
