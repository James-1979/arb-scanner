from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API, DEFAULT_CONFIG
from arbscanner.db import DB
from arbscanner.lifecycle import event_phase
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.normalization import classify_market, match_markets
from arbscanner.sports import SUPPORTED_SPORTS, normalize_sport


def q(exchange, event, market, selection, sid, sport, start, odds=2.1):
    return Quote(
        exchange=exchange, event_id=f"{exchange}-{event}", market_id=f"{exchange}-{market}",
        event_name=event, market_name=market, selection_id=sid, selection=selection,
        odds=odds, liquidity=100.0, captured_at=datetime.now(timezone.utc).isoformat(),
        start_time=start, sport=sport, market_type="match winner", strategy="two-way",
    )


def market(exchange, event, sport, start):
    return ExchangeMarket(
        exchange=exchange, event_id=f"{exchange}-e", market_id=f"{exchange}-m",
        event_name=event, market_name="Match Winner", start_time=start,
        quotes=[q(exchange,event,"Match Winner","Player A","1",sport,start,2.05),
                q(exchange,event,"Match Winner","Player B","2",sport,start,1.98)],
        sport=sport, market_type="match winner", strategy="two-way", status="OPEN", in_play=False,
    )


def test_supported_sports_and_aliases():
    assert "American Football" in SUPPORTED_SPORTS
    assert normalize_sport("soccer") == "Football"
    assert normalize_sport("ice-hockey") == "Ice Hockey"


def test_nonfootball_only_simple_two_runner_winner_market():
    assert classify_market("Moneyline", 2, "American Football") == ("match winner", "two-way")
    assert classify_market("Match Odds", 2, "Tennis") == ("match winner", "two-way")
    assert classify_market("Match Odds", 3, "Ice Hockey")[1] == "unknown"


def test_multisport_matching_requires_same_sport():
    start=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
    a=market("Matchbook","Aces v Bears","Tennis",start)
    b=market("Betfair delayed","Aces v Bears","Tennis",start)
    assert len(match_markets([a,b],threshold=.7)) == 1
    b.sport="American Football"
    assert match_markets([a,b],threshold=.7) == []


def test_event_phase_upcoming_live_historic():
    now=datetime(2026,8,9,14,0,tzinfo=timezone.utc)
    future=(now+timedelta(hours=2)).isoformat()
    past=(now-timedelta(hours=1)).isoformat()
    assert event_phase(future,"OPEN",False,now=now)["phase"] == "upcoming"
    assert event_phase(past,"OPEN",True,now=now)["phase"] == "live"
    assert event_phase(past,"CLOSED",False,now=now)["phase"] == "historic"


def test_v06_defaults_enable_sports_and_live_lookback(tmp_path: Path):
    api=API(tmp_path / "db.sqlite3")
    state=api.get_state()
    assert state["version"] == "0.9.36"
    cfg=state["settings"]["config"]
    assert cfg["sport_american_football_enabled"] is True
    assert cfg["live_lookback_hours"] == 8


def test_sport_coverage_aggregates_latest_scan(tmp_path: Path):
    db=DB(tmp_path / "db.sqlite3")
    sid=db.start_scan()
    db.add_matched_market(
        sid,"event","Team A v Team B",(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
        "Match Winner",.98,1.0,1.0,.2,.8,500,4,None,"recommended","ok",[],[],
        strategy="two-way",quality={"quality_score":80,"quality_band":"Strong","reference_bankroll":500,"bankroll_roi_pct":.8,"capital_used_pct":100},
        sport="American Football",in_play=False,event_status="OPEN",
    )
    db.finish_scan(sid, markets_seen=20, matches_seen=1, opportunities_found=1,
                   statuses=[{"exchange":"Matchbook","ok":True,"sport_counts":{"American Football":7}},
                             {"exchange":"Betfair delayed","ok":True,"sport_counts":{"American Football":9}}])
    cov=db.sport_coverage()
    row=next(x for x in cov["rows"] if x["sport"]=="American Football")
    assert row["markets_seen"] == 16
    assert row["matched"] == 1
    assert row["recommended"] == 1
