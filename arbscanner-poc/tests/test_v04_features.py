from pathlib import Path
from arbscanner.adapters import BetfairDelayedAdapter
from arbscanner.alerts import qualifies_for_alert
from arbscanner.db import DB
from arbscanner.models import ExchangeMarket, Quote
from arbscanner.normalization import match_markets, align_quotes
from arbscanner.quality import quality_profile
from arbscanner.engine import simulate_equal_return
from arbscanner.models import Leg, Scenario


def q(ex, mid, event, market, sel, sid, odds, liq):
    return Quote(ex,"e",mid,event,market,sid,sel,odds,liq,"2026-08-09T12:00:00+00:00",start_time="2026-08-09T18:00:00+00:00")


def test_betfair_effective_commission_uses_api_base_and_discount_when_allowed():
    pct, src = BetfairDelayedAdapter._effective_commission(
        {"description":{"marketBaseRate":5.0,"discountAllowed":True}}, 20.0, 2.0
    )
    assert pct == 4.0
    assert "marketBaseRate" in src
    assert "discount" in src

    pct_no_discount, src_no_discount = BetfairDelayedAdapter._effective_commission(
        {"description":{"marketBaseRate":5.0,"discountAllowed":False}}, 20.0, 2.0
    )
    assert pct_no_discount == 5.0
    assert "not allowed" in src_no_discount

    pct2, src2 = BetfairDelayedAdapter._effective_commission({}, 20.0, 2.0)
    assert pct2 == 2.0
    assert "fallback" in src2


def test_two_way_markets_match_and_align():
    a=ExchangeMarket("Matchbook","e1","m1","A v B","Over/Under 2.5 Goals","2026-08-09T18:00:00+00:00",[
        q("Matchbook","m1","A v B","Over/Under 2.5 Goals","Over 2.5","1",2.1,100),
        q("Matchbook","m1","A v B","Over/Under 2.5 Goals","Under 2.5","2",1.9,100)])
    b=ExchangeMarket("Betfair delayed","e2","m2","A v B","Over/Under 2.5 Goals","2026-08-09T18:00:00+00:00",[
        q("Betfair delayed","m2","A v B","Over/Under 2.5 Goals","Over 2.5 Goals","3",2.05,100),
        q("Betfair delayed","m2","A v B","Over/Under 2.5 Goals","Under 2.5 Goals","4",1.95,100)])
    ms=match_markets([a,b],0.7)
    assert len(ms)==1
    assert ms[0].strategy=="two-way"
    groups=align_quotes(ms[0])
    assert len(groups)==2


def test_alert_requires_quality_and_capacity():
    cfg={"alerts_enabled":True,"alert_quality_bands":["Strong","Excellent"],"alert_min_deployed_roi_pct":0.75,
         "alert_min_bankroll_roi_pct":0.2,"alert_min_capital_used_pct":20,"alert_min_profit":1.0}
    good={"quality_band":"Strong","deployed_roi_pct":1.0,"bankroll_roi_pct":0.4,"capital_used_pct":40,"expected_profit":2.0}
    tiny={**good,"capital_used_pct":1,"expected_profit":0.05}
    assert qualifies_for_alert(good,cfg)[0]
    assert not qualifies_for_alert(tiny,cfg)[0]


def test_track_persistence_and_close(tmp_path: Path):
    db=DB(tmp_path/"db.sqlite3")
    sid=db.start_scan()
    db.upsert_track("t",sid,"e","Event","Match Odds","1x2",1.0,.2,100,2,65,"Strong",500,"recommended","ok")
    sid2=db.start_scan()
    db.upsert_track("t",sid2,"e","Event","Match Odds","1x2",1.2,.3,120,3,80,"Excellent",500,"recommended","better")
    row=db.track_for("e","Match Odds")
    assert row["scan_count"]==2
    assert row["peak_quality_band"]=="Excellent"
    db.close_tracks_not_seen(sid2,set())
    assert db.track_for("e","Match Odds")["closed_at"] is not None


def test_quality_penalizes_tiny_capacity():
    legs=[Leg("A","x",2.1,0.01),Leg("B","y",2.1,100)]
    sim=simulate_equal_return(legs,Scenario("s",500))
    p=quality_profile(sim,1.0,500)
    assert p["capital_used_pct"] < 1
    assert p["quality_band"] in {"Tiny","Invalid"}


def test_diagnostic_exposes_gross_and_commission_impact():
    legs=[Leg("A","home",2.1,100,commission_pct=2.0),Leg("B","away",2.1,100,commission_pct=2.0)]
    d=__import__("arbscanner.engine",fromlist=["diagnose_equal_return"]).diagnose_equal_return(legs,100)
    assert d["valid"]
    assert d["gross_roi_pct"] > d["expected_roi_pct"]
    assert d["commission_impact_pct"] > 0
