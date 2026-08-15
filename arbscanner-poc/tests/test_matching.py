from arbscanner.models import ExchangeMarket, Quote
from arbscanner.normalization import align_quotes, match_markets


def q(ex, eid, mid, event, market, sid, selection, odds, start):
    return Quote(ex, eid, mid, event, market, sid, selection, odds, 100.0, "2026-08-09T12:00:00+00:00", start, 2.0)


def test_cross_exchange_event_and_runner_alignment():
    start1 = "2026-08-10T15:00:00Z"
    start2 = "2026-08-10T15:02:00Z"
    a = ExchangeMarket("Betfair delayed", "1", "bf1", "Manchester United v Arsenal", "Match Odds", start1, [
        q("Betfair delayed", "1", "bf1", "Manchester United v Arsenal", "Match Odds", "11", "Manchester United", 2.5, start1),
        q("Betfair delayed", "1", "bf1", "Manchester United v Arsenal", "Match Odds", "12", "The Draw", 3.4, start1),
        q("Betfair delayed", "1", "bf1", "Manchester United v Arsenal", "Match Odds", "13", "Arsenal", 3.0, start1),
    ])
    b = ExchangeMarket("Matchbook", "2", "mb1", "Man Utd vs Arsenal", "1X2", start2, [
        q("Matchbook", "2", "mb1", "Man Utd vs Arsenal", "1X2", "21", "Man Utd", 2.55, start2),
        q("Matchbook", "2", "mb1", "Man Utd vs Arsenal", "1X2", "22", "Draw", 3.5, start2),
        q("Matchbook", "2", "mb1", "Man Utd vs Arsenal", "1X2", "23", "Arsenal FC", 2.95, start2),
    ])
    matches = match_markets([a, b], threshold=0.55)
    assert len(matches) == 1
    groups = align_quotes(matches[0], threshold=0.5)
    assert len(groups) == 3
    assert all(len(v) == 2 for v in groups.values())
