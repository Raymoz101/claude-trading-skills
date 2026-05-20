"""FMP /stable migration for pead-screener: earnings-calendar + profile-bulk.

- get_earnings_calendar: v3 /earning_calendar -> /stable/earnings-calendar.
- get_company_profiles: /stable has no batch profile endpoint (comma-batched
  ?symbol= silently returns []), so the universe is pulled once from
  /stable/profile-bulk (CSV) and looked up locally, with v3-compatible field
  aliases and a legacy v3 batched fallback.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fmp_client import FMPClient

_BULK_HEADER = ["symbol", "price", "marketCap", "exchange", "companyName", "sector", "industry"]


def _bulk_csv(rows):
    lines = ['"' + '","'.join(_BULK_HEADER) + '"']
    for r in rows:
        lines.append(",".join(str(r.get(h, "")) for h in _BULK_HEADER))
    return "\n".join(lines) + "\n"


def _make_client():
    client = FMPClient(api_key="test_key")
    client.max_retries = 0
    return client


def _resp(status_code, *, text="", json_payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_payload
    return resp


class TestEarningsCalendarStable:
    @patch("fmp_client.requests.Session")
    def test_uses_stable_calendar_first(self, mock_session_class):
        mock_session = MagicMock()
        mock_session.get.return_value = _resp(
            200, json_payload=[{"symbol": "AAPL", "date": "2026-05-19"}]
        )
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        result = client.get_earnings_calendar("2026-05-01", "2026-05-19")
        assert result and result[0]["symbol"] == "AAPL"
        first_url = mock_session.get.call_args_list[0][0][0]
        assert first_url.endswith("/stable/earnings-calendar")

    @patch("fmp_client.requests.Session")
    def test_falls_back_to_v3_calendar(self, mock_session_class):
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(url)
            if url.endswith("/stable/earnings-calendar"):
                return _resp(404, json_payload=[])  # stable empty -> fallback
            return _resp(200, json_payload=[{"symbol": "AAPL", "date": "2026-05-19"}])

        mock_session = MagicMock()
        mock_session.get.side_effect = fake_get
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        result = client.get_earnings_calendar("2026-05-01", "2026-05-19")
        assert result and result[0]["symbol"] == "AAPL"
        assert any(u.endswith("/api/v3/earning_calendar") for u in calls)


class TestProfilesViaBulk:
    @patch("fmp_client.requests.Session")
    def test_lookup_and_field_normalization(self, mock_session_class):
        bulk = _bulk_csv(
            [
                {
                    "symbol": "AAPL",
                    "price": "298.97",
                    "marketCap": "4391078823320",
                    "exchange": "NASDAQ",
                    "companyName": "Apple Inc.",
                    "sector": "Technology",
                },
                {
                    "symbol": "MSFT",
                    "price": "417.42",
                    "marketCap": "3100775250600",
                    "exchange": "NASDAQ",
                    "companyName": "Microsoft",
                    "sector": "Technology",
                },
            ]
        )

        def fake_get(url, params=None, timeout=None):
            if url.endswith("/profile-bulk"):
                return _resp(200, text=bulk if (params or {}).get("part") == 0 else "")
            raise AssertionError(f"unexpected request: {url}")

        mock_session = MagicMock()
        mock_session.get.side_effect = fake_get
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        result = client.get_company_profiles(["AAPL", "MSFT", "NVDA"])  # NVDA absent -> omitted
        assert set(result) == {"AAPL", "MSFT"}
        assert result["AAPL"]["mktCap"] == 4391078823320.0
        assert isinstance(result["AAPL"]["mktCap"], float)
        assert result["AAPL"]["exchangeShortName"] == "NASDAQ"
        assert all(c[0][0].endswith("/profile-bulk") for c in mock_session.get.call_args_list)

    @patch("fmp_client.requests.Session")
    def test_falls_back_to_v3_when_bulk_unavailable(self, mock_session_class):
        def fake_get(url, params=None, timeout=None):
            if url.endswith("/profile-bulk"):
                return _resp(403, text="Legacy Endpoint")
            if "/api/v3/profile/" in url:
                return _resp(200, json_payload=[{"symbol": "AAPL", "mktCap": 5}])
            raise AssertionError(f"unexpected request: {url}")

        mock_session = MagicMock()
        mock_session.get.side_effect = fake_get
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        result = client.get_company_profiles(["AAPL"])
        assert result["AAPL"]["mktCap"] == 5
        assert any("/api/v3/profile/" in c[0][0] for c in mock_session.get.call_args_list)
