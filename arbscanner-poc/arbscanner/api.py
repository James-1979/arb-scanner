from __future__ import annotations
import asyncio
import base64
import csv
import json
import os
import re
import statistics
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from types import SimpleNamespace
from .adapters import BetfairDelayedAdapter, MatchbookAdapter
from .db import DB
from .demo import demo_opportunity
from .engine import arb_edge, diagnose_equal_return, simulate_equal_return, strategy_book_analysis
from .execution import build_execution_plan, stress_test_plan
from .models import Leg, Scenario
from .replay import ReplayHistoryLimitExceeded, prepare_replay_history, replay_analysis, replay_scenarios
from .quality import assess_data_quality, beginner_explanation, history_summary, quality_profile
from .scanner import Scanner
from .secrets import SecretStore
from .service import LaunchAgentManager
from .sports import SUPPORTED_SPORTS, SUPPORTED_RACING
from .racing import normalize_runner_name, runner_match_score
from .lifecycle import event_phase
from .alerts import qualifies_for_alert, send_macos_notification_diagnostic
from .live_providers import LiveProviderRegistry
from .venues import DEFAULT_PROVIDER_REGISTRY, provider_id_for_name
from .modes import canonical_mode_value, canonical_execution_mode, ExecutionMode, FeedEntitlement
from .provider_runtime import default_provider_runtime_registry
from .contracts import SERVICE_BOUNDARY_MANIFEST
from .analytics_store import AnalyticsStore
from .financial_projection import authoritative_account_totals, portfolio_streams, project_portfolio_financial_state
from .market_analytics import (HEATMAP_METRICS, MarketFilters, heatmap_metric_ownership, live_heatmap_cell, live_market_row, market_row_matches)
from .operator_projection import (engine_catalog_row, engine_catalog_visible, merge_engine_lifecycle_groups, operator_domain, project_engine_lifecycle)
from .strategy_engines import EngineRuntime, EngineRegistry, MarketEvidence, ENGINE_LIFECYCLES, stable_hash
from .engine_packages import (inspect_package_bytes, install_package_bytes, build_export_package, downloads_dir,
    quarantine_package_bytes, read_quarantined_package, remove_quarantined_package, load_reviewed_engine_class)
from .archive import (
    archive_continuity, archive_prune_dry_run, default_archive_root, duckdb_runtime_status, load_runtime_gate_report, load_runtime_state,
    manifest_verified, newest_closed_hour, next_pilot_archive_hour, runtime_blocked_until, runtime_gate_passed as archive_runtime_gate_passed,
)

APP_DIR = Path.home() / "Library" / "Application Support" / "ArbScanner" if os.name != "nt" else Path.home() / "AppData" / "Local" / "ArbScanner"


from .config import DEFAULT_CONFIG, OPERATING_MODES
from .racing_projection import racing_book_analysis_from_sources

# Backward-compatible private import retained for historical tests/callers.
_racing_book_analysis_from_sources = racing_book_analysis_from_sources


class API:
    def __init__(self, db_path: Path | None = None):
        self.db = DB(db_path or (APP_DIR / "arbscanner.sqlite3"))
        self.analytics_store = AnalyticsStore(self.db, default_archive_root(self.db.path))
        self.secrets = SecretStore()
        self.provider_runtime = default_provider_runtime_registry()
        self.scanner = Scanner(self.db, self.secrets, producer="app", provider_runtime=self.provider_runtime)
        self.service = LaunchAgentManager()
        # v0.8.44: LIVE UI/data has a dedicated provider boundary. The registry is
        # intentionally stubbed until real LIVE exchange integrations are added;
        # it must never fall back to SIM scanner caches or virtual ledgers.
        self.live_providers = LiveProviderRegistry(self.provider_runtime, self.db, self.secrets)
        if self.db.get_setting("scenarios") is None:
            self.db.set_setting("scenarios", [500, 1000, 5000, 10000, 25000])
        # 0.9.0 exposes only canonical SIM/LIVE economic modes. Historical
        # MONITOR/WATCH/PAPER/MONITOR_TIMING values migrate safely to SIM on startup.
        stored_mode = canonical_mode_value(self.db.get_setting("mode", "sim"))
        if stored_mode not in OPERATING_MODES or not OPERATING_MODES[stored_mode]["available"]:
            stored_mode = "sim"
        self.db.set_setting("mode", stored_mode)
        # 0.9.8 separates the global data/presentation context from economic
        # execution mode. LIVE context can drive read-only decision evidence while
        # the economic execution mode remains SIM/locked.
        if self.db.get_setting("data_context_mode") is None:
            self.db.set_setting("data_context_mode", stored_mode)
        else:
            self.db.set_setting("data_context_mode", canonical_mode_value(self.db.get_setting("data_context_mode", stored_mode)))
        cfg = self.db.get_setting("config")
        merged_cfg = DEFAULT_CONFIG.copy() if cfg is None else {**DEFAULT_CONFIG, **cfg}
        # v0.7.19 splits slow discovery from fast cached-market price refreshes.
        # Preserve an existing custom scanner interval as the discovery cadence on first upgrade.
        if cfg is not None and "discovery_interval_seconds" not in cfg:
            merged_cfg["discovery_interval_seconds"] = int(float(cfg.get("scan_interval_seconds", 60) or 60))
        # v0.7.5 returns to a continuous scanner model.  WATCH is implicit: the
        # worker keeps scanning and storing opportunities; MONITOR_TIMING/LIVE only
        # change what happens after a qualifying opportunity is found.
        if self.db.get_setting("v075_continuous_modes_migrated") is None:
            old_job = self.db.active_job()
            if old_job:
                try:
                    self.db.finish_job(int(old_job["id"]), "stopped", "migrated to continuous mode")
                except Exception:
                    pass
            merged_cfg["scanner_enabled"] = True
            self.db.set_setting("v075_continuous_modes_migrated", True)
        # v0.6.6 raises the research floor from the old 0.5% default to 1.0%.
        # Apply this once to existing installs that are still on the old default,
        # while preserving any deliberately higher threshold. Future user edits
        # remain editable because the migration flag prevents repeated overrides.
        if self.db.get_setting("v066_roi_floor_migrated") is None:
            try:
                if float(merged_cfg.get("minimum_net_roi_pct", 0.5)) <= 0.5:
                    merged_cfg["minimum_net_roi_pct"] = 1.0
            except (TypeError, ValueError):
                merged_cfg["minimum_net_roi_pct"] = 1.0
            self.db.set_setting("v066_roi_floor_migrated", True)
        # v0.7.11: max-stake capping and delayed-feed quality penalties made the
        # old default alert rules effectively impossible to satisfy (Strong/
        # Excellent + 20% capital use on a £500 reference bankroll with a £25
        # max stake). Migrate only installations still carrying those old
        # defaults; explicit custom alert settings are left alone.
        if self.db.get_setting("v0710_alert_rules_migrated") is None:
            old_bands = list(merged_cfg.get("alert_quality_bands") or [])
            if set(old_bands) == {"Strong", "Excellent"}:
                merged_cfg["alert_quality_bands"] = ["Usable", "Strong", "Excellent"]
            try:
                if abs(float(merged_cfg.get("alert_min_capital_used_pct", 20.0)) - 20.0) < 1e-9:
                    merged_cfg["alert_min_capital_used_pct"] = 0.0
                if abs(float(merged_cfg.get("alert_min_bankroll_roi_pct", 0.20)) - 0.20) < 1e-9:
                    merged_cfg["alert_min_bankroll_roi_pct"] = 0.0
            except (TypeError, ValueError):
                pass
            self.db.set_setting("v0710_alert_rules_migrated", True)

        # v0.7.24: split the old shared Monitor settings into independent
        # pre-match and in-play portfolios. Copy the user's current values once so
        # upgrading does not silently change strategy behaviour or bankroll size.
        if self.db.get_setting("v0724_stream_settings_migrated") is None:
            legacy_map = {
                "pre_match_monitor_betfair_starting_balance": "monitor_betfair_starting_balance",
                "pre_match_monitor_matchbook_starting_balance": "monitor_matchbook_starting_balance",
                "inplay_monitor_betfair_starting_balance": "monitor_betfair_starting_balance",
                "inplay_monitor_matchbook_starting_balance": "monitor_matchbook_starting_balance",
                "pre_match_minimum_liquidity": "minimum_liquidity",
                "pre_match_minimum_net_roi_pct": "minimum_net_roi_pct",
                "pre_match_minimum_profit": "minimum_profit",
                "pre_match_execution_max_stake": "execution_max_stake",
                "pre_match_max_event_exposure_pct": "max_event_exposure_pct",
                "pre_match_execution_max_slippage_pct": "execution_max_slippage_pct",
                "pre_match_execution_max_unhedged_exposure": "execution_max_unhedged_exposure",
                "pre_match_execution_hedge_reserve_pct": "execution_hedge_reserve_pct",
                "inplay_minimum_liquidity": "minimum_liquidity",
                "inplay_minimum_net_roi_pct": "minimum_net_roi_pct",
                "inplay_minimum_profit": "minimum_profit",
                "inplay_execution_max_stake": "execution_max_stake",
                "inplay_max_event_exposure_pct": "max_event_exposure_pct",
                "inplay_execution_max_unhedged_exposure": "execution_max_unhedged_exposure",
                "inplay_execution_hedge_reserve_pct": "execution_hedge_reserve_pct",
            }
            raw_cfg = cfg or {}
            for new_key, old_key in legacy_map.items():
                if new_key not in raw_cfg:
                    merged_cfg[new_key] = merged_cfg.get(old_key, DEFAULT_CONFIG[new_key])
            self.db.set_setting("v0724_stream_settings_migrated", True)

        stored_username = self.secrets.get("matchbook_username")
        if stored_username and not str(merged_cfg.get("matchbook_username") or "").strip():
            merged_cfg["matchbook_username"] = stored_username
        self.db.set_setting("config", merged_cfg)
        self.db.ensure_monitor_streams(
            self._monitor_starting_balances(merged_cfg, "pre_match"),
            self._monitor_starting_balances(merged_cfg, "in_play"),
            self._monitor_starting_balances(merged_cfg, "racing"),
        )
        # v0.8.30: establish an auditable opening account checkpoint for every
        # application process. This is additive history only; it does not mutate
        # wallet balances or change execution behaviour.
        try:
            self._monitor_account_state(merged_cfg, capture=True, context="startup")
        except Exception:
            # Account audit must never prevent the scanner UI from starting.
            pass

    @staticmethod
    def _monitor_starting_balances(cfg: dict, stream: str) -> dict[str, float]:
        """Return explicit SIM capital for a stream without redistributing bankroll.

        Betfair/Matchbook retain their legacy configuration keys. Future providers
        are opt-in through ``sim_provider_starting_balances`` and therefore start
        at zero unless an amount is deliberately configured.
        """
        stream = str(stream or "pre_match")
        if stream == "in_play":
            bf_key, mb_key = "inplay_monitor_betfair_starting_balance", "inplay_monitor_matchbook_starting_balance"
        elif stream == "racing":
            bf_key, mb_key = "racing_monitor_betfair_starting_balance", "racing_monitor_matchbook_starting_balance"
        else:
            bf_key, mb_key = "pre_match_monitor_betfair_starting_balance", "pre_match_monitor_matchbook_starting_balance"
        balances = {
            "betfair": max(0.0, float(cfg.get(bf_key, cfg.get("monitor_betfair_starting_balance", 250.0)) or 0.0)),
            "matchbook": max(0.0, float(cfg.get(mb_key, cfg.get("monitor_matchbook_starting_balance", 250.0)) or 0.0)),
        }
        extra = cfg.get("sim_provider_starting_balances") or {}
        if isinstance(extra, dict):
            stream_extra = extra.get(stream) if isinstance(extra.get(stream), dict) else {}
            for provider_id, raw in stream_extra.items():
                pid = str(provider_id or "").strip().lower()
                if not pid or pid in balances:
                    continue
                try:
                    balances[pid] = max(0.0, float(raw or 0.0))
                except (TypeError, ValueError):
                    balances[pid] = 0.0
        return balances

    @staticmethod
    def _monitor_reserve_pct(cfg: dict, stream: str) -> float:
        stream = str(stream or "pre_match")
        if stream == "in_play":
            key = "inplay_execution_hedge_reserve_pct"
        elif stream == "racing":
            key = "racing_execution_hedge_reserve_pct"
        else:
            key = "pre_match_execution_hedge_reserve_pct"
        return min(100.0, max(0.0, float(cfg.get(key, cfg.get("execution_hedge_reserve_pct", 20.0)) or 0.0)))

    @staticmethod
    def _parse_utc_dt(value):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _execution_value_metrics(row: dict | None) -> dict:
        """Derive placed-position economics from actual modelled fills.

        `locked_profit` is only populated when the post-hedge position is balanced.
        It intentionally survives settlement because it is derived from the stored
        execution snapshot rather than the eventual winning outcome.
        """
        row = row or {}
        details = row.get("details") or {}
        result = details.get("execution_result") or {}
        after = result.get("after_hedge") or details.get("after_hedge") or {}
        deployed = max(0.0, float(row.get("deployed") or 0.0))
        execution_state = str(result.get("state") or details.get("execution_state") or row.get("state") or "").upper()
        row_state = str(row.get("state") or "").upper()

        worst = after.get("worst_case_pnl")
        best = after.get("best_case_pnl")
        balanced = after.get("balanced")
        if worst is None and result.get("captured_profit") is not None:
            worst = result.get("captured_profit")

        # Compatibility for older open modeled executions that stored only the
        # captured worst-case value on execution_runs. Never use the settled
        # captured value for this fallback because settlement is outcome-specific.
        if worst is None and row.get("captured_profit") is not None and "SETTLED" not in row_state and "EXPOSED" not in row_state:
            worst = row.get("captured_profit")
            if balanced is None and ("OPEN" in row_state or execution_state in {"COMPLETE", "HEDGED"}):
                balanced = True

        guaranteed = bool(balanced) and execution_state not in {"PANIC", "FAILED"} and "EXPOSED" not in row_state
        locked_profit = float(worst) if guaranteed and worst is not None else None
        worst_case_pnl = float(worst) if worst is not None else None
        best_case_pnl = float(best) if best is not None else None
        locked_return = (locked_profit / deployed) * 100.0 if locked_profit is not None and deployed > 0 else None
        worst_case_return = (worst_case_pnl / deployed) * 100.0 if worst_case_pnl is not None and deployed > 0 else None
        settled = bool(row.get("outcome")) or "SETTLED" in row_state
        final_pnl = float(row.get("captured_profit") or 0.0) if settled and row.get("captured_profit") is not None else None
        return {
            "locked_profit": None if locked_profit is None else round(locked_profit, 4),
            "locked_return_pct": None if locked_return is None else round(locked_return, 6),
            "locked_is_guaranteed": bool(guaranteed and locked_profit is not None),
            "worst_case_pnl": None if worst_case_pnl is None else round(worst_case_pnl, 4),
            "worst_case_return_pct": None if worst_case_return is None else round(worst_case_return, 6),
            "best_case_profit": None if best_case_pnl is None else round(best_case_pnl, 4),
            "final_pnl": None if final_pnl is None else round(final_pnl, 4),
        }

    @staticmethod
    def _settled_commission_audit(row: dict | None) -> dict:
        """Reconcile gross fill P&L, venue commission and recorded net settlement.

        This is deliberately derived from the fills that actually formed the paper
        position.  It is therefore useful for historical Sports positions created
        before the commission-aware stake solver existed: we can prove whether a
        clean gross-positive position became negative only after commission without
        rewriting historical records.
        """
        row = row or {}
        details = row.get("details") or {}
        result = details.get("execution_result") or {}
        fills = list(result.get("fills") or [])
        outcome = str(row.get("outcome") or "").strip()
        if not fills or not outcome:
            return {"available": False}

        gross_by_exchange: dict[str, float] = {}
        rates: dict[str, float] = {}
        for fill in fills:
            exchange = str(fill.get("exchange") or "Unknown")
            stake = max(0.0, float(fill.get("stake") or 0.0))
            odds = max(1.0, float(fill.get("odds") or 1.0))
            winner = str(fill.get("selection") or "").strip().lower() == outcome.lower()
            side = str(fill.get("side") or "BACK").upper()
            if side == "LAY":
                pnl = -stake * (odds - 1.0) if winner else stake
            else:
                pnl = stake * (odds - 1.0) if winner else -stake
            gross_by_exchange[exchange] = gross_by_exchange.get(exchange, 0.0) + pnl
            rates[exchange] = max(rates.get(exchange, 0.0), max(0.0, float(fill.get("commission_pct") or 0.0)) / 100.0)

        commission_by_exchange = {
            exchange: max(0.0, gross) * rates.get(exchange, 0.0)
            for exchange, gross in gross_by_exchange.items()
        }
        gross = sum(gross_by_exchange.values())
        commission = sum(commission_by_exchange.values())
        model_net = gross - commission
        recorded = row.get("final_pnl")
        if recorded is None:
            recorded = row.get("realized_pnl")
        if recorded is None and (row.get("settled_at") or "SETTLED" in str(row.get("state") or "").upper()):
            recorded = row.get("captured_profit")
        recorded_net = None if recorded is None else float(recorded)

        events = list(result.get("events") or [])
        event_states = {str(x.get("state") or "").upper() for x in events}
        hedge = any(bool(x.get("is_hedge")) for x in fills) or "EMERGENCY_HEDGE" in event_states
        disrupted = bool(event_states & {"LEG_FAILED", "LEG_PARTIAL", "PANIC"})
        planned = row.get("legs")
        if not isinstance(planned, list):
            try:
                planned = json.loads(row.get("legs_json") or "[]")
            except Exception:
                planned = []
        normal_fills = [x for x in fills if not bool(x.get("is_hedge"))]
        full_fill = not planned or len(normal_fills) == len(planned)
        clean = bool(full_fill and not hedge and not disrupted)
        net_for_classification = model_net if recorded_net is None else recorded_net
        return {
            "available": True,
            "clean": clean,
            "gross_pnl": round(gross, 4),
            "commission": round(commission, 4),
            "model_net_pnl": round(model_net, 4),
            "recorded_net_pnl": None if recorded_net is None else round(recorded_net, 4),
            "reconciliation_delta": None if recorded_net is None else round(recorded_net - model_net, 4),
            "gross_by_exchange": {k: round(v, 4) for k, v in gross_by_exchange.items()},
            "commission_by_exchange": {k: round(v, 4) for k, v in commission_by_exchange.items()},
            "commission_erosion": commission > 1e-9,
            "post_commission_negative": bool(clean and gross > 1e-9 and net_for_classification < -1e-9),
            "gross_positive": gross > 1e-9,
            "net_negative": net_for_classification < -1e-9,
        }

    def _engine_metadata_map(self) -> dict[str, dict]:
        out = {}
        for row in self.db.engine_instances():
            iid = str(row.get("engine_instance_id") or "")
            if iid:
                out[iid] = row
        return out

    def _attach_engine_provenance(self, row: dict, *, fallback_section: str | None = None) -> dict:
        """Attach human nickname without replacing immutable engine provenance."""
        iid = str(row.get("engine_instance_id") or "").strip()
        source = str(row.get("engine_provenance_source") or "").strip().lower()
        authoritative = source in {"runtime_origin", "execution_origin"}
        # Raw, pre-execution market evidence can still show the engine it is routed
        # toward. This is routing context only, never ownership/provenance.
        routed_only = "engine_provenance_source" not in row and not iid and bool(fallback_section)
        if routed_only:
            section = str(fallback_section or "").lower()
            if section == "racing" or str(row.get("sport") or "").lower() == "greyhounds":
                iid = "GREYHOUNDS_BASELINE_ARB_PRIMARY"
            elif section == "sports":
                iid = "SPORTS_BASELINE_ARB_PRIMARY"
        if routed_only and iid:
            engine = self.db.engine_instance(iid)
            if engine:
                row["engine_instance_id"] = iid
                row["engine_type"] = engine.get("engine_type")
                row["engine_nickname"] = str(engine.get("nickname") or engine.get("display_name") or iid)
                row["engine_display_name"] = str(engine.get("display_name") or row["engine_nickname"])
                row["engine_provenance_authoritative"] = False
                row["engine_provenance_role"] = "routing_only"
                row["engine_provenance_source"] = "routing_only"
                return row
        engine = self.db.engine_instance(iid) if iid and authoritative else None
        if engine and authoritative:
            row["engine_instance_id"] = engine.get("engine_instance_id")
            row["engine_type"] = engine.get("engine_type")
            row["engine_version"] = row.get("engine_version") or engine.get("engine_version")
            row["engine_config_version"] = row.get("engine_config_version") or engine.get("active_config_version")
            row["engine_nickname"] = str(engine.get("nickname") or engine.get("display_name") or iid)
            row["engine_display_name"] = str(engine.get("display_name") or row["engine_nickname"])
            row["engine_provenance_authoritative"] = True
        else:
            label = "Legacy / Unverified" if iid else "Legacy / Unattributed"
            row["engine_nickname"] = label
            row["engine_display_name"] = label
            row["engine_provenance_authoritative"] = False
        return row

    def _attach_venue_account(self, row: dict) -> dict:
        controls = {x["provider_id"]: x for x in self.db.venue_controls()}
        providers = []
        legs = row.get("legs") or []
        if not legs and row.get("legs_json"):
            try: legs = json.loads(row.get("legs_json") or "[]")
            except Exception: legs = []
        for leg in legs:
            key = str((leg or {}).get("provider_id") or (leg or {}).get("resolved_provider_id") or (leg or {}).get("exchange") or "").lower()
            if key.startswith("betfair"): key="betfair"
            elif key.startswith("matchbook"): key="matchbook"
            elif key.startswith("smarkets"): key="smarkets"
            if key and key not in providers: providers.append(key)
        stakes = row.get("stakes_by_exchange") or {}
        if not stakes and row.get("stakes_by_exchange_json"):
            try: stakes=json.loads(row.get("stakes_by_exchange_json") or "{}")
            except Exception: stakes={}
        for key0 in stakes:
            key=str(key0).lower()
            if key.startswith("betfair"): key="betfair"
            elif key.startswith("matchbook"): key="matchbook"
            elif key.startswith("smarkets"): key="smarkets"
            if key and key not in providers: providers.append(key)
        # Discovery/decision rows may carry venue identity without legs/stakes.
        # Keep account attribution generic and provider-driven rather than
        # requiring an execution record.
        if not providers:
            raw = str(row.get("provider_id") or row.get("exchange") or row.get("provider_pair") or "").lower()
            for token in ("betfair", "matchbook", "smarkets"):
                if token in raw and token not in providers:
                    providers.append(token)
        row["venue_ids"] = providers
        row["venue"] = " + ".join(p.title() for p in providers) if providers else "—"
        nicknames=[str(controls[p].get("account_nickname") or p.title()) for p in providers if p in controls]
        row["account"] = " + ".join(nicknames) if nicknames else "—"
        row["account_nicknames"] = nicknames
        row["mode"] = str(row.get("mode") or "sim").lower()
        return row

    @staticmethod
    def _provider_selected_mode_state(mode_name: str, control: dict, feed_state: str, sim_account_state: str, live_account_state: str) -> dict:
        """Selected-mode provider health. Opposite-mode capability is never a health input."""
        is_sim = str(mode_name).lower() == "sim"
        feed_expected = bool(control.get("sim_feed_enabled" if is_sim else "live_feed_enabled"))
        account_expected = bool(control.get("sim_account_enabled" if is_sim else "live_account_enabled"))
        expected = bool(feed_expected or account_expected)
        selected_feed_state = str(feed_state or "unknown").lower() if feed_expected else "disabled"
        selected_account_state = str(sim_account_state if is_sim else live_account_state or "unknown").lower() if account_expected else "disabled"
        component_states = ([selected_feed_state] if feed_expected else []) + ([selected_account_state] if account_expected else [])
        failure_states = {"error", "failed", "failure"}
        degraded_states = {"stale", "slow", "waiting", "unknown", "awaiting_api_access", "offline", "disabled", "unavailable", "disconnected"}
        ready_states = {"connected", "ready", "healthy", "ok", "active"}
        if not expected:
            overall = "disabled"
        elif any(x in failure_states for x in component_states):
            overall = "error"
        elif any(x in degraded_states for x in component_states):
            overall = "degraded"
        elif component_states and all(x in ready_states for x in component_states):
            overall = "ready"
        else:
            overall = "degraded"
        return {
            "expected": expected, "feed_expected": feed_expected, "feed_enabled": feed_expected, "feed_state": selected_feed_state,
            "account_expected": account_expected, "account_enabled": account_expected, "account_state": selected_account_state, "state": overall,
        }

    def _operational_status(self, selected_mode: str | None = None, *, feeds_only: bool = False) -> dict:
        selected_mode = str(selected_mode or "").strip().lower()
        if selected_mode not in {"sim", "live"}:
            selected_mode = None
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        health = self.db.scanner_health(include_storage=not feeds_only)
        background = self.service.status()
        last_price = health.get("last_price_scan") or health.get("last_scan") or {}
        last_price_ok = health.get("last_successful_price_scan") or health.get("last_successful_scan") or {}
        last_discovery = health.get("last_discovery_scan") or {}
        last_discovery_ok = health.get("last_successful_discovery_scan") or {}
        now = datetime.now(timezone.utc)

        def parse_statuses(row):
            try: return json.loads(row.get("status_json") or "[]")
            except Exception: return []
        price_statuses = parse_statuses(last_price)
        discovery_statuses = parse_statuses(last_discovery)

        # Feed readiness is provider-scoped. Price and discovery scans may cover
        # different venues, so selecting one whole status list can make a healthy
        # provider appear UNKNOWN merely because it was absent from the latest
        # scan kind. Merge the newest available row per provider instead.
        def provider_key(value):
            name = str(value or "").lower()
            if name.startswith("betfair"):
                return "betfair"
            if name.startswith("matchbook"):
                return "matchbook"
            if name.startswith("smarkets"):
                return "smarkets"
            return name

        status_candidates: dict[str, list[tuple[datetime | None, str, dict]]] = {}
        for source, scan_row, rows in (("price", last_price, price_statuses), ("discovery", last_discovery, discovery_statuses)):
            observed_at = self._parse_utc_dt(scan_row.get("finished_at") or scan_row.get("started_at"))
            for item in rows:
                if not isinstance(item, dict):
                    continue
                key = provider_key(item.get("provider_id") or item.get("exchange"))
                if key:
                    status_candidates.setdefault(key, []).append((observed_at, source, item))

        price_tick = max(1, int(float(cfg.get("price_scan_tick_seconds", 2) or 2)))
        discovery_interval = max(30, int(float(cfg.get("discovery_interval_seconds", cfg.get("scan_interval_seconds", 60)) or 60)))
        price_started = self._parse_utc_dt(last_price.get("started_at")); price_finished = self._parse_utc_dt(last_price.get("finished_at"))
        price_success = self._parse_utc_dt(last_price_ok.get("finished_at") or last_price_ok.get("started_at"))
        discovery_started = self._parse_utc_dt(last_discovery.get("started_at")); discovery_finished = self._parse_utc_dt(last_discovery.get("finished_at"))
        discovery_success = self._parse_utc_dt(last_discovery_ok.get("finished_at") or last_discovery_ok.get("started_at"))
        price_in_progress = bool(price_started and not price_finished)
        discovery_in_progress = bool(discovery_started and not discovery_finished)
        price_next = price_finished + timedelta(seconds=price_tick) if background.get("loaded") and price_finished else None
        discovery_next = discovery_finished + timedelta(seconds=discovery_interval) if background.get("loaded") and discovery_finished else None
        price_age = max(0.0, (now-price_finished).total_seconds()) if price_finished else None
        discovery_age = max(0.0, (now-discovery_finished).total_seconds()) if discovery_finished else None
        price_stale_after = max(30.0, float(cfg.get("price_refresh_later_seconds", 30) or 30) * 2.0, price_tick * 5.0)
        discovery_stale_after = max(120.0, discovery_interval * 2.5)
        try:
            _price_stage_latency = json.loads(last_price.get("stage_timings_json") or "{}")
        except Exception:
            _price_stage_latency = {}
        try:
            _discovery_stage_latency = json.loads(last_discovery.get("stage_timings_json") or "{}")
        except Exception:
            _discovery_stage_latency = {}

        def loop_state(row, in_progress, age, stale_after):
            if in_progress: return "scanning"
            if not background.get("loaded") or not bool(cfg.get("scanner_enabled", True)): return "offline"
            if row.get("error"): return "error"
            if age is not None and age > stale_after: return "stale"
            if row.get("finished_at"): return "healthy"
            return "waiting"

        price_state = loop_state(last_price, price_in_progress, price_age, price_stale_after)
        discovery_state = loop_state(last_discovery, discovery_in_progress, discovery_age, discovery_stale_after)

        snapshot_map = {}
        for row in health.get("latest_snapshots") or []:
            name = str(row.get("exchange") or "").lower()
            key = "betfair" if name.startswith("betfair") else "matchbook" if name.startswith("matchbook") else name
            snapshot_map[key] = row.get("latest")
        status_map = {}
        price_status_map = {}
        status_seen_at = {}
        price_status_seen_at = {}
        status_source = {}
        for key, candidates in status_candidates.items():
            candidates.sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            observed_at, source, row = candidates[0]
            status_map[key] = row
            status_seen_at[key] = observed_at
            status_source[key] = source
            price_candidates = [item for item in candidates if item[1] == "price"]
            if price_candidates:
                price_observed_at, _price_source, price_row = price_candidates[0]
                price_status_map[key] = price_row
                price_status_seen_at[key] = price_observed_at
        controls = {x["provider_id"]: x for x in self.db.venue_controls()}
        # A feed-only projection needs the selected mode's account readiness to
        # preserve the existing selected-mode RAG, but it must not inspect the
        # opposite mode merely to render market/feed evidence.
        sim_accounts = self.db.latest_account_snapshots("sim") if (not feeds_only or selected_mode == "sim") else {}
        live_accounts = self.db.latest_live_account_snapshots() if (not feeds_only or selected_mode == "live") else {}
        feeds=[]
        for spec in self.provider_runtime.providers.all():
            key = spec.provider_id.lower(); label = spec.venue.venue_name
            profile = self.provider_runtime.profile(key)
            runtime_status = self.provider_runtime.runtime_status(key)
            control = controls.get(key) or {"account_nickname": label, "sim_feed_enabled": True, "live_feed_enabled": False, "sim_account_enabled": True, "live_account_enabled": True, "live_execution_enabled": False}
            sim_feed_enabled = bool(control.get("sim_feed_enabled"))
            live_feed_enabled = bool(control.get("live_feed_enabled"))
            physical_feed_enabled = bool(sim_feed_enabled or live_feed_enabled)
            enabled = bool(
                physical_feed_enabled and cfg.get(f"{key}_enabled", profile.enabled if profile is not None else True)
                and (profile.enabled if profile is not None else True)
                and runtime_status.enabled
            )
            row=status_map.get(key) or {}
            price_row=price_status_map.get(key) or {}
            snap_dt=self._parse_utc_dt(snapshot_map.get(key))
            seen_dt=status_seen_at.get(key)
            price_seen_dt=price_status_seen_at.get(key)
            # Connectivity may be evidenced by either discovery or price traffic,
            # but market-data freshness must only advance from an actual price
            # observation/snapshot. A busy discovery pass must never make old
            # prices look fresh.
            freshness=max((dt for dt in (snap_dt, price_seen_dt) if dt is not None), default=None)
            age=max(0.0,(now-freshness).total_seconds()) if freshness else None
            market_row = price_row or row
            latency=int(market_row.get("latency_ms") or 0) if market_row and market_row.get("latency_ms") is not None else None
            # Older/partial scan status rows can omit provider latency even though
            # the scan stage and runtime health measured it.  Preserve that measured
            # value rather than rendering a false dash on a healthy connected feed.
            if latency is None:
                stage_latency = _price_stage_latency.get(f"{key}_fetch_ms")
                if stage_latency is not None and float(stage_latency or 0) > 0:
                    latency = int(float(stage_latency))
                elif runtime_status.latency_ms is not None:
                    latency = int(runtime_status.latency_ms)
                elif runtime_status.rolling_latency_ms is not None:
                    latency = int(round(float(runtime_status.rolling_latency_ms)))
            pending_api = bool(profile is not None and str(profile.api_state or "").lower() in {"pending_api", "awaiting_api_access"})
            transport_state = (
                "awaiting_api_access" if pending_api and not row else
                "disabled" if not enabled else
                ("waiting" if background.get("loaded") and bool(cfg.get("scanner_enabled", True)) else "offline") if not row else
                "error" if not bool(row.get("ok")) else
                "connected"
            )
            freshness_state = (
                "unavailable" if freshness is None else
                "stale" if age > max(30.0, price_stale_after*2) else
                "fresh"
            )
            if transport_state != "connected": state = transport_state
            elif freshness_state == "stale": state = "stale"
            elif latency is not None and latency > 5000: state = "slow"
            else: state = "connected"
            requested_feed = str(row.get("requested_feed_entitlement") or runtime_status.requested_feed_entitlement or (cfg.get("betfair_feed_entitlement") if key == "betfair" else None) or (profile.feed_entitlement.value if profile is not None else "unknown")).lower()
            effective_feed = str(row.get("effective_feed_entitlement") or runtime_status.effective_feed_entitlement or "unknown").lower()
            feed_reason = str(row.get("feed_reason") or runtime_status.feed_reason or "").strip() or None
            if key == "betfair":
                requested_feed = str(cfg.get("betfair_feed_entitlement", "delayed") or "delayed").lower()
                selected_key = self.secrets.get("betfair_live_app_key") if requested_feed == "live" else self.secrets.get("betfair_app_key")
                if not selected_key:
                    effective_feed = "unavailable"
                    feed_reason = f"{requested_feed.title()} App Key not configured"
                elif row:
                    effective_feed = str(row.get("effective_feed_entitlement") or effective_feed or "unknown").lower()
                elif requested_feed == "delayed":
                    effective_feed = "delayed"
                    feed_reason = feed_reason or "Delayed App Key configured; awaiting fresh market observation"
                elif effective_feed not in {"live", "delayed"}:
                    effective_feed = "unknown"
                    feed_reason = feed_reason or "Awaiting Betfair MarketBook confirmation"
            self.provider_runtime.update_market_health(key, ok=bool(enabled and row and row.get("ok")), latency_ms=latency,
                                                       quote_age_ms=None if age is None else int(age*1000),
                                                       error=None if bool(enabled and row and row.get("ok")) else (row.get("message") if row else ("Awaiting first feed observation" if state == "waiting" else "Scanner/feed status unavailable")),
                                                       requested_feed_entitlement=requested_feed, effective_feed_entitlement=effective_feed, feed_reason=feed_reason)
            sim = sim_accounts.get(key) or sim_accounts.get(label) or {}
            live = live_accounts.get(key) or {}
            sim_account_state = ("disabled" if not bool(control.get("sim_account_enabled")) else ("ready" if sim and sim.get("equity") is not None else ("awaiting_api_access" if pending_api else "waiting")))
            account_connection_state = ("awaiting_api_access" if pending_api else ("disabled" if not bool(control.get("live_account_enabled")) else str(live.get("connection_state") or "unknown").lower()))

            mode_states = {
                "sim": self._provider_selected_mode_state("sim", control, state, sim_account_state, account_connection_state),
                "live": self._provider_selected_mode_state("live", control, state, sim_account_state, account_connection_state),
            }
            selected_mode_state = mode_states.get(selected_mode, {}) if selected_mode else {}
            selected_state = str(selected_mode_state.get("state") or "disabled").lower()
            selected_rag = (
                "grey" if selected_state == "disabled" else
                "green" if selected_state == "ready" else
                "red" if selected_state == "error" else
                "amber"
            )
            feeds.append({"key":key,"provider_id":key,"venue_id":spec.venue.venue_id,"exchange":label,"enabled":enabled,"state":state,"ok":bool(row.get("ok")) if enabled and row else False,
                "account_nickname": str(control.get("account_nickname") or label),
                "sim_feed_enabled": sim_feed_enabled,
                "live_feed_enabled": live_feed_enabled,
                "sim_account_enabled": bool(control.get("sim_account_enabled")),
                "live_account_enabled": bool(control.get("live_account_enabled")),
                "live_execution_enabled": bool(control.get("live_execution_enabled")),
                "sim_account_state": sim_account_state,
                "account_state": account_connection_state,
                "mode_states": mode_states,
                "selected_mode": selected_mode,
                "selected_mode_expected": mode_states.get(selected_mode, {}).get("expected") if selected_mode else None,
                "selected_mode_enabled": mode_states.get(selected_mode, {}).get("expected") if selected_mode else None,
                "selected_mode_state": mode_states.get(selected_mode, {}).get("state") if selected_mode else None,
                "selected_mode_rag": selected_rag if selected_mode else None,
                "selected_mode_latency_ms": latency if selected_mode and bool(selected_mode_state.get("feed_expected")) else None,
                "live_execution_effective": False, "live_execution_reason": "Central LIVE execution lock",
                "sim_account": {"currency": sim.get("currency"), "balance": sim.get("equity"), "available": sim.get("available_balance"), "exposure": sim.get("exposure"), "realized_pnl": sim.get("realized_pnl"), "captured_at": sim.get("captured_at")},
                "live_account": {"currency": live.get("currency"), "balance": live.get("balance"), "available": live.get("available_balance"), "exposure": live.get("exposure"), "received_at": live.get("received_at"), "connection_state": live.get("connection_state")},
                "latency_ms":latency,"markets":int(market_row.get("markets") or market_row.get("requested") or 0) if market_row else 0,
                "requested_feed_entitlement": requested_feed, "effective_feed_entitlement": effective_feed,
                "feed_entitlement": effective_feed, "feed_reason": feed_reason,
                "transport": profile.market_data_transport.value if profile is not None else "unknown",
                "transport_state": transport_state,
                "freshness_state": freshness_state,
                "status_source": status_source.get(key),
                "status_observed_at": seen_dt.isoformat() if seen_dt else None,
                "price_status_observed_at": price_seen_dt.isoformat() if price_seen_dt else None,
                "last_snapshot_at":snap_dt.isoformat() if snap_dt else None,"age_seconds":round(age,2) if age is not None else None,
                "api_state": (profile.api_state if profile is not None else "available"),
                "message":("Approved · awaiting Smarkets API activation" if key == "smarkets" and pending_api else (
                    "Feed disabled in both SIM and LIVE" if not physical_feed_enabled else (row.get("message") if row else ("Awaiting first feed observation" if state == "waiting" else "Scanner/feed status unavailable")))),
                "sim_feed_state": ("off" if not sim_feed_enabled else effective_feed),
                "live_feed_state": ("off" if not live_feed_enabled else effective_feed)})

        if feeds_only:
            return {"mode": selected_mode, "feeds": feeds}

        pipeline={"fetched":int(last_price.get("markets_seen") or 0),"matched":int(last_price.get("matches_seen") or 0),
            "processed":int(last_price.get("processed_candidates") or 0),"opportunities":int(last_price.get("positive_opportunities") or 0),
            "qualified":int(last_price.get("qualified_count") or 0),"executed":int(last_price.get("executed_count") or 0),
            "stale_rejections":int(last_price.get("stale_rejections") or 0)}
        processed=pipeline["processed"]; opportunities=pipeline["opportunities"]
        # Scanner qualified_count is the decision-engine boundary. In LIVE that is
        # simulated decision evidence, not an authoritative lifecycle qualification.
        # Keep it as a diagnostic while making the selected-mode operational suffix
        # fail closed. This prevents generic status consumers from reintroducing the
        # same semantic leak after the Dashboard-specific renderer has corrected it.
        if selected_mode == "live":
            pipeline["decision_qualified_evidence"] = int(pipeline.get("qualified") or 0)
            pipeline["decision_executed_evidence"] = int(pipeline.get("executed") or 0)
            pipeline["qualified"] = 0
            pipeline["executed"] = 0
        qualified=pipeline["qualified"]
        pipeline["opportunity_rate_pct"]=round((opportunities/processed)*100.0,3) if processed else 0.0
        pipeline["qualification_rate_pct"]=round((qualified/opportunities)*100.0,3) if opportunities else 0.0
        if selected_mode == "live":
            evidence_q = int(pipeline.get("decision_qualified_evidence") or 0)
            pipeline["decision_qualification_rate_pct"] = round((evidence_q/opportunities)*100.0,3) if opportunities else 0.0
        pipeline["execution_conversion_pct"]=round((pipeline["executed"]/qualified)*100.0,3) if qualified else 0.0
        pipeline["failure_reasons"]=self.db.execution_failure_reasons_between(price_started.isoformat() if price_started else None, price_finished.isoformat() if price_finished else None)
        pipeline["qualification_breakdown"]=self.db.qualification_breakdown_for_scan(last_price.get("id") if last_price else None)
        pipeline["in_play_research"]=int((pipeline.get("qualification_breakdown") or {}).get("in_play_research",0) or 0)
        stage_timings=dict(_price_stage_latency)
        discovery_stage=dict(_discovery_stage_latency)
        def _measured_duration_ms(row, started, finished, stage):
            stored = int(float((row or {}).get("duration_ms") or 0))
            if stored > 0:
                return stored
            if started is not None and finished is not None:
                return max(0, int(round((finished-started).total_seconds()*1000.0)))
            vals = [float(v or 0) for k,v in (stage or {}).items() if str(k).endswith("_ms") and isinstance(v,(int,float))]
            return int(round(max(vals))) if vals else 0
        price_duration_ms = _measured_duration_ms(last_price, price_started, price_finished, stage_timings)
        discovery_duration_ms = _measured_duration_ms(last_discovery, discovery_started, discovery_finished, discovery_stage)
        cache=health.get("market_cache") or {}
        price_obj={"state":price_state,"running":bool(background.get("loaded") and cfg.get("scanner_enabled",True)),"enabled":bool(cfg.get("scanner_enabled",True)),"worker_loaded":bool(background.get("loaded")),"scan_in_progress":price_in_progress,
            "interval_seconds":price_tick,"last_started_at":price_started.isoformat() if price_started else None,"last_finished_at":price_finished.isoformat() if price_finished else None,
            "last_success_at":price_success.isoformat() if price_success else None,"last_age_seconds":round(price_age,2) if price_age is not None else None,
            "next_poll_at":price_next.isoformat() if price_next else None,"duration_ms":price_duration_ms,"error":last_price.get("error"),
            "stage_timings":stage_timings,"cache_entries":int(last_price.get("cache_entries") or 0),
            "active_cache":int(cache.get("active") or 0),"scan_id":int(last_price.get("id") or 0)}
        discovery_obj={"state":discovery_state,"running":bool(background.get("loaded") and cfg.get("scanner_enabled",True)),"enabled":bool(cfg.get("scanner_enabled",True)),"worker_loaded":bool(background.get("loaded")),"scan_in_progress":discovery_in_progress,
            "interval_seconds":discovery_interval,"last_started_at":discovery_started.isoformat() if discovery_started else None,
            "last_finished_at":discovery_finished.isoformat() if discovery_finished else None,"last_success_at":discovery_success.isoformat() if discovery_success else None,
            "last_age_seconds":round(discovery_age,2) if discovery_age is not None else None,"next_poll_at":discovery_next.isoformat() if discovery_next else None,
            "duration_ms":discovery_duration_ms,"error":last_discovery.get("error"),"stage_timings":discovery_stage,
            "fetched":int(last_discovery.get("markets_seen") or 0),"matched":int(last_discovery.get("matches_seen") or 0),"cache_entries":int(last_discovery.get("cache_entries") or cache.get("active") or 0)}
        mode_summary = {}
        for mode_name in ("sim", "live"):
            provider_expected = [f for f in feeds if (f.get("mode_states") or {}).get(mode_name, {}).get("expected")]
            provider_ready = [f for f in provider_expected if (f.get("mode_states") or {}).get(mode_name, {}).get("state") == "ready"]
            feed_expected = [f for f in feeds if (f.get("mode_states") or {}).get(mode_name, {}).get("feed_expected")]
            feed_ready = [f for f in feed_expected if (f.get("mode_states") or {}).get(mode_name, {}).get("feed_state") in {"connected", "healthy", "ready"}]
            account_expected = [f for f in feeds if (f.get("mode_states") or {}).get(mode_name, {}).get("account_expected")]
            account_ready = [f for f in account_expected if (f.get("mode_states") or {}).get(mode_name, {}).get("account_state") in {"connected", "healthy", "ready"}]
            feed_latencies = [int(f.get("latency_ms")) for f in feed_expected if f.get("latency_ms") is not None]
            summary_state = ("ready" if provider_expected and len(provider_ready) == len(provider_expected) else ("disabled" if not provider_expected else ("error" if any((f.get("mode_states") or {}).get(mode_name, {}).get("state") == "error" for f in provider_expected) else "degraded")))
            mode_summary[mode_name] = {
                "providers_expected": len(provider_expected), "providers_ready": len(provider_ready),
                "feeds_expected": len(feed_expected), "feeds_ready": len(feed_ready),
                "accounts_expected": len(account_expected), "accounts_ready": len(account_ready),
                "latency_ms": max(feed_latencies) if feed_latencies else None,
                "state": summary_state,
                "rag": "grey" if summary_state == "disabled" else "green" if summary_state == "ready" else "red" if summary_state == "error" else "amber",
            }
        selected_summary = mode_summary.get(selected_mode) if selected_mode else None
        pre_enabled = bool(cfg.get("pre_match_monitor_enabled", True))
        inplay_enabled = bool(cfg.get("inplay_monitor_enabled", True))
        sports_streams_enabled = int(pre_enabled) + int(inplay_enabled)
        selected_feed_expected = int((selected_summary or {}).get("feeds_expected") or 0)
        selected_feed_ready = int((selected_summary or {}).get("feeds_ready") or 0)
        scanner_enabled = bool(cfg.get("scanner_enabled", True))
        worker_loaded = bool(background.get("loaded"))
        if not sports_streams_enabled:
            monitor_state, monitor_rag, monitor_reason = "disabled", "grey", "Pre-match and In-play are disabled in Sports Config"
        elif selected_mode and not selected_feed_expected:
            monitor_state, monitor_rag, monitor_reason = "disabled", "grey", f"No {selected_mode.upper()} market feeds enabled in Admin"
        elif not scanner_enabled:
            monitor_state, monitor_rag, monitor_reason = "disabled", "grey", "Scanner disabled"
        elif not worker_loaded or price_state in {"offline", "error"}:
            monitor_state, monitor_rag, monitor_reason = "offline", "red", "Scanner worker unavailable" if not worker_loaded else "Price scanner error"
        elif selected_mode and selected_feed_ready < selected_feed_expected:
            monitor_state, monitor_rag, monitor_reason = "degraded", "amber", f"{selected_feed_ready}/{selected_feed_expected} selected-mode feeds ready"
        elif sports_streams_enabled < 2:
            monitor_state, monitor_rag, monitor_reason = "partial", "amber", "Only one Sports stream is enabled"
        else:
            monitor_state, monitor_rag, monitor_reason = "active", "green", "Pre-match and In-play enabled"
        monitor = {
            "state": monitor_state, "rag": monitor_rag, "reason": monitor_reason,
            "selected_mode": selected_mode, "pre_match_enabled": pre_enabled, "in_play_enabled": inplay_enabled,
            "streams_enabled": sports_streams_enabled, "feeds_expected": selected_feed_expected, "feeds_ready": selected_feed_ready,
            "latency_ms": (selected_summary or {}).get("latency_ms"),
            "live_execution_allowed": False,
        }
        return {"scanner":price_obj,"price_scanner":price_obj,"discovery":discovery_obj,"feeds":feeds,"pipeline":pipeline,"cache":cache,
                "monitor": monitor, "selected_mode": selected_mode, "mode_summary": mode_summary,
                "selected_mode_summary": selected_summary}

    def dashboard_trends(self, data=None):
        """Seven-day dashboard trend data for Sports performance and Racing MONITOR activity."""
        data = data or {}
        try:
            days = max(1, min(31, int(data.get("days") or 7)))
        except (TypeError, ValueError):
            days = 7
        try:
            timezone_offset_minutes = int(data.get("timezone_offset_minutes") or 0)
        except (TypeError, ValueError):
            timezone_offset_minutes = 0
        timezone_name = str(data.get("timezone_name") or "").strip() or None
        trend = self.db.dashboard_daily_trends(
            days,
            timezone_name=timezone_name,
            timezone_offset_minutes=timezone_offset_minutes,
        )
        return {"ok": True, **trend}

    def live_activity_status(self, data=None):
        """Lightweight operational snapshot for the dashboard process strip.

        The endpoint name is retained for compatibility, but callers may now pass
        ``mode`` so SIM polling cannot accidentally receive a LIVE summary.
        """
        data = data or {}
        selected_mode = str(data.get("mode") or "live").strip().lower()
        if selected_mode not in {"sim", "live"}:
            selected_mode = "live"
        return {
            "ok": True,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "operations": self._operational_status(selected_mode),
        }

    def pipeline_analytics(self, data=None):
        """Period scan-funnel totals with explicit SIM/LIVE lifecycle ownership.

        Discovery/fetched/matched/processed are shared scanner facts. The stored
        opportunity/Monitor transaction counts are SIM lifecycle records, so a
        LIVE caller must never receive them as canonical Qualified/Executed.
        """
        data = data or {}
        mode = canonical_mode_value(data.get("mode") or "sim")
        date_from = self._parse_utc(data.get("from_utc"))
        date_to = self._parse_utc(data.get("to_utc"))
        pipeline = self.db.scan_pipeline_between(
            date_from.isoformat() if date_from else None,
            date_to.isoformat() if date_to else None,
        )
        if mode == "live":
            # The price scanner may still report decision-boundary observations.
            # Preserve those only as diagnostics; SIM opportunity/Monitor rows
            # have no authority in the LIVE lifecycle.
            pipeline["decision_qualified_evidence"] = int(pipeline.get("qualified_observations") or 0)
            pipeline["decision_executed_evidence"] = int(pipeline.get("executed_observations") or 0)
            pipeline["qualified"] = 0
            pipeline["executed"] = 0
        pipeline["failure_reasons"] = self.db.execution_failure_reasons_between(
            date_from.isoformat() if date_from else None,
            date_to.isoformat() if date_to else None,
        )
        discovery = self.db.discovery_pipeline_between(
            date_from.isoformat() if date_from else None,
            date_to.isoformat() if date_to else None,
        )
        in_play_research = self.db.monitor_timing_metrics(
            from_utc=date_from.isoformat() if date_from else None,
            to_utc=date_to.isoformat() if date_to else None,
            include_demo=False,
            qualification_status="in_play_research",
        )
        in_play_monitor = self.db.monitor_timing_metrics(
            from_utc=date_from.isoformat() if date_from else None,
            to_utc=date_to.isoformat() if date_to else None,
            include_demo=False,
            qualification_status="in_play_qualified",
        )
        return {
            "ok": True,
            "mode": mode,
            "from_utc": date_from.isoformat() if date_from else None,
            "to_utc": date_to.isoformat() if date_to else None,
            "pipeline": pipeline,
            "discovery": discovery,
            "in_play_research": in_play_research,
            "in_play_monitor": in_play_monitor,
        }

    def archive_pilot_status(self, data=None):
        """Read-only operational status for the 0.9.36 archive/prune lifecycle."""
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        root = default_archive_root(self.db.path)
        runtime = load_runtime_state(root)
        dependency = duckdb_runtime_status()
        runtime_gate = load_runtime_gate_report(root)
        runtime_gate_passed = archive_runtime_gate_passed(root)
        blocked_until, blocked_reason = runtime_blocked_until(root)
        first_success = runtime.get("first_success_hour")
        last_success = runtime.get("last_success_hour")
        enabled = bool(cfg.get("matched_market_archive_enabled", False))
        pilot_start = runtime.get("pilot_start_hour") or first_success
        continuity = archive_continuity(root, pilot_start, newest_closed_hour())
        latest_verified = continuity.get("latest_verified_hour") or last_success
        latest_checksum_verified = bool(latest_verified and manifest_verified(root, latest_verified, verify_checksum=True))
        next_target = next_pilot_archive_hour(root, pilot_start, newest_closed_hour()) if enabled else None
        storage = self.db.matched_market_storage_health(
            retention_hours=int(cfg.get("matched_market_retention_hours", 48) or 48)
        )
        prune_dry_run = archive_prune_dry_run(
            self.db.path, root, retention_hours=int(cfg.get("matched_market_retention_hours", 48) or 48)
        )
        runtime_gate_required = bool(cfg.get("matched_market_archive_runtime_gate_required", True))
        prune_gate = bool(cfg.get("matched_market_archive_required_before_prune", False))
        now = time.time()
        blocked = bool(blocked_until and blocked_until > now)
        if not dependency.get("available"):
            readiness = "DEPENDENCY_MISSING"
        elif latest_verified and not latest_checksum_verified:
            readiness = "CHECKSUM_FAILURE"
        elif blocked:
            readiness = "PAUSED"
        elif enabled and runtime_gate_required and not runtime_gate_passed:
            readiness = "GATE_REQUIRED"
        elif enabled and continuity.get("started") and continuity.get("complete"):
            readiness = "HEALTHY"
        elif enabled and continuity.get("started"):
            readiness = "CATCHING_UP"
        elif enabled:
            readiness = "STARTING"
        else:
            readiness = "OFF"
        return {
            "ok": True,
            "enabled": enabled,
            "archive_required_before_prune": prune_gate,
            "readiness": readiness,
            "dependency": dependency,
            "runtime_gate": runtime_gate,
            "runtime_gate_passed": runtime_gate_passed,
            "runtime_gate_required": runtime_gate_required,
            "archive_root": str(root),
            "latest_closed_hour": newest_closed_hour(),
            "pilot_started_at": runtime.get("pilot_started_at"),
            "pilot_start_hour": runtime.get("pilot_start_hour"),
            "first_success_hour": first_success,
            "last_success_hour": last_success,
            "latest_verified_hour": latest_verified,
            "latest_checksum_verified": latest_checksum_verified,
            "next_target_hour": next_target,
            "pending_archive_hours": len(continuity.get("gaps") or []),
            "last_attempt_hour": runtime.get("last_attempt_hour"),
            "last_error": runtime.get("last_error"),
            "last_guard": runtime.get("last_guard"),
            "blocked": blocked,
            "blocked_reason": blocked_reason if blocked else None,
            "blocked_until_epoch": blocked_until if blocked else 0,
            "continuity": continuity,
            "storage": storage,
            "prune_dry_run": prune_dry_run,
            "prune_execution_enabled": prune_gate,
            "prune_policy": ("archive-gated" if prune_gate else "pilot-soak-no-delete") if enabled else "existing-48h-unchanged",
            "prune_planner_mode": "PRUNING_CAPABLE",
            "live_order_writes": False,
        }

    def get_state(self):
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        dashboard = self.db.dashboard(include_demo=not bool(cfg.get("hide_demo_data", True)))
        try:
            # Startup only needs the newest cards. Avoid scoring the entire history
            # every time the lightweight application state is refreshed.
            dashboard["recent_cards"] = self.opportunity_history({
                "bankroll": float(cfg.get("quality_reference_bankroll", 500.0)),
                "limit": 12,
                "recent_only": True,
            }).get("rows", [])
        except Exception:
            dashboard["recent_cards"] = []
        mode = canonical_mode_value(self.db.get_setting("mode", "sim"))
        if mode not in OPERATING_MODES or not OPERATING_MODES[mode]["available"]:
            mode = "sim"
        background = self.service.status()
        automation_meta = self.db.get_setting("automation_runtime", {}) or {}
        scanner_running = bool(cfg.get("scanner_enabled", True) and background.get("loaded"))
        automation = {
            "armed": mode == "live",
            "running": scanner_running,
            "mode": mode,
            "started_at": automation_meta.get("started_at") if mode == "live" else None,
            "stopped_at": automation_meta.get("stopped_at"),
            "status": ("LIVE ACTIVE" if mode == "live" else "SIM ACTIVE") if scanner_running else "SCANNER OFFLINE",
            "stop_policy": "SIM is the default operating state. Future LIVE stop blocks new real bets while reconciliation/hedging remains allowed to flatten exposure.",
        }
        active_job = self.db.active_job()
        job_history = self.db.job_history(limit=12)
        current_job = next((x for x in job_history if active_job and int(x.get("id") or 0) == int(active_job.get("id") or 0)), active_job)
        schedules = self.db.schedules(limit=20)
        data_context_mode = canonical_mode_value(self.db.get_setting("data_context_mode", mode))
        return {
            "version": "1.0",
            "settings": {
                "mode": mode,
                "data_context_mode": data_context_mode,
                "operating_modes": OPERATING_MODES,
                "live_execution_available": bool(OPERATING_MODES["live"]["available"]),
                "scenarios": self.db.get_setting("scenarios", []),
                "config": cfg,
                "credential_store": self.secrets.status(),
                "credentials": self.secrets.presence(),
                "betfair_feed": self.betfair_feed_status(include_state=False),
            },
            "dashboard": dashboard,
            "background": background,
            "automation": automation,
            "operations": self._operational_status(data_context_mode),
            "jobs": {
                "current": current_job,
                "recent": job_history,
                "schedules": schedules,
                "legacy_only": True,
            },
        }

    def runtime_state(self, data=None):
        """Lightweight 0.9.36 periodic runtime state.

        Unlike get_state(), this intentionally omits Dashboard history, jobs,
        schedules and configuration hydration so a background heartbeat cannot
        inject SIM economic rows into a LIVE page or trigger expensive history
        reads every 15 seconds.
        """
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        mode = canonical_mode_value(self.db.get_setting("mode", "sim"))
        if mode not in OPERATING_MODES or not OPERATING_MODES[mode]["available"]:
            mode = "sim"
        data_context_mode = canonical_mode_value(self.db.get_setting("data_context_mode", mode))
        return {
            "ok": True, "version": "1.0",
            "settings": {
                "mode": mode, "data_context_mode": data_context_mode,
                "betfair_feed": self.betfair_feed_status(include_state=False),
            },
            "background": self.service.status(),
            "operations": self._operational_status(data_context_mode),
        }

    def save_settings(self, data):
        scenarios = []
        for x in data.get("scenarios", []):
            try: v = float(x)
            except (TypeError, ValueError): continue
            if v > 0: scenarios.append(v)
        if scenarios:
            self.db.set_setting("scenarios", scenarios)

        current = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        previous_betfair_feed = str(current.get("betfair_feed_entitlement", "delayed") or "delayed").lower()
        incoming = data.get("config", {}) or {}
        # 0.9.3: Racing uses PATCH semantics. Missing/uninitialised fields never
        # become zero, while an explicit numeric zero remains a real operator value.
        racing_rules = {
            "racing_monitor_betfair_starting_balance": (0.0, 1_000_000.0),
            "racing_monitor_matchbook_starting_balance": (0.0, 1_000_000.0),
            "racing_execution_max_stake": (0.0, 1_000_000.0),
            "racing_max_event_exposure_pct": (0.0, 100.0),
            "racing_execution_max_slippage_pct": (0.0, 25.0),
            "racing_execution_hedge_reserve_pct": (0.0, 100.0),
            "racing_execution_max_unhedged_exposure": (0.0, 1_000_000.0),
            "racing_minimum_liquidity": (0.0, 1_000_000.0),
            "racing_minimum_net_roi_pct": (0.0, 100.0),
            "racing_minimum_profit": (0.0, 1_000_000.0),
            "racing_match_threshold": (0.5, 1.0),
            "racing_runner_match_threshold": (0.5, 1.0),
            "racing_monitor_retry_cooldown_seconds": (0.0, 300.0),
            "racing_monitor_max_attempts_per_race": (1.0, 20.0),
            "racing_max_cross_venue_receipt_spread_ms": (0.0, 60_000.0),
        }
        for key, (low, high) in racing_rules.items():
            if key not in incoming:
                continue
            raw = incoming.get(key)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                state = self.get_state(); state.update({"ok": False, "message": f"{key} was blank; Racing settings were not saved."}); return state
            try:
                value = float(raw)
            except (TypeError, ValueError):
                state = self.get_state(); state.update({"ok": False, "message": f"{key} is not a valid number; Racing settings were not saved."}); return state
            if not (low <= value <= high):
                state = self.get_state(); state.update({"ok": False, "message": f"{key} must be between {low:g} and {high:g}; Racing settings were not saved."}); return state
            incoming[key] = int(value) if key in {"racing_monitor_max_attempts_per_race", "racing_max_cross_venue_receipt_spread_ms"} else value
        # v0.8.44: feed enablement is an explicit operator action. Generic Admin
        # saves must never flip Betfair/Matchbook on or off as a side effect.
        protected_feed_flags = {"betfair_enabled", "matchbook_enabled", "smarkets_enabled"}
        for k, v in incoming.items():
            if k in DEFAULT_CONFIG and k not in protected_feed_flags:
                current[k] = v
        current["scan_interval_seconds"] = max(30, int(float(current.get("scan_interval_seconds", 60))))
        current["discovery_interval_seconds"] = max(30, int(float(current.get("discovery_interval_seconds", current["scan_interval_seconds"]))))
        current["price_scan_tick_seconds"] = max(1, min(30, int(float(current.get("price_scan_tick_seconds", 2)))))
        current["price_refresh_inplay_seconds"] = max(1, min(30, int(float(current.get("price_refresh_inplay_seconds", 2)))))
        current["price_refresh_near_seconds"] = max(2, min(60, int(float(current.get("price_refresh_near_seconds", 3)))))
        current["price_refresh_today_seconds"] = max(current["price_refresh_near_seconds"], min(120, int(float(current.get("price_refresh_today_seconds", 8)))))
        current["price_refresh_later_seconds"] = max(current["price_refresh_today_seconds"], min(600, int(float(current.get("price_refresh_later_seconds", 30)))))
        current["horizon_hours"] = min(168, max(1, int(float(current.get("horizon_hours", 24)))))
        current["live_lookback_hours"] = min(24, max(1, int(float(current.get("live_lookback_hours", 8)))))
        currency = str(current.get("account_currency", "GBP") or "GBP").strip().upper()
        current["account_currency"] = currency if len(currency) == 3 and currency.isalpha() else "GBP"
        for key, default, maxv in [
            ("account_balance_stale_seconds", 90, 3600), ("account_refresh_seconds", 30, 3600), ("account_history_cache_seconds", 120, 86400), ("account_reconciliation_tolerance", 0.01, 1000),
            ("minimum_liquidity", 2.0, 1_000_000), ("minimum_net_roi_pct", 1.0, 100), ("minimum_profit", 0.0, 1_000_000),
            ("max_bankroll_pct", 100, 100), ("max_event_exposure_pct", 100, 100),
            ("matchbook_commission_pct", 2.0, 20), ("betfair_commission_pct", 2.0, 20),
            ("quality_reference_bankroll", 500, 1_000_000), ("stale_quote_seconds", 90, 3600),
            ("price_quote_max_age_seconds", 10.0, 300),
            ("live_decision_max_quote_age_seconds", 10.0, 300),
            ("live_decision_max_receipt_spread_ms", 1500, 60_000),
            ("alert_min_deployed_roi_pct", 0.75, 100),
            ("alert_min_bankroll_roi_pct", 0.0, 100), ("alert_min_capital_used_pct", 0.0, 100),
            ("alert_min_profit", 1.0, 1_000_000),
            ("alert_retry_minutes", 15, 1440),
            ("execution_plan_ttl_ms", 1500, 30_000), ("execution_max_stake", 25.0, 1_000_000),
            ("execution_max_slippage_pct", 0.50, 25),
            ("execution_max_unhedged_exposure", 25.0, 1_000_000),
            ("execution_hedge_reserve_pct", 20.0, 100),
            ("execution_balance_tolerance", 0.10, 1000),
            ("monitor_execution_checkpoint_ms", 500, 30_000),
            ("monitor_hedge_checkpoint_ms", 1000, 30_000),
            ("monitor_betfair_starting_balance", 250.0, 1_000_000),
            ("monitor_matchbook_starting_balance", 250.0, 1_000_000),
            ("pre_match_monitor_betfair_starting_balance", 250.0, 1_000_000),
            ("pre_match_monitor_matchbook_starting_balance", 250.0, 1_000_000),
            ("inplay_monitor_betfair_starting_balance", 250.0, 1_000_000),
            ("inplay_monitor_matchbook_starting_balance", 250.0, 1_000_000),
            ("pre_match_minimum_liquidity", 2.0, 1_000_000),
            ("pre_match_minimum_net_roi_pct", 1.0, 100),
            ("pre_match_minimum_profit", 0.0, 1_000_000),
            ("pre_match_execution_max_stake", 25.0, 1_000_000),
            ("pre_match_max_event_exposure_pct", 100.0, 100),
            ("pre_match_execution_max_slippage_pct", 0.50, 25),
            ("pre_match_execution_max_unhedged_exposure", 25.0, 1_000_000),
            ("pre_match_execution_hedge_reserve_pct", 20.0, 100),
            ("inplay_minimum_liquidity", 2.0, 1_000_000),
            ("inplay_minimum_net_roi_pct", 1.0, 100),
            ("inplay_minimum_profit", 0.0, 1_000_000),
            ("inplay_execution_max_stake", 25.0, 1_000_000),
            ("inplay_max_event_exposure_pct", 100.0, 100),
            ("inplay_execution_max_unhedged_exposure", 25.0, 1_000_000),
            ("inplay_execution_hedge_reserve_pct", 20.0, 100),
            ("inplay_monitor_cooldown_seconds", 8.0, 300),
            ("inplay_betfair_delay_ms", 5000, 30_000),
            ("inplay_matchbook_delay_ms", 1000, 30_000),
            ("inplay_adverse_odds_pct_per_second", 0.20, 25),
            ("inplay_liquidity_decay_pct_per_second", 8.0, 100),
            ("inplay_execution_max_slippage_pct", 1.50, 25),
            ("racing_monitor_betfair_starting_balance", 250.0, 1_000_000),
            ("racing_monitor_matchbook_starting_balance", 250.0, 1_000_000),
            ("racing_execution_max_stake", 25.0, 1_000_000),
            ("racing_max_event_exposure_pct", 100.0, 100),
            ("racing_execution_max_slippage_pct", 0.50, 25),
            ("racing_execution_max_unhedged_exposure", 25.0, 1_000_000),
            ("racing_execution_hedge_reserve_pct", 20.0, 100),
            ("racing_minimum_liquidity", 2.0, 1_000_000),
            ("racing_minimum_net_roi_pct", 1.0, 100),
            ("racing_minimum_profit", 0.0, 1_000_000),
            ("racing_monitor_retry_cooldown_seconds", 5.0, 300),
            ("racing_max_cross_venue_receipt_spread_ms", 0.0, 60_000),
            ("matched_market_retention_hours", 48, 24 * 31),
            ("matched_market_prune_batch_rows", 5000, 100_000),
            ("matched_market_heartbeat_seconds", 900, 86_400),
            ("matched_market_maintenance_seconds", 30, 3600),
        ]:
            current[key] = min(maxv, max(0.0, float(current.get(key, default))))
        current["racing_match_threshold"] = min(1.0, max(0.5, float(current.get("racing_match_threshold", 0.90) or 0.90)))
        current["racing_runner_match_threshold"] = min(1.0, max(0.5, float(current.get("racing_runner_match_threshold", 0.92) or 0.92)))
        current["racing_monitor_max_attempts_per_race"] = min(20, max(1, int(float(current.get("racing_monitor_max_attempts_per_race", 3) or 3))))
        # 0.9.48: operator-visible technical settings are validated server-side.
        current["engine_max_concurrent_runtimes"] = min(1000, max(1, int(float(current.get("engine_max_concurrent_runtimes", 100) or 100))))
        current["price_scan_cache_limit"] = min(100000, max(100, int(float(current.get("price_scan_cache_limit", 1000) or 1000))))
        current["snapshot_legacy_keep_rows"] = min(10_000_000, max(0, int(float(current.get("snapshot_legacy_keep_rows", 100000) or 0))))
        current["snapshot_prune_batch_rows"] = min(1_000_000, max(100, int(float(current.get("snapshot_prune_batch_rows", 100000) or 100000))))
        current["snapshot_maintenance_seconds"] = min(3600, max(1, int(float(current.get("snapshot_maintenance_seconds", 10) or 10))))
        current["settlement_poll_seconds"] = min(3600, max(1, int(float(current.get("settlement_poll_seconds", 30) or 30))))
        # v0.8.30 SUPERBET validation. ``unlimited`` removes only the tranche
        # count ceiling; wallet/bankroll/exposure and max-total-stake limits still
        # apply in the executor.
        current["event_match_threshold"] = min(1.0, max(0.5, float(current.get("event_match_threshold", 0.72))))
        current["live_decision_min_mapping_confidence"] = min(1.0, max(0.5, float(current.get("live_decision_min_mapping_confidence", current["event_match_threshold"]) or current["event_match_threshold"])))
        current["live_decision_evidence_enabled"] = bool(current.get("live_decision_evidence_enabled", True))
        requested_betfair_feed = str(current.get("betfair_feed_entitlement", "delayed") or "delayed").strip().lower()
        if requested_betfair_feed not in {"delayed", "live"}:
            requested_betfair_feed = "delayed"
        current["betfair_feed_entitlement"] = requested_betfair_feed
        bands = current.get("alert_quality_bands") or ["Usable", "Strong", "Excellent"]
        allowed_bands = [b for b in ["Excellent", "Strong", "Usable", "Thin", "Tiny"] if b in bands]
        current["alert_quality_bands"] = allowed_bands or ["Usable", "Strong", "Excellent"]
        quality_bands = {"Tiny", "Thin", "Usable", "Strong", "Excellent"}
        for key in ("pre_match_minimum_quality_band", "inplay_minimum_quality_band"):
            value = str(current.get(key, "Tiny") or "Tiny").title()
            current[key] = value if value in quality_bands else "Tiny"
        self.db.set_setting("config", current)
        if current.get("betfair_feed_entitlement") != previous_betfair_feed:
            self.db.invalidate_provider_market_quotes("betfair")
            runtime = self.provider_runtime.runtime_status("betfair")
            runtime.requested_feed_entitlement = str(current.get("betfair_feed_entitlement") or "delayed")
            runtime.effective_feed_entitlement = "unknown"
            runtime.market_data_connected = False
            runtime.quote_age_ms = None
            runtime.feed_generation = int(runtime.feed_generation or 0) + 1
            runtime.feed_reason = "Feed selection changed; awaiting fresh Betfair MarketBook confirmation"

        secrets = data.get("secrets", {}) or {}
        updates = {
            key: secrets.get(key)
            for key in ("matchbook_password", "matchbook_session_token", "betfair_app_key", "betfair_live_app_key", "betfair_session_token")
            if key in secrets
        }
        username = str(current.get("matchbook_username") or "").strip()
        if username:
            updates["matchbook_username"] = username
        if updates:
            self.secrets.set_many(updates)
            if any(str(k).startswith("betfair_") for k in updates):
                self.live_providers.invalidate_account_providers("betfair")
            if any(str(k).startswith("matchbook_") for k in updates):
                self.live_providers.invalidate_account_providers("matchbook")
        return self.get_state()

    def betfair_feed_status(self, data=None, *, include_state: bool = False):
        """Return safe requested/effective Betfair market-feed state.

        Requested entitlement is persisted operator configuration. Effective
        entitlement is runtime/provider evidence and is never promoted to LIVE
        solely because the selector says LIVE.
        """
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        requested = str(cfg.get("betfair_feed_entitlement", "delayed") or "delayed").strip().lower()
        if requested not in {"delayed", "live"}:
            requested = "delayed"
        presence = self.secrets.presence()
        delayed_configured = bool(presence.get("betfair_app_key"))
        live_configured = bool(presence.get("betfair_live_app_key"))
        runtime = self.provider_runtime.runtime_status("betfair")
        selected_configured = live_configured if requested == "live" else delayed_configured
        effective = str(runtime.effective_feed_entitlement or "unknown").lower()
        reason = runtime.feed_reason
        if not selected_configured:
            effective = "unavailable"
            reason = f"{requested.title()} App Key not configured"
        elif requested == "delayed" and effective not in {"delayed", "live"}:
            effective = "delayed"
            reason = reason or "Delayed App Key configured; awaiting fresh market observation"
        elif requested == "live" and effective not in {"live", "delayed"}:
            effective = "unknown"
            reason = reason or "Awaiting Betfair MarketBook confirmation"
        payload = {
            "ok": True, "provider_id": "betfair", "requested_feed_entitlement": requested,
            "effective_feed_entitlement": effective, "feed_reason": reason,
            "delayed_app_key_configured": delayed_configured, "live_app_key_configured": live_configured,
            "session_token_configured": bool(presence.get("betfair_session_token")),
            "transport": "poll", "feed_generation": int(runtime.feed_generation or 0),
            "orders_write_capability": False, "live_execution_allowed": False,
        }
        if include_state:
            payload["state"] = self.get_state()
        return payload

    def set_betfair_market_feed(self, data=None):
        data = data or {}
        requested = str(data.get("feed") or data.get("requested_feed_entitlement") or "").strip().lower()
        if requested not in {"delayed", "live"}:
            return {"ok": False, "message": "Betfair market feed must be DELAYED or LIVE."}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        previous = str(cfg.get("betfair_feed_entitlement", "delayed") or "delayed").lower()
        cfg["betfair_feed_entitlement"] = requested
        self.db.set_setting("config", cfg)
        runtime = self.provider_runtime.runtime_status("betfair")
        runtime.requested_feed_entitlement = requested
        runtime.feed_generation = int(runtime.feed_generation or 0) + (1 if requested != previous else 0)
        if requested != previous:
            self.db.invalidate_provider_market_quotes("betfair")
            runtime.market_data_connected = False
            runtime.quote_age_ms = None
            runtime.last_success_at = None
            runtime.effective_feed_entitlement = "unknown"
            runtime.feed_reason = "Feed selection changed; awaiting fresh Betfair MarketBook confirmation"
        presence = self.secrets.presence()
        selected_configured = bool(presence.get("betfair_live_app_key" if requested == "live" else "betfair_app_key"))
        if not selected_configured:
            runtime.effective_feed_entitlement = "unavailable"
            runtime.feed_reason = f"{requested.title()} App Key not configured"
        elif requested == "delayed" and runtime.effective_feed_entitlement not in {"live", "delayed"}:
            runtime.effective_feed_entitlement = "delayed"
            runtime.feed_reason = "Delayed App Key configured; awaiting fresh market observation"
        elif requested == "live" and runtime.effective_feed_entitlement not in {"live", "delayed"}:
            runtime.effective_feed_entitlement = "unknown"
            runtime.feed_reason = "Awaiting Betfair MarketBook confirmation"
        result = self.betfair_feed_status()
        result["message"] = f"Betfair requested market feed set to {requested.upper()}. Effective feed remains provider-derived."
        result["state"] = self.get_state()
        return result

    def set_feed_enabled(self, data=None):
        """Compatibility wrapper: explicit mode is required in 0.9.36."""
        data = data or {}
        mode = str(data.get("mode") or "").strip().lower()
        if mode not in {"sim","live"}:
            return {"ok": False, "message": "Feed mode must be SIM or LIVE", "state": self.get_state()}
        return self.update_venue_control({
            "provider_id": data.get("exchange") or data.get("provider_id"),
            f"{mode}_feed_enabled": bool(data.get("enabled")),
        })

    def venue_controls(self, data=None):
        data = data or {}
        selected_mode = canonical_mode_value(data.get("mode") or self.db.get_setting("data_context_mode", self.db.get_setting("mode", "sim")))
        return {"ok": True, "rows": self.db.venue_controls(), "operations": self._operational_status(selected_mode), "live_execution_locked": True}

    def update_venue_control(self, data=None):
        data = data or {}
        provider_id = str(data.get("provider_id") or data.get("exchange") or "").strip().lower()
        if self.provider_runtime.providers.get(provider_id) is None:
            return {"ok": False, "message": "Unknown provider/venue"}
        allowed = ("account_nickname","sim_feed_enabled","live_feed_enabled","sim_account_enabled","live_account_enabled","live_execution_enabled")
        changes = {k:data.get(k) for k in allowed if k in data}
        try:
            row = self.db.update_venue_control(provider_id, **changes)
            if "sim_feed_enabled" in changes or "live_feed_enabled" in changes:
                physical = bool(row.get("sim_feed_enabled") or row.get("live_feed_enabled"))
                cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
                cfg[f"{provider_id}_enabled"] = physical
                self.db.set_setting("config", cfg)
                self.provider_runtime.set_runtime_enabled(provider_id, physical)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        selected_mode = canonical_mode_value(data.get("mode") or self.db.get_setting("data_context_mode", self.db.get_setting("mode", "sim")))
        return {"ok": True, "venue": row, "operations": self._operational_status(selected_mode), "live_execution_locked": True}

    def clear_secret(self, key):
        if key not in {"matchbook_password", "matchbook_session_token", "betfair_app_key", "betfair_live_app_key", "betfair_session_token"}:
            return {"ok": False, "message": "Unknown secret"}
        self.secrets.set(key, "")
        self.live_providers.invalidate_account_providers("betfair" if str(key).startswith("betfair_") else "matchbook")
        return {"ok": True, "state": self.get_state()}

    def matchbook_login(self, data=None):
        data = data or {}
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        username = str(data.get("username") or cfg.get("matchbook_username") or self.secrets.get("matchbook_username") or "").strip()
        password = str(data.get("password") or self.secrets.get("matchbook_password") or "")
        mfa_code = str(data.get("mfa_code") or "").strip() or None
        if not username or not password:
            return {"ok": False, "message": "Enter Matchbook username and password first."}
        try:
            adapter = MatchbookAdapter(username=username, password=password, mfa_code=mfa_code)
            token = asyncio.run(adapter.login())
            cfg["matchbook_username"] = username
            self.db.set_setting("config", cfg)
            self.secrets.set_many({
                "matchbook_username": username,
                "matchbook_password": password,
                "matchbook_session_token": token,
            })
            self.live_providers.invalidate_account_providers("matchbook")
            return {"ok": True, "message": f"Matchbook session created and saved to {self.secrets.secrets_path}.", "state": self.get_state()}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def prepare_secrets_file(self):
        try:
            path = self.secrets.ensure_file()
            return {
                "ok": True,
                "message": f"Secrets file ready: {path}",
                "path": str(path),
                "state": self.get_state(),
            }
        except Exception as exc:
            return {"ok": False, "message": str(exc), "state": self.get_state()}

    def migrate_legacy_keychain(self):
        result = self.secrets.import_legacy_keychain()
        if result.get("imported"):
            names = ", ".join(str(x) for x in result["imported"])
            message = f"Imported legacy Keychain credentials into {self.secrets.secrets_path}: {names}. Normal scans will not open Keychain again."
            ok = True
        elif result.get("errors"):
            message = "No credentials imported. " + "; ".join(str(x) for x in result["errors"])
            ok = False
        else:
            message = "No legacy ArbScanner credentials were found in Keychain."
            ok = False
        return {"ok": ok, "message": message, "result": result, "state": self.get_state()}

    def test_connections(self):
        return {"ok": True, "statuses": self.scanner.test_connections(), "state": self.get_state()}

    def ensure_scanner_running(self, data=None):
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        cfg["scanner_enabled"] = True
        self.db.set_setting("config", cfg)
        worker = self._ensure_worker()
        return {
            "ok": bool(worker.get("ok")),
            "message": worker.get("message") or ("Scanner is running." if worker.get("ok") else "Could not load scanner worker."),
            "state": self.get_state(),
        }

    def set_data_context_mode(self, data=None):
        """Persist the global SIM/LIVE data context without unlocking execution.

        0.9.4 made the shell switch immediate in the browser. 0.9.8 mirrors that
        context to SQLite so the background scanner can route decisions into the
        correct evidence sink. This method never changes ``mode`` to LIVE and
        never enables an ExecutionProvider.
        """
        data = data or {}
        requested = canonical_mode_value(data.get("mode") if isinstance(data, dict) else data)
        if requested not in {"sim", "live"}:
            return {"ok": False, "message": "Data context must be SIM or LIVE."}
        try:
            generation = max(0, int(data.get("generation") or 0)) if isinstance(data, dict) else 0
        except (TypeError, ValueError):
            generation = 0
        stored_generation = int(self.db.get_setting("data_context_generation", 0) or 0)
        if generation and generation < stored_generation:
            current = canonical_mode_value(self.db.get_setting("data_context_mode", "sim"))
            return {
                "ok": True, "stale_request": True, "mode": current, "data_context_mode": current,
                "generation": stored_generation,
                "economic_execution_mode": canonical_mode_value(self.db.get_setting("mode", "sim")),
                "live_execution_allowed": False, "orders_write_capability": False,
            }
        self.db.set_setting("data_context_mode", requested)
        if generation:
            self.db.set_setting("data_context_generation", generation)
        return {
            "ok": True, "mode": requested, "data_context_mode": requested, "generation": generation or stored_generation,
            "economic_execution_mode": canonical_mode_value(self.db.get_setting("mode", "sim")),
            "live_execution_allowed": False, "orders_write_capability": False,
        }

    def set_operating_mode(self, data=None):
        data = data or {}
        raw = data.get("mode") if isinstance(data, dict) else data
        requested = canonical_mode_value(raw or "sim")
        if requested not in OPERATING_MODES:
            return {"ok": False, "message": "Mode must be SIM or LIVE.", "state": self.get_state()}
        if not OPERATING_MODES[requested]["available"]:
            return {
                "ok": False,
                "message": "LIVE is intentionally locked: this build contains no real venue order-placement path.",
                "requested_mode": requested,
                "state": self.get_state(),
            }
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        cfg["scanner_enabled"] = True
        self.db.set_setting("config", cfg)
        self.db.set_setting("mode", requested)
        worker = self._ensure_worker()
        message = "SIM active." if requested == "sim" else "LIVE active."
        if not worker.get("ok"):
            message += " The background worker could not be confirmed."
        return {"ok": True, "mode": requested, "message": message, "state": self.get_state()}

    # Legacy aliases kept so older packaged front-ends can still connect safely.
    def activate_monitor_timing(self, data=None):
        return self.set_operating_mode({"mode": "sim"})

    def stop_monitor_timing(self, data=None):
        return self.set_operating_mode({"mode": "sim"})

    def start_automation(self, data=None):
        return self.set_operating_mode({"mode": self._job_mode(data) if isinstance(data, dict) else "sim"})

    def stop_automation(self, data=None):
        return self.set_operating_mode({"mode": "sim"})

    def _job_mode(self, data=None) -> str:
        data = data or {}
        raw = str(data.get("mode") or data.get("action") or self.db.get_setting("mode", "sim") or "sim").strip().lower()
        return canonical_mode_value(raw)

    def _snapshot_strategy(self, overrides=None) -> dict:
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        for k, v in (overrides or {}).items():
            if k in DEFAULT_CONFIG and k != "scanner_enabled":
                cfg[k] = v
        cfg["scanner_enabled"] = False
        return cfg

    def _ensure_worker(self):
        bg = self.service.status()
        if bg.get("loaded"):
            return {"ok": True, **bg}
        return self.service.install()

    def activate_job(self, data=None):
        data = data or {}
        requested = self._job_mode(data)
        if requested not in OPERATING_MODES:
            return {"ok": False, "message": "Choose Find opportunities, Simulate betting or Place real bets.", "state": self.get_state()}
        if not OPERATING_MODES[requested]["available"]:
            return {"ok": False, "message": "Real betting is still locked until live order-placement and reconciliation are implemented.", "state": self.get_state()}
        if self.db.active_job():
            return {"ok": False, "message": "A job is already running. Stop it before activating another run.", "state": self.get_state()}
        strategy = self._snapshot_strategy(data.get("strategy") or {})
        # Treat the rules shown on the run screen as the new defaults too.
        persisted = {**strategy, "scanner_enabled": True}
        self.db.set_setting("config", persisted)
        self.db.set_setting("mode", requested)
        worker = self._ensure_worker()
        if not worker.get("ok"):
            persisted["scanner_enabled"] = False
            self.db.set_setting("config", persisted)
            return {"ok": False, "message": worker.get("message") or "Could not start background worker.", "state": self.get_state()}
        name = str(data.get("name") or f"{OPERATING_MODES[requested]['label'].title()} run").strip()[:120]
        duration = data.get("duration_minutes")
        try:
            duration = int(duration) if duration not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            duration = None
        job_id = self.db.create_job(name, requested, strategy, trigger_type="manual", duration_minutes=duration, start_now=True)
        now = datetime.now(timezone.utc).isoformat()
        self.db.set_setting("automation_runtime", {"started_at": now, "stopped_at": None, "mode": requested, "job_id": job_id})
        return {"ok": True, "job_id": job_id, "message": f"Job #{job_id} activated: {name}.", "state": self.get_state()}

    def schedule_job(self, data=None):
        data = data or {}
        requested = self._job_mode(data)
        if requested not in OPERATING_MODES:
            return {"ok": False, "message": "Choose Find opportunities, Simulate betting or Place real bets.", "state": self.get_state()}
        if not OPERATING_MODES[requested]["available"]:
            return {"ok": False, "message": "Real betting schedules are locked until LIVE execution is implemented.", "state": self.get_state()}
        first_run_at = str(data.get("start_at") or "").strip()
        if not self._parse_utc(first_run_at):
            return {"ok": False, "message": "Choose a valid schedule start time.", "state": self.get_state()}
        try:
            duration = min(24 * 60, max(1, int(data.get("duration_minutes") or 60)))
        except (TypeError, ValueError):
            duration = 60
        recurrence = str(data.get("recurrence") or "once").strip().lower()
        if recurrence not in {"once", "daily", "weekdays"}:
            recurrence = "once"
        timezone_name = str(data.get("timezone_name") or "UTC").strip() or "UTC"
        strategy = self._snapshot_strategy(data.get("strategy") or {})
        persisted = {**strategy, "scanner_enabled": bool(self.db.active_job())}
        self.db.set_setting("config", persisted)
        self.db.set_setting("mode", requested)
        worker = self._ensure_worker()
        if not worker.get("ok"):
            return {"ok": False, "message": worker.get("message") or "Could not load the background worker for scheduling.", "state": self.get_state()}
        name = str(data.get("name") or f"Scheduled {OPERATING_MODES[requested]['label'].title()}").strip()[:120]
        try:
            schedule_id = self.db.create_schedule(name, requested, strategy, first_run_at, duration, recurrence, timezone_name)
        except Exception as exc:
            return {"ok": False, "message": str(exc), "state": self.get_state()}
        return {"ok": True, "schedule_id": schedule_id, "message": f"Schedule #{schedule_id} saved.", "state": self.get_state()}

    def stop_job(self, data=None):
        job = self.db.active_job()
        mode = str((job or {}).get("mode") or self.db.get_setting("mode", "watch") or "watch").lower()
        if not job:
            cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
            cfg["scanner_enabled"] = False
            self.db.set_setting("config", cfg)
            return {"ok": True, "message": "No job is currently running.", "state": self.get_state()}
        self.db.finish_job(int(job["id"]), "stopped", "user stop")
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        cfg = {**DEFAULT_CONFIG, **(cfg or {})}
        cfg["scanner_enabled"] = False
        self.db.set_setting("config", cfg)
        meta = self.db.get_setting("automation_runtime", {}) or {}
        meta["stopped_at"] = datetime.now(timezone.utc).isoformat()
        meta["mode"] = mode
        self.db.set_setting("automation_runtime", meta)
        if mode == "live":
            message = "LIVE stop requested: new opportunities are blocked. Reconciliation/hedging must remain active until exposure is flat."
        else:
            message = f"Job #{job['id']} stopped. No new opportunities will be created for this run."
        return {"ok": True, "message": message, "state": self.get_state()}

    def jobs_history(self, data=None):
        data = data or {}
        try: limit = min(500, max(1, int(data.get("limit") or 100)))
        except Exception: limit = 100
        return {"ok": True, "jobs": self.db.job_history(limit), "schedules": self.db.schedules(limit)}

    def _monitor_timing_scan_summary(self, found):
        rows = []
        errors = []
        for item in found or []:
            monitor_timing = item.get("monitor_timing_execution") or {}
            if monitor_timing.get("error"):
                errors.append({"opportunity_id": item.get("id"), "error": monitor_timing.get("error")})
                continue
            if not monitor_timing.get("run_id"):
                continue
            rows.append({
                "opportunity_id": item.get("id"),
                "event": item.get("event"),
                "market": item.get("market"),
                "reference_checkpoint_ms": int(monitor_timing.get("reference_checkpoint_ms") or 250),
                "reference_executable": bool(monitor_timing.get("reference_executable")),
                "reference_profit": float(monitor_timing.get("reference_profit") or 0.0),
                "survived_through_ms": int(monitor_timing.get("survived_through_ms") or 0),
                "first_failure_reason": monitor_timing.get("first_failure_reason"),
            })
        return {
            "tested": len(rows),
            "survived_reference": sum(1 for x in rows if x.get("reference_executable")),
            "reference_profit": round(sum(float(x.get("reference_profit") or 0.0) for x in rows), 4),
            "median_survived_through_ms": (
                round(statistics.median([int(x.get("survived_through_ms") or 0) for x in rows]), 2) if rows else 0.0
            ),
            "rows": rows,
            "errors": errors,
            "note": "Fresh timed Monitor market rechecks; no real orders are placed.",
        }

    def run_scan_now(self, data=None):
        """Run one manual scan in a frozen SIM/LIVE data context.

        The browser passes its mode generation so an out-of-order click cannot
        route a LIVE manual scan through the SIM opportunity/economic sink (or
        vice versa). This only selects the read/decision sink; economic LIVE
        execution remains locked and no provider order method is reachable.
        """
        data = data or {}
        mode = canonical_mode_value(self.db.get_setting("mode", "sim"))
        requested = data.get("data_context_mode") or data.get("mode")
        if requested is not None:
            ctx = self.set_data_context_mode({"mode": requested, "generation": data.get("generation") or 0})
            if not ctx.get("ok"):
                ctx["state"] = self.get_state()
                return ctx
            requested_mode = canonical_mode_value(requested)
            data_mode = canonical_mode_value(ctx.get("data_context_mode", requested_mode))
            if ctx.get("stale_request") and data_mode != requested_mode:
                return {
                    "ok": False, "stale_request": True, "message": "Manual scan cancelled because the global SIM/LIVE context changed.",
                    "data_context_mode": data_mode, "operating_mode": mode,
                    "live_execution_allowed": False, "orders_write_capability": False, "state": self.get_state(),
                }
        else:
            data_mode = canonical_mode_value(self.db.get_setting("data_context_mode", mode))
        result = self.scanner.scan_once(job_id=None, data_context_mode=data_mode)
        result["operating_mode"] = mode
        result["data_context_mode"] = data_mode
        if result.get("ok") and data_mode == "sim":
            result["monitor_execution"] = self._monitor_timing_scan_summary(result.get("found") or [])
        elif result.get("ok") and data_mode == "live":
            result["live_decision_summary"] = self.db.live_decision_summary().get("summary") or {}
        result["state"] = self.get_state()
        return result

    def run_discovery_now(self):
        """Refresh the market universe/matching cache without placing any orders."""
        result = self.scanner.discover_once(job_id=None)
        result["state"] = self.get_state()
        return result

    # --- 0.9.36 Engines ----------------------------------------------------------

    def engines(self, data=None):
        data = data or {}
        section = str(data.get("section") or "").strip().lower() or None
        include_reference = bool(data.get("include_reference") or data.get("include_research_test"))
        rows = self.db.engine_instances(section=section)
        type_meta = {x["engine_type"]: x for x in self.scanner.engine_runtime.registry.types()}
        visible = []
        for source in rows:
            iid = str(source["engine_instance_id"])
            row = engine_catalog_row(source, type_meta.get(str(source.get("engine_type"))), self.db.engine_performance(iid))
            if engine_catalog_visible(row, include_reference=include_reference):
                visible.append(row)
        return {"ok": True, "rows": visible, "section": section, "live_execution_locked": True,
                "reference_hidden": not include_reference, "hidden_count": len(rows)-len(visible)}

    def engine_detail(self, data=None):
        data = data or {}
        iid = str(data.get("engine_instance_id") or data.get("id") or "").strip()
        row = self.db.engine_instance(iid) if iid else None
        if not row:
            return {"ok": False, "message": "Engine instance was not found."}
        row["config_history"] = self.db.engine_config_history(iid)
        row["performance"] = self.db.engine_performance(iid)
        row["recent_decisions"] = self.db.engine_recent_decisions(iid, limit=max(1, min(100, int(data.get("limit") or 30))))
        return {"ok": True, "engine": row, "live_execution_locked": True}

    def engine_set_route(self, data=None):
        data = data or {}
        iid = str(data.get("engine_instance_id") or "").strip()
        if not iid:
            return {"ok": False, "message": "engine_instance_id is required."}
        try:
            row = self.db.engine_set_route(
                iid, section=data.get("section"), sport=data.get("sport"),
                competition=data.get("competition"), market_type=data.get("market_type"),
            )
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "engine": row}

    def engine_set_lifecycle(self, data=None):
        data = data or {}
        iid = str(data.get("engine_instance_id") or "").strip()
        requested = str(data.get("requested_lifecycle") or data.get("lifecycle") or "").upper()
        if not iid or requested not in ENGINE_LIFECYCLES:
            return {"ok": False, "message": "A valid engine instance and SIM/LIVE lifecycle are required."}
        try:
            row = self.db.engine_set_lifecycle(iid, requested)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "engine": row, "live_execution_locked": True}

    def engine_set_mode_enablement(self, data=None):
        data = data or {}
        iid = str(data.get("engine_instance_id") or "").strip()
        mode = str(data.get("mode") or "").strip().lower()
        if not iid or mode not in {"sim", "live"}:
            return {"ok": False, "message": "engine_instance_id and mode SIM/LIVE are required."}
        try:
            row = self.db.engine_set_mode_enablement(iid, mode, bool(data.get("enabled")))
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "engine": row, "live_execution_locked": True}

    def engine_create_config(self, data=None):
        data = data or {}
        iid = str(data.get("engine_instance_id") or "").strip()
        config = data.get("config")
        if not iid or not isinstance(config, dict):
            return {"ok": False, "message": "engine_instance_id and config are required."}
        try:
            row = self.db.engine_create_config(iid, config, activate=bool(data.get("activate", True)))
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "config": row}

    def engine_clone(self, data=None):
        data = data or {}
        source = str(data.get("source_engine_instance_id") or data.get("engine_instance_id") or "").strip()
        target = str(data.get("new_engine_instance_id") or data.get("new_id") or "").strip()
        if not source or not target:
            return {"ok": False, "message": "Source and new engine instance IDs are required."}
        try:
            row = self.db.engine_clone(source, target, requested_lifecycle=str(data.get("requested_lifecycle") or "DISABLED"),
                                       engine_grade=str(data.get("engine_grade") or "RESEARCH"))
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "engine": row}

    def engine_set_grade(self, data=None):
        data = data or {}
        iid = str(data.get("engine_instance_id") or "").strip()
        try:
            row = self.db.engine_set_grade(iid, str(data.get("engine_grade") or data.get("grade") or ""))
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "engine": row}

    def engine_update_metadata(self, data=None):
        data = data or {}
        iid = str(data.get("engine_instance_id") or "").strip()
        if not iid:
            return {"ok": False, "message": "engine_instance_id is required."}
        try:
            row = self.db.engine_update_metadata(iid, nickname=data.get("nickname"), description=data.get("description"), notes=data.get("notes"))
        except KeyError:
            return {"ok": False, "message": "Engine instance was not found."}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "engine": row}

    def engine_export_package(self, data=None):
        data = data or {}
        iid = str(data.get("engine_instance_id") or "").strip()
        engine = self.db.engine_instance(iid) if iid else None
        if not engine:
            return {"ok": False, "message": "Engine instance was not found."}
        meta = next((x for x in self.scanner.engine_runtime.registry.types() if x["engine_type"] == engine.get("engine_type")), None)
        if not meta:
            return {"ok": False, "message": "Engine type is not currently registered."}
        source = None
        try:
            candidate = Path(str(engine.get("package_source") or ""))
            if candidate.suffix == ".arbengine" and candidate.exists():
                source = candidate
        except Exception:
            source = None
        raw = build_export_package(engine=engine, type_meta=meta, source_package=source)
        filename = f"{str(engine.get('engine_instance_id') or engine.get('engine_type')).strip().upper()}-{str(engine.get('engine_version') or '1.0.0')}.arbengine"
        path = downloads_dir() / filename
        path.write_bytes(raw)
        return {"ok": True, "path": str(path), "filename": filename, "bytes": len(raw),
                "sha256": __import__('hashlib').sha256(raw).hexdigest()}

    def engine_validate_package(self, data=None):
        """Quarantine and statically validate an uploaded .arbengine without executing it."""
        data = data or {}
        encoded = str(data.get("package_base64") or "").strip()
        filename = str(data.get("filename") or "uploaded.arbengine").strip() or "uploaded.arbengine"
        if not encoded:
            return {"ok": False, "message": "package_base64 is required."}
        quarantine_token = None
        try:
            raw = base64.b64decode(encoded, validate=True)
            preview = quarantine_package_bytes(raw, filename=filename)
            quarantine_token = preview.get("token")
            manifest = preview["manifest"]
            target_id = str(manifest.get("engine_instance_id") or "").strip().upper()
            existing = self.db.engine_instance(target_id) if target_id else None
            # An existing engine_type without an explicit matching instance ID is not
            # silently overwritten: that upload remains a New Install candidate.
            install_kind = "UPGRADE_REVIEW" if existing else "NEW_INSTALL"
            if existing and str(existing.get("engine_type") or "").upper() != str(manifest.get("engine_type") or "").upper():
                raise ValueError("Uploaded engine_instance_id belongs to a different installed engine type")
            return {
                "ok": True, "quarantine_token": preview["token"], "manifest": manifest,
                "package_sha256": preview["sha256"], "bytes": preview["bytes"],
                "file_count": preview.get("file_count"), "extracted_bytes": preview.get("extracted_bytes"),
                "filename": preview.get("filename") or filename, "install_kind": install_kind,
                "installed_version": existing.get("engine_version") if existing else None,
                "requires_confirmation": True, "code_executed": False,
                "safety": "Static validation only. Uploaded strategy code has not executed and no dependencies were installed.",
            }
        except Exception as exc:
            if quarantine_token:
                try:
                    remove_quarantined_package(str(quarantine_token))
                except Exception:
                    pass
            return {"ok": False, "message": str(exc), "code_executed": False}

    def engine_import_package(self, data=None):
        """Backward-compatible entry point: import now means quarantine + review, never install."""
        return self.engine_validate_package(data)

    def engine_install_quarantined_package(self, data=None):
        """Install only after an explicit review confirmation. Code may load only at this point."""
        data = data or {}
        token = str(data.get("quarantine_token") or "").strip()
        if not token:
            return {"ok": False, "message": "quarantine_token is required."}
        if data.get("confirm") is not True:
            return {"ok": False, "message": "Explicit install confirmation is required."}
        installed_path = None
        old_registry = self.scanner.engine_runtime.registry
        try:
            raw, preview = read_quarantined_package(token)
            manifest = preview["manifest"]
            target_id = str(manifest.get("engine_instance_id") or "").strip().upper()
            existing = self.db.engine_instance(target_id) if target_id else None
            if existing and str(existing.get("engine_type") or "").upper() != str(manifest.get("engine_type") or "").upper():
                raise ValueError("Upgrade engine type does not match the installed instance")
            if existing:
                active_cfg = dict((existing.get("active_config") or {}).get("config") or {})
                schema = dict(manifest.get("config_schema") or {})
                missing = sorted(set(active_cfg) - set(schema))
                if missing:
                    raise ValueError("Upgrade configuration is incompatible; missing schema keys: " + ", ".join(missing))

            # Runtime validation executes reviewed restricted code only after explicit
            # confirmation, and before the package can replace anything in the persistent store.
            # Built-in metadata exports contain no uploaded strategy source and therefore
            # retain the built-in class rather than executing package code.
            if manifest.get("implementation_kind") == "restricted_python":
                _reviewed_cls, reviewed_info = load_reviewed_engine_class(raw)
                prior_type = next((x for x in old_registry.types() if x["engine_type"] == manifest["engine_type"]), None)
                if prior_type and not prior_type.get("package_origin"):
                    raise ValueError("Uploaded packages may not override a built-in engine type")
                if str(reviewed_info.get("sha256") or "") != str(preview.get("sha256") or ""):
                    raise ValueError("Reviewed package checksum changed before installation")

            installed = install_package_bytes(raw)
            installed_path = Path(installed["path"])
            fresh_registry = EngineRegistry()
            registered = next((x for x in fresh_registry.types() if x["engine_type"] == manifest["engine_type"]), None)
            if not registered:
                raise ValueError("Package installed but its engine type could not be registered on this ArbScanner build")
            if str(registered.get("package_sha256") or "") and str(registered.get("package_sha256")) != str(installed["sha256"]):
                raise ValueError("Installed engine checksum did not match the reviewed package")
            # Uploaded packages may never replace built-in/core engine types.
            if manifest.get("implementation_kind") == "restricted_python" and not registered.get("package_origin"):
                raise ValueError("Uploaded packages may not override a built-in engine type")

            self.scanner.engine_runtime.registry = fresh_registry
            if existing:
                row = self.db.engine_upgrade_package_instance(target_id, manifest, package_path=installed["path"], package_sha256=installed["sha256"])
                install_kind = "UPGRADE_REVIEW"
            else:
                row = self.db.engine_install_package_instance(manifest, package_path=installed["path"], package_sha256=installed["sha256"])
                install_kind = "NEW_INSTALL"
            remove_quarantined_package(token)
            return {
                "ok": True, "engine": row, "manifest": manifest, "package_sha256": installed["sha256"],
                "install_kind": install_kind, "code_executed_during_validation": False,
                "safety": "Installed after explicit review. New engines remain DISABLED until the operator enables evaluation.",
            }
        except Exception as exc:
            self.scanner.engine_runtime.registry = old_registry
            # If a new package file was written but runtime validation failed, remove
            # that exact reviewed artifact; prior versions remain untouched.
            if installed_path is not None:
                try:
                    installed_path.unlink()
                except OSError:
                    pass
            return {"ok": False, "message": str(exc)}

    def engine_cancel_quarantined_package(self, data=None):
        token = str((data or {}).get("quarantine_token") or "").strip()
        if not token:
            return {"ok": False, "message": "quarantine_token is required."}
        try:
            remove_quarantined_package(token)
        except Exception as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True}

    def engine_experiments(self, data=None):
        data = data or {}
        return {"ok": True, "rows": self.db.engine_experiments(section=data.get("section"))}

    def engine_create_experiment(self, data=None):
        data = data or {}
        source = str(data.get("source_engine_instance_id") or data.get("engine_instance_id") or "").strip()
        target = str(data.get("new_engine_instance_id") or data.get("new_id") or "").strip()
        if not source or not target:
            return {"ok": False, "message": "Source and new engine instance IDs are required."}
        overrides = data.get("config_overrides") or {}
        if not isinstance(overrides, dict):
            return {"ok": False, "message": "config_overrides must be an object."}
        try:
            row = self.db.engine_create_experiment(source, target, config_overrides=overrides, notes=data.get("notes"))
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "experiment": row, "engine": self.db.engine_instance(row["engine_instance_id"])}

    def engine_create_sweep(self, data=None):
        """Create a bounded set of RESEARCH engine variants; execution is separate."""
        from itertools import product
        data = data or {}
        source = str(data.get("source_engine_instance_id") or data.get("engine_instance_id") or "").strip()
        grid = data.get("grid") or {}
        if not source or not isinstance(grid, dict) or not grid:
            return {"ok": False, "message": "source_engine_instance_id and a non-empty grid are required."}
        keys = list(grid)
        values = []
        for key in keys:
            items = grid[key]
            if not isinstance(items, list) or not items:
                return {"ok": False, "message": f"Sweep parameter {key} must contain one or more values."}
            values.append(items)
        total = 1
        for items in values:
            total *= len(items)
        platform = self.db.get_setting("config", {}) or {}
        limit = max(1, min(500, int(platform.get("engine_experiment_variant_limit", 100) or 100)))
        if total > limit:
            return {"ok": False, "status": "TOO_MANY_VARIANTS", "requested_variants": total, "maximum_variants": limit,
                    "message": f"Experiment requires {total} engine variants; limit is {limit}. Narrow the parameter ranges."}
        source_row = self.db.engine_instance(source)
        if not source_row:
            return {"ok": False, "message": "Source engine was not found."}
        base = dict((source_row.get("active_config") or {}).get("config") or {})
        registry = self.scanner.engine_runtime.registry
        planned = []
        prefix = str(data.get("prefix") or (source + "_SWEEP")).strip().upper()
        for idx, combo in enumerate(product(*values), 1):
            overrides = dict(zip(keys, combo))
            candidate = dict(base); candidate.update(overrides)
            try:
                registry.validate_config(str(source_row["engine_type"]), candidate)
            except (KeyError, ValueError) as exc:
                return {"ok": False, "message": f"Variant {idx} is invalid: {exc}"}
            planned.append((f"{prefix}_{idx:03d}", overrides))
        created = []
        try:
            for target, overrides in planned:
                created.append(self.db.engine_create_experiment(source, target, config_overrides=overrides, notes="parameter sweep"))
        except (KeyError, ValueError) as exc:
            return {"ok": False, "message": str(exc), "created": created}
        return {"ok": True, "requested_variants": total, "maximum_variants": limit, "rows": created}

    def engine_decisions(self, data=None):
        data = data or {}
        iid = str(data.get("engine_instance_id") or "").strip() or None
        return {"ok": True, "rows": self.db.engine_recent_decisions(iid, limit=max(1, min(1000, int(data.get("limit") or 100))))}

    def engine_lifecycle(self, data=None):
        """Shared Sports/Racing engine lifecycle projection with mode-owned semantics."""
        data = data or {}
        section = operator_domain(data.get("section"))
        mode = str(data.get("mode") or "sim").lower()
        if mode not in {"sim", "live"}:
            return {"ok": False, "message": "mode must be sim or live"}
        sports = data.get("sports") if isinstance(data.get("sports"), list) else None
        common = dict(
            section=section, mode=mode, from_utc=data.get("from_utc"), to_utc=data.get("to_utc"),
            stream=str(data.get("stream") or data.get("phase") or "all").lower(),
            market=str(data.get("market") or ""), search=str(data.get("search") or ""),
            venue=str(data.get("venue") or "all"), account=str(data.get("account") or "all"),
        )
        if sports and section == "sports":
            groups = [
                self.db.engine_lifecycle_rows(sport=str(sport_name), **common)
                for sport_name in sports if str(sport_name).strip()
            ]
            rows = merge_engine_lifecycle_groups(groups)
        else:
            rows = self.db.engine_lifecycle_rows(sport=str(data.get("sport") or "all"), **common)
        rows, totals = project_engine_lifecycle(rows, mode=mode, engine_filter=str(data.get("engine") or "all"))
        return {"ok": True, "section": section, "mode": mode, "rows": rows, "totals": totals,
                "live_execution_locked": True, "provenance_rule": "originating engine only; legacy unverified rows are not attributed"}

    @staticmethod
    def _engine_evidence_from_row(row: dict, *, observed_at: str | None = None) -> MarketEvidence | None:
        raw_legs = row.get("legs_json") or row.get("legs") or []
        if isinstance(raw_legs, str):
            try:
                raw_legs = json.loads(raw_legs or "[]")
            except Exception:
                raw_legs = []
        if not isinstance(raw_legs, list) or not raw_legs:
            return None
        fields = set(Leg.__dataclass_fields__)
        candidates: dict[str, list[Leg]] = {}
        for item in raw_legs:
            if not isinstance(item, dict):
                continue
            payload = {k: v for k, v in item.items() if k in fields}
            try:
                leg = Leg(**payload)
            except Exception:
                continue
            candidates.setdefault(str(leg.selection), []).append(leg)
        if len(candidates) < 2:
            return None
        market = SimpleNamespace(
            canonical_event_id=row.get("event_key") or row.get("event_id"), event_key=row.get("event_key") or row.get("event_id") or "",
            canonical_market_id=row.get("market_id") or row.get("market_name"), display_market=row.get("market_name") or "",
            display_event=row.get("event_name") or row.get("event") or "", start_time=row.get("event_start"),
            section=row.get("section") or "sports", sport=row.get("sport") or "Unknown", competition=row.get("competition"),
            strategy=row.get("strategy") or "two-way", status=row.get("event_status") or row.get("market_status"),
            in_play=None if row.get("in_play") is None else bool(row.get("in_play")),
            canonical_market_type=row.get("market_type") or row.get("market_name") or "Unknown",
        )
        feed_generation = str(row.get("book_revision") or row.get("feed_generation") or "historical")
        return MarketEvidence.from_candidates(market, candidates, feed_generation=feed_generation, observed_at=observed_at or row.get("observed_at") or row.get("detected_at"))

    def engine_scenario_compare(self, data=None):
        """Run one canonical historical opportunity through selected engines.

        This is a research path: a disabled engine may be selected explicitly, but
        the run is forced into SIM and cannot affect its operational lifecycle.
        """
        data = data or {}
        try:
            opportunity_id = int(data.get("opportunity_id") or 0)
        except (TypeError, ValueError):
            opportunity_id = 0
        if opportunity_id <= 0:
            return {"ok": False, "message": "A valid opportunity_id is required."}
        row = self.db.opportunity_by_id(opportunity_id, include_demo=bool(data.get("include_demo", False)))
        if not row:
            return {"ok": False, "message": "Opportunity was not found."}
        evidence = self._engine_evidence_from_row(row, observed_at=row.get("detected_at"))
        if evidence is None:
            return {"ok": False, "message": "Opportunity does not contain complete canonical leg evidence."}
        selected = [str(x) for x in (data.get("engine_instance_ids") or []) if str(x).strip()]
        simulation_level = str(data.get("simulation_level") or "DECISION_SIM").upper()
        results = self.scanner.engine_runtime.evaluate(
            evidence, instance_ids=selected or None, evaluation_timestamp=str(row.get("detected_at") or evidence.observed_at),
            mode_override="sim", persist=False, research_mode=True,
        )
        scenario_id = str(data.get("scenario_id") or f"OPPORTUNITY_{opportunity_id}")
        scenario_version = max(1, int(data.get("scenario_version") or 1))
        out = []
        for result in results:
            run_id = stable_hash({"scenario": scenario_id, "version": scenario_version, "engine": result.context.engine_instance_id,
                                  "snapshot": evidence.market_snapshot_id, "config": result.context.config_hash, "level": simulation_level})[:32]
            self.db.engine_record_scenario_run(run_id=run_id, scenario_id=scenario_id, scenario_version=scenario_version,
                                               result=result, evidence=evidence, simulation_level=simulation_level)
            d = result.decision
            out.append({
                "run_id": run_id, "engine_instance_id": result.context.engine_instance_id, "engine_type": result.context.engine_type,
                "engine_version": result.context.engine_version, "engine_grade": result.context.engine_grade, "config_version": result.context.config_version,
                "config_hash": result.context.config_hash, "simulation_level": simulation_level,
                "intent_type": d.intent_type if d else None, "capabilities": list(d.capabilities) if d else [],
                "opportunity_identified": bool(d), "requested_stake": float(d.requested_stake if d else 0.0),
                "requested_capital": float(d.requested_capital if d else 0.0), "expected_profit": float(d.expected_profit or 0.0) if d else 0.0,
                "expected_edge": float(d.expected_edge or 0.0) if d else 0.0, "strategy_metrics": dict(d.strategy_metrics) if d else {}, "error": result.error,
            })
        return {"ok": True, "scenario_id": scenario_id, "scenario_version": scenario_version,
                "market_snapshot_id": evidence.market_snapshot_id, "input_observed_at": evidence.observed_at, "rows": out}

    def engine_replay_compare(self, data=None):
        """Compare engines against canonical archive/hot-SQLite evidence in time order."""
        data = data or {}
        from_utc, to_utc = data.get("from_utc"), data.get("to_utc")
        if not from_utc or not to_utc:
            return {"ok": False, "message": "from_utc and to_utc are required."}
        history = self.analytics_store.detailed_history(
            from_utc, to_utc, limit=max(1, min(10000, int(data.get("limit") or 2000))), allow_partial=False,
            section=data.get("section") or data.get("scope"), sport=data.get("sport"), market=data.get("market"), search=data.get("search"),
        )
        if not history.get("ok"):
            return history
        selected = [str(x) for x in (data.get("engine_instance_ids") or []) if str(x).strip()]
        aggregates: dict[str, dict] = {}
        previous_time = None
        evaluated_rows = 0
        for row in history.get("rows") or []:
            observed = str(row.get("observed_at") or "")
            if previous_time is not None and observed < previous_time:
                return {"ok": False, "message": "Replay evidence ordering is not deterministic."}
            previous_time = observed
            evidence = self._engine_evidence_from_row(row, observed_at=observed)
            if evidence is None:
                continue
            # The engine receives only this snapshot at historical time T; no later
            # row is visible during the evaluation, which is the look-ahead barrier.
            results = self.scanner.engine_runtime.evaluate(
                evidence, instance_ids=selected or None, evaluation_timestamp=observed,
                mode_override="sim", persist=False, research_mode=True,
            )
            evaluated_rows += 1
            for result in results:
                key = result.context.engine_instance_id
                agg = aggregates.setdefault(key, {
                    "engine_instance_id": key, "engine_type": result.context.engine_type, "engine_version": result.context.engine_version,
                    "engine_grade": result.context.engine_grade, "config_version": result.context.config_version, "config_hash": result.context.config_hash,
                    "evaluations": 0, "decisions": 0, "expected_profit": 0.0, "requested_capital": 0.0, "turnover": 0.0,
                })
                agg["evaluations"] += 1
                if result.decision:
                    agg["decisions"] += 1
                    agg["expected_profit"] += float(result.decision.expected_profit or 0.0)
                    agg["requested_capital"] += float(result.decision.requested_capital or 0.0)
                    agg["turnover"] += float(result.decision.requested_stake or 0.0)
        for agg in aggregates.values():
            capital = float(agg["requested_capital"] or 0.0)
            agg["capital_deployed"] = capital
            agg["net_pnl"] = float(agg["expected_profit"] or 0.0)
            agg["profit_on_capital_pct"] = (float(agg["expected_profit"]) / capital * 100.0) if capital > 0 else 0.0
            agg["roi_pct"] = agg["profit_on_capital_pct"]
            agg["decision_rate_pct"] = (float(agg["decisions"]) / float(agg["evaluations"]) * 100.0) if agg["evaluations"] else 0.0
        cohort_hash = stable_hash({"from": history.get("from_utc"), "to": history.get("to_utc"), "archive": history.get("archive_hours") or [],
                                   "sqlite": history.get("sqlite_hours") or [], "rows": evaluated_rows})[:32]
        experiment_id = str(data.get("experiment_id") or "").strip() or None
        for agg in aggregates.values():
            run_id = stable_hash({"kind": "REPLAY", "cohort": cohort_hash, "engine": agg["engine_instance_id"],
                                  "config": agg["config_hash"], "level": str(data.get("simulation_level") or "DECISION_SIM")})[:32]
            agg["experiment_run_id"] = run_id
            self.db.engine_record_experiment_run(run_id=run_id, experiment_id=experiment_id, engine_instance_id=agg["engine_instance_id"],
                run_type="REPLAY", evidence_from_utc=history.get("from_utc"), evidence_to_utc=history.get("to_utc"),
                evidence_cohort_hash=cohort_hash, simulation_level=str(data.get("simulation_level") or "DECISION_SIM"), status="PASS", metrics=agg)
        return {"ok": True, "from_utc": history.get("from_utc"), "to_utc": history.get("to_utc"),
                "history_source": "verified_parquet+hot_sqlite", "no_lookahead": True, "evidence_cohort_hash": cohort_hash,
                "evaluated_market_rows": evaluated_rows, "rows": list(aggregates.values()),
                "archive_hours": history.get("archive_hours") or [], "sqlite_hours": history.get("sqlite_hours") or []}

    def replay(self, data=None):
        data = data or {}
        capitals = data.get("capitals") or self.db.get_setting("scenarios", [])
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        results = replay_scenarios(self.db, [float(x) for x in capitals], float(cfg.get("max_event_exposure_pct", 100.0)),
                                   include_demo=not bool(cfg.get("hide_demo_data", True)))
        return {"ok": True, "results": results}

    def scenario_capital_sources(self, data=None):
        """Return current SIM exchange totals and market-allocation budgets.

        Stage 04 query boundary: missing wallet authority is reported by omission;
        this read never creates or repairs SIM wallet rows. Startup/explicit
        account commands own wallet initialisation.
        """
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        reserve_pct = {
            "pre_match": self._monitor_reserve_pct(cfg, "pre_match"),
            "in_play": self._monitor_reserve_pct(cfg, "in_play"),
            "racing": self._monitor_reserve_pct(cfg, "racing"),
        }
        wallets = self.db.monitor_wallets_by_stream(reserve_pct)
        account_state = self._monitor_account_state(cfg, capture=False, context="scenario_capital_source")
        accounts = account_state.get("accounts") or {}
        account_balances = {
            str(venue_id): round(float((account or {}).get("equity") or 0.0), 4)
            for venue_id, account in sorted(accounts.items())
        }
        budgets = {}
        for stream in ("pre_match", "in_play", "racing"):
            xs = wallets.get(stream) or {}
            venue_rows = {}
            for venue_id, wallet in sorted(xs.items()):
                equity = float(wallet.get("equity") or 0.0)
                hedge = float(wallet.get("hedge_reserve") or 0.0)
                venue_rows[str(venue_id)] = {
                    "equity": round(equity, 4),
                    "hedge_reserve": round(hedge, 4),
                    "normal_deployable": round(max(0.0, equity - hedge), 4),
                }
            bf = venue_rows.get("betfair") or {}
            mb = venue_rows.get("matchbook") or {}
            budgets[stream] = {
                "venues": venue_rows,
                # Compatibility aliases for the current two-card UI/older clients.
                "betfair": round(float(bf.get("equity") or 0.0), 4),
                "matchbook": round(float(mb.get("equity") or 0.0), 4),
                "betfair_hedge_reserve": round(float(bf.get("hedge_reserve") or 0.0), 4),
                "matchbook_hedge_reserve": round(float(mb.get("hedge_reserve") or 0.0), 4),
                "betfair_normal_deployable": round(float(bf.get("normal_deployable") or 0.0), 4),
                "matchbook_normal_deployable": round(float(mb.get("normal_deployable") or 0.0), 4),
                "hedge_reserve_pct": round(float(reserve_pct[stream]), 4),
                "hedge_reserve": round(sum(float(v.get("hedge_reserve") or 0.0) for v in xs.values()), 4),
                "normal_deployable_allocation": round(sum(max(0.0, float(v.get("equity") or 0.0) - float(v.get("hedge_reserve") or 0.0)) for v in xs.values()), 4),
            }
        return {"ok": True, "currency": str(cfg.get("account_currency", "GBP") or "GBP").upper(),
                "sim_accounts": account_balances, "budgets": budgets,
                "default_source": "sim_accounts", "live_execution_allowed": False}

    def analytics_replay(self, data=None):
        data = data or {}
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        include_demo = not bool(cfg.get("hide_demo_data", True))

        def _list_values(raw):
            if isinstance(raw, (list, tuple, set)):
                values = list(raw)
            elif raw is None:
                values = []
            else:
                values = str(raw).split(",")
            out = []
            for value in values:
                text = str(value or "").strip()
                if text and text.lower() != "all" and text not in out:
                    out.append(text)
            return out
        try:
            requested_capital = min(1_000_000.0, max(1.0, float(data.get("starting_capital") or cfg.get("quality_reference_bankroll", 500.0))))
        except (TypeError, ValueError):
            requested_capital = float(cfg.get("quality_reference_bankroll", 500.0))

        # ``venue_balances`` is canonical in 0.9.0; ``exchange_balances`` remains
        # a compatibility alias. A total-only scenario follows the explicit current
        # SIM allocation proportions, so enabling another provider never silently
        # invents or redistributes actual wallet capital.
        raw_balances = data.get("venue_balances") or data.get("exchange_balances") or {}
        replay_stream = str(data.get("monitor_stream") or "pre_match").lower()
        base_stream = "in_play" if replay_stream == "in_play" else "racing" if replay_stream == "racing" else "pre_match"
        defaults = self._monitor_starting_balances(cfg, base_stream)

        def _clean_balances(values):
            out = {}
            for raw_key, raw_value in (values or {}).items():
                key = provider_id_for_name(str(raw_key or "")) or str(raw_key or "").strip().lower()
                if not key:
                    continue
                try:
                    out[key] = max(0.0, float(raw_value or 0.0))
                except (TypeError, ValueError):
                    out[key] = 0.0
            return out

        try:
            if raw_balances:
                venue_balances = _clean_balances(raw_balances)
            elif data.get("starting_capital") is not None:
                explicit = _clean_balances(defaults)
                base_total = sum(explicit.values())
                if base_total > 1e-9:
                    venue_balances = {key: requested_capital * (value / base_total) for key, value in explicit.items()}
                else:
                    venue_balances = {"unallocated": requested_capital}
            else:
                venue_balances = _clean_balances(defaults)
        except (TypeError, ValueError):
            venue_balances = _clean_balances(defaults)

        if not venue_balances:
            venue_balances = {"unallocated": requested_capital}
        starting_capital = max(0.01, sum(venue_balances.values()))
        # Legacy replay internals still name this map exchange_balances; values are
        # now canonical venue IDs and may contain any number of venues.
        exchange_balances = dict(venue_balances)
        try:
            min_profit = min(1_000_000.0, max(0.0, float(data.get("minimum_profit", cfg.get("minimum_profit", 0.0)))))
        except (TypeError, ValueError):
            min_profit = float(cfg.get("minimum_profit", 0.0))
        try:
            min_roi = min(100.0, max(0.0, float(data.get("minimum_deployed_roi_pct", cfg.get("minimum_net_roi_pct", 1.0)))))
        except (TypeError, ValueError):
            min_roi = float(cfg.get("minimum_net_roi_pct", 1.0))
        try:
            exposure = min(100.0, max(0.0, float(data.get("max_event_exposure_pct", cfg.get("max_event_exposure_pct", 100.0)))))
        except (TypeError, ValueError):
            exposure = float(cfg.get("max_event_exposure_pct", 100.0))
        try:
            max_stake = min(1_000_000.0, max(0.0, float(data.get("max_stake", cfg.get("pre_match_execution_max_stake", cfg.get("execution_max_stake", 25.0))))))
        except (TypeError, ValueError):
            max_stake = float(cfg.get("pre_match_execution_max_stake", cfg.get("execution_max_stake", 25.0)) or 25.0)
        try:
            hedge_reserve_pct = min(100.0, max(0.0, float(data.get("hedge_reserve_pct", cfg.get("pre_match_execution_hedge_reserve_pct", cfg.get("execution_hedge_reserve_pct", 20.0))))))
        except (TypeError, ValueError):
            hedge_reserve_pct = float(cfg.get("pre_match_execution_hedge_reserve_pct", cfg.get("execution_hedge_reserve_pct", 20.0)) or 20.0)
        try:
            days_value = int(data.get("days") or 0)
            days = min(3650, max(1, days_value)) if days_value > 0 else None
        except (TypeError, ValueError):
            days = None
        sports = _list_values(data.get("sports"))
        sport = str(data.get("sport") or (sports[0] if len(sports) == 1 else "all"))
        strategy = str(data.get("strategy") or "all")
        engine_instance_ids = _list_values(data.get("engine_instance_ids"))
        engine_instance_id = str(data.get("engine_instance_id") or (engine_instance_ids[0] if len(engine_instance_ids) == 1 else "all")).strip() or "all"
        exchanges = [provider_id_for_name(x) or x.strip().lower() for x in _list_values(data.get("exchanges"))]
        exchange = str(data.get("exchange") or (exchanges[0] if len(exchanges) == 1 else "all"))
        requested_streams = [str(x).strip().lower() for x in _list_values(data.get("streams") or data.get("monitor_streams"))]
        requested_streams = [x for x in requested_streams if x in {"pre_match", "in_play", "racing"}]
        if not requested_streams:
            legacy_stream = str(data.get("monitor_stream") or "combined").strip().lower()
            requested_streams = [legacy_stream] if legacy_stream in {"pre_match", "in_play", "racing"} else ["pre_match", "in_play", "racing"]
        market = str(data.get("market") or "")
        search = str(data.get("search") or "")
        execution_mode = str(data.get("mode") or "all")
        minimum_quality_band = str(data.get("minimum_quality_band") or "all")
        date_from = self._parse_utc(data.get("from_utc"))
        date_to = self._parse_utc(data.get("to_utc"))
        release_policy = str(data.get("release_policy") or "estimated_close").strip().lower()
        if release_policy not in {"estimated_close", "observed"}:
            release_policy = "estimated_close"
        time_basis = str(data.get("time_basis") or "detected_at").strip().lower()
        if time_basis not in {"detected_at", "settled_at"}:
            time_basis = "settled_at"
        capital_source = str(data.get("capital_source") or "custom").strip().lower()
        source_stream = str(data.get("capital_source_stream") or "pre_match").strip().lower()

        scenario_started = time.perf_counter()
        try:
            prepared_history = prepare_replay_history(
                self.db, sport=sport, sports=sports, strategy=strategy, engine_instance_id=engine_instance_id,
                engine_instance_ids=engine_instance_ids, days=days, include_demo=include_demo,
                date_from=date_from, date_to=date_to, exchange=exchange, exchanges=exchanges, market=market, search=search,
                execution_mode=execution_mode, minimum_quality_band=minimum_quality_band,
                time_basis=time_basis, require_monitor_evidence=True,
            )
        except ReplayHistoryLimitExceeded as exc:
            return {"ok": False, "message": str(exc), "history_limit": 250000, "history_complete": False}
        prepare_finished = time.perf_counter()

        def run_stream(stream_name: str | None = None, stream_names: list[str] | None = None):
            return replay_analysis(
                self.db, starting_capital, exposure, min_profit, min_roi, sport=sport, sports=sports, strategy=strategy,
                days=days, include_demo=include_demo, release_policy=release_policy, date_from=date_from, date_to=date_to,
                exchange=exchange, exchanges=exchanges, market=market, search=search, engine_instance_id=engine_instance_id,
                engine_instance_ids=engine_instance_ids, execution_mode=execution_mode, exchange_balances=exchange_balances,
                require_monitor_evidence=True, monitor_stream=stream_name, monitor_streams=stream_names,
                minimum_quality_band=minimum_quality_band, time_basis=time_basis, prepared_history=prepared_history,
                max_stake=max_stake, hedge_reserve_pct=hedge_reserve_pct,
            )

        # Individual stream variants remain useful diagnostics; the main result follows
        # the exact multi-selected stream set from the Scenarios console.
        pre_match_result = run_stream("pre_match")
        in_play_result = run_stream("in_play")
        racing_result = run_stream("racing")
        result = run_stream(stream_names=requested_streams)
        model_finished = time.perf_counter()

        capitals = data.get("comparison_capitals") if "comparison_capitals" in data else self.db.get_setting("scenarios", [])
        comparison = []
        seen = set()
        for raw in capitals:
            try:
                capital = min(1_000_000.0, max(1.0, float(raw)))
            except (TypeError, ValueError):
                continue
            key = round(capital, 6)
            if key in seen:
                continue
            seen.add(key)
            ratio_total = sum(venue_balances.values())
            if ratio_total > 1e-9:
                scenario_balances = {key: capital * (value / ratio_total) for key, value in venue_balances.items()}
            else:
                scenario_balances = {"unallocated": capital}
            scenario = replay_analysis(
                self.db, capital, exposure, min_profit, min_roi, sport=sport, sports=sports, strategy=strategy,
                days=days, include_demo=include_demo, release_policy=release_policy, date_from=date_from, date_to=date_to,
                exchange=exchange, exchanges=exchanges, market=market, search=search, engine_instance_id=engine_instance_id,
                engine_instance_ids=engine_instance_ids, execution_mode=execution_mode, exchange_balances=scenario_balances,
                require_monitor_evidence=True, monitor_streams=requested_streams, minimum_quality_band=minimum_quality_band,
                time_basis=time_basis, prepared_history=prepared_history, max_stake=max_stake, hedge_reserve_pct=hedge_reserve_pct,
            )
            comparison.append({
                "starting_capital": scenario["starting_capital"],
                "ending_capital": scenario["ending_capital"],
                "realized_profit": scenario["realized_profit"],
                "realized_roi_pct": scenario["realized_roi_pct"],
                "return_on_deployed_pct": scenario["return_on_deployed_pct"],
                "taken": scenario["counts"]["taken"],
                "settled_available": scenario["counts"]["settled_available"],
                "peak_concurrent_deployed": scenario["peak_concurrent_deployed"],
                "peak_capital_tied_pct": scenario["peak_capital_tied_pct"],
            })
        comparison.sort(key=lambda x: x["starting_capital"])
        comparison_finished = time.perf_counter()

        # Replay is MONITOR-first: the replay itself uses measured monitor evidence
        # and the same venue-aware transaction constraints. LIVE remains actual-only.
        selected_ids = [int(x.get("id") or 0) for x in (result.get("events") or []) if int(x.get("id") or 0) > 0]
        execution_rows = self.db.execution_history_for_opportunities(selected_ids, include_demo=include_demo)
        latest_live = {}
        for row in execution_rows:
            oid = int(row.get("opportunity_id") or 0)
            if oid not in selected_ids or oid in latest_live or str(row.get("mode") or "").lower() != "live" or not bool(row.get("is_real")):
                continue
            if row.get("captured_profit") is not None:
                latest_live[oid] = row
        actual_profit = round(sum(float(x.get("captured_profit") or 0.0) for x in latest_live.values()), 4) if latest_live else None
        monitor_summary = {
            "ending_capital": result.get("ending_capital"), "profit": result.get("realized_profit"), "roi_pct": result.get("realized_roi_pct"),
            "locked_profit": result.get("locked_profit"), "locked_return_on_deployed_pct": result.get("locked_return_on_deployed_pct"),
            "opportunities": int((result.get("counts") or {}).get("taken") or 0),
            "selected_opportunities": int((result.get("counts") or {}).get("taken") or 0), "coverage_pct": 100.0 if (result.get("counts") or {}).get("taken") else 0.0,
        }
        actual_summary = {
            "ending_capital": None, "profit": actual_profit, "roi_pct": None, "opportunities": len(latest_live),
            "selected_opportunities": len(selected_ids), "coverage_pct": round((len(latest_live) / len(selected_ids)) * 100.0, 2) if selected_ids else 0.0,
        }
        evidence_comparison = {"monitor": monitor_summary, "actual": actual_summary, "monitor_timing": monitor_summary, "potential": monitor_summary}
        def stream_summary(name, replay_result):
            counts = replay_result.get("counts") or {}
            return {
                "stream": name,
                "starting_capital": replay_result.get("starting_capital"),
                "ending_capital": replay_result.get("ending_capital"),
                "realized_profit": replay_result.get("realized_profit"),
                "realized_roi_pct": replay_result.get("realized_roi_pct"),
                "return_on_deployed_pct": replay_result.get("return_on_deployed_pct"),
                "locked_profit": replay_result.get("locked_profit"),
                "locked_return_on_deployed_pct": replay_result.get("locked_return_on_deployed_pct"),
                "taken": int(counts.get("taken") or 0),
                "settled_available": int(counts.get("settled_available") or 0),
                "skipped_monitor_miss": int(counts.get("skipped_monitor_miss") or 0),
                "skipped_no_monitor_evidence": int(counts.get("skipped_no_monitor_evidence") or 0),
                "peak_concurrent_deployed": replay_result.get("peak_concurrent_deployed"),
                "peak_capital_tied_pct": replay_result.get("peak_capital_tied_pct"),
            }
        stream_comparison = {
            "pre_match": stream_summary("pre_match", pre_match_result),
            "in_play": stream_summary("in_play", in_play_result),
            "racing": stream_summary("racing", racing_result),
            "combined": stream_summary("combined", result),
        }
        # Direct actual-vs-modelled comparison uses the exact same UTC window.
        # Performance is settlement-based; when Scenario uses detection-time we
        # label the basis mismatch explicitly instead of pretending reconciliation.
        actual_stream = requested_streams[0] if len(requested_streams) == 1 else "all"
        actual_sport = sports[0] if len(sports) == 1 else sport
        actual_scope = "racing" if str(actual_sport).strip().lower() == "greyhounds" else "all"
        actual_perf = self.performance_analytics({
            "period": "custom", "from_utc": date_from.isoformat() if date_from else None,
            "to_utc": date_to.isoformat() if date_to else None, "scope": actual_scope,
            "stream": actual_stream, "sport": actual_sport, "engine_instance_id": engine_instance_id, "basis": "actual",
            "timezone_offset_minutes": data.get("timezone_offset_minutes", 0),
            "timezone_name": data.get("timezone_name", ""),
        }) if date_from else {"ok": False}
        ap = actual_perf.get("summary") or {}
        actual_performance = {
            "available": bool(actual_perf.get("ok")), "time_basis": "settled_at",
            "range_label": actual_perf.get("range_label"), "starting_capital": ap.get("period_start_capital"),
            "ending_capital": ap.get("period_end_capital"), "profit": ap.get("period_profit"),
            "deployed": ap.get("deployed_turnover"), "return_on_deployed_pct": ap.get("return_on_deployed_pct"),
            "settled": ap.get("settled_bets"), "basis_matches_scenario": time_basis == "settled_at",
        }
        total_finished = time.perf_counter()
        prep_diag = dict(prepared_history.get("diagnostics") or {})
        scenario_diagnostics = {
            **prep_diag,
            "scenario_prepare_ms": round((prepare_finished - scenario_started) * 1000.0, 3),
            "scenario_model_ms": round((model_finished - prepare_finished) * 1000.0, 3),
            "scenario_comparison_ms": round((comparison_finished - model_finished) * 1000.0, 3),
            "scenario_total_ms": round((total_finished - scenario_started) * 1000.0, 3),
            "replay_variants": 4 + len(comparison),
            "history_preparations": 1,
        }
        return {"ok": True, "result": result, "comparison": comparison, "stream_comparison": stream_comparison,
                "evidence_comparison": evidence_comparison, "actual_performance": actual_performance,
                "capital_source": capital_source, "capital_source_stream": source_stream,
                "engine_instance_id": engine_instance_id, "engine_instance_ids": engine_instance_ids,
                "sports": sports, "streams": requested_streams, "exchanges": exchanges,
                "max_stake": max_stake, "hedge_reserve_pct": hedge_reserve_pct,
                "time_basis": time_basis, "demo_included": include_demo,
                "scenario_diagnostics": scenario_diagnostics}

    def opportunity_history(self, data=None):
        data = data or {}
        try: reference_bankroll = float(data.get("bankroll") or 500.0)
        except (TypeError, ValueError): reference_bankroll = 500.0
        reference_bankroll = min(1_000_000.0, max(1.0, reference_bankroll))
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        include_demo = not bool(cfg.get("hide_demo_data", True))
        try:
            limit = min(10000, max(1, int(data.get("limit") or 10000)))
        except (TypeError, ValueError):
            limit = 10000
        recent_only = bool(data.get("recent_only"))
        rows = (
            self.db.recent_opportunity_rows(limit=limit, include_demo=include_demo)
            if recent_only
            else self.db.opportunity_rows(limit=limit, include_demo=include_demo)
        )
        out = []
        for row in rows:
            try:
                leg_dicts = json.loads(row.get("legs_json") or "[]")
                legs = [Leg(**{k: v for k, v in item.items() if k in Leg.__dataclass_fields__}) for item in leg_dicts]
                sim = simulate_equal_return(legs, Scenario(f"quality-{reference_bankroll:g}", reference_bankroll, 100.0, 100.0))
                dq = assess_data_quality(legs, float(row.get("match_score") or 0.0), row.get("detected_at"), float(cfg.get("stale_quote_seconds", 90.0)))
                profile = quality_profile(sim, float(row.get("match_score") or 0.0), reference_bankroll, data_quality=dq)
                outcome, realized = row.get("outcome"), None
                if outcome and sim.get("executable"):
                    pnls = sim.get("outcome_pnls") or {}; realized = pnls.get(outcome)
                    if realized is None:
                        norm = str(outcome).strip().lower(); realized = next((v for k,v in pnls.items() if str(k).strip().lower()==norm), None)
                exchanges = sorted({str(l.exchange) for l in legs if l.exchange})
                track = self.db.track_for(row.get("event_key") or "", row.get("market_name") or "", row.get("sport") or None) or {}
                uses_delayed = any("delayed" in x.lower() for x in exchanges)
                out.append({
                    "id": int(row["id"]), "detected_at": row.get("detected_at"), "event_name": row.get("event_name") or row.get("event_key"),
                    "market_name": row.get("market_name"), "strategy": row.get("strategy") or "1x2", "sport": row.get("sport") or (legs[0].sport if legs else "Unknown"),
                    "event_start": row.get("event_start"), "event_timing": event_phase(row.get("event_start"), row.get("event_status"), bool(row.get("in_play")) if row.get("in_play") is not None else None, settled=bool(outcome)),
                    "edge_pct": float(row.get("edge_pct") or 0.0),
                    "match_score": float(row.get("match_score") or 0.0), "status": row.get("status"), "is_demo": bool(row.get("is_demo")),
                    "outcome": outcome, "settled_at": row.get("settled_at"), "exchanges": exchanges, "uses_delayed_feed": uses_delayed,
                    "realized_pnl": None if realized is None else round(float(realized), 4),
                    "persistence_scans": int(track.get("scan_count") or 0), "persistence_seconds": int(track.get("duration_seconds") or 0),
                    "peak_quality_score": float(track.get("peak_quality_score") or 0.0), "peak_quality_band": track.get("peak_quality_band"),
                    "peak_deployed": float(track.get("peak_deployed") or 0.0), "peak_profit": float(track.get("peak_profit") or 0.0),
                    "peak_roi_pct": float(track.get("peak_roi_pct") or 0.0),
                    "first_seen": track.get("first_seen"), "last_seen": track.get("last_seen"), "closed_at": track.get("closed_at"),
                    "gross_roi_pct": float(sim.get("gross_roi_pct") or 0.0) if sim.get("executable") else None,
                    "commission_impact_pct": float(sim.get("commission_impact_pct") or 0.0) if sim.get("executable") else None,
                    **profile,
                    "plain_english": beginner_explanation(profile, uses_delayed),
                })
            except Exception:
                continue
        out.sort(key=lambda r: r.get("detected_at") or "", reverse=True)
        return {"ok": True, "reference_bankroll": reference_bankroll, "rows": out,
                "summary": history_summary(out), "demo_included": include_demo}

    def opportunity_tracks(self, data=None):
        data = data or {}; rows = self.db.track_history(limit=min(5000, max(1, int(data.get("limit", 2000)))))
        return {"ok": True, "rows": rows}

    def research_scorecard(self, data=None):
        data = data or {}
        days = min(365, max(1, int(data.get("days", 30))))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        observations = self.db.track_observations_since(cutoff.isoformat())

        # Aggregate once per unique positive-opportunity track.  Crucially, every
        # peak below is selected only from observations inside the requested
        # window; lifetime track peaks would make a 7-day scorecard inherit older
        # values and overstate the selected period.
        tracks = {}
        for obs in observations:
            key = str(obs.get("track_key") or "")
            if not key:
                continue
            current = tracks.get(key)
            score = float(obs.get("quality_score") or 0.0)
            if current is None:
                current = {
                    "track_key": key,
                    "strategy": obs.get("strategy") or "unknown",
                    "sport": obs.get("sport") or "Unknown",
                    "peak_quality_score": score,
                    "peak_quality_band": obs.get("quality_band"),
                    "peak_bankroll_roi_pct": float(obs.get("bankroll_roi_pct") or 0.0),
                    "peak_deployed": float(obs.get("deployed") or 0.0),
                    "peak_profit": float(obs.get("expected_profit") or 0.0),
                    "observation_count": 0,
                }
                tracks[key] = current
            current["observation_count"] += 1
            if score > float(current.get("peak_quality_score") or 0.0):
                current["peak_quality_score"] = score
                current["peak_quality_band"] = obs.get("quality_band")
            current["peak_bankroll_roi_pct"] = max(
                float(current.get("peak_bankroll_roi_pct") or 0.0),
                float(obs.get("bankroll_roi_pct") or 0.0),
            )
            current["peak_deployed"] = max(
                float(current.get("peak_deployed") or 0.0),
                float(obs.get("deployed") or 0.0),
            )
            current["peak_profit"] = max(
                float(current.get("peak_profit") or 0.0),
                float(obs.get("expected_profit") or 0.0),
            )

        rows = list(tracks.values())
        strong = [r for r in rows if r.get("peak_quality_band") in {"Strong", "Excellent"}]
        total_deployed = sum(float(r.get("peak_deployed") or 0) for r in rows)
        total_profit = sum(float(r.get("peak_profit") or 0) for r in rows)
        by_strategy = {}
        for r in rows:
            k = r.get("strategy") or "unknown"
            d = by_strategy.setdefault(k, {"count": 0, "strong": 0, "peak_profit": 0.0, "peak_deployed": 0.0})
            d["count"] += 1
            d["strong"] += int(r.get("peak_quality_band") in {"Strong", "Excellent"})
            d["peak_profit"] += float(r.get("peak_profit") or 0)
            d["peak_deployed"] += float(r.get("peak_deployed") or 0)
        avg_bankroll_roi = (
            sum(float(r.get("peak_bankroll_roi_pct") or 0.0) for r in rows) / len(rows)
        ) if rows else 0.0
        if len(rows) < 30:
            conclusion = "INSUFFICIENT EVIDENCE — KEEP COLLECTING"
        elif len(strong) >= 5 and avg_bankroll_roi >= 0.20:
            conclusion = "PROMISING PAPER EVIDENCE — KEEP TESTING"
        elif len(strong) == 0 and avg_bankroll_roi < 0.05:
            conclusion = "NOT PROMISING IN THIS SAMPLE"
        else:
            conclusion = "INCONCLUSIVE PAPER EVIDENCE"
        return {
            "ok": True,
            "days": days,
            "opportunities": len(rows),
            "observations": len(observations),
            "strong_or_better": len(strong),
            "peak_deployable_total": round(total_deployed, 2),
            "peak_paper_profit_total": round(total_profit, 2),
            "average_peak_bankroll_roi_pct": round(avg_bankroll_roi, 6),
            "by_strategy": by_strategy,
            "conclusion": conclusion,
            "conclusion_note": (
                "Unique positive opportunity tracks observed in the selected period; peak metrics are calculated only "
                "from observations inside that period. Heuristic paper-research score only; it is not proof of future profitability."
            ),
        }

    def matched_markets(self, data=None):
        data = data or {}; limit = min(1000, max(1, int(data.get("limit", 500))))
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        payload = self.db.latest_matched_markets(limit=limit)
        reference_bankroll = float(cfg.get("quality_reference_bankroll", 500.0))
        for row in payload.get("rows", []):
            self._attach_engine_provenance(row, fallback_section=str(row.get("section") or "sports"))
            self._attach_venue_account(row)
            legs = [Leg(**{k:v for k,v in item.items() if k in Leg.__dataclass_fields__}) for item in (row.get("legs") or [])]
            dq = assess_data_quality(legs, float(row.get("match_score") or 0.0), row.get("observed_at"), float(cfg.get("stale_quote_seconds", 90.0)))
            row["data_quality"] = dq
            row["display_reference_bankroll"] = reference_bankroll
            row["event_timing"] = event_phase(row.get("event_start"), row.get("event_status"), bool(row.get("in_play")) if row.get("in_play") is not None else None)
            try:
                sim = simulate_equal_return(legs, Scenario("matched-reference", reference_bankroll, 100.0, 100.0))
            except Exception:
                sim = {"executable": False}
            if sim.get("executable"):
                row["reference_deployed"] = sim.get("deployed")
                row["reference_profit"] = sim.get("expected_profit")
                row["reference_gross_profit"] = sim.get("gross_profit")
                row["reference_commission_cost"] = sim.get("commission_cost")
                row["reference_bankroll_roi_pct"] = sim.get("bankroll_roi_pct")
                row["reference_capital_used_pct"] = sim.get("capital_used_pct")
                row["reference_limiting_leg"] = sim.get("limiting_leg")
        return {"ok": True, **payload, "operations": self._operational_status()}

    def monitor_last_detected(self, data=None):
        data = data or {}
        mode = str(data.get("mode") or "sim").lower()
        if mode not in {"sim", "live"}:
            return {"ok": False, "message": "mode must be sim or live"}
        row = self.db.monitor_last_detected(
            mode=mode, section=str(data.get("section") or "sports"),
            stream=str(data.get("stream") or data.get("phase") or "all"),
            engine=str(data.get("engine") or "all"), sport=str(data.get("sport") or "all"),
            market=str(data.get("market") or ""), venue=str(data.get("venue") or "all"),
            account=str(data.get("account") or "all"),
        )
        return {"ok": True, "mode": mode, **row}

    def _racing_matched_rows(self, cfg, *, limit=1000):
        """Project matched Racing rows without loading unrelated page/dashboard state."""
        payload = self.db.latest_matched_markets(limit=1000)
        rows = []
        for row in payload.get("rows", []):
            if str(row.get("section") or "") != "racing" and str(row.get("sport") or "") not in SUPPORTED_RACING:
                continue
            self._attach_engine_provenance(row, fallback_section="racing")
            self._attach_venue_account(row)
            timing = event_phase(row.get("event_start"), row.get("event_status"), bool(row.get("in_play")) if row.get("in_play") is not None else None)
            row["event_timing"] = timing
            row["time_to_off_seconds"] = timing.get("seconds_to_start")
            min_liquidity = max(0.0, float(cfg.get("racing_minimum_liquidity", 0.0) or 0.0))
            book_analysis = _racing_book_analysis_from_sources(row.get("source_markets") or [], min_liquidity)
            row["book_analysis"] = book_analysis
            if book_analysis.get("valid"):
                # Use the auditable source-price reconstruction as the display truth.
                # New scans already persist the same selection, while this also
                # corrects any pre-upgrade matched row that used the old negative-
                # market absolute-£ ranking.
                row["legs"] = list(book_analysis.get("selected_legs") or [])
                diag = book_analysis.get("selected_diagnostic") or {}
                row["selected_cross_exchange_book_pct"] = book_analysis.get("selected_cross_exchange_book_pct")
                row["best_combined_book_pct"] = book_analysis.get("best_combined_book_pct")
                row["exchange_books_pct"] = book_analysis.get("exchange_books_pct") or {}
                row["selection_basis"] = book_analysis.get("selection_basis")
                row["liquidity_limiter"] = book_analysis.get("liquidity_limiter")
                row["best_price_book_pct"] = book_analysis.get("selected_cross_exchange_book_pct")
                row["theoretical_edge_pct"] = diag.get("theoretical_edge_pct")
                row["gross_roi_pct"] = diag.get("gross_roi_pct")
                row["commission_impact_pct"] = diag.get("commission_impact_pct")
                row["net_roi_pct"] = diag.get("expected_roi_pct")
                row["diagnostic_deployed"] = diag.get("deployed")
                row["diagnostic_profit"] = diag.get("expected_profit")
            else:
                if book_analysis.get("mapping_error"):
                    # Fail closed: a matched N-runner race must have exactly N
                    # canonical economic outcomes. Never display a stale/legacy ROI
                    # when the stored source runners cannot be mapped one-to-one.
                    for key in ("selected_cross_exchange_book_pct", "best_combined_book_pct", "best_price_book_pct",
                                "theoretical_edge_pct", "gross_roi_pct", "commission_impact_pct", "net_roi_pct",
                                "diagnostic_deployed", "diagnostic_profit"):
                        row[key] = None
                    row["pricing_state"] = "runner_mapping_error"
                    row["pricing_error"] = book_analysis.get("reason")
                    row["legs"] = []
                else:
                    row["best_price_book_pct"] = round(100.0 - float(row.get("theoretical_edge_pct") or 0.0), 4) if row.get("theoretical_edge_pct") is not None else None

            legs = [Leg(**{k: v for k, v in item.items() if k in Leg.__dataclass_fields__}) for item in (row.get("legs") or [])]
            row["data_quality"] = assess_data_quality(legs, float(row.get("match_score") or 0.0), row.get("observed_at"), float(cfg.get("stale_quote_seconds", 90.0)))
            row["runner_count"] = int(row.get("runner_count") or len(legs) or 0)
            reference_bankroll = float(cfg.get("quality_reference_bankroll", 500.0) or 500.0)
            row["display_reference_bankroll"] = reference_bankroll
            try:
                sim = simulate_equal_return(legs, Scenario("racing-reference", reference_bankroll, 100.0, 100.0))
            except Exception:
                sim = {"executable": False}
            row["reference_executable"] = bool(sim.get("executable"))
            # Racing diagnostics should remain informative even when a market is not
            # an arbitrage. Equal-return diagnostics show deployable depth, paper
            # P&L and commission for the selected book without enabling execution.
            try:
                ref_diag = diagnose_equal_return(legs, reference_bankroll)
            except Exception:
                ref_diag = {"valid": False}
            if ref_diag.get("valid"):
                row["reference_deployed"] = ref_diag.get("deployed")
                row["reference_profit"] = ref_diag.get("expected_profit")
                row["reference_gross_profit"] = ref_diag.get("gross_profit")
                row["reference_commission_cost"] = ref_diag.get("commission_cost")
                row["reference_bankroll_roi_pct"] = round((float(ref_diag.get("expected_profit") or 0.0) / reference_bankroll) * 100.0, 6) if reference_bankroll > 0 else 0.0
                row["reference_capital_used_pct"] = round((float(ref_diag.get("deployed") or 0.0) / reference_bankroll) * 100.0, 4) if reference_bankroll > 0 else 0.0
                row["reference_limiting_leg"] = ref_diag.get("limiting_leg")
                display_by_key = {str(x.get("key") or ""): str(x.get("display") or x.get("key") or "Runner") for x in (book_analysis.get("runner_prices") or [])}
                row["outcome_pnls"] = {display_by_key.get(str(k), str(k)): v for k, v in (ref_diag.get("outcome_pnls") or {}).items()}
                row["gross_outcome_pnls"] = {display_by_key.get(str(k), str(k)): v for k, v in (ref_diag.get("gross_outcome_pnls") or {}).items()}
                row["commission_by_outcome"] = {display_by_key.get(str(k), str(k)): v for k, v in (ref_diag.get("commission_by_outcome") or {}).items()}
                row["commission_by_outcome_exchange"] = {display_by_key.get(str(k), str(k)): v for k, v in (ref_diag.get("commission_by_outcome_exchange") or {}).items()}
                row["staking_method"] = ref_diag.get("staking_method")
                row["net_equalized"] = bool(ref_diag.get("net_equalized"))
                row["net_pnl_spread"] = ref_diag.get("net_pnl_spread")
                row["reference_stakes"] = ref_diag.get("stakes") or []
            rows.append(row)
        rows.sort(key=lambda r: (r.get("event_start") or "9999", -(float(r.get("net_roi_pct") or -9999))))
        rows = rows[:limit]
        return rows, payload.get("scan")

    def racing_overview(self, data=None):
        """Greyhound Racing overview with MONITOR-only execution evidence.

        v0.9.8 keeps LIVE Racing hard-locked while qualified pre-race Greyhound
        Win opportunities may enter the deterministic Monitor execution path.
        """
        data = data or {}
        limit = min(1000, max(1, int(data.get("limit", 500))))
        mode = canonical_mode_value(data.get("mode") or "sim")
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        tz_payload = {k: data.get(k) for k in ("timezone_offset_minutes", "timezone_name") if data.get(k) is not None}
        if mode == "live":
            current = asyncio.run(self._live_portfolio_financial_state_async(cfg, scope="racing", venue="all"))
            decisions = self.live_decision_evidence({"domain": "racing", "limit": 200, "include_latest": True, "include_summary": True})
            latest = list(decisions.get("latest") or [])
            decision_summary = decisions.get("summary") or {}

            # Race schedule/matching is shared provider-derived market evidence,
            # but the Dashboard/Overview is a *current* operator view. Only future,
            # non-closed races count as current candidates/matches or Next Off.
            discovery = self.db.get_setting("racing_discovery_latest", {}) or {}
            future_rows = []
            for raw in discovery.get("rows") or []:
                row = dict(raw)
                timing = event_phase(row.get("event_start"), row.get("market_status") or row.get("event_status"), row.get("in_play"))
                seconds = timing.get("seconds_to_start")
                if seconds is None or float(seconds) < 0:
                    continue
                if str(row.get("market_status") or "").upper() in {"CLOSED", "SETTLED"}:
                    continue
                row["event_timing"] = timing
                row["time_to_off_seconds"] = seconds
                future_rows.append(row)
            future_rows.sort(key=lambda x: (float(x.get("time_to_off_seconds") or 0), str(x.get("event_name") or "")))

            def _race_key(event_name, market_name):
                event = " ".join(str(event_name or "").strip().lower().split())
                market = " ".join(str(market_name or "win").strip().lower().split())
                return event, market

            matched_by_key = {}
            for race in future_rows:
                if str(race.get("match_status") or "").lower() != "matched":
                    continue
                matched_by_key.setdefault(_race_key(race.get("event_name") or race.get("race_track"), race.get("market_name") or "Win"), race)
            matched = len(matched_by_key)
            current_positive_keys = set()
            for row in latest:
                try:
                    edge = float(row.get("net_roi_pct"))
                except (TypeError, ValueError):
                    continue
                key = _race_key(row.get("event_name"), row.get("market_name") or row.get("market_type"))
                if edge > 0 and key in matched_by_key:
                    current_positive_keys.add(key)
            next_off = future_rows[0].get("event_start") if future_rows else None

            # Decision evidence remains queryable for diagnostics, but LIVE Racing
            # Highlights are lifecycle-owned. With no authoritative LIVE Qualified
            # or position persistence, no decision-evidence row may become a card.
            # This deliberately fails closed rather than displaying a market that
            # merely *would* have qualified under simulated evaluation.
            return {
                "ok": True, "mode": "live", "financial": current, "today_pnl": None,
                "active_positions": 0, "positions": [], "highlights": [], "rows": [],
                "upcoming": future_rows[:25],
                "summary": {
                    "next_off": next_off, "matched_races": matched,
                    "candidate_races": len(future_rows),
                    "net_positive": len(current_positive_keys),
                    "qualified_monitor": 0,
                    "decision_qualified_evidence": int(decision_summary.get("qualified") or 0),
                    "feed_health": "OBSERVING" if future_rows or latest else "WAITING",
                },
                "operations": self._operational_status("live"),
                "message": "LIVE Racing uses future shared provider schedule/matching facts. Decision evidence remains diagnostic-only; no Race Highlight, Qualified or Executed state is created without authoritative LIVE lifecycle persistence.",
                "live_execution_allowed": False,
            }
        if bool(data.get("_matched_rows_only")):
            matched_rows, _matched_scan = self._racing_matched_rows(cfg, limit=limit)
            return {"ok": True, "mode": "sim", "rows": matched_rows}
        current = self._sim_portfolio_financial_state(cfg, scope="racing", venue="all")
        dash = self.dashboard_overview(tz_payload)
        racing_positions = [x for x in (dash.get("rows") or []) if str(x.get("monitor_stream") or "") == "racing"]
        financial = dash.get("financial") or {}
        today_pnl = float((((financial.get("today") or {}).get("racing_summary") or {}).get("pnl") or 0.0))
        rows, matched_scan = self._racing_matched_rows(cfg, limit=limit)
        positive = [r for r in rows if float(r.get("net_roi_pct") or 0.0) > 0]
        qualified = [r for r in rows if r.get("status") in {"racing_monitor", "racing_qualified"}]
        funnel = self.db.racing_execution_funnel(hours=24)
        health_warnings = []
        health_invalid = []
        racing_numeric = {
            "Max stake": float(cfg.get("racing_execution_max_stake", 25.0) or 0.0),
            "Max event exposure": float(cfg.get("racing_max_event_exposure_pct", 100.0) or 0.0),
            "Max slippage": float(cfg.get("racing_execution_max_slippage_pct", 0.5) or 0.0),
            "Max unhedged exposure": float(cfg.get("racing_execution_max_unhedged_exposure", 25.0) or 0.0),
        }
        for label, value in racing_numeric.items():
            if value < 0: health_invalid.append(f"{label} cannot be negative")
        if bool(cfg.get("racing_monitor_enabled", True)):
            if racing_numeric["Max stake"] <= 0:
                health_warnings.append("Racing max stake is zero; no MONITOR position can deploy")
            if racing_numeric["Max event exposure"] <= 0:
                health_warnings.append("Racing max event exposure is zero; execution will be blocked")
            if all(abs(v) < 1e-12 for v in racing_numeric.values()):
                health_warnings.append("All Racing execution guardrails are zero; verify this was intentional")
        config_health = {
            "status": "INVALID" if health_invalid else ("WARNING" if health_warnings else "HEALTHY"),
            "warnings": health_warnings, "errors": health_invalid,
        }

        latest = self.db.get_setting("racing_discovery_latest", {}) or {}
        discovery_rows = [dict(x) for x in (latest.get("rows") or [])]
        discovery_summary = latest.get("summary") or {}
        by_exchange = discovery_summary.get("by_exchange") or {}
        betfair_feed = discovery_summary.get("betfair_feed") or {}
        venue_counts = {}
        for raw_name, raw_count in by_exchange.items():
            venue_id = provider_id_for_name(str(raw_name or "")) or str(raw_name or "").strip().lower().replace(" ", "_")
            if venue_id:
                venue_counts[venue_id] = max(int(raw_count or 0), int(venue_counts.get(venue_id) or 0))
        # Betfair exposes catalogue-level Racing evidence that can be richer than
        # executable ExchangeMarket rows; preserve that provider-specific hook.
        venue_counts["betfair"] = max(int(venue_counts.get("betfair") or 0), int(betfair_feed.get("catalogue") or 0))
        betfair_count = int(venue_counts.get("betfair") or 0)
        matchbook_count = int(venue_counts.get("matchbook") or 0)
        matched_count = int(discovery_summary.get("matched") or (len(rows) if not discovery_rows else 0))
        candidate_count = int(discovery_summary.get("candidates") or 0)
        race_candidate_count = int(discovery_summary.get("race_candidates") or candidate_count)
        event_pair_count = int(discovery_summary.get("event_pairs") or candidate_count)
        runner_aligned_count = int(discovery_summary.get("runner_aligned") or matched_count)
        unmatched_count = int(discovery_summary.get("unmatched") or 0)
        rejected_count = int(discovery_summary.get("rejected") or 0)
        complete_count = int(betfair_feed.get("fully_priced") or 0)
        incomplete_count = int(betfair_feed.get("incomplete_prices") or 0)

        def source_label(item):
            ex = str(item.get("exchange") or "")
            status = str(item.get("match_status") or "unmatched")
            if status == "matched":
                return "Matched"
            if status == "candidate":
                return "Candidate"
            if status == "rejected":
                return "Rejected"
            venue_id = provider_id_for_name(ex) or ex.strip().lower().replace(" ", "_")
            if venue_id:
                spec = self.provider_runtime.providers.get(venue_id) if self.provider_runtime is not None else None
                label = spec.venue.venue_name if spec is not None else ex or venue_id.replace("_", " ").title()
                return f"{label} only"
            return "Unmatched"

        upcoming = []
        seen = set()
        for item in sorted(discovery_rows, key=lambda x: (x.get("event_start") or "9999", 0 if x.get("exchange") == "Matchbook" else 1)):
            timing = event_phase(item.get("event_start"), item.get("market_status"), item.get("in_play"))
            secs = timing.get("seconds_to_start")
            if secs is not None and int(secs) < 0:
                continue
            pair = item.get("matched_event_key") or (item.get("candidate_pair_key") if item.get("match_status") == "candidate" else None)
            key = f"pair:{pair}" if pair else f"{item.get('exchange')}:{item.get('market_id')}"
            if key in seen:
                continue
            seen.add(key)
            row = dict(item)
            row["time_to_off_seconds"] = secs
            row["coverage_state"] = source_label(item)
            upcoming.append(row)
            if len(upcoming) >= 12:
                break

        next_off = next((x for x in upcoming if x.get("event_start")), None)
        if next_off is None:
            next_matched = next((r for r in rows if (r.get("time_to_off_seconds") is not None and int(r.get("time_to_off_seconds")) >= 0)), None)
            next_off_value = next_matched.get("event_start") if next_matched else None
        else:
            next_off_value = next_off.get("event_start")

        active_venue_counts = [count for count in venue_counts.values() if int(count or 0) > 0]
        min_feed = min(active_venue_counts) if len(active_venue_counts) >= 2 else 0
        match_pct = (100.0 * matched_count / min_feed) if min_feed > 0 else 0.0
        matchbook_pct = (100.0 * matched_count / matchbook_count) if matchbook_count > 0 else 0.0
        if len(active_venue_counts) >= 2:
            feed_health = "HEALTHY"
        elif active_venue_counts:
            feed_health = "DEGRADED"
        else:
            feed_health = "NO DATA"
        scope_venues = []
        for venue_id in sorted(venue_counts):
            spec = self.provider_runtime.providers.get(venue_id) if self.provider_runtime is not None else None
            scope_venues.append(spec.venue.venue_name if spec is not None else venue_id.replace("_", " ").title())

        open_keys = {(str(x.get("event_key") or ""), str(x.get("market_name") or "")): x for x in racing_positions}
        ranked = []
        for row in rows:
            key = (str(row.get("event_key") or ""), str(row.get("market_name") or ""))
            pos = open_keys.get(key)
            qualified_now = str(row.get("status") or "").lower() in {"racing_monitor", "racing_qualified"}
            edge = float(row.get("net_roi_pct") or 0.0)
            secs = row.get("time_to_off_seconds")
            if not pos and not qualified_now and edge <= 0:
                continue
            legs = row.get("legs") or []
            venues = []
            for leg in legs:
                name = str((leg or {}).get("exchange") or (leg or {}).get("venue_id") or "").strip()
                if name and name not in venues:
                    venues.append(name)
            highlight = "OPEN POSITION" if pos else ("QUALIFIED" if qualified_now else "BEST EDGE")
            ranked.append((1 if pos else 0, 1 if qualified_now else 0, edge, -(int(secs) if secs is not None else 10**9), {
                "event_name": row.get("event_name") or row.get("event_key") or "Greyhound race",
                "event_key": row.get("event_key"), "market_name": row.get("market_name") or "Win",
                "engine_instance_id": (pos or row).get("engine_instance_id"),
                "engine_nickname": (pos or row).get("engine_nickname"),
                "highlight": highlight, "best_edge_pct": round(edge, 4),
                "qualified": 1 if qualified_now else 0, "open_positions": 1 if pos else 0,
                "capital_deployed": None if not pos else float(pos.get("deployed") or 0.0),
                "locked_profit": None if not pos else pos.get("locked_profit"),
                "venue_pair": " ↔ ".join(venues[:2]) if venues else None,
                "event_start": row.get("event_start"), "freshness": row.get("last_seen") or row.get("observed_at"),
            }))
        ranked.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
        highlights = [x[-1] for x in ranked[:3]]

        return {
            "ok": True, "mode": "sim", "financial": current, "today_pnl": round(today_pnl, 4),
            "active_positions": len(racing_positions), "positions": racing_positions[:6], "highlights": highlights,
            "research_only": False,
            "monitor_execution_allowed": bool(cfg.get("racing_monitor_enabled", True)),
            "live_execution_allowed": False,
            "scope": {"sport": "Greyhounds", "market": "Win", "timing": "pre-race", "venues": scope_venues, "exchanges": scope_venues},
            "scan": matched_scan,
            "observed_at": latest.get("observed_at"),
            "producer": latest.get("producer") or {},
            "rows": rows,
            "upcoming": upcoming,
            "summary": {
                "betfair_detected": betfair_count,
                "matchbook_detected": matchbook_count,
                "venue_detected": {key: int(value or 0) for key, value in sorted(venue_counts.items())},
                "venue_count": len(active_venue_counts),
                "matched_races": matched_count,
                "candidate_races": candidate_count,
                "race_candidates": race_candidate_count,
                "event_pairs": event_pair_count,
                "runner_aligned": runner_aligned_count,
                "unmatched_races": unmatched_count,
                "rejected_races": rejected_count,
                "betfair_complete": complete_count,
                "betfair_incomplete": incomplete_count,
                "cross_exchange_match_pct": round(match_pct, 2),
                "matchbook_match_pct": round(matchbook_pct, 2),
                "feed_health": feed_health,
                "net_positive": len(positive),
                "qualified_research": int(funnel.get("qualified") or 0),
                "qualified_monitor": int(funnel.get("qualified") or 0),
                "next_off": next_off_value,
            },
            "matching": {
                "matched": matched_count,
                "candidates": candidate_count,
                "race_candidates": race_candidate_count,
                "event_pairs": event_pair_count,
                "runner_aligned": runner_aligned_count,
                "unmatched": unmatched_count,
                "rejected": rejected_count,
                "matchbook_coverage_pct": round(matchbook_pct, 2),
                "cross_exchange_match_pct": round(match_pct, 2),
            },
            "funnel": funnel,
            "config_health": config_health,
            "settings": {
                "enabled": bool(cfg.get("racing_greyhounds_enabled", True)),
                "minimum_liquidity": float(cfg.get("racing_minimum_liquidity", 2.0) or 0.0),
                "minimum_net_roi_pct": float(cfg.get("racing_minimum_net_roi_pct", 1.0) or 0.0),
                "minimum_profit": float(cfg.get("racing_minimum_profit", 0.0) or 0.0),
                "minimum_quality_band": str(cfg.get("racing_minimum_quality_band", "Tiny") or "Tiny"),
                "race_match_threshold": float(cfg.get("racing_match_threshold", 0.90) or 0.0),
                "runner_match_threshold": float(cfg.get("racing_runner_match_threshold", 0.92) or 0.0),
                "monitor_enabled": bool(cfg.get("racing_monitor_enabled", True)),
                "monitor_betfair_starting_balance": float(cfg.get("racing_monitor_betfair_starting_balance", 250.0) or 0.0),
                "monitor_matchbook_starting_balance": float(cfg.get("racing_monitor_matchbook_starting_balance", 250.0) or 0.0),
                "execution_max_stake": float(cfg.get("racing_execution_max_stake", 25.0) or 0.0),
                "max_event_exposure_pct": float(cfg.get("racing_max_event_exposure_pct", 100.0) or 0.0),
                "execution_max_slippage_pct": float(cfg.get("racing_execution_max_slippage_pct", 0.5) or 0.0),
                "execution_max_unhedged_exposure": float(cfg.get("racing_execution_max_unhedged_exposure", 25.0) or 0.0),
                "execution_hedge_reserve_pct": float(cfg.get("racing_execution_hedge_reserve_pct", 20.0) or 0.0),
                "monitor_retry_cooldown_seconds": float(cfg.get("racing_monitor_retry_cooldown_seconds", 5.0) or 0.0),
                "monitor_max_attempts_per_race": int(cfg.get("racing_monitor_max_attempts_per_race", 3) or 3),
            },
            "matchbook_price_side_audit": self.db.get_setting("matchbook_price_side_audit_latest", {}) or {},
            "operations": self._operational_status("sim"),
        }

    def racing_monitor(self, data=None):
        """Latest raw Greyhound discovery and matching state, including unmatched races."""
        data = data or {}
        latest = self.db.get_setting("racing_discovery_latest", {}) or {}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        matched = self.racing_overview({"limit": 1000, "_matched_rows_only": True})
        matched_rows = matched.get("rows") or []
        by_event = {str(x.get("event_key") or ""): x for x in matched_rows}
        rows = []
        for row in latest.get("rows") or []:
            item = dict(row)
            detail = by_event.get(str(item.get("matched_event_key") or ""))
            if detail:
                item["quality_band"] = (detail.get("data_quality") or {}).get("band") or detail.get("quality_band")
                item["net_roi_pct"] = detail.get("net_roi_pct")
                item["research_status"] = detail.get("status")
                item["best_price_book_pct"] = detail.get("best_price_book_pct")
                item["best_combined_book_pct"] = detail.get("best_combined_book_pct")
                item["selection_basis"] = detail.get("selection_basis")
                # Keep the Monitor self-contained: a selected matched race can expose
                # the same auditable runner-level pricing evidence as Racing Overview
                # without changing any execution behaviour.  This is diagnostic-only.
                item["pricing_detail"] = {
                    "event_key": detail.get("event_key"),
                    "event_name": detail.get("event_name"),
                    "event_start": detail.get("event_start"),
                    "runner_count": detail.get("runner_count"),
                    "book_analysis": detail.get("book_analysis") or {},
                    "exchange_books_pct": detail.get("exchange_books_pct") or {},
                    "best_combined_book_pct": detail.get("best_combined_book_pct"),
                    "selected_cross_exchange_book_pct": detail.get("selected_cross_exchange_book_pct"),
                    "best_price_book_pct": detail.get("best_price_book_pct"),
                    "theoretical_edge_pct": detail.get("theoretical_edge_pct"),
                    "gross_roi_pct": detail.get("gross_roi_pct"),
                    "commission_impact_pct": detail.get("commission_impact_pct"),
                    "net_roi_pct": detail.get("net_roi_pct"),
                    "selection_basis": detail.get("selection_basis"),
                    "liquidity_limiter": detail.get("liquidity_limiter") or (detail.get("book_analysis") or {}).get("liquidity_limiter"),
                    "reference_deployed": detail.get("reference_deployed"),
                    "reference_profit": detail.get("reference_profit"),
                    "reference_stakes": detail.get("reference_stakes") or [],
                }
                item["price_state"] = "ready"
                item["time_to_off_seconds"] = detail.get("time_to_off_seconds")
            else:
                item["price_state"] = "pending" if str(item.get("match_status") or "") == "matched" else "unavailable"
                item["time_to_off_seconds"] = event_phase(item.get("event_start"), item.get("market_status"), item.get("in_play")).get("seconds_to_start")
            self._attach_engine_provenance(item, fallback_section="racing")
            self._attach_venue_account(item)
            rows.append(item)
        rows.sort(key=lambda x: (x.get("event_start") or "9999", x.get("exchange") or ""))
        return {
            "ok": True,
            "observed_at": latest.get("observed_at"),
            "producer": latest.get("producer") or {},
            "summary": latest.get("summary") or {"total": 0, "matched": 0, "unmatched": 0, "rejected": 0, "by_exchange": {}},
            "rows": rows,
            "matchbook_price_side_audit": self.db.get_setting("matchbook_price_side_audit_latest", {}) or {},
            "research_only": False,
            "monitor_execution_allowed": bool(cfg.get("racing_monitor_enabled", True)),
            "live_execution_allowed": False,
        }

    def sport_coverage(self, data=None):
        payload = self.db.sport_coverage()
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        enabled = []
        for sport in SUPPORTED_SPORTS:
            key = "sport_" + sport.lower().replace(" ", "_") + "_enabled"
            if cfg.get(key, True):
                enabled.append(sport)

        # The database only has rows for sports that produced raw markets or
        # cross-exchange matches in the latest scan.  The coverage screen should
        # still show every supported sport, including a clear zero row when a
        # sport was enabled but no suitable markets were returned in that scan.
        # This also makes newly-added sports visible immediately after upgrade.
        by_sport = {str(row.get("sport")): dict(row) for row in payload.get("rows", [])}
        coverage_rows = []
        for sport in SUPPORTED_SPORTS:
            row = by_sport.pop(sport, {})
            coverage_rows.append({
                "sport": sport,
                "enabled": sport in enabled,
                "markets_seen": int(row.get("markets_seen") or 0),
                "matched": int(row.get("matched") or 0),
                "live_matched": int(row.get("live_matched") or 0),
                "theoretical_arbs": int(row.get("theoretical_arbs") or 0),
                "net_positive": int(row.get("net_positive") or 0),
                "recommended": int(row.get("recommended") or 0),
            })

        # Preserve any unexpected/legacy sport label rather than silently hiding
        # it; these rows are useful when diagnosing an exchange naming change.
        for sport, row in sorted(by_sport.items()):
            coverage_rows.append({
                "sport": sport,
                "enabled": False,
                "markets_seen": int(row.get("markets_seen") or 0),
                "matched": int(row.get("matched") or 0),
                "live_matched": int(row.get("live_matched") or 0),
                "theoretical_arbs": int(row.get("theoretical_arbs") or 0),
                "net_positive": int(row.get("net_positive") or 0),
                "recommended": int(row.get("recommended") or 0),
            })

        return {
            "ok": True,
            "enabled_sports": enabled,
            "supported_sports": list(SUPPORTED_SPORTS),
            "scan": payload.get("scan"),
            "rows": coverage_rows,
        }

    def opportunity_details(self, opportunity_id):
        oid = int(opportunity_id)
        row = next((r for r in self.db.opportunity_rows(limit=10000, include_demo=True) if int(r["id"]) == oid), None)
        if not row:
            return {"ok": False, "message": "Opportunity not found"}
        row["legs"] = json.loads(row.pop("legs_json") or "[]")
        row["source_markets"] = json.loads(row.pop("source_markets_json") or "[]")
        row["event_timing"] = event_phase(row.get("event_start"), row.get("event_status"), bool(row.get("in_play")) if row.get("in_play") is not None else None, settled=bool(row.get("outcome")))
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        legs = [Leg(**{k:v for k,v in item.items() if k in Leg.__dataclass_fields__}) for item in row["legs"]]
        exchanges = sorted({str(l.exchange or "") for l in legs if l.exchange})
        min_liq = min((float(l.liquidity or 0.0) for l in legs), default=0.0)
        dq = assess_data_quality(legs, float(row.get("match_score") or 0.0), row.get("detected_at"), float(cfg.get("stale_quote_seconds", 90.0)))
        ref = float(cfg.get("quality_reference_bankroll", 500.0))
        qsim = simulate_equal_return(legs, Scenario("quality", ref, 100, 100))
        quality = {
            "distinct_exchanges": len(exchanges), "exchanges": exchanges, "minimum_leg_liquidity": min_liq,
            "passes_cross_exchange": (len(exchanges) >= 2) if cfg.get("require_cross_exchange", True) else True,
            "passes_liquidity": min_liq >= float(cfg.get("minimum_liquidity", 2.0)),
            "uses_delayed_feed": any("delayed" in str(l.exchange).lower() for l in legs),
        }
        quality["valid_under_current_rules"] = bool(quality["passes_cross_exchange"] and quality["passes_liquidity"])
        quality.update(quality_profile(qsim, float(row.get("match_score") or 0), ref, data_quality=dq))
        quality["gross_roi_pct"] = qsim.get("gross_roi_pct")
        quality["commission_impact_pct"] = qsim.get("commission_impact_pct")
        quality["plain_english"] = beginner_explanation(quality, quality["uses_delayed_feed"])

        checks = []
        checks.append({"level": "good" if quality["passes_cross_exchange"] else "bad", "text": "Uses at least two exchanges" if quality["passes_cross_exchange"] else "Only one exchange is represented"})
        checks.append({"level": "good" if quality["passes_liquidity"] else "bad", "text": f"Minimum displayed leg liquidity £{min_liq:.2f}"})
        checks.append({"level": "warn" if dq.get("uses_delayed_feed") else "good", "text": "Betfair delayed development data contributes to this observation" if dq.get("uses_delayed_feed") else "No delayed-feed warning detected"})
        checks.append({"level": "good" if float(row.get("match_score") or 0) >= 0.9 else "warn", "text": f"Market-match confidence {float(row.get('match_score') or 0)*100:.0f}%"})
        if dq.get("fallback_commission"):
            checks.append({"level": "warn", "text": "Commission fallback was used for at least one Betfair leg"})
        else:
            checks.append({"level": "good", "text": "Commission sources captured with the opportunity"})

        comparisons = []
        for capital in [250.0, 500.0, 1000.0, 5000.0]:
            sim = simulate_equal_return(legs, Scenario(f"£{capital:g}", capital, 100.0, 100.0))
            comparisons.append({"bankroll": capital, **sim})

        track = self.db.track_for(row.get("event_key") or "", row.get("market_name") or "", row.get("sport") or None)
        lifecycle = []
        if track:
            lifecycle = self.db.track_observations_for(track.get("track_key") or "", limit=500)

        execution_plan = None
        if qsim.get("executable"):
            try:
                in_play_row = bool(row.get("in_play"))
                racing_row = str(row.get("section") or "sports").lower() == "racing"
                if racing_row:
                    slippage_key = "racing_execution_max_slippage_pct"
                    unhedged_key = "racing_execution_max_unhedged_exposure"
                    reserve_key = "racing_execution_hedge_reserve_pct"
                else:
                    slippage_key = "inplay_execution_max_slippage_pct" if in_play_row else "pre_match_execution_max_slippage_pct"
                    unhedged_key = "inplay_execution_max_unhedged_exposure" if in_play_row else "pre_match_execution_max_unhedged_exposure"
                    reserve_key = "inplay_execution_hedge_reserve_pct" if in_play_row else "pre_match_execution_hedge_reserve_pct"
                execution_plan = build_execution_plan(
                    legs, qsim, opportunity_id=oid, event_name=row.get("event_name") or row.get("event_key") or "",
                    market_name=row.get("market_name") or "",
                    ttl_ms=int(float(cfg.get("execution_plan_ttl_ms", 1500))),
                    max_slippage_pct=float(cfg.get(slippage_key, cfg.get("execution_max_slippage_pct", 0.50))),
                    max_unhedged_exposure=float(cfg.get(unhedged_key, cfg.get("execution_max_unhedged_exposure", 25.0))),
                    hedge_reserve_pct=float(cfg.get(reserve_key, cfg.get("execution_hedge_reserve_pct", 20.0))),
                ).as_dict()
                execution_plan["pre_match_live_candidate"] = bool(
                    not execution_plan.get("in_play") and not row.get("outcome")
                )
            except Exception:
                execution_plan = None
        latest_execution = self.db.latest_execution_for_opportunity(oid)
        if latest_execution:
            latest_execution.update(self._execution_value_metrics(latest_execution))
        return {
            "ok": True, "opportunity": row, "scenario_runs": self.db.scenario_runs_for_opportunity(oid),
            "quality": quality, "reference_simulation": qsim, "persistence": track, "lifecycle": lifecycle, "validity_checks": checks,
            "bankroll_comparison": comparisons, "execution_plan": execution_plan,
            "monitor_timing_run": self.db.monitor_timing_run_for_opportunity(oid),
            "latest_execution": latest_execution,
        }


    def export_opportunity(self, data=None):
        """Export one opportunity as a diagnostic JSON bundle plus flat CSVs.

        The JSON is intended to be complete enough to share for debugging: captured
        opportunity data, current quality/simulation, timed Monitor observations
        (including per-venue in-play/status flags), lifecycle and latest execution.
        CSV files make the legs and timing checkpoints easy to inspect separately.
        """
        data = data or {}
        try:
            oid = int(data.get("opportunity_id"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "Valid opportunity_id required"}

        details = self.opportunity_details(oid)
        if not details.get("ok"):
            return details

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path.home() / "Downloads" / "ArbScanner-Exports" / f"opportunity-{oid}-{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)

        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        diagnostic_setting_keys = (
            "minimum_liquidity", "minimum_net_roi_pct", "minimum_profit",
            "event_match_threshold", "stale_quote_seconds", "price_quote_max_age_seconds", "max_event_exposure_pct",
            "discovery_interval_seconds", "price_scan_tick_seconds", "price_refresh_near_seconds",
            "price_refresh_today_seconds", "price_refresh_later_seconds",
            "execution_max_stake", "execution_max_slippage_pct",
            "execution_max_unhedged_exposure", "execution_hedge_reserve_pct",
            "execution_pre_match_only", "monitor_timing_reference_checkpoint_ms",
            "monitor_execution_checkpoint_ms", "monitor_hedge_checkpoint_ms",
            "pre_match_monitor_betfair_starting_balance", "pre_match_monitor_matchbook_starting_balance",
            "inplay_monitor_betfair_starting_balance", "inplay_monitor_matchbook_starting_balance",
            "pre_match_minimum_liquidity", "pre_match_minimum_net_roi_pct", "pre_match_minimum_profit",
            "pre_match_minimum_quality_band",
            "pre_match_execution_max_stake", "pre_match_max_event_exposure_pct",
            "pre_match_execution_max_slippage_pct", "pre_match_execution_max_unhedged_exposure",
            "pre_match_execution_hedge_reserve_pct",
            "inplay_minimum_liquidity", "inplay_minimum_net_roi_pct", "inplay_minimum_profit",
            "inplay_minimum_quality_band",
            "inplay_execution_max_stake", "inplay_max_event_exposure_pct",
            "inplay_execution_max_slippage_pct", "inplay_execution_max_unhedged_exposure",
            "inplay_execution_hedge_reserve_pct", "inplay_monitor_cooldown_seconds",
            "inplay_betfair_delay_ms", "inplay_matchbook_delay_ms",
            "inplay_adverse_odds_pct_per_second", "inplay_liquidity_decay_pct_per_second",
        )
        bundle = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "app_version": "1.0",
            "diagnostic_type": "opportunity",
            "diagnostic_settings": {k: cfg.get(k) for k in diagnostic_setting_keys},
            "scanner_operations": self._operational_status(),
            **{k: v for k, v in details.items() if k != "ok"},
        }
        json_path = out_dir / f"opportunity-{oid}-diagnostic.json"
        json_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        legs_path = out_dir / f"opportunity-{oid}-legs.csv"
        legs = list((details.get("opportunity") or {}).get("legs") or [])
        leg_fields = [
            "exchange", "market_id", "selection", "selection_id", "odds", "liquidity",
            "commission_pct", "commission_source", "captured_at", "source_latency_ms",
            "in_play", "market_status",
        ]
        with legs_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=leg_fields, extrasaction="ignore")
            w.writeheader()
            for row in legs:
                w.writerow(row)

        timing_path = out_dir / f"opportunity-{oid}-monitor-timing.csv"
        timing_fields = [
            "offset_ms", "elapsed_ms", "observed_at", "fetch_latency_ms", "still_executable",
            "failure_reason", "expected_profit", "expected_roi_pct", "executable_fraction",
            "full_stake_available", "venue", "venue_market_id", "venue_ok", "venue_status",
            "venue_in_play", "venue_latency_ms", "venue_captured_at", "venue_quote_age_seconds", "venue_error",
        ]
        observations = list((details.get("monitor_timing_run") or {}).get("observations") or [])
        with timing_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=timing_fields)
            w.writeheader()
            for obs in observations:
                venues = list(obs.get("venues") or []) or [{}]
                for venue in venues:
                    w.writerow({
                        "offset_ms": obs.get("offset_ms"),
                        "elapsed_ms": obs.get("elapsed_ms"),
                        "observed_at": obs.get("observed_at"),
                        "fetch_latency_ms": obs.get("fetch_latency_ms"),
                        "still_executable": obs.get("still_executable"),
                        "failure_reason": obs.get("failure_reason"),
                        "expected_profit": obs.get("expected_profit"),
                        "expected_roi_pct": obs.get("expected_roi_pct"),
                        "executable_fraction": obs.get("executable_fraction"),
                        "full_stake_available": obs.get("full_stake_available"),
                        "venue": venue.get("exchange"),
                        "venue_market_id": venue.get("market_id"),
                        "venue_ok": venue.get("ok"),
                        "venue_status": venue.get("status"),
                        "venue_in_play": venue.get("in_play"),
                        "venue_latency_ms": venue.get("latency_ms"),
                        "venue_captured_at": venue.get("captured_at"),
                        "venue_quote_age_seconds": venue.get("quote_age_seconds"),
                        "venue_error": venue.get("error"),
                    })

        return {
            "ok": True,
            "directory": str(out_dir),
            "files": [str(json_path), str(legs_path), str(timing_path)],
            "primary_file": str(json_path),
        }


    def execution_stress_test(self, data=None):
        """Run paper-only partial-fill/rejection stress cases for one stored opportunity."""
        data = data or {}
        try:
            oid = int(data.get("opportunity_id"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "Valid opportunity_id required"}
        row = next((r for r in self.db.opportunity_rows(limit=10000, include_demo=True) if int(r["id"]) == oid), None)
        if not row:
            return {"ok": False, "message": "Opportunity not found"}
        try:
            payload = json.loads(row.get("legs_json") or "[]")
            legs = [Leg(**{k: v for k, v in item.items() if k in Leg.__dataclass_fields__}) for item in payload]
        except Exception as exc:
            return {"ok": False, "message": f"Could not load opportunity legs: {exc}"}
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        try:
            bankroll = max(1.0, float(data.get("bankroll") or cfg.get("quality_reference_bankroll", 500.0)))
            worse_pct = min(25.0, max(0.0, float(data.get("worse_hedge_odds_pct", 0.50))))
        except (TypeError, ValueError):
            return {"ok": False, "message": "Invalid execution stress-test settings"}
        sim = simulate_equal_return(legs, Scenario("execution-lab", bankroll, 100.0, 100.0))
        if not sim.get("executable"):
            return {"ok": False, "message": sim.get("reason") or "Opportunity is not executable at this bankroll"}
        in_play_row = bool(row.get("in_play"))
        plan = build_execution_plan(
            legs, sim, opportunity_id=oid, event_name=row.get("event_name") or row.get("event_key") or "",
            market_name=row.get("market_name") or "",
            ttl_ms=int(float(cfg.get("execution_plan_ttl_ms", 1500))),
            max_slippage_pct=float(cfg.get("inplay_execution_max_slippage_pct" if in_play_row else "pre_match_execution_max_slippage_pct", cfg.get("execution_max_slippage_pct", 0.50))),
            max_unhedged_exposure=float(cfg.get("inplay_execution_max_unhedged_exposure" if in_play_row else "pre_match_execution_max_unhedged_exposure", cfg.get("execution_max_unhedged_exposure", 25.0))),
            hedge_reserve_pct=float(cfg.get("inplay_execution_hedge_reserve_pct" if in_play_row else "pre_match_execution_hedge_reserve_pct", cfg.get("execution_hedge_reserve_pct", 20.0))),
        )
        rows = stress_test_plan(plan, worse_hedge_odds_pct=worse_pct)
        hedged = sum(1 for x in rows if x.get("state") == "HEDGED")
        panic = sum(1 for x in rows if x.get("state") == "PANIC")
        complete = sum(1 for x in rows if x.get("state") == "COMPLETE")
        worst_captured = min((float(x.get("captured_profit") or 0.0) for x in rows), default=0.0)
        max_before_exposure = max((float((x.get("before_hedge") or {}).get("exposure_spread") or 0.0) for x in rows), default=0.0)
        return {
            "ok": True,
            "mode": "paper-only",
            "live_order_placement": False,
            "plan": plan.as_dict(),
            "summary": {
                "scenarios": len(rows), "complete": complete, "hedged": hedged, "panic": panic,
                "worst_captured_profit": round(worst_captured, 4),
                "max_pre_hedge_exposure_spread": round(max_before_exposure, 4),
            },
            "rows": rows,
        }

    @staticmethod
    def _parse_utc(value):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _viewer_timezone(data=None):
        data = data or {}
        name = str(data.get("timezone_name") or "").strip()
        if name:
            try:
                return ZoneInfo(name), name
            except Exception:
                pass
        try:
            offset = max(-840, min(840, int(float(data.get("timezone_offset_minutes") or 0))))
        except Exception:
            offset = 0
        # JavaScript getTimezoneOffset() is UTC - local.
        return timezone(timedelta(minutes=-offset)), ""

    @classmethod
    def _local_today_bounds(cls, data=None):
        local_tz, tz_name = cls._viewer_timezone(data or {})
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(local_tz)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return {
            "from_utc": start_local.astimezone(timezone.utc),
            "to_utc": now_utc + timedelta(seconds=1),
            "local_date": start_local.date().isoformat(),
            "timezone_name": tz_name,
        }

    @staticmethod
    def _row_exchange_match(row, exchange):
        exchange = str(exchange or "").strip().lower()
        if not exchange or exchange == "all":
            return True
        values = row.get("exchanges") or []
        return any(exchange in str(x or "").lower() for x in values)

    @staticmethod
    def _exchanges_from_legs(legs_json):
        try:
            payload = json.loads(legs_json or "[]")
            return sorted({str(x.get("exchange") or "").strip() for x in payload if str(x.get("exchange") or "").strip()})
        except Exception:
            return []

    @staticmethod
    def _venues_from_legs(legs_json):
        """Return canonical venue ids while preserving legacy exchange evidence."""
        try:
            payload = json.loads(legs_json or "[]")
        except Exception:
            return []
        out = set()
        for leg in payload:
            venue_id = str(leg.get("venue_id") or "").strip()
            if not venue_id:
                venue_id = provider_id_for_name(leg.get("exchange"))
            if venue_id:
                out.add(venue_id)
        return sorted(out)

    @staticmethod
    def _text_match(row, sport="all", market="", search=""):
        sport = str(sport or "all").strip().lower()
        market = str(market or "").strip().lower()
        search = str(search or "").strip().lower()
        if sport != "all" and str(row.get("sport") or "Unknown").lower() != sport:
            return False
        if market and market not in str(row.get("market_name") or "").lower():
            return False
        if search:
            hay = " ".join(str(row.get(k) or "") for k in ("event_name", "event_key", "market_name", "sport", "strategy", "outcome")).lower()
            if search not in hay:
                return False
        return True

    def venue_provider_manifest(self, data=None):
        """Serializable provider/runtime manifest; no provider-native objects escape."""
        return {
            "ok": True,
            "providers": self.provider_runtime.manifest(),
            "service_boundary": dict(SERVICE_BOUNDARY_MANIFEST),
            "live_execution": False,
            "live_master_locked": True,
            "note": "0.9.8 read-only LIVE accounts plus isolated provider-derived decision evidence; SIM/LIVE finances remain isolated and LIVE execution remains locked.",
        }

    def activity_analytics(self, data=None):
        """Unified evidence feed for Results / Monitor / future LIVE UI with shared filtering."""
        data = data or {}
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        include_demo = not bool(cfg.get("hide_demo_data", True))
        try:
            limit = min(5000, max(1, int(data.get("limit") or 1000)))
        except (TypeError, ValueError):
            limit = 1000
        mode = str(data.get("mode") or "all").lower()
        sport = str(data.get("sport") or "all")
        exchange = str(data.get("exchange") or "all")
        market = str(data.get("market") or "")
        search = str(data.get("search") or "")
        phase = str(data.get("phase") or "all").lower()
        domain = str(data.get("domain") or "all").lower()
        if domain not in {"all", "sports", "racing"}:
            domain = "all"
        # Domain separation is enforced here on the server-side analytical feed.
        # Browser filters may narrow a domain further but cannot broaden it.
        if domain == "sports" and phase == "racing":
            phase = "all"
        elif domain == "racing":
            phase = "racing"
        timeline_range = bool(data.get("timeline_range"))
        date_from = self._parse_utc(data.get("from_utc"))
        date_to = self._parse_utc(data.get("to_utc"))
        # v0.8.27: callers can request only the evidence they render. Defaults
        # preserve the historical API contract, while Results/Replay/Dashboard
        # avoid loading unrelated datasets and timing analytics.
        include_results = bool(data.get("include_results", True))
        include_executions = bool(data.get("include_executions", True))
        include_metrics = bool(data.get("include_metrics", True))
        include_all_time = bool(data.get("include_all_time", True))

        all_results = self.db.stored_result_history(
            limit=5000, include_demo=include_demo,
            from_utc=date_from.isoformat() if date_from else None,
            to_utc=date_to.isoformat() if date_to else None,
            sport=sport, market=market, search=search,
        ) if include_results else []
        need_execution_rows = include_executions or (include_results and mode not in {"", "all"})
        if need_execution_rows:
            all_executions = self.db.execution_history(
                limit=5000, mode=mode, include_demo=include_demo,
                from_utc=date_from.isoformat() if date_from else None,
                to_utc=date_to.isoformat() if date_to else None,
                sport=sport, market=market, search=search, timeline_range=timeline_range,
            )
        else:
            all_executions = []
        mode_ids = None if mode in {"", "all"} else {int(x.get("opportunity_id") or 0) for x in all_executions}

        def in_range(value):
            dt = self._parse_utc(value)
            if not dt:
                return date_from is None and date_to is None
            if date_from and dt < date_from:
                return False
            if date_to and dt >= date_to:
                return False
            return True

        results = []
        for row in all_results:
            # v0.8.27: settled Results use settlement observation time everywhere.
            stamp = row.get("result_observed_at") or row.get("event_start")
            if not in_range(stamp):
                continue
            if mode_ids is not None and not (set(int(x or 0) for x in (row.get("opportunity_ids") or [])) & mode_ids):
                continue
            if not self._text_match(row, sport=sport, market=market, search=search):
                continue
            if not self._row_exchange_match(row, exchange):
                continue
            results.append(row)
        results = results[:limit]

        def execution_diagnostics(row):
            details = row.get("details") or {}
            state = str(row.get("state") or "").upper()
            result = details.get("execution_result") or {}
            fills = result.get("fills") or []
            events = result.get("events") or []
            hedge_fills = [x for x in fills if bool(x.get("is_hedge"))]
            emergency_events = [x for x in events if str(x.get("state") or "").upper() == "EMERGENCY_HEDGE"]
            failed_leg_events = [x for x in events if str(x.get("state") or "").upper() == "LEG_FAILED"]
            partial_leg_events = [x for x in events if str(x.get("state") or "").upper() == "LEG_PARTIAL"]
            position_opened = bool(details.get("monitor_position_opened")) or "_OPEN" in state or "_SETTLED" in state
            executed = bool(position_opened)
            missed = ("MISSED" in state or "SKIPPED" in state or "FAILED" in state) and not executed
            observations = details.get("observations") or []
            reason = details.get("first_failure_reason") or details.get("monitor_reason")
            if not reason:
                failed = next((x for x in observations if not bool(x.get("still_executable"))), None)
                reason = (failed or {}).get("failure_reason")
            raw_leakage = float(row.get("execution_leakage") or 0.0)
            expected = float(row.get("expected_profit") or 0.0)
            # A quote that disappeared before a position was opened is opportunity
            # value lost, not execution leakage. Leakage starts only after the
            # transaction engine actually opens a simulated/real position.
            opportunity_lost = expected if missed else 0.0
            effective_leakage = raw_leakage if executed else 0.0
            checkpoints = []
            for obs in observations:
                offset = int(obs.get("offset_ms") or 0)
                if offset <= 0:
                    continue
                checkpoints.append({
                    "offset_ms": offset,
                    "still_executable": bool(obs.get("still_executable")),
                    "failure_reason": obs.get("failure_reason"),
                    "expected_profit": round(float(obs.get("expected_profit") or 0.0), 4),
                    "expected_roi_pct": round(float(obs.get("expected_roi_pct") or 0.0), 6),
                    "executable_fraction": round(float(obs.get("executable_fraction") or 0.0), 6),
                    "fetch_latency_ms": int(obs.get("fetch_latency_ms") or 0),
                })
            return {
                "executed": executed,
                "missed": missed,
                "reason": reason,
                "fill_count": len(fills),
                "emergency_hedge": bool(emergency_events),
                "hedge_fill_count": len(hedge_fills),
                "failed_leg_count": len(failed_leg_events),
                "partial_leg_count": len(partial_leg_events),
                "execution_actions": [
                    {
                        "state": str(event.get("state") or ""),
                        "exchange": event.get("exchange"),
                        "venue_id": event.get("venue_id") or provider_id_for_name(event.get("exchange")),
                        "provider_id": event.get("provider_id") or provider_id_for_name(event.get("exchange")),
                        "selection": event.get("selection"),
                        "stake": event.get("stake"),
                        "odds": event.get("odds"),
                        "fraction": event.get("fraction"),
                        "exposure_spread": event.get("exposure_spread"),
                        "limit": event.get("limit"),
                        "at": event.get("at"),
                    }
                    for event in events
                    if str(event.get("state") or "").upper() in {
                        "LEG_FAILED", "LEG_PARTIAL", "LEG_FILLED", "EMERGENCY_HEDGE",
                        "HEDGING", "HEDGE_CAPITAL_LIMITED", "HEDGE_REJECTED_NO_CAPITAL",
                        "HEDGED", "PANIC",
                    }
                ],
                "opportunity_lost": round(opportunity_lost, 4),
                "execution_leakage": round(effective_leakage, 4),
                "checkpoints": checkpoints,
            }

        def execution_in_range(row):
            if not timeline_range:
                return in_range(row.get("started_at"))
            stamps = [row.get("started_at"), row.get("settled_at"), row.get("finished_at")]
            details = row.get("details") or {}
            result = details.get("execution_result") or {}
            for event in result.get("events") or []:
                stamps.append(event.get("at"))
            return any(stamp and in_range(stamp) for stamp in stamps)

        executions = []
        for row in all_executions:
            if not execution_in_range(row):
                continue
            if not self._text_match(row, sport=sport, market=market, search=search):
                continue
            if not self._row_exchange_match(row, exchange):
                continue
            stream_name = str(row.get("monitor_stream") or "pre_match")
            if domain == "sports" and stream_name not in {"pre_match", "in_play"}:
                continue
            if domain == "racing" and stream_name != "racing":
                continue
            if phase in {"pre_match", "in_play", "racing"} and stream_name != phase:
                continue
            row = dict(row)
            if row.get("provenance_mode"): row["mode"] = str(row.get("provenance_mode") or row.get("mode") or "sim").lower()
            self._attach_engine_provenance(row, fallback_section=str(row.get("section") or ("racing" if stream_name == "racing" else "sports")))
            self._attach_venue_account(row)
            row["venues"] = self._venues_from_legs(row.get("legs_json"))
            row.update(self._execution_value_metrics(row))
            row["commission_audit"] = self._settled_commission_audit(row)
            row["diagnostics"] = execution_diagnostics(row)
            row["diagnostics"]["commission_erosion"] = bool((row.get("commission_audit") or {}).get("commission_erosion"))
            row["diagnostics"]["post_commission_negative"] = bool((row.get("commission_audit") or {}).get("post_commission_negative"))
            executions.append(row)
        executions = executions[:limit]

        def counts_for(rows):
            empty_bucket = lambda: {"count": 0, "executed": 0, "missed": 0, "expected": 0.0, "captured": 0.0, "leakage": 0.0, "execution_leakage": 0.0, "opportunity_lost": 0.0}
            out = {"sim": empty_bucket(), "live": empty_bucket()}
            for row in rows:
                key = canonical_mode_value(row.get("mode"))
                if key not in {"sim", "live"}: key = "sim"
                bucket = out.setdefault(key, {"count": 0, "executed": 0, "missed": 0, "expected": 0.0, "captured": 0.0, "leakage": 0.0, "execution_leakage": 0.0, "opportunity_lost": 0.0})
                diag = row.get("diagnostics") or execution_diagnostics(row)
                bucket["count"] += 1
                bucket["executed"] += int(bool(diag.get("executed")))
                bucket["missed"] += int(bool(diag.get("missed")))
                bucket["expected"] += float(row.get("expected_profit") or 0.0)
                bucket["captured"] += float(row.get("captured_profit") or 0.0)
                # Keep raw leakage for compatibility/debugging, but expose the
                # professionally-labelled execution-only metric separately.
                bucket["leakage"] += float(row.get("execution_leakage") or 0.0)
                bucket["execution_leakage"] += float(diag.get("execution_leakage") or 0.0)
                bucket["opportunity_lost"] += float(diag.get("opportunity_lost") or 0.0)
            for bucket in out.values():
                for metric in ("expected", "captured", "leakage", "execution_leakage", "opportunity_lost"):
                    bucket[metric] = round(float(bucket[metric]), 4)
            # MONITOR remains a UI/workflow alias for canonical SIM evidence.
            # No active MONITOR_TIMING economic mode is emitted in 0.9.36.
            out["monitor"] = dict(out["sim"])
            return out

        filtered_counts = counts_for(executions)
        empty_timing = {
            "runs": 0, "initial_profit": 0.0, "reference_profit": 0.0, "execution_leakage": 0.0,
            "survival": {"100": 0.0, "250": 0.0, "500": 0.0, "1000": 0.0},
            "median_survived_through_ms": 0.0, "median_fetch_latency_ms": 0.0,
            "failure_reasons": {}, "reference_checkpoint_ms": int(cfg.get("monitor_timing_reference_checkpoint_ms", 250) or 250),
        }
        if include_metrics and mode != "live":
            monitor_timing_metrics_pre_match = self.db.monitor_timing_metrics(
                from_utc=date_from.isoformat() if date_from else None, to_utc=date_to.isoformat() if date_to else None,
                include_demo=include_demo, sport=sport, exchange=exchange, market=market, search=search, qualification_status="qualified")
            monitor_timing_metrics_in_play = self.db.monitor_timing_metrics(
                from_utc=date_from.isoformat() if date_from else None, to_utc=date_to.isoformat() if date_to else None,
                include_demo=include_demo, sport=sport, exchange=exchange, market=market, search=search, qualification_status="in_play_qualified")
        else:
            monitor_timing_metrics_pre_match = dict(empty_timing)
            monitor_timing_metrics_in_play = dict(empty_timing)
        monitor_timing_metrics = monitor_timing_metrics_in_play if phase == "in_play" else monitor_timing_metrics_pre_match
        global_counts = self.db.execution_counts(include_demo=include_demo) if include_all_time else {}
        opps = self.db.opportunity_rows(limit=10000, include_demo=include_demo) if include_all_time else []
        settled_opportunities_all = sum(1 for x in opps if x.get("outcome"))
        filtered_pending = 0
        filtered_opportunities = 0
        for row in opps:
            stamp = row.get("event_start") or row.get("detected_at")
            if not in_range(stamp):
                continue
            if mode_ids is not None and int(row.get("id") or 0) not in mode_ids:
                continue
            if not self._text_match(row, sport=sport, market=market, search=search):
                continue
            if not self._row_exchange_match({"exchanges": self._exchanges_from_legs(row.get("legs_json"))}, exchange):
                continue
            filtered_opportunities += 1
            if not row.get("outcome"):
                filtered_pending += 1
        filtered_settled_captures = sum(int(x.get("opportunity_count") or 0) for x in results)
        conflicts = sum(1 for x in results if x.get("conflict"))
        # Compact Replay period index. The UI can highlight sport/market tiles as
        # the playhead moves without making any additional API calls.
        sport_map, market_map, engine_map = {}, {}, {}
        for row in executions:
            diag = row.get("diagnostics") or {}
            if not bool(diag.get("executed")):
                continue
            sport_name = str(row.get("sport") or "Unknown")
            event_name = str(row.get("event_name") or row.get("event_key") or "Position")
            market_name = str(row.get("market_name") or "Unknown market")
            stream = str(row.get("monitor_stream") or "pre_match")
            details = row.get("details") or {}
            state = str(row.get("state") or "").upper()
            settled = bool(row.get("settled_at") or details.get("monitor_settled") or "_SETTLED" in state or state == "SETTLED")
            settled_stamp = row.get("settled_at") or (row.get("finished_at") if settled else None)
            pnl_value = float(row.get("final_pnl") if row.get("final_pnl") is not None else (row.get("captured_profit") or 0.0)) if settled else 0.0
            sitem = sport_map.setdefault(sport_name,{"sport":sport_name,"positions":0,"wins":0,"losses":0,"pnl":0.0,"position_ids":[]})
            sitem["positions"] += 1; sitem["position_ids"].append(int(row.get("opportunity_id") or 0)); sitem["pnl"] += pnl_value
            if settled and pnl_value > 1e-9: sitem["wins"] += 1
            elif settled and pnl_value < -1e-9: sitem["losses"] += 1
            authoritative = str(row.get("engine_provenance_source") or "") in {"runtime_origin", "execution_origin"} and bool(row.get("engine_instance_id"))
            # Replay Engine controls represent actual installed/originating Engines only.
            # Historical positions without stored Engine provenance remain part of the
            # All-engines period totals/timeline, but must never be manufactured into a
            # pseudo Engine such as "Legacy / Unverified".
            if authoritative:
                engine_id = str(row.get("engine_instance_id") or "")
                engine_name = str(row.get("engine_nickname") or row.get("engine_name") or engine_id)
                eitem = engine_map.setdefault(engine_id,{"engine_id":engine_id,"engine":engine_name,"authoritative":True,"positions":0,"wins":0,"losses":0,"pnl":0.0,"position_ids":[],"start_at":None,"end_at":None})
                eitem["positions"] += 1; eitem["position_ids"].append(int(row.get("opportunity_id") or 0)); eitem["pnl"] += pnl_value
                if settled and pnl_value > 1e-9: eitem["wins"] += 1
                elif settled and pnl_value < -1e-9: eitem["losses"] += 1
                estarts=[x for x in (eitem.get("start_at"),row.get("started_at")) if x]; eends=[x for x in (eitem.get("end_at"),settled_stamp or row.get("finished_at") or row.get("started_at")) if x]
                eitem["start_at"] = min(estarts) if estarts else None; eitem["end_at"] = max(eends) if eends else None
            key = f"{sport_name}|{event_name}|{market_name}"
            mitem = market_map.setdefault(key,{"key":key,"sport":sport_name,"event_name":event_name,"market_name":market_name,"stream":stream,"positions":0,"settled":0,"wins":0,"losses":0,"break_even":0,"pnl":0.0,"position_ids":[],"start_at":None,"end_at":None})
            mitem["positions"] += 1; mitem["pnl"] += pnl_value; mitem["position_ids"].append(int(row.get("opportunity_id") or 0))
            if settled:
                mitem["settled"] += 1
                if pnl_value > 1e-9: mitem["wins"] += 1
                elif pnl_value < -1e-9: mitem["losses"] += 1
                else: mitem["break_even"] += 1
            starts=[x for x in (mitem.get("start_at"),row.get("started_at")) if x]; ends=[x for x in (mitem.get("end_at"),settled_stamp or row.get("finished_at") or row.get("started_at")) if x]
            mitem["start_at"] = min(starts) if starts else None; mitem["end_at"] = max(ends) if ends else None
        for item in list(sport_map.values()) + list(market_map.values()) + list(engine_map.values()): item["pnl"] = round(float(item.get("pnl") or 0.0),4)
        period_activity = {"sports":sorted(sport_map.values(), key=lambda x:(-x["positions"],x["sport"])),
                           "engines":sorted(engine_map.values(), key=lambda x:(-x["positions"],x["engine"])),
                           "markets":sorted(market_map.values(), key=lambda x:(-x["positions"],x["event_name"],x["market_name"]))}
        return {
            "ok": True,
            "results": results,
            "executions": executions,
            "period_activity": period_activity,
            "execution_counts": filtered_counts,
            "monitor_timing_metrics": monitor_timing_metrics,
            "monitor_timing_metrics_pre_match": monitor_timing_metrics_pre_match,
            "monitor_timing_metrics_in_play": monitor_timing_metrics_in_play,
            "all_time_execution_counts": global_counts,
            "summary": {
                "stored_opportunities": filtered_opportunities,
                "settled_opportunities": filtered_settled_captures,
                "unique_settled_markets": len(results),
                "pending_results": filtered_pending,
                "result_conflicts": conflicts,
                "filtered_results": len(results),
                "filtered_executions": len(executions),
            },
            "all_time_summary": {
                "unique_settled_markets": len(all_results),
                "settled_opportunities": settled_opportunities_all,
                "stored_opportunities": len(opps),
            },
            "filters": {
                "from_utc": date_from.isoformat() if date_from else None,
                "to_utc": date_to.isoformat() if date_to else None,
                "mode": mode, "phase": phase, "domain": domain, "sport": sport, "exchange": exchange, "market": market, "search": search,
            },
            "settlement_timing_note": "Stored settlement time is when ArbScanner observed the result. Settled Results filtering uses settlement time; Replay uses explicit event timestamps inside the selected range.",
        }

    def settled_positions(self, data=None):
        """Canonical settled-position ledger using settlement time for period filters.

        Results, Dashboard settled totals and other financial summaries should use
        this endpoint/table rather than re-filtering execution start timestamps.
        """
        data = data or {}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        include_demo = not bool(cfg.get("hide_demo_data", True))
        date_from = self._parse_utc(data.get("from_utc"))
        date_to = self._parse_utc(data.get("to_utc"))
        if str(data.get("period") or "").lower() == "today" and not date_from:
            bounds = self._local_today_bounds(data)
            date_from, date_to = bounds["from_utc"], bounds["to_utc"]
        phase = str(data.get("phase") or data.get("stream") or "all").lower()
        if phase not in {"all", "pre_match", "in_play", "racing"}:
            phase = "all"
        domain = str(data.get("domain") or "all").lower()
        if domain not in {"all", "sports", "racing"}:
            domain = "all"
        if domain == "racing":
            phase = "racing"
        elif domain == "sports" and phase == "racing":
            phase = "all"
        sport = str(data.get("sport") or "all")
        market = str(data.get("market") or "")
        search = str(data.get("search") or "")
        try:
            limit = min(20000, max(1, int(data.get("limit") or 5000)))
        except Exception:
            limit = 5000
        include_rows = bool(data.get("include_rows", True))
        fast_summary = None
        if not include_rows and not market.strip() and not search.strip():
            fast_summary = self.db.settled_monitor_summary(
                from_utc=date_from.isoformat() if date_from else None,
                to_utc=date_to.isoformat() if date_to else None, include_demo=include_demo,
                sport=sport, domain=domain, stream=phase,
            )
            rows = []
        else:
            rows = self.db.settled_monitor_positions(
                from_utc=date_from.isoformat() if date_from else None,
                to_utc=date_to.isoformat() if date_to else None, include_demo=include_demo,
                sport=sport, domain=domain, stream=phase, market=market, search=search, limit=limit,
            )
        engine_filter = str(data.get("engine") or data.get("engine_instance_id") or "").strip().lower()
        venue_filter = str(data.get("venue") or data.get("exchange") or "").strip().lower()
        account_filter = str(data.get("account") or "").strip().lower()
        mode_filter = str(data.get("mode") or "").strip().lower()
        enriched_rows = []
        for row in rows:
            self._attach_engine_provenance(row, fallback_section=str(row.get("section") or ("racing" if row.get("monitor_stream") == "racing" else "sports")))
            self._attach_venue_account(row)
            if engine_filter and engine_filter not in {"all", str(row.get("engine_instance_id") or "").lower(), str(row.get("engine_type") or "").lower(), str(row.get("engine_nickname") or "").lower()}: continue
            if venue_filter and venue_filter != "all" and venue_filter not in {str(x).lower() for x in row.get("venue_ids") or []}: continue
            if account_filter and account_filter != "all" and account_filter not in str(row.get("account") or "").lower(): continue
            if mode_filter and mode_filter != "all" and mode_filter != str(row.get("mode") or "sim").lower(): continue
            row.update(self._execution_value_metrics(row))
            # Position-level reporting precision is canonical 4dp.  Quantise the
            # row before any page-level aggregation so Results and all summary
            # surfaces reconcile exactly.
            row["final_pnl"] = round(float(row.get("realized_pnl") or 0.0), 4)
            row["deployed"] = round(float(row.get("deployed") or 0.0), 4)
            row["returned"] = round(max(0.0, row["deployed"] + row["final_pnl"]), 4)
            row["commission_audit"] = self._settled_commission_audit(row)
            # Sporting outcome comes from the canonical settlement record. It is
            # deliberately independent of ArbScanner P&L (an arb can profit for
            # more than one real-world outcome).
            row["event_result"] = row.get("outcome")
            row["result_available"] = bool(str(row.get("outcome") or "").strip())
            enriched_rows.append(row)
        if rows:
            rows = enriched_rows
            fast_summary = None
        if fast_summary is not None:
            wins, losses, breakeven = fast_summary["wins"], fast_summary["losses"], fast_summary["breakeven"]
            pnl, deployed, returned = fast_summary["pnl"], fast_summary["deployed"], fast_summary["returned"]
            execution_leakage = fast_summary.get("execution_leakage", 0.0)
            best_pnl, worst_pnl = fast_summary.get("best_pnl"), fast_summary.get("worst_pnl")
            settled_count = fast_summary["settled"]
        else:
            wins = sum(1 for x in rows if float(x.get("final_pnl") or 0.0) > 1e-9)
            losses = sum(1 for x in rows if float(x.get("final_pnl") or 0.0) < -1e-9)
            breakeven = len(rows) - wins - losses
            pnl = round(sum(float(x.get("final_pnl") or 0.0) for x in rows), 4)
            deployed = round(sum(float(x.get("deployed") or 0.0) for x in rows), 4)
            returned = round(sum(float(x.get("returned") or 0.0) for x in rows), 4)
            execution_leakage = round(sum(float(x.get("execution_leakage") or 0.0) for x in rows), 4)
            best = max(rows, key=lambda x: float(x.get("final_pnl") or 0.0), default=None)
            worst = min(rows, key=lambda x: float(x.get("final_pnl") or 0.0), default=None)
            best_pnl = None if best is None else round(float(best.get("final_pnl") or 0.0), 4)
            worst_pnl = None if worst is None else round(float(worst.get("final_pnl") or 0.0), 4)
            settled_count = len(rows)
        return {
            "ok": True, "time_basis": "settled_at", "domain": domain,
            "from_utc": date_from.isoformat() if date_from else None,
            "to_utc": date_to.isoformat() if date_to else None,
            "rows": rows,
            "summary": {
                "settled": settled_count, "wins": wins, "losses": losses, "breakeven": breakeven,
                "pnl": pnl, "deployed": deployed, "returned": returned,
                "execution_leakage": execution_leakage,
                "best_pnl": best_pnl, "worst_pnl": worst_pnl,
            },
            "settlement_timing_note": "Settled financial periods use monitor_positions.settled_at. Stored timestamps are UTC; the UI supplies local-day UTC bounds.",
        }

    def market_analysis(self, data=None):
        """Historical market-by-market comparison across Sports and Racing."""
        data = data or {}
        selected_mode = str(data.get("mode") or "sim").strip().lower()
        if selected_mode not in {"sim", "live"}:
            selected_mode = "sim"
        requested_from = self._parse_utc(data.get("from_utc"))
        requested_to = self._parse_utc(data.get("to_utc"))
        # A LIVE-selected Market Analysis projection may consume shared provider
        # observations but must never execute SIM lifecycle/economic SQL. The
        # dedicated LIVE wrapper adds isolated decision diagnostics afterwards.
        include_economics = selected_mode == "sim" and bool(data.get("_include_economics", True))
        payload = self.analytics_store.market_summary(
            requested_from.isoformat() if requested_from else None,
            requested_to.isoformat() if requested_to else None,
            include_economics=include_economics,
        )
        # All history (and any null-bounded legacy call) is resolved to the true
        # ledger + hot-tail range before the rest of Market Analysis runs.
        date_from = self._parse_utc(payload.get("history_from_utc"))
        date_to = self._parse_utc(payload.get("history_to_utc"))
        filters = MarketFilters.from_data(data)
        scope, phase, sport, search = filters.scope, filters.phase, filters.sport, filters.search
        selected_streams = set(filters.streams)

        rows = [
            row for row in (payload.get("rows") or [])
            if market_row_matches(row, filters, include_phase=True, include_search=True)
        ]
        # Rejection reasons historically follow scope/phase/stream/sport but not
        # the free-text market search. Preserve that contract explicitly.
        reasons = [
            row for row in (payload.get("reasons") or [])
            if market_row_matches(row, filters, include_phase=True, include_search=False)
        ]
        local_tz, market_timezone_name = self._viewer_timezone(data)
        def local_hour_from_utc_bucket(value):
            text = str(value or "")
            try:
                if len(text) <= 2 and text.isdigit():
                    # Backward-compatible fallback for pre-v0.8.24 DB payloads.
                    utc_hour = int(text)
                    offset = int(float(data.get("timezone_offset_minutes") or 0))
                    return (utc_hour - round(offset / 60.0)) % 24
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(local_tz).hour
            except Exception:
                return 0
        activity = {}
        for row in payload.get("activity_hours") or []:
            if not market_row_matches(row, filters, include_phase=True, include_search=False):
                continue
            local_hour = local_hour_from_utc_bucket(row.get("hour_utc"))
            bucket = activity.setdefault(local_hour, {"hour": local_hour, "observations": 0, "net_positive": 0, "qualified": 0, "executed": 0, "pnl": 0.0})
            for key in ("observations", "net_positive", "qualified"):
                bucket[key] += int(row.get(key) or 0)
        for row in payload.get("execution_hours") or []:
            if not market_row_matches(row, filters, include_phase=True, include_search=False):
                continue
            local_hour = local_hour_from_utc_bucket(row.get("hour_utc"))
            bucket = activity.setdefault(local_hour, {"hour": local_hour, "observations": 0, "net_positive": 0, "qualified": 0, "executed": 0, "pnl": 0.0})
            bucket["executed"] += int(row.get("executed") or 0)
            bucket["pnl"] += float(row.get("pnl") or 0.0)
        activity_hours = [activity.get(h, {"hour": h, "observations": 0, "net_positive": 0, "qualified": 0, "executed": 0, "pnl": 0.0}) for h in range(24)]

        def comparator_scope_match(row, *, include_phase=False):
            return market_row_matches(row, filters, include_phase=include_phase, include_search=True)

        # 0.9.0 venue coverage is exchange/venue-native discovery, not the
        # downstream matched-market pipeline. Native IDs are counted once across
        # repeated scans and canonical identities can be present on 2..N venues.
        venue_native_ids = {}
        canonical_venues = {}
        union_tokens = set()
        for row in payload.get("exchange_discovery_rows") or []:
            if not market_row_matches(
                row, filters, include_phase=True, include_search=True,
                search_fields=("sport", "market_name", "event_name", "section"), phase_field="phase",
            ):
                continue
            raw_venue = str(row.get("exchange_key") or "").lower()
            venue_id = provider_id_for_name(raw_venue) or raw_venue
            mid = str(row.get("market_id") or "")
            canonical = str(row.get("canonical_market_key") or "").strip().lower()
            if not venue_id or not mid:
                continue
            venue_native_ids.setdefault(venue_id, set()).add(mid)
            token = f"canonical:{canonical}" if canonical else f"native:{venue_id}:{mid}"
            union_tokens.add(token)
            if canonical:
                canonical_venues.setdefault(canonical, set()).add(venue_id)

        opportunity_counts = {}
        for row in payload.get("opportunity_venue_rows") or []:
            if not comparator_scope_match(row, include_phase=True):
                continue
            try:
                legs = json.loads(row.get("legs_json") or "[]")
            except Exception:
                legs = []
            venues_for_opportunity = set()
            for leg in legs if isinstance(legs, list) else []:
                if not isinstance(leg, dict):
                    continue
                venue_id = provider_id_for_name(str(leg.get("venue_id") or leg.get("provider_id") or leg.get("exchange") or ""))
                if venue_id:
                    venues_for_opportunity.add(venue_id)
            for venue_id in venues_for_opportunity:
                opportunity_counts[venue_id] = opportunity_counts.get(venue_id, 0) + 1

        venue_ids = sorted(set(venue_native_ids) | set(opportunity_counts))
        venue_coverage = []
        for venue_id in venue_ids:
            spec = self.provider_runtime.providers.get(venue_id) if self.provider_runtime is not None else None
            label = spec.venue.venue_name if spec is not None else venue_id.replace("_", " ").title()
            venue_coverage.append({
                "venue_id": venue_id,
                "venue": label,
                "markets": len(venue_native_ids.get(venue_id) or set()),
                "opportunities": int(opportunity_counts.get(venue_id) or 0),
            })

        # 0.9.1 liquidity analytics. Period liquidity is compact hourly evidence;
        # current depth/status is deliberately separate so a 30-day card never
        # presents a current order book value as though it were a 30-day total.
        liquidity_period = self.db.liquidity_market_summary_between(
            date_from.isoformat() if date_from else None,
            date_to.isoformat() if date_to else None,
        )

        def liquidity_scope_match(row):
            return market_row_matches(row, filters, include_phase=True, include_search=True)

        depth_by_market = {}
        for row in liquidity_period.get("depth") or []:
            if not liquidity_scope_match(row): continue
            key = (str(row.get("section") or "sports"), str(row.get("sport") or "Unknown"),
                   str(row.get("market_name") or "Unknown"), int(row.get("in_play") or 0))
            depth_by_market[key] = dict(row)
        liquidity_by_market = {}
        for row in liquidity_period.get("opportunity") or []:
            if not liquidity_scope_match(row): continue
            key = (str(row.get("section") or "sports"), str(row.get("sport") or "Unknown"),
                   str(row.get("market_name") or "Unknown"), int(row.get("in_play") or 0))
            liquidity_by_market[key] = dict(row)

        venue_sets_by_market = {}
        for row in payload.get("exchange_discovery_rows") or []:
            if not comparator_scope_match(row, include_phase=False): continue
            row_phase = str(row.get("phase") or "pre_match")
            in_play_key = 1 if row_phase == "in_play" else 0
            if phase == "pre_match" and in_play_key != 0: continue
            if phase == "in_play" and in_play_key != 1: continue
            pid = provider_id_for_name(str(row.get("exchange_key") or "")) or str(row.get("exchange_key") or "").lower()
            if not pid: continue
            key = (str(row.get("section") or "sports"), str(row.get("sport") or "Unknown"),
                   str(row.get("market_name") or "Unknown"), in_play_key)
            venue_sets_by_market.setdefault(key, set()).add(pid)

        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        stale_thresholds = {}
        for spec in self.provider_runtime.providers.all():
            profile = self.provider_runtime.profile(spec.provider_id)
            stale_thresholds[spec.provider_id] = float(profile.stale_quote_limit_seconds if profile is not None else cfg.get("price_quote_max_age_seconds", 10.0) or 10.0)
        current_depth = {x["provider_id"]: x for x in self.db.latest_liquidity_summary(
            stale_after_seconds=stale_thresholds, scope=scope, phase=phase, sport=sport, search=search
        )}
        operational_feeds = {str(x.get("provider_id") or x.get("key") or "").lower(): x for x in (self._operational_status(selected_mode, feeds_only=True).get("feeds") or [])}
        coverage_map = {str(x.get("venue_id") or "").lower(): x for x in venue_coverage}
        venue_summary = []
        active_provider_ids = set()
        for spec in self.provider_runtime.providers.all():
            pid = spec.provider_id.lower(); profile = self.provider_runtime.profile(pid); runtime = self.provider_runtime.runtime_status(pid)
            feed = operational_feeds.get(pid) or {}
            mode_state = (feed.get("mode_states") or {}).get(selected_mode) or {}
            feed_expected = bool(mode_state.get("feed_expected", mode_state.get("feed_enabled", False)))
            feed_enabled = bool(mode_state.get("feed_enabled", feed_expected))
            pending_api = str(feed.get("api_state") or (profile.api_state if profile is not None else "")).lower() in {"pending_api", "awaiting_api_access"}
            transport_state = str(feed.get("transport_state") or feed.get("state") or "unknown").lower()
            freshness_state = str(feed.get("freshness_state") or "unavailable").lower()
            raw_state = str(mode_state.get("feed_state") or feed.get("state") or "unknown").lower()
            provider_stale_after = float(stale_thresholds.get(pid, 10.0) or 10.0)
            try:
                feed_age_seconds = float(feed.get("age_seconds")) if feed.get("age_seconds") is not None else None
            except (TypeError, ValueError):
                feed_age_seconds = None
            if not feed_expected:
                status = "awaiting_api_access" if pending_api else "not_expected"
            elif not feed_enabled:
                status = "disabled"
            elif pending_api:
                status = "awaiting_api_access"
            elif transport_state in {"error", "failed", "failure", "offline", "disconnected"}:
                status = "disconnected"
            elif freshness_state == "stale" or raw_state == "stale" or (feed_age_seconds is not None and feed_age_seconds > provider_stale_after):
                status = "stale"
            elif transport_state == "connected" and freshness_state in {"fresh", "unknown", "unavailable"}:
                status = "active"
            elif raw_state in {"waiting", "unknown"}:
                status = "pending"
            else:
                status = "active" if raw_state in {"connected", "ready", "healthy"} else "pending"
            if status == "active": active_provider_ids.add(pid)
            cov = coverage_map.get(pid) or {}; depth = current_depth.get(pid) or {}
            venue_summary.append({
                "provider_id": pid, "venue_id": spec.venue.venue_id, "display_name": spec.venue.venue_name,
                "enabled": feed_enabled, "analytics_status": status, "connection_state": transport_state,
                "selected_mode": selected_mode, "selected_mode_expected": feed_expected,
                "selected_mode_enabled": feed_enabled, "selected_mode_state": mode_state.get("state"),
                "freshness_state": freshness_state, "authoritative_market_data": bool(feed_expected and status not in {"not_expected", "awaiting_api_access", "disabled"}),
                "last_quote_at": depth.get("last_quote_at") or feed.get("last_snapshot_at"),
                "last_success_at": runtime.last_success_at, "market_count": int(cov.get("markets") or 0),
                "opportunities": int(cov.get("opportunities") or 0),
                "current_market_count": int(depth.get("current_markets") or 0),
                "top_book_depth": round(float(depth.get("top_book_depth") or 0.0), 4),
                "top3_depth": round(float(depth.get("top3_depth") or 0.0), 4),
                "fresh_depth_rows": int(depth.get("fresh_depth_rows") or 0), "stale_depth_rows": int(depth.get("stale_depth_rows") or 0),
                "feed_entitlement": str(feed.get("effective_feed_entitlement") or (profile.feed_entitlement.value if profile is not None else depth.get("feed_entitlement") or "unknown")),
                "transport": str(feed.get("transport") or (profile.market_data_transport.value if profile is not None else depth.get("transport") or "unknown")),
                "liquidity_supported": bool(spec.capabilities.executable_capacity),
                "message": feed.get("message") or runtime.degraded_reason,
            })

        for row in rows:
            key = (str(row.get("section") or "sports"), str(row.get("sport") or "Unknown"),
                   str(row.get("market_name") or "Unknown"), int(row.get("in_play") or 0))
            dr = depth_by_market.get(key) or {}; lr = liquidity_by_market.get(key) or {}
            depth_samples = int(dr.get("depth_samples") or 0); exec_samples = int(lr.get("executable_stake_samples") or 0)
            venues_seen = venue_sets_by_market.get(key) or set()
            row["venues_observed"] = len(venues_seen)
            row["active_venues"] = len(set(venues_seen) & active_provider_ids)
            row["avg_top_book_depth"] = round(float(dr.get("top_book_depth_sum") or 0.0) / depth_samples, 4) if depth_samples else None
            row["avg_available_depth"] = round(float(dr.get("top3_depth_sum") or 0.0) / depth_samples, 4) if depth_samples else None
            row["max_available_depth"] = round(float(dr.get("max_top3_depth") or 0.0), 4) if depth_samples else None
            row["avg_executable_stake"] = round(float(lr.get("executable_stake_sum") or 0.0) / exec_samples, 4) if exec_samples else None
            row["liquidity_capable"] = int(lr.get("liquidity_capable") or 0)
            row["liquidity_rejected"] = int(lr.get("liquidity_rejected") or 0)
            liq_total = row["liquidity_capable"] + row["liquidity_rejected"]
            row["liquidity_rejection_rate_pct"] = round((row["liquidity_rejected"] / liq_total) * 100.0, 3) if liq_total else 0.0

        liquidity_funnel = {
            "observed": sum(int(x.get("observations") or 0) for x in rows),
            "positive": sum(int(x.get("positive_observations") or 0) for x in liquidity_by_market.values()),
            "liquidity_capable": sum(int(x.get("liquidity_capable") or 0) for x in liquidity_by_market.values()),
            "qualified": sum(int(x.get("qualified") or 0) for x in rows),
            "attempted": sum(int(x.get("attempts") or 0) for x in rows),
            "executed": sum(int(x.get("executed") or 0) for x in rows),
            "settled": sum(int(x.get("settled") or 0) for x in rows),
        }

        multi_venue_markets = sum(1 for venues_for_market in canonical_venues.values() if len(venues_for_market) >= 2)
        single_venue_canonical = sum(1 for venues_for_market in canonical_venues.values() if len(venues_for_market) == 1)
        union_markets = len(union_tokens)
        bf_ids = venue_native_ids.get("betfair") or set()
        mb_ids = venue_native_ids.get("matchbook") or set()
        bf_canonical = {key for key, values in canonical_venues.items() if "betfair" in values}
        mb_canonical = {key for key, values in canonical_venues.items() if "matchbook" in values}
        betfair_markets = len(bf_ids)
        matchbook_markets = len(mb_ids)
        overlap_markets = len(bf_canonical & mb_canonical)
        betfair_opportunities = int(opportunity_counts.get("betfair") or 0)
        matchbook_opportunities = int(opportunity_counts.get("matchbook") or 0)

        exchange_comparator = {
            # 0.8.x compatibility fields used by the existing Market Analysis cards.
            "betfair_markets": betfair_markets,
            "betfair_opportunities": betfair_opportunities,
            "matchbook_markets": matchbook_markets,
            "matchbook_opportunities": matchbook_opportunities,
            "overlap_markets": overlap_markets,
            "union_markets": union_markets,
            "overlap_pct": round((overlap_markets / union_markets) * 100.0, 3) if union_markets else 0.0,
            # Canonical N-venue contract.
            "venue_coverage": venue_coverage,
            "multi_venue_markets": multi_venue_markets,
            "single_venue_canonical_markets": single_venue_canonical,
            "canonical_market_venue_counts": {key: len(values) for key, values in canonical_venues.items()},
            "market_definition": "Distinct venue-native market IDs discovered in the selected period. Repeated scans do not inflate coverage.",
            "overlap_definition": "Canonical event/market identities may map to two or more venues. Betfair/Matchbook overlap is retained as a compatibility view.",
            "opportunity_definition": "Stored non-demo opportunity records in which the venue contributes at least one planned leg.",
        }
        return {
            "ok": True,
            "from_utc": date_from.isoformat() if date_from else None,
            "to_utc": date_to.isoformat() if date_to else None,
            "rows": rows,
            "reasons": reasons,
            "activity_hours": activity_hours,
            "selected_streams": filters.selected_streams_response,
            "timezone_name": market_timezone_name,
            "financial_time_basis": "settled_at",
            "sports_discovery": payload.get("sports_discovery") or {},
            "sports_scans": payload.get("sports_scans") or [],
            "racing_discovery": payload.get("racing_discovery") or {},
            "racing_scans": payload.get("racing_scans") or [],
            "latest_racing_discovery": self.db.get_setting("racing_discovery_latest", {}) or {},
            "matchbook_price_side_audit": self.db.get_setting("matchbook_price_side_audit_latest", {}) or {},
            "venue_summary": venue_summary,
            "liquidity_funnel": liquidity_funnel,
            "liquidity_definitions": {
                "top_book_depth": "Current amount at level 1 across fresh quotes in the filtered venue universe.",
                "top3_depth": "Current amount across levels 1-3. Stale levels are excluded.",
                "avg_executable_stake": "Average maximum balanced deployment observed while preserving the opportunity economics.",
            },
            "exchange_comparator": exchange_comparator,
            "summary_history_complete": bool(payload.get("summary_history_complete", True)),
            "detailed_history_complete": bool(payload.get("detailed_history_complete", True)),
            "summary_history_gaps": payload.get("summary_history_gaps") or [],
            "detailed_history_gaps": payload.get("detailed_history_gaps") or [],
            "history_available_from_utc": payload.get("history_available_from_utc"),
            "history_available_to_utc": payload.get("history_available_to_utc"),
            "history_source": payload.get("summary_source") or "hourly_rollups+hot_sqlite",
            "detailed_history_source": payload.get("detailed_source") or "verified_parquet+hot_sqlite",
        }

    def replay_market_evidence(self, data=None):
        """High-resolution market evidence for one Replay position.

        The caller is deliberately source-agnostic: AnalyticsStore selects VERIFIED
        Parquet for archived hours and hot SQLite for the current tail, refuses
        incomplete coverage by default, and never touches provider acquisition.
        """
        data = data or {}
        try:
            opportunity_id = int(data.get("opportunity_id") or data.get("id") or 0)
        except (TypeError, ValueError):
            opportunity_id = 0
        if opportunity_id <= 0:
            return {"ok": False, "message": "A valid opportunity_id is required.", "rows": []}
        include_demo = bool(data.get("include_demo", False))
        opportunity = self.db.opportunity_by_id(opportunity_id, include_demo=include_demo)
        if not opportunity:
            return {"ok": False, "message": "Replay opportunity was not found.", "rows": [], "opportunity_id": opportunity_id}

        requested_from = self._parse_utc(data.get("from_utc"))
        requested_to = self._parse_utc(data.get("to_utc"))
        range_basis = "explicit"
        if not requested_from or not requested_to:
            detected = self._parse_utc(opportunity.get("detected_at"))
            event_start = self._parse_utc(opportunity.get("event_start"))
            settled = self._parse_utc(opportunity.get("settled_at"))
            anchors = [x for x in (detected, event_start) if x]
            if not anchors:
                return {"ok": False, "message": "Replay opportunity has no usable historical timestamp.", "rows": [], "opportunity_id": opportunity_id}
            requested_from = min(anchors) - timedelta(minutes=30)
            natural_end = settled or ((event_start or detected) + timedelta(hours=6))
            requested_to = max(anchors + [natural_end]) + timedelta(minutes=30)
            # Keep an implicit click-through bounded. Research callers can request a
            # wider explicit period through detailed_market_history when required.
            if requested_to - requested_from > timedelta(days=3):
                requested_to = requested_from + timedelta(days=3)
            range_basis = "position_window"

        result = self.analytics_store.detailed_history(
            requested_from.isoformat(), requested_to.isoformat(),
            limit=max(1, min(10000, int(data.get("limit") or 5000))),
            allow_partial=bool(data.get("allow_partial", False)),
            section=opportunity.get("section"), sport=opportunity.get("sport"),
            market=opportunity.get("market_name"), event_key=opportunity.get("event_key"),
        )
        result.update({
            "opportunity_id": opportunity_id,
            "event_key": opportunity.get("event_key"),
            "event_name": opportunity.get("event_name"),
            "market_name": opportunity.get("market_name"),
            "sport": opportunity.get("sport"),
            "range_basis": range_basis,
            "provider_acquisition_touched": False,
        })
        return result

    def market_heatmap(self, data=None):
        """Fast weekly heatmap from compact hourly activity/financial rollups.

        One response contains All-sports plus per-sport cells, so changing the
        metric or sport in the UI is client-side and does not rerun history SQL.
        """
        data = data or {}
        date_from = self._parse_utc(data.get("from_utc"))
        date_to = self._parse_utc(data.get("to_utc"))
        if not date_from or not date_to or date_to <= date_from:
            return {"ok": False, "error": "A valid hourly heatmap time range is required"}
        payload = self.db.market_heatmap_between(date_from.isoformat(), date_to.isoformat(), include_financial=bool(data.get("_include_financial", True)))
        filters = MarketFilters.from_data(data)
        local_tz, tz_name = self._viewer_timezone(data)

        def keep(row):
            # Heatmap rollups historically classify Sports stream solely from the
            # stored in_play flag; preserve that distinction from detailed market
            # rows, which may carry an explicit phase hint.
            return market_row_matches(
                row, filters, include_phase=True, include_search=True, stream_phase_hint=False
            )

        local_start = date_from.astimezone(local_tz)
        first_day = local_start.date()
        day_count = max(1, min(14, (date_to.astimezone(local_tz).date() - first_day).days))
        day_names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

        def blank_cell(day, hour):
            return {
                "date": day.isoformat(), "day_index": day.weekday(), "day_label": day_names[day.weekday()], "hour": hour,
                "observations": 0, "unique_markets": 0, "net_positive": 0, "qualified": 0, "executed": 0, "deployed": 0.0,
                "settled": 0, "settled_deployed": 0.0, "pnl": 0.0, "roi_pct": 0.0,
                "depth_samples": 0, "top_book_depth_sum": 0.0, "top3_depth_sum": 0.0,
                "executable_stake_sum": 0.0, "executable_stake_samples": 0,
                "liquidity_capable": 0, "liquidity_rejected": 0,
                "observed": False, "most_active_sport": None,
            }

        base_keys=[]
        for day_index in range(day_count):
            day=first_day+timedelta(days=day_index)
            for hour in range(24): base_keys.append((day.isoformat(),hour,day))
        all_cells={(date_key,hour):blank_cell(day,hour) for date_key,hour,day in base_keys}
        sport_cells: dict[str,dict] = {}
        sport_activity: dict[tuple[str,int],dict[str,int]] = {}
        sports=set()

        def local_slot(value):
            try:
                dt=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                local=dt.astimezone(local_tz)
                return local.date().isoformat(), local.hour, local.date()
            except Exception:
                return first_day.isoformat(), 0, first_day

        def get_cell(store, date_key, hour, day):
            if (date_key,hour) not in store: store[(date_key,hour)]=blank_cell(day,hour)
            return store[(date_key,hour)]

        def get_sport_store(sport_name):
            if sport_name not in sport_cells:
                sport_cells[sport_name]={(date_key,hour):blank_cell(day,hour) for date_key,hour,day in base_keys}
            return sport_cells[sport_name]

        for row in payload.get("rollups") or []:
            if not keep(row): continue
            sport_name=str(row.get("sport") or "Unknown"); sports.add(sport_name)
            date_key,hour,day=local_slot(row.get("hour_utc"))
            values={"observations":int(row.get("observations") or 0),"unique_markets":int(row.get("unique_markets") or 0),"net_positive":int(row.get("net_positive") or 0)}
            for store in (all_cells,get_sport_store(sport_name)):
                c=get_cell(store,date_key,hour,day); c["observations"] += values["observations"]; c["unique_markets"] += values["unique_markets"]; c["net_positive"] += values["net_positive"]; c["observed"]=True
            sa=sport_activity.setdefault((date_key,hour),{}); sa[sport_name]=sa.get(sport_name,0)+values["observations"]

        for row in payload.get("financial") or []:
            if not keep(row): continue
            sport_name=str(row.get("sport") or "Unknown"); sports.add(sport_name)
            date_key,hour,day=local_slot(row.get("hour_utc"))
            for store in (all_cells,get_sport_store(sport_name)):
                c=get_cell(store,date_key,hour,day)
                c["qualified"] += int(row.get("qualified") or 0)
                c["executed"] += int(row.get("executed") or 0)
                c["deployed"] += float(row.get("deployed") or 0.0)
                c["settled"] += int(row.get("settled") or 0)
                c["settled_deployed"] += float(row.get("settled_deployed") or 0.0)
                c["pnl"] += float(row.get("pnl") or 0.0)
                c["observed"]=True

        for row in payload.get("liquidity_depth") or []:
            if not keep(row): continue
            sport_name=str(row.get("sport") or "Unknown"); sports.add(sport_name)
            date_key,hour,day=local_slot(row.get("hour_utc"))
            for store in (all_cells,get_sport_store(sport_name)):
                c=get_cell(store,date_key,hour,day)
                c["depth_samples"] += int(row.get("depth_samples") or 0)
                c["top_book_depth_sum"] += float(row.get("top_book_depth_sum") or 0.0)
                c["top3_depth_sum"] += float(row.get("top3_depth_sum") or 0.0)
                c["observed"] = True

        for row in payload.get("liquidity_opportunity") or []:
            if not keep(row): continue
            sport_name=str(row.get("sport") or "Unknown"); sports.add(sport_name)
            date_key,hour,day=local_slot(row.get("hour_utc"))
            for store in (all_cells,get_sport_store(sport_name)):
                c=get_cell(store,date_key,hour,day)
                c["liquidity_capable"] += int(row.get("liquidity_capable") or 0)
                c["liquidity_rejected"] += int(row.get("liquidity_rejected") or 0)
                c["executable_stake_sum"] += float(row.get("executable_stake_sum") or 0.0)
                c["executable_stake_samples"] += int(row.get("executable_stake_samples") or 0)
                c["observed"] = True

        def finish(store, *, include_active=False):
            out=[]
            for key,item in sorted(store.items(), key=lambda kv:(kv[0][0],kv[0][1])):
                item=dict(item)
                for k in ("deployed","settled_deployed","pnl","top_book_depth_sum","top3_depth_sum","executable_stake_sum"): item[k]=round(float(item[k]),4)
                item["roi_pct"]=round((item["pnl"]/item["settled_deployed"])*100.0,4) if item["settled_deployed"] else 0.0
                item["available_depth"] = round(item["top3_depth_sum"] / item["depth_samples"], 4) if item["depth_samples"] else 0.0
                item["top_book_depth"] = round(item["top_book_depth_sum"] / item["depth_samples"], 4) if item["depth_samples"] else 0.0
                item["avg_executable_stake"] = round(item["executable_stake_sum"] / item["executable_stake_samples"], 4) if item["executable_stake_samples"] else 0.0
                liq_total = int(item["liquidity_capable"] or 0) + int(item["liquidity_rejected"] or 0)
                item["liquidity_rejection_rate_pct"] = round((int(item["liquidity_rejected"] or 0) / liq_total) * 100.0, 4) if liq_total else 0.0
                if include_active:
                    activity=sport_activity.get(key) or {}
                    item["most_active_sport"]=max(activity,key=activity.get) if activity else None
                out.append(item)
            return out

        all_out=finish(all_cells,include_active=True)
        by_sport={name:finish(store) for name,store in sorted(sport_cells.items())}
        # Backwards-compatible 24-hour aggregate for old render/tests.
        hours=[]
        for h in range(24):
            selected=[x for x in all_out if int(x.get("hour") or 0)==h]
            dep=sum(float(x.get("deployed") or 0) for x in selected)
            sdep=sum(float(x.get("settled_deployed") or 0) for x in selected)
            pnl=sum(float(x.get("pnl") or 0) for x in selected)
            depth_samples=sum(int(x.get("depth_samples") or 0) for x in selected); exec_samples=sum(int(x.get("executable_stake_samples") or 0) for x in selected)
            top3_sum=sum(float(x.get("top3_depth_sum") or 0.0) for x in selected); exec_sum=sum(float(x.get("executable_stake_sum") or 0.0) for x in selected)
            liq_cap=sum(int(x.get("liquidity_capable") or 0) for x in selected); liq_rej=sum(int(x.get("liquidity_rejected") or 0) for x in selected); liq_total=liq_cap+liq_rej
            top_book_sum=sum(float(x.get("top_book_depth_sum") or 0.0) for x in selected)
            settled=sum(int(x.get("settled") or 0) for x in selected)
            hours.append({"hour":h,"observations":sum(int(x.get("observations") or 0) for x in selected),
                          "unique_markets":sum(int(x.get("unique_markets") or 0) for x in selected),
                          "net_positive":sum(int(x.get("net_positive") or 0) for x in selected),
                          "qualified":sum(int(x.get("qualified") or 0) for x in selected),
                          "executed":sum(int(x.get("executed") or 0) for x in selected),"deployed":round(dep,4),
                          "settled":settled,"settled_deployed":round(sdep,4),
                          "pnl":round(pnl,4),"roi_pct":round((pnl/sdep)*100.0,4) if sdep else 0.0,
                          "available_depth":round(top3_sum/depth_samples,4) if depth_samples else 0.0,
                          "top_book_depth":round(top_book_sum/depth_samples,4) if depth_samples else 0.0,
                          "avg_executable_stake":round(exec_sum/exec_samples,4) if exec_samples else 0.0,
                          "liquidity_capable":liq_cap,"liquidity_rejected":liq_rej,
                          "liquidity_rejection_rate_pct":round((liq_rej/liq_total)*100.0,4) if liq_total else 0.0})
        metric_ownership = heatmap_metric_ownership("sim")
        # 0.9.50 route diagnostics: every selectable heatmap metric is backed by
        # one of these four explicit source streams. Keep the diagnostic compact
        # so operator/test audits can detect a disconnected source without
        # changing the UI response contract.
        route_integrity = {
            "rollup_rows": sum(1 for row in (payload.get("rollups") or []) if keep(row)),
            "financial_rows": sum(1 for row in (payload.get("financial") or []) if keep(row)),
            "liquidity_depth_rows": sum(1 for row in (payload.get("liquidity_depth") or []) if keep(row)),
            "liquidity_opportunity_rows": sum(1 for row in (payload.get("liquidity_opportunity") or []) if keep(row)),
            "cell_count": len(all_out),
            "metric_count": len(metric_ownership),
        }
        return {
            "ok": True, "from_utc": date_from.isoformat(), "to_utc": date_to.isoformat(), "timezone_name": tz_name,
            "application_mode":"sim", "financial_mode":"sim",
            "hours": hours, "cells": all_out, "by_sport": by_sport, "sports": sorted(sports),
            "metrics": list(HEATMAP_METRICS),
            "metric_ownership": metric_ownership, "route_integrity": route_integrity,
            "source": payload.get("source") or "compact_market_rollups+authoritative_sim_ledger",
            "financial_source": payload.get("financial_source") or "authoritative_sim_ledger",
            "note": "Weekly metric/sport switching is client-side. Shared market/liquidity metrics use compact provider-evidence rollups; lifecycle and economic cells are explicitly SIM-owned and are rebuilt from the bounded authoritative SIM opportunity/position ledger for the requested window so settlement reconciliation is reflected immediately. Liquidity depth is an hourly average of observed books rather than a sum of repeatedly observed exchange liquidity; stale current quotes are excluded from current executable calculations.",
        }

    def live_market_heatmap(self, data=None):
        """Shared provider heatmap with fail-closed LIVE lifecycle economics.

        Market/liquidity evidence is shared between SIM and LIVE. Engine/lifecycle
        financial metrics are LIVE-owned here and are never populated from SIM.
        Decision qualification remains diagnostic-only until it is an actual LIVE
        lifecycle record.
        """
        data = dict(data or {})
        base = self.market_heatmap({**data, "_include_financial": False})
        if not base.get("ok"):
            return base
        date_from = self._parse_utc(data.get("from_utc"))
        date_to = self._parse_utc(data.get("to_utc"))
        filters = MarketFilters.from_data(data)
        domain = filters.live_heatmap_domain
        analytics = self.db.live_decision_analytics(
            date_from.isoformat() if date_from else data.get("from_utc"),
            date_to.isoformat() if date_to else data.get("to_utc"),
            domain=domain,
            sport="all",
        )
        local_tz, _ = self._viewer_timezone(data)

        # LIVE decision evidence is diagnostic and intentionally separate from
        # canonical Qualified. Sport-aware rollups prevent an all-sports total
        # being duplicated into every per-sport heatmap cell. The current LIVE
        # decision hourly rollup does not carry phase, so diagnostic evidence is
        # suppressed for phase-specific views rather than pretending precision.
        qmap_all: dict[tuple[str, int], int] = {}
        qmap_sport: dict[tuple[str, str, int], int] = {}
        if filters.live_decision_hourly_is_precise:
            for row in analytics.get("hourly") or []:
                try:
                    dt = datetime.fromisoformat(str(row.get("hour_utc") or "").replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    local = dt.astimezone(local_tz)
                    key = (local.date().isoformat(), local.hour)
                except Exception:
                    continue
                qmap_all[key] = qmap_all.get(key, 0) + int(row.get("qualified") or 0)
            for row in analytics.get("hourly_by_sport") or []:
                sport_name = str(row.get("sport") or "Unknown")
                try:
                    dt = datetime.fromisoformat(str(row.get("hour_utc") or "").replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    local = dt.astimezone(local_tz)
                    key = (sport_name, local.date().isoformat(), local.hour)
                except Exception:
                    continue
                qmap_sport[key] = qmap_sport.get(key, 0) + int(row.get("qualified") or 0)

        def clean(cell, *, sport_name=None):
            slot = (str(cell.get("date") or ""), int(cell.get("hour") or 0))
            if sport_name is None:
                decision_count = int(qmap_all.get(slot, 0))
            else:
                decision_count = int(qmap_sport.get((str(sport_name), slot[0], slot[1]), 0))
            return live_heatmap_cell(cell, decision_count=decision_count)

        base["cells"] = [clean(x) for x in base.get("cells") or []]
        base["by_sport"] = {
            sport_name: [clean(x, sport_name=sport_name) for x in rows]
            for sport_name, rows in (base.get("by_sport") or {}).items()
        }
        hours = []
        for hour in range(24):
            selected = [x for x in base["cells"] if int(x.get("hour") or 0) == hour]
            depth_samples = sum(int(x.get("depth_samples") or 0) for x in selected)
            exec_samples = sum(int(x.get("executable_stake_samples") or 0) for x in selected)
            top3 = sum(float(x.get("top3_depth_sum") or 0.0) for x in selected)
            top_book = sum(float(x.get("top_book_depth_sum") or 0.0) for x in selected)
            exsum = sum(float(x.get("executable_stake_sum") or 0.0) for x in selected)
            cap = sum(int(x.get("liquidity_capable") or 0) for x in selected)
            rej = sum(int(x.get("liquidity_rejected") or 0) for x in selected)
            hours.append({
                "hour": hour,
                "observations": sum(int(x.get("observations") or 0) for x in selected),
                "unique_markets": sum(int(x.get("unique_markets") or 0) for x in selected),
                "net_positive": sum(int(x.get("net_positive") or 0) for x in selected),
                "decision_qualified_evidence": sum(int(x.get("decision_qualified_evidence") or 0) for x in selected),
                "qualified": 0, "executed": 0, "settled": 0,
                "deployed": 0.0, "settled_deployed": 0.0, "pnl": 0.0, "roi_pct": 0.0,
                "available_depth": round(top3 / depth_samples, 4) if depth_samples else 0.0,
                "top_book_depth": round(top_book / depth_samples, 4) if depth_samples else 0.0,
                "avg_executable_stake": round(exsum / exec_samples, 4) if exec_samples else 0.0,
                "liquidity_capable": cap, "liquidity_rejected": rej,
                "liquidity_rejection_rate_pct": round(rej / (cap + rej) * 100.0, 4) if cap + rej else 0.0,
            })
        base["hours"] = hours
        base["application_mode"] = "live"
        base["financial_mode"] = "live"
        base["orders_write_capability"] = False
        base["live_execution_allowed"] = False
        base["metrics"] = list(HEATMAP_METRICS)
        base["metric_ownership"] = heatmap_metric_ownership("live")
        base["note"] = (
            "Provider market/liquidity evidence is shared. Canonical Qualified/Executed/settlement/P&L are actual-LIVE only "
            "and never fall back to SIM. Simulated LIVE decision qualification is diagnostic-only and is not exposed as Qualified."
        )
        return base

    def dashboard_results_24h(self, data=None):
        """Compact settled-position Dashboard results.

        The legacy action name remains for API compatibility. 0.9.36 may request
        ``period=today`` so the Dashboard KPI row follows the viewer's local
        calendar day instead of a rolling 24-hour window.
        """
        data = data or {}
        now = datetime.now(timezone.utc)
        period = str(data.get("period") or "24h").strip().lower()
        if period == "today":
            local_tz, _ = self._viewer_timezone(data)
            local_now = now.astimezone(local_tz)
            start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        else:
            start = now - timedelta(hours=24)
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        rows = self.db.settled_monitor_positions(
            from_utc=start.isoformat(), to_utc=(now + timedelta(seconds=1)).isoformat(),
            include_demo=not bool(cfg.get("hide_demo_data", True)), limit=5000,
        )
        settled = [{**row, "_pnl": float(row.get("realized_pnl") or 0.0)} for row in rows]
        wins = [x for x in settled if x["_pnl"] > 1e-9]
        losses = [x for x in settled if x["_pnl"] < -1e-9]
        best = max(wins, key=lambda x: x["_pnl"], default=None)
        return {
            "ok": True, "time_basis": "settled_at", "pnl_basis": "realized_pnl",
            "settled_only": True, "period": period,
            "from_utc": start.isoformat(), "to_utc": now.isoformat(),
            "wins": len(wins), "losses": len(losses), "break_even": max(0, len(settled)-len(wins)-len(losses)),
            "decided": len(wins)+len(losses), "settled": len(settled),
            "win_rate_pct": round((len(wins) / (len(wins)+len(losses))) * 100.0, 3) if (wins or losses) else 0.0,
            "best_win": None if best is None else {
                "opportunity_id": int(best.get("opportunity_id") or 0),
                "execution_id": int(best.get("execution_run_id") or 0),
                "event_name": best.get("event_name") or best.get("event_key"),
                "market_name": best.get("market_name"), "sport": best.get("sport"),
                "pnl": round(best["_pnl"], 4), "settled_at": best.get("settled_at"),
            },
        }

    @staticmethod
    def _dashboard_latest_result_payload(row: dict | None, *, mode: str, source: str) -> dict:
        if not row:
            return {
                "ok": True, "mode": mode, "source": source, "result": None,
                "sim_fallback_used": False,
            }
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        score = row.get("score") or details.get("score") or details.get("final_score")
        pnl = row.get("final_pnl")
        if pnl is None:
            pnl = row.get("realized_pnl")
        if pnl is None:
            pnl = row.get("pnl")
        return {
            "ok": True, "mode": mode, "source": source, "sim_fallback_used": False,
            "result": {
                "opportunity_id": int(row.get("opportunity_id") or 0),
                "execution_id": int(row.get("execution_run_id") or row.get("execution_id") or 0),
                "event_name": row.get("event_name") or row.get("event_key") or "Settled position",
                "event_start": row.get("event_start"),
                "market_name": row.get("market_name") or row.get("market_type"),
                "sport": row.get("sport"),
                "stream": row.get("monitor_stream") or row.get("stream"),
                "outcome": row.get("outcome") or row.get("winner"),
                "score": score,
                "pnl": None if pnl is None else round(float(pnl or 0.0), 4),
                "settled_at": row.get("settled_at") or row.get("finished_at"),
            },
        }

    def dashboard_latest_sim_result(self, data=None):
        """Latest SIM settlement for the compact Dashboard ticker.

        This endpoint reads only the SIM Monitor settlement ledger. It never
        consults LIVE results or provider account state.
        """
        data = data or {}
        domain = str(data.get("domain") or "all").strip().lower()
        if domain not in {"all", "sports", "racing"}:
            domain = "all"
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        rows = self.db.settled_monitor_positions(
            include_demo=not bool(cfg.get("hide_demo_data", True)),
            domain=domain, limit=1,
        )
        return self._dashboard_latest_result_payload(
            rows[0] if rows else None, mode="sim", source="sim_monitor_settlement"
        )

    def dashboard_latest_live_result(self, data=None):
        """Latest actual LIVE settlement for the compact Dashboard ticker.

        Deliberately delegates only to the actual LIVE result read model. There
        is no SIM fallback: while LIVE execution is locked this correctly returns
        an empty result.
        """
        data = data or {}
        domain = str(data.get("domain") or "all").strip().lower()
        if domain not in {"all", "sports", "racing"}:
            domain = "all"
        live = self.live_results({"domain": domain})
        rows = list(live.get("rows") or []) if live.get("ok") else []
        rows.sort(key=lambda row: str(row.get("settled_at") or row.get("finished_at") or ""), reverse=True)
        return self._dashboard_latest_result_payload(
            rows[0] if rows else None, mode="live", source="actual_live_results"
        )

    @staticmethod
    def _portfolio_streams(scope: str) -> list[str]:
        """Compatibility wrapper over the shared selected-mode financial projector."""
        return portfolio_streams(scope)

    def _sim_portfolio_financial_state(self, cfg: dict, *, scope: str = "all", venue: str = "all") -> dict:
        """Project current SIM portfolio finances from canonical virtual-ledger authority."""
        state = self._monitor_account_state(cfg, capture=False, context=f"portfolio_financial_state:{scope}")
        return project_portfolio_financial_state(
            state.get("accounts") or {}, mode="sim", scope=scope, venue=venue
        )

    async def _live_portfolio_financial_state_async(self, cfg: dict, *, scope: str = "all", venue: str = "all") -> dict:
        """Project current LIVE finances from provider account authority, with no SIM fallback."""
        state = await self._live_account_state_async(cfg, capture=False, context=f"portfolio_financial_state:{scope}")
        return project_portfolio_financial_state(
            state.get("accounts") or {}, mode="live", scope=scope, venue=venue
        )

    def portfolio_financial_state(self, data=None):
        """Current canonical financial state shared by Overview and Performance."""
        data = data or {}
        mode = canonical_mode_value(data.get("mode") or "sim")
        scope = str(data.get("scope") or "all").lower()
        venue = str(data.get("venue") or "all").lower()
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        if mode == "live":
            current = asyncio.run(self._live_portfolio_financial_state_async(cfg, scope=scope, venue=venue))
        else:
            current = self._sim_portfolio_financial_state(cfg, scope=scope, venue=venue)
        return {"ok": True, "mode": mode, "scope": scope, "venue": venue, "current": current, "live_execution_allowed": False}

    def sports_overview(self, data=None):
        """Operational Sports-only summary using canonical portfolio money state."""
        data = data or {}
        mode = canonical_mode_value(data.get("mode") or "sim")
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        tz_payload = {k: data.get(k) for k in ("timezone_offset_minutes", "timezone_name") if data.get(k) is not None}
        if mode == "live":
            current = asyncio.run(self._live_portfolio_financial_state_async(cfg, scope="sports", venue="all"))
            decisions = self.live_decision_evidence({"domain": "sports", "limit": 200, "include_latest": False, "include_rows": False, "include_summary": True})
            decision_summary = decisions.get("summary") or {}
            # Sports Overview labels these cards as current operational highlights
            # and explicitly shows a Qualified count. Simulated LIVE decisions
            # therefore cannot become cards: keep them diagnostic-only and fail
            # closed until an authoritative LIVE lifecycle exists.
            return {
                "ok": True, "mode": "live", "financial": current,
                "today_pnl": None, "active_positions": 0,
                "streams": {
                    "pre_match": {"state": "OBSERVING", "qualified": 0, "open_positions": 0, "capital_deployed": None},
                    "in_play": {"state": "OBSERVING", "qualified": 0, "open_positions": 0, "capital_deployed": None},
                },
                "operations": self._operational_status("live"), "highlights": [], "positions": [],
                "decision_qualified_evidence": int(decision_summary.get("qualified") or 0),
                "message": "LIVE Sports uses actual LIVE account/position state only. Decision evidence remains diagnostic-only and is not promoted into Market Highlights or Qualified/Executed lifecycle state.",
                "live_execution_allowed": False,
            }

        current = self._sim_portfolio_financial_state(cfg, scope="sports", venue="all")
        dash = self.dashboard_overview(tz_payload)
        rows = [x for x in (dash.get("rows") or []) if str(x.get("monitor_stream") or "pre_match") in {"pre_match", "in_play"}]
        financial = dash.get("financial") or {}
        today_pnl = float((((financial.get("today") or {}).get("sports_summary") or {}).get("pnl") or 0.0))
        stream_summary = dash.get("stream_summary") or {}
        matched = self.db.latest_matched_markets(limit=500)
        market_rows = [x for x in (matched.get("rows") or []) if str(x.get("section") or "sports").lower() == "sports"]
        open_keys = {(str(x.get("event_key") or ""), str(x.get("market_name") or "")): x for x in rows}
        qual_states = {"recommended", "in_play_monitor", "in_play_qualified"}
        def venue_pair(row):
            names=[]
            for leg in row.get("legs") or []:
                name=str((leg or {}).get("exchange") or (leg or {}).get("venue_id") or "").strip()
                if name and name not in names: names.append(name)
            return " ↔ ".join(names[:2]) if names else None
        ranked=[]
        for row0 in market_rows:
            row=dict(row0)
            key=(str(row.get("event_key") or ""),str(row.get("market_name") or ""))
            pos=open_keys.get(key)
            qualified=str(row.get("status") or "").lower() in qual_states
            edge=float(row.get("net_roi_pct") or 0.0)
            if not pos and not qualified and edge <= 0:
                continue
            if pos and pos.get("locked_profit") is not None:
                highlight="OPEN POSITION"; primary=float(pos.get("locked_profit") or 0.0); primary_kind="locked_profit"
            elif qualified:
                highlight="BEST MATCH"; primary=edge; primary_kind="edge"
            else:
                highlight="BEST EDGE"; primary=edge; primary_kind="edge"
            ranked.append((1 if pos else 0,1 if qualified else 0,edge,row.get("last_seen") or row.get("observed_at") or "",{
                "sport":row.get("sport") or "Sports","event_name":row.get("event_name") or row.get("event_key") or "Market",
                "event_key":row.get("event_key"),"market_name":row.get("market_name") or "Market",
                "stream":"in_play" if row.get("in_play") else "pre_match","highlight":highlight,
                "primary_value":round(primary,4),"primary_kind":primary_kind,"best_edge_pct":round(edge,4),
                "qualified":1 if qualified else 0,"open_positions":1 if pos else 0,"venue_pair":venue_pair(row),
                "event_start":row.get("event_start"),"event_status":row.get("event_status"),"freshness":row.get("last_seen") or row.get("observed_at"),
            }))
        ranked.sort(key=lambda x:(x[0],x[1],x[2],x[3]),reverse=True)
        highlights=[x[-1] for x in ranked[:3]]
        ops=self._operational_status()
        price_scan=matched.get("scan") or {}
        return {
            "ok":True,"mode":"sim","financial":current,"today_pnl":round(today_pnl,4),"active_positions":len(rows),
            "streams":{
                "pre_match":{"state":"ACTIVE" if cfg.get("pre_match_monitor_enabled",True) else "IDLE","qualified":sum(1 for x in market_rows if not x.get("in_play") and str(x.get("status") or "").lower() in qual_states),"open_positions":int((stream_summary.get("pre_match") or {}).get("active_bets") or 0),"capital_deployed":round(float((stream_summary.get("pre_match") or {}).get("committed") or 0.0),4)},
                "in_play":{"state":"ACTIVE" if cfg.get("inplay_monitor_enabled",True) else "IDLE","qualified":sum(1 for x in market_rows if x.get("in_play") and str(x.get("status") or "").lower() in qual_states),"open_positions":int((stream_summary.get("in_play") or {}).get("active_bets") or 0),"capital_deployed":round(float((stream_summary.get("in_play") or {}).get("committed") or 0.0),4)},
            },
            "operations":ops,"last_scan":price_scan,"highlights":highlights,"positions":rows[:6],
            "live_execution_allowed":False,
        }

    def performance_analytics(self, data=None):
        """Read-only financial trend analytics for the current Monitor portfolios.

        ``actual`` uses the realised P&L produced when real event results settle the
        virtual Monitor positions. ``simulated`` uses the expected/locked profit
        stored when those same positions opened. LIVE remains unavailable.
        """
        data = data or {}
        requested_mode = canonical_mode_value(data.get("mode") or "sim")
        if requested_mode != "sim":
            return {"ok": False, "message": "performance_analytics is SIM-only; use live_performance for LIVE financial state.", "mode": requested_mode}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        include_demo = not bool(cfg.get("hide_demo_data", True))
        period = str(data.get("period") or "30d").lower()
        if period not in {"today", "yesterday", "24h", "7d", "30d", "90d", "this_week", "this_month", "custom", "all"}:
            period = "30d"
        scope = str(data.get("scope") or "all").lower()
        if scope not in {"all", "sports", "racing"}:
            scope = "all"
        stream = str(data.get("stream") or "all").lower()
        if stream not in {"all", "pre_match", "in_play", "racing"}:
            stream = "all"
        basis = str(data.get("basis") or "actual").lower()
        if basis not in {"actual", "simulated"}:
            basis = "actual"
        sport_filter = str(data.get("sport") or "all").strip()
        market_filter = str(data.get("market") or "").strip()
        venue_filter = str(data.get("venue") or "all").strip().lower()
        pair_filter = str(data.get("venue_pair") or "all").strip().lower()
        engine_filter = str(data.get("engine_instance_id") or data.get("engine") or "all").strip()

        def _json(value, default):
            if isinstance(value, (dict, list)):
                return value
            try:
                parsed = json.loads(value or ("{}" if isinstance(default, dict) else "[]"))
                return parsed if isinstance(parsed, type(default)) else default
            except Exception:
                return default

        def _venue_id(value):
            text = str(value or "").strip().lower()
            if "betfair" in text:
                return "betfair"
            if "matchbook" in text:
                return "matchbook"
            return text.replace(" ", "_")

        def _display_venue(value):
            key = _venue_id(value)
            return {"betfair": "Betfair", "matchbook": "Matchbook"}.get(key, key.replace("_", " ").title())

        local_tz, timezone_name = self._viewer_timezone(data)
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(local_tz)

        def parse_dt(value):
            if not value:
                return None
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (TypeError, ValueError):
                return None

        raw_rows = self.db.monitor_performance_rows(include_demo=include_demo)
        rows = []
        for raw in raw_rows:
            section = str(raw.get("section") or "sports").lower()
            row_stream = str(raw.get("stream") or "pre_match").lower()
            if scope == "sports" and section != "sports":
                continue
            if scope == "racing" and section != "racing":
                continue
            if stream != "all" and row_stream != stream:
                continue
            if engine_filter and engine_filter.lower() != "all" and str(raw.get("engine_instance_id") or "") != engine_filter:
                continue
            opened = parse_dt(raw.get("opened_at"))
            settled = parse_dt(raw.get("settled_at"))
            if not opened:
                continue
            row = dict(raw)
            row["_opened"] = opened
            row["_settled"] = settled
            # Reporting precision is position-level 4dp everywhere.  Quantise
            # before aggregation so Performance reconciles exactly with Results
            # and the canonical Dashboard settlement summary.
            row["_profit"] = round(float(
                (row.get("realized_pnl") if basis == "actual" else row.get("expected_profit")) or 0.0
            ), 4)
            row["_deployed"] = max(0.0, round(float(row.get("deployed") or 0.0), 4))
            row["_expected"] = max(0.0, round(float(row.get("expected_profit") or 0.0), 4))
            row["_stakes"] = _json(row.get("stakes_by_exchange_json"), {})
            row["_realized_by_venue"] = _json(row.get("realized_by_exchange_json"), {})
            row["_simulation"] = _json(row.get("simulation_json"), {})
            row["_legs"] = _json(row.get("legs_json"), [])
            venues = {_venue_id(k) for k in row["_stakes"].keys() if _venue_id(k)}
            for leg in row["_legs"]:
                if isinstance(leg, dict):
                    vid = _venue_id(leg.get("venue_id") or leg.get("provider_id") or leg.get("exchange"))
                    if vid:
                        venues.add(vid)
            row["_venues"] = sorted(venues)
            row["_venue_pair"] = "|".join(sorted(venues))
            if sport_filter.lower() not in {"", "all"} and str(row.get("sport") or "").lower() != sport_filter.lower():
                continue
            if market_filter and market_filter.lower() not in str(row.get("market_name") or "").lower():
                continue
            if venue_filter not in {"", "all"} and venue_filter not in venues:
                continue
            if pair_filter not in {"", "all"} and row["_venue_pair"] != pair_filter:
                continue
            rows.append(row)

        if scope == "racing":
            selected_streams = ["racing"]
        elif scope == "sports":
            selected_streams = [stream] if stream in {"pre_match", "in_play"} else ["pre_match", "in_play"]
        else:
            selected_streams = [stream] if stream in {"pre_match", "in_play", "racing"} else ["pre_match", "in_play", "racing"]
        opening_capital = 0.0
        reserve_pct = {
            "pre_match": self._monitor_reserve_pct(cfg, "pre_match"),
            "in_play": self._monitor_reserve_pct(cfg, "in_play"),
            "racing": self._monitor_reserve_pct(cfg, "racing"),
        }
        wallets = self.db.monitor_wallets_by_stream(reserve_pct)
        for stream_name in selected_streams:
            for wallet in (wallets.get(stream_name) or {}).values():
                opening_capital += float(wallet.get("opening_balance") or 0.0)

        today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        explicit_from = parse_dt(data.get("from_utc"))
        explicit_to = parse_dt(data.get("to_utc"))
        if period == "custom" and explicit_from:
            start_utc = explicit_from
            end_utc = min(explicit_to or now_utc, now_utc)
            if end_utc <= start_utc:
                end_utc = min(now_utc, start_utc + timedelta(minutes=1))
            start_local = start_utc.astimezone(local_tz)
            end_local = end_utc.astimezone(local_tz)
        elif period == "today":
            start_local, end_local = today_local, now_local
            start_utc, end_utc = start_local.astimezone(timezone.utc), now_utc
        elif period == "yesterday":
            start_local, end_local = today_local - timedelta(days=1), today_local
            start_utc, end_utc = start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
        elif period == "24h":
            start_utc, end_utc = now_utc - timedelta(hours=24), now_utc
            start_local, end_local = start_utc.astimezone(local_tz), now_local
        elif period == "this_week":
            start_local = today_local - timedelta(days=today_local.weekday())
            end_local = now_local
            start_utc, end_utc = start_local.astimezone(timezone.utc), now_utc
        elif period == "this_month":
            start_local = today_local.replace(day=1)
            end_local = now_local
            start_utc, end_utc = start_local.astimezone(timezone.utc), now_utc
        else:
            period_days = {"7d": 7, "30d": 30, "90d": 90}.get(period)
            if period_days:
                start_local = today_local - timedelta(days=period_days - 1)
                end_local = now_local
                start_utc, end_utc = start_local.astimezone(timezone.utc), now_utc
            else:
                earliest = min((r["_opened"] for r in rows), default=None)
                start_local = (earliest.astimezone(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)
                               if earliest else today_local - timedelta(days=6))
                end_local = now_local
                start_utc, end_utc = start_local.astimezone(timezone.utc), now_utc

        # Exchange-capital history is read from canonical SIM account checkpoints.
        # Performance never invents pre-checkpoint exchange balances: scoped values
        # remain null until a real aggregate account snapshot exists.
        account_opening = self.db.account_snapshot_state_at(mode="sim", at_utc=start_utc.isoformat())
        account_history_raw = self.db.account_snapshot_history(
            mode="sim", from_utc=start_utc.isoformat(), to_utc=min(end_utc, now_utc).isoformat(), limit=20000
        )
        account_history_raw = [x for x in account_history_raw if x.get("stream") is None]
        account_current_state = self._monitor_account_state(cfg, capture=False, context="performance").get("accounts") or {}
        # 0.9.36: headline/current Performance and Sports Overview share one canonical
        # portfolio-attribution calculation. Historical buckets still use stored
        # snapshots only; missing history remains unavailable rather than fabricated.
        current_scope_state = self._sim_portfolio_financial_state(cfg, scope=scope, venue=venue_filter)

        def selected_account_metric(account_row, field):
            if not account_row:
                return None
            # All portfolios / all streams maps to the actual venue-account metric.
            if scope == "all" and stream == "all":
                value = account_row.get(field)
                return None if value is None else float(value)
            allocations = account_row.get("allocations")
            if allocations is None:
                allocations = (account_row.get("metadata") or {}).get("allocations") or []
            if not allocations:
                return None
            wanted = set(selected_streams)
            selected = [x for x in allocations if str(x.get("stream") or "") in wanted]
            if not selected:
                return 0.0
            if field not in {"equity", "available", "reserved"}:
                return None
            return sum(float(x.get(field) or 0.0) for x in selected)

        def selected_account_capital(account_row):
            return selected_account_metric(account_row, "equity")

        venue_ids = set(str(x).lower() for x in account_opening.keys()) | set(str(x).lower() for x in account_current_state.keys())
        for snap in account_history_raw:
            venue_id = provider_id_for_name(str(snap.get("venue_id") or snap.get("provider_id") or snap.get("exchange") or ""))
            if venue_id:
                venue_ids.add(venue_id)
        if venue_filter not in {"", "all"}:
            venue_ids = {v for v in venue_ids if _venue_id(v) == venue_filter}
        account_history = {venue_id: [] for venue_id in sorted(venue_ids)}
        for snap in account_history_raw:
            venue_id = provider_id_for_name(str(snap.get("venue_id") or snap.get("provider_id") or snap.get("exchange") or ""))
            stamp = parse_dt(snap.get("captured_at"))
            if venue_id and stamp:
                account_history.setdefault(venue_id, []).append((stamp, snap))
        for venue_id in account_history:
            account_history[venue_id].sort(key=lambda item: item[0])
        venue_ids = sorted(account_history.keys())

        def is_settled(row):
            return bool(row.get("_settled")) and str(row.get("status") or "").upper() == "SETTLED"

        def profit_before(cutoff):
            return sum(r["_profit"] for r in rows if is_settled(r) and r["_settled"] < cutoff)

        def deployment_stats(day_start_local, day_end_local):
            effective_end_local = min(day_end_local, now_local)
            if effective_end_local <= day_start_local:
                return 0.0, 0.0, 0.0
            a = day_start_local.astimezone(timezone.utc)
            b = effective_end_local.astimezone(timezone.utc)
            current = 0.0
            events = []
            for row in rows:
                opened = row["_opened"]
                closed = row["_settled"] or now_utc
                if opened >= b or closed <= a:
                    continue
                amount = row["_deployed"]
                if opened <= a < closed:
                    current += amount
                elif a < opened < b:
                    events.append((opened, amount))
                if a < closed < b:
                    events.append((closed, -amount))
            events.sort(key=lambda item: (item[0], 0 if item[1] < 0 else 1))
            peak = current
            area = 0.0
            cursor = a
            for stamp, delta in events:
                if stamp > cursor:
                    area += current * (stamp - cursor).total_seconds()
                    cursor = stamp
                current = max(0.0, current + delta)
                peak = max(peak, current)
            if b > cursor:
                area += current * (b - cursor).total_seconds()
            seconds = max(1.0, (b - a).total_seconds())
            return area / seconds, peak, current

        # Performance uses adaptive financial buckets. Today/24h are intraday;
        # medium ranges are daily; long/custom ranges become weekly when needed.
        span = max(timedelta(minutes=1), end_local - start_local)
        if period in {"today", "yesterday", "24h"} or (period == "custom" and span <= timedelta(days=2)):
            bucket_kind = "hour"
        elif period in {"7d", "30d", "90d", "this_week", "this_month"} or (period == "custom" and span <= timedelta(days=120)):
            bucket_kind = "day"
        else:
            bucket_kind = "week" if span > timedelta(days=120) else "day"

        def next_bucket(cursor_local):
            if bucket_kind == "hour":
                return min(cursor_local + timedelta(hours=1), end_local)
            if bucket_kind == "week":
                return min(cursor_local + timedelta(days=7), end_local)
            return min(cursor_local + timedelta(days=1), end_local)

        def metric_total(state, field):
            vals = []
            for venue_id in venue_ids:
                value = selected_account_metric((state or {}).get(venue_id), field)
                if value is not None:
                    vals.append(float(value))
            return None if not vals else sum(vals)

        # Opening capital for ROI prefers the canonical account snapshot at the
        # start of the requested period. Only fall back to configured SIM wallet
        # openings when no authoritative account snapshot exists at all.
        snapshot_start_capital = metric_total(account_opening, "equity")
        if snapshot_start_capital is not None:
            capital_at_start = snapshot_start_capital
        else:
            capital_at_start = 0.0
            for stream_name in selected_streams:
                for exchange, wallet in (wallets.get(stream_name) or {}).items():
                    if venue_filter not in {"", "all"} and _venue_id(exchange) != venue_filter:
                        continue
                    capital_at_start += float(wallet.get("opening_balance") or 0.0)
            capital_at_start += profit_before(start_utc)

        # Build account-state cursors once, then sample the most recent canonical
        # venue account state at every financial bucket boundary. Missing history
        # stays null instead of being reconstructed from P&L.
        account_cursors = {venue_id: account_opening.get(venue_id) for venue_id in venue_ids}
        account_indices = {venue_id: 0 for venue_id in venue_ids}

        day_rows = []
        cumulative_profit = 0.0
        cursor = start_local
        while cursor < end_local:
            bucket_end = next_bucket(cursor)
            a, b = cursor.astimezone(timezone.utc), bucket_end.astimezone(timezone.utc)
            settled_bucket = [r for r in rows if is_settled(r) and a <= r["_settled"] < b]
            opened_bucket = [r for r in rows if a <= r["_opened"] < b]
            bucket_profit = sum(r["_profit"] for r in settled_bucket)
            turnover = sum(r["_deployed"] for r in settled_bucket)
            expected_bucket = sum(r["_expected"] for r in settled_bucket)
            cumulative_profit += bucket_profit
            avg_deployed, peak_deployed, end_deployed = deployment_stats(cursor, bucket_end)

            for venue_id in venue_ids:
                hist = account_history.get(venue_id) or []
                idx = account_indices.get(venue_id, 0)
                while idx < len(hist) and hist[idx][0] <= b:
                    account_cursors[venue_id] = hist[idx][1]
                    idx += 1
                account_indices[venue_id] = idx
            bucket_capital = metric_total(account_cursors, "equity")
            bucket_available = metric_total(account_cursors, "available")
            bucket_exposure = metric_total(account_cursors, "reserved")
            # The current bucket must reconcile to the current canonical account
            # state rather than the last persisted checkpoint.
            if bucket_end >= end_local - timedelta(seconds=1):
                current_capital_authoritative = current_scope_state.get("capital")
                current_available_authoritative = current_scope_state.get("available")
                current_exposure_authoritative = current_scope_state.get("capital_deployed")
                if current_capital_authoritative is not None: bucket_capital = current_capital_authoritative
                if current_available_authoritative is not None: bucket_available = current_available_authoritative
                if current_exposure_authoritative is not None: bucket_exposure = current_exposure_authoritative

            util = None
            if bucket_capital not in (None, 0):
                util = (float(bucket_exposure or 0.0) / float(bucket_capital)) * 100.0
            label = cursor.strftime("%H:%M") if bucket_kind == "hour" else (cursor.strftime("%d %b") if bucket_kind == "day" else cursor.strftime("%d %b"))
            day_rows.append({
                "date": cursor.isoformat(),
                "bucket_start": cursor.isoformat(),
                "bucket_end": bucket_end.isoformat(),
                "bucket_kind": bucket_kind,
                "label": label,
                "capital": None if bucket_capital is None else round(bucket_capital, 4),
                "available": None if bucket_available is None else round(bucket_available, 4),
                "exposure": None if bucket_exposure is None else round(bucket_exposure, 4),
                "profit": round(bucket_profit, 4),
                "cumulative_period_profit": round(cumulative_profit, 4),
                "avg_deployed": round(avg_deployed, 4),
                "peak_deployed": round(peak_deployed, 4),
                "end_deployed": round(end_deployed, 4),
                "utilization_pct": None if util is None else round(util, 4),
                "return_on_deployed_pct": round((bucket_profit / turnover) * 100.0, 4) if turnover else 0.0,
                "portfolio_roi_pct": round((cumulative_profit / capital_at_start) * 100.0, 4) if capital_at_start else None,
                "captured_edge_pct": round((bucket_profit / expected_bucket) * 100.0, 4) if expected_bucket > 1e-9 else None,
                "qualified_edge_value": round(expected_bucket, 4),
                "deployed_turnover": round(turnover, 4),
                "settled": len(settled_bucket),
                "opened": len(opened_bucket),
            })
            cursor = bucket_end

        # Event-driven current-exposure history for the Capital Position chart.
        # Financial buckets above remain the settled-P&L aggregation contract;
        # this separate series records every position open/release so deployed
        # capital can visibly rise and fall at the actual event timestamps.
        exposure_deltas: dict[datetime, dict[str, float | int]] = {}
        initial_deployed = 0.0
        for row in rows:
            opened = row["_opened"]
            settled = row["_settled"]
            amount = float(row["_deployed"] or 0.0)
            if opened <= start_utc and (settled is None or settled > start_utc):
                initial_deployed += amount
            if start_utc < opened <= end_utc:
                slot = exposure_deltas.setdefault(opened, {"delta": 0.0, "opened": 0, "settled": 0})
                slot["delta"] = float(slot["delta"]) + amount
                slot["opened"] = int(slot["opened"]) + 1
            if settled is not None and start_utc < settled <= end_utc:
                slot = exposure_deltas.setdefault(settled, {"delta": 0.0, "opened": 0, "settled": 0})
                slot["delta"] = float(slot["delta"]) - amount
                slot["settled"] = int(slot["settled"]) + 1

        capital_point_times = {start_utc, end_utc}
        capital_point_times.update(exposure_deltas.keys())
        for hist in account_history.values():
            for stamp, _ in hist:
                if start_utc < stamp <= end_utc:
                    capital_point_times.add(stamp)

        capital_account_cursors = {venue_id: account_opening.get(venue_id) for venue_id in venue_ids}
        capital_account_indices = {venue_id: 0 for venue_id in venue_ids}
        deployed_at_point = max(0.0, initial_deployed)
        capital_timeline = []
        for stamp in sorted(capital_point_times):
            for venue_id in venue_ids:
                hist = account_history.get(venue_id) or []
                idx = capital_account_indices.get(venue_id, 0)
                while idx < len(hist) and hist[idx][0] <= stamp:
                    capital_account_cursors[venue_id] = hist[idx][1]
                    idx += 1
                capital_account_indices[venue_id] = idx
            event = exposure_deltas.get(stamp)
            if event:
                deployed_at_point = max(0.0, deployed_at_point + float(event.get("delta") or 0.0))
            point_capital = metric_total(capital_account_cursors, "equity")
            point_available = metric_total(capital_account_cursors, "available")
            point_deployed = deployed_at_point
            # SIM virtual accounts have the canonical identity Available = Equity -
            # current reserved exposure.  Recompute Available at each authoritative
            # position open/release event so the Capital-over-time chart shows the
            # actual cash dip/release pattern instead of a stale snapshot between
            # account captures.  This is a derivation from two authoritative values,
            # not interpolation of an unknown financial state.
            if point_capital is not None:
                point_available = max(0.0, float(point_capital) - float(point_deployed))
            if stamp >= end_utc - timedelta(seconds=1):
                if current_scope_state.get("capital") is not None:
                    point_capital = float(current_scope_state["capital"])
                if current_scope_state.get("available") is not None:
                    point_available = float(current_scope_state["available"])
                if current_scope_state.get("capital_deployed") is not None:
                    point_deployed = float(current_scope_state["capital_deployed"])
                    deployed_at_point = point_deployed
            capital_timeline.append({
                "timestamp": stamp.isoformat(),
                "capital": None if point_capital is None else round(float(point_capital), 4),
                "available": None if point_available is None else round(float(point_available), 4),
                # Explicit operator-facing alias. ``capital_deployed`` remains for
                # backwards compatibility; both represent capital currently tied
                # up by open strategy positions at this timestamp.
                "capital_in_use": round(float(point_deployed), 4),
                "capital_deployed": round(float(point_deployed), 4),
                "exposure": round(float(point_deployed), 4),
                "opened": int((event or {}).get("opened") or 0),
                "settled": int((event or {}).get("settled") or 0),
            })

        # Venue-capital compatibility series shares the same adaptive Performance buckets.
        exchange_rows = []
        first_available = {}
        for venue_id in venue_ids:
            candidates = []
            if account_opening.get(venue_id): candidates.append(account_opening[venue_id].get("captured_at"))
            if account_history.get(venue_id): candidates.append(account_history[venue_id][0][1].get("captured_at"))
            first_available[venue_id] = min((x for x in candidates if x), default=None)
        for row in day_rows:
            point = {"date": row["date"], "bucket_start": row.get("bucket_start"), "bucket_end": row.get("bucket_end")}
            # Account cursor sampling was already performed while building day_rows;
            # preserve compatibility with the legacy per-venue equity chart using
            # the latest snapshot at each bucket end.
            end_stamp = parse_dt(row.get("bucket_end")) or end_utc
            state_at = self.db.account_snapshot_state_at(mode="sim", at_utc=end_stamp.isoformat())
            for venue_id in venue_ids:
                value = selected_account_capital(state_at.get(venue_id))
                point[venue_id] = None if value is None else round(float(value), 4)
            exchange_rows.append(point)
        if exchange_rows and day_rows:
            for venue_id in venue_ids:
                current_value = selected_account_capital(account_current_state.get(venue_id))
                if current_value is not None:
                    exchange_rows[-1][venue_id] = round(float(current_value), 4)

        exchange_current = {}
        for item in current_scope_state.get("rows") or []:
            venue_id = str(item.get("venue_id") or item.get("provider_id") or "").lower()
            if not venue_id:
                continue
            exchange_current[venue_id] = {
                "provider_id": venue_id, "venue_id": venue_id,
                "capital": item.get("capital"), "equity": item.get("capital"),
                "available": item.get("available"), "reserved": item.get("capital_deployed"),
                "currency": str(item.get("currency") or cfg.get("account_currency") or "GBP").upper(),
                "authoritative": bool(item.get("authoritative")),
            }

        period_settled = [r for r in rows if is_settled(r) and start_utc <= r["_settled"] < end_utc]
        period_opened = [r for r in rows if start_utc <= r["_opened"] < end_utc]
        period_profit = sum(r["_profit"] for r in period_settled)
        turnover = sum(r["_deployed"] for r in period_settled)
        current_deployed = sum(r["_deployed"] for r in rows if not is_settled(r))
        current_capital_authoritative = current_scope_state.get("capital")
        current_available_authoritative = current_scope_state.get("available")
        current_exposure_authoritative = current_scope_state.get("capital_deployed")
        current_capital = current_capital_authoritative
        avg_deployed = (sum(float(x["avg_deployed"]) for x in day_rows) / len(day_rows)) if day_rows else 0.0
        peak_deployed_ledger = max((float(x["peak_deployed"]) for x in day_rows), default=0.0)
        exposure_rows = [float(x["exposure"]) for x in day_rows if x.get("exposure") is not None]
        event_peak_deployed = max((float(x.get("capital_deployed") or 0.0) for x in capital_timeline), default=0.0)
        peak_deployed = max(max(exposure_rows, default=0.0), peak_deployed_ledger, event_peak_deployed)
        util_rows = [float(x["utilization_pct"]) for x in day_rows if x.get("utilization_pct") is not None]
        avg_util = (sum(util_rows) / len(util_rows)) if util_rows else 0.0

        def half_metrics(items):
            if not items:
                return {"avg_deployed": 0.0, "utilization": 0.0, "efficiency": 0.0, "settled": 0, "profit": 0.0}
            dep = sum(float(x["avg_deployed"]) for x in items) / len(items)
            util_values = [float(x["utilization_pct"]) for x in items if x.get("utilization_pct") is not None]
            util = (sum(util_values) / len(util_values)) if util_values else 0.0
            turnover_local = sum(float(x["deployed_turnover"]) for x in items)
            profit_local = sum(float(x["profit"]) for x in items)
            return {
                "avg_deployed": dep,
                "utilization": util,
                "efficiency": (profit_local / turnover_local) * 100.0 if turnover_local else 0.0,
                "settled": sum(int(x["settled"]) for x in items),
                "profit": profit_local,
            }

        split = max(1, len(day_rows) // 2)
        earlier = half_metrics(day_rows[:split])
        recent = half_metrics(day_rows[split:] or day_rows[-1:])

        def pct_change(new, old):
            if abs(old) < 1e-9:
                return None if abs(new) > 1e-9 else 0.0
            return ((new - old) / abs(old)) * 100.0

        # v0.8.44 decision-focused Performance analytics. Opportunity-funnel
        # counts use opportunity IDs consistently; financial facts remain on the
        # canonical settlement-time Monitor ledger. Venue/pair attribution is
        # position-level so multi-leg positions are never double-counted in pair P&L.
        opportunity_groups = self.db.performance_opportunity_aggregates(
            start_utc.isoformat(), min(end_utc, now_utc).isoformat(), include_demo=include_demo,
            venue=venue_filter, venue_pair=pair_filter,
        )

        def _dimension_ok(item):
            section = str(item.get("section") or "sports").lower()
            in_play = int(item.get("in_play") or 0)
            if scope == "sports" and section != "sports":
                return False
            if scope == "racing" and section != "racing":
                return False
            if stream == "pre_match" and (section != "sports" or in_play != 0):
                return False
            if stream == "in_play" and (section != "sports" or in_play != 1):
                return False
            if stream == "racing" and section != "racing":
                return False
            if sport_filter.lower() not in {"", "all"} and str(item.get("sport") or "").lower() != sport_filter.lower():
                return False
            if market_filter and market_filter.lower() not in str(item.get("market_name") or "").lower():
                return False
            return True

        opportunity_groups = [dict(x) for x in opportunity_groups if _dimension_ok(x)]
        funnel = {key: sum(int(x.get(key) or 0) for x in opportunity_groups)
                  for key in ("observed", "positive", "qualified", "attempted", "executed", "settled")}
        funnel["previous_conversion_pct"] = {}
        stages = ["observed", "positive", "qualified", "attempted", "executed", "settled"]
        for prev, cur in zip(stages, stages[1:]):
            denom = funnel.get(prev, 0)
            funnel["previous_conversion_pct"][cur] = round((funnel.get(cur, 0) / denom) * 100.0, 3) if denom else 0.0
        funnel["observed_to_executed_pct"] = round((funnel["executed"] / funnel["observed"]) * 100.0, 3) if funnel["observed"] else 0.0

        settled_detail_rows = self.db.settled_monitor_positions(
            from_utc=start_utc.isoformat(), to_utc=min(end_utc, now_utc).isoformat(),
            include_demo=include_demo, sport="all", stream="all", limit=20000,
        )
        detailed = []
        for source in settled_detail_rows:
            if not _dimension_ok(source):
                continue
            item = dict(source)
            stakes = _json(item.get("stakes_by_exchange_json"), {})
            legs = item.get("legs") if isinstance(item.get("legs"), list) else _json(item.get("legs_json"), [])
            details = item.get("details") or {}
            execution_result = details.get("execution_result") or {}
            fills = execution_result.get("fills") or []
            events = execution_result.get("events") or []
            venues = {_venue_id(k) for k in stakes if _venue_id(k)}
            for leg in legs:
                if isinstance(leg, dict):
                    venue_id = _venue_id(leg.get("venue_id") or leg.get("provider_id") or leg.get("exchange"))
                    if venue_id:
                        venues.add(venue_id)
            for fill in fills:
                if isinstance(fill, dict):
                    venue_id = _venue_id(fill.get("venue_id") or fill.get("provider_id") or fill.get("exchange"))
                    if venue_id:
                        venues.add(venue_id)
            pair_key = "|".join(sorted(venues))
            if venue_filter not in {"", "all"} and venue_filter not in venues:
                continue
            if pair_filter not in {"", "all"} and pair_key != pair_filter:
                continue
            recovery = any(bool(x.get("is_hedge")) for x in fills if isinstance(x, dict)) or any(
                str(x.get("state") or "").upper() in {"HEDGING", "HEDGED", "EMERGENCY_HEDGE", "PANIC"}
                for x in events if isinstance(x, dict)
            )
            emergency = any(str(x.get("state") or "").upper() == "EMERGENCY_HEDGE" for x in events if isinstance(x, dict))
            item["_stakes"] = stakes
            item["_legs"] = legs
            item["_fills"] = fills
            item["_events"] = events
            item["_venues"] = sorted(venues)
            item["_venue_pair"] = pair_key
            item["_recovery"] = recovery
            item["_emergency"] = emergency
            item["_pnl"] = round(float((item.get("realized_pnl") if basis == "actual" else item.get("expected_profit")) or 0.0), 4)
            item["_expected"] = max(0.0, round(float(item.get("expected_profit") or item.get("execution_expected_profit") or 0.0), 4))
            item["_deployed"] = max(0.0, round(float(item.get("deployed") or 0.0), 4))
            item["_leakage"] = round(float(item.get("execution_leakage") or 0.0), 4)
            detailed.append(item)

        def _base_metrics(items):
            count = len(items)
            pnl_value = sum(float(x.get("_pnl") or 0.0) for x in items)
            deployed_value = sum(float(x.get("_deployed") or 0.0) for x in items)
            expected_value = sum(float(x.get("_expected") or 0.0) for x in items)
            recovery_count = sum(1 for x in items if x.get("_recovery"))
            emergency_count = sum(1 for x in items if x.get("_emergency"))
            return {
                "positions": count,
                "settled": count,
                "pnl": round(pnl_value, 4),
                "capital_deployed": round(deployed_value, 4),
                "roi_pct": round((pnl_value / deployed_value) * 100.0, 4) if deployed_value else 0.0,
                "return_on_deployed_pct": round((pnl_value / deployed_value) * 100.0, 4) if deployed_value else 0.0,
                "qualified_edge_value": round(expected_value, 4),
                "avg_qualified_edge_pct": round((expected_value / deployed_value) * 100.0, 4) if deployed_value else 0.0,
                "avg_realized_edge_pct": round((pnl_value / deployed_value) * 100.0, 4) if deployed_value else 0.0,
                "captured_edge_pct": round((pnl_value / expected_value) * 100.0, 4) if expected_value > 1e-9 else None,
                "recovery_positions": recovery_count,
                "recovery_rate_pct": round((recovery_count / count) * 100.0, 3) if count else 0.0,
                "emergency_hedges": emergency_count,
                "execution_leakage": round(sum(max(0.0, float(x.get("_leakage") or 0.0)) for x in items), 4),
            }

        def _opp_for(predicate):
            subset = [x for x in opportunity_groups if predicate(x)]
            return {key: sum(int(x.get(key) or 0) for x in subset) for key in ("observed", "positive", "qualified", "attempted", "executed", "settled")}

        def _attach_conversion(metrics, opp):
            metrics.update(opp)
            metrics["execution_conversion_pct"] = round((opp["executed"] / opp["attempted"]) * 100.0, 3) if opp["attempted"] else 0.0
            return metrics

        domains = []
        for key, label in (("sports", "Sports"), ("racing", "Racing")):
            items = [x for x in detailed if str(x.get("section") or "sports").lower() == key]
            opp = _opp_for(lambda x, k=key: str(x.get("section") or "sports").lower() == k)
            domains.append({"key": key, "label": label, **_attach_conversion(_base_metrics(items), opp)})

        sport_names = sorted({str(x.get("sport") or "Unknown") for x in detailed} | {str(x.get("sport") or "Unknown") for x in opportunity_groups})
        sports_breakdown = []
        for name in sport_names:
            items = [x for x in detailed if str(x.get("sport") or "Unknown") == name]
            opp = _opp_for(lambda x, n=name: str(x.get("sport") or "Unknown") == n)
            sports_breakdown.append({"sport": name, **_attach_conversion(_base_metrics(items), opp)})
        sports_breakdown.sort(key=lambda x: (-float(x.get("pnl") or 0.0), -int(x.get("positions") or 0), x["sport"]))

        market_keys = sorted({(str(x.get("sport") or "Unknown"), str(x.get("market_name") or "Unknown")) for x in detailed} |
                             {(str(x.get("sport") or "Unknown"), str(x.get("market_name") or "Unknown")) for x in opportunity_groups})
        market_breakdown = []
        for sport_name, market_name in market_keys:
            items = [x for x in detailed if str(x.get("sport") or "Unknown") == sport_name and str(x.get("market_name") or "Unknown") == market_name]
            opp = _opp_for(lambda x, s=sport_name, m=market_name: str(x.get("sport") or "Unknown") == s and str(x.get("market_name") or "Unknown") == m)
            record = {"sport": sport_name, "market": market_name, **_attach_conversion(_base_metrics(items), opp)}
            market_breakdown.append(record)
        market_breakdown.sort(key=lambda x: (-float(x.get("pnl") or 0.0), -int(x.get("positions") or 0), x["sport"], x["market"]))

        venue_map = {}
        for item in detailed:
            realized_map = _json(item.get("realized_by_exchange_json"), {})
            for venue in item.get("_venues") or []:
                bucket = venue_map.setdefault(venue, {"venue_id": venue, "venue": _display_venue(venue), "position_ids": set(), "legs_submitted": 0, "legs_executed": 0, "partial_fills": 0, "rejections": 0, "requested_stake": 0.0, "executed_stake": 0.0, "capital_deployed": 0.0, "pnl_contribution": 0.0, "recovery_events": 0, "settlement_exceptions": 0})
                bucket["position_ids"].add(int(item.get("opportunity_id") or 0))
                bucket["capital_deployed"] += sum(float(value or 0.0) for key, value in (item.get("_stakes") or {}).items() if _venue_id(key) == venue)
                for k, value in realized_map.items():
                    if _venue_id(k) == venue:
                        bucket["pnl_contribution"] += float(value or 0.0)
                if item.get("_recovery"):
                    bucket["recovery_events"] += 1
                legs_for_venue = [x for x in item.get("_legs") or [] if isinstance(x, dict) and _venue_id(x.get("venue_id") or x.get("provider_id") or x.get("exchange")) == venue]
                fills_for_venue = [x for x in item.get("_fills") or [] if isinstance(x, dict) and _venue_id(x.get("venue_id") or x.get("provider_id") or x.get("exchange")) == venue]
                bucket["legs_submitted"] += len(legs_for_venue)
                bucket["legs_executed"] += len(fills_for_venue)
                bucket["requested_stake"] += sum(float(x.get("stake") or x.get("requested_stake") or 0.0) for x in legs_for_venue)
                bucket["executed_stake"] += sum(float(x.get("stake") or x.get("executed_stake") or 0.0) for x in fills_for_venue)
                bucket["partial_fills"] += sum(1 for x in item.get("_events") or [] if isinstance(x, dict) and str(x.get("state") or "").upper() == "LEG_PARTIAL" and _venue_id(x.get("venue_id") or x.get("provider_id") or x.get("exchange")) == venue)
                bucket["rejections"] += sum(1 for x in item.get("_events") or [] if isinstance(x, dict) and str(x.get("state") or "").upper() == "LEG_FAILED" and _venue_id(x.get("venue_id") or x.get("provider_id") or x.get("exchange")) == venue)
        venues = []
        for bucket in venue_map.values():
            positions_n = len(bucket.pop("position_ids"))
            submitted = int(bucket["legs_submitted"] or 0)
            bucket.update({
                "positions": positions_n,
                "fill_rate_pct": round((bucket["legs_executed"] / submitted) * 100.0, 3) if submitted else None,
                "partial_fill_rate_pct": round((bucket["partial_fills"] / submitted) * 100.0, 3) if submitted else None,
                "rejection_rate_pct": round((bucket["rejections"] / submitted) * 100.0, 3) if submitted else None,
                "avg_requested_stake": round(bucket["requested_stake"] / submitted, 4) if submitted else None,
                "avg_executed_stake": round(bucket["executed_stake"] / max(1, bucket["legs_executed"]), 4) if bucket["legs_executed"] else None,
                "capital_deployed": round(bucket["capital_deployed"], 4),
                "pnl_contribution": round(bucket["pnl_contribution"], 4),
            })
            venues.append(bucket)
        venues.sort(key=lambda x: (-int(x.get("positions") or 0), x["venue"]))

        pair_map = {}
        directional_map = {}
        for item in detailed:
            pair_key = item.get("_venue_pair") or "unknown"
            pair_label = " ↔ ".join(_display_venue(x) for x in (item.get("_venues") or [])) or "Unknown"
            pair_map.setdefault(pair_key, {"key": pair_key, "label": pair_label, "items": []})["items"].append(item)
            sides = []
            for leg in item.get("_legs") or []:
                if not isinstance(leg, dict):
                    continue
                venue = _venue_id(leg.get("venue_id") or leg.get("provider_id") or leg.get("exchange"))
                side = str(leg.get("side") or "BACK").upper()
                if venue:
                    sides.append((side, venue))
            backs = [x for x in sides if x[0] == "BACK"]
            lays = [x for x in sides if x[0] == "LAY"]
            if backs and lays:
                direction = f"BACK {_display_venue(backs[0][1])} → LAY {_display_venue(lays[0][1])}"
            elif len(sides) >= 2:
                direction = " ↔ ".join(f"{side} {_display_venue(venue)}" for side, venue in sides[:2])
            else:
                direction = pair_label
            directional_map.setdefault(direction, []).append(item)
        venue_pairs = []
        for pair in pair_map.values():
            m = _base_metrics(pair["items"])
            venue_pairs.append({"key": pair["key"], "pair": pair["label"], **m})
        venue_pairs.sort(key=lambda x: (-float(x.get("pnl") or 0.0), -int(x.get("positions") or 0), x["pair"]))
        directional_pairs = [{"direction": key, **_base_metrics(items)} for key, items in directional_map.items()]
        directional_pairs.sort(key=lambda x: (-float(x.get("pnl") or 0.0), -int(x.get("positions") or 0), x["direction"]))

        recovered = [x for x in detailed if x.get("_recovery")]
        recovery_expected = sum(float(x.get("_expected") or 0.0) for x in recovered)
        recovery_pnl = sum(float(x.get("_pnl") or 0.0) for x in recovered)
        expected_settled = sum(float(x.get("_expected") or 0.0) for x in period_settled)
        captured_edge_pct = round((period_profit / expected_settled) * 100.0, 4) if expected_settled > 1e-9 else None
        portfolio_roi_pct = round((period_profit / capital_at_start) * 100.0, 4) if capital_at_start else 0.0
        capital_efficiency = {
            "available_trading_capital": round(float(current_available_authoritative or 0.0), 4),
            "reserved_capital": round(float(current_exposure_authoritative or 0.0), 4),
            "capital_deployed": round(turnover, 4),
            "average_capital_per_position": round(turnover / len(period_settled), 4) if period_settled else 0.0,
            "peak_deployed": round(peak_deployed, 4),
            "average_utilization_pct": round(avg_util, 4),
            "return_on_deployed_pct": round((period_profit / turnover) * 100.0, 4) if turnover else 0.0,
            "profit_per_1000_deployed": round((period_profit / turnover) * 1000.0, 4) if turnover else 0.0,
        }
        recovery_summary = {
            "positions": len(recovered),
            "rate_pct": round((len(recovered) / len(detailed)) * 100.0, 3) if detailed else 0.0,
            "qualified_edge_value": round(recovery_expected, 4),
            "final_pnl": round(recovery_pnl, 4),
            "edge_lost": round(max(0.0, recovery_expected - recovery_pnl), 4),
            "execution_leakage": round(sum(max(0.0, float(x.get("_leakage") or 0.0)) for x in recovered), 4),
        }
        venue_option_ids = sorted(set(venue_ids) | {str(x.get("venue_id") or "").lower() for x in venues if x.get("venue_id")})
        filter_options = {
            "sports": sorted({str(x.get("sport") or "Unknown") for x in opportunity_groups} | {str(x.get("sport") or "Unknown") for x in detailed}),
            "markets": sorted({str(x.get("market_name") or "Unknown") for x in opportunity_groups} | {str(x.get("market_name") or "Unknown") for x in detailed}),
            "venues": [{"id": venue_id, "label": _display_venue(venue_id)} for venue_id in venue_option_ids],
            "venue_pairs": [{"id": x["key"], "label": x["pair"]} for x in venue_pairs],
        }

        return {
            "ok": True,
            "filters": {"period": period, "scope": scope, "stream": stream, "basis": basis,
                        "sport": sport_filter, "market": market_filter, "venue": venue_filter, "venue_pair": pair_filter},
            "filter_options": filter_options,
            "range_label": f"{start_local.strftime('%d %b %H:%M')} → {end_local.strftime('%d %b %H:%M')} · {timezone_name}",
            "from_utc": start_utc.isoformat(),
            "to_utc": min(end_utc, now_utc).isoformat(),
            "timezone_name": timezone_name,
            "time_basis": "settled_at" if basis == "actual" else "settled_at_with_entry_expected_profit",
            "basis_note": (
                "Actual outcome uses the real event result applied to virtual Monitor stakes; settled financial periods use settlement time."
                if basis == "actual" else
                "Simulated/expected uses the expected profit stored when each Monitor position opened, grouped by settlement day for consistent period comparison."
            ),
            "research_only": False,
            "summary": {
                "opening_capital": round(opening_capital, 4),
                "period_start_capital": round(capital_at_start, 4),
                "current_capital": None if current_capital is None else round(current_capital, 4),
                "current_available": None if current_available_authoritative is None else round(current_available_authoritative, 4),
                "current_exposure": None if current_exposure_authoritative is None else round(current_exposure_authoritative, 4),
                "period_end_capital": None if current_capital is None else round(current_capital, 4),
                "period_profit": round(period_profit, 4),
                "net_pnl": round(period_profit, 4),
                "portfolio_roi_pct": portfolio_roi_pct,
                "capital_growth_pct": round((period_profit / capital_at_start) * 100.0, 4) if capital_at_start else 0.0,
                "current_deployed": round(current_deployed, 4),
                "average_deployed": round(avg_deployed, 4),
                "peak_deployed": round(peak_deployed, 4),
                "average_utilization_pct": round(avg_util, 4),
                "deployed_turnover": round(turnover, 4),
                "return_on_deployed_pct": round((period_profit / turnover) * 100.0, 4) if turnover else 0.0,
                "captured_edge_pct": captured_edge_pct,
                "qualified_edge_value": round(expected_settled, 4),
                "positions_executed": len(period_opened),
                "attempted_positions": int(funnel.get("attempted") or 0),
                "settled_bets": len(period_settled),
                "opened_bets": len(period_opened),
                "open_bets": sum(1 for r in rows if not is_settled(r)),
            },
            "venue_capital": {
                "basis": "account_equity" if scope == "all" and stream == "all" else "selected_portfolio_allocation",
                "basis_label": "Total SIM venue equity" if scope == "all" and stream == "all" else "Selected portfolio allocation by venue",
                "rows": exchange_rows,
                "current": exchange_current,
                "first_available": first_available,
            },
            # Compatibility alias retained for the 0.8.x Performance frontend.
            "exchange_capital": {
                "basis": "account_equity" if scope == "all" and stream == "all" else "selected_portfolio_allocation",
                "basis_label": "Total SIM venue equity" if scope == "all" and stream == "all" else "Selected portfolio allocation by venue",
                "rows": exchange_rows,
                "current": exchange_current,
                "first_available": first_available,
            },
            "trends": {
                "capital_change": round(period_profit, 4),
                "deployed_change_pct": None if pct_change(recent["avg_deployed"], earlier["avg_deployed"]) is None else round(pct_change(recent["avg_deployed"], earlier["avg_deployed"]), 3),
                "utilization_change_pp": round(recent["utilization"] - earlier["utilization"], 3),
                "efficiency_change_pp": round(recent["efficiency"] - earlier["efficiency"], 3),
                "settled_change": int(recent["settled"] - earlier["settled"]),
                "profit_change": round(recent["profit"] - earlier["profit"], 4),
            },
            "performance": {
                "domains": domains,
                "sports": sports_breakdown,
                "markets": market_breakdown,
                "venues": venues,
                "venue_pairs": venue_pairs,
                "directional_pairs": directional_pairs,
                "funnel": funnel,
                "capital_efficiency": capital_efficiency,
                "recovery": recovery_summary,
                "metric_definitions": {
                    "portfolio_roi": "Realised settled P&L divided by selected-period starting portfolio capital.",
                    "return_on_deployed": "Realised settled P&L divided by settled position capital deployed in the selected period.",
                    "captured_edge": "Realised settled P&L divided by expected profit recorded at execution for the same settled positions; weighted by economic value, not a simple average of percentages.",
                    "venue_pnl": "Settlement contribution recorded against each venue; position-level P&L remains the portfolio source of truth.",
                },
            },
            "timeline_granularity": bucket_kind,
            "rows": day_rows,
            "capital_timeline": capital_timeline,
        }

    def financial_reconciliation_snapshot(self, data=None):
        """Canonical settled-finance snapshot shared by Dashboard surfaces.

        All values are derived from the same settlement ledger and the same
        ``as_of_utc`` boundary.  This prevents tiny disagreements caused by
        independent requests landing either side of a settlement, and keeps
        Today aligned to the viewer's local calendar day.
        """
        data = data or {}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        include_demo = not bool(cfg.get("hide_demo_data", True))
        local_tz, tz_name = self._viewer_timezone(data)
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(local_tz)
        today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        seven_local = today_local - timedelta(days=6)
        as_of = now_utc + timedelta(seconds=1)

        def summary(start_local=None, stream="all"):
            return self.db.settled_monitor_summary(
                from_utc=start_local.astimezone(timezone.utc).isoformat() if start_local else None,
                to_utc=as_of.isoformat(), include_demo=include_demo, sport="all", stream=stream,
            )

        def bucket(start_local=None):
            all_summary = summary(start_local, "all")
            pre = summary(start_local, "pre_match")
            ip = summary(start_local, "in_play")
            racing = summary(start_local, "racing")
            sports = {
                key: round(float(pre.get(key) or 0.0) + float(ip.get(key) or 0.0), 4)
                for key in ("pnl", "deployed", "returned", "execution_leakage")
            }
            for key in ("settled", "wins", "losses", "breakeven"):
                sports[key] = int(pre.get(key) or 0) + int(ip.get(key) or 0)
            sports["best_pnl"] = max([v for v in (pre.get("best_pnl"), ip.get("best_pnl")) if v is not None], default=None)
            sports["worst_pnl"] = min([v for v in (pre.get("worst_pnl"), ip.get("worst_pnl")) if v is not None], default=None)
            return {"summary": all_summary, "sports_summary": sports, "racing_summary": racing}

        return {
            "ok": True,
            "as_of_utc": as_of.isoformat(),
            "timezone_name": tz_name,
            "time_basis": "settled_at",
            "today": {
                "from_utc": today_local.astimezone(timezone.utc).isoformat(),
                "to_utc": as_of.isoformat(),
                "local_date": today_local.date().isoformat(),
                **bucket(today_local),
            },
            "seven_day": {
                "from_utc": seven_local.astimezone(timezone.utc).isoformat(),
                "to_utc": as_of.isoformat(),
                **bucket(seven_local),
            },
            "all": {
                "from_utc": None, "to_utc": as_of.isoformat(), **bucket(None),
            },
        }

    @staticmethod
    def _freshness_from_age(age_seconds: float | None, stale_after: float) -> str:
        if age_seconds is None:
            return "UNAVAILABLE"
        return "CURRENT" if age_seconds <= max(1.0, float(stale_after or 90.0)) else "STALE"

    def _monitor_account_state(self, cfg: dict, *, capture: bool = False, context: str = "view") -> dict:
        """Project SIM account state without repairing wallet authority.

        ``capture=True`` is an explicit diagnostic snapshot command and may append
        account audit history; ordinary ``capture=False`` views are authority-read-only.
        """
        currency = str(cfg.get("account_currency", "GBP") or "GBP").upper()
        tolerance = max(0.0001, float(cfg.get("account_reconciliation_tolerance", 0.01) or 0.01))
        reserve_pct = {
            "pre_match": self._monitor_reserve_pct(cfg, "pre_match"),
            "in_play": self._monitor_reserve_pct(cfg, "in_play"),
            "racing": self._monitor_reserve_pct(cfg, "racing"),
        }
        wallets = self.db.monitor_wallets_by_stream(reserve_pct)
        accounts = {}
        now = datetime.now(timezone.utc)
        venue_ids = sorted({str(ex).lower() for stream_wallets in wallets.values() for ex in stream_wallets})
        for exchange in venue_ids:
            allocations = []
            opening = available = reserved = realized = funding = free_normal = 0.0
            updated = []
            for stream in ("pre_match", "in_play", "racing"):
                wallet = (wallets.get(stream) or {}).get(exchange) or {}
                if not wallet:
                    continue
                eq = float(wallet.get("equity") or 0.0)
                item = {
                    "stream": stream,
                    "label": "Pre-match Sports" if stream == "pre_match" else "In-play Sports" if stream == "in_play" else "Greyhound Racing",
                    "opening": round(float(wallet.get("opening_balance") or 0.0), 4),
                    "available": round(float(wallet.get("available") or 0.0), 4),
                    "reserved": round(float(wallet.get("reserved") or 0.0), 4),
                    "equity": round(eq, 4),
                    "realized_pnl": round(float(wallet.get("realized_pnl") or 0.0), 4),
                    "funding_adjustment": round(float(wallet.get("funding_adjustment") or 0.0), 4),
                }
                allocations.append(item)
                opening += item["opening"]
                available += item["available"]
                reserved += item["reserved"]
                realized += item["realized_pnl"]
                funding += item["funding_adjustment"]
                free_normal += float(wallet.get("free_for_normal") or 0.0)
                if wallet.get("updated_at"):
                    dt = self._parse_utc_dt(wallet.get("updated_at"))
                    if dt: updated.append(dt)
            equity = available + reserved
            expected_equity = opening + realized + funding
            delta = equity - expected_equity
            status = "RECONCILED" if abs(delta) <= tolerance else "DISCREPANCY"
            account = {
                "exchange": exchange,
                "display_name": ((self.provider_runtime.providers.get(exchange).venue.venue_name
                                  if self.provider_runtime.providers.get(exchange) is not None else exchange.replace("_", " ").title())),
                "provider_id": exchange,
                "venue_id": (self.provider_runtime.providers.get(exchange).venue.venue_id
                             if self.provider_runtime.providers.get(exchange) is not None else exchange),
                "mode": "sim",
                "storage_mode": "sim",
                "currency": currency,
                "source": "virtual_ledger",
                "available": round(available, 4),
                "reserved": round(reserved, 4),
                "exposure": round(reserved, 4),
                "equity": round(equity, 4),
                "opening_balance": round(opening, 4),
                "realized_pnl": round(realized, 4),
                "funding_adjustment": round(funding, 4),
                "free_for_normal": round(free_normal, 4),
                "freshness": "CURRENT",
                "last_updated": max(updated).isoformat() if updated else now.isoformat(),
                "allocations": allocations,
                "reconciliation": {"status": status, "expected": round(expected_equity, 4), "observed": round(equity, 4), "delta": round(delta, 4), "tolerance": tolerance},
                "order_placement_enabled": False,
            }
            accounts[exchange] = account
            if capture:
                self.db.record_account_snapshot(mode="sim", exchange=exchange, currency=currency, source="virtual_ledger",
                    available=available, reserved=reserved, exposure=reserved, equity=equity, realized_pnl=realized,
                    freshness="CURRENT", context=context, metadata={"allocations": allocations, "funding_adjustment": funding, "ui_mode": "sim"})
                self.db.record_balance_reconciliation(mode="sim", exchange=exchange, status=status,
                    expected=expected_equity, observed=equity, delta=delta, tolerance=tolerance,
                    details={"source": "virtual_ledger", "allocations": allocations, "funding_adjustment": funding, "ui_mode": "sim"})
        overall_delta = round(sum(float(x["reconciliation"]["delta"]) for x in accounts.values()), 4)
        overall = "RECONCILED" if all(x["reconciliation"]["status"] == "RECONCILED" for x in accounts.values()) else "DISCREPANCY"
        return {"accounts": accounts, "reconciliation": {"status": overall, "delta": overall_delta, "tolerance": tolerance}}

    async def _live_account_state_async(self, cfg: dict, *, capture: bool = False, context: str = "view") -> dict:
        """Read dedicated LIVE provider account state without touching SIM ledgers."""
        return await self.live_providers.account_state(cfg, refresh=bool(capture), context=context)

    @staticmethod
    def _account_period_bounds(period: str, timezone_offset_minutes: int = 0) -> tuple[str | None, str | None]:
        """Return UTC period boundaries using the browser's local offset when supplied."""
        period = str(period or "30D").strip().upper()
        if period == "ALL":
            return None, None
        offset = timedelta(minutes=-int(timezone_offset_minutes or 0))
        now_utc = datetime.now(timezone.utc)
        local_now = now_utc + offset
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "TODAY":
            local_start = local_midnight
        elif period == "7D":
            local_start = local_midnight - timedelta(days=6)
        elif period == "30D":
            local_start = local_midnight - timedelta(days=29)
        elif period == "MTD":
            local_start = local_midnight.replace(day=1)
        elif period == "YTD":
            local_start = local_midnight.replace(month=1, day=1)
        else:
            local_start = local_midnight - timedelta(days=29)
        start_utc = (local_start - offset).astimezone(timezone.utc)
        return start_utc.isoformat(), now_utc.isoformat()

    @staticmethod
    def _authoritative_account_totals(rows: list[dict], fields: list[str], *, mode: str) -> dict:
        """Compatibility wrapper over the shared selected-mode account aggregator."""
        return authoritative_account_totals(rows, fields, mode=mode)

    @staticmethod
    def _compatible_currency_totals(rows: list[dict], fields: list[str]) -> dict:
        """Compatibility wrapper for authoritative LIVE account aggregation."""
        return API._authoritative_account_totals(rows, fields, mode="live")

    @staticmethod
    def _compatible_sim_currency_totals(rows: list[dict], fields: list[str]) -> dict:
        """Compatibility wrapper for authoritative SIM account aggregation."""
        return API._authoritative_account_totals(rows, fields, mode="sim")

    def account_overview(self, data=None):
        data = data or {}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        requested = str(data.get("mode") or "sim").lower()
        mode = "live" if requested == "live" else "sim"
        capture = bool(data.get("capture", False))
        context = str(data.get("context") or ("manual_refresh" if capture else "view"))
        if mode == "live":
            state = asyncio.run(self._live_account_state_async(cfg, capture=capture, context=context))
            history = self.db.live_account_snapshot_history(limit=200) if bool(data.get("include_history", False)) else []
        else:
            state = self._monitor_account_state(cfg, capture=capture, context=context)
            history = self.db.account_snapshot_history(mode="sim", limit=200) if bool(data.get("include_history", False)) else []
        return {"ok": True, "mode": mode, "storage_mode": mode, "live_execution_allowed": False, "live_order_placement": False,
                "currency": str(cfg.get("account_currency", "GBP") or "GBP").upper(),
                "financial_revision": self.db.sim_financial_revision() if mode == "sim" else None,
                "accounts": state.get("accounts") or {}, "reconciliation": state.get("reconciliation") or {},
                "history": history, "provider": state.get("provider") or ("sim_virtual_ledger" if mode == "sim" else "live_account_provider"),
                "capabilities": (self.live_providers.manifest().get("capabilities") if mode == "live" else {"balances": True, "account_health": True, "account_activity": True, "market_feed": True, "positions": True, "executions": True, "settlements": True, "performance": True, "replay": True, "order_placement": False}),
                "account_semantics": {
                    "venue_account": "Underlying SIM virtual venue account or LIVE provider account.",
                    "exchange_account": "Compatibility alias for the underlying venue account.",
                    "portfolio_allocation": "Sports/Racing attribution inside ArbScanner; not a separate venue account.",
                    "position_capital": "Capital reserved/deployed by individual executions.",
                }}

    @staticmethod
    def _account_transaction_rows(rows: list[dict], mode: str) -> list[dict]:
        """Normalize account-ledger evidence for the read-only Accounts page.

        This is presentation normalization only: it never invents a movement or
        venue attribution. SIM rows come from the auditable funding ledger; LIVE
        rows come from provider-native account history.
        """
        out = []
        mode = "live" if str(mode).lower() == "live" else "sim"
        sim_labels = {
            "add": "FUNDS ADDED",
            "withdraw": "FUNDS WITHDRAWN",
            "set": "BALANCE ADJUSTMENT",
            "reset": "BALANCE ADJUSTMENT",
            "reallocate": "ALLOCATION",
        }
        live_labels = {
            "DEPOSIT": "FUNDS ADDED",
            "WITHDRAWAL": "FUNDS WITHDRAWN",
            "SETTLEMENT": "SETTLEMENT",
            "COMMISSION": "COMMISSION",
            "ADJUSTMENT": "BALANCE ADJUSTMENT",
            "OTHER": "OTHER",
        }
        for raw in rows or []:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            if mode == "sim":
                native = str(row.get("action") or "other").lower()
                tx_type = sim_labels.get(native, native.replace("_", " ").upper() or "OTHER")
                occurred = row.get("created_at")
                balance = row.get("resulting_equity")
                detail = row.get("reason") or native
                provider = str(row.get("exchange") or "").lower()
            else:
                native = str(row.get("movement_type") or row.get("activity_type") or "OTHER").upper()
                tx_type = live_labels.get(native, native.replace("_", " "))
                occurred = row.get("occurred_at") or row.get("timestamp") or row.get("created_at")
                balance = row.get("balance_after")
                detail = row.get("description") or row.get("reference") or row.get("external_reference") or row.get("native_type") or native
                provider = str(row.get("provider_id") or row.get("exchange") or "").lower()
            out.append({
                **row,
                "mode": mode,
                "provider_id": provider or row.get("provider_id") or row.get("exchange"),
                "transaction_type": tx_type,
                "occurred_at": occurred,
                "balance_after": balance,
                "detail": detail,
            })
        out.sort(key=lambda x: str(x.get("occurred_at") or ""), reverse=True)
        return out

    @staticmethod
    def _account_transaction_summary(rows: list[dict], *, deposited=None, withdrawn=None, currency: str | None = None) -> dict:
        signed = [float(x.get("amount") or 0.0) for x in rows or [] if x.get("amount") is not None]
        added = deposited
        removed = withdrawn
        if added is None:
            added = sum(max(0.0, v) for v in signed)
        if removed is None:
            removed = sum(max(0.0, -v) for v in signed)
        net = None if added is None or removed is None else float(added) - float(removed)
        return {
            "currency": currency,
            "added": None if added is None else round(float(added), 4),
            "withdrawn": None if removed is None else round(float(removed), 4),
            "net_funding": None if net is None else round(net, 4),
            "transactions": len(rows or []),
        }

    def _sim_accounts_period(self, cfg: dict, from_utc: str | None, to_utc: str | None) -> dict:
        state = self._monitor_account_state(cfg, capture=False, context="accounts")
        accounts = state.get("accounts") or {}
        adjustments = self.db.sim_account_adjustments(None, 5000)
        if from_utc:
            adjustments = [x for x in adjustments if str(x.get("created_at") or "") >= str(from_utc)]
        if to_utc:
            adjustments = [x for x in adjustments if str(x.get("created_at") or "") <= str(to_utc)]
        by_provider: dict[str, dict] = {}
        for pid, account in accounts.items():
            rows = [x for x in adjustments if str(x.get("exchange") or "").lower() == str(pid).lower()]
            deposited = sum(max(0.0, float(x.get("amount") or 0.0)) for x in rows)
            withdrawn = sum(max(0.0, -float(x.get("amount") or 0.0)) for x in rows)
            by_provider[pid] = {"deposited": round(deposited,4), "withdrawn": round(withdrawn,4),
                                "net_capital_added": round(deposited-withdrawn,4), "history_available": True}
        summary = self.db.settled_monitor_summary(from_utc=from_utc, to_utc=to_utc)
        settled = self.db.settled_monitor_positions(from_utc=from_utc, to_utc=to_utc, limit=5000)
        commission = 0.0
        for row in settled:
            audit = self._settled_commission_audit(row)
            if audit.get("available"):
                commission += float(audit.get("commission") or 0.0)
        # SIM settlement P&L is portfolio-level canonical evidence; do not fabricate a venue split.
        return {"state": state, "providers": by_provider, "trading_pnl": round(float(summary.get("pnl") or 0.0),4),
                "commission": round(commission,4), "activity": adjustments}

    @staticmethod
    def _live_provider_period_metrics(provider_id: str, history_state: dict, snapshot_rows: list[dict]) -> dict:
        rows = list(history_state.get("rows") or [])
        capability = str(history_state.get("capability") or "none")
        history_ok = bool(history_state.get("available")) and not bool(history_state.get("error"))
        kinds: dict[str, list[dict]] = {}
        for row in rows:
            kinds.setdefault(str(row.get("movement_type") or row.get("activity_type") or "OTHER").upper(), []).append(row)
        declared_support = dict(history_state.get("metric_support") or {})
        metric_support = {
            "deposits": bool(declared_support.get("deposits")) and history_ok,
            "withdrawals": bool(declared_support.get("withdrawals")) and history_ok,
            "trading_pnl": bool(declared_support.get("trading_pnl")) and history_ok,
            "commission": bool(declared_support.get("commission")) and history_ok,
        }
        deposited = sum(max(0.0, float(x.get("amount") or 0.0)) for x in kinds.get("DEPOSIT", [])) if metric_support["deposits"] else None
        withdrawn = sum(abs(float(x.get("amount") or 0.0)) for x in kinds.get("WITHDRAWAL", [])) if metric_support["withdrawals"] else None
        trading = sum(float(x.get("amount") or 0.0) for x in kinds.get("SETTLEMENT", [])) if metric_support["trading_pnl"] else None
        commission = sum(abs(float(x.get("amount") or 0.0)) for x in kinds.get("COMMISSION", [])) if metric_support["commission"] else None
        snaps = sorted(snapshot_rows, key=lambda x: str(x.get("received_at") or ""))
        balances = [x for x in snaps if x.get("balance") is not None]
        net_change = round(float(balances[-1]["balance"]) - float(balances[0]["balance"]),4) if len(balances) >= 2 else None
        net_capital = None if deposited is None or withdrawn is None else round(deposited-withdrawn,4)
        # Reconcile only when every component and anchored balance is genuinely available.
        reconciliation = {"status": "UNAVAILABLE", "difference": None, "reason": "Insufficient provider accounting evidence"}
        if net_change is not None and deposited is not None and withdrawn is not None and trading is not None and commission is not None:
            expected_change = deposited - withdrawn + trading - commission
            reconciliation = {"status": "CALCULATED", "difference": round(net_change-expected_change,4),
                              "expected_change": round(expected_change,4), "observed_change": net_change}
        return {"history_capability": capability, "history_available": history_ok, "history_stale": bool(history_state.get("stale")),
                "deposited": None if deposited is None else round(deposited,4), "withdrawn": None if withdrawn is None else round(withdrawn,4),
                "net_capital_added": net_capital, "trading_pnl": None if trading is None else round(trading,4),
                "commission": None if commission is None else round(commission,4), "net_account_change": net_change,
                "reconciliation": reconciliation, "metric_support": metric_support, "activity_count": len(rows)}

    def accounts_page(self, data=None):
        """Mode-aware operational Accounts page; LIVE side is strictly read-only."""
        data = data or {}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        mode = "live" if str(data.get("mode") or "sim").lower() == "live" else "sim"
        period = str(data.get("period") or "30D").upper()
        tz_offset = int(data.get("timezone_offset_minutes") or 0)
        from_utc, to_utc = self._account_period_bounds(period, tz_offset)
        refresh = bool(data.get("refresh", False))
        currency = str(cfg.get("account_currency", "GBP") or "GBP").upper()
        tolerance = max(0.0001, float(cfg.get("account_reconciliation_tolerance", 0.01) or 0.01))
        controls = {x["provider_id"]: x for x in self.db.venue_controls()}

        if mode == "live":
            state = asyncio.run(self.live_providers.account_state(cfg, refresh=refresh, context="accounts_manual" if refresh else "accounts"))
            history_states = asyncio.run(self.live_providers.refresh_account_activity(cfg, from_utc=from_utc, to_utc=to_utc,
                                                                                     refresh=refresh, context="accounts"))
            accounts = state.get("accounts") or {}
            provider_rows = []
            all_activity = []
            for pid, account in accounts.items():
                hstate = history_states.get(pid) or {"available": False, "capability": account.get("account_history_capability", "none"), "rows": []}
                snap_rows = self.db.live_account_snapshot_history(provider_id=pid, from_utc=from_utc, to_utc=to_utc, limit=10000)
                period_metrics = self._live_provider_period_metrics(pid, hstate, snap_rows)
                all_activity.extend(hstate.get("rows") or [])
                control = controls.get(pid) or {}
                provider_rows.append({**account, "period": period_metrics,
                    "account_nickname": str(control.get("account_nickname") or account.get("display_name") or pid.title()),
                    "sim_feed_enabled": bool(control.get("sim_feed_enabled")), "live_feed_enabled": bool(control.get("live_feed_enabled")),
                    "sim_account_enabled": bool(control.get("sim_account_enabled")), "live_account_enabled": bool(control.get("live_account_enabled", True)),
                    "live_execution_enabled": bool(control.get("live_execution_enabled"))})
            current = self._compatible_currency_totals(provider_rows, ["available", "exposure", "balance"])
            current["venue_accounts"] = int(current.get("reporting_venue_accounts") or 0)
            current["connected_providers"] = sum(1 for x in provider_rows if x.get("connection_state") == "connected" and not x.get("is_stale"))
            current["configured_providers"] = sum(1 for x in provider_rows if not x.get("integration_pending"))
            current["total_providers"] = len(provider_rows)

            fresh_period = [x for x in provider_rows if x.get("period",{}).get("history_available")]
            pcurrencies = sorted({str(x.get("currency") or "").upper() for x in fresh_period if x.get("currency")})
            period_totals = {"currency": pcurrencies[0] if len(pcurrencies)==1 else None, "currencies": pcurrencies,
                             "compatible": len(pcurrencies) <= 1}
            for key in ("deposited","withdrawn","net_capital_added","trading_pnl","commission","net_account_change"):
                vals = [x.get("period",{}).get(key) for x in fresh_period]
                period_totals[key] = round(sum(float(v) for v in vals),4) if fresh_period and len(pcurrencies)==1 and all(v is not None for v in vals) else None
            recs = [x.get("period",{}).get("reconciliation") or {} for x in fresh_period]
            calc = [x for x in recs if x.get("status") == "CALCULATED"]
            if calc and len(calc) == len(fresh_period):
                diff = round(sum(float(x.get("difference") or 0.0) for x in calc),4)
                reconciliation = {"status": "RECONCILED" if abs(diff) <= tolerance else "WARNING", "difference": diff, "tolerance": tolerance}
            else:
                reconciliation = {"status": "UNAVAILABLE", "difference": None, "tolerance": tolerance,
                                  "reason": "Opening/closing balance or provider transaction classification is incomplete"}
            all_activity.sort(key=lambda x: str(x.get("occurred_at") or x.get("timestamp") or ""), reverse=True)
            transactions = self._account_transaction_rows(all_activity, "live")
            transaction_summary = self._account_transaction_summary(
                transactions, deposited=period_totals.get("deposited"), withdrawn=period_totals.get("withdrawn"),
                currency=period_totals.get("currency"),
            )
            # Accounts is observation-only. Reuse the canonical operational
            # status after any requested provider refresh so feed/latency/freshness
            # state matches the money snapshot shown on the same page.
            operations = self._operational_status("live")
            return {"ok": True, "mode": "live", "period": period, "from_utc": from_utc, "to_utc": to_utc,
                    "current": current, "period_kpis": period_totals, "providers": provider_rows,
                    "activity": all_activity[:500], "transactions": transactions[:500], "transaction_summary": transaction_summary,
                    "reconciliation": reconciliation,
                    "operations": operations, "page_read_only": True,
                    "live_execution_allowed": False, "live_order_placement": False,
                    "read_only": True, "isolated_from_sim": True, "audit": self.db.live_account_audit(limit=100)}

        sim = self._sim_accounts_period(cfg, from_utc, to_utc)
        accounts = sim.get("state",{}).get("accounts") or {}
        provider_rows = []
        labels = {"betfair":"Betfair", "matchbook":"Matchbook", "smarkets":"Smarkets"}
        for pid in ("betfair","matchbook","smarkets"):
            control = controls.get(pid) or {}
            account = accounts.get(pid) or accounts.get(labels[pid]) or {}
            if pid == "smarkets" and not account:
                provider_rows.append({"provider_id":pid,"exchange":pid,"display_name":"Smarkets","account_nickname":str(control.get("account_nickname") or "Smarkets"),
                    "currency":None,"available":None,"exposure":None,"balance":None,"equity":None,"connection_state":"awaiting_api_access",
                    "integration_pending":True,"error":"Awaiting API access","period":{},
                    "sim_feed_enabled":bool(control.get("sim_feed_enabled")),"live_feed_enabled":bool(control.get("live_feed_enabled")),
                    "sim_account_enabled":bool(control.get("sim_account_enabled")),"live_account_enabled":bool(control.get("live_account_enabled")),
                    "live_execution_enabled":bool(control.get("live_execution_enabled"))})
                continue
            p = sim.get("providers",{}).get(pid) or {"deposited":0.0,"withdrawn":0.0,"net_capital_added":0.0,"history_available":True}
            provider_rows.append({**account, "provider_id":pid, "display_name":labels[pid],
                "account_nickname":str(control.get("account_nickname") or labels[pid]),
                "balance": account.get("equity"), "period": {**p, "trading_pnl": None, "commission": None, "net_account_change": None, "reconciliation": {"status":"UNAVAILABLE","difference":None}},
                "sim_feed_enabled":bool(control.get("sim_feed_enabled")),"live_feed_enabled":bool(control.get("live_feed_enabled")),
                "sim_account_enabled":bool(control.get("sim_account_enabled")),"live_account_enabled":bool(control.get("live_account_enabled", True)),
                "live_execution_enabled":bool(control.get("live_execution_enabled"))})
        current = self._compatible_sim_currency_totals(provider_rows, ["available","exposure","balance"])
        current.update({
            # Compatibility alias retained for older consumers: this now means
            # fully reporting venue accounts, never supported venue slots.
            "venue_accounts": int(current.get("reporting_venue_accounts") or 0),
            # Counts are mode-specific and do not imply network connectivity.
            "connected_providers": int(current.get("reporting_venue_accounts") or 0),
            "configured_providers": sum(1 for x in provider_rows if x.get("sim_account_enabled") and not x.get("integration_pending")),
            "total_providers": 3,
        })
        deposits = sum(float(x.get("deposited") or 0.0) for x in sim.get("providers",{}).values())
        withdrawals = sum(float(x.get("withdrawn") or 0.0) for x in sim.get("providers",{}).values())
        period_kpis = {"currency": currency, "currencies":[currency], "compatible":True,
                       "deposited":round(deposits,4), "withdrawn":round(withdrawals,4), "net_capital_added":round(deposits-withdrawals,4),
                       "trading_pnl":sim.get("trading_pnl"), "commission":sim.get("commission"), "net_account_change":None}
        transactions = self._account_transaction_rows(sim.get("activity") or [], "sim")
        transaction_summary = self._account_transaction_summary(
            transactions, deposited=period_kpis.get("deposited"), withdrawn=period_kpis.get("withdrawn"), currency=currency
        )
        operations = self._operational_status("sim")
        return {"ok": True, "mode":"sim", "period":period, "from_utc":from_utc, "to_utc":to_utc,
                "financial_revision": self.db.sim_financial_revision(),
                "current":current, "period_kpis":period_kpis, "providers":provider_rows, "activity":sim.get("activity") or [],
                "transactions": transactions, "transaction_summary": transaction_summary,
                "reconciliation":sim.get("state",{}).get("reconciliation") or {}, "operations": operations, "page_read_only": True,
                "live_execution_allowed":False, "live_order_placement":False, "read_only":False, "isolated_from_live":True, "audit":[]}

    def sim_account_adjust(self, data=None):
        data = data or {}
        cfg = self.db.get_setting("config", DEFAULT_CONFIG) or DEFAULT_CONFIG.copy()
        # Ensure all allocation wallets exist before distributing the adjustment.
        self.db.ensure_monitor_streams(
            self._monitor_starting_balances(cfg, "pre_match"),
            self._monitor_starting_balances(cfg, "in_play"),
            self._monitor_starting_balances(cfg, "racing"),
        )
        result = self.db.adjust_sim_account(
            exchange=str(data.get("exchange") or ""), action=str(data.get("action") or ""),
            value=data.get("value"), currency=str(cfg.get("account_currency", "GBP") or "GBP"),
            reason=str(data.get("reason") or "manual SIM account adjustment"),
        )
        if result.get("ok"):
            result["account_overview"] = self.account_overview({"mode": "sim", "capture": True, "context": "sim_manual_adjustment"})
        return result

    def sim_account_adjustment_history(self, data=None):
        data = data or {}
        return {"ok": True, "mode": "sim", "rows": self.db.sim_account_adjustments(data.get("exchange"), int(data.get("limit") or 200))}

    def sim_portfolio_budget_overview(self, data=None):
        """Read current SIM allocation budgets without lazily creating wallets."""
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        reserve_pct = {
            "pre_match": self._monitor_reserve_pct(cfg, "pre_match"),
            "in_play": self._monitor_reserve_pct(cfg, "in_play"),
            "racing": self._monitor_reserve_pct(cfg, "racing"),
        }
        wallets = self.db.monitor_wallets_by_stream(reserve_pct)
        labels = {"pre_match": "Pre-match Sports", "in_play": "In-play Sports", "racing": "Greyhound Racing"}
        rows = []
        for stream in ("pre_match", "in_play", "racing"):
            stream_wallets = wallets.get(stream) or {}
            venues = {}
            for venue_id, wallet in sorted(stream_wallets.items()):
                venues[venue_id] = {
                    "equity": round(float(wallet.get("equity") or 0.0), 4),
                    "reserved": round(float(wallet.get("reserved") or 0.0), 4),
                    "hedge_reserve": round(float(wallet.get("hedge_reserve") or 0.0), 4),
                    "free_for_normal": round(float(wallet.get("free_for_normal") or 0.0), 4),
                }
            total_equity = sum(float(v.get("equity") or 0.0) for v in venues.values())
            hedge_amount = sum(float(v.get("hedge_reserve") or 0.0) for v in venues.values())
            bf = venues.get("betfair") or {}; mb = venues.get("matchbook") or {}
            rows.append({
                "stream": stream, "label": labels[stream], "venues": venues,
                # Compatibility aliases retained while the current UI has two cards.
                "betfair": round(float(bf.get("equity") or 0.0), 4),
                "matchbook": round(float(mb.get("equity") or 0.0), 4),
                "betfair_reserved": round(float(bf.get("reserved") or 0.0), 4),
                "matchbook_reserved": round(float(mb.get("reserved") or 0.0), 4),
                "total": round(total_equity, 4), "total_allocation": round(total_equity, 4),
                "hedge_reserve_pct": round(float(reserve_pct[stream]), 4),
                "hedge_reserve_amount": round(hedge_amount, 4),
                "normal_deployable": round(max(0.0, total_equity - hedge_amount), 4),
                "normal_available_now": round(sum(float(v.get("free_for_normal") or 0.0) for v in venues.values()), 4),
            })
        accounts = self._monitor_account_state(cfg, capture=False, context="sim_budget_overview").get("accounts") or {}
        return {
            "ok": True, "mode": "sim", "currency": str(cfg.get("account_currency", "GBP") or "GBP").upper(),
            "rows": rows,
            "account_totals": {ex: round(float(account.get("equity") or 0.0), 4) for ex, account in accounts.items()},
            "rule": "Market budgets reallocate current SIM equity only; account totals and reserved open-position capital are preserved.",
        }

    def sim_portfolio_budget_update(self, data=None):
        data = data or {}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        currency = str(cfg.get("account_currency", "GBP") or "GBP").upper()
        targets = data.get("targets") or {}
        hedge_amounts = data.get("hedge_reserve_amounts") or {}
        key_map = {
            "pre_match": "pre_match_execution_hedge_reserve_pct",
            "in_play": "inplay_execution_hedge_reserve_pct",
            "racing": "racing_execution_hedge_reserve_pct",
        }
        reserve_pcts = {}
        try:
            for stream, key in key_map.items():
                stream_targets = targets.get(stream) or {}
                total = max(0.0, sum(float(v or 0.0) for v in stream_targets.values()))
                requested = max(0.0, float(hedge_amounts.get(stream, 0.0) or 0.0))
                if requested > total + 1e-9:
                    return {"ok": False, "message": f"{stream.replace('_',' ').title()} hedge reserve cannot exceed its total market budget"}
                reserve_pcts[stream] = (requested / total) * 100.0 if total > 0 else 0.0
        except (TypeError, ValueError):
            return {"ok": False, "message": "Invalid SIM market or hedge-reserve budget"}

        # Reallocate current equity only after all input validation. The DB then
        # enforces exchange-account totals and protects reserved open-position capital.
        result = self.db.rebalance_sim_allocations(
            targets=targets, currency=currency, reason=str(data.get("reason") or "manual SIM market budget update"),
            tolerance=max(0.0001, float(cfg.get("account_reconciliation_tolerance", 0.01) or 0.01)),
        )
        if not result.get("ok"):
            return result
        for stream, key in key_map.items():
            cfg[key] = min(100.0, max(0.0, float(reserve_pcts[stream])))
        # Legacy alias follows pre-match for old execution/replay paths.
        cfg["execution_hedge_reserve_pct"] = cfg["pre_match_execution_hedge_reserve_pct"]
        self.db.set_setting("config", cfg)
        return {
            "ok": True, "message": "SIM market budgets and hedge reserves updated",
            "budgets": self.sim_portfolio_budget_overview({}),
            "account_overview": self.account_overview({"mode": "sim", "capture": True, "context": "sim_budget_update"}),
        }

    def data_provider_manifest(self, data=None):
        return {
            "ok": True,
            "shared_market_data": {
                "available": True,
                "owner": "provider_runtime_canonical_market_state",
                "consumers": ["sim", "live"],
                "economic_authority": False,
                "provenance_required": True,
                "rule": "Provider-derived market/reference observations are shared; feed entitlement and timestamps retain provider provenance.",
            },
            "sim": {
                "provider": "sim_scanner_and_virtual_ledger", "isolated_from_live": True,
                "capabilities": {"balances": True, "market_feed": True, "positions": True, "executions": True, "settlements": True, "performance": True, "replay": True, "order_placement": False},
            },
            "live": self.live_providers.manifest(),
            "live_decision_evidence": {"available": True, "decision_type": "simulated", "orders_write_capability": False},
            "rule": "Market/reference data is shared. SIM and LIVE economic/execution state never fall back to each other; LIVE decision evidence has no financial authority.",
        }

    def live_decision_evidence(self, data=None):
        """Read isolated 0.9.8 LIVE-context simulated decision evidence only."""
        data = data or {}
        domain = str(data.get("domain") or "all").lower()
        if domain not in {"all", "sports", "racing"}:
            domain = "all"
        date_from = self._parse_utc(data.get("from_utc"))
        date_to = self._parse_utc(data.get("to_utc"))
        from_utc = date_from.isoformat() if date_from else data.get("from_utc")
        to_utc = date_to.isoformat() if date_to else data.get("to_utc")
        try:
            limit = max(1, min(2000, int(data.get("limit") or 200)))
        except (TypeError, ValueError):
            limit = 200
        include_summary = bool(data.get("include_summary", True))
        include_rows = bool(data.get("include_rows", True))
        include_latest = bool(data.get("include_latest", True))
        summary = self.db.live_decision_summary(from_utc, to_utc, domain=domain) if include_summary else {"summary": {}, "quality": [], "reasons": [], "provider_pairs": []}
        rows = self.db.live_decision_events_between(from_utc, to_utc, domain=domain, limit=limit) if include_rows else []
        latest = self.db.live_decision_latest_rows(domain=domain, limit=min(limit, 200)) if include_latest else []
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        return {
            "ok": True,
            "application_mode": "live",
            "decision_type": "simulated",
            "enabled": bool(cfg.get("live_decision_evidence_enabled", True)),
            "summary": summary.get("summary") or {},
            "quality": summary.get("quality") or [],
            "reasons": summary.get("reasons") or [],
            "provider_pairs": summary.get("provider_pairs") or [],
            "rows": rows,
            "latest": latest,
            "orders_write_capability": False,
            "live_execution_allowed": False,
            "real_orders_sent": 0,
            "note": "Provider-derived LIVE-context observations evaluated by the existing decision engine. Outcomes are simulated; Accounts remain provider-derived and no order method is reachable.",
        }

    def live_execution_activity(self, data=None):
        """Actual LIVE execution read model.

        0.9.8 has no real order/execution pathway, so this is a valid empty
        dataset rather than a SIM fallback or a projection of simulated
        decision evidence.  The contract is intentionally stable for future
        real execution persistence.
        """
        data = data or {}
        domain = str(data.get("domain") or "all").lower()
        if domain not in {"all", "sports", "racing"}:
            domain = "all"
        return {
            "ok": True, "mode": "live", "domain": domain, "rows": [],
            "metrics": {"attempted": 0, "submitted": 0, "filled": 0, "partial": 0, "cancelled": 0, "failed": 0, "qualified": 0, "positions": 0, "settled": 0},
            "orders_write_capability": False, "live_execution_allowed": False,
            "empty": True, "message": "No LIVE execution activity recorded."
        }

    def live_results(self, data=None):
        data = data or {}
        domain = str(data.get("domain") or "all").lower()
        if domain not in {"all", "sports", "racing"}:
            domain = "all"
        return {"ok": True, "mode": "live", "domain": domain, "rows": [], "count": 0, "empty": True,
                "message": "No LIVE settled positions.", "live_execution_allowed": False}

    def live_performance(self, data=None):
        """Page-native LIVE Performance read model.

        Actual remains honest real execution/settlement state (currently empty).
        Expected is isolated LIVE-context decision evidence and never becomes
        account P&L, a position, fill or execution record.
        """
        data = data or {}
        requested_mode = canonical_mode_value(data.get("mode") or "live")
        if requested_mode != "live":
            return {"ok": False, "message": "live_performance is LIVE-only; SIM financial state cannot enter this endpoint.", "mode": requested_mode}
        basis = str(data.get("basis") or "actual").lower()
        domain = str(data.get("scope") or data.get("domain") or "all").lower()
        if domain not in {"all", "sports", "racing"}: domain = "all"
        stream = str(data.get("stream") or "all").lower()
        if stream == "racing": domain = "racing"
        date_from = self._parse_utc(data.get("from_utc")); date_to = self._parse_utc(data.get("to_utc"))
        from_utc = date_from.isoformat() if date_from else data.get("from_utc")
        to_utc = date_to.isoformat() if date_to else data.get("to_utc")
        if basis != "simulated":
            return {
                "ok": True, "mode": "live", "basis": "actual", "empty": True,
                "summary": {"net_pnl": None, "portfolio_roi_pct": None, "deployed_turnover": 0.0, "return_on_deployed_pct": None,
                            "captured_edge_pct": None, "positions_executed": 0, "attempted_positions": 0, "settled_bets": 0},
                "performance": {"domains": [], "markets": [], "venues": [], "venue_pairs": [],
                                "funnel": {"observed":0,"positive":0,"qualified":0,"attempted":0,"executed":0,"settled":0},
                                "capital_efficiency": {}, "recovery": {}},
                "rows": [], "filter_options": {"sports": [], "markets": [], "venues": [], "venue_pairs": []},
                "basis_note": "Actual LIVE performance uses real LIVE executions and settlements only. None exist yet; simulated decisions are not executions.",
                "message": "No actual LIVE positions or settlements have been recorded.",
                "live_execution_allowed": False, "orders_write_capability": False,
            }
        analytics = self.db.live_decision_analytics(from_utc, to_utc, domain=domain, sport=data.get("sport") or "all", market_type=data.get("market") or "", provider_pair=data.get("venue_pair") or "all")
        sm = analytics.get("summary") or {}
        expected = float(sm.get("expected_profit_sum") or 0.0); attempts=int(sm.get("simulated_attempts") or 0); fills=int(sm.get("simulated_fills") or 0)
        deployed=float(sm.get("executable_stake_sum") or 0.0)
        roi=(expected/deployed*100.0) if deployed else None
        def perfrow(x, key_label):
            executable = float(x.get("executable_stake_sum") or 0.0)
            profit = float(x.get("expected_profit_sum") or 0.0)
            roi_pct = (profit / executable * 100.0) if executable else None
            qualified = int(x.get("qualified") or 0)
            attempts = int(x.get("simulated_attempts") or 0)
            return {**x, "positions": int(x.get("simulated_fills") or 0), "capital_deployed": executable,
                    "pnl": profit, "return_on_deployed_pct": roi_pct, "roi_pct": roi_pct,
                    "execution_conversion_pct": (attempts / qualified * 100.0) if qualified else 0.0,
                    "captured_edge_pct": None, "avg_qualified_edge_pct": None, "recovery_rate_pct": None,
                    "settled": 0, "executed": 0, "attempted": attempts}
        markets=[]
        for x in analytics.get("markets") or []:
            y=perfrow(x,"market"); y["market"]=x.get("market_type") or "Unknown"; markets.append(y)
        pairs=[]
        for x in analytics.get("provider_pairs") or []:
            y=perfrow(x,"pair"); y["pair"]=x.get("provider_pair") or "Unknown"; pairs.append(y)
        domains=[]
        for x in analytics.get("domains") or []:
            y=perfrow(x,"domain"); y["key"]=x.get("domain") or "all"; y["label"]="Racing" if y["key"]=="racing" else "Sports"; domains.append(y)
        daily_map={}
        for x in analytics.get("hourly") or []:
            day=str(x.get("hour_utc") or "")[:10]
            d=daily_map.setdefault(day,{"date":day,"profit":0.0,"deployed_turnover":0.0,"settled":0})
            d["profit"] += float(x.get("expected_profit_sum") or 0.0); d["deployed_turnover"] += float(x.get("executable_stake_sum") or 0.0); d["settled"] += int(x.get("simulated_fills") or 0)
        hourly=[]; cumulative=0.0
        for day in sorted(daily_map):
            d=daily_map[day]; cumulative+=d["profit"]; dep=float(d["deployed_turnover"] or 0.0)
            hourly.append({**d,"profit":round(d["profit"],4),"deployed_turnover":round(dep,4),"cumulative_period_profit":round(cumulative,4),"portfolio_roi_pct":None,"return_on_deployed_pct":round(d["profit"]/dep*100.0,4) if dep else None,"captured_edge_pct":None})
        sports=sorted({str(x.get("sport")) for x in analytics.get("markets") or [] if x.get("sport")})
        market_opts=sorted({str(x.get("market_type")) for x in analytics.get("markets") or [] if x.get("market_type")})
        pair_opts=sorted({str(x.get("provider_pair")) for x in analytics.get("provider_pairs") or [] if x.get("provider_pair")})
        venues=sorted({v for pair in pair_opts for v in [p.strip() for p in pair.replace("|","+").replace("/","+").split("+")] if v})
        return {
            "ok": True, "mode":"live", "basis":"simulated", "empty": int(sm.get("observed") or 0)==0,
            "summary": {"net_pnl": expected, "portfolio_roi_pct": roi, "deployed_turnover": deployed, "return_on_deployed_pct": roi,
                        "captured_edge_pct": None, "positions_executed": 0, "attempted_positions": attempts, "settled_bets": 0,
                        "current_capital": None, "current_deployed": 0.0, "average_deployed": float(sm.get("average_executable_stake") or 0.0), "peak_deployed": 0.0, "average_utilization_pct": 0.0},
            "performance": {"domains":domains,"markets":markets,"venues":[],"venue_pairs":pairs,
                            "funnel":{"observed":int(sm.get("observed") or 0),"positive":int(sm.get("positive") or 0),"qualified":int(sm.get("qualified") or 0),
                                      "attempted":attempts,"executed":0,"settled":0,"simulated_fills":fills,"execution_grade":int(sm.get("execution_grade") or 0),
                                      "previous_conversion_pct":{},"observed_to_executed_pct":0.0},
                            "capital_efficiency":{"available_trading_capital":0.0,"reserved_capital":0.0,"average_capital_per_position":float(sm.get("average_executable_stake") or 0.0),"peak_deployed":0.0,"average_utilization_pct":0.0,"profit_per_1000_deployed":(expected/deployed*1000.0) if deployed else 0.0},
                            "recovery":{"positions":0,"rate_pct":None,"qualified_edge_value":expected,"final_pnl":expected,"edge_lost":0.0,"execution_leakage":0.0}},
            "rows": hourly, "quality":analytics.get("quality") or [], "reasons":analytics.get("reasons") or [],
            "filter_options":{"sports":sports,"markets":market_opts,"venues":venues,"venue_pairs":pair_opts},
            "basis_note":"Expected LIVE performance is simulated decision evidence from provider-derived market observations. It is not LIVE account P&L, execution or settlement.",
            "live_execution_allowed":False,"orders_write_capability":False,
        }

    def live_market_analysis(self, data=None):
        """Shared market/reference analytics plus isolated LIVE decision evidence."""
        data = dict(data or {})
        base = self.market_analysis({**data, "mode": "live", "_include_economics": False})
        if not base.get("ok"):
            return base
        filters = MarketFilters.from_data(data)
        domain = filters.live_domain
        date_from=self._parse_utc(data.get("from_utc")); date_to=self._parse_utc(data.get("to_utc"))
        analytics=self.db.live_decision_analytics(date_from.isoformat() if date_from else data.get("from_utc"), date_to.isoformat() if date_to else data.get("to_utc"), domain=domain, sport=data.get("sport") or "all")
        decision_by_market={}
        for x in analytics.get("markets") or []:
            key=(str(x.get("domain") or "sports"),str(x.get("sport") or "Unknown"),str(x.get("market_type") or "Unknown"))
            decision_by_market[key]=x
        rows=[]
        for row0 in base.get("rows") or []:
            key=("racing" if str(row0.get("section"))=="racing" else "sports",str(row0.get("sport") or "Unknown"),str(row0.get("market_name") or "Unknown"))
            rows.append(live_market_row(row0, decision_by_market.get(key) or {}))
        base["rows"]=rows
        sm=analytics.get("summary") or {}; shared_f=base.get("liquidity_funnel") or {}
        base["liquidity_funnel"]={"observed":int(shared_f.get("observed") or 0),"positive":int(shared_f.get("positive") or 0),"liquidity_capable":int(shared_f.get("liquidity_capable") or 0),
                                   "qualified":0,"attempted":0,"executed":0,"settled":0}
        base["live_decision_qualified"] = int(sm.get("qualified") or 0)
        for v in base.get("venue_summary") or []: v["opportunities"]=0
        base["reasons"]=analytics.get("reasons") or []
        base["live_decision_summary"]=sm; base["live_decision_quality"]=analytics.get("quality") or []; base["application_mode"]="live"
        base["financial_time_basis"]="no_live_execution"; base["orders_write_capability"]=False; base["live_execution_allowed"]=False
        return base

    def live_replay(self, data=None):
        return {"ok": True, "mode": "live", "rows": [], "count": 0, "empty": True,
                "message": "No LIVE execution history available to replay.", "live_execution_allowed": False}

    def live_view_data(self, data=None):
        data = data or {}
        # Dedicated LIVE view state may include read-only account/provider state and
        # isolated 0.9.8 decision evidence. It must never read SIM positions/results
        # as a fallback and cannot expose an order-writing capability.
        page = str(data.get("page") or "unknown")
        payload = self.live_providers.view(page)
        decisions = self.live_decision_evidence({"domain": data.get("domain") or "all", "limit": data.get("limit") or 50})
        payload["decision_evidence"] = decisions
        payload.setdefault("capabilities", {})["decision_evidence"] = True
        payload["capabilities"]["market_observations"] = True
        payload["live_execution_allowed"] = False
        payload["orders_write_capability"] = False
        return payload

    def live_preflight(self, data=None):
        data = data or {}
        return self.live_providers.preflight(str(data.get("stream") or "pre_match"))

    def account_snapshot_history(self, data=None):
        data = data or {}
        requested_mode = str(data.get("mode") or "sim").lower()
        if requested_mode == "live":
            rows = self.db.live_account_snapshot_history(provider_id=data.get("exchange") or data.get("provider_id"),
                                                         from_utc=data.get("from_utc"), to_utc=data.get("to_utc"),
                                                         limit=int(data.get("limit") or 2000))
            return {"ok": True, "mode": "live", "rows": rows}
        rows = self.db.account_snapshot_history(mode="sim", exchange=data.get("exchange"),
                                                from_utc=data.get("from_utc"), to_utc=data.get("to_utc"),
                                                limit=int(data.get("limit") or 2000))
        return {"ok": True, "mode": "sim", "rows": rows}

    def account_timeline(self, data=None):
        data = data or {}
        requested_mode = str(data.get("mode") or "sim").lower()
        mode = "live" if requested_mode == "live" else "sim"
        from_utc = data.get("from_utc")
        to_utc = data.get("to_utc")
        limit = max(1, min(10000, int(data.get("limit") or 5000)))
        if mode == "live":
            rows = self.db.live_account_snapshot_history(from_utc=from_utc, to_utc=to_utc, limit=limit)
            opening = {}
            for row in reversed(rows):
                opening.setdefault(str(row.get("provider_id") or "unknown"), row)
            return {"ok": True, "mode": "live", "storage_mode": "live", "opening": opening, "rows": rows,
                    "first_available": opening, "live_execution_allowed": False, "time_basis": "received_at"}
        rows = self.db.account_snapshot_history(mode=mode, from_utc=from_utc, to_utc=to_utc, limit=limit)
        # Replay/account charts consume canonical exchange-level checkpoints, not
        # the stream-attribution records written alongside them.
        rows = [x for x in rows if x.get("stream") is None]
        opening = self.db.account_snapshot_state_at(mode=mode, at_utc=from_utc) if from_utc else {}
        first_rows = self.db.account_snapshot_history(mode=mode, limit=20000)
        first_available = {}
        for row in first_rows:
            if row.get("stream") is not None:
                continue
            key = str(row.get("exchange") or "unknown").lower()
            first_available.setdefault(key, row)
        return {"ok": True, "mode": ("live" if mode == "live" else "sim"), "storage_mode": mode, "opening": opening, "rows": rows,
                "first_available": first_available, "live_execution_allowed": False, "time_basis": "captured_at"}

    def account_integrity_report(self, data=None):
        """Cross-check canonical MONITOR account balances against open-position reservations."""
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        state = self._monitor_account_state(cfg, capture=False, context="integrity")
        accounts = state.get("accounts") or {}
        tolerance = max(0.0001, float(cfg.get("account_reconciliation_tolerance", 0.01) or 0.01))
        reserved_from_positions = {}
        open_positions = self.db.monitor_open_positions()
        for pos in open_positions:
            for raw_venue, amount in (pos.get("stakes_by_exchange") or {}).items():
                key = provider_id_for_name(str(raw_venue or "")) or str(raw_venue or "").strip().lower()
                if not key:
                    continue
                reserved_from_positions[key] = reserved_from_positions.get(key, 0.0) + max(0.0, float(amount or 0.0))
        checks = []
        venue_ids = sorted(set(accounts) | set(reserved_from_positions))
        for key in venue_ids:
            a = accounts.get(key) or {}
            observed = float(a.get("reserved") or 0.0)
            expected = float(reserved_from_positions.get(key) or 0.0)
            delta = observed - expected
            status = "RECONCILED" if abs(delta) <= tolerance else "DISCREPANCY"
            checks.append({"name": "open_position_reservations", "exchange": key, "venue_id": key, "status": status,
                           "expected": round(expected, 4), "observed": round(observed, 4),
                           "delta": round(delta, 4), "tolerance": tolerance})
            alloc_equity = sum(float(x.get("equity") or 0.0) for x in (a.get("allocations") or []))
            account_equity = float(a.get("equity") or 0.0)
            adelta = account_equity - alloc_equity
            checks.append({"name": "portfolio_allocation_equity", "exchange": key, "venue_id": key,
                           "status": "RECONCILED" if abs(adelta) <= tolerance else "DISCREPANCY",
                           "expected": round(account_equity, 4), "observed": round(alloc_equity, 4),
                           "delta": round(adelta, 4), "tolerance": tolerance})
            rec = a.get("reconciliation") or {}
            checks.append({"name": "ledger_equity", "exchange": key, "venue_id": key, "status": rec.get("status") or "WARNING",
                           "expected": rec.get("expected"), "observed": rec.get("observed"),
                           "delta": rec.get("delta"), "tolerance": tolerance})
        overall = "RECONCILED" if all(x.get("status") == "RECONCILED" for x in checks) else "DISCREPANCY"
        return {"ok": True, "mode": "sim", "status": overall, "checks": checks,
                "open_positions": len(open_positions), "currency": str(cfg.get("account_currency", "GBP") or "GBP").upper(),
                "live_execution_allowed": False}

    def dashboard_overview(self, data=None):
        """Project Dashboard state; Stage 04 forbids lazy authority repair here."""
        data = data or {}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        financial = self.financial_reconciliation_snapshot(data)
        # Configured opening capital is a read-only reference value; do not use it
        # to repair missing wallet authority during a Dashboard projection.
        pre_balances = self._monitor_starting_balances(cfg, "pre_match")
        inplay_balances = self._monitor_starting_balances(cfg, "in_play")
        racing_balances = self._monitor_starting_balances(cfg, "racing")
        reserve_pct = {
            "pre_match": self._monitor_reserve_pct(cfg, "pre_match"),
            "in_play": self._monitor_reserve_pct(cfg, "in_play"),
            "racing": self._monitor_reserve_pct(cfg, "racing"),
        }
        wallets_by_stream = self.db.monitor_wallets_by_stream(reserve_pct)
        positions = self.db.monitor_open_positions()
        rows = []
        for pos in positions:
            sim = pos.get("simulation") or {}
            stakes = sim.get("stakes") or []
            stream = str(pos.get("stream") or "pre_match")
            after = sim.get("after_hedge") or {}
            deployed = round(float(pos.get("deployed") or 0.0), 4)
            balanced = bool(after.get("balanced"))
            locked_profit = float(after.get("worst_case_pnl")) if balanced and after.get("worst_case_pnl") is not None else None
            best_case_profit = float(after.get("best_case_pnl")) if after.get("best_case_pnl") is not None else None
            locked_return_pct = (locked_profit / deployed) * 100.0 if locked_profit is not None and deployed > 0 else None
            execution_details = pos.get("execution_details") or {}
            if not execution_details:
                execution_row = self.db.latest_execution_for_opportunity(int(pos.get("opportunity_id") or 0)) or {}
                execution_details = execution_row.get("details") or {}
            execution_result = (execution_details.get("execution_result") or {})
            scaled_entry = (sim.get("scaled_entry") or sim.get("superbet")) if isinstance(sim, dict) else None
            if not isinstance(scaled_entry, dict):
                scaled_entry = (execution_details.get("scaled_entry") or execution_details.get("superbet")) if isinstance(execution_details, dict) else None
            if not isinstance(scaled_entry, dict):
                scaled_entry = {}
            emergency_hedge = any(str(event.get("state") or "").upper() == "EMERGENCY_HEDGE" for event in (execution_result.get("events") or []))
            try:
                planned_legs = json.loads(pos.get("legs_json") or "[]")
            except Exception:
                planned_legs = []
            if not isinstance(planned_legs, list):
                planned_legs = []
            rows.append({
                "execution_id": int(pos.get("execution_run_id") or 0), "opportunity_id": int(pos.get("opportunity_id") or 0),
                "mode": "sim", "monitor_stream": stream, "section": pos.get("section") or ("racing" if stream == "racing" else "sports"), "event_key": pos.get("event_key"), "event_name": pos.get("event_name") or pos.get("event_key"), "market_name": pos.get("market_name"),
                "sport": pos.get("sport") or "Unknown", "event_start": pos.get("event_start"), "started_at": pos.get("opened_at"),
                "state": "MONITOR_OPEN", "deployed": deployed,
                "expected_profit": round(float(pos.get("expected_profit") or 0.0),4),
                "locked_profit": None if locked_profit is None else round(locked_profit, 4),
                "locked_return_pct": None if locked_return_pct is None else round(locked_return_pct, 6),
                "locked_is_guaranteed": bool(locked_profit is not None),
                "worst_case_pnl": None if after.get("worst_case_pnl") is None else round(float(after.get("worst_case_pnl")), 4),
                "best_case_profit": None if best_case_profit is None else round(best_case_profit, 4),
                "captured_profit": None if locked_profit is None else round(locked_profit, 4),
                "emergency_hedge": bool(emergency_hedge),
                "is_scaled_entry": bool(scaled_entry.get("is_scaled_entry") or scaled_entry.get("is_superbet")),
                "is_superbet": bool(scaled_entry.get("is_scaled_entry") or scaled_entry.get("is_superbet")),  # legacy client alias
                "tranche_count": max(1, int(scaled_entry.get("tranche_count") or 1)),
                "scaled_entry": scaled_entry,
                "superbet": scaled_entry,  # legacy client alias
                "planned_leg_count": len(planned_legs),
                "account_mode": "sim",
                "currency": str(pos.get("currency") or cfg.get("account_currency", "GBP") or "GBP").upper(),
                "account_funding": {str(k): round(float(v or 0.0), 4) for k, v in (pos.get("stakes_by_exchange") or {}).items()},
                "bets": [{
                    "exchange": x.get("exchange"), "selection": x.get("selection"),
                    "odds": float(x.get("odds") or 0.0), "stake": float(x.get("stake") or 0.0),
                    "is_hedge": bool(x.get("is_hedge")), "tranche": max(1, int(x.get("tranche") or 1)),
                    "role": ("emergency_hedge" if bool(x.get("is_hedge")) and emergency_hedge else
                             "balancing" if bool(x.get("is_hedge")) else
                             "scaled_entry" if int(x.get("tranche") or 1) > 1 else "planned"),
                } for x in stakes],
                "is_real": False,
            })
        stream_summary = {}
        for stream_name, wallets in wallets_by_stream.items():
            stream_rows = [x for x in rows if x.get("monitor_stream") == stream_name]
            stream_summary[stream_name] = {
                "equity": round(sum(float(v.get("equity") or 0.0) for v in wallets.values()),4),
                "committed": round(sum(float(v.get("reserved") or 0.0) for v in wallets.values()),4),
                "available": round(sum(float(v.get("free_for_normal") or 0.0) for v in wallets.values()),4),
                "hedge_reserve": round(sum(float(v.get("hedge_reserve") or 0.0) for v in wallets.values()),4),
                "active_bets": len(stream_rows),
                "expected_open_profit": round(sum(float(x.get("expected_profit") or 0.0) for x in stream_rows),4),
                "locked_open_profit": round(sum(float(x.get("locked_profit") or 0.0) for x in stream_rows if x.get("locked_profit") is not None),4),
                "locked_open_deployed": round(sum(float(x.get("deployed") or 0.0) for x in stream_rows if x.get("locked_profit") is not None),4),
                "wallets": wallets,
            }
        for summary in stream_summary.values():
            locked_deployed = float(summary.get("locked_open_deployed") or 0.0)
            locked_profit = float(summary.get("locked_open_profit") or 0.0)
            summary["locked_open_return_pct"] = round((locked_profit / locked_deployed) * 100.0, 6) if locked_deployed > 0 else 0.0
        working_bankroll = round(sum(float(x.get("equity") or 0.0) for x in stream_summary.values()),4)
        starting_bankroll = round(sum(float(v or 0.0) for v in pre_balances.values()) + sum(float(v or 0.0) for v in inplay_balances.values()) + sum(float(v or 0.0) for v in racing_balances.values()), 4)
        wallet_profit = round(working_bankroll - starting_bankroll, 4)
        total_profit = round(float((((financial.get("all") or {}).get("summary") or {}).get("pnl") or 0.0)), 4)
        sports_total_profit = round(float((((financial.get("all") or {}).get("sports_summary") or {}).get("pnl") or 0.0)), 4)
        racing_total_profit = round(float((((financial.get("all") or {}).get("racing_summary") or {}).get("pnl") or 0.0)), 4)
        sports_working_bankroll = round(sum(float((stream_summary.get(name) or {}).get("equity") or 0.0) for name in ("pre_match", "in_play")), 4)
        racing_working_bankroll = round(float((stream_summary.get("racing") or {}).get("equity") or 0.0), 4)
        committed = round(sum(float(x.get("committed") or 0.0) for x in stream_summary.values()),4)
        available = round(sum(float(x.get("available") or 0.0) for x in stream_summary.values()),4)
        hedge_reserve = round(sum(float(x.get("hedge_reserve") or 0.0) for x in stream_summary.values()),4)
        scaled_entry_summary = self.db.scaled_entry_summary(include_demo=not bool(cfg.get("hide_demo_data", True)))
        account_state = self._monitor_account_state(cfg, capture=False, context="dashboard")
        account_currency = str(cfg.get("account_currency", "GBP") or "GBP").upper()

        # 0.9.36 Dashboard venue tiles. Capital comes from the canonical SIM
        # venue-account ledger. Open capital follows the actual per-venue stake
        # reservations. Settled P&L uses stored exchange settlement contribution;
        # legacy rows without that split are attributed by deployed stake so the
        # venue totals still reconcile to the canonical position P&L. Locked open
        # profit is likewise an attribution by deployed stake, not a claim that a
        # single venue independently guarantees that amount.
        def dashboard_venue_key(value):
            text = str(value or "").strip().lower()
            if "betfair" in text:
                return "betfair"
            if "matchbook" in text:
                return "matchbook"
            if "smarkets" in text:
                return "smarkets"
            return text.replace(" ", "_")

        controls = {str(x.get("provider_id") or "").lower(): x for x in self.db.venue_controls()}
        account_rows = account_state.get("accounts") or {}
        venue_metrics = {}
        for venue_id, display_name in (("betfair", "Betfair"), ("matchbook", "Matchbook"), ("smarkets", "Smarkets")):
            account = account_rows.get(venue_id) or {}
            control = controls.get(venue_id) or {}
            venue_metrics[venue_id] = {
                "venue_id": venue_id,
                "display_name": display_name,
                "account_nickname": str(control.get("account_nickname") or display_name),
                "currency": str(account.get("currency") or account_currency),
                "capital": (round(float(account.get("equity")), 4) if account.get("equity") is not None else None),
                "capital_in_play": 0.0,
                "profit_today": 0.0,
                "locked_profit": 0.0,
                "locked_profit_basis": "deployed_stake_attribution",
                "profit_today_basis": "exchange_settlement_contribution",
            }

        for position in rows:
            stakes = position.get("account_funding") or {}
            canonical_stakes = {}
            for raw_venue, raw_stake in stakes.items():
                venue_id = dashboard_venue_key(raw_venue)
                if venue_id in venue_metrics:
                    canonical_stakes[venue_id] = canonical_stakes.get(venue_id, 0.0) + max(0.0, float(raw_stake or 0.0))
            total_stake = sum(canonical_stakes.values())
            for venue_id, stake in canonical_stakes.items():
                venue_metrics[venue_id]["capital_in_play"] += stake
            locked = position.get("locked_profit")
            if locked is not None and total_stake > 0:
                for venue_id, stake in canonical_stakes.items():
                    venue_metrics[venue_id]["locked_profit"] += float(locked or 0.0) * (stake / total_stake)

        local_tz, _ = self._viewer_timezone(data)
        now_utc = datetime.now(timezone.utc)
        local_now = now_utc.astimezone(local_tz)
        today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        settled_today = self.db.settled_monitor_positions(
            from_utc=today_start.isoformat(), to_utc=(now_utc + timedelta(seconds=1)).isoformat(),
            include_demo=not bool(cfg.get("hide_demo_data", True)), limit=20000,
        )
        for position in settled_today:
            try:
                split = json.loads(position.get("realized_by_exchange_json") or "{}")
            except Exception:
                split = {}
            canonical_split = {}
            if isinstance(split, dict):
                for raw_venue, raw_value in split.items():
                    venue_id = dashboard_venue_key(raw_venue)
                    if venue_id in venue_metrics:
                        canonical_split[venue_id] = canonical_split.get(venue_id, 0.0) + float(raw_value or 0.0)
            if not canonical_split:
                try:
                    stakes = json.loads(position.get("stakes_by_exchange_json") or "{}")
                except Exception:
                    stakes = {}
                canonical_stakes = {}
                if isinstance(stakes, dict):
                    for raw_venue, raw_stake in stakes.items():
                        venue_id = dashboard_venue_key(raw_venue)
                        if venue_id in venue_metrics:
                            canonical_stakes[venue_id] = canonical_stakes.get(venue_id, 0.0) + max(0.0, float(raw_stake or 0.0))
                total_stake = sum(canonical_stakes.values())
                if total_stake > 0:
                    pnl_value = float(position.get("realized_pnl") or 0.0)
                    canonical_split = {venue_id: pnl_value * (stake / total_stake) for venue_id, stake in canonical_stakes.items()}
            for venue_id, value in canonical_split.items():
                venue_metrics[venue_id]["profit_today"] += float(value or 0.0)

        for metric in venue_metrics.values():
            for key in ("capital_in_play", "profit_today", "locked_profit"):
                metric[key] = round(float(metric.get(key) or 0.0), 4)

        # Exchange-wallet drift is a capital-location diagnostic, not a second
        # strategy-P&L calculation.  It explains whether venue balances moved due
        # to settlements/funding and gives routing evidence enough context to
        # distinguish normal migration from a persistent placement skew.
        # Routing diagnostics are explanatory evidence, not a dependency of the
        # Dashboard's canonical financial state.  Keep the Dashboard alive if an
        # upgrade/diagnostic read is temporarily unavailable; financial/account
        # ownership must not disappear because an optional audit surface failed.
        try:
            routing_diagnostics = self.db.exchange_routing_diagnostics(limit=10000)
            routing_diagnostics_error = None
        except Exception as exc:
            routing_diagnostics = {
                "positions": 0, "economic_ties": 0, "positions_with_equivalent_routes": 0,
                "routing_reasons": {}, "favourite_outcomes": {}, "held_outcomes": {},
                "held_outcome_pct": {}, "winning_outcomes": {}, "winning_outcome_pct": {},
                "available": False,
            }
            routing_diagnostics_error = f"{type(exc).__name__}: {exc}"
        wallet_drift = {}
        total_current = 0.0; total_opening = 0.0
        for venue_id in ("betfair", "matchbook", "smarkets"):
            opening = current = reserved = realized = funding = 0.0
            for wallets in wallets_by_stream.values():
                row = (wallets or {}).get(venue_id) or {}
                opening += float(row.get("opening_balance") or 0.0)
                current += float(row.get("equity") or 0.0)
                reserved += float(row.get("reserved") or 0.0)
                realized += float(row.get("realized_pnl") or 0.0)
                funding += float(row.get("funding_adjustment") or 0.0)
            total_current += current; total_opening += opening
            wallet_drift[venue_id] = {
                "opening_balance": round(opening,4), "current_balance": round(current,4),
                "settled_pnl_contribution": round(realized,4), "outstanding_exposure": round(reserved,4),
                "funding_adjustment": round(funding,4),
                # Capital migration is the selected venue's balance movement after
                # external funding. Settlement contribution explains the expected
                # component; any residual is surfaced separately for investigation.
                "net_capital_migration": round(current-opening-funding,4),
                "unexplained_migration": round(current-opening-funding-realized,4),
            }
        for venue_id, drift in wallet_drift.items():
            drift["bankroll_share_pct"] = round((float(drift["current_balance"])/total_current)*100.0,2) if total_current>0 else 0.0
            opening_share = (float(drift["opening_balance"])/total_opening)*100.0 if total_opening>0 else 0.0
            drift["opening_share_pct"] = round(opening_share,2)
            drift["share_drift_pct_points"] = round(float(drift["bankroll_share_pct"])-opening_share,2)
            if venue_id in venue_metrics:
                venue_metrics[venue_id].update({
                    "bankroll_share_pct": drift["bankroll_share_pct"],
                    "opening_share_pct": drift["opening_share_pct"],
                    "share_drift_pct_points": drift["share_drift_pct_points"],
                    "net_capital_migration": drift["net_capital_migration"],
                    "unexplained_migration": drift["unexplained_migration"],
                })
        max_share_drift = max((abs(float(x.get("share_drift_pct_points") or 0.0)) for x in wallet_drift.values()), default=0.0)
        held_pct = routing_diagnostics.get("held_outcome_pct") or {}
        routing_skew = max((abs(float(v)-50.0) for k,v in held_pct.items() if k in {"betfair","matchbook"}), default=0.0)
        if float(total_profit or 0.0) < -0.01:
            drift_classification = "STRATEGY_LOSS_PRESENT"
        elif max_share_drift >= 10.0 and int(routing_diagnostics.get("economic_ties") or 0) > 0 and routing_skew >= 15.0:
            drift_classification = "ROUTING_IMBALANCE_REVIEW"
        elif max_share_drift >= 10.0:
            drift_classification = "SETTLEMENT_DRIVEN_MIGRATION"
        else:
            drift_classification = "BALANCED"
        wallet_drift_summary = {
            "classification": drift_classification, "material": max_share_drift >= 10.0,
            "max_share_drift_pct_points": round(max_share_drift,2), "venues": wallet_drift,
            "routing": routing_diagnostics,
            "routing_diagnostics_available": routing_diagnostics_error is None,
            "routing_diagnostics_error": routing_diagnostics_error,
        }

        return {
            "ok": True, "mode": str(self.db.get_setting("mode", "sim") or "sim").lower(),
            "working_bankroll": working_bankroll, "starting_bankroll": starting_bankroll, "total_profit": total_profit,
            "sports_total_profit": sports_total_profit, "racing_total_profit": racing_total_profit,
            "sports_working_bankroll": sports_working_bankroll, "racing_working_bankroll": racing_working_bankroll,
            "wallet_profit": wallet_profit, "financial": financial, "scaled_entry": scaled_entry_summary, "superbet": scaled_entry_summary,
            "financial_reconciliation_delta": round(wallet_profit - total_profit, 4),
            "capital_in_play": committed, "hedge_reserve": hedge_reserve, "available_capital": available,
            "bets_in_play": len(rows), "monitor_in_play": len(rows), "live_in_play": 0,
            "expected_open_profit": round(sum(float(x.get("expected_profit") or 0.0) for x in rows),4),
            "locked_open_profit": round(sum(float(x.get("locked_profit") or 0.0) for x in rows if x.get("locked_profit") is not None),4),
            "locked_open_deployed": round(sum(float(x.get("deployed") or 0.0) for x in rows if x.get("locked_profit") is not None), 4),
            "unlocked_open_deployed": round(sum(float(x.get("deployed") or 0.0) for x in rows if x.get("locked_profit") is None), 4),
            "locked_position_count": sum(1 for x in rows if x.get("locked_profit") is not None),
            "locked_open_return_pct": round((sum(float(x.get("locked_profit") or 0.0) for x in rows if x.get("locked_profit") is not None) / sum(float(x.get("deployed") or 0.0) for x in rows if x.get("locked_profit") is not None)) * 100.0, 6) if sum(float(x.get("deployed") or 0.0) for x in rows if x.get("locked_profit") is not None) > 0 else 0.0,
            "locked_return_basis": "balanced_position_deployed_capital",
            "captured_open_profit": round(sum(float(x.get("captured_profit") or 0.0) for x in rows),4),
            "wallets": wallets_by_stream.get("pre_match") or {}, "wallets_by_stream": wallets_by_stream, "stream_summary": stream_summary, "rows": rows,
            "accounts": account_state.get("accounts") or {}, "account_reconciliation": account_state.get("reconciliation") or {},
            "venue_metrics": venue_metrics, "wallet_drift": wallet_drift_summary, "routing_diagnostics": routing_diagnostics,
            "account_currency": account_currency, "financial_revision": self.db.sim_financial_revision(),
            "operations": self._operational_status("sim"),
            "delayed_feed_warning": "Betfair delayed development data is still in use. IN-PLAY Monitor results are scenario evidence, not proof of live executability.",
        }

    def reset_monitor_balances(self, data=None):
        data = data or {}
        stream = str(data.get("stream") or "all").lower().strip()
        if stream not in {"all", "pre_match", "in_play", "racing"}:
            return {"ok": False, "message": "stream must be pre_match, in_play, racing or all"}
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        streams = ["pre_match", "in_play", "racing"] if stream == "all" else [stream]
        open_positions = []
        for stream_name in streams:
            open_positions.extend(self.db.monitor_open_positions(stream_name))
        if open_positions and not bool(data.get("force")):
            label = "selected Monitor portfolio" if stream != "all" else "Monitor portfolios"
            return {"ok": False, "message": f"Cannot reset {label} while {len(open_positions)} position(s) are open.", "open_positions": len(open_positions)}

        supplied = data.get("balances") or {}
        reset_rows = {}
        for stream_name in streams:
            candidate = supplied.get(stream_name) if stream == "all" and isinstance(supplied.get(stream_name), dict) else supplied
            if not candidate:
                candidate = self._monitor_starting_balances(cfg, stream_name)
            clean = {
                "betfair": max(0.0, float(candidate.get("betfair", 0.0) or 0.0)),
                "matchbook": max(0.0, float(candidate.get("matchbook", 0.0) or 0.0)),
            }
            self.db.reset_monitor_wallets(clean, stream=stream_name)
            if stream_name == "pre_match":
                cfg["pre_match_monitor_betfair_starting_balance"] = clean["betfair"]
                cfg["pre_match_monitor_matchbook_starting_balance"] = clean["matchbook"]
                cfg["monitor_betfair_starting_balance"] = clean["betfair"]
                cfg["monitor_matchbook_starting_balance"] = clean["matchbook"]
            elif stream_name == "in_play":
                cfg["inplay_monitor_betfair_starting_balance"] = clean["betfair"]
                cfg["inplay_monitor_matchbook_starting_balance"] = clean["matchbook"]
            else:
                cfg["racing_monitor_betfair_starting_balance"] = clean["betfair"]
                cfg["racing_monitor_matchbook_starting_balance"] = clean["matchbook"]
            reset_rows[stream_name] = clean
        self.db.set_setting("config", cfg)
        reserves = {"pre_match": self._monitor_reserve_pct(cfg, "pre_match"), "in_play": self._monitor_reserve_pct(cfg, "in_play"), "racing": self._monitor_reserve_pct(cfg, "racing")}
        label = "all Monitor portfolios" if stream == "all" else ("Pre-match Monitor portfolio" if stream == "pre_match" else "In-play Monitor portfolio" if stream == "in_play" else "Racing Monitor portfolio")
        return {"ok": True, "message": f"{label} reset.", "reset": reset_rows, "wallets": self.db.monitor_wallets_by_stream(reserves),
                "account_overview": self.account_overview({"mode": "sim", "capture": True, "context": "sim_wallet_reset"}),
                "state": self.get_state()}

    def reset_trading_data(self, data=None):
        """Archive then fully reset all scanner/trading-derived data.

        The LaunchAgent is paused first so a scan cannot repopulate rows halfway
        through the reset. Credentials, exchange setup and user configuration are
        preserved.
        """
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        was_enabled = bool(cfg.get("scanner_enabled", True))
        cfg["scanner_enabled"] = False
        self.db.set_setting("config", cfg)
        pause = self.service.pause()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive = APP_DIR / "archives" / f"arbscanner-pre-reset-{stamp}.sqlite3"
        before = self.db.trading_data_counts()
        try:
            self.db.backup_to(archive)
            remaining = self.db.clear_research_history()
            self.db.reset_monitor_wallets(self._monitor_starting_balances(cfg, "pre_match"), stream="pre_match", capture_snapshot=False)
            self.db.reset_monitor_wallets(self._monitor_starting_balances(cfg, "in_play"), stream="in_play", capture_snapshot=False)
            self.db.reset_monitor_wallets(self._monitor_starting_balances(cfg, "racing"), stream="racing", capture_snapshot=False)
            # Wallet rows are intentionally recreated; every other trading table
            # must remain empty at the point the reset completes.
            verify = self.db.trading_data_counts()
            unexpected = {k: v for k, v in verify.items() if k not in {"monitor_wallets", "monitor_stream_wallets"} and v != 0}
            if unexpected:
                raise RuntimeError(f"Reset verification failed: {unexpected}")
            self.db.set_setting("mode", "sim")
            return_payload = {
                "ok": True,
                "message": "Trading data reset complete.",
                "archive": str(archive),
                "cleared_rows": int(sum(v for v in before.values() if v > 0)),
                "remaining": remaining,
            }
        except Exception as exc:
            return_payload = {"ok": False, "message": f"Trading data reset failed: {exc}", "archive": str(archive) if archive.exists() else None}
        finally:
            cfg["scanner_enabled"] = was_enabled
            self.db.set_setting("config", cfg)
            if pause.get("was_loaded"):
                resume = self.service.resume()
                return_payload["worker_resume"] = resume
                if not resume.get("ok") and return_payload.get("ok"):
                    return_payload["ok"] = False
                    return_payload["message"] += " Data was reset, but the background scanner could not be restarted automatically."
        return_payload["state"] = self.get_state()
        return return_payload

    def alert_status(self, data=None):
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        diag = self.db.alert_diagnostics()
        candidate = self.db.latest_alert_candidate()
        evaluation = None
        if candidate:
            ref = float(candidate.get("reference_bankroll") or cfg.get("quality_reference_bankroll", 500.0) or 500.0)
            broi = float(candidate.get("bankroll_roi_pct") or 0.0)
            profile = {
                "quality_band": candidate.get("quality_band"),
                "quality_score": float(candidate.get("quality_score") or 0.0),
                "deployed_roi_pct": float(candidate.get("net_roi_pct") or 0.0),
                "bankroll_roi_pct": broi,
                "capital_used_pct": float(candidate.get("capital_used_pct") or 0.0),
                "expected_profit": ref * broi / 100.0,
            }
            ok, reason = qualifies_for_alert(profile, cfg)
            evaluation = {**candidate, "qualifies": ok, "reason": reason, "expected_profit": profile["expected_profit"]}
        return {
            "ok": True,
            "enabled": bool(cfg.get("alerts_enabled", True)),
            "bands": cfg.get("alert_quality_bands") or [],
            "thresholds": {
                "deployed_roi_pct": float(cfg.get("alert_min_deployed_roi_pct",0.75)),
                "bankroll_roi_pct": float(cfg.get("alert_min_bankroll_roi_pct",0.0)),
                "capital_used_pct": float(cfg.get("alert_min_capital_used_pct",0.0)),
                "profit": float(cfg.get("alert_min_profit",1.0)),
            },
            "latest_candidate": evaluation,
            **diag,
        }

    def test_alert(self, data=None):
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        result = send_macos_notification_diagnostic(
            "ArbScanner alert test",
            "Desktop alerts are enabled and the notification command is responding.",
            sound=bool(cfg.get("alert_sound", True)),
        )
        self.db.record_alert_attempt(None, "TEST", 0.0, bool(result.get("ok")), str(result.get("message") or ""))
        return {"ok": bool(result.get("ok")), "message": result.get("message"), "status": self.alert_status()}

    def system_health(self):
        health = self.db.scanner_health()
        health["background"] = self.service.status()
        health["database_path"] = str(self.db.path)
        last = health.get("last_scan") or {}
        try:
            health["last_feed_statuses"] = json.loads(last.get("status_json") or "[]")
        except Exception:
            health["last_feed_statuses"] = []
        health["last_all_feeds_ok"] = bool(health["last_feed_statuses"]) and all(bool(x.get("ok")) for x in health["last_feed_statuses"])
        health["operations"] = self._operational_status()
        return {"ok": True, **health}

    def export_research(self, data=None):
        data = data or {}
        cfg = self.db.get_setting("config", DEFAULT_CONFIG)
        bankroll = float(data.get("bankroll") or cfg.get("quality_reference_bankroll", 500.0))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path.home() / "Downloads" / "ArbScanner-Exports" / stamp
        out_dir.mkdir(parents=True, exist_ok=True)

        history = self.opportunity_history({"bankroll": bankroll}).get("rows", [])
        opp_path = out_dir / "opportunity-history.csv"
        fields = ["detected_at","sport","event_name","event_start","market_name","strategy","quality_band","quality_score","deployed","capital_used_pct",
                  "expected_profit","gross_profit","commission_cost","deployed_roi_pct","bankroll_roi_pct","persistence_scans",
                  "persistence_seconds","peak_quality_band","peak_quality_score","outcome","realized_pnl","uses_delayed_feed"]
        with opp_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
            for row in history:
                w.writerow({k: row.get(k) for k in fields})

        latest = self.db.latest_matched_markets(limit=1000).get("rows", [])
        matched_path = out_dir / "latest-matched-markets.csv"
        mfields = ["observed_at","sport","event_name","event_start","event_status","in_play","market_name","strategy","match_score","theoretical_edge_pct","gross_roi_pct",
                   "commission_impact_pct","net_roi_pct","diagnostic_deployed","diagnostic_profit","bankroll_roi_pct","capital_used_pct",
                   "quality_band","quality_score","status","reason"]
        with matched_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=mfields); w.writeheader()
            for row in latest:
                w.writerow({k: row.get(k) for k in mfields})
        return {"ok": True, "directory": str(out_dir), "files": [str(opp_path), str(matched_path)]}

    def backup_database(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = Path.home() / "Downloads" / "ArbScanner-Backups" / f"arbscanner-{stamp}.sqlite3"
        self.db.backup_to(destination)
        return {"ok": True, "path": str(destination)}

    def database_compaction_status(self, data=None):
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        storage = self.db.snapshot_storage_health()
        matched = self.db.matched_market_storage_health(retention_hours=int(cfg.get("matched_market_retention_hours", 48) or 48))
        integrity = self.db.database_integrity_check()
        cleanup_complete = int(storage.get("legacy_rows_remaining_estimate") or 0) == 0 and int(matched.get("eligible_rows") or 0) == 0
        return {"ok": True, "cleanup_complete": cleanup_complete,
                "db_bytes": self.db.path.stat().st_size if self.db.path.exists() else 0,
                "reclaimable_bytes": max(int(storage.get("reclaimable_bytes") or 0), int(matched.get("reusable_bytes") or 0)),
                "snapshot_storage": storage, "matched_market_storage": matched,
                "integrity": integrity, "worker": self.service.status()}

    def compact_database(self, data=None):
        cfg = {**DEFAULT_CONFIG, **(self.db.get_setting("config", DEFAULT_CONFIG) or {})}
        storage = self.db.snapshot_storage_health()
        matched = self.db.matched_market_storage_health(retention_hours=int(cfg.get("matched_market_retention_hours", 48) or 48))
        remaining = int(storage.get("legacy_rows_remaining_estimate") or 0)
        if remaining > 0:
            return {"ok": False, "message": f"Legacy snapshot cleanup is not complete ({remaining:,} rows remain)."}
        matched_remaining = int(matched.get("eligible_rows") or 0)
        if matched_remaining > 0:
            return {"ok": False, "message": f"Matched-market history cleanup is not complete ({matched_remaining:,} eligible rows remain)."}
        before = self.db.path.stat().st_size if self.db.path.exists() else 0
        free = shutil.disk_usage(self.db.path.parent).free
        # Backup + VACUUM can briefly need roughly two database-sized files.
        required = max(1024 * 1024 * 1024, int(before * 2.15))
        if free < required:
            return {"ok": False, "message": f"Insufficient free disk space for backup + compaction. Need about {required/1024**3:.1f} GB; {free/1024**3:.1f} GB is free.",
                    "required_bytes": required, "free_bytes": free}
        pause = self.service.pause()
        was_loaded = bool(pause.get("was_loaded"))
        backup_path = None
        try:
            time.sleep(0.6 if was_loaded else 0.0)
            pre = self.db.database_integrity_check()
            if not pre.get("ok"):
                return {"ok": False, "message": "Pre-compaction integrity check failed; database was not modified.", "integrity": pre}
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = Path.home() / "Downloads" / "ArbScanner-Backups" / f"arbscanner-pre-vacuum-{stamp}.sqlite3"
            self.db.backup_to(backup_path)
            result = self.db.compact_database()
            result.update({"backup_path": str(backup_path), "worker_paused": was_loaded,
                           "message": "Database backup, VACUUM and integrity verification completed."})
            return result
        except Exception as exc:
            self.db.rollback_if_needed()
            return {"ok": False, "message": f"Database compaction failed safely: {exc}",
                    "backup_path": str(backup_path) if backup_path else None}
        finally:
            if was_loaded:
                self.service.resume()

    def set_demo_visibility(self, data=None):
        data=data or {}; cfg=self.db.get_setting("config",DEFAULT_CONFIG); cfg["hide_demo_data"]=bool(data.get("hide",True)); self.db.set_setting("config",cfg)
        return {"ok":True,"state":self.get_state()}

    def clear_demo_data(self):
        removed=self.db.clear_demo_data(); return {"ok":True,"removed":removed,"state":self.get_state()}

    def background_install(self): return {**self.service.install(), "state": self.get_state()}
    def background_uninstall(self): return {**self.service.uninstall(), "state": self.get_state()}

    def run_demo_scan(self):
        opp=demo_opportunity(); legs=opp["legs"]; edge=arb_edge(legs); probe=simulate_equal_return(legs,Scenario("probe",1000.0))
        if edge<=0 or not probe.get("executable"): return {"ok":False,"message":"Demo fixture does not currently represent a net arbitrage"}
        event_key=opp["event_key"]+f" {self.db.dashboard(include_demo=True)['opportunities']+1}"
        oid=self.db.add_opportunity(event_key,opp["event_name"],opp["event_start"],opp["market_name"],edge,probe["expected_roi_pct"],[asdict(l) for l in legs],[],1.0,f"demo-{event_key}",is_demo=True,strategy="1x2")
        out=[]; cfg=self.db.get_setting("config",DEFAULT_CONFIG)
        for capital in self.db.get_setting("scenarios",[]):
            s=Scenario(f"£{capital:g}",float(capital),float(cfg["max_bankroll_pct"]),float(cfg["max_event_exposure_pct"])); sim=simulate_equal_return(legs,s)
            if sim.get("executable"): self.db.add_scenario_run(oid,s.name,s.bankroll,sim["deployed"],sim["expected_profit"],sim["expected_roi_pct"],sim["limited_by"],sim["stakes"],sim["outcome_pnls"])
            out.append({"capital":capital,**sim})
        return {"ok":True,"opportunity_id":oid,"edge_pct":round(edge,4),"scenarios":out,"state":self.get_state()}
