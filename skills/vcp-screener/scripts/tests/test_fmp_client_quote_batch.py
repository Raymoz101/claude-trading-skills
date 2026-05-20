"""FMP /stable migration: per-symbol quotes + sp500-constituent endpoint.

/stable/quote does not support comma-batched symbols (silently returns []),
so get_batch_quotes must issue one request per symbol. The S&P 500 constituent
list also moved from v3 /sp500_constituent to /stable/sp500-constituent.
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


def _mock_response(status_code, json_payload):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_payload
    resp.text = ""
    return resp


class TestBatchQuotesPerSymbol:
    @patch("fmp_client.requests.Session")
    def test_no_comma_batched_symbol_param(self, mock_session_class):
        """Each symbol is fetched individually; no comma-joined symbol value."""

        def fake_get(url, params=None, timeout=None):
            sym = (params or {}).get("symbol", "")
            return _mock_response(200, [{"symbol": sym, "price": 100.0}])

        mock_session = MagicMock()
        mock_session.get.side_effect = fake_get
        mock_session_class.return_value = mock_session

        client = _make_client()
        client.session = mock_session

        result = client.get_batch_quotes(["AAPL", "MSFT", "NVDA"])

        assert set(result) == {"AAPL", "MSFT", "NVDA"}
        # Every quote request used a single, comma-free symbol value.
        for call in mock_session.get.call_args_list:
            symbol_param = call[1]["params"].get("symbol", "")
            assert "," not in symbol_param, f"comma-batched call: {symbol_param!r}"
        # One request per symbol (3), all to the stable quote endpoint.
        assert mock_session.get.call_count == 3
        assert all("/stable/quote" in c[0][0] for c in mock_session.get.call_args_list)


class TestSP500ConstituentEndpoint:
    @patch("fmp_client.requests.Session")
    def test_uses_stable_constituent_first(self, mock_session_class):
        mock_session = MagicMock()
        mock_session.get.return_value = _mock_response(
            200, [{"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology"}]
        )
        mock_session_class.return_value = mock_session

        client = _make_client()
        client.session = mock_session

        result = client.get_sp500_constituents()
        assert result and result[0]["symbol"] == "AAPL"

        first_url = mock_session.get.call_args_list[0][0][0]
        assert first_url.endswith("/stable/sp500-constituent")
