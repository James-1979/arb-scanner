from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from arbscanner.adapters import BetfairDelayedAdapter


def test_betfair_rpc_error_keeps_aping_code_and_details():
    err = {
        "message": "ANGX-0001",
        "data": {
            "APINGException": {
                "errorCode": "TOO_MUCH_DATA",
                "errorDetails": "Request exceeds market data request limit",
            },
            "exceptionname": "APINGException",
        },
    }
    text = BetfairDelayedAdapter._rpc_error_text(err)
    assert "ANGX-0001" in text
    assert "TOO_MUCH_DATA" in text
    assert "market data request limit" in text


class RecordingBetfair(BetfairDelayedAdapter):
    def __init__(self):
        super().__init__(app_key="key", session_token="token", enabled_sports=["Football"])
        self.calls = []

    async def list_event_types(self):
        return {"1": "Football"}

    async def _rpc(self, method, params, rpc_id=1):
        self.calls.append((method, params, rpc_id))
        return [], 1


def test_catalogue_market_description_stays_within_200_point_limit():
    adapter = RecordingBetfair()
    rows = asyncio.run(adapter.list_catalogue(24))
    assert rows == []
    catalogue_calls = [params for method, params, _ in adapter.calls if method == "listMarketCatalogue"]
    assert catalogue_calls
    assert all(int(p["maxResults"]) <= 200 for p in catalogue_calls)
    assert all(len(p["filter"]["marketTypeCodes"]) == 1 for p in catalogue_calls)


class SplittingBetfair(BetfairDelayedAdapter):
    def __init__(self):
        super().__init__(app_key="key", session_token="token", enabled_sports=["Football"])
        self.calls = []

    async def _rpc(self, method, params, rpc_id=1):
        self.calls.append(params)
        start = datetime.fromisoformat(params["filter"]["marketStartTime"]["from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(params["filter"]["marketStartTime"]["to"].replace("Z", "+00:00"))
        # Force one split for windows wider than an hour, then return unique rows.
        if end - start > timedelta(hours=1):
            return [{"marketId": f"full-{i}"} for i in range(200)], 1
        return [{"marketId": f"{start.timestamp()}-{end.timestamp()}"}], 1


def test_full_catalogue_window_is_split_instead_of_silently_truncated():
    adapter = SplittingBetfair()
    start = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    end = start + timedelta(hours=2)
    rows = asyncio.run(adapter._catalogue_window("1", "Football", "MATCH_ODDS", start, end))
    assert len(adapter.calls) == 3
    assert len(rows) == 2
    assert all(int(call["maxResults"]) == 200 for call in adapter.calls)
