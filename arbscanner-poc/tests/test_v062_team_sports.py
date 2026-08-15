import asyncio

from arbscanner.api import API
from arbscanner.adapters import BetfairDelayedAdapter
from arbscanner.normalization import classify_market
from arbscanner.sports import SUPPORTED_SPORTS, is_allowed_market_shape, normalize_sport


def test_new_team_sports_supported_and_normalized():
    expected = {"Rugby Union", "Rugby League", "Volleyball", "Handball", "Australian Rules", "Field Hockey"}
    assert expected.issubset(set(SUPPORTED_SPORTS))
    assert normalize_sport("AFL") == "Australian Rules"
    assert normalize_sport("hockey") == "Field Hockey"
    assert normalize_sport("ice-hockey") == "Ice Hockey"


def test_three_way_team_match_odds_are_conservative_and_explicit():
    assert classify_market("Match Odds", 3, "Rugby Union") == ("match odds", "1x2")
    assert classify_market("Full Time Result", 3, "Handball") == ("match odds", "1x2")
    assert is_allowed_market_shape("Field Hockey", "match odds", "1x2")
    assert classify_market("Match Odds", 3, "Volleyball")[1] == "unknown"


def test_new_sports_default_enabled(tmp_path):
    api = API(tmp_path / "db.sqlite3")
    cfg = api.get_state()["settings"]["config"]
    for key in (
        "sport_rugby_union_enabled", "sport_rugby_league_enabled",
        "sport_volleyball_enabled", "sport_handball_enabled",
        "sport_australian_rules_enabled", "sport_field_hockey_enabled",
    ):
        assert cfg[key] is True


class EventTypeProbe(BetfairDelayedAdapter):
    def __init__(self):
        super().__init__("key", "token", enabled_sports=list(SUPPORTED_SPORTS))

    async def _rpc(self, method, params, rpc_id=1):
        assert method == "listEventTypes"
        return ([
            {"eventType": {"id": "10", "name": "Rugby Union"}},
            {"eventType": {"id": "11", "name": "Hockey"}},
            {"eventType": {"id": "12", "name": "Volleyball"}},
            {"eventType": {"id": "99", "name": "Horse Racing"}},
        ], 1)


def test_betfair_event_type_discovery_maps_new_team_sports():
    found = asyncio.run(EventTypeProbe().list_event_types())
    assert found == {"10": "Rugby Union", "11": "Field Hockey", "12": "Volleyball"}
