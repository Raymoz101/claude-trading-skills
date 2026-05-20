"""FMP /stable migration for parabolic-short-trade-planner.

Three v3 endpoints used at screen time are migrated (stable-first, v3 fallback):
- get_company_profile: /profile/{sym} -> /stable/profile?symbol= (+ mktCap alias)
- get_sp500_constituents: /sp500_constituent -> /stable/sp500-constituent
- get_earnings_calendar: /earning_calendar -> /stable/earnings-calendar
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fmp_client import FMPClient


def _make_client():
    client = FMPClient(api_key="test_key")
    client.max_retries = 0
    return client


def _resp(status_code, json_payload):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_payload
    resp.text = ""
    return resp


class TestCompanyProfile:
    @patch("fmp_client.requests.Session")
    def test_uses_stable_profile_and_aliases_mktcap(self, mock_session_class):
        mock_session = MagicMock()
        # /stable/profile renames mktCap -> marketCap (numeric in JSON).
        mock_session.get.return_value = _resp(
            200, [{"symbol": "AAPL", "marketCap": 4_000_000_000_000, "sector": "Technology"}]
        )
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        profile = client.get_company_profile("AAPL")
        assert profile["mktCap"] == 4_000_000_000_000  # aliased from marketCap
        call = mock_session.get.call_args
        assert call[0][0].endswith("/stable/profile")
        assert call[1]["params"] == {"symbol": "AAPL"}

    @patch("fmp_client.requests.Session")
    def test_falls_back_to_v3_profile(self, mock_session_class):
        def fake_get(url, params=None, timeout=None):
            if url.endswith("/stable/profile"):
                return _resp(403, [])  # stable unavailable -> fallback
            return _resp(200, [{"symbol": "AAPL", "mktCap": 5}])

        mock_session = MagicMock()
        mock_session.get.side_effect = fake_get
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        profile = client.get_company_profile("AAPL")
        assert profile["mktCap"] == 5
        assert any("/api/v3/profile/AAPL" in c[0][0] for c in mock_session.get.call_args_list)


class TestSP500Constituents:
    @patch("fmp_client.requests.Session")
    def test_uses_stable_constituent_first(self, mock_session_class):
        mock_session = MagicMock()
        mock_session.get.return_value = _resp(200, [{"symbol": "AAPL"}, {"symbol": "MSFT"}])
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        rows = client.get_sp500_constituents()
        assert [r["symbol"] for r in rows] == ["AAPL", "MSFT"]
        assert mock_session.get.call_args_list[0][0][0].endswith("/stable/sp500-constituent")


class TestEarningsCalendar:
    @patch("fmp_client.requests.Session")
    def test_uses_stable_calendar_first(self, mock_session_class):
        mock_session = MagicMock()
        mock_session.get.return_value = _resp(200, [{"symbol": "AAPL", "date": "2026-05-19"}])
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        events = client.get_earnings_calendar("2026-05-01", "2026-05-19")
        assert events[0]["symbol"] == "AAPL"
        assert mock_session.get.call_args_list[0][0][0].endswith("/stable/earnings-calendar")
