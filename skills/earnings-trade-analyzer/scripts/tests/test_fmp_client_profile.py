"""FMP /stable migration: company profiles via profile-bulk.

/stable has no batch profile endpoint (comma-batched ?symbol= silently returns
[]), so the full profile universe is downloaded once from /stable/profile-bulk
(CSV, paginated by `part`) and requested symbols are looked up locally. Field
names are normalized to v3-compatible aliases (marketCap -> mktCap,
exchange -> exchangeShortName) and CSV numerics are coerced. A legacy v3 batched
fallback is used when profile-bulk is unavailable.
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
                    "industry": "Consumer Electronics",
                },
                {
                    "symbol": "MSFT",
                    "price": "417.42",
                    "marketCap": "3100775250600",
                    "exchange": "NASDAQ",
                    "companyName": "Microsoft",
                    "sector": "Technology",
                    "industry": "Software",
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

        # NVDA is not in the bulk dump -> simply omitted (as in production).
        result = client.get_company_profiles(["AAPL", "MSFT", "NVDA"])

        assert set(result) == {"AAPL", "MSFT"}
        # v3-compatible aliases present and numerics coerced from CSV strings.
        assert result["AAPL"]["mktCap"] == 4391078823320.0
        assert isinstance(result["AAPL"]["mktCap"], float)
        assert result["AAPL"]["exchangeShortName"] == "NASDAQ"
        assert result["AAPL"]["price"] == 298.97
        assert result["MSFT"]["sector"] == "Technology"

        # Only profile-bulk was hit (no per-symbol requests), and it was cached:
        bulk_calls = [
            c for c in mock_session.get.call_args_list if c[0][0].endswith("/profile-bulk")
        ]
        assert len(bulk_calls) >= 1
        assert all(c[0][0].endswith("/profile-bulk") for c in mock_session.get.call_args_list)

    @patch("fmp_client.requests.Session")
    def test_bulk_downloaded_once_and_cached(self, mock_session_class):
        bulk = _bulk_csv([{"symbol": "AAPL", "marketCap": "100", "exchange": "NYSE"}])

        def fake_get(url, params=None, timeout=None):
            return _resp(200, text=bulk if (params or {}).get("part") == 0 else "")

        mock_session = MagicMock()
        mock_session.get.side_effect = fake_get
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        client.get_company_profiles(["AAPL"])
        calls_after_first = mock_session.get.call_count
        client.get_company_profiles(["AAPL"])  # second call should hit the cache
        assert mock_session.get.call_count == calls_after_first


class TestLegacyV3Fallback:
    @patch("fmp_client.requests.Session")
    def test_falls_back_to_v3_when_bulk_unavailable(self, mock_session_class):
        def fake_get(url, params=None, timeout=None):
            if url.endswith("/profile-bulk"):
                return _resp(403, text="Legacy Endpoint")  # bulk unavailable
            if "/api/v3/profile/" in url:
                return _resp(
                    200, json_payload=[{"symbol": "AAPL", "mktCap": 5, "exchangeShortName": "NYSE"}]
                )
            raise AssertionError(f"unexpected request: {url}")

        mock_session = MagicMock()
        mock_session.get.side_effect = fake_get
        mock_session_class.return_value = mock_session
        client = _make_client()
        client.session = mock_session

        result = client.get_company_profiles(["AAPL"])
        assert result["AAPL"]["mktCap"] == 5
        assert any("/api/v3/profile/" in c[0][0] for c in mock_session.get.call_args_list)
