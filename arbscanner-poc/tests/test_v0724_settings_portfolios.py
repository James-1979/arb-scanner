from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB


def test_upgrade_copies_legacy_settings_into_both_streams(tmp_path):
    path = tmp_path / "legacy-config.sqlite3"
    db = DB(path)
    db.set_setting("config", {
        "monitor_betfair_starting_balance": 410.0,
        "monitor_matchbook_starting_balance": 290.0,
        "minimum_liquidity": 7.0,
        "minimum_net_roi_pct": 1.7,
        "minimum_profit": 2.25,
        "execution_max_stake": 88.0,
        "max_event_exposure_pct": 44.0,
        "execution_max_slippage_pct": 0.8,
        "execution_max_unhedged_exposure": 17.0,
        "execution_hedge_reserve_pct": 23.0,
    })
    db.conn.close()

    api = API(path)
    cfg = api.db.get_setting("config", {})
    for stream in ("pre_match", "inplay"):
        assert cfg[f"{stream}_minimum_liquidity"] == 7.0
        assert cfg[f"{stream}_minimum_net_roi_pct"] == 1.7
        assert cfg[f"{stream}_minimum_profit"] == 2.25
        assert cfg[f"{stream}_execution_max_stake"] == 88.0
        assert cfg[f"{stream}_max_event_exposure_pct"] == 44.0
        assert cfg[f"{stream}_execution_max_unhedged_exposure"] == 17.0
        assert cfg[f"{stream}_execution_hedge_reserve_pct"] == 23.0
    assert cfg["pre_match_execution_max_slippage_pct"] == 0.8
    assert cfg["pre_match_monitor_betfair_starting_balance"] == 410.0
    assert cfg["pre_match_monitor_matchbook_starting_balance"] == 290.0
    assert cfg["inplay_monitor_betfair_starting_balance"] == 410.0
    assert cfg["inplay_monitor_matchbook_starting_balance"] == 290.0


def test_stream_starting_balance_setting_does_not_mutate_wallet_until_reset(tmp_path):
    api = API(tmp_path / "settings-wallet.sqlite3")
    before = api.dashboard_overview({})["wallets_by_stream"]
    assert before["pre_match"]["betfair"]["opening_balance"] == 250.0

    result = api.save_settings({"config": {
        "pre_match_monitor_betfair_starting_balance": 999.0,
        "pre_match_monitor_matchbook_starting_balance": 777.0,
    }})
    assert result["version"] == "0.9.36"
    still = api.dashboard_overview({})["wallets_by_stream"]
    assert still["pre_match"]["betfair"]["opening_balance"] == 250.0
    assert still["pre_match"]["matchbook"]["opening_balance"] == 250.0

    reset = api.reset_monitor_balances({"stream": "pre_match"})
    assert reset["ok"] is True
    after = reset["wallets"]
    assert after["pre_match"]["betfair"]["opening_balance"] == 999.0
    assert after["pre_match"]["matchbook"]["opening_balance"] == 777.0
    assert after["in_play"]["betfair"]["opening_balance"] == 250.0
    assert after["in_play"]["matchbook"]["opening_balance"] == 250.0


def test_independent_stream_resets_preserve_other_portfolio(tmp_path):
    api = API(tmp_path / "independent-reset.sqlite3")
    first = api.reset_monitor_balances({
        "stream": "pre_match",
        "balances": {"betfair": 400.0, "matchbook": 300.0},
    })
    assert first["ok"] is True
    assert first["wallets"]["pre_match"]["betfair"]["opening_balance"] == 400.0
    assert first["wallets"]["in_play"]["betfair"]["opening_balance"] == 250.0

    second = api.reset_monitor_balances({
        "stream": "in_play",
        "balances": {"betfair": 600.0, "matchbook": 500.0},
    })
    assert second["ok"] is True
    assert second["wallets"]["in_play"]["betfair"]["opening_balance"] == 600.0
    assert second["wallets"]["in_play"]["matchbook"]["opening_balance"] == 500.0
    assert second["wallets"]["pre_match"]["betfair"]["opening_balance"] == 400.0
    assert second["wallets"]["pre_match"]["matchbook"]["opening_balance"] == 300.0


def test_stream_hedge_reserves_are_independent(tmp_path):
    api = API(tmp_path / "reserve.sqlite3")
    saved = api.save_settings({"config": {
        "pre_match_execution_hedge_reserve_pct": 10.0,
        "inplay_execution_hedge_reserve_pct": 30.0,
    }})
    assert saved["version"] == "0.9.36"
    wallets = api.dashboard_overview({})["wallets_by_stream"]
    assert wallets["pre_match"]["betfair"]["free_for_normal"] == 225.0
    assert wallets["in_play"]["betfair"]["free_for_normal"] == 175.0


def test_frontend_has_separate_portfolios_and_strategy_cards():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    for element_id in (
        "preMatchBfStart", "preMatchMbStart", "inPlayBfStart", "inPlayMbStart",
        "preMinLiquidity", "preMinRoi", "preMinProfit", "preMaxStake", "preMaxExposure",
        "preSlippage", "preHedgeReserve", "preUnhedged",
        "ipMinLiquidity", "ipMinRoi", "ipMinProfit", "ipMaxStake", "ipMaxExposure",
        "inplaySlippage", "ipHedgeReserve", "ipUnhedged", "inplayCooldown",
    ):
        assert f'id="{element_id}"' in html
    assert "Simulation assumptions — not exchange settings." in html
    assert "resetMonitorBalances('pre_match')" in html
    assert "resetMonitorBalances('in_play')" in html
    assert "resetMonitorBalances('all')" in html


def test_frontend_sport_setting_hydration_uses_real_config_keys():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "sportFootball:'sport_football_enabled'" in html
    assert "sportDarts:'sport_darts_enabled'" in html
    assert "sportAmericanFootball:'sport_american_football_enabled'" in html
    assert "sportIceHockey:'sport_ice_hockey_enabled'" in html
    assert "sport__football_enabled" not in html
