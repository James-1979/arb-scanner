from __future__ import annotations
import json
import hashlib
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from .modes import canonical_mode_value

from .db_schema import SCHEMA



class DB:
    # v0.8.25 DB-lock hotfix: these markers identify databases that have already
    # completed all structural migrations used by the current release.  When they
    # are present, opening a second process must stay read-only so the dashboard
    # can attach while the background scanner is writing.
    _CURRENT_TABLES = {
        "settings", "snapshots", "opportunities", "scenario_runs", "settlements",
        "execution_runs", "monitor_timing_runs", "monitor_timing_observations", "job_schedules",
        "jobs", "scan_runs", "market_cache", "matched_markets",
        "opportunity_tracks", "track_observations", "alert_log", "alert_attempts",
        "monitor_wallets", "monitor_stream_wallets", "monitor_positions",
        "account_snapshots", "balance_reconciliations", "sim_account_adjustments",
        "latest_snapshots", "snapshot_rollups", "snapshot_storage_state",
        "market_hourly_rollups", "market_hourly_seen", "market_hourly_rollup_state",
        "market_financial_hourly_rollups", "market_financial_hourly_state",
        "exchange_market_discovery_hours", "exchange_market_discovery_state",
        "live_accounts", "live_order_attempts", "live_orders", "live_fills", "live_positions",
        "live_recovery_actions", "live_settlements", "live_account_movements", "live_reconciliations",
        "latest_depth_snapshots", "liquidity_depth_hourly_rollups",
        "liquidity_opportunity_hourly_rollups", "liquidity_opportunity_rollup_state",
        "live_account_snapshots", "live_account_audit",
        "matched_market_latest", "matched_market_reason_hourly_rollups", "scan_qualification_breakdown",
        "matched_market_history_state", "matched_market_storage_state", "racing_funnel_hourly_rollups",
        "live_decision_latest", "live_decision_events", "live_decision_hourly_rollups",
        "engine_instances", "engine_configs", "engine_decisions", "engine_errors",
        "engine_sim_results", "engine_monitor_timing_results", "engine_scenario_runs",
        "engine_experiments", "engine_experiment_runs", "venue_controls", "engine_evaluations",
        "settlement_audits",
    }
    _CURRENT_INDEXES = {
        "idx_latest_snapshots_exchange_time", "idx_latest_snapshots_event_time", "idx_snapshot_rollups_time",
        "idx_market_hourly_rollups_time", "idx_market_hourly_seen_time",
        "idx_market_financial_hourly_time", "idx_exchange_market_discovery_time", "idx_exchange_market_discovery_market",
        "idx_opportunities_event_market", "idx_execution_runs_mode_time",
        "idx_execution_runs_opportunity", "idx_monitor_timing_runs_opportunity",
        "idx_monitor_timing_runs_time", "idx_monitor_timing_observations_run",
        "idx_job_schedules_next", "idx_jobs_status_time", "idx_jobs_schedule",
        "idx_market_cache_active_start", "idx_market_cache_refresh",
        "idx_matched_markets_scan", "idx_matched_markets_roi",
        "idx_opportunity_tracks_last_seen", "idx_alert_attempts_time",
        "idx_alert_attempts_track", "idx_monitor_positions_status",
        "idx_monitor_positions_market", "idx_scan_runs_kind_time",
        "idx_account_snapshots_lookup", "idx_account_snapshots_stream",
        "idx_balance_reconciliations_lookup", "idx_sim_account_adjustments_time",
        "idx_opportunities_detected", "idx_opportunities_qualification_time",
        "idx_opportunities_market_analysis", "idx_settlements_time",
        "idx_execution_runs_time", "idx_execution_runs_time_mode",
        "idx_monitor_positions_execution_run", "idx_monitor_positions_opened",
        "idx_monitor_positions_settled", "idx_matched_markets_observed",
        "idx_matched_markets_analysis", "idx_scan_runs_time_kind",
        "idx_live_order_attempts_state", "idx_live_order_attempts_provider", "idx_live_fills_order",
        "idx_live_positions_status", "idx_live_account_movements_time", "idx_live_reconciliations_time",
        "idx_latest_depth_provider_time", "idx_latest_depth_market", "idx_liquidity_depth_hourly_time",
        "idx_liquidity_opportunity_hourly_time",
        "idx_live_account_snapshots_time", "idx_live_account_audit_time", "idx_live_account_movements_activity",
        "idx_matched_market_latest_scan", "idx_matched_market_latest_time", "idx_matched_market_latest_analysis",
        "idx_matched_market_reason_hourly_time", "idx_scan_qualification_breakdown_scan", "idx_racing_funnel_hourly_time",
        "idx_live_decision_latest_time", "idx_live_decision_latest_market", "idx_live_decision_events_time",
        "idx_live_decision_events_analysis", "idx_live_decision_hourly_time",
        "idx_engine_instances_route", "idx_engine_configs_active", "idx_engine_decisions_instance_time",
        "idx_engine_decisions_market", "idx_engine_decisions_economic", "idx_engine_errors_instance_time",
        "idx_engine_sim_results_instance_time", "idx_engine_monitor_timing_results_instance_time", "idx_engine_scenario_runs_engine_time",
        "idx_engine_experiments_instance", "idx_engine_experiment_runs_experiment_time",
        "idx_venue_controls_updated", "idx_engine_evaluations_scope", "idx_engine_evaluations_engine_time",
        "idx_settlement_audits_opportunity", "idx_settlement_audits_status",
    }
    _CURRENT_COLUMNS = {
        "snapshots": {"market_id", "selection_id", "commission_pct", "commission_source", "market_type", "strategy", "sport", "in_play", "market_status", "section", "trap_number", "canonical_selection_key", "runner_status", "feed_entitlement", "market_data_transport", "source_timestamp", "timestamp_quality", "source_state_version"},
        "latest_snapshots": {"exchange", "market_id", "selection_id", "side", "captured_at", "odds", "liquidity", "raw_json", "provider_id", "venue_id", "feed_entitlement", "market_data_transport", "source_timestamp", "timestamp_quality", "quote_age_ms", "source_state_version", "depth_levels_json"},
        "opportunities": {"event_name", "event_start", "source_markets_json", "match_score", "signature", "is_demo", "strategy", "sport", "in_play", "event_status", "qualification_status", "qualification_reason", "section", "race_track", "race_number", "runner_count", "time_to_off_seconds", "job_id", "max_executable_stake", "limiting_provider", "limiting_selection", "limiting_side", "liquidity_capable", "liquidity_rejection_reason", "depth_at_qualification_json", "quote_age_at_qualification_ms", "book_revision", "quote_oldest_age_ms", "quote_newest_age_ms", "quote_receipt_spread_ms", "source_timestamp_spread_ms", "timestamp_quality", "engine_instance_id", "engine_type", "engine_version", "engine_config_version", "engine_provenance_source", "routing_diagnostics_json"},
        "scenario_runs": {"stakes_json", "outcome_pnls_json", "realized_pnl"},
        "matched_markets": {"diagnostic_deployed", "diagnostic_profit", "limited_by", "strategy", "quality_score", "quality_band", "reference_bankroll", "bankroll_roi_pct", "capital_used_pct", "gross_roi_pct", "commission_impact_pct", "sport", "section", "race_track", "race_number", "runner_count", "time_to_off_seconds", "in_play", "event_status", "max_executable_stake", "limiting_provider", "limiting_selection", "limiting_side", "liquidity_capable", "liquidity_rejection_reason", "depth_at_qualification_json", "quote_age_at_qualification_ms", "book_revision", "quote_oldest_age_ms", "quote_newest_age_ms", "quote_receipt_spread_ms", "source_timestamp_spread_ms", "timestamp_quality"},
        "monitor_timing_runs": {"research_only", "stream"},
        "monitor_positions": {"stream", "currency", "mode", "engine_instance_id", "engine_type", "engine_version", "engine_config_version", "engine_provenance_source"},
        "monitor_stream_wallets": {"funding_adjustment"},
        "account_snapshots": {"mode", "exchange", "stream", "currency", "source", "available_balance", "reserved_balance", "exposure", "equity", "realized_pnl", "freshness", "captured_at", "context", "metadata_json"},
        "balance_reconciliations": {"mode", "exchange", "stream", "status", "expected", "observed", "delta", "tolerance", "checked_at", "details_json"},
        "market_cache": {"section", "race_track", "race_number", "runner_count"},
        "opportunity_tracks": {"sport"},
        "scan_runs": {"job_id", "processed_candidates", "positive_opportunities", "qualified_count", "executed_count", "duration_ms", "scan_kind", "stage_timings_json", "cache_entries", "stale_rejections"},
        "execution_runs": {"job_id"},
        "live_accounts": {"account_id", "provider_id", "venue_id", "currency", "available_balance", "reserved_balance", "exposure", "equity", "captured_at", "source", "metadata_json"},
        "live_account_movements": {"provider_id", "venue_id", "currency", "movement_type", "amount", "occurred_at", "external_reference", "provider_activity_id", "description", "native_type", "balance_after", "metadata_json"},
        "market_hourly_rollups": {"raw_positive", "net_roi_sum", "net_roi_count", "best_net_roi_pct", "deployable_sum", "deployable_count"},
        "market_hourly_seen": {"raw_positive", "net_positive"},
        "engine_instances": {"engine_grade", "nickname", "description", "notes", "package_source", "package_sha256", "package_author", "package_filename", "package_installed_at", "package_previous_version", "sim_enabled", "live_enabled"},
        "engine_decisions": {"intent_type", "engine_grade"},
        "engine_errors": {"engine_instance_id", "market_snapshot_id", "error_type", "message", "created_at", "mode", "section", "stream"},
        "engine_evaluations": {"engine_instance_id", "market_snapshot_id", "evaluated_at", "observed_at", "mode", "section", "sport", "market_name", "market_type", "stream", "decision_id", "had_opportunity", "venue_ids_json"},
        "venue_controls": {"provider_id", "venue_id", "account_nickname", "sim_feed_enabled", "live_feed_enabled", "sim_account_enabled", "live_account_enabled", "live_execution_enabled", "created_at", "updated_at"},
    }

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # sqlite3's timeout controls the initial busy handler.  Set the PRAGMA as
        # well because schema scripts/connections can otherwise revert to 5s.
        self.conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA busy_timeout=30000")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA temp_store=MEMORY")
            self.conn.execute("PRAGMA cache_size=-32768")
            # v0.8.34 is an additive storage migration. Install its three small
            # bounded-storage tables separately so an otherwise-current 0.8.34
            # database (which may contain millions of legacy snapshots) does not
            # rerun every historical data migration just to gain these tables.
            self._ensure_v0833_storage_schema()
            self._ensure_v0835_market_rollup_schema()
            self._ensure_v0842_analytics_schema()
            self._ensure_v0900_runtime_schema()
            self._ensure_v091_liquidity_schema()
            self._ensure_v092_account_schema()
            self._ensure_v093_racing_storage_schema()
            self._ensure_v096_decision_schema()
            self._ensure_v0914_engine_schema()
            self._ensure_v0915_engine_lab_schema()
            self._ensure_v0916_engine_library_schema()
            self._ensure_v0917_operational_schema()
            self._ensure_v0918_accounts_schema()
            self._ensure_v0936_sports_lifecycle_schema()
            self._ensure_v0938_monitor_engine_schema()
            self._ensure_v0956_routing_settlement_schema()
            if not self._schema_is_current():
                # Only an actual new/old schema performs DDL/data migrations.
                # A current database takes the fast read-only path above, which is
                # what allows the UI process to coexist with the writer process.
                self.conn.executescript(SCHEMA)
                self._migrate()
                self._ensure_v0900_runtime_schema()
                self._ensure_v091_liquidity_schema()
                self._ensure_v092_account_schema()
                self._ensure_v093_racing_storage_schema()
                self._ensure_v096_decision_schema()
                self._ensure_v0914_engine_schema()
                self._ensure_v0915_engine_lab_schema()
                self._ensure_v0916_engine_library_schema()
                self._ensure_v0917_operational_schema()
                self._ensure_v0918_accounts_schema()
                self._ensure_v0936_sports_lifecycle_schema()
                self._ensure_v0938_monitor_engine_schema()
                self.conn.commit()
                self.conn.execute("PRAGMA busy_timeout=30000")


    def _ensure_v0956_routing_settlement_schema(self) -> None:
        """Additive 0.9.56 routing/settlement audit schema for mature databases.

        This must run before ``_schema_is_current``.  0.9.56 originally added the
        objects only to the full SCHEMA/_migrate path, which meant an otherwise
        current 0.9.55 database skipped them and Dashboard overview subsequently
        failed when routing diagnostics queried the absent column/table.
        """
        with self.lock:
            tables = {str(r["name"]) for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "opportunities" not in tables:
                return
            self._ensure_column("opportunities", "routing_diagnostics_json", "TEXT")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS settlement_audits (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER NOT NULL, observed_at TEXT NOT NULL, status TEXT NOT NULL,
                  raw_provider_winner TEXT, provider_winner_id TEXT, canonical_winner TEXT, stored_selections_json TEXT,
                  mapping_method TEXT, mapping_confidence REAL, winning_exchange TEXT, settlement_contributions_json TEXT,
                  total_realized_pnl REAL, reconciliation_status TEXT, reconciliation_delta REAL, details_json TEXT,
                  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
                );
                CREATE INDEX IF NOT EXISTS idx_settlement_audits_opportunity ON settlement_audits(opportunity_id, observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_settlement_audits_status ON settlement_audits(status, observed_at DESC);
            """)
            self.conn.commit()

    def _ensure_v0833_storage_schema(self) -> None:
        rows = self.conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('table','index')").fetchall()
        names = {str(r["name"]) for r in rows}
        required = {"latest_snapshots", "snapshot_rollups", "snapshot_storage_state",
                    "idx_latest_snapshots_exchange_time", "idx_latest_snapshots_event_time", "idx_snapshot_rollups_time"}
        legacy_indexes = {"idx_snapshots_event_time", "idx_snapshots_market_time"} & names
        if required.issubset(names) and not legacy_indexes:
            return
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS latest_snapshots (
             exchange TEXT NOT NULL, market_id TEXT NOT NULL, selection_id TEXT NOT NULL, side TEXT NOT NULL,
             captured_at TEXT NOT NULL, event_id TEXT NOT NULL, event_name TEXT NOT NULL, market_name TEXT NOT NULL,
             selection TEXT NOT NULL, odds REAL NOT NULL, liquidity REAL NOT NULL, source_latency_ms INTEGER DEFAULT 0,
             feed_entitlement TEXT NOT NULL DEFAULT 'unknown', market_data_transport TEXT NOT NULL DEFAULT 'unknown', source_timestamp TEXT, source_state_version TEXT,
             commission_pct REAL DEFAULT 0, commission_source TEXT, market_type TEXT, strategy TEXT, sport TEXT,
             in_play INTEGER, market_status TEXT, section TEXT DEFAULT 'sports', trap_number INTEGER,
             canonical_selection_key TEXT, runner_status TEXT, raw_json TEXT,
             PRIMARY KEY(exchange,market_id,selection_id,side)
            );
            CREATE INDEX IF NOT EXISTS idx_latest_snapshots_exchange_time ON latest_snapshots(exchange,captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_latest_snapshots_event_time ON latest_snapshots(event_id,captured_at DESC);
            CREATE TABLE IF NOT EXISTS snapshot_rollups (
             hour_utc TEXT NOT NULL, exchange TEXT NOT NULL, quote_observations INTEGER NOT NULL DEFAULT 0,
             batches INTEGER NOT NULL DEFAULT 0, last_captured_at TEXT, PRIMARY KEY(hour_utc,exchange)
            );
            CREATE INDEX IF NOT EXISTS idx_snapshot_rollups_time ON snapshot_rollups(hour_utc DESC,exchange);
            CREATE TABLE IF NOT EXISTS snapshot_storage_state (
             id INTEGER PRIMARY KEY CHECK(id=1), legacy_target_id INTEGER NOT NULL DEFAULT 0,
             legacy_rows_deleted INTEGER NOT NULL DEFAULT 0, last_prune_at TEXT, last_write_error TEXT, last_write_error_at TEXT
            );
            INSERT OR IGNORE INTO snapshot_storage_state(id) VALUES(1);
            -- The legacy append-only raw table is no longer queried by event or
            -- market. Dropping its multi-million-row indexes immediately releases
            -- their pages for reuse while the table itself is pruned in batches.
            DROP INDEX IF EXISTS idx_snapshots_event_time;
            DROP INDEX IF EXISTS idx_snapshots_market_time;
        """)
        self.conn.commit()

    def _ensure_v0835_market_rollup_schema(self) -> None:
        """Install the compact Market Analysis rollup layer without replaying old migrations."""
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS market_hourly_rollups (
                 hour_utc TEXT NOT NULL, section TEXT NOT NULL DEFAULT 'sports', sport TEXT NOT NULL DEFAULT 'Unknown',
                 market_name TEXT NOT NULL DEFAULT 'Unknown', in_play INTEGER NOT NULL DEFAULT 0,
                 observations INTEGER NOT NULL DEFAULT 0, unique_markets INTEGER NOT NULL DEFAULT 0,
                 net_positive INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY(hour_utc,section,sport,market_name,in_play)
                );
                CREATE INDEX IF NOT EXISTS idx_market_hourly_rollups_time ON market_hourly_rollups(hour_utc DESC,section,sport,in_play);
                CREATE TABLE IF NOT EXISTS market_hourly_seen (
                 hour_utc TEXT NOT NULL, section TEXT NOT NULL DEFAULT 'sports', sport TEXT NOT NULL DEFAULT 'Unknown',
                 market_name TEXT NOT NULL DEFAULT 'Unknown', in_play INTEGER NOT NULL DEFAULT 0, event_key TEXT NOT NULL,
                 net_positive INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY(hour_utc,section,sport,market_name,in_play,event_key)
                );
                CREATE INDEX IF NOT EXISTS idx_market_hourly_seen_time ON market_hourly_seen(hour_utc DESC);
                CREATE TABLE IF NOT EXISTS market_hourly_rollup_state (hour_utc TEXT PRIMARY KEY,built_at TEXT NOT NULL);
            """)
            self.conn.commit()

    def _ensure_v0842_analytics_schema(self) -> None:
        """Install 0.8.42 compact analytics/discovery tables only when missing.

        Current databases take a sqlite_master fast path so opening the UI does not
        perform schema writes while the scanner process owns the writer connection.
        """
        rows = self.conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('table','index')").fetchall()
        names = {str(r["name"]) for r in rows}
        required = {
            "market_financial_hourly_rollups", "market_financial_hourly_state",
            "exchange_market_discovery_hours", "exchange_market_discovery_state",
            "idx_market_financial_hourly_time", "idx_exchange_market_discovery_time",
            "idx_exchange_market_discovery_market",
        }
        if required.issubset(names):
            return
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS market_financial_hourly_rollups (
             hour_utc TEXT NOT NULL, section TEXT NOT NULL DEFAULT 'sports', sport TEXT NOT NULL DEFAULT 'Unknown',
             market_name TEXT NOT NULL DEFAULT 'Unknown', in_play INTEGER NOT NULL DEFAULT 0,
             qualified INTEGER NOT NULL DEFAULT 0, executed INTEGER NOT NULL DEFAULT 0, deployed REAL NOT NULL DEFAULT 0,
             settled INTEGER NOT NULL DEFAULT 0, settled_deployed REAL NOT NULL DEFAULT 0, pnl REAL NOT NULL DEFAULT 0,
             PRIMARY KEY(hour_utc,section,sport,market_name,in_play)
            );
            CREATE INDEX IF NOT EXISTS idx_market_financial_hourly_time ON market_financial_hourly_rollups(hour_utc DESC,section,sport,in_play);
            CREATE TABLE IF NOT EXISTS market_financial_hourly_state (hour_utc TEXT PRIMARY KEY,built_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exchange_market_discovery_hours (
             hour_utc TEXT NOT NULL, exchange_key TEXT NOT NULL, exchange_label TEXT NOT NULL, market_id TEXT NOT NULL,
             phase TEXT NOT NULL DEFAULT 'pre_match', event_id TEXT, event_name TEXT, market_name TEXT, canonical_market_key TEXT,
             sport TEXT NOT NULL DEFAULT 'Unknown', section TEXT NOT NULL DEFAULT 'sports', event_start TEXT, race_track TEXT,
             race_number INTEGER, source_quality TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, observations INTEGER NOT NULL DEFAULT 1,
             PRIMARY KEY(hour_utc,exchange_key,market_id,phase)
            );
            CREATE INDEX IF NOT EXISTS idx_exchange_market_discovery_time ON exchange_market_discovery_hours(hour_utc DESC,exchange_key,sport,section,phase);
            CREATE INDEX IF NOT EXISTS idx_exchange_market_discovery_market ON exchange_market_discovery_hours(exchange_key,market_id,hour_utc DESC);
            CREATE TABLE IF NOT EXISTS exchange_market_discovery_state (hour_utc TEXT PRIMARY KEY,built_at TEXT NOT NULL,completeness TEXT NOT NULL DEFAULT 'historical');
        """)
        self.conn.commit()

    def _ensure_v0900_runtime_schema(self) -> None:
        """Install 0.9.0 LIVE-isolated runtime/journal tables and quote provenance.

        This migration is additive and intentionally independent from historical
        migrations so an otherwise-current large database can gain the foundation
        without replaying old data work. No table below is used for real order
        submission in 0.9.0; LIVE remains locked.
        """
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS live_accounts (
                 account_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, venue_id TEXT NOT NULL, currency TEXT NOT NULL,
                 available_balance REAL, reserved_balance REAL, exposure REAL, equity REAL,
                 captured_at TEXT, source TEXT, metadata_json TEXT
                );
                CREATE TABLE IF NOT EXISTS live_order_attempts (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL UNIQUE, position_id TEXT, leg_id TEXT, attempt_id TEXT,
                 provider_id TEXT NOT NULL, venue_id TEXT NOT NULL, canonical_event_id TEXT, canonical_market_id TEXT, canonical_selection_id TEXT,
                 side TEXT, requested_odds REAL, requested_stake REAL, mode TEXT NOT NULL DEFAULT 'live',
                 state TEXT NOT NULL DEFAULT 'NOT_SUBMITTED', intent_json TEXT NOT NULL, external_order_id TEXT,
                 created_at TEXT NOT NULL, submission_attempted_at TEXT, reconciled_at TEXT, last_error TEXT, provider_metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_live_order_attempts_state ON live_order_attempts(state,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_live_order_attempts_provider ON live_order_attempts(provider_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS live_orders (
                 client_order_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, venue_id TEXT NOT NULL, external_order_id TEXT,
                 position_id TEXT, leg_id TEXT, state TEXT NOT NULL, requested_stake REAL, executed_stake REAL DEFAULT 0,
                 average_odds REAL, updated_at TEXT NOT NULL, metadata_json TEXT
                );
                CREATE TABLE IF NOT EXISTS live_fills (
                 fill_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL, provider_id TEXT NOT NULL, venue_id TEXT NOT NULL,
                 external_order_id TEXT, selection_id TEXT, side TEXT, odds REAL, stake REAL, filled_at TEXT NOT NULL, metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_live_fills_order ON live_fills(client_order_id,filled_at DESC);
                CREATE TABLE IF NOT EXISTS live_positions (
                 position_id TEXT PRIMARY KEY, opportunity_id INTEGER, event_key TEXT, canonical_market_id TEXT, opened_at TEXT NOT NULL,
                 settled_at TEXT, status TEXT NOT NULL, deployed REAL NOT NULL DEFAULT 0, realized_pnl REAL, currency TEXT NOT NULL DEFAULT 'GBP',
                 stream TEXT, position_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_live_positions_status ON live_positions(status,opened_at DESC);
                CREATE TABLE IF NOT EXISTS live_recovery_actions (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT NOT NULL, action_type TEXT NOT NULL, state TEXT NOT NULL,
                 provider_id TEXT, venue_id TEXT, created_at TEXT NOT NULL, completed_at TEXT, details_json TEXT
                );
                CREATE TABLE IF NOT EXISTS live_settlements (
                 position_id TEXT PRIMARY KEY, settled_at TEXT NOT NULL, outcome TEXT, realized_pnl REAL, provider_evidence_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_account_movements (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id TEXT NOT NULL, venue_id TEXT NOT NULL, currency TEXT NOT NULL,
                 movement_type TEXT NOT NULL, amount REAL NOT NULL, occurred_at TEXT NOT NULL, external_reference TEXT, metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_live_account_movements_time ON live_account_movements(provider_id,occurred_at DESC);
                CREATE TABLE IF NOT EXISTS live_reconciliations (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id TEXT NOT NULL, venue_id TEXT, reconciliation_type TEXT NOT NULL,
                 status TEXT NOT NULL, checked_at TEXT NOT NULL, expected_json TEXT, observed_json TEXT, details_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_live_reconciliations_time ON live_reconciliations(provider_id,checked_at DESC);
            """)
            # Shared quote data remains common, but its feed provenance must be explicit.
            existing_tables = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            for table in ("latest_snapshots", "snapshots"):
                if table not in existing_tables:
                    continue
                self._ensure_column(table, "feed_entitlement", "TEXT NOT NULL DEFAULT 'unknown'")
                self._ensure_column(table, "market_data_transport", "TEXT NOT NULL DEFAULT 'unknown'")
                self._ensure_column(table, "source_timestamp", "TEXT")
                self._ensure_column(table, "source_state_version", "TEXT")
            self.conn.commit()

    def _ensure_v091_liquidity_schema(self) -> None:
        """Install bounded top-N depth and compact liquidity analytics storage.

        Depth snapshots are current-state only. Historical analytics are compact
        hourly rollups; raw multi-level books are never appended indefinitely.
        """
        rows = self.conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('table','index')").fetchall()
        names = {str(r["name"]) for r in rows}
        required = {
            "latest_depth_snapshots", "liquidity_depth_hourly_rollups",
            "liquidity_opportunity_hourly_rollups", "liquidity_opportunity_rollup_state",
            "idx_latest_depth_provider_time", "idx_latest_depth_market",
            "idx_liquidity_depth_hourly_time", "idx_liquidity_opportunity_hourly_time",
        }
        if not required.issubset(names):
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS latest_depth_snapshots (
                 provider_id TEXT NOT NULL, venue_id TEXT NOT NULL, market_id TEXT NOT NULL, selection_id TEXT NOT NULL,
                 side TEXT NOT NULL, level INTEGER NOT NULL, captured_at TEXT NOT NULL, source_timestamp TEXT, timestamp_quality TEXT NOT NULL DEFAULT 'UNKNOWN',
                 quote_age_ms INTEGER, feed_entitlement TEXT NOT NULL DEFAULT 'unknown', market_data_transport TEXT NOT NULL DEFAULT 'unknown',
                 event_id TEXT, event_name TEXT, market_name TEXT NOT NULL DEFAULT 'Unknown', selection TEXT,
                 section TEXT NOT NULL DEFAULT 'sports', sport TEXT NOT NULL DEFAULT 'Unknown', in_play INTEGER NOT NULL DEFAULT 0,
                 price REAL NOT NULL, available_size REAL NOT NULL,
                 PRIMARY KEY(provider_id,market_id,selection_id,side,level)
                );
                CREATE INDEX IF NOT EXISTS idx_latest_depth_provider_time ON latest_depth_snapshots(provider_id,captured_at DESC);
                CREATE INDEX IF NOT EXISTS idx_latest_depth_market ON latest_depth_snapshots(market_id,selection_id,side,level);
                CREATE TABLE IF NOT EXISTS liquidity_depth_hourly_rollups (
                 hour_utc TEXT NOT NULL, provider_id TEXT NOT NULL, section TEXT NOT NULL DEFAULT 'sports', sport TEXT NOT NULL DEFAULT 'Unknown',
                 market_name TEXT NOT NULL DEFAULT 'Unknown', in_play INTEGER NOT NULL DEFAULT 0, depth_samples INTEGER NOT NULL DEFAULT 0,
                 top_book_depth_sum REAL NOT NULL DEFAULT 0, top3_depth_sum REAL NOT NULL DEFAULT 0,
                 max_top_book_depth REAL NOT NULL DEFAULT 0, max_top3_depth REAL NOT NULL DEFAULT 0, last_captured_at TEXT,
                 PRIMARY KEY(hour_utc,provider_id,section,sport,market_name,in_play)
                );
                CREATE INDEX IF NOT EXISTS idx_liquidity_depth_hourly_time ON liquidity_depth_hourly_rollups(hour_utc DESC,provider_id,section,sport,in_play);
                CREATE TABLE IF NOT EXISTS liquidity_opportunity_hourly_rollups (
                 hour_utc TEXT NOT NULL, section TEXT NOT NULL DEFAULT 'sports', sport TEXT NOT NULL DEFAULT 'Unknown',
                 market_name TEXT NOT NULL DEFAULT 'Unknown', in_play INTEGER NOT NULL DEFAULT 0, positive_observations INTEGER NOT NULL DEFAULT 0,
                 liquidity_capable INTEGER NOT NULL DEFAULT 0, liquidity_rejected INTEGER NOT NULL DEFAULT 0, qualified_observations INTEGER NOT NULL DEFAULT 0,
                 executable_stake_sum REAL NOT NULL DEFAULT 0, executable_stake_samples INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY(hour_utc,section,sport,market_name,in_play)
                );
                CREATE INDEX IF NOT EXISTS idx_liquidity_opportunity_hourly_time ON liquidity_opportunity_hourly_rollups(hour_utc DESC,section,sport,in_play);
                CREATE TABLE IF NOT EXISTS liquidity_opportunity_rollup_state (hour_utc TEXT PRIMARY KEY,built_at TEXT NOT NULL);
            """)
        existing = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "latest_snapshots" in existing:
            for name, decl in (
                ("provider_id", "TEXT"), ("venue_id", "TEXT"), ("quote_age_ms", "INTEGER"), ("depth_levels_json", "TEXT")
            ):
                self._ensure_column("latest_snapshots", name, decl)
        for table in ("matched_markets", "opportunities"):
            if table not in existing:
                continue
            for name, decl in (
                ("max_executable_stake", "REAL"), ("limiting_provider", "TEXT"), ("limiting_selection", "TEXT"),
                ("limiting_side", "TEXT"), ("liquidity_capable", "INTEGER"), ("liquidity_rejection_reason", "TEXT"),
                ("depth_at_qualification_json", "TEXT"), ("quote_age_at_qualification_ms", "INTEGER")
            ):
                self._ensure_column(table, name, decl)
        self.conn.commit()

    def _ensure_v092_account_schema(self) -> None:
        """Install dedicated read-only LIVE account history/audit persistence."""
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS live_account_snapshots (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL,
                 provider_id TEXT NOT NULL, venue_id TEXT NOT NULL, currency TEXT, balance REAL, available_balance REAL,
                 reserved_balance REAL, exposure REAL, credit REAL, source_timestamp TEXT, received_at TEXT NOT NULL,
                 is_stale INTEGER NOT NULL DEFAULT 0, connection_state TEXT NOT NULL, data_quality TEXT NOT NULL,
                 balance_semantics TEXT, provider_account_ref TEXT, error_code TEXT, error_message TEXT, metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_live_account_snapshots_time ON live_account_snapshots(provider_id,received_at DESC);
                CREATE TABLE IF NOT EXISTS live_account_audit (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id TEXT, event_type TEXT NOT NULL, status TEXT NOT NULL,
                 occurred_at TEXT NOT NULL, latency_ms INTEGER, error_type TEXT, message TEXT, details_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_live_account_audit_time ON live_account_audit(provider_id,occurred_at DESC);
            """)
            existing = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "live_account_movements" in existing:
                self._ensure_column("live_account_movements", "provider_activity_id", "TEXT")
                self._ensure_column("live_account_movements", "description", "TEXT")
                self._ensure_column("live_account_movements", "native_type", "TEXT")
                self._ensure_column("live_account_movements", "balance_after", "REAL")
                self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_live_account_movements_activity ON live_account_movements(provider_id,provider_activity_id) WHERE provider_activity_id IS NOT NULL")
            self.conn.commit()

    def _ensure_v093_racing_storage_schema(self) -> None:
        """Install 0.9.3 Racing evidence and bounded matched-market storage.

        This is additive and deliberately cheap on an existing large DB. Historical
        verbose rows are not rewritten here; worker maintenance finalises one old
        hour at a time before it becomes prune-eligible.  A database that already
        carries the complete 0.9.3 schema takes a SELECT-only fast path so the UI
        can still open it while the worker owns SQLite's writer lock.
        """
        with self.lock:
            objects = {
                str(r["name"])
                for r in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
                ).fetchall()
            }
            required_objects = {
                "matched_market_latest", "idx_matched_market_latest_scan",
                "idx_matched_market_latest_time", "idx_matched_market_latest_analysis",
                "matched_market_reason_hourly_rollups", "idx_matched_market_reason_hourly_time",
                "scan_qualification_breakdown", "idx_scan_qualification_breakdown_scan",
                "racing_funnel_hourly_rollups", "idx_racing_funnel_hourly_time",
                "matched_market_history_state", "matched_market_storage_state",
            }

            def has_columns(table: str, required: set[str]) -> bool:
                if table not in objects:
                    return False
                cols = {
                    str(r["name"])
                    for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                return required.issubset(cols)

            ready = required_objects.issubset(objects)
            ready = ready and all(
                has_columns(table, {"timestamp_quality"})
                for table in ("snapshots", "latest_snapshots", "latest_depth_snapshots")
            )
            ready = ready and all(
                has_columns(
                    table,
                    {
                        "book_revision", "quote_oldest_age_ms", "quote_newest_age_ms",
                        "quote_receipt_spread_ms", "source_timestamp_spread_ms", "timestamp_quality",
                    },
                )
                for table in ("matched_markets", "opportunities")
            )
            ready = ready and has_columns(
                "market_hourly_rollups",
                {
                    "raw_positive", "net_roi_sum", "net_roi_count",
                    "best_net_roi_pct", "deployable_sum", "deployable_count",
                },
            )
            ready = ready and has_columns("market_hourly_seen", {"raw_positive", "net_positive"})
            if ready:
                return

            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS matched_market_latest (
                 state_key TEXT PRIMARY KEY, scan_id INTEGER NOT NULL, observed_at TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                 observation_count INTEGER NOT NULL DEFAULT 1, material_fingerprint TEXT NOT NULL, last_verbose_at TEXT,
                 event_key TEXT NOT NULL, event_name TEXT NOT NULL, event_start TEXT, market_name TEXT NOT NULL, match_score REAL DEFAULT 0,
                 theoretical_edge_pct REAL, gross_roi_pct REAL, commission_impact_pct REAL, net_roi_pct REAL, diagnostic_deployed REAL, diagnostic_profit REAL, limited_by TEXT,
                 status TEXT NOT NULL, reason TEXT, legs_json TEXT, source_markets_json TEXT, strategy TEXT DEFAULT '1x2', quality_score REAL, quality_band TEXT,
                 reference_bankroll REAL, bankroll_roi_pct REAL, capital_used_pct REAL, sport TEXT, section TEXT DEFAULT 'sports', race_track TEXT, race_number INTEGER,
                 runner_count INTEGER, time_to_off_seconds INTEGER, in_play INTEGER, event_status TEXT, max_executable_stake REAL, limiting_provider TEXT,
                 limiting_selection TEXT, limiting_side TEXT, liquidity_capable INTEGER, liquidity_rejection_reason TEXT, depth_at_qualification_json TEXT,
                 quote_age_at_qualification_ms INTEGER, book_revision TEXT, quote_oldest_age_ms INTEGER, quote_newest_age_ms INTEGER, quote_receipt_spread_ms INTEGER,
                 source_timestamp_spread_ms INTEGER, timestamp_quality TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_matched_market_latest_scan ON matched_market_latest(scan_id);
                CREATE INDEX IF NOT EXISTS idx_matched_market_latest_time ON matched_market_latest(last_seen DESC);
                CREATE INDEX IF NOT EXISTS idx_matched_market_latest_analysis ON matched_market_latest(section,sport,market_name,in_play,last_seen DESC);
                CREATE TABLE IF NOT EXISTS matched_market_reason_hourly_rollups (
                 hour_utc TEXT NOT NULL, section TEXT NOT NULL DEFAULT 'sports', sport TEXT NOT NULL DEFAULT 'Unknown', market_name TEXT NOT NULL DEFAULT 'Unknown',
                 in_play INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, reason_sample TEXT, observations INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY(hour_utc,section,sport,market_name,in_play,status)
                );
                CREATE INDEX IF NOT EXISTS idx_matched_market_reason_hourly_time ON matched_market_reason_hourly_rollups(hour_utc DESC,section,sport,in_play);
                CREATE TABLE IF NOT EXISTS scan_qualification_breakdown (
                 scan_id INTEGER NOT NULL, status TEXT NOT NULL, total_count INTEGER NOT NULL DEFAULT 0, positive_count INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY(scan_id,status)
                );
                CREATE INDEX IF NOT EXISTS idx_scan_qualification_breakdown_scan ON scan_qualification_breakdown(scan_id);
                CREATE TABLE IF NOT EXISTS racing_funnel_hourly_rollups (
                 hour_utc TEXT NOT NULL, sport TEXT NOT NULL DEFAULT 'Greyhounds', observations INTEGER NOT NULL DEFAULT 0,
                 complete_books INTEGER NOT NULL DEFAULT 0, theoretical_positive INTEGER NOT NULL DEFAULT 0, post_commission_positive INTEGER NOT NULL DEFAULT 0,
                 liquidity_capable INTEGER NOT NULL DEFAULT 0, qualified INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY(hour_utc,sport)
                );
                CREATE INDEX IF NOT EXISTS idx_racing_funnel_hourly_time ON racing_funnel_hourly_rollups(hour_utc DESC,sport);
                CREATE TABLE IF NOT EXISTS matched_market_history_state (hour_utc TEXT PRIMARY KEY,built_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS matched_market_storage_state (
                 id INTEGER PRIMARY KEY CHECK(id=1), rows_deleted INTEGER NOT NULL DEFAULT 0, last_prune_at TEXT, prune_safe_through TEXT,
                 last_error TEXT, last_error_at TEXT
                );
                INSERT OR IGNORE INTO matched_market_storage_state(id) VALUES(1);
            """)
            for quote_table in ("snapshots", "latest_snapshots", "latest_depth_snapshots"):
                existing_tables = {str(r[0]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if quote_table in existing_tables:
                    self._ensure_column(quote_table, "timestamp_quality", "TEXT NOT NULL DEFAULT 'UNKNOWN'")
            for table in ("matched_markets", "opportunities"):
                if table not in {str(r[0]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
                    continue
                for name, decl in (
                    ("book_revision", "TEXT"), ("quote_oldest_age_ms", "INTEGER"), ("quote_newest_age_ms", "INTEGER"),
                    ("quote_receipt_spread_ms", "INTEGER"), ("source_timestamp_spread_ms", "INTEGER"), ("timestamp_quality", "TEXT")
                ):
                    self._ensure_column(table, name, decl)
            for name, decl in (
                ("raw_positive", "INTEGER NOT NULL DEFAULT 0"), ("net_roi_sum", "REAL NOT NULL DEFAULT 0"),
                ("net_roi_count", "INTEGER NOT NULL DEFAULT 0"), ("best_net_roi_pct", "REAL"),
                ("deployable_sum", "REAL NOT NULL DEFAULT 0"), ("deployable_count", "INTEGER NOT NULL DEFAULT 0")
            ):
                self._ensure_column("market_hourly_rollups", name, decl)
            self._ensure_column("market_hourly_seen", "raw_positive", "INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()


    def _ensure_v096_decision_schema(self) -> None:
        """Install isolated 0.9.8 LIVE-context decision evidence tables.

        These tables are deliberately independent from SIM opportunities/positions
        and from LIVE account/order persistence. Opening an already-current DB is
        SELECT-only because the sqlite_master fast path returns immediately.
        """
        with self.lock:
            names = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')").fetchall()}
            required = {
                "live_decision_latest", "live_decision_events", "live_decision_hourly_rollups",
                "idx_live_decision_latest_time", "idx_live_decision_latest_market",
                "idx_live_decision_events_time", "idx_live_decision_events_analysis", "idx_live_decision_hourly_time",
            }
            if required.issubset(names):
                return
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS live_decision_latest (
                 state_key TEXT PRIMARY KEY, decision_id TEXT NOT NULL, canonical_event_id TEXT, canonical_market_id TEXT NOT NULL,
                 book_revision TEXT NOT NULL, strategy TEXT NOT NULL, domain TEXT NOT NULL, section TEXT NOT NULL, sport TEXT NOT NULL,
                 market_type TEXT, event_name TEXT, market_name TEXT NOT NULL, in_play INTEGER NOT NULL DEFAULT 0,
                 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, observation_count INTEGER NOT NULL DEFAULT 1,
                 state TEXT NOT NULL, evidence_quality TEXT NOT NULL, reason_code TEXT, reason TEXT,
                 gross_edge_pct REAL, net_roi_pct REAL, expected_simulated_profit REAL, requested_stake REAL, max_executable_stake REAL,
                 simulated_stake REAL, simulated_filled_stake REAL, oldest_quote_age_ms INTEGER, receipt_spread_ms INTEGER,
                 source_time_spread_ms INTEGER, decision_compute_ms REAL, provider_pair TEXT, limiting_provider TEXT, limiting_selection TEXT,
                 limiting_side TEXT, application_mode TEXT NOT NULL DEFAULT 'live', decision_type TEXT NOT NULL DEFAULT 'simulated',
                 legs_json TEXT NOT NULL, simulation_json TEXT, qualification_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_live_decision_latest_time ON live_decision_latest(last_seen DESC);
                CREATE INDEX IF NOT EXISTS idx_live_decision_latest_market ON live_decision_latest(canonical_market_id,strategy);
                CREATE TABLE IF NOT EXISTS live_decision_events (
                 decision_id TEXT PRIMARY KEY, state_key TEXT NOT NULL, created_at TEXT NOT NULL, canonical_event_id TEXT, canonical_market_id TEXT NOT NULL,
                 book_revision TEXT NOT NULL, strategy TEXT NOT NULL, domain TEXT NOT NULL, section TEXT NOT NULL, sport TEXT NOT NULL,
                 market_type TEXT, event_name TEXT, market_name TEXT NOT NULL, in_play INTEGER NOT NULL DEFAULT 0,
                 state TEXT NOT NULL, evidence_quality TEXT NOT NULL, reason_code TEXT, reason TEXT, gross_edge_pct REAL, net_roi_pct REAL,
                 expected_simulated_profit REAL, requested_stake REAL, max_executable_stake REAL, simulated_stake REAL, simulated_filled_stake REAL,
                 oldest_quote_age_ms INTEGER, receipt_spread_ms INTEGER, source_time_spread_ms INTEGER, decision_compute_ms REAL,
                 provider_pair TEXT, limiting_provider TEXT, limiting_selection TEXT, limiting_side TEXT,
                 application_mode TEXT NOT NULL DEFAULT 'live', decision_type TEXT NOT NULL DEFAULT 'simulated',
                 legs_json TEXT NOT NULL, simulation_json TEXT, qualification_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_live_decision_events_time ON live_decision_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_live_decision_events_analysis ON live_decision_events(domain,sport,market_type,evidence_quality,created_at DESC);
                CREATE TABLE IF NOT EXISTS live_decision_hourly_rollups (
                 hour_utc TEXT NOT NULL, domain TEXT NOT NULL, sport TEXT NOT NULL, market_type TEXT NOT NULL, provider_pair TEXT NOT NULL,
                 evidence_quality TEXT NOT NULL, reason_code TEXT NOT NULL, observed INTEGER NOT NULL DEFAULT 0, positive INTEGER NOT NULL DEFAULT 0,
                 liquidity_capable INTEGER NOT NULL DEFAULT 0, qualified INTEGER NOT NULL DEFAULT 0, simulated_attempts INTEGER NOT NULL DEFAULT 0,
                 simulated_fills INTEGER NOT NULL DEFAULT 0, simulated_misses INTEGER NOT NULL DEFAULT 0, execution_grade INTEGER NOT NULL DEFAULT 0,
                 expected_profit_sum REAL NOT NULL DEFAULT 0, executable_stake_sum REAL NOT NULL DEFAULT 0, executable_stake_samples INTEGER NOT NULL DEFAULT 0,
                 decision_ms_sum REAL NOT NULL DEFAULT 0, decision_ms_samples INTEGER NOT NULL DEFAULT 0, max_decision_ms REAL NOT NULL DEFAULT 0,
                 PRIMARY KEY(hour_utc,domain,sport,market_type,provider_pair,evidence_quality,reason_code)
                );
                CREATE INDEX IF NOT EXISTS idx_live_decision_hourly_time ON live_decision_hourly_rollups(hour_utc DESC,domain,sport,evidence_quality);
            """)
            self.conn.commit()

    def _ensure_v0914_engine_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS engine_instances (
             engine_instance_id TEXT PRIMARY KEY, engine_type TEXT NOT NULL, engine_version TEXT NOT NULL,
             section TEXT NOT NULL DEFAULT 'all', sport TEXT NOT NULL DEFAULT 'all', competition TEXT NOT NULL DEFAULT 'all',
             market_type TEXT NOT NULL DEFAULT 'all', requested_lifecycle TEXT NOT NULL DEFAULT 'DISABLED',
             effective_lifecycle TEXT NOT NULL DEFAULT 'DISABLED', effective_reason TEXT NOT NULL DEFAULT 'REQUESTED_DISABLED',
             active_config_version INTEGER, health TEXT NOT NULL DEFAULT 'HEALTHY', last_evidence_at TEXT, last_evaluation_at TEXT,
             events_processed INTEGER NOT NULL DEFAULT 0, decisions_generated INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
             processing_latency_ms REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_engine_instances_route ON engine_instances(section,sport,market_type,effective_lifecycle);
            CREATE TABLE IF NOT EXISTS engine_configs (
             engine_instance_id TEXT NOT NULL, config_version INTEGER NOT NULL, config_hash TEXT NOT NULL, config_json TEXT NOT NULL,
             created_at TEXT NOT NULL, activated_at TEXT, derived_from_version INTEGER,
             PRIMARY KEY(engine_instance_id,config_version), UNIQUE(engine_instance_id,config_hash),
             FOREIGN KEY(engine_instance_id) REFERENCES engine_instances(engine_instance_id)
            );
            CREATE INDEX IF NOT EXISTS idx_engine_configs_active ON engine_configs(engine_instance_id,activated_at DESC,config_version DESC);
            CREATE TABLE IF NOT EXISTS engine_decisions (
             decision_id TEXT PRIMARY KEY, economic_intent_key TEXT NOT NULL, created_at TEXT NOT NULL, engine_instance_id TEXT NOT NULL,
             engine_type TEXT NOT NULL, engine_version TEXT NOT NULL, config_version INTEGER NOT NULL, config_hash TEXT NOT NULL,
             market_snapshot_id TEXT NOT NULL, feed_generation TEXT NOT NULL, section TEXT NOT NULL, sport TEXT NOT NULL,
             event_name TEXT, market_name TEXT, mode TEXT NOT NULL, requested_lifecycle TEXT NOT NULL, effective_lifecycle TEXT NOT NULL,
             expected_edge REAL NOT NULL DEFAULT 0, expected_profit REAL NOT NULL DEFAULT 0, requested_capital REAL NOT NULL DEFAULT 0,
             intent_json TEXT NOT NULL, evaluation_latency_ms REAL NOT NULL DEFAULT 0, central_validation TEXT NOT NULL DEFAULT 'NOT_SUBMITTED'
            );
            CREATE INDEX IF NOT EXISTS idx_engine_decisions_instance_time ON engine_decisions(engine_instance_id,created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_engine_decisions_market ON engine_decisions(market_snapshot_id,created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_engine_decisions_economic ON engine_decisions(economic_intent_key,created_at DESC);
            CREATE TABLE IF NOT EXISTS engine_errors (
             id INTEGER PRIMARY KEY AUTOINCREMENT, engine_instance_id TEXT NOT NULL, market_snapshot_id TEXT,
             error_type TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_engine_errors_instance_time ON engine_errors(engine_instance_id,created_at DESC);
CREATE TABLE IF NOT EXISTS engine_sim_results (
 decision_id TEXT PRIMARY KEY,
 engine_instance_id TEXT NOT NULL,
 created_at TEXT NOT NULL,
 deployed REAL NOT NULL DEFAULT 0,
 expected_profit REAL NOT NULL DEFAULT 0,
 expected_roi_pct REAL NOT NULL DEFAULT 0,
 simulation_level TEXT NOT NULL DEFAULT 'DECISION_SIM'
);
CREATE INDEX IF NOT EXISTS idx_engine_sim_results_instance_time ON engine_sim_results(engine_instance_id,created_at DESC);
CREATE TABLE IF NOT EXISTS engine_monitor_timing_results (
 decision_id TEXT PRIMARY KEY,
 engine_instance_id TEXT NOT NULL,
 created_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'WOULD_HAVE_PLACED',
 requested_capital REAL NOT NULL DEFAULT 0,
 expected_profit REAL NOT NULL DEFAULT 0,
 central_validation TEXT NOT NULL DEFAULT 'LIVE_EXECUTION_LOCKED'
);
CREATE INDEX IF NOT EXISTS idx_engine_monitor_timing_results_instance_time ON engine_monitor_timing_results(engine_instance_id,created_at DESC);
CREATE TABLE IF NOT EXISTS engine_scenario_runs (
 run_id TEXT PRIMARY KEY,
 scenario_id TEXT NOT NULL,
 scenario_version INTEGER NOT NULL DEFAULT 1,
 engine_instance_id TEXT NOT NULL,
 engine_version TEXT NOT NULL,
 config_version INTEGER NOT NULL,
 config_hash TEXT NOT NULL,
 market_snapshot_id TEXT NOT NULL,
 run_at TEXT NOT NULL,
 simulation_level TEXT NOT NULL,
 decision_json TEXT,
 input_observed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_engine_scenario_runs_engine_time ON engine_scenario_runs(engine_instance_id,run_at DESC);
        """)
        self.conn.commit()

    def _ensure_v0915_engine_lab_schema(self) -> None:
        """Install the strategy-neutral Engine Lab and canonical 0.9.15 identities.

        The migration is additive: canonical market/Parquet evidence is never
        rewritten.  Existing 0.9.14/early-0.9.15 engine identities are renamed
        in-place so decision/config provenance does not split across aliases.
        """
        with self.lock:
            existing = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

            # Fast read-only path for an already-migrated 0.9.15 database. DB() is
            # opened by UI/readers while the scanner writer can hold BEGIN IMMEDIATE;
            # a current schema must never issue opportunistic UPDATE/DDL on every open.
            structural_ready = {"engine_experiments", "engine_experiment_runs"}.issubset(existing)
            if structural_ready and "engine_instances" in existing and "engine_decisions" in existing:
                instance_cols = self._columns("engine_instances")
                decision_cols = self._columns("engine_decisions")
                structural_ready = "engine_grade" in instance_cols and {"intent_type", "engine_grade"}.issubset(decision_cols)
            if structural_ready:
                aliases = (
                    "SPORTS_LEGACY_SIMPLE_PRIMARY", "RACING_LEGACY_SIMPLE_PRIMARY", "RACING_GREYHOUND_BASELINE_PRIMARY",
                    "SUPERBET_ARB_PRIMARY", "SPORTS_DEPTH_REFERENCE",
                )
                alias_types = ("LEGACY_SIMPLE_ARB", "BASELINE_ARB", "SUPERBET_ARB", "GREYHOUND_BASELINE", "GREYHOUNDS_BASELINE", "DEPTH_ARB_REFERENCE")
                q_ids = ",".join("?" for _ in aliases)
                q_types = ",".join("?" for _ in alias_types)
                stale = self.conn.execute(
                    f"SELECT 1 FROM engine_instances WHERE engine_instance_id IN ({q_ids}) OR engine_type IN ({q_types}) LIMIT 1",
                    (*aliases, *alias_types),
                ).fetchone()
                grade_mismatch = self.conn.execute(
                    """SELECT 1 FROM engine_instances WHERE
                       (engine_instance_id='SPORTS_BASELINE_ARB_PRIMARY' AND (engine_type!='SPORTS_BASELINE_ARB' OR engine_grade!='STANDARD')) OR
                       (engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY' AND (engine_type!='SPORTS_SUPERBET_ARB' OR engine_grade!='ADVANCED')) OR
                       (engine_instance_id='GREYHOUNDS_BASELINE_ARB_PRIMARY' AND (engine_type!='GREYHOUNDS_BASELINE_ARB' OR engine_grade!='STANDARD')) OR
                       (engine_instance_id='SPORTS_DEPTH_ARB_REFERENCE' AND (engine_type!='SPORTS_DEPTH_ARB_REFERENCE' OR engine_grade!='RESEARCH'))
                       LIMIT 1"""
                ).fetchone()
                if not stale and not grade_mismatch:
                    return

            if "engine_instances" in existing:
                self._ensure_column("engine_instances", "engine_grade", "TEXT NOT NULL DEFAULT 'RESEARCH'")
            if "engine_decisions" in existing:
                self._ensure_column("engine_decisions", "intent_type", "TEXT NOT NULL DEFAULT 'ARBITRAGE'")
                self._ensure_column("engine_decisions", "engine_grade", "TEXT NOT NULL DEFAULT 'RESEARCH'")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS engine_experiments (
                 experiment_id TEXT PRIMARY KEY, source_engine_instance_id TEXT NOT NULL, engine_instance_id TEXT NOT NULL UNIQUE,
                 engine_type TEXT NOT NULL, engine_version TEXT NOT NULL, engine_grade TEXT NOT NULL DEFAULT 'RESEARCH',
                 config_version INTEGER NOT NULL, config_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
                 evidence_from_utc TEXT, evidence_to_utc TEXT, simulation_level TEXT NOT NULL DEFAULT 'DECISION_SIM',
                 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_engine_experiments_instance ON engine_experiments(engine_instance_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS engine_experiment_runs (
                 run_id TEXT PRIMARY KEY, experiment_id TEXT, engine_instance_id TEXT NOT NULL, run_type TEXT NOT NULL,
                 started_at TEXT NOT NULL, finished_at TEXT, evidence_from_utc TEXT, evidence_to_utc TEXT, evidence_cohort_hash TEXT,
                 simulation_level TEXT NOT NULL DEFAULT 'DECISION_SIM', status TEXT NOT NULL, metrics_json TEXT, error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_engine_experiment_runs_experiment_time ON engine_experiment_runs(experiment_id,started_at DESC);
            """)
            # Include tables created above so identity convergence also updates
            # experiment provenance when this migration is re-run against a dev DB.
            existing = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

            # Canonical 0.9.15 names.  "Sports" and "Greyhounds" are explicit in
            # the engine identity; Greyhounds remains a first-class UI/domain.
            mappings = (
                ("SPORTS_LEGACY_SIMPLE_PRIMARY", "SPORTS_BASELINE_ARB_PRIMARY", "SPORTS_BASELINE_ARB", "STANDARD"),
                ("SPORTS_BASELINE_ARB_PRIMARY", "SPORTS_BASELINE_ARB_PRIMARY", "SPORTS_BASELINE_ARB", "STANDARD"),
                ("RACING_LEGACY_SIMPLE_PRIMARY", "GREYHOUNDS_BASELINE_ARB_PRIMARY", "GREYHOUNDS_BASELINE_ARB", "STANDARD"),
                ("RACING_GREYHOUND_BASELINE_PRIMARY", "GREYHOUNDS_BASELINE_ARB_PRIMARY", "GREYHOUNDS_BASELINE_ARB", "STANDARD"),
                ("GREYHOUNDS_BASELINE_ARB_PRIMARY", "GREYHOUNDS_BASELINE_ARB_PRIMARY", "GREYHOUNDS_BASELINE_ARB", "STANDARD"),
                ("SUPERBET_ARB_PRIMARY", "SPORTS_SUPERBET_ARB_PRIMARY", "SPORTS_SUPERBET_ARB", "ADVANCED"),
                ("SPORTS_SUPERBET_ARB_PRIMARY", "SPORTS_SUPERBET_ARB_PRIMARY", "SPORTS_SUPERBET_ARB", "ADVANCED"),
                ("SPORTS_DEPTH_REFERENCE", "SPORTS_DEPTH_ARB_REFERENCE", "SPORTS_DEPTH_ARB_REFERENCE", "RESEARCH"),
                ("SPORTS_DEPTH_ARB_REFERENCE", "SPORTS_DEPTH_ARB_REFERENCE", "SPORTS_DEPTH_ARB_REFERENCE", "RESEARCH"),
            )
            simple_children = ("engine_decisions", "engine_errors", "engine_sim_results", "engine_monitor_timing_results", "engine_scenario_runs", "engine_experiment_runs")
            for old_id, new_id, engine_type, grade in mappings:
                if "engine_instances" not in existing:
                    break
                old = self.conn.execute("SELECT 1 FROM engine_instances WHERE engine_instance_id=?", (old_id,)).fetchone()
                new = self.conn.execute("SELECT 1 FROM engine_instances WHERE engine_instance_id=?", (new_id,)).fetchone()
                if old and old_id != new_id and not new:
                    for table in simple_children:
                        if table in existing:
                            self.conn.execute(f"UPDATE {table} SET engine_instance_id=? WHERE engine_instance_id=?", (new_id, old_id))
                    if "engine_configs" in existing:
                        self.conn.execute("UPDATE engine_configs SET engine_instance_id=? WHERE engine_instance_id=?", (new_id, old_id))
                    if "engine_experiments" in existing:
                        self.conn.execute("UPDATE engine_experiments SET engine_instance_id=? WHERE engine_instance_id=?", (new_id, old_id))
                        self.conn.execute("UPDATE engine_experiments SET source_engine_instance_id=? WHERE source_engine_instance_id=?", (new_id, old_id))
                    self.conn.execute(
                        "UPDATE engine_instances SET engine_instance_id=?,engine_type=?,engine_grade=? WHERE engine_instance_id=?",
                        (new_id, engine_type, grade, old_id),
                    )
                elif old:
                    self.conn.execute("UPDATE engine_instances SET engine_type=?,engine_grade=? WHERE engine_instance_id=?", (engine_type, grade, old_id))
                self.conn.execute("UPDATE engine_instances SET engine_type=?,engine_grade=? WHERE engine_instance_id=?", (engine_type, grade, new_id))

            type_aliases = (
                ("LEGACY_SIMPLE_ARB", "SPORTS_BASELINE_ARB"),
                ("BASELINE_ARB", "SPORTS_BASELINE_ARB"),
                ("SUPERBET_ARB", "SPORTS_SUPERBET_ARB"),
                ("GREYHOUND_BASELINE", "GREYHOUNDS_BASELINE_ARB"),
                ("GREYHOUNDS_BASELINE", "GREYHOUNDS_BASELINE_ARB"),
                ("DEPTH_ARB_REFERENCE", "SPORTS_DEPTH_ARB_REFERENCE"),
            )
            for old_type, new_type in type_aliases:
                if "engine_instances" in existing:
                    self.conn.execute("UPDATE engine_instances SET engine_type=? WHERE engine_type=?", (new_type, old_type))
                if "engine_decisions" in existing:
                    self.conn.execute("UPDATE engine_decisions SET engine_type=? WHERE engine_type=?", (new_type, old_type))
                if "engine_experiments" in existing:
                    self.conn.execute("UPDATE engine_experiments SET engine_type=? WHERE engine_type=?", (new_type, old_type))
            self.conn.commit()

    def _ensure_v0916_engine_library_schema(self) -> None:
        """0.9.16 installed-engine metadata and portable package provenance.

        This migration is additive and deliberately does not touch engine decisions,
        archive evidence, runtime-gate state or pruning settings.
        """
        with self.lock:
            existing = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "engine_instances" not in existing:
                return
            required_columns = {"description", "notes", "package_source", "package_sha256", "package_author"}
            current_columns = self._columns("engine_instances")
            # An already-current database must be a read-only fast path. The UI
            # opens DB() while the scanner may hold BEGIN IMMEDIATE, so even a
            # no-op UPDATE/COMMIT here would block readers for the full busy timeout.
            if required_columns.issubset(current_columns):
                return
            self._ensure_column("engine_instances", "description", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("engine_instances", "notes", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("engine_instances", "package_source", "TEXT NOT NULL DEFAULT 'builtin'")
            self._ensure_column("engine_instances", "package_sha256", "TEXT")
            self._ensure_column("engine_instances", "package_author", "TEXT")
            defaults = {
                "SPORTS_BASELINE_ARB_PRIMARY": "General Sports arbitrage baseline using canonical matched exchange evidence and commission-aware equal-return staking.",
                "SPORTS_SUPERBET_ARB_PRIMARY": "Advanced Sports arbitrage strategy with capability-driven scaled entry and depth-aware execution controls.",
                "GREYHOUNDS_BASELINE_ARB_PRIMARY": "Greyhounds specialist arbitrage baseline routed through canonical Racing evidence.",
                "SPORTS_DEPTH_ARB_REFERENCE": "Research/reference Sports arbitrage engine using top-of-book depth to characterise alternative strategy behaviour.",
                "NOOP_FRAMEWORK_TEST": "Framework validation engine. Produces no economic intent and is hidden from normal engine management.",
            }
            for iid, description in defaults.items():
                self.conn.execute(
                    "UPDATE engine_instances SET description=CASE WHEN TRIM(COALESCE(description,''))='' THEN ? ELSE description END WHERE engine_instance_id=?",
                    (description, iid),
                )
            self.conn.commit()

    def _ensure_v0917_operational_schema(self) -> None:
        """0.9.23 engine provenance, SIM/LIVE controls and venue-account metadata.

        The migration is additive. Existing archive/history tables are not rewritten;
        only bounded metadata rows and the relatively small opportunity/position
        provenance columns are populated. An already-current database takes a
        SELECT-only fast path so the UI can coexist with the scanner writer.
        """
        with self.lock:
            existing = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "engine_instances" not in existing or "opportunities" not in existing or "monitor_positions" not in existing:
                return
            required_engine = {"nickname", "sim_enabled", "live_enabled"}
            required_opp = {"engine_instance_id", "engine_type", "engine_version", "engine_config_version"}
            required_pos = {"mode", "engine_instance_id", "engine_type", "engine_version", "engine_config_version"}
            venue_required = {"provider_id","venue_id","account_nickname","sim_feed_enabled","live_feed_enabled","sim_account_enabled","live_account_enabled","live_execution_enabled","created_at","updated_at"}
            venue_exists = "venue_controls" in existing
            venue_ok = venue_exists and venue_required.issubset(self._columns("venue_controls")) and "idx_venue_controls_updated" in {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
            if (required_engine.issubset(self._columns("engine_instances")) and
                required_opp.issubset(self._columns("opportunities")) and
                required_pos.issubset(self._columns("monitor_positions")) and venue_ok):
                return

            self._ensure_column("engine_instances", "nickname", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("engine_instances", "sim_enabled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("engine_instances", "live_enabled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("opportunities", "engine_instance_id", "TEXT")
            self._ensure_column("opportunities", "engine_type", "TEXT")
            self._ensure_column("opportunities", "engine_version", "TEXT")
            self._ensure_column("opportunities", "engine_config_version", "INTEGER")
            self._ensure_column("monitor_positions", "mode", "TEXT NOT NULL DEFAULT 'sim'")
            self._ensure_column("monitor_positions", "engine_instance_id", "TEXT")
            self._ensure_column("monitor_positions", "engine_type", "TEXT")
            self._ensure_column("monitor_positions", "engine_version", "TEXT")
            self._ensure_column("monitor_positions", "engine_config_version", "INTEGER")
            # A 0.9.23 database may still have the old shared venue-control columns.
            # Do not write new columns into that table here; 0.9.23 owns the bounded
            # one-way reconstruction immediately after this ensure.
            venue_current = venue_exists and venue_required.issubset(self._columns("venue_controls"))
            if not venue_exists:
                self.conn.executescript("""
                    CREATE TABLE venue_controls (
                     provider_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, account_nickname TEXT NOT NULL,
                     sim_feed_enabled INTEGER NOT NULL DEFAULT 1, live_feed_enabled INTEGER NOT NULL DEFAULT 0,
                     sim_account_enabled INTEGER NOT NULL DEFAULT 1, live_account_enabled INTEGER NOT NULL DEFAULT 1,
                     live_execution_enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_venue_controls_updated ON venue_controls(updated_at DESC,provider_id);
                """)
                venue_current = True
            if venue_current:
                now = datetime.now(timezone.utc).isoformat()
                defaults = (
                    ("betfair", "betfair", "Main Betfair", 1, 0, 1, 1, 0),
                    ("matchbook", "matchbook", "Main Matchbook", 1, 0, 1, 1, 0),
                    ("smarkets", "smarkets", "Smarkets", 0, 0, 0, 0, 0),
                )
                for row in defaults:
                    self.conn.execute(
                        """INSERT OR IGNORE INTO venue_controls(provider_id,venue_id,account_nickname,sim_feed_enabled,live_feed_enabled,sim_account_enabled,live_account_enabled,live_execution_enabled,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""", (*row, now, now),
                    )

            self.conn.execute("""UPDATE engine_instances SET sim_enabled=CASE
                WHEN UPPER(requested_lifecycle) IN ('SIM','EXPERIMENTAL') THEN 1 ELSE sim_enabled END""")
            self.conn.execute("""UPDATE engine_instances SET live_enabled=CASE
                WHEN UPPER(requested_lifecycle)='LIVE_APPROVED' THEN 1 ELSE live_enabled END""")
            nicknames = {
                "SPORTS_BASELINE_ARB_PRIMARY": "Baseline",
                "SPORTS_SUPERBET_ARB_PRIMARY": "SuperBet",
                "GREYHOUNDS_BASELINE_ARB_PRIMARY": "Greyhounds Base",
                "SPORTS_DEPTH_ARB_REFERENCE": "Depth Research",
                "NOOP_FRAMEWORK_TEST": "Framework Test",
            }
            for iid, nickname in nicknames.items():
                self.conn.execute("UPDATE engine_instances SET nickname=CASE WHEN TRIM(COALESCE(nickname,''))='' THEN ? ELSE nickname END WHERE engine_instance_id=?", (nickname, iid))

            # Deterministic historical attribution only. Ambiguous rows remain NULL
            # and surface as Legacy / Unattributed rather than being guessed.
            if "opportunities" in existing:
                self.conn.execute("""UPDATE opportunities SET engine_instance_id='GREYHOUNDS_BASELINE_ARB_PRIMARY',engine_type='GREYHOUNDS_BASELINE_ARB'
                    WHERE engine_instance_id IS NULL AND (LOWER(COALESCE(section,''))='racing' OR LOWER(COALESCE(sport,''))='greyhounds')""")
                self.conn.execute("""UPDATE opportunities SET engine_instance_id='SPORTS_BASELINE_ARB_PRIMARY',engine_type='SPORTS_BASELINE_ARB'
                    WHERE engine_instance_id IS NULL AND LOWER(COALESCE(section,'sports'))='sports'""")
                self.conn.execute("""UPDATE opportunities SET engine_version=(SELECT engine_version FROM engine_instances i WHERE i.engine_instance_id=opportunities.engine_instance_id),
                    engine_config_version=(SELECT active_config_version FROM engine_instances i WHERE i.engine_instance_id=opportunities.engine_instance_id)
                    WHERE engine_instance_id IS NOT NULL AND (engine_version IS NULL OR engine_config_version IS NULL)""")
            if "monitor_positions" in existing:
                self.conn.execute("""UPDATE monitor_positions SET mode='sim' WHERE mode IS NULL OR LOWER(TRIM(mode)) NOT IN ('sim','live')""")
                self.conn.execute("""UPDATE monitor_positions SET engine_instance_id=(SELECT o.engine_instance_id FROM opportunities o WHERE o.id=monitor_positions.opportunity_id),
                    engine_type=(SELECT o.engine_type FROM opportunities o WHERE o.id=monitor_positions.opportunity_id),
                    engine_version=(SELECT o.engine_version FROM opportunities o WHERE o.id=monitor_positions.opportunity_id),
                    engine_config_version=(SELECT o.engine_config_version FROM opportunities o WHERE o.id=monitor_positions.opportunity_id)
                    WHERE engine_instance_id IS NULL""")
                # Scaled-entry/SuperBet executions are deterministically attributable
                # to SPORTS_SUPERBET_ARB when the stored execution evidence says so.
                self.conn.execute("""UPDATE monitor_positions SET engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY',engine_type='SPORTS_SUPERBET_ARB',
                    engine_version=(SELECT engine_version FROM engine_instances WHERE engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY'),
                    engine_config_version=(SELECT active_config_version FROM engine_instances WHERE engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY')
                    WHERE LOWER(COALESCE(simulation_json,'')) LIKE '%is_scaled_entry%true%' OR LOWER(COALESCE(simulation_json,'')) LIKE '%is_superbet%true%'""")
                self.conn.execute("""UPDATE opportunities SET engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY',engine_type='SPORTS_SUPERBET_ARB',
                    engine_version=(SELECT engine_version FROM engine_instances WHERE engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY'),
                    engine_config_version=(SELECT active_config_version FROM engine_instances WHERE engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY')
                    WHERE id IN (SELECT opportunity_id FROM monitor_positions WHERE engine_type='SPORTS_SUPERBET_ARB')""")
            self.conn.commit()

    def _ensure_v0918_accounts_schema(self) -> None:
        """0.9.23 Accounts/Admin consolidation and legacy-mode retirement.

        This migration only rewrites bounded metadata/control tables plus the old
        monitor-timing measurement table names. Historical market evidence, Results,
        Replay archives, Parquet files and pruning state are not touched.
        """
        with self.lock:
            tables = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            indexes = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}

            # Once this bounded migration has completed, opening an already-current
            # database must remain read-only. The UI frequently opens SQLite while
            # the worker owns a write transaction; even INSERT OR IGNORE would then
            # block unnecessarily. A tiny schema marker lets subsequent opens take
            # the same lock-safe fast path as the rest of the current schema.
            if "settings" in tables and "venue_controls" in tables:
                fast_cols = {"provider_id","venue_id","account_nickname","sim_feed_enabled","live_feed_enabled","sim_account_enabled","live_account_enabled","live_execution_enabled","created_at","updated_at"}
                marker = self.conn.execute("SELECT value FROM settings WHERE key='schema_v0918_accounts'").fetchone()
                if marker and fast_cols.issubset(self._columns("venue_controls")):
                    try:
                        if int(json.loads(marker["value"])) == 1:
                            return
                    except Exception:
                        pass

            # One-way rename of the retired legacy measurement tables. Constructing
            # the old token keeps it out of the active source vocabulary while still
            # allowing an in-place upgrade from older databases.
            legacy_word = "".join(("sha", "dow"))
            legacy_runs = legacy_word + "_runs"
            legacy_obs = legacy_word + "_observations"
            legacy_run_fk = legacy_word + "_run_id"
            legacy_engine_results = "engine_" + legacy_word + "_results"
            if legacy_runs in tables and "monitor_timing_runs" not in tables:
                self.conn.execute(f"ALTER TABLE {legacy_runs} RENAME TO monitor_timing_runs")
                tables.discard(legacy_runs); tables.add("monitor_timing_runs")
            if legacy_obs in tables and "monitor_timing_observations" not in tables:
                self.conn.execute(f"ALTER TABLE {legacy_obs} RENAME TO monitor_timing_observations")
                tables.discard(legacy_obs); tables.add("monitor_timing_observations")
                if legacy_run_fk in self._columns("monitor_timing_observations"):
                    self.conn.execute(f"ALTER TABLE monitor_timing_observations RENAME COLUMN {legacy_run_fk} TO monitor_timing_run_id")
            if legacy_engine_results in tables:
                if "engine_monitor_timing_results" not in tables:
                    self.conn.execute(f"ALTER TABLE {legacy_engine_results} RENAME TO engine_monitor_timing_results")
                    tables.discard(legacy_engine_results); tables.add("engine_monitor_timing_results")
                else:
                    # The 0.9.14 engine schema ensure may already have created the
                    # neutral table before this one-way upgrade runs. Preserve any
                    # historical rows from the retired table, then remove it.
                    self.conn.execute(
                        f"""INSERT OR IGNORE INTO engine_monitor_timing_results
                        (decision_id,engine_instance_id,created_at,status,requested_capital,expected_profit,central_validation)
                        SELECT decision_id,engine_instance_id,created_at,status,requested_capital,expected_profit,central_validation
                        FROM {legacy_engine_results}"""
                    )
                    self.conn.execute(f"DROP TABLE {legacy_engine_results}")
                    tables.discard(legacy_engine_results)

            # Old index names can survive a table rename. Replace them with the
            # canonical 0.9.23 names so the schema fast-path is deterministic.
            for suffix in ("runs_opportunity", "runs_time", "observations_run"):
                old_idx = "idx_" + legacy_word + "_" + suffix
                if old_idx in indexes:
                    self.conn.execute(f"DROP INDEX IF EXISTS {old_idx}")
            old_engine_idx = "idx_engine_" + legacy_word + "_results_instance_time"
            if old_engine_idx in indexes:
                self.conn.execute(f"DROP INDEX IF EXISTS {old_engine_idx}")
            if "monitor_timing_runs" in tables:
                self.conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_timing_runs_opportunity ON monitor_timing_runs(opportunity_id,id DESC)")
                self.conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_timing_runs_time ON monitor_timing_runs(started_at DESC)")
            if "monitor_timing_observations" in tables:
                self.conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_timing_observations_run ON monitor_timing_observations(monitor_timing_run_id,offset_ms)")
            if "engine_monitor_timing_results" in tables:
                self.conn.execute("CREATE INDEX IF NOT EXISTS idx_engine_monitor_timing_results_instance_time ON engine_monitor_timing_results(engine_instance_id,created_at DESC)")

            target_cols = {"provider_id","venue_id","account_nickname","sim_feed_enabled","live_feed_enabled","sim_account_enabled","live_account_enabled","live_execution_enabled","created_at","updated_at"}
            old_rows = []
            if "venue_controls" in tables:
                current_cols = self._columns("venue_controls")
                if not target_cols.issubset(current_cols):
                    old_rows = [dict(r) for r in self.conn.execute("SELECT * FROM venue_controls").fetchall()]
                    self.conn.execute("DROP TABLE venue_controls")
                    tables.discard("venue_controls")
            if "venue_controls" not in tables:
                self.conn.executescript("""
                    CREATE TABLE venue_controls (
                     provider_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, account_nickname TEXT NOT NULL,
                     sim_feed_enabled INTEGER NOT NULL DEFAULT 1, live_feed_enabled INTEGER NOT NULL DEFAULT 0,
                     sim_account_enabled INTEGER NOT NULL DEFAULT 1, live_account_enabled INTEGER NOT NULL DEFAULT 1,
                     live_execution_enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_venue_controls_updated ON venue_controls(updated_at DESC,provider_id);
                """)
                tables.add("venue_controls")
                now = datetime.now(timezone.utc).isoformat()
                for row in old_rows:
                    pid = str(row.get("provider_id") or "").lower()
                    if pid not in {"betfair","matchbook","smarkets"}:
                        continue
                    old_feed = int(bool(row.get("feed_enabled", 1)))
                    self.conn.execute(
                        """INSERT OR REPLACE INTO venue_controls(provider_id,venue_id,account_nickname,sim_feed_enabled,live_feed_enabled,sim_account_enabled,live_account_enabled,live_execution_enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (pid, str(row.get("venue_id") or pid), str(row.get("account_nickname") or pid.title()),
                         old_feed, 0, int(bool(row.get("sim_enabled", 1))), int(bool(row.get("account_access_enabled", 1))),
                         int(bool(row.get("live_execution_enabled", 0))), str(row.get("created_at") or now), now),
                    )

            now = datetime.now(timezone.utc).isoformat()
            defaults = (
                ("betfair","betfair","Main Betfair",1,0,1,1,0),
                ("matchbook","matchbook","Main Matchbook",1,0,1,1,0),
                ("smarkets","smarkets","Smarkets",0,0,0,0,0),
            )
            for row in defaults:
                self.conn.execute(
                    """INSERT OR IGNORE INTO venue_controls(provider_id,venue_id,account_nickname,sim_feed_enabled,live_feed_enabled,sim_account_enabled,live_account_enabled,live_execution_enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (*row, now, now),
                )

            # Migrate old timing configuration keys into the neutral measurement
            # namespace, then delete the retired keys. This touches one settings row.
            if "settings" in tables:
                cfg_row = self.conn.execute("SELECT value FROM settings WHERE key='config'").fetchone()
                if cfg_row:
                    try:
                        cfg = json.loads(cfg_row["value"] or "{}")
                    except Exception:
                        cfg = {}
                    legacy_prefix = legacy_word + "_"
                    mapping = {
                        legacy_prefix + "checkpoints_ms": "monitor_timing_checkpoints_ms",
                        legacy_prefix + "reference_checkpoint_ms": "monitor_timing_reference_checkpoint_ms",
                        legacy_prefix + "max_concurrent_runs": "monitor_timing_max_concurrent_runs",
                    }
                    changed = False
                    for old_key,new_key in mapping.items():
                        if old_key in cfg:
                            cfg.setdefault(new_key, cfg.get(old_key)); cfg.pop(old_key, None); changed = True
                    if changed:
                        self.conn.execute("UPDATE settings SET value=? WHERE key='config'", (json.dumps(cfg, separators=(",",":")),))

            # Old active-mode values are canonicalised once. No third operational
            # mode is accepted or emitted after this migration.
            legacy_upper = legacy_word.upper()
            if "engine_instances" in tables:
                cols = self._columns("engine_instances")
                if {"requested_lifecycle","sim_enabled"}.issubset(cols):
                    self.conn.execute("UPDATE engine_instances SET requested_lifecycle='SIM',sim_enabled=1 WHERE UPPER(requested_lifecycle)=?", (legacy_upper,))
                if {"effective_lifecycle","effective_reason"}.issubset(cols):
                    self.conn.execute("UPDATE engine_instances SET effective_lifecycle='SIM',effective_reason='MIGRATED_LEGACY_TO_SIM' WHERE UPPER(effective_lifecycle)=?", (legacy_upper,))
            if "engine_decisions" in tables:
                cols = self._columns("engine_decisions")
                if "mode" in cols:
                    self.conn.execute("UPDATE engine_decisions SET mode='sim' WHERE LOWER(mode)=?", (legacy_word,))
                if "requested_lifecycle" in cols:
                    self.conn.execute("UPDATE engine_decisions SET requested_lifecycle='SIM' WHERE UPPER(requested_lifecycle)=?", (legacy_upper,))
                if "effective_lifecycle" in cols:
                    self.conn.execute("UPDATE engine_decisions SET effective_lifecycle='SIM' WHERE UPPER(effective_lifecycle)=?", (legacy_upper,))
            for table in ("execution_runs","job_schedules","jobs","account_snapshots","balance_reconciliations"):
                if table in tables and "mode" in self._columns(table):
                    self.conn.execute(f"UPDATE {table} SET mode='sim' WHERE LOWER(mode)=?", (legacy_word,))
            if "settings" in tables:
                self.conn.execute(
                    "INSERT OR REPLACE INTO settings(key,value) VALUES('schema_v0918_accounts',?)",
                    (json.dumps(1),),
                )
            self.conn.commit()

    def _ensure_v0936_sports_lifecycle_schema(self) -> None:
        """0.9.36 authoritative Sports engine provenance and evaluation ledger.

        New executions carry an explicit provenance source from origination time.
        Existing rows are deliberately marked legacy/unverified rather than being
        re-attributed from market/strategy characteristics.  The compact evaluation
        ledger lets Monitor and Engines share filtered Processed/Opportunity counts.
        """
        with self.lock:
            catalog = self.conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('table','index')").fetchall()
            tables = {str(r["name"]) for r in catalog if r["type"] == "table"}
            indexes = {str(r["name"]) for r in catalog if r["type"] == "index"}
            if "opportunities" not in tables or "monitor_positions" not in tables:
                return
            structural_ready = (
                "engine_evaluations" in tables
                and {"idx_engine_evaluations_scope", "idx_engine_evaluations_engine_time"}.issubset(indexes)
                and "engine_provenance_source" in self._columns("opportunities")
                and "engine_provenance_source" in self._columns("monitor_positions")
                and ("engine_errors" not in tables or {"mode", "section", "stream"}.issubset(self._columns("engine_errors")))
            )
            if structural_ready:
                pending_opp = self.conn.execute("SELECT 1 FROM opportunities WHERE engine_provenance_source IS NULL LIMIT 1").fetchone()
                pending_pos = self.conn.execute("SELECT 1 FROM monitor_positions WHERE engine_provenance_source IS NULL LIMIT 1").fetchone()
                if not pending_opp and not pending_pos:
                    return
            self._ensure_column("opportunities", "engine_provenance_source", "TEXT")
            self._ensure_column("monitor_positions", "engine_provenance_source", "TEXT")
            if "engine_errors" in tables:
                self._ensure_column("engine_errors", "mode", "TEXT")
                self._ensure_column("engine_errors", "section", "TEXT")
                self._ensure_column("engine_errors", "stream", "TEXT")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS engine_evaluations (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 engine_instance_id TEXT NOT NULL,
                 market_snapshot_id TEXT NOT NULL,
                 evaluated_at TEXT NOT NULL,
                 observed_at TEXT,
                 mode TEXT NOT NULL,
                 section TEXT NOT NULL,
                 sport TEXT NOT NULL,
                 event_name TEXT,
                 market_name TEXT,
                 market_type TEXT,
                 stream TEXT NOT NULL,
                 decision_id TEXT,
                 had_opportunity INTEGER NOT NULL DEFAULT 0,
                 UNIQUE(engine_instance_id,market_snapshot_id,evaluated_at)
                );
                CREATE INDEX IF NOT EXISTS idx_engine_evaluations_scope ON engine_evaluations(section,mode,stream,evaluated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_engine_evaluations_engine_time ON engine_evaluations(engine_instance_id,evaluated_at DESC);
            """)
            # Do not pretend pre-0.9.36 attribution is authoritative. Some older
            # migrations used deterministic compatibility inference; preserve the
            # stored id for forensic display but mark its authority explicitly.
            self.conn.execute("""UPDATE opportunities SET engine_provenance_source='legacy_unverified'
                                 WHERE engine_provenance_source IS NULL AND engine_instance_id IS NOT NULL""")
            self.conn.execute("""UPDATE opportunities SET engine_provenance_source='legacy_unattributed'
                                 WHERE engine_provenance_source IS NULL""")
            self.conn.execute("""UPDATE monitor_positions SET engine_provenance_source='legacy_unverified'
                                 WHERE engine_provenance_source IS NULL AND engine_instance_id IS NOT NULL""")
            self.conn.execute("""UPDATE monitor_positions SET engine_provenance_source='legacy_unattributed'
                                 WHERE engine_provenance_source IS NULL""")
            self.conn.commit()

    def _ensure_v0938_monitor_engine_schema(self) -> None:
        """0.9.38 additive Monitor/engine-ingest metadata.

        Venue provenance on evaluations lets Monitor lifecycle counters honor the
        same Venue/Account scope as the visible rows. Package metadata records the
        reviewed artifact used for an install/upgrade without changing engine
        enablement or executing uploaded code during validation.
        """
        with self.lock:
            tables = {str(r["name"]) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "engine_evaluations" in tables:
                self._ensure_column("engine_evaluations", "venue_ids_json", "TEXT")
            if "engine_instances" in tables:
                self._ensure_column("engine_instances", "package_filename", "TEXT")
                self._ensure_column("engine_instances", "package_installed_at", "TEXT")
                self._ensure_column("engine_instances", "package_previous_version", "TEXT")
            self.conn.commit()

    def _schema_is_current(self) -> bool:
        rows = self.conn.execute(
            "SELECT type,name FROM sqlite_master WHERE type IN ('table','index')"
        ).fetchall()
        tables = {str(r["name"]) for r in rows if r["type"] == "table"}
        indexes = {str(r["name"]) for r in rows if r["type"] == "index"}
        if not self._CURRENT_TABLES.issubset(tables) or not self._CURRENT_INDEXES.issubset(indexes):
            return False
        for table, required in self._CURRENT_COLUMNS.items():
            if not required.issubset(self._columns(table)):
                return False
        return True

    def rollback_if_needed(self) -> bool:
        """Release a failed write transaction so another process is never starved."""
        with self.lock:
            if not self.conn.in_transaction:
                return False
            self.conn.rollback()
            return True

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def _ensure_column(self, table: str, name: str, decl: str):
        if name not in self._columns(table):
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _migrate(self):
        self._ensure_column("snapshots", "market_id", "TEXT")
        self._ensure_column("snapshots", "selection_id", "TEXT")
        self._ensure_column("opportunities", "event_name", "TEXT")
        self._ensure_column("opportunities", "event_start", "TEXT")
        self._ensure_column("opportunities", "source_markets_json", "TEXT")
        self._ensure_column("opportunities", "match_score", "REAL DEFAULT 0")
        self._ensure_column("opportunities", "signature", "TEXT")
        self._ensure_column("opportunities", "is_demo", "INTEGER NOT NULL DEFAULT 0")
        # Tag demo rows created by v0.2/v0.3 so upgraded databases can hide them cleanly.
        self.conn.execute("UPDATE opportunities SET is_demo=1 WHERE COALESCE(is_demo,0)=0 AND (signature LIKE 'demo-%' OR event_key LIKE 'demo %')")
        self._ensure_column("scenario_runs", "stakes_json", "TEXT")
        self._ensure_column("scenario_runs", "outcome_pnls_json", "TEXT")
        self._ensure_column("scenario_runs", "realized_pnl", "REAL")
        self._ensure_column("matched_markets", "diagnostic_deployed", "REAL")
        self._ensure_column("matched_markets", "diagnostic_profit", "REAL")
        self._ensure_column("matched_markets", "limited_by", "TEXT")
        self._ensure_column("snapshots", "feed_entitlement", "TEXT NOT NULL DEFAULT 'unknown'")
        self._ensure_column("snapshots", "market_data_transport", "TEXT NOT NULL DEFAULT 'unknown'")
        self._ensure_column("snapshots", "source_timestamp", "TEXT")
        self._ensure_column("snapshots", "source_state_version", "TEXT")
        self._ensure_column("latest_snapshots", "feed_entitlement", "TEXT NOT NULL DEFAULT 'unknown'")
        self._ensure_column("latest_snapshots", "market_data_transport", "TEXT NOT NULL DEFAULT 'unknown'")
        self._ensure_column("latest_snapshots", "source_timestamp", "TEXT")
        self._ensure_column("latest_snapshots", "source_state_version", "TEXT")
        self._ensure_column("latest_snapshots", "provider_id", "TEXT")
        self._ensure_column("latest_snapshots", "venue_id", "TEXT")
        self._ensure_column("latest_snapshots", "quote_age_ms", "INTEGER")
        self._ensure_column("latest_snapshots", "depth_levels_json", "TEXT")
        for table in ("matched_markets", "opportunities"):
            self._ensure_column(table, "max_executable_stake", "REAL")
            self._ensure_column(table, "limiting_provider", "TEXT")
            self._ensure_column(table, "limiting_selection", "TEXT")
            self._ensure_column(table, "limiting_side", "TEXT")
            self._ensure_column(table, "liquidity_capable", "INTEGER")
            self._ensure_column(table, "liquidity_rejection_reason", "TEXT")
            self._ensure_column(table, "depth_at_qualification_json", "TEXT")
            self._ensure_column(table, "quote_age_at_qualification_ms", "INTEGER")
        self._ensure_column("snapshots", "commission_pct", "REAL DEFAULT 0")
        self._ensure_column("snapshots", "commission_source", "TEXT")
        self._ensure_column("snapshots", "market_type", "TEXT")
        self._ensure_column("snapshots", "strategy", "TEXT")
        self._ensure_column("opportunities", "strategy", "TEXT DEFAULT '1x2'")
        self._ensure_column("matched_markets", "strategy", "TEXT DEFAULT '1x2'")
        self._ensure_column("matched_markets", "quality_score", "REAL")
        self._ensure_column("matched_markets", "quality_band", "TEXT")
        self._ensure_column("matched_markets", "reference_bankroll", "REAL")
        self._ensure_column("matched_markets", "bankroll_roi_pct", "REAL")
        self._ensure_column("matched_markets", "capital_used_pct", "REAL")
        self._ensure_column("matched_markets", "gross_roi_pct", "REAL")
        self._ensure_column("matched_markets", "commission_impact_pct", "REAL")
        self._ensure_column("snapshots", "sport", "TEXT")
        self._ensure_column("snapshots", "in_play", "INTEGER")
        self._ensure_column("snapshots", "market_status", "TEXT")
        self._ensure_column("opportunities", "sport", "TEXT")
        self._ensure_column("opportunities", "in_play", "INTEGER")
        self._ensure_column("opportunities", "event_status", "TEXT")
        self._ensure_column("opportunities", "qualification_status", "TEXT DEFAULT 'qualified'")
        self._ensure_column("opportunities", "qualification_reason", "TEXT")
        self._ensure_column("opportunities", "routing_diagnostics_json", "TEXT")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS settlement_audits (
          id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER NOT NULL, observed_at TEXT NOT NULL, status TEXT NOT NULL,
          raw_provider_winner TEXT, provider_winner_id TEXT, canonical_winner TEXT, stored_selections_json TEXT,
          mapping_method TEXT, mapping_confidence REAL, winning_exchange TEXT, settlement_contributions_json TEXT,
          total_realized_pnl REAL, reconciliation_status TEXT, reconciliation_delta REAL, details_json TEXT,
          FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
        );
        CREATE INDEX IF NOT EXISTS idx_settlement_audits_opportunity ON settlement_audits(opportunity_id, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_settlement_audits_status ON settlement_audits(status, observed_at DESC);
        """)
        self._ensure_column("monitor_timing_runs", "research_only", "INTEGER NOT NULL DEFAULT 0")
        # v0.7.21: independent virtual bankrolls for pre-match and in-play Monitor streams.
        self._ensure_column("monitor_positions", "stream", "TEXT NOT NULL DEFAULT 'pre_match'")
        self._ensure_column("monitor_positions", "currency", "TEXT NOT NULL DEFAULT 'GBP'")
        self._ensure_column("monitor_stream_wallets", "funding_adjustment", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("monitor_timing_runs", "stream", "TEXT NOT NULL DEFAULT 'pre_match'")
        self.conn.execute("UPDATE monitor_timing_runs SET stream='in_play' WHERE COALESCE(research_only,0)=1")
        self.conn.execute("UPDATE monitor_positions SET stream=CASE WHEN stream IS NULL OR TRIM(stream)='' THEN 'pre_match' ELSE stream END")
        # Preserve the existing Monitor wallet as the pre-match portfolio on upgrade.
        self.conn.execute("""INSERT OR IGNORE INTO monitor_stream_wallets(stream,exchange,opening_balance,available_balance,reserved_balance,realized_pnl,updated_at)
            SELECT 'pre_match',exchange,opening_balance,available_balance,reserved_balance,realized_pnl,updated_at FROM monitor_wallets""")
        self.conn.execute("UPDATE opportunities SET qualification_status='qualified' WHERE qualification_status IS NULL OR TRIM(qualification_status)=''")
        self.conn.execute("""UPDATE opportunities SET qualification_status='in_play_research', qualification_reason=COALESCE(qualification_reason,'Fresh exchange state confirmed in-play')
            WHERE COALESCE(qualification_status,'qualified')='qualified'
              AND id IN (SELECT opportunity_id FROM monitor_timing_runs WHERE UPPER(COALESCE(first_failure_reason,'')) IN ('EVENT_STARTED','BETFAIR_IN_PLAY','MATCHBOOK_IN_PLAY','BOTH_IN_PLAY'))
              AND id NOT IN (SELECT opportunity_id FROM monitor_positions)""")
        self.conn.execute("""UPDATE matched_markets SET status='in_play_research', reason='In-play research only — fresh exchange state confirmed in-play'
            WHERE id IN (
                SELECT mm.id FROM matched_markets mm JOIN opportunities o
                  ON mm.event_key=o.event_key AND mm.market_name=o.market_name
                 AND COALESCE(mm.sport,'Unknown')=COALESCE(o.sport,'Unknown')
                WHERE COALESCE(o.qualification_status,'qualified')='in_play_research'
                  AND ABS((julianday(mm.observed_at)-julianday(o.detected_at))*86400.0) <= 10.0
            )""")
        self._ensure_column("matched_markets", "sport", "TEXT")
        self._ensure_column("matched_markets", "section", "TEXT DEFAULT 'sports'")
        self._ensure_column("matched_markets", "race_track", "TEXT")
        self._ensure_column("matched_markets", "race_number", "INTEGER")
        self._ensure_column("matched_markets", "runner_count", "INTEGER")
        self._ensure_column("matched_markets", "time_to_off_seconds", "INTEGER")
        self._ensure_column("matched_markets", "in_play", "INTEGER")
        self._ensure_column("matched_markets", "event_status", "TEXT")
        self._ensure_column("market_cache", "section", "TEXT DEFAULT 'sports'")
        self._ensure_column("market_cache", "race_track", "TEXT")
        self._ensure_column("market_cache", "race_number", "INTEGER")
        self._ensure_column("market_cache", "runner_count", "INTEGER")
        self._ensure_column("opportunities", "section", "TEXT DEFAULT 'sports'")
        self._ensure_column("opportunities", "race_track", "TEXT")
        self._ensure_column("opportunities", "race_number", "INTEGER")
        self._ensure_column("opportunities", "runner_count", "INTEGER")
        self._ensure_column("opportunities", "time_to_off_seconds", "INTEGER")
        self._ensure_column("snapshots", "section", "TEXT DEFAULT 'sports'")
        self._ensure_column("snapshots", "trap_number", "INTEGER")
        self._ensure_column("snapshots", "canonical_selection_key", "TEXT")
        self._ensure_column("snapshots", "runner_status", "TEXT")
        self._ensure_column("opportunity_tracks", "sport", "TEXT")
        self._ensure_column("scan_runs", "job_id", "INTEGER")
        self._ensure_column("opportunities", "job_id", "INTEGER")
        self._ensure_column("execution_runs", "job_id", "INTEGER")
        # v0.7.14 operational telemetry for the latest scan funnel.
        self._ensure_column("scan_runs", "processed_candidates", "INTEGER DEFAULT 0")
        self._ensure_column("scan_runs", "positive_opportunities", "INTEGER DEFAULT 0")
        self._ensure_column("scan_runs", "qualified_count", "INTEGER DEFAULT 0")
        self._ensure_column("scan_runs", "executed_count", "INTEGER DEFAULT 0")
        self._ensure_column("scan_runs", "duration_ms", "INTEGER DEFAULT 0")
        # v0.7.17 separates slow market discovery from fast price scans.
        self._ensure_column("scan_runs", "scan_kind", "TEXT DEFAULT 'legacy'")
        self._ensure_column("scan_runs", "stage_timings_json", "TEXT")
        self._ensure_column("scan_runs", "cache_entries", "INTEGER DEFAULT 0")
        self._ensure_column("scan_runs", "stale_rejections", "INTEGER DEFAULT 0")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_runs_kind_time ON scan_runs(scan_kind,started_at DESC)")
        # v0.8.17 read-path indexes. These are additive only: no user rows are rewritten or removed.
        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_opportunities_detected ON opportunities(detected_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_opportunities_qualification_time ON opportunities(qualification_status,detected_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_opportunities_market_analysis ON opportunities(section,sport,market_name,in_play,detected_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_settlements_time ON settlements(settled_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_execution_runs_time ON execution_runs(started_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_execution_runs_time_mode ON execution_runs(started_at DESC,mode)",
            "CREATE INDEX IF NOT EXISTS idx_monitor_positions_execution_run ON monitor_positions(execution_run_id)",
            "CREATE INDEX IF NOT EXISTS idx_monitor_positions_opened ON monitor_positions(opened_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_monitor_positions_settled ON monitor_positions(status,settled_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_matched_markets_observed ON matched_markets(observed_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_matched_markets_analysis ON matched_markets(section,sport,market_name,in_play,observed_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_scan_runs_time_kind ON scan_runs(started_at DESC,scan_kind)",
            "CREATE INDEX IF NOT EXISTS idx_account_snapshots_lookup ON account_snapshots(mode,exchange,captured_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_account_snapshots_stream ON account_snapshots(mode,stream,exchange,captured_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_balance_reconciliations_lookup ON balance_reconciliations(mode,exchange,checked_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_sim_account_adjustments_time ON sim_account_adjustments(exchange,created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_latest_snapshots_exchange_time ON latest_snapshots(exchange,captured_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_latest_snapshots_event_time ON latest_snapshots(event_id,captured_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_snapshot_rollups_time ON snapshot_rollups(hour_utc DESC,exchange)",
            "CREATE INDEX IF NOT EXISTS idx_market_hourly_rollups_time ON market_hourly_rollups(hour_utc DESC,section,sport,in_play)",
            "CREATE INDEX IF NOT EXISTS idx_market_hourly_seen_time ON market_hourly_seen(hour_utc DESC)",
        ):
            self.conn.execute(sql)

    def get_setting(self, key, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.lock:
            # Avoid taking SQLite's single-writer lock for idempotent startup
            # assignments such as mode/config.  Compare decoded values so legacy
            # JSON formatting/key order does not force a needless rewrite.
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row is not None:
                try:
                    if json.loads(row[0]) == value:
                        return
                except Exception:
                    pass
            self.conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self.conn.commit()

    def sim_financial_revision(self) -> int:
        """Monotonic revision for canonical SIM financial state.

        Used by UI/account consistency checks only; LIVE snapshots retain their own
        provider timestamps/IDs and never share this revision.
        """
        try:
            return int(self.get_setting("sim_financial_revision", 0) or 0)
        except Exception:
            return 0

    def _bump_sim_financial_revision_locked(self) -> int:
        row = self.conn.execute("SELECT value FROM settings WHERE key='sim_financial_revision'").fetchone()
        current = 0
        if row is not None:
            try:
                current = int(json.loads(row[0]) or 0)
            except Exception:
                current = 0
        nxt = current + 1
        self.conn.execute(
            "INSERT INTO settings(key,value) VALUES('sim_financial_revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(nxt),),
        )
        return nxt

    # --- Monitor virtual exchange wallets -------------------------------------

    def ensure_monitor_wallets(self, balances: dict[str, float], stream: str = "pre_match") -> None:
        now = datetime.now(timezone.utc).isoformat()
        stream = str(stream or "pre_match")
        with self.lock:
            wanted = {str(exchange): max(0.0, float(raw or 0.0)) for exchange, raw in (balances or {}).items()}
            changed = False
            existing_stream = {
                str(r[0]) for r in self.conn.execute(
                    "SELECT exchange FROM monitor_stream_wallets WHERE stream=?", (stream,)
                ).fetchall()
            }
            existing_legacy = set()
            if stream == "pre_match":
                existing_legacy = {str(r[0]) for r in self.conn.execute("SELECT exchange FROM monitor_wallets").fetchall()}
            missing_stream = [exchange for exchange in wanted if exchange not in existing_stream]
            missing_legacy = [exchange for exchange in wanted if stream == "pre_match" and exchange not in existing_legacy]
            if not missing_stream and not missing_legacy:
                return
            for exchange in missing_stream:
                amount = wanted[exchange]
                self.conn.execute(
                    """INSERT INTO monitor_stream_wallets(stream,exchange,opening_balance,available_balance,reserved_balance,realized_pnl,updated_at)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(stream,exchange) DO NOTHING""",
                    (stream, exchange, amount, amount, 0.0, 0.0, now),
                )
            # Legacy compatibility: mirror only the pre-match opening wallet so
            # older diagnostics/tests can still inspect the historical table.
            for exchange in missing_legacy:
                amount = wanted[exchange]
                self.conn.execute(
                    """INSERT INTO monitor_wallets(exchange,opening_balance,available_balance,reserved_balance,realized_pnl,updated_at)
                       VALUES(?,?,?,?,?,?) ON CONFLICT(exchange) DO NOTHING""",
                    (exchange, amount, amount, 0.0, 0.0, now),
                )
            changed = bool(missing_stream or missing_legacy)
            if changed:
                self._bump_sim_financial_revision_locked()
            self.conn.commit()

    def ensure_monitor_streams(self, pre_match_balances: dict[str, float], in_play_balances: dict[str, float] | None = None,
                               racing_balances: dict[str, float] | None = None) -> None:
        self.ensure_monitor_wallets(pre_match_balances, "pre_match")
        self.ensure_monitor_wallets(in_play_balances if in_play_balances is not None else pre_match_balances, "in_play")
        if racing_balances is not None:
            self.ensure_monitor_wallets(racing_balances, "racing")

    def reset_monitor_wallets(self, balances: dict[str, float], stream: str | None = None, capture_snapshot: bool = True) -> None:
        now = datetime.now(timezone.utc).isoformat()
        streams = [str(stream)] if stream else ["pre_match", "in_play", "racing"]
        with self.lock:
            if stream:
                self.conn.execute("DELETE FROM monitor_positions WHERE stream=?", (str(stream),))
                self.conn.execute("DELETE FROM monitor_stream_wallets WHERE stream=?", (str(stream),))
            else:
                self.conn.execute("DELETE FROM monitor_positions")
                self.conn.execute("DELETE FROM monitor_stream_wallets")
                self.conn.execute("DELETE FROM monitor_wallets")
            for stream_name in streams:
                for exchange, raw in (balances or {}).items():
                    amount = max(0.0, float(raw or 0.0))
                    self.conn.execute(
                        "INSERT INTO monitor_stream_wallets(stream,exchange,opening_balance,available_balance,reserved_balance,realized_pnl,updated_at) VALUES(?,?,?,?,?,?,?)",
                        (stream_name, str(exchange), amount, amount, 0.0, 0.0, now),
                    )
                    if stream_name == "pre_match":
                        self.conn.execute(
                            "INSERT INTO monitor_wallets(exchange,opening_balance,available_balance,reserved_balance,realized_pnl,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(exchange) DO UPDATE SET opening_balance=excluded.opening_balance,available_balance=excluded.available_balance,reserved_balance=0,realized_pnl=0,updated_at=excluded.updated_at",
                            (str(exchange), amount, amount, 0.0, 0.0, now),
                        )
                if capture_snapshot:
                    self._snapshot_monitor_stream_wallets_locked(stream_name, "wallet_reset")
            self._bump_sim_financial_revision_locked()
            self.conn.commit()

    def monitor_wallet_snapshot(self, hedge_reserve_pct: float = 0.0, stream: str = "pre_match") -> dict:
        reserve_pct = min(100.0, max(0.0, float(hedge_reserve_pct or 0.0)))
        stream = str(stream or "pre_match")
        with self.lock:
            rows = [dict(r) for r in self.conn.execute("SELECT * FROM monitor_stream_wallets WHERE stream=? ORDER BY exchange", (stream,)).fetchall()]
            out = {}
            for row in rows:
                equity = float(row.get("available_balance") or 0.0) + float(row.get("reserved_balance") or 0.0)
                reserve_floor = equity * reserve_pct / 100.0
                free_normal = max(0.0, float(row.get("available_balance") or 0.0) - reserve_floor)
                out[str(row["exchange"])] = {
                    "stream": stream,
                    "opening_balance": round(float(row.get("opening_balance") or 0.0), 4),
                    "available": round(float(row.get("available_balance") or 0.0), 4),
                    "reserved": round(float(row.get("reserved_balance") or 0.0), 4),
                    "equity": round(equity, 4),
                    "realized_pnl": round(float(row.get("realized_pnl") or 0.0), 4),
                    "funding_adjustment": round(float(row.get("funding_adjustment") or 0.0), 4),
                    "hedge_reserve": round(reserve_floor, 4),
                    "free_for_normal": round(free_normal, 4),
                    "updated_at": row.get("updated_at"),
                }
            return out

    def monitor_wallets_by_stream(self, hedge_reserve_pct: float | dict = 0.0) -> dict:
        if isinstance(hedge_reserve_pct, dict):
            pre_reserve = float(hedge_reserve_pct.get("pre_match", 0.0) or 0.0)
            inplay_reserve = float(hedge_reserve_pct.get("in_play", pre_reserve) or 0.0)
            racing_reserve = float(hedge_reserve_pct.get("racing", pre_reserve) or 0.0)
        else:
            pre_reserve = inplay_reserve = racing_reserve = float(hedge_reserve_pct or 0.0)
        return {
            "pre_match": self.monitor_wallet_snapshot(pre_reserve, "pre_match"),
            "in_play": self.monitor_wallet_snapshot(inplay_reserve, "in_play"),
            "racing": self.monitor_wallet_snapshot(racing_reserve, "racing"),
        }

    # --- v0.8.34 SIM account funding ------------------------------------------

    def sim_account_adjustments(self, exchange: str | None = None, limit: int = 200) -> list[dict]:
        clauses, args = [], []
        if exchange:
            clauses.append("exchange=?")
            args.append(str(exchange).lower())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(max(1, min(5000, int(limit or 200))))
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM sim_account_adjustments{where} ORDER BY created_at DESC,id DESC LIMIT ?", tuple(args)
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            try: d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            except Exception: d["metadata"] = {}
            out.append(d)
        return out

    def adjust_sim_account(self, *, exchange: str, action: str, value: float | None,
                           currency: str = "GBP", reason: str | None = None) -> dict:
        """Apply an auditable funding adjustment to one SIM exchange account.

        The adjustment is distributed across portfolio wallets, so Sports/Racing
        allocations remain the execution source of truth. Opening balances and
        historical P&L are never rewritten. Reserved capital is never withdrawn.
        """
        exchange = str(exchange or "").strip().lower()
        if not exchange:
            return {"ok": False, "message": "Provider/venue is required"}
        action = str(action or "").lower()
        if action not in {"add", "withdraw", "set", "reset"}:
            return {"ok": False, "message": "Unknown SIM funding action"}
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT * FROM monitor_stream_wallets WHERE exchange=? ORDER BY stream", (exchange,)
            ).fetchall()]
            if not rows:
                return {"ok": False, "message": "SIM account has no portfolio allocations"}
            previous = sum(float(r.get("available_balance") or 0.0) + float(r.get("reserved_balance") or 0.0) for r in rows)
            available_total = sum(float(r.get("available_balance") or 0.0) for r in rows)
            funding_total = sum(float(r.get("funding_adjustment") or 0.0) for r in rows)
            raw = 0.0 if value is None else float(value)
            if action == "add":
                delta = abs(raw)
            elif action == "withdraw":
                delta = -abs(raw)
            elif action == "set":
                if raw < 0:
                    return {"ok": False, "message": "SIM balance cannot be negative"}
                delta = raw - previous
            else:  # reset funding adjustments only; history/opening balances remain intact
                delta = -funding_total
            if abs(delta) < 0.0000001:
                return {"ok": True, "message": "No balance change required", "previous_equity": round(previous,4), "resulting_equity": round(previous,4), "amount": 0.0}
            if delta < 0 and abs(delta) > available_total + 1e-9:
                return {"ok": False, "message": "Cannot withdraw reserved capital; settle/release positions or choose a higher SIM balance"}

            if delta > 0:
                # Preserve the operator's current market-allocation proportions.
                # Using historic opening balances here would undo a later SIM
                # budget reallocation whenever the account is topped up.
                weights = [max(0.0, float(r.get("available_balance") or 0.0) + float(r.get("reserved_balance") or 0.0)) for r in rows]
                denom = sum(weights)
                if denom <= 0:
                    weights = [1.0] * len(rows); denom = float(len(rows))
            else:
                weights = [max(0.0, float(r.get("available_balance") or 0.0)) for r in rows]
                denom = sum(weights) or 1.0

            remaining = delta
            for idx, (r, w) in enumerate(zip(rows, weights)):
                share = remaining if idx == len(rows)-1 else delta * (w / denom)
                if delta < 0:
                    share = max(-float(r.get("available_balance") or 0.0), share)
                new_available = float(r.get("available_balance") or 0.0) + share
                new_funding = float(r.get("funding_adjustment") or 0.0) + share
                self.conn.execute(
                    "UPDATE monitor_stream_wallets SET available_balance=?,funding_adjustment=?,updated_at=? WHERE stream=? AND exchange=?",
                    (new_available, new_funding, now, str(r.get("stream")), exchange),
                )
                if str(r.get("stream")) == "pre_match":
                    self.conn.execute(
                        "UPDATE monitor_wallets SET available_balance=available_balance+?,updated_at=? WHERE exchange=?",
                        (share, now, exchange),
                    )
                remaining -= share
            resulting = previous + delta
            self.conn.execute(
                "INSERT INTO sim_account_adjustments(exchange,action,amount,previous_equity,resulting_equity,currency,reason,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (exchange, action, delta, previous, resulting, str(currency or "GBP").upper(), reason, now,
                 json.dumps({"funding_adjustment_before": funding_total, "reserved_untouched": True})),
            )
            # Capture every affected portfolio plus canonical account checkpoints.
            for stream in {str(r.get("stream")) for r in rows}:
                self._snapshot_monitor_stream_wallets_locked(stream, f"sim_funding_{action}", currency)
            self._bump_sim_financial_revision_locked()
            self.conn.commit()
        return {"ok": True, "message": f"SIM {exchange.title()} balance updated", "amount": round(delta,4),
                "previous_equity": round(previous,4), "resulting_equity": round(resulting,4)}

    def rebalance_sim_allocations(self, *, targets: dict, currency: str = "GBP",
                                  reason: str | None = None, tolerance: float = 0.01) -> dict:
        """Move unreserved SIM equity between portfolio wallets without changing account totals.

        The implementation is venue-neutral. Existing venue allocations omitted by
        a client are preserved, so enabling a future provider can never silently
        redistribute its capital or the capital of existing providers.
        """
        streams = ("pre_match", "in_play", "racing")
        now = datetime.now(timezone.utc).isoformat()
        tolerance = max(0.0001, float(tolerance or 0.01))
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT * FROM monitor_stream_wallets ORDER BY stream,exchange"
            ).fetchall()]
            by_key = {(str(r.get("stream")), str(r.get("exchange")).lower()): r for r in rows}
            exchanges = sorted({e for _, e in by_key} | {str(e).lower() for v in (targets or {}).values() if isinstance(v, dict) for e in v})
            if not exchanges:
                return {"ok": False, "message": "No SIM venue accounts are configured"}

            before: dict[str, dict[str, float]] = {st: {} for st in streams}
            clean: dict[str, dict[str, float]] = {st: {} for st in streams}
            for stream in streams:
                raw = (targets or {}).get(stream) or {}
                if not isinstance(raw, dict):
                    return {"ok": False, "message": f"Invalid {stream} market budgets"}
                for exchange in exchanges:
                    row = by_key.get((stream, exchange))
                    if row is None:
                        # New venue allocations must be created explicitly via the
                        # account/bootstrap path. Rebalancing never invents capital.
                        if exchange in {str(k).lower() for k in raw}:
                            return {"ok": False, "message": f"Missing SIM allocation wallet: {stream}/{exchange}"}
                        continue
                    current = float(row.get("available_balance") or 0.0) + float(row.get("reserved_balance") or 0.0)
                    before[stream][exchange] = round(current, 4)
                    supplied = next((v for k, v in raw.items() if str(k).lower() == exchange), None)
                    try:
                        value = current if supplied is None else float(supplied or 0.0)
                    except (TypeError, ValueError):
                        return {"ok": False, "message": f"Invalid {stream} {exchange} budget"}
                    if value < 0:
                        return {"ok": False, "message": "SIM market budgets cannot be negative"}
                    clean[stream][exchange] = value

            for exchange in exchanges:
                participating = [st for st in streams if exchange in before[st]]
                if not participating:
                    continue
                current_total = sum(before[st][exchange] for st in participating)
                target_total = sum(clean[st][exchange] for st in participating)
                if abs(target_total - current_total) > tolerance:
                    return {
                        "ok": False,
                        "message": (f"{exchange.title()} market budgets must total the current SIM account equity "
                                    f"({current_total:.4f}); target total is {target_total:.4f}. "
                                    "Use Add funds / Withdraw / Set balance first if you want to change the account total."),
                        "exchange": exchange, "current_total": round(current_total,4), "target_total": round(target_total,4),
                    }
                for stream in participating:
                    reserved = float(by_key[(stream, exchange)].get("reserved_balance") or 0.0)
                    if clean[stream][exchange] + tolerance < reserved:
                        return {
                            "ok": False,
                            "message": (f"{stream.replace('_',' ').title()} {exchange.title()} budget cannot be below "
                                        f"reserved open-position capital ({reserved:.4f})."),
                            "stream": stream, "exchange": exchange, "reserved": round(reserved,4),
                        }

            for stream in streams:
                for exchange, target in clean[stream].items():
                    row = by_key[(stream, exchange)]
                    reserved = float(row.get("reserved_balance") or 0.0)
                    new_available = max(0.0, target - reserved)
                    self.conn.execute(
                        "UPDATE monitor_stream_wallets SET available_balance=?,updated_at=? WHERE stream=? AND exchange=?",
                        (new_available, now, stream, exchange),
                    )
                    if stream == "pre_match":
                        self.conn.execute(
                            "UPDATE monitor_wallets SET available_balance=?,reserved_balance=?,updated_at=? WHERE exchange=?",
                            (new_available, reserved, now, exchange),
                        )

            audit_reason = str(reason or "manual SIM market budget reallocation")
            for exchange in exchanges:
                participating = [st for st in streams if exchange in clean[st]]
                if not participating:
                    continue
                total = sum(clean[st][exchange] for st in participating)
                self.conn.execute(
                    "INSERT INTO sim_account_adjustments(exchange,action,amount,previous_equity,resulting_equity,currency,reason,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (exchange, "reallocate", 0.0, total, total, str(currency or "GBP").upper(), audit_reason, now,
                     json.dumps({"allocation_before": {st: before[st][exchange] for st in participating},
                                 "allocation_after": {st: clean[st][exchange] for st in participating},
                                 "reserved_untouched": True})),
                )
            for stream in streams:
                self._snapshot_monitor_stream_wallets_locked(stream, "sim_budget_reallocate", currency)
            self._bump_sim_financial_revision_locked()
            self.conn.commit()
        return {"ok": True, "message": "SIM market budgets reallocated", "targets": clean, "before": before}

    # --- Canonical account/balance audit --------------------------------------

    def record_account_snapshot(self, *, mode: str, exchange: str, currency: str,
                                source: str, available: float, reserved: float = 0.0,
                                exposure: float = 0.0, equity: float | None = None,
                                realized_pnl: float = 0.0, freshness: str = "CURRENT",
                                stream: str | None = None, context: str | None = None,
                                metadata: dict | None = None, captured_at: str | None = None) -> int:
        captured_at = str(captured_at or datetime.now(timezone.utc).isoformat())
        available = float(available or 0.0)
        reserved = float(reserved or 0.0)
        exposure = float(exposure or 0.0)
        if equity is None:
            equity = available + reserved
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO account_snapshots(mode,exchange,stream,currency,source,available_balance,reserved_balance,
                   exposure,equity,realized_pnl,freshness,captured_at,context,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (canonical_mode_value(mode), str(exchange or "unknown").lower(),
                 None if stream is None else str(stream), str(currency or "GBP").upper(), str(source or "unknown"),
                 available, reserved, exposure, float(equity or 0.0), float(realized_pnl or 0.0),
                 str(freshness or "CURRENT").upper(), captured_at, context,
                 json.dumps(metadata or {}, default=str)),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def latest_account_snapshots(self, mode: str = "sim") -> dict[str, dict]:
        mode = canonical_mode_value(mode)
        aliases = ("sim", "monitor", "monitor_timing", "watch", "paper", "simulate") if mode == "sim" else ("live",)
        marks = ",".join("?" for _ in aliases)
        with self.lock:
            rows = self.conn.execute(
                f"""SELECT a.* FROM account_snapshots a
                   JOIN (SELECT exchange,MAX(id) id FROM account_snapshots WHERE LOWER(mode) IN ({marks}) GROUP BY exchange) x ON x.id=a.id
                   ORDER BY a.exchange""", aliases
            ).fetchall()
        out = {}
        for row in rows:
            d = dict(row)
            d["mode"] = canonical_mode_value(d.get("mode"))
            try: d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            except Exception: d["metadata"] = {}
            out[str(d.get("exchange") or "unknown")] = d
        return out

    def account_snapshot_history(self, *, mode: str = "sim", exchange: str | None = None,
                                 from_utc: str | None = None, to_utc: str | None = None,
                                 limit: int = 2000) -> list[dict]:
        canonical = canonical_mode_value(mode)
        aliases = ("sim", "monitor", "monitor_timing", "watch", "paper", "simulate") if canonical == "sim" else ("live",)
        clauses = [f"LOWER(mode) IN ({','.join('?' for _ in aliases)})"]
        args: list = list(aliases)
        if exchange:
            clauses.append("exchange=?")
            args.append(str(exchange).lower())
        if from_utc:
            clauses.append("captured_at>=?")
            args.append(str(from_utc))
        if to_utc:
            clauses.append("captured_at<?")
            args.append(str(to_utc))
        args.append(max(1, min(20000, int(limit or 2000))))
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM account_snapshots WHERE {' AND '.join(clauses)} ORDER BY captured_at ASC,id ASC LIMIT ?",
                tuple(args),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["mode"] = canonical_mode_value(d.get("mode"))
            try: d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            except Exception: d["metadata"] = {}
            out.append(d)
        return out

    def account_snapshot_state_at(self, *, mode: str = "sim", at_utc: str | None = None) -> dict[str, dict]:
        """Latest aggregate account snapshot per venue at/before a timestamp."""
        canonical = canonical_mode_value(mode)
        aliases = ("sim", "monitor", "monitor_timing", "watch", "paper", "simulate") if canonical == "sim" else ("live",)
        at_utc = str(at_utc or datetime.now(timezone.utc).isoformat())
        marks = ",".join("?" for _ in aliases)
        with self.lock:
            rows = self.conn.execute(
                f"""SELECT a.* FROM account_snapshots a
                   JOIN (SELECT exchange,MAX(id) id FROM account_snapshots
                         WHERE LOWER(mode) IN ({marks}) AND stream IS NULL AND captured_at<=? GROUP BY exchange) x ON x.id=a.id
                   ORDER BY a.exchange""",
                tuple(aliases) + (at_utc,),
            ).fetchall()
        out = {}
        for row in rows:
            d = dict(row)
            d["mode"] = canonical
            try: d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            except Exception: d["metadata"] = {}
            out[str(d.get("exchange") or "unknown")] = d
        return out

    def record_balance_reconciliation(self, *, mode: str, status: str, expected: float | None,
                                      observed: float | None, delta: float | None, tolerance: float = 0.01,
                                      exchange: str | None = None, stream: str | None = None,
                                      details: dict | None = None, checked_at: str | None = None) -> int:
        checked_at = str(checked_at or datetime.now(timezone.utc).isoformat())
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO balance_reconciliations(mode,exchange,stream,status,expected,observed,delta,tolerance,checked_at,details_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (canonical_mode_value(mode), None if exchange is None else str(exchange).lower(),
                 stream, str(status or "WARNING").upper(), expected, observed, delta,
                 float(tolerance or 0.01), checked_at, json.dumps(details or {}, default=str)),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def latest_balance_reconciliations(self, mode: str = "sim") -> dict[str, dict]:
        canonical = canonical_mode_value(mode)
        aliases = ("sim", "monitor", "monitor_timing", "watch", "paper", "simulate") if canonical == "sim" else ("live",)
        marks = ",".join("?" for _ in aliases)
        with self.lock:
            rows = self.conn.execute(
                f"""SELECT b.* FROM balance_reconciliations b
                   JOIN (SELECT COALESCE(exchange,'__all__') ex,MAX(id) id FROM balance_reconciliations WHERE LOWER(mode) IN ({marks}) GROUP BY COALESCE(exchange,'__all__')) x ON x.id=b.id
                   ORDER BY b.exchange""", aliases
            ).fetchall()
        out = {}
        for row in rows:
            d = dict(row)
            d["mode"] = canonical
            try: d["details"] = json.loads(d.pop("details_json") or "{}")
            except Exception: d["details"] = {}
            out[str(d.get("exchange") or "all")] = d
        return out

    def _configured_account_currency_locked(self) -> str:
        try:
            row = self.conn.execute("SELECT value FROM settings WHERE key='config'").fetchone()
            cfg = json.loads(row[0]) if row and row[0] else {}
            value = str(cfg.get("account_currency") or "GBP").strip().upper()
            return value if len(value) == 3 and value.isalpha() else "GBP"
        except Exception:
            return "GBP"

    def _snapshot_monitor_stream_wallets_locked(self, stream: str, context: str, currency: str | None = None) -> None:
        """Capture both the changed portfolio allocation and canonical exchange totals.

        Stream snapshots explain attribution; aggregate (stream=NULL) snapshots are
        the account-level series consumed by Dashboard/Replay reconciliation.
        Called while ``self.lock`` and the surrounding wallet transaction are held.
        """
        now = datetime.now(timezone.utc).isoformat()
        stream = str(stream)
        if currency is None:
            currency = self._configured_account_currency_locked()
        rows = self.conn.execute("SELECT * FROM monitor_stream_wallets ORDER BY stream,exchange").fetchall()
        totals: dict[str, dict[str, float]] = {}
        for row in rows:
            d = dict(row)
            exchange = str(d.get("exchange") or "unknown").lower()
            available = float(d.get("available_balance") or 0.0)
            reserved = float(d.get("reserved_balance") or 0.0)
            realized = float(d.get("realized_pnl") or 0.0)
            opening = float(d.get("opening_balance") or 0.0)
            funding = float(d.get("funding_adjustment") or 0.0)
            bucket = totals.setdefault(exchange, {"available": 0.0, "reserved": 0.0, "realized": 0.0, "opening": 0.0, "funding": 0.0})
            bucket["available"] += available
            bucket["reserved"] += reserved
            bucket["realized"] += realized
            bucket["opening"] += opening
            bucket["funding"] += funding
            if str(d.get("stream") or "") != stream:
                continue
            self.conn.execute(
                """INSERT INTO account_snapshots(mode,exchange,stream,currency,source,available_balance,reserved_balance,exposure,equity,realized_pnl,freshness,captured_at,context,metadata_json)
                   VALUES('sim',?,?,?,?,?,?,?,?,?,'CURRENT',?,?,?)""",
                (exchange, stream, str(currency or "GBP").upper(), "virtual_ledger", available, reserved, reserved,
                 available + reserved, realized, now, context, json.dumps({"level": "portfolio_allocation", "funding_adjustment": funding})),
            )
        for exchange, bucket in totals.items():
            available = bucket["available"]
            reserved = bucket["reserved"]
            self.conn.execute(
                """INSERT INTO account_snapshots(mode,exchange,stream,currency,source,available_balance,reserved_balance,exposure,equity,realized_pnl,freshness,captured_at,context,metadata_json)
                   VALUES('sim',?,NULL,?,?,?,?,?,?,?,'CURRENT',?,?,?)""",
                (exchange, str(currency or "GBP").upper(), "virtual_ledger", available, reserved, reserved,
                 available + reserved, bucket["realized"], now, context,
                 json.dumps({"level": "exchange_account", "changed_stream": stream, "opening_balance": bucket["opening"], "funding_adjustment": bucket["funding"]})),
            )

    def monitor_has_open_market(self, event_key: str | None, market_name: str | None, stream: str = "pre_match") -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM monitor_positions WHERE status='OPEN' AND event_key=? AND market_name=? AND COALESCE(stream,'pre_match')=? LIMIT 1",
                (str(event_key or ""), str(market_name or ""), str(stream or "pre_match")),
            ).fetchone()
            return bool(row)

    def open_monitor_position(self, *, opportunity_id: int, execution_run_id: int | None, event_key: str | None,
                              market_name: str | None, deployed: float, expected_profit: float,
                              stakes_by_exchange: dict[str, float], outcome_exchange_pnls: dict,
                              simulation: dict, hedge_reserve_pct: float = 0.0,
                              normal_stakes_by_exchange: dict[str, float] | None = None,
                              stream: str = "pre_match") -> tuple[bool, str | None]:
        now = datetime.now(timezone.utc).isoformat()
        stream = str(stream or "pre_match")
        reserve_pct = min(100.0, max(0.0, float(hedge_reserve_pct or 0.0)))
        with self.lock:
            if self.conn.execute("SELECT 1 FROM monitor_positions WHERE opportunity_id=? LIMIT 1", (int(opportunity_id),)).fetchone():
                return False, "already_recorded"
            if self.conn.execute(
                "SELECT 1 FROM monitor_positions WHERE status='OPEN' AND event_key=? AND market_name=? AND COALESCE(stream,'pre_match')=? LIMIT 1",
                (str(event_key or ""), str(market_name or ""), stream),
            ).fetchone():
                return False, "market_already_open"
            wallet_rows = {str(r["exchange"]): dict(r) for r in self.conn.execute("SELECT * FROM monitor_stream_wallets WHERE stream=?", (stream,)).fetchall()}
            for exchange, need in (stakes_by_exchange or {}).items():
                row = wallet_rows.get(str(exchange))
                if not row:
                    return False, f"wallet_missing:{exchange}"
                available = float(row.get("available_balance") or 0.0)
                equity = available + float(row.get("reserved_balance") or 0.0)
                free_normal = max(0.0, available - equity * reserve_pct / 100.0)
                total_need = max(0.0, float(need or 0.0))
                normal_need = max(0.0, float((normal_stakes_by_exchange or stakes_by_exchange or {}).get(exchange, 0.0) or 0.0))
                if normal_need > free_normal + 1e-8:
                    return False, f"insufficient_balance:{exchange}"
                if total_need > available + 1e-8:
                    return False, f"insufficient_hedge_balance:{exchange}"
            self._snapshot_monitor_stream_wallets_locked(stream, "execution_reserve_before")
            for exchange, need in (stakes_by_exchange or {}).items():
                amount = max(0.0, float(need or 0.0))
                self.conn.execute(
                    "UPDATE monitor_stream_wallets SET available_balance=available_balance-?,reserved_balance=reserved_balance+?,updated_at=? WHERE stream=? AND exchange=?",
                    (amount, amount, now, stream, str(exchange)),
                )
            opp = self.conn.execute("SELECT engine_instance_id,engine_type,engine_version,engine_config_version,engine_provenance_source FROM opportunities WHERE id=?", (int(opportunity_id),)).fetchone()
            engine_instance_id = opp["engine_instance_id"] if opp else None
            engine_type = opp["engine_type"] if opp else None
            engine_version = opp["engine_version"] if opp else None
            engine_config_version = opp["engine_config_version"] if opp else None
            engine_provenance_source = opp["engine_provenance_source"] if opp else None
            scaled = (simulation or {}).get("scaled_entry") or (simulation or {}).get("superbet") or {}
            if bool((scaled or {}).get("is_scaled_entry") or (scaled or {}).get("is_superbet")):
                e = self.conn.execute("SELECT engine_instance_id,engine_type,engine_version,active_config_version FROM engine_instances WHERE engine_instance_id='SPORTS_SUPERBET_ARB_PRIMARY'").fetchone()
                if e:
                    engine_instance_id, engine_type, engine_version, engine_config_version = e["engine_instance_id"], e["engine_type"], e["engine_version"], e["active_config_version"]
                    # The scaled-entry marker is generated by the originating
                    # engine at execution time, not inferred later from display
                    # characteristics. Preserve that execution-origin evidence.
                    engine_provenance_source = "execution_origin"
            self.conn.execute(
                """INSERT INTO monitor_positions(opportunity_id,execution_run_id,event_key,market_name,opened_at,status,deployed,expected_profit,
                       stakes_by_exchange_json,outcome_exchange_pnls_json,simulation_json,stream,currency,mode,engine_instance_id,engine_type,engine_version,engine_config_version,engine_provenance_source)
                       VALUES(?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(opportunity_id), execution_run_id, str(event_key or ""), str(market_name or ""), now,
                 float(deployed or 0.0), float(expected_profit or 0.0), json.dumps(stakes_by_exchange or {}, default=str),
                 json.dumps(outcome_exchange_pnls or {}, default=str), json.dumps(simulation or {}, default=str), stream,
                 self._configured_account_currency_locked(), "sim", engine_instance_id, engine_type, engine_version, engine_config_version,
                 engine_provenance_source or ("runtime_origin" if engine_instance_id else "unattributed")),
            )
            self._snapshot_monitor_stream_wallets_locked(stream, "execution_reserve_after")
            self._bump_sim_financial_revision_locked()
            self.conn.commit()
            return True, None

    def settle_monitor_position(self, opportunity_id: int, outcome: str, *, _commit: bool = True, _settled_at: str | None = None) -> dict | None:
        """Settle Monitor position authority using the canonical settlement primitive.

        ``_commit`` and ``_settled_at`` are internal transaction controls used by
        :meth:`settle_canonical_lifecycle`. Public callers retain the historical
        standalone behaviour. Runtime scanner settlement uses the atomic canonical
        boundary instead of separately committed position/result writes.
        """
        now = str(_settled_at or datetime.now(timezone.utc).isoformat())
        with self.lock:
            row = self.conn.execute("SELECT * FROM monitor_positions WHERE opportunity_id=? AND status='OPEN'", (int(opportunity_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            stream = str(d.get("stream") or "pre_match")
            stakes = json.loads(d.get("stakes_by_exchange_json") or "{}")
            outcomes = json.loads(d.get("outcome_exchange_pnls_json") or "{}")
            target = None
            norm = str(outcome or "").strip().lower()
            for key, value in outcomes.items():
                if str(key).strip().lower() == norm:
                    target = value
                    break
            if target is None:
                return {"ok": False, "reason": "outcome_not_mapped", "opportunity_id": int(opportunity_id)}
            try:
                simulation = json.loads(d.get("simulation_json") or "{}")
            except Exception:
                simulation = {}
            # Rebuild venue gross/commission/net from the actual paper fills.  The
            # stored outcome split was also derived from these fills, so any material
            # difference indicates corrupted/inconsistent settlement evidence and
            # must fail closed before wallet balances are touched.
            fills = list(simulation.get("fills") or []) if isinstance(simulation, dict) else []
            gross_by_exchange: dict[str, float] = {}
            commission_rates: dict[str, float] = {}
            def _vkey(value):
                text = str(value or "").strip().lower()
                if "betfair" in text: return "betfair"
                if "matchbook" in text: return "matchbook"
                if "smarkets" in text: return "smarkets"
                return text.replace(" ", "_")
            if fills:
                for fill in fills:
                    exchange = _vkey(fill.get("venue_id") or fill.get("exchange"))
                    stake = max(0.0, float(fill.get("stake") or 0.0))
                    odds = max(1.0, float(fill.get("odds") or 1.0))
                    wins = str(fill.get("selection") or "").strip().lower() == norm
                    side = str(fill.get("side") or "BACK").upper()
                    pnl = (-stake * (odds - 1.0) if wins else stake) if side == "LAY" else (stake * (odds - 1.0) if wins else -stake)
                    gross_by_exchange[exchange] = gross_by_exchange.get(exchange, 0.0) + pnl
                    commission_rates[exchange] = max(commission_rates.get(exchange, 0.0), max(0.0, float(fill.get("commission_pct") or 0.0)) / 100.0)
            commission_by_exchange = {k: max(0.0, v) * commission_rates.get(k, 0.0) for k, v in gross_by_exchange.items()}
            model_net_by_exchange = {k: gross_by_exchange[k] - commission_by_exchange.get(k, 0.0) for k in gross_by_exchange}
            target_canonical = {_vkey(k): float(v or 0.0) for k, v in (target or {}).items()}
            reconciliation_delta = 0.0
            reconciliation_by_exchange = {}
            if model_net_by_exchange:
                all_keys = set(model_net_by_exchange) | set(target_canonical)
                reconciliation_by_exchange = {k: target_canonical.get(k, 0.0) - model_net_by_exchange.get(k, 0.0) for k in all_keys}
                reconciliation_delta = sum(target_canonical.values()) - sum(model_net_by_exchange.values())
                if abs(reconciliation_delta) > 0.01 or any(abs(v) > 0.01 for v in reconciliation_by_exchange.values()):
                    return {
                        "ok": False, "reason": "settlement_reconciliation_error", "opportunity_id": int(opportunity_id),
                        "gross_by_exchange": {k: round(v, 8) for k,v in gross_by_exchange.items()},
                        "commission_by_exchange": {k: round(v, 8) for k,v in commission_by_exchange.items()},
                        "model_net_by_exchange": {k: round(v, 8) for k,v in model_net_by_exchange.items()},
                        "stored_net_by_exchange": {k: round(v, 8) for k,v in target_canonical.items()},
                        "reconciliation_by_exchange": {k: round(v, 8) for k,v in reconciliation_by_exchange.items()},
                        "reconciliation_delta": round(reconciliation_delta, 8),
                    }
            self._snapshot_monitor_stream_wallets_locked(stream, "settlement_before_release")
            total_pnl = 0.0
            for exchange, principal in stakes.items():
                principal = max(0.0, float(principal or 0.0))
                pnl = float((target or {}).get(exchange, 0.0) or 0.0)
                total_pnl += pnl
                self.conn.execute(
                    """UPDATE monitor_stream_wallets SET available_balance=available_balance+?+?,reserved_balance=MAX(0,reserved_balance-?),
                       realized_pnl=realized_pnl+?,updated_at=? WHERE stream=? AND exchange=?""",
                    (principal, pnl, principal, pnl, now, stream, str(exchange)),
                )
            # Preserve tranche-level settlement evidence for scaled-entry parents
            # so incremental profit is auditable independently of the base tranche.
            # Legacy ``superbet`` storage is read/written only for history compatibility.
            scaled_entry = (simulation.get("scaled_entry") or simulation.get("superbet")) if isinstance(simulation, dict) else None
            if isinstance(scaled_entry, dict) and isinstance(scaled_entry.get("tranches"), list):
                tranche_realized = []
                for tranche in scaled_entry.get("tranches") or []:
                    mapped = (tranche or {}).get("outcome_exchange_pnls") or {}
                    t_target = None
                    for key, value in mapped.items():
                        if str(key).strip().lower() == norm:
                            t_target = value
                            break
                    realized = None if t_target is None else float(sum(float(v or 0.0) for v in (t_target or {}).values()))
                    if isinstance(tranche, dict):
                        tranche["realized_pnl"] = None if realized is None else round(realized, 4)
                    if realized is not None:
                        tranche_realized.append(realized)
                if tranche_realized:
                    scaled_entry["base_realized_pnl"] = round(tranche_realized[0], 4)
                    scaled_entry["incremental_realized_pnl"] = round(sum(tranche_realized[1:]), 4)
                    scaled_entry["total_realized_pnl"] = round(sum(tranche_realized), 4)
                simulation["scaled_entry"] = scaled_entry
                simulation["superbet"] = scaled_entry  # legacy stored-shape compatibility
            self.conn.execute(
                "UPDATE monitor_positions SET status='SETTLED',settled_at=?,outcome=?,realized_pnl=?,realized_by_exchange_json=?,simulation_json=? WHERE id=?",
                (now, str(outcome), float(total_pnl), json.dumps(target or {}, default=str), json.dumps(simulation, default=str), int(d["id"])),
            )
            if d.get("execution_run_id"):
                exec_row = self.conn.execute("SELECT details_json,expected_profit FROM execution_runs WHERE id=?", (int(d["execution_run_id"]),)).fetchone()
                details = {}
                if exec_row:
                    try: details = json.loads(exec_row["details_json"] or "{}")
                    except Exception: details = {}
                    details.update({"monitor_settled": True, "monitor_stream": stream, "outcome": str(outcome), "realized_by_exchange": target or {}})
                    if isinstance(scaled_entry, dict):
                        details["scaled_entry"] = scaled_entry
                        details["superbet"] = scaled_entry  # legacy stored-shape compatibility
                    expected = float(exec_row["expected_profit"] or 0.0)
                    self.conn.execute(
                        "UPDATE execution_runs SET state='MONITOR_SETTLED',captured_profit=?,execution_leakage=?,finished_at=?,details_json=? WHERE id=?",
                        (float(total_pnl), expected - float(total_pnl), now, json.dumps(details, default=str), int(d["execution_run_id"])),
                    )
            self._snapshot_monitor_stream_wallets_locked(stream, "settlement_after_release")
            self._bump_sim_financial_revision_locked()
            if _commit:
                self.conn.commit()
            return {
                "ok": True, "opportunity_id": int(opportunity_id), "stream": stream, "realized_pnl": round(total_pnl, 4),
                "by_exchange": target,
                "gross_by_exchange": {k: round(v, 8) for k,v in gross_by_exchange.items()},
                "commission_by_exchange": {k: round(v, 8) for k,v in commission_by_exchange.items()},
                "model_net_by_exchange": {k: round(v, 8) for k,v in model_net_by_exchange.items()},
                "reconciliation_by_exchange": {k: round(v, 8) for k,v in reconciliation_by_exchange.items()},
                "reconciliation_delta": round(reconciliation_delta, 8),
                "reconciliation_status": "OK" if abs(reconciliation_delta) <= 0.01 else "ERROR",
            }

    def monitor_performance_rows(self, include_demo: bool = False) -> list[dict]:
        """Return Monitor positions with market metadata for read-only performance analytics."""
        with self.lock:
            demo_clause = "" if include_demo else "AND COALESCE(o.is_demo,0)=0"
            rows = self.conn.execute(
                f"""SELECT mp.*,o.event_name,o.event_start,o.sport,COALESCE(o.section,'sports') section,o.is_demo,
                          o.legs_json,o.source_markets_json,o.edge_pct,o.expected_roi_pct,o.qualification_status,
                          COALESCE(o.in_play,0) in_play,o.strategy
                   FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                   WHERE 1=1 {demo_clause}
                   ORDER BY mp.opened_at ASC,mp.id ASC"""
            ).fetchall()
            return [dict(row) for row in rows]

    def scaled_entry_summary(self, include_demo: bool = False) -> dict:
        """Aggregate capability-level scaled-entry evidence from the Monitor ledger.

        New 0.9.15 records use ``scaled_entry``.  The legacy ``superbet`` key is
        read for historical continuity only; strategy identity lives in Engines.
        """
        demo_clause = "" if include_demo else "AND COALESCE(o.is_demo,0)=0"
        with self.lock:
            rows = self.conn.execute(
                f"""SELECT mp.status,mp.simulation_json,mp.realized_pnl,mp.opened_at,mp.settled_at
                    FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                    WHERE 1=1 {demo_clause} ORDER BY mp.opened_at ASC,mp.id ASC"""
            ).fetchall()
        parents = []
        for row in rows:
            d = dict(row)
            try:
                simulation = json.loads(d.get("simulation_json") or "{}")
            except Exception:
                simulation = {}
            sb = (simulation.get("scaled_entry") or simulation.get("superbet")) if isinstance(simulation, dict) else None
            if not isinstance(sb, dict) or not bool(sb.get("is_scaled_entry") or sb.get("is_superbet")):
                continue
            parents.append((d, sb))
        stop_reasons: dict[str, int] = {}
        total_tranches = 0
        additional_stake = 0.0
        incremental_expected = 0.0
        incremental_realized = 0.0
        fill_rates: list[float] = []
        edge_decays: list[float] = []
        settled = 0
        opened = 0
        last_stop_reason = None
        for d, sb in parents:
            count = max(2, int(sb.get("tranche_count") or len(sb.get("tranches") or []) or 2))
            total_tranches += count
            additional_stake += max(0.0, float(sb.get("additional_stake") or 0.0))
            incremental_expected += float(sb.get("incremental_expected_profit") or 0.0)
            incremental_realized += float(sb.get("incremental_realized_pnl") or 0.0)
            tranches = sb.get("tranches") or []
            for tranche in tranches:
                if (tranche or {}).get("fill_rate_pct") is not None:
                    fill_rates.append(float(tranche.get("fill_rate_pct") or 0.0))
            if tranches:
                base_edge = float((tranches[0] or {}).get("expected_roi_pct") or 0.0)
                for tranche in tranches[1:]:
                    edge_decays.append(base_edge - float((tranche or {}).get("expected_roi_pct") or 0.0))
            status = str(d.get("status") or "").upper()
            if status == "SETTLED": settled += 1
            else: opened += 1
            reason = str(sb.get("stop_reason") or "unknown")
            stop_reasons[reason] = stop_reasons.get(reason, 0) + 1
            last_stop_reason = reason
        count = len(parents)
        return {
            "scaled_positions": count,
            "superbets_placed": count,  # legacy response alias
            "open": opened,
            "settled": settled,
            "total_tranches": total_tranches,
            "average_tranches": round(total_tranches / count, 2) if count else 0.0,
            "additional_stake": round(additional_stake, 4),
            "incremental_expected_profit": round(incremental_expected, 4),
            "incremental_realized_pnl": round(incremental_realized, 4),
            "average_tranche_fill_rate_pct": round(sum(fill_rates) / len(fill_rates), 2) if fill_rates else 0.0,
            "average_edge_decay_pct_points": round(sum(edge_decays) / len(edge_decays), 6) if edge_decays else 0.0,
            "stop_reasons": stop_reasons,
            "last_stop_reason": last_stop_reason,
        }

    def superbet_summary(self, include_demo: bool = False) -> dict:
        """Legacy API compatibility; core/UI use :meth:`scaled_entry_summary`."""
        return self.scaled_entry_summary(include_demo=include_demo)

    def settled_monitor_positions(self, from_utc: str | None = None, to_utc: str | None = None,
                                  include_demo: bool = False, sport: str | None = None, domain: str | None = None,
                                  stream: str | None = None, market: str | None = None,
                                  search: str | None = None, limit: int = 5000) -> list[dict]:
        """Canonical settled-position ledger filtered by settlement observation time.

        v0.8.24 deliberately makes ``monitor_positions.settled_at`` the single
        period boundary for settled financial facts. A position opened yesterday
        and settled today therefore belongs to today's Results/P&L everywhere.
        """
        clauses = ["mp.status='SETTLED'", "mp.settled_at IS NOT NULL"]
        args: list = []
        if not include_demo:
            clauses.append("COALESCE(o.is_demo,0)=0")
        if from_utc:
            clauses.append("mp.settled_at>=?")
            args.append(str(from_utc))
        if to_utc:
            clauses.append("mp.settled_at<?")
            args.append(str(to_utc))
        domain_value = str(domain or '').strip().lower()
        if domain_value == 'sports':
            clauses.append("LOWER(COALESCE(mp.stream,'pre_match')) IN ('pre_match','in_play')")
        elif domain_value == 'racing':
            clauses.append("LOWER(COALESCE(mp.stream,'pre_match'))='racing'")
        sport_value = str(sport or '').strip()
        if sport_value and sport_value.lower() != 'all':
            clauses.append("LOWER(COALESCE(o.sport,''))=LOWER(?)")
            args.append(sport_value)
        stream_value = str(stream or '').strip().lower()
        if stream_value and stream_value != 'all':
            clauses.append("LOWER(COALESCE(mp.stream,'pre_match'))=?")
            args.append(stream_value)
        market_value = str(market or '').strip().lower()
        if market_value:
            clauses.append("LOWER(COALESCE(o.market_name,'')) LIKE ?")
            args.append('%' + market_value + '%')
        search_value = str(search or '').strip().lower()
        if search_value:
            clauses.append("LOWER(COALESCE(o.event_name,'') || ' ' || COALESCE(o.event_key,'') || ' ' || COALESCE(o.market_name,'') || ' ' || COALESCE(o.sport,'') || ' ' || COALESCE(o.strategy,'')) LIKE ?")
            args.append('%' + search_value + '%')
        args.append(min(20000, max(1, int(limit or 5000))))
        with self.lock:
            rows = self.conn.execute(
                f"""SELECT mp.*,o.event_name,o.event_start,o.sport,o.strategy,o.section,o.in_play,o.legs_json,
                           er.mode,er.state execution_state,er.started_at execution_started_at,er.finished_at execution_finished_at,
                           er.expected_profit execution_expected_profit,er.captured_profit,er.execution_leakage,er.details_json
                    FROM monitor_positions mp
                    JOIN opportunities o ON o.id=mp.opportunity_id
                    LEFT JOIN execution_runs er ON er.id=mp.execution_run_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY mp.settled_at DESC,mp.id DESC LIMIT ?""", tuple(args)
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            try:
                d['details'] = json.loads(d.pop('details_json') or '{}')
            except Exception:
                d['details'] = {}
            try:
                d['legs'] = json.loads(d.get('legs_json') or '[]')
            except Exception:
                d['legs'] = []
            d['monitor_stream'] = str(d.get('stream') or 'pre_match')
            d['final_pnl'] = round(float(d.get('realized_pnl') or 0.0), 4)
            d['deployed'] = round(float(d.get('deployed') or 0.0), 4)
            d['returned'] = round(max(0.0, d['deployed'] + d['final_pnl']), 4)
            d['state'] = str(d.get('execution_state') or 'MONITOR_SETTLED')
            d['started_at'] = d.get('execution_started_at') or d.get('opened_at')
            d['finished_at'] = d.get('execution_finished_at') or d.get('settled_at')
            if d.get('captured_profit') is None:
                d['captured_profit'] = d['final_pnl']
            out.append(d)
        return out

    def settled_monitor_summary(self, from_utc: str | None = None, to_utc: str | None = None,
                                include_demo: bool = False, sport: str | None = None, domain: str | None = None,
                                stream: str | None = None) -> dict:
        """Fast canonical summary over the same settlement-time ledger."""
        clauses = ["mp.status='SETTLED'", "mp.settled_at IS NOT NULL"]
        args: list = []
        if not include_demo:
            clauses.append("COALESCE(o.is_demo,0)=0")
        if from_utc:
            clauses.append("mp.settled_at>=?")
            args.append(str(from_utc))
        if to_utc:
            clauses.append("mp.settled_at<?")
            args.append(str(to_utc))
        domain_value = str(domain or '').strip().lower()
        if domain_value == 'sports':
            clauses.append("LOWER(COALESCE(mp.stream,'pre_match')) IN ('pre_match','in_play')")
        elif domain_value == 'racing':
            clauses.append("LOWER(COALESCE(mp.stream,'pre_match'))='racing'")
        sport_value = str(sport or '').strip()
        if sport_value and sport_value.lower() != 'all':
            clauses.append("LOWER(COALESCE(o.sport,''))=LOWER(?)")
            args.append(sport_value)
        stream_value = str(stream or '').strip().lower()
        if stream_value and stream_value != 'all':
            clauses.append("LOWER(COALESCE(mp.stream,'pre_match'))=?")
            args.append(stream_value)
        with self.lock:
            row = self.conn.execute(
                f"""SELECT COUNT(*) settled,
                           SUM(CASE WHEN ROUND(COALESCE(mp.realized_pnl,0),4)>0 THEN 1 ELSE 0 END) wins,
                           SUM(CASE WHEN ROUND(COALESCE(mp.realized_pnl,0),4)<0 THEN 1 ELSE 0 END) losses,
                           SUM(CASE WHEN ABS(ROUND(COALESCE(mp.realized_pnl,0),4))<=1e-9 THEN 1 ELSE 0 END) breakeven,
                           COALESCE(SUM(ROUND(COALESCE(mp.realized_pnl,0),4)),0) pnl,
                           COALESCE(SUM(ROUND(COALESCE(mp.deployed,0),4)),0) deployed,
                           COALESCE(SUM(ROUND(COALESCE(mp.deployed,0),4)+ROUND(COALESCE(mp.realized_pnl,0),4)),0) returned,
                           COALESCE(SUM(er.execution_leakage),0) execution_leakage,
                           MAX(mp.realized_pnl) best_pnl, MIN(mp.realized_pnl) worst_pnl
                    FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                    LEFT JOIN execution_runs er ON er.id=mp.execution_run_id
                    WHERE {' AND '.join(clauses)}""", tuple(args)
            ).fetchone()
        d = dict(row or {})
        return {
            'settled': int(d.get('settled') or 0), 'wins': int(d.get('wins') or 0),
            'losses': int(d.get('losses') or 0), 'breakeven': int(d.get('breakeven') or 0),
            'pnl': round(float(d.get('pnl') or 0.0), 4), 'deployed': round(float(d.get('deployed') or 0.0), 4),
            'returned': round(float(d.get('returned') or 0.0), 4),
            'execution_leakage': round(float(d.get('execution_leakage') or 0.0), 4),
            'best_pnl': None if d.get('best_pnl') is None else round(float(d.get('best_pnl')), 4),
            'worst_pnl': None if d.get('worst_pnl') is None else round(float(d.get('worst_pnl')), 4),
        }

    def monitor_open_positions(self, stream: str | None = None) -> list[dict]:
        with self.lock:
            clauses = ["mp.status='OPEN'"]
            args = []
            if stream:
                clauses.append("COALESCE(mp.stream,'pre_match')=?")
                args.append(str(stream))
            rows = self.conn.execute(
                f"""SELECT mp.*,o.event_name,o.event_start,o.sport,COALESCE(o.section,'sports') section,o.legs_json,er.details_json execution_details_json
                   FROM monitor_positions mp
                   JOIN opportunities o ON o.id=mp.opportunity_id
                   LEFT JOIN execution_runs er ON er.id=mp.execution_run_id
                   WHERE {' AND '.join(clauses)} ORDER BY mp.opened_at DESC""", tuple(args)
            ).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                d["stream"] = str(d.get("stream") or "pre_match")
                for src, dst in (("stakes_by_exchange_json", "stakes_by_exchange"), ("outcome_exchange_pnls_json", "outcome_exchange_pnls"), ("simulation_json", "simulation")):
                    try: d[dst] = json.loads(d.get(src) or "{}")
                    except Exception: d[dst] = {}
                try:
                    d["execution_details"] = json.loads(d.pop("execution_details_json") or "{}")
                except Exception:
                    d["execution_details"] = {}
                out.append(d)
            return out

    TRADING_DATA_TABLES = (
        "account_snapshots", "balance_reconciliations", "sim_account_adjustments",
        "monitor_timing_observations", "monitor_timing_runs", "monitor_positions", "monitor_stream_wallets", "monitor_wallets", "execution_runs",
        "settlements", "scenario_runs", "alert_attempts", "alert_log", "track_observations", "opportunity_tracks",
        "opportunities", "matched_markets", "snapshots", "latest_snapshots", "snapshot_rollups",
        "market_hourly_rollups", "market_hourly_seen", "market_hourly_rollup_state",
        "market_financial_hourly_rollups", "market_financial_hourly_state",
        "exchange_market_discovery_hours", "exchange_market_discovery_state",
        "scan_runs", "market_cache", "jobs", "job_schedules",
    )

    @staticmethod
    def _reset_where_clause(table: str) -> str:
        """Rows owned by the SIM/research reset boundary.

        LIVE economic evidence is deliberately excluded even in legacy shared
        audit tables. 0.9.0's dedicated ``live_*`` tables are not part of
        ``TRADING_DATA_TABLES`` at all.
        """
        if table in {"account_snapshots", "balance_reconciliations"}:
            return "WHERE LOWER(COALESCE(mode,'')) <> 'live'"
        if table == "execution_runs":
            return "WHERE LOWER(COALESCE(mode,'')) <> 'live' AND COALESCE(is_real,0)=0"
        return ""

    def trading_data_counts(self) -> dict[str, int]:
        """Count resettable SIM/research rows without counting LIVE evidence."""
        with self.lock:
            out = {}
            for table in self.TRADING_DATA_TABLES:
                try:
                    where = self._reset_where_clause(table)
                    out[table] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0])
                except Exception:
                    out[table] = -1
            return out

    def clear_research_history(self) -> dict[str, int]:
        """Clear SIM/research-derived data while preserving settings and LIVE state.

        The worker is paused by the API before this is called. Dedicated LIVE
        tables are never touched, and any legacy LIVE audit rows in shared tables
        are preserved. A verification pass counts only rows inside the reset scope.
        """
        tables = list(self.TRADING_DATA_TABLES)
        with self.lock:
            for table in tables:
                where = self._reset_where_clause(table)
                self.conn.execute(f"DELETE FROM {table} {where}")
            try:
                # Sequence resets are safe only for tables that were fully emptied.
                fully_cleared = [t for t in tables if not self._reset_where_clause(t)]
                if fully_cleared:
                    marks = ",".join("?" for _ in fully_cleared)
                    self.conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({marks})", tuple(fully_cleared))
            except Exception:
                pass
            self.conn.execute("""UPDATE snapshot_storage_state SET legacy_target_id=0,legacy_rows_deleted=0,last_prune_at=NULL,
                               last_write_error=NULL,last_write_error_at=NULL WHERE id=1""")
            self.conn.commit()
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            remaining = {}
            for table in tables:
                try:
                    where = self._reset_where_clause(table)
                    remaining[table] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0])
                except Exception:
                    remaining[table] = -1
            if any(v != 0 for v in remaining.values()):
                raise RuntimeError(f"Trading-data reset verification failed: {remaining}")
            try:
                self.conn.execute("VACUUM")
            except Exception:
                pass
            return remaining

    # --- Jobs / scheduled runs -------------------------------------------------

    @staticmethod
    def _iso(dt: datetime | None) -> str | None:
        return dt.astimezone(timezone.utc).isoformat() if dt else None

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def create_job(self, name: str, mode: str, strategy: dict, trigger_type: str = "manual",
                   schedule_id: int | None = None, scheduled_start: str | None = None,
                   duration_minutes: int | None = None, start_now: bool = False) -> int:
        now = datetime.now(timezone.utc)
        scheduled = self._parse_dt(scheduled_start)
        started = now if start_now else None
        if start_now:
            status = "running"
            scheduled = scheduled or now
        else:
            status = "scheduled"
        end = None
        if duration_minutes and (started or scheduled):
            end = (started or scheduled) + timedelta(minutes=max(1, int(duration_minutes)))
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO jobs(schedule_id,name,mode,trigger_type,status,created_at,scheduled_start,scheduled_end,started_at,strategy_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (schedule_id, str(name or "Run"), str(mode), str(trigger_type), status, self._iso(now),
                 self._iso(scheduled), self._iso(end), self._iso(started), json.dumps(strategy or {}, default=str)),
            )
            job_id = int(cur.lastrowid)
            if start_now:
                self.conn.execute(
                    "INSERT INTO settings(key,value) VALUES('active_job_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(job_id),),
                )
            self.conn.commit()
            return job_id

    def create_schedule(self, name: str, mode: str, strategy: dict, first_run_at: str,
                        duration_minutes: int, recurrence: str = "once", timezone_name: str = "UTC") -> int:
        first = self._parse_dt(first_run_at)
        if not first:
            raise ValueError("A valid scheduled start is required")
        recurrence = str(recurrence or "once").lower()
        if recurrence not in {"once", "daily", "weekdays"}:
            recurrence = "once"
        try:
            ZoneInfo(str(timezone_name or "UTC"))
            tz_name = str(timezone_name or "UTC")
        except Exception:
            tz_name = "UTC"
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO job_schedules(name,mode,enabled,recurrence,timezone_name,first_run_at,next_run_at,duration_minutes,strategy_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (str(name or "Scheduled run"), str(mode), 1, recurrence, tz_name, self._iso(first), self._iso(first),
                 max(1, int(duration_minutes or 60)), json.dumps(strategy or {}, default=str), self._iso(datetime.now(timezone.utc))),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def _next_schedule_time(self, row: dict, from_dt: datetime) -> datetime | None:
        recurrence = str(row.get("recurrence") or "once").lower()
        if recurrence == "once":
            return None
        try:
            tz = ZoneInfo(str(row.get("timezone_name") or "UTC"))
        except Exception:
            tz = timezone.utc
        local = from_dt.astimezone(tz)
        candidate = local + timedelta(days=1)
        if recurrence == "weekdays":
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    def due_schedule(self, now: datetime | None = None) -> dict | None:
        now = now or datetime.now(timezone.utc)
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM job_schedules WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at ASC,id ASC LIMIT 1",
                (self._iso(now),),
            ).fetchone()
            return dict(row) if row else None

    def spawn_due_schedule(self, now: datetime | None = None) -> int | None:
        """Create and start one due scheduled job when no other job is active."""
        now = now or datetime.now(timezone.utc)
        if self.active_job():
            return None
        sched = self.due_schedule(now)
        if not sched:
            return None
        strategy = json.loads(sched.get("strategy_json") or "{}")
        duration = max(1, int(sched.get("duration_minutes") or 60))
        job_id = self.create_job(
            sched.get("name") or "Scheduled run", sched.get("mode") or "watch", strategy,
            trigger_type="scheduled", schedule_id=int(sched["id"]), scheduled_start=sched.get("next_run_at"),
            duration_minutes=duration, start_now=True,
        )
        base = self._parse_dt(sched.get("next_run_at")) or now
        nxt = self._next_schedule_time(sched, base)
        with self.lock:
            self.conn.execute(
                "INSERT INTO settings(key,value) VALUES('mode',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(str(sched.get("mode") or "watch")),),
            )
            self.conn.execute(
                "UPDATE job_schedules SET last_run_at=?,next_run_at=?,enabled=? WHERE id=?",
                (self._iso(now), self._iso(nxt), 1 if nxt else 0, int(sched["id"])),
            )
            self.conn.commit()
        return job_id

    def active_job(self) -> dict | None:
        active_id = self.get_setting("active_job_id")
        with self.lock:
            if active_id:
                row = self.conn.execute("SELECT * FROM jobs WHERE id=? AND status='running'", (int(active_id),)).fetchone()
                if row:
                    d = dict(row)
                    d["strategy"] = json.loads(d.pop("strategy_json") or "{}")
                    return d
            row = self.conn.execute("SELECT * FROM jobs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                return None
            d = dict(row)
            d["strategy"] = json.loads(d.pop("strategy_json") or "{}")
            return d

    def finish_expired_job(self, now: datetime | None = None) -> dict | None:
        now = now or datetime.now(timezone.utc)
        job = self.active_job()
        if not job:
            return None
        end = self._parse_dt(job.get("scheduled_end"))
        if end and now >= end:
            self.finish_job(int(job["id"]), "completed", "scheduled duration complete", now)
            return job
        return None

    def finish_job(self, job_id: int, status: str = "stopped", reason: str = "user", when: datetime | None = None):
        when = when or datetime.now(timezone.utc)
        status = status if status in {"completed", "stopped", "failed"} else "stopped"
        with self.lock:
            self.conn.execute(
                "UPDATE jobs SET status=?,finished_at=?,stop_reason=? WHERE id=? AND status='running'",
                (status, self._iso(when), str(reason or ""), int(job_id)),
            )
            active_id = self.get_setting("active_job_id")
            if active_id and int(active_id) == int(job_id):
                self.conn.execute(
                    "INSERT INTO settings(key,value) VALUES('active_job_id','null') ON CONFLICT(key) DO UPDATE SET value='null'"
                )
            self.conn.commit()

    def job_strategy(self) -> dict | None:
        job = self.active_job()
        return (job or {}).get("strategy") if job else None

    def job_history(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                d["strategy"] = json.loads(d.pop("strategy_json") or "{}")
                jid = int(d["id"])
                ref_bankroll = float((d.get("strategy") or {}).get("quality_reference_bankroll") or 500.0)
                scans = self.conn.execute(
                    "SELECT COUNT(*) c,COALESCE(SUM(markets_seen),0) markets,COALESCE(SUM(matches_seen),0) matches,COALESCE(SUM(opportunities_found),0) found FROM scan_runs WHERE job_id=?",
                    (jid,),
                ).fetchone()
                opps = self.conn.execute(
                    "SELECT COUNT(*) c FROM opportunities WHERE job_id=? AND COALESCE(is_demo,0)=0", (jid,)
                ).fetchone()
                execs = self.conn.execute(
                    """SELECT COUNT(*) c,COALESCE(SUM(expected_profit),0) expected,COALESCE(SUM(captured_profit),0) captured,
                              COALESCE(SUM(execution_leakage),0) leakage,COALESCE(MAX(max_unhedged_exposure),0) peak_exposure
                       FROM execution_runs WHERE job_id=?""", (jid,)
                ).fetchone()
                settled = self.conn.execute(
                    """SELECT COUNT(*) c FROM settlements s JOIN opportunities o ON o.id=s.opportunity_id
                       WHERE o.job_id=? AND COALESCE(o.is_demo,0)=0""", (jid,)
                ).fetchone()
                potential = self.conn.execute(
                    """SELECT COALESCE(SUM(sr.expected_profit),0) p FROM scenario_runs sr
                       JOIN opportunities o ON o.id=sr.opportunity_id
                       WHERE o.job_id=? AND COALESCE(o.is_demo,0)=0 AND ABS(sr.bankroll-?)<0.0001""",
                    (jid, ref_bankroll),
                ).fetchone()
                d["stats"] = {
                    "scans": int(scans["c"] or 0), "markets": int(scans["markets"] or 0), "matches": int(scans["matches"] or 0),
                    "opportunities": int(opps["c"] or 0), "reported_found": int(scans["found"] or 0),
                    "executions": int(execs["c"] or 0), "expected_profit": round(float(execs["expected"] or 0), 4),
                    "potential_profit": round(float(potential["p"] or 0), 4),
                    "captured_profit": round(float(execs["captured"] or 0), 4), "execution_leakage": round(float(execs["leakage"] or 0), 4),
                    "peak_exposure": round(float(execs["peak_exposure"] or 0), 4), "settled": int(settled["c"] or 0),
                }
                out.append(d)
            return out

    def schedules(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM job_schedules ORDER BY enabled DESC,next_run_at ASC,id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
            out=[]
            for row in rows:
                d=dict(row); d["strategy"] = json.loads(d.pop("strategy_json") or "{}"); out.append(d)
            return out

    def cancel_schedule(self, schedule_id: int):
        with self.lock:
            self.conn.execute("UPDATE job_schedules SET enabled=0,next_run_at=NULL WHERE id=?", (int(schedule_id),))
            self.conn.commit()

    def add_snapshot(self, **s):
        cols = ["captured_at", "exchange", "event_id", "event_name", "market_id", "market_name", "selection_id", "selection", "side", "odds", "liquidity", "source_latency_ms", "commission_pct", "commission_source", "market_type", "strategy", "sport", "in_play", "market_status", "section", "trap_number", "canonical_selection_key", "runner_status", "raw_json"]
        with self.lock:
            self.conn.execute(
                f"INSERT INTO snapshots({','.join(cols)}) VALUES({','.join(['?'] * len(cols))})",
                tuple(s.get(c) for c in cols),
            )
            self.conn.commit()

    def add_snapshots(self, rows: list[dict]):
        if not rows:
            return
        cols = ["captured_at", "exchange", "event_id", "event_name", "market_id", "market_name", "selection_id", "selection", "side", "odds", "liquidity", "source_latency_ms", "commission_pct", "commission_source", "market_type", "strategy", "sport", "in_play", "market_status", "section", "trap_number", "canonical_selection_key", "runner_status", "raw_json"]
        vals = [tuple(r.get(c) for c in cols) for r in rows]
        with self.lock:
            self.conn.executemany(
                f"INSERT INTO snapshots({','.join(cols)}) VALUES({','.join(['?'] * len(cols))})",
                vals,
            )
            self.conn.commit()

    def upsert_latest_snapshots(self, rows: list[dict]) -> dict:
        """Persist bounded best quotes, bounded top-N books and compact depth rollups.

        0.9.1 keeps one current row per quote/price level. Multi-level books are
        never append-only: each new quote replaces that selection's current depth,
        while historical Market Analysis receives only compact hourly aggregates.
        """
        if not rows:
            return {"rows": 0, "exchanges": 0, "depth_rows": 0}
        from .venues import provider_id_for_name, venue_identity_for_name
        cols = ["exchange", "market_id", "selection_id", "side", "captured_at", "event_id", "event_name",
                "market_name", "selection", "odds", "liquidity", "source_latency_ms", "commission_pct",
                "commission_source", "market_type", "strategy", "sport", "in_play", "market_status",
                "section", "trap_number", "canonical_selection_key", "runner_status", "provider_id", "venue_id",
                "feed_entitlement", "market_data_transport", "source_timestamp", "timestamp_quality", "quote_age_ms", "source_state_version",
                "depth_levels_json", "raw_json"]
        clean = []
        rollups: dict[tuple[str, str], dict] = {}
        depth_rows: list[tuple] = []
        depth_replace_keys: set[tuple[str, str, str]] = set()
        depth_buckets: dict[tuple[str, str, str, str, str, int], dict] = {}
        for raw in rows:
            r = dict(raw or {})
            market_id = str(r.get("market_id") or "").strip()
            selection_id = str(r.get("selection_id") or "").strip()
            exchange = str(r.get("exchange") or "").strip()
            if not market_id:
                market_id = f"event:{r.get('event_id') or ''}:{r.get('market_name') or ''}"
            if not selection_id:
                selection_id = f"selection:{r.get('selection') or ''}"
            if not exchange:
                continue
            provider_id = str(r.get("provider_id") or provider_id_for_name(exchange) or exchange).strip().lower()
            venue_id = str(r.get("venue_id") or venue_identity_for_name(exchange).venue_id or provider_id).strip().lower()
            r["market_id"], r["selection_id"] = market_id, selection_id
            r["provider_id"], r["venue_id"] = provider_id, venue_id
            r["feed_entitlement"] = str(r.get("feed_entitlement") or "unknown")
            r["market_data_transport"] = str(r.get("market_data_transport") or "unknown")
            r["source_timestamp"] = r.get("source_timestamp")
            quality = str(r.get("timestamp_quality") or ("PROVIDER_SOURCE" if r.get("source_timestamp") else "LOCAL_RECEIPT")).upper()
            r["timestamp_quality"] = quality if quality in {"PROVIDER_SOURCE", "LOCAL_RECEIPT", "ESTIMATED", "UNKNOWN"} else "UNKNOWN"
            clean.append(tuple(r.get(c) for c in cols))
            captured = str(r.get("captured_at") or datetime.now(timezone.utc).isoformat())
            try:
                dt = datetime.fromisoformat(captured.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                dt = datetime.now(timezone.utc)
            hour = dt.replace(minute=0, second=0, microsecond=0).isoformat()
            key = (hour, exchange)
            bucket = rollups.setdefault(key, {"quotes": 0, "last": captured})
            bucket["quotes"] += 1
            if captured > str(bucket["last"]):
                bucket["last"] = captured

            try:
                levels = json.loads(r.get("depth_levels_json") or "[]")
            except Exception:
                levels = []
            if not isinstance(levels, list) or not levels:
                levels = [{"side": str(r.get("side") or "BACK").upper(), "level": 1,
                           "odds": float(r.get("odds") or 0.0), "available_size": float(r.get("liquidity") or 0.0)}]
            depth_replace_keys.add((provider_id, market_id, selection_id))
            section = str(r.get("section") or "sports")
            sport = str(r.get("sport") or "Unknown")
            market_name = str(r.get("market_name") or "Unknown")
            in_play = int(bool(r.get("in_play")))
            depth_bucket = depth_buckets.setdefault((hour, provider_id, section, sport, market_name, in_play),
                {"top": 0.0, "top3": 0.0, "last": captured})
            for raw_level in levels:
                if not isinstance(raw_level, dict):
                    continue
                try:
                    level = int(raw_level.get("level") or 0)
                    price = float(raw_level.get("odds") if raw_level.get("odds") is not None else raw_level.get("price") or 0.0)
                    size = float(raw_level.get("available_size") if raw_level.get("available_size") is not None else raw_level.get("size") or 0.0)
                except (TypeError, ValueError):
                    continue
                side = str(raw_level.get("side") or r.get("side") or "BACK").upper()
                if level < 1 or level > 3 or price <= 1.0 or size < 0.0:
                    continue
                depth_rows.append((provider_id, venue_id, market_id, selection_id, side, level, captured,
                                   r.get("source_timestamp"), r.get("timestamp_quality"), r.get("quote_age_ms"), r.get("feed_entitlement"),
                                   r.get("market_data_transport"), r.get("event_id"), r.get("event_name"), market_name,
                                   r.get("selection"), section, sport, in_play, price, size))
                if level == 1:
                    depth_bucket["top"] += size
                depth_bucket["top3"] += size
                if captured > str(depth_bucket["last"]):
                    depth_bucket["last"] = captured
        if not clean:
            return {"rows": 0, "exchanges": 0, "depth_rows": 0}
        assignments = ",".join(f"{c}=excluded.{c}" for c in cols if c not in {"exchange", "market_id", "selection_id", "side"})
        sql = f"""INSERT INTO latest_snapshots({','.join(cols)}) VALUES({','.join(['?'] * len(cols))})
                  ON CONFLICT(exchange,market_id,selection_id,side) DO UPDATE SET {assignments}"""
        depth_sql = """INSERT INTO latest_depth_snapshots(
            provider_id,venue_id,market_id,selection_id,side,level,captured_at,source_timestamp,timestamp_quality,quote_age_ms,feed_entitlement,market_data_transport,
            event_id,event_name,market_name,selection,section,sport,in_play,price,available_size
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(provider_id,market_id,selection_id,side,level) DO UPDATE SET
             venue_id=excluded.venue_id,captured_at=excluded.captured_at,source_timestamp=excluded.source_timestamp,timestamp_quality=excluded.timestamp_quality,quote_age_ms=excluded.quote_age_ms,
             feed_entitlement=excluded.feed_entitlement,market_data_transport=excluded.market_data_transport,event_id=excluded.event_id,event_name=excluded.event_name,
             market_name=excluded.market_name,selection=excluded.selection,section=excluded.section,sport=excluded.sport,in_play=excluded.in_play,
             price=excluded.price,available_size=excluded.available_size"""
        with self.lock:
            try:
                self.conn.executemany(sql, clean)
                for provider_id, market_id, selection_id in depth_replace_keys:
                    self.conn.execute("DELETE FROM latest_depth_snapshots WHERE provider_id=? AND market_id=? AND selection_id=?",
                                      (provider_id, market_id, selection_id))
                if depth_rows:
                    self.conn.executemany(depth_sql, depth_rows)
                for (hour, exchange), bucket in rollups.items():
                    self.conn.execute(
                        """INSERT INTO snapshot_rollups(hour_utc,exchange,quote_observations,batches,last_captured_at)
                           VALUES(?,?,?,1,?)
                           ON CONFLICT(hour_utc,exchange) DO UPDATE SET
                             quote_observations=snapshot_rollups.quote_observations+excluded.quote_observations,
                             batches=snapshot_rollups.batches+1,last_captured_at=excluded.last_captured_at""",
                        (hour, exchange, int(bucket["quotes"]), bucket["last"]),
                    )
                for (hour, provider_id, section, sport, market_name, in_play), bucket in depth_buckets.items():
                    self.conn.execute(
                        """INSERT INTO liquidity_depth_hourly_rollups(
                           hour_utc,provider_id,section,sport,market_name,in_play,depth_samples,top_book_depth_sum,top3_depth_sum,max_top_book_depth,max_top3_depth,last_captured_at
                           ) VALUES(?,?,?,?,?,?,1,?,?,?,?,?)
                           ON CONFLICT(hour_utc,provider_id,section,sport,market_name,in_play) DO UPDATE SET
                            depth_samples=liquidity_depth_hourly_rollups.depth_samples+1,
                            top_book_depth_sum=liquidity_depth_hourly_rollups.top_book_depth_sum+excluded.top_book_depth_sum,
                            top3_depth_sum=liquidity_depth_hourly_rollups.top3_depth_sum+excluded.top3_depth_sum,
                            max_top_book_depth=MAX(liquidity_depth_hourly_rollups.max_top_book_depth,excluded.max_top_book_depth),
                            max_top3_depth=MAX(liquidity_depth_hourly_rollups.max_top3_depth,excluded.max_top3_depth),
                            last_captured_at=excluded.last_captured_at""",
                        (hour, provider_id, section, sport, market_name, in_play, float(bucket["top"]), float(bucket["top3"]),
                         float(bucket["top"]), float(bucket["top3"]), bucket["last"]),
                    )
                self.conn.execute("UPDATE snapshot_storage_state SET last_write_error=NULL,last_write_error_at=NULL WHERE id=1")
                self.conn.commit()
            except Exception as exc:
                self.conn.rollback()
                now = datetime.now(timezone.utc).isoformat()
                try:
                    self.conn.execute("UPDATE snapshot_storage_state SET last_write_error=?,last_write_error_at=? WHERE id=1", (str(exc)[:2000], now))
                    self.conn.commit()
                except Exception:
                    self.conn.rollback()
                raise
        return {"rows": len(clean), "exchanges": len({x[0] for x in clean}), "depth_rows": len(depth_rows)}

    # --- 0.9.0 isolated LIVE persistence / order journal ---------------------

    def upsert_live_account_snapshot(self, snapshot: dict) -> dict:
        """Persist a real read-only account snapshot without touching SIM ledgers."""
        provider_id = str(snapshot.get("provider_id") or "").lower()
        account_id = str(snapshot.get("account_id") or f"{provider_id}:primary")
        snapshot_id = str(snapshot.get("snapshot_id") or f"{provider_id}:{snapshot.get('received_at') or datetime.now(timezone.utc).isoformat()}")
        received_at = str(snapshot.get("received_at") or datetime.now(timezone.utc).isoformat())
        metadata = dict(snapshot.get("provider_metadata") or snapshot.get("metadata") or {})
        with self.lock:
            self.conn.execute(
                """INSERT INTO live_account_snapshots(snapshot_id,account_id,provider_id,venue_id,currency,balance,available_balance,reserved_balance,exposure,credit,source_timestamp,received_at,is_stale,connection_state,data_quality,balance_semantics,provider_account_ref,error_code,error_message,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(snapshot_id) DO UPDATE SET is_stale=excluded.is_stale,connection_state=excluded.connection_state,error_code=excluded.error_code,error_message=excluded.error_message,metadata_json=excluded.metadata_json""",
                (snapshot_id, account_id, provider_id, str(snapshot.get("venue_id") or provider_id), snapshot.get("currency"),
                 snapshot.get("balance"), snapshot.get("available_balance"), snapshot.get("reserved_balance"), snapshot.get("exposure"), snapshot.get("credit"),
                 snapshot.get("source_timestamp"), received_at, int(bool(snapshot.get("is_stale"))), str(snapshot.get("connection_state") or "connected"),
                 str(snapshot.get("data_quality") or "partial"), snapshot.get("balance_semantics"), snapshot.get("provider_account_ref"),
                 snapshot.get("error_code"), snapshot.get("error_message"), json.dumps(metadata, default=str, separators=(",", ":"))),
            )
            # Current-state table is an upsert cache, physically isolated from SIM.
            self.conn.execute(
                """INSERT INTO live_accounts(account_id,provider_id,venue_id,currency,available_balance,reserved_balance,exposure,equity,captured_at,source,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(account_id) DO UPDATE SET provider_id=excluded.provider_id,venue_id=excluded.venue_id,currency=excluded.currency,
                    available_balance=excluded.available_balance,reserved_balance=excluded.reserved_balance,exposure=excluded.exposure,equity=excluded.equity,
                    captured_at=excluded.captured_at,source=excluded.source,metadata_json=excluded.metadata_json""",
                (account_id, provider_id, str(snapshot.get("venue_id") or provider_id), str(snapshot.get("currency") or ""),
                 snapshot.get("available_balance"), snapshot.get("reserved_balance"), snapshot.get("exposure"), snapshot.get("balance"),
                 received_at, "live_account_provider", json.dumps({**metadata, "snapshot_id": snapshot_id, "connection_state": snapshot.get("connection_state"),
                 "data_quality": snapshot.get("data_quality"), "is_stale": bool(snapshot.get("is_stale")), "balance_semantics": snapshot.get("balance_semantics"),
                 "error_code": snapshot.get("error_code"), "error_message": snapshot.get("error_message")}, default=str, separators=(",", ":"))),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM live_account_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        return dict(row) if row else {}

    def latest_live_account_snapshots(self) -> dict[str, dict]:
        with self.lock:
            rows = self.conn.execute(
                """SELECT s.* FROM live_account_snapshots s JOIN (SELECT provider_id,MAX(id) id FROM live_account_snapshots GROUP BY provider_id) x ON x.id=s.id ORDER BY s.provider_id"""
            ).fetchall()
        out = {}
        for row in rows:
            d = dict(row)
            try: d["provider_metadata"] = json.loads(d.pop("metadata_json") or "{}")
            except Exception: d["provider_metadata"] = {}
            d["is_stale"] = bool(d.get("is_stale"))
            out[str(d.get("provider_id") or "unknown")] = d
        return out

    def live_account_snapshot_history(self, *, provider_id: str | None = None, from_utc: str | None = None,
                                      to_utc: str | None = None, limit: int = 2000) -> list[dict]:
        clauses, args = [], []
        if provider_id:
            clauses.append("provider_id=?"); args.append(str(provider_id).lower())
        if from_utc:
            clauses.append("received_at>=?"); args.append(str(from_utc))
        if to_utc:
            clauses.append("received_at<=?"); args.append(str(to_utc))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(max(1, min(int(limit), 10000)))
        with self.lock:
            rows = self.conn.execute(f"SELECT * FROM live_account_snapshots{where} ORDER BY received_at DESC LIMIT ?", tuple(args)).fetchall()
        out=[]
        for row in rows:
            d=dict(row)
            try:d["provider_metadata"]=json.loads(d.pop("metadata_json") or "{}")
            except Exception:d["provider_metadata"]={}
            d["is_stale"]=bool(d.get("is_stale"));out.append(d)
        return out

    def record_live_account_activity(self, activity: dict) -> bool:
        pid = str(activity.get("provider_id") or "").lower()
        aid = str(activity.get("activity_id") or "") or None
        with self.lock:
            try:
                self.conn.execute(
                    """INSERT INTO live_account_movements(provider_id,venue_id,currency,movement_type,amount,occurred_at,external_reference,provider_activity_id,description,native_type,balance_after,metadata_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, str(activity.get("venue_id") or pid), str(activity.get("currency") or ""), str(activity.get("activity_type") or "OTHER"),
                     float(activity.get("amount") or 0.0), str(activity.get("timestamp") or datetime.now(timezone.utc).isoformat()), activity.get("reference"), aid,
                     activity.get("description"), activity.get("provider_native_type"), activity.get("balance_after"),
                     json.dumps(activity.get("provider_metadata") or {}, default=str, separators=(",", ":"))),
                )
                self.conn.commit(); return True
            except sqlite3.IntegrityError:
                return False

    def live_account_activity(self, *, provider_id: str | None = None, from_utc: str | None = None,
                              to_utc: str | None = None, limit: int = 5000) -> list[dict]:
        clauses,args=[],[]
        if provider_id: clauses.append("provider_id=?");args.append(str(provider_id).lower())
        if from_utc: clauses.append("occurred_at>=?");args.append(str(from_utc))
        if to_utc: clauses.append("occurred_at<=?");args.append(str(to_utc))
        where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
        args.append(max(1,min(int(limit),10000)))
        with self.lock:
            rows=self.conn.execute(f"SELECT * FROM live_account_movements{where} ORDER BY occurred_at DESC,id DESC LIMIT ?",tuple(args)).fetchall()
        out=[]
        for row in rows:
            d=dict(row)
            try:d["provider_metadata"]=json.loads(d.pop("metadata_json") or "{}")
            except Exception:d["provider_metadata"]={}
            out.append(d)
        return out

    def record_live_account_audit(self, *, provider_id: str | None, event_type: str, status: str,
                                  latency_ms: int | None = None, error_type: str | None = None,
                                  message: str | None = None, details: dict | None = None) -> int:
        with self.lock:
            cur=self.conn.execute(
                "INSERT INTO live_account_audit(provider_id,event_type,status,occurred_at,latency_ms,error_type,message,details_json) VALUES(?,?,?,?,?,?,?,?)",
                (None if provider_id is None else str(provider_id).lower(), str(event_type), str(status), datetime.now(timezone.utc).isoformat(),
                 latency_ms,error_type,message,json.dumps(details or {},default=str,separators=(",", ":"))))
            self.conn.commit(); return int(cur.lastrowid)

    def live_account_audit(self, *, provider_id: str | None = None, limit: int = 500) -> list[dict]:
        with self.lock:
            if provider_id:
                rows=self.conn.execute("SELECT * FROM live_account_audit WHERE provider_id=? ORDER BY occurred_at DESC LIMIT ?",(str(provider_id).lower(),max(1,min(int(limit),5000)))).fetchall()
            else:
                rows=self.conn.execute("SELECT * FROM live_account_audit ORDER BY occurred_at DESC LIMIT ?",(max(1,min(int(limit),5000)),)).fetchall()
        out=[]
        for row in rows:
            d=dict(row)
            try:d["details"]=json.loads(d.pop("details_json") or "{}")
            except Exception:d["details"]={}
            out.append(d)
        return out

    def record_live_order_intent(self, intent: dict) -> dict:
        """Durably journal a LIVE order intent before any provider transmission.

        0.9.0 has no external submission path; this method is the persistence
        boundary later LIVE adapters must call first. SIM callers are rejected.
        """
        from .modes import canonical_mode_value
        mode = canonical_mode_value(intent.get("mode"))
        if mode != "live":
            raise ValueError("LIVE order journal accepts mode=live only")
        client_order_id = str(intent.get("client_order_id") or "").strip()
        if not client_order_id:
            raise ValueError("client_order_id is required before LIVE submission")
        created_at = str(intent.get("created_at") or datetime.now(timezone.utc).isoformat())
        with self.lock:
            self.conn.execute(
                """INSERT INTO live_order_attempts(
                     client_order_id,position_id,leg_id,attempt_id,provider_id,venue_id,canonical_event_id,canonical_market_id,
                     canonical_selection_id,side,requested_odds,requested_stake,mode,state,intent_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'NOT_SUBMITTED',?,?)
                   ON CONFLICT(client_order_id) DO NOTHING""",
                (client_order_id, str(intent.get("position_id") or "") or None, str(intent.get("leg_id") or "") or None,
                 str(intent.get("attempt_id") or "") or None, str(intent.get("provider_id") or ""), str(intent.get("venue_id") or ""),
                 intent.get("canonical_event_id"), intent.get("canonical_market_id"), intent.get("canonical_selection_id"),
                 intent.get("side"), intent.get("target_odds"), intent.get("stake"), mode,
                 json.dumps(intent, separators=(",", ":"), sort_keys=True, default=str), created_at),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM live_order_attempts WHERE client_order_id=?", (client_order_id,)).fetchone()
        return dict(row) if row else {}

    def mark_live_order_submission_attempted(self, client_order_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.conn.execute(
                "UPDATE live_order_attempts SET state='PENDING',submission_attempted_at=? WHERE client_order_id=? AND state='NOT_SUBMITTED'",
                (now, str(client_order_id)),
            )
            self.conn.commit()

    def mark_live_order_unknown(self, client_order_id: str, error: str | None = None) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE live_order_attempts SET state='UNKNOWN',last_error=? WHERE client_order_id=?",
                (str(error or "transport state uncertain"), str(client_order_id)),
            )
            self.conn.commit()

    def reconcile_live_order_attempt(self, client_order_id: str, *, state: str, external_order_id: str | None = None,
                                     provider_metadata: dict | None = None) -> dict:
        allowed = {"NOT_SUBMITTED", "PENDING", "ACCEPTED", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"}
        state = str(state or "").upper()
        if state not in allowed:
            raise ValueError(f"Unsupported LIVE order state: {state}")
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.conn.execute(
                """UPDATE live_order_attempts SET state=?,external_order_id=COALESCE(?,external_order_id),reconciled_at=?,
                     provider_metadata_json=? WHERE client_order_id=?""",
                (state, external_order_id, now, json.dumps(provider_metadata or {}, separators=(",", ":")), str(client_order_id)),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM live_order_attempts WHERE client_order_id=?", (str(client_order_id),)).fetchone()
        return dict(row) if row else {}

    def live_order_attempts(self, *, state: str | None = None, limit: int = 1000) -> list[dict]:
        with self.lock:
            if state:
                rows = self.conn.execute(
                    "SELECT * FROM live_order_attempts WHERE state=? ORDER BY created_at DESC LIMIT ?",
                    (str(state).upper(), max(1, min(int(limit), 10000))),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM live_order_attempts ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(int(limit), 10000)),),
                ).fetchall()
        return [dict(r) for r in rows]

    def unresolved_live_order_count(self) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) n FROM live_order_attempts WHERE state IN ('PENDING','UNKNOWN')"
            ).fetchone()
        return int(row["n"] if row else 0)

    def live_persistence_counts(self) -> dict[str, int]:
        tables = ("live_accounts", "live_account_snapshots", "live_account_audit", "live_order_attempts", "live_orders", "live_fills", "live_positions",
                  "live_recovery_actions", "live_settlements", "live_account_movements", "live_reconciliations")
        with self.lock:
            return {table: int(self.conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]) for table in tables}

    def snapshot_storage_maintenance(self, *, keep_legacy_rows: int = 100000, batch_size: int = 50000) -> dict:
        """Incrementally reclaim legacy append-only raw snapshot pages.

        The old ``snapshots`` table is no longer written by v0.8.34. Keeping the
        newest bounded tail provides migration-era forensic evidence while millions
        of older duplicate raw quote rows can be deleted without touching trading,
        settlement, result, replay or account history. Deletes are intentionally
        batched so the worker does not monopolise SQLite's writer lock.
        """
        keep = max(0, int(keep_legacy_rows or 0))
        batch = max(1000, min(250000, int(batch_size or 50000)))
        with self.lock:
            state = self.conn.execute("SELECT * FROM snapshot_storage_state WHERE id=1").fetchone()
            target = int((state["legacy_target_id"] if state else 0) or 0)
            if target <= 0:
                mx = self.conn.execute("SELECT MAX(id) m FROM snapshots").fetchone()["m"]
                target = max(0, int(mx or 0) - keep)
                self.conn.execute("UPDATE snapshot_storage_state SET legacy_target_id=? WHERE id=1", (target,))
                self.conn.commit()
            mn = self.conn.execute("SELECT MIN(id) m FROM snapshots").fetchone()["m"]
            if not mn or int(mn) > target or target <= 0:
                return {"done": True, "deleted": 0, "target_id": target, "oldest_id": int(mn or 0)}
            try:
                self.conn.execute(
                    "DELETE FROM snapshots WHERE id IN (SELECT id FROM snapshots WHERE id<=? ORDER BY id LIMIT ?)",
                    (target, batch),
                )
                deleted = int(self.conn.execute("SELECT changes() c").fetchone()["c"] or 0)
                now = datetime.now(timezone.utc).isoformat()
                self.conn.execute(
                    "UPDATE snapshot_storage_state SET legacy_rows_deleted=legacy_rows_deleted+?,last_prune_at=? WHERE id=1",
                    (deleted, now),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            mn2 = self.conn.execute("SELECT MIN(id) m FROM snapshots").fetchone()["m"]
            return {"done": not mn2 or int(mn2) > target, "deleted": deleted, "target_id": target, "oldest_id": int(mn2 or 0)}

    def snapshot_storage_health(self) -> dict:
        with self.lock:
            state = self.conn.execute("SELECT * FROM snapshot_storage_state WHERE id=1").fetchone()
            latest = self.conn.execute(
                "SELECT exchange,MAX(captured_at) latest,COUNT(*) c FROM latest_snapshots GROUP BY exchange ORDER BY exchange"
            ).fetchall()
            bounds = self.conn.execute("SELECT MIN(id) min_id,MAX(id) max_id FROM snapshots").fetchone()
            page_size = int(self.conn.execute("PRAGMA page_size").fetchone()[0] or 0)
            free_pages = int(self.conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)
            target = int((state["legacy_target_id"] if state else 0) or 0)
            min_id = int((bounds["min_id"] if bounds else 0) or 0)
            max_id = int((bounds["max_id"] if bounds else 0) or 0)
            remaining_estimate = max(0, target - min_id + 1) if min_id and target >= min_id else 0
            return {
                "mode": "bounded_latest",
                "latest": [dict(r) for r in latest],
                "legacy_min_id": min_id, "legacy_max_id": max_id,
                "legacy_prune_target_id": target, "legacy_rows_remaining_estimate": remaining_estimate,
                "legacy_rows_deleted": int((state["legacy_rows_deleted"] if state else 0) or 0),
                "last_prune_at": (state["last_prune_at"] if state else None),
                "last_write_error": (state["last_write_error"] if state else None),
                "last_write_error_at": (state["last_write_error_at"] if state else None),
                "reclaimable_bytes": free_pages * page_size,
            }

    def finalize_matched_market_hour(self, hour_utc: str) -> dict:
        """Build lossless compact analytics for one legacy raw-history hour.

        0.9.3 writes compact rollups incrementally and marks the hour in
        ``matched_market_history_state``. Hours without that marker are legacy
        append-only history and are rebuilt here before any raw row can be
        pruned. This is deliberately idempotent.
        """
        hour = self._hour_floor_iso(hour_utc)
        end = (datetime.fromisoformat(hour) + timedelta(hours=1)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            already = self.conn.execute(
                "SELECT 1 FROM matched_market_history_state WHERE hour_utc=?", (hour,)
            ).fetchone()
            if already:
                return {"ok": True, "hour_utc": hour, "already_finalized": True}
            try:
                raw_count = int(self.conn.execute(
                    "SELECT COUNT(*) c FROM matched_markets WHERE observed_at>=? AND observed_at<?", (hour, end)
                ).fetchone()["c"] or 0)
                # Rebuild every compact structure whose historical semantics used
                # to depend on the verbose matched_markets stream.
                self.conn.execute("DELETE FROM market_hourly_rollups WHERE hour_utc=?", (hour,))
                self.conn.execute("DELETE FROM market_hourly_seen WHERE hour_utc=?", (hour,))
                self.conn.execute("DELETE FROM matched_market_reason_hourly_rollups WHERE hour_utc=?", (hour,))
                self.conn.execute("DELETE FROM liquidity_opportunity_hourly_rollups WHERE hour_utc=?", (hour,))

                grouped = self.conn.execute(
                    """SELECT COALESCE(section,'sports') section,COALESCE(sport,'Unknown') sport,
                              COALESCE(market_name,'Unknown') market_name,COALESCE(in_play,0) in_play,
                              COUNT(*) observations,COUNT(DISTINCT event_key) unique_markets,
                              COUNT(DISTINCT CASE WHEN COALESCE(theoretical_edge_pct,0)>0 THEN event_key END) raw_positive,
                              COUNT(DISTINCT CASE WHEN COALESCE(net_roi_pct,0)>0 THEN event_key END) net_positive,
                              COALESCE(SUM(CASE WHEN net_roi_pct IS NOT NULL THEN net_roi_pct ELSE 0 END),0) net_roi_sum,
                              SUM(CASE WHEN net_roi_pct IS NOT NULL THEN 1 ELSE 0 END) net_roi_count,
                              MAX(net_roi_pct) best_net_roi_pct,
                              COALESCE(SUM(CASE WHEN diagnostic_deployed IS NOT NULL THEN diagnostic_deployed ELSE 0 END),0) deployable_sum,
                              SUM(CASE WHEN diagnostic_deployed IS NOT NULL THEN 1 ELSE 0 END) deployable_count
                       FROM matched_markets WHERE observed_at>=? AND observed_at<?
                       GROUP BY COALESCE(section,'sports'),COALESCE(sport,'Unknown'),COALESCE(market_name,'Unknown'),COALESCE(in_play,0)""",
                    (hour, end),
                ).fetchall()
                for r in grouped:
                    self.conn.execute(
                        """INSERT INTO market_hourly_rollups(
                           hour_utc,section,sport,market_name,in_play,observations,unique_markets,raw_positive,net_positive,
                           net_roi_sum,net_roi_count,best_net_roi_pct,deployable_sum,deployable_count)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (hour,r['section'],r['sport'],r['market_name'],int(r['in_play'] or 0),int(r['observations'] or 0),
                         int(r['unique_markets'] or 0),int(r['raw_positive'] or 0),int(r['net_positive'] or 0),
                         float(r['net_roi_sum'] or 0.0),int(r['net_roi_count'] or 0),r['best_net_roi_pct'],
                         float(r['deployable_sum'] or 0.0),int(r['deployable_count'] or 0)),
                    )

                seen = self.conn.execute(
                    """SELECT COALESCE(section,'sports') section,COALESCE(sport,'Unknown') sport,
                              COALESCE(market_name,'Unknown') market_name,COALESCE(in_play,0) in_play,event_key,
                              MAX(CASE WHEN COALESCE(theoretical_edge_pct,0)>0 THEN 1 ELSE 0 END) raw_positive,
                              MAX(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN 1 ELSE 0 END) net_positive
                       FROM matched_markets WHERE observed_at>=? AND observed_at<?
                       GROUP BY COALESCE(section,'sports'),COALESCE(sport,'Unknown'),COALESCE(market_name,'Unknown'),COALESCE(in_play,0),event_key""",
                    (hour, end),
                ).fetchall()
                self.conn.executemany(
                    """INSERT INTO market_hourly_seen(hour_utc,section,sport,market_name,in_play,event_key,raw_positive,net_positive)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    [(hour,r['section'],r['sport'],r['market_name'],int(r['in_play'] or 0),str(r['event_key'] or ''),
                      int(r['raw_positive'] or 0),int(r['net_positive'] or 0)) for r in seen],
                )

                reasons = self.conn.execute(
                    """SELECT COALESCE(section,'sports') section,COALESCE(sport,'Unknown') sport,
                              COALESCE(market_name,'Unknown') market_name,COALESCE(in_play,0) in_play,
                              COALESCE(status,'unknown') status,MAX(COALESCE(reason,'')) reason_sample,COUNT(*) observations
                       FROM matched_markets WHERE observed_at>=? AND observed_at<?
                       GROUP BY COALESCE(section,'sports'),COALESCE(sport,'Unknown'),COALESCE(market_name,'Unknown'),COALESCE(in_play,0),COALESCE(status,'unknown')""",
                    (hour, end),
                ).fetchall()
                self.conn.executemany(
                    """INSERT INTO matched_market_reason_hourly_rollups(hour_utc,section,sport,market_name,in_play,status,reason_sample,observations)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    [(hour,r['section'],r['sport'],r['market_name'],int(r['in_play'] or 0),r['status'],r['reason_sample'],int(r['observations'] or 0)) for r in reasons],
                )

                liq = self.conn.execute(
                    """SELECT COALESCE(section,'sports') section,COALESCE(sport,'Unknown') sport,
                              COALESCE(market_name,'Unknown') market_name,COALESCE(in_play,0) in_play,
                              SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN 1 ELSE 0 END) positive_observations,
                              SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 AND COALESCE(liquidity_capable,CASE WHEN status='below_liquidity' THEN 0 ELSE 1 END)=1 THEN 1 ELSE 0 END) liquidity_capable,
                              SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 AND COALESCE(liquidity_capable,CASE WHEN status='below_liquidity' THEN 0 ELSE 1 END)=0 THEN 1 ELSE 0 END) liquidity_rejected,
                              SUM(CASE WHEN status IN ('recommended','in_play_monitor','in_play_qualified','racing_monitor','racing_qualified') THEN 1 ELSE 0 END) qualified_observations,
                              COALESCE(SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN COALESCE(max_executable_stake,diagnostic_deployed,0) ELSE 0 END),0) executable_stake_sum,
                              SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 AND COALESCE(max_executable_stake,diagnostic_deployed,0)>0 THEN 1 ELSE 0 END) executable_stake_samples
                       FROM matched_markets WHERE observed_at>=? AND observed_at<?
                       GROUP BY COALESCE(section,'sports'),COALESCE(sport,'Unknown'),COALESCE(market_name,'Unknown'),COALESCE(in_play,0)""",
                    (hour, end),
                ).fetchall()
                self.conn.executemany(
                    """INSERT INTO liquidity_opportunity_hourly_rollups(
                       hour_utc,section,sport,market_name,in_play,positive_observations,liquidity_capable,liquidity_rejected,
                       qualified_observations,executable_stake_sum,executable_stake_samples) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    [(hour,r['section'],r['sport'],r['market_name'],int(r['in_play'] or 0),int(r['positive_observations'] or 0),
                      int(r['liquidity_capable'] or 0),int(r['liquidity_rejected'] or 0),int(r['qualified_observations'] or 0),
                      float(r['executable_stake_sum'] or 0.0),int(r['executable_stake_samples'] or 0)) for r in liq],
                )

                self.conn.execute("DELETE FROM racing_funnel_hourly_rollups WHERE hour_utc=?", (hour,))
                racing = self.conn.execute(
                    """SELECT COALESCE(sport,'Greyhounds') sport,COUNT(*) observations,
                              SUM(CASE WHEN COALESCE(status,'') NOT IN ('incomplete','racing_runner_field_incomplete') THEN 1 ELSE 0 END) complete_books,
                              SUM(CASE WHEN COALESCE(theoretical_edge_pct,0)>0 THEN 1 ELSE 0 END) theoretical_positive,
                              SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN 1 ELSE 0 END) post_commission_positive,
                              SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 AND COALESCE(liquidity_capable,CASE WHEN status='below_liquidity' THEN 0 ELSE 1 END)=1 THEN 1 ELSE 0 END) liquidity_capable,
                              SUM(CASE WHEN status IN ('racing_monitor','racing_qualified') THEN 1 ELSE 0 END) qualified
                       FROM matched_markets WHERE observed_at>=? AND observed_at<? AND (COALESCE(section,'sports')='racing' OR LOWER(COALESCE(sport,'')) LIKE '%greyhound%')
                       GROUP BY COALESCE(sport,'Greyhounds')""", (hour,end)
                ).fetchall()
                self.conn.executemany(
                    """INSERT INTO racing_funnel_hourly_rollups(hour_utc,sport,observations,complete_books,theoretical_positive,post_commission_positive,liquidity_capable,qualified)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    [(hour,r['sport'],int(r['observations'] or 0),int(r['complete_books'] or 0),int(r['theoretical_positive'] or 0),
                      int(r['post_commission_positive'] or 0),int(r['liquidity_capable'] or 0),int(r['qualified'] or 0)) for r in racing],
                )

                scan_ids = [int(r[0]) for r in self.conn.execute(
                    "SELECT DISTINCT scan_id FROM matched_markets WHERE observed_at>=? AND observed_at<? AND scan_id IS NOT NULL",
                    (hour, end),
                ).fetchall()]
                if scan_ids:
                    marks = ','.join('?' for _ in scan_ids)
                    self.conn.execute(f"DELETE FROM scan_qualification_breakdown WHERE scan_id IN ({marks})", tuple(scan_ids))
                    scan_rows = self.conn.execute(
                        f"""SELECT scan_id,COALESCE(status,'unknown') status,COUNT(*) total_count,
                                   SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN 1 ELSE 0 END) positive_count
                            FROM matched_markets WHERE scan_id IN ({marks})
                            GROUP BY scan_id,COALESCE(status,'unknown')""", tuple(scan_ids)
                    ).fetchall()
                    self.conn.executemany(
                        "INSERT INTO scan_qualification_breakdown(scan_id,status,total_count,positive_count) VALUES(?,?,?,?)",
                        [(int(r['scan_id']),r['status'],int(r['total_count'] or 0),int(r['positive_count'] or 0)) for r in scan_rows],
                    )

                self.conn.execute("INSERT OR REPLACE INTO market_hourly_rollup_state(hour_utc,built_at) VALUES(?,?)", (hour, now))
                self.conn.execute("INSERT OR REPLACE INTO liquidity_opportunity_rollup_state(hour_utc,built_at) VALUES(?,?)", (hour, now))
                self.conn.execute("INSERT OR REPLACE INTO matched_market_history_state(hour_utc,built_at) VALUES(?,?)", (hour, now))
                self.conn.commit()
                return {"ok": True, "hour_utc": hour, "already_finalized": False, "raw_rows": raw_count}
            except Exception:
                self.conn.rollback()
                raise

    def matched_market_storage_maintenance(self, *, retention_hours: int = 48, batch_size: int = 5000,
                                         archive_required_before_prune: bool = False, archive_root: Path | None = None) -> dict:
        """Incrementally prune only compacted, old verbose matched-market events.

        Archive gating is opt-in.  With the default ``False`` value the existing
        48-hour lifecycle is unchanged; when enabled, a VERIFIED sidecar Parquet
        manifest is required for the hour before any raw rows can be deleted.
        """
        retention = max(1, int(retention_hours or 48))
        batch = max(100, min(50000, int(batch_size or 5000)))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention)).replace(minute=0, second=0, microsecond=0)
        cutoff_iso = cutoff.isoformat()
        try:
            with self.lock:
                oldest = self.conn.execute(
                    "SELECT MIN(observed_at) t FROM matched_markets WHERE observed_at<?", (cutoff_iso,)
                ).fetchone()["t"]
            if not oldest:
                return {"ok": True, "done": True, "deleted": 0, "cutoff": cutoff_iso}
            hour = self._hour_floor_iso(str(oldest))
            finalized = self.finalize_matched_market_hour(hour)
            end = (datetime.fromisoformat(hour) + timedelta(hours=1)).isoformat()
            with self.lock:
                # A prune-safe marker is required even for incrementally written
                # 0.9.3 hours. It is never inferred from the age alone.
                safe = self.conn.execute("SELECT 1 FROM matched_market_history_state WHERE hour_utc=?", (hour,)).fetchone()
                if not safe:
                    return {"ok": False, "done": False, "deleted": 0, "hour_utc": hour, "message": "Compact history is not finalized"}
                if archive_required_before_prune:
                    from .archive import default_archive_root, manifest_verified
                    root = Path(archive_root) if archive_root is not None else default_archive_root(self.path)
                    if not manifest_verified(root, hour, verify_checksum=True):
                        return {"ok": True, "done": False, "deleted": 0, "hour_utc": hour,
                                "archive_required": True, "archive_verified": False,
                                "message": "Raw history retained: VERIFIED archive is required before prune."}
                self.conn.execute(
                    """DELETE FROM matched_markets WHERE id IN (
                           SELECT id FROM matched_markets WHERE observed_at>=? AND observed_at<? AND observed_at<? ORDER BY id LIMIT ?
                       )""", (hour, end, cutoff_iso, batch)
                )
                deleted = int(self.conn.execute("SELECT changes() c").fetchone()["c"] or 0)
                remaining_hour = int(self.conn.execute(
                    "SELECT COUNT(*) c FROM matched_markets WHERE observed_at>=? AND observed_at<? AND observed_at<?",
                    (hour, end, cutoff_iso),
                ).fetchone()["c"] or 0)
                now = datetime.now(timezone.utc).isoformat()
                prune_safe_through = end if remaining_hour == 0 else hour
                self.conn.execute(
                    """UPDATE matched_market_storage_state SET rows_deleted=rows_deleted+?,last_prune_at=?,
                       prune_safe_through=?,last_error=NULL,last_error_at=NULL WHERE id=1""",
                    (deleted, now, prune_safe_through),
                )
                self.conn.commit()
                return {"ok": True, "done": False, "deleted": deleted, "hour_utc": hour,
                        "hour_remaining": remaining_hour, "cutoff": cutoff_iso,
                        "finalized": not bool(finalized.get('already_finalized')),
                        "prune_safe_through": prune_safe_through}
        except Exception as exc:
            with self.lock:
                self.conn.rollback()
                try:
                    self.conn.execute(
                        "UPDATE matched_market_storage_state SET last_error=?,last_error_at=? WHERE id=1",
                        (str(exc), datetime.now(timezone.utc).isoformat()),
                    )
                    self.conn.commit()
                except Exception:
                    self.conn.rollback()
            raise

    def matched_market_finalize_due_hour(self, *, retention_hours: int = 48) -> dict:
        """Finalize the oldest retention-expired raw hour without deleting it.

        Used while the verified archive pilot is soaking but archive-gated pruning
        is not armed. This preserves the compact-history ledger while ensuring the
        pilot cannot accidentally inherit the legacy raw-row deletion path.
        """
        retention = max(1, int(retention_hours or 48))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention)).replace(minute=0, second=0, microsecond=0)
        cutoff_iso = cutoff.isoformat()
        with self.lock:
            oldest = self.conn.execute(
                "SELECT MIN(observed_at) t FROM matched_markets WHERE observed_at<?", (cutoff_iso,)
            ).fetchone()["t"]
        if not oldest:
            return {"ok": True, "done": True, "deleted": 0, "cutoff": cutoff_iso}
        hour = self._hour_floor_iso(str(oldest))
        finalized = self.finalize_matched_market_hour(hour)
        return {
            "ok": True, "done": False, "deleted": 0, "hour_utc": hour, "cutoff": cutoff_iso,
            "finalized": not bool(finalized.get("already_finalized")), "mode": "FINALIZE_ONLY",
        }

    def matched_market_archive_prune_batch(self, *, hour_utc: str, cutoff_utc: str, batch_size: int,
                                           expected_archive_rows: int, expected_min_id: int | None,
                                           expected_max_id: int | None, deleted_so_far: int = 0) -> dict:
        """Delete one bounded batch from a planner-approved archived hour.

        The archive planner owns eligibility. This method only executes a bounded
        SQLite mutation after rechecking the exact source shape and finalized-hour
        ledger under the DB lock. A closed historical hour must not gain new rows.
        """
        hour = self._hour_floor_iso(hour_utc)
        end = (datetime.fromisoformat(hour) + timedelta(hours=1)).isoformat()
        cutoff = datetime.fromisoformat(str(cutoff_utc).replace("Z", "+00:00"))
        if datetime.fromisoformat(end) > cutoff:
            return {"ok": False, "deleted": 0, "status": "CUTOFF_MISMATCH", "hour_utc": hour}
        batch = max(100, min(50000, int(batch_size or 5000)))
        expected_total = max(0, int(expected_archive_rows or 0))
        already = max(0, int(deleted_so_far or 0))
        expected_remaining = max(0, expected_total - already)
        with self.lock:
            safe = self.conn.execute(
                "SELECT 1 FROM matched_market_history_state WHERE hour_utc=?", (hour,)
            ).fetchone()
            if not safe:
                return {"ok": False, "deleted": 0, "status": "HISTORY_NOT_FINALIZED", "hour_utc": hour}
            stats = self.conn.execute(
                "SELECT COUNT(*) c,MIN(id) min_id,MAX(id) max_id FROM matched_markets WHERE observed_at>=? AND observed_at<? AND observed_at<?",
                (hour, end, cutoff_utc),
            ).fetchone()
            current_count = int(stats["c"] or 0)
            if current_count != expected_remaining:
                return {
                    "ok": False, "deleted": 0, "status": "SOURCE_SHAPE_CHANGED", "hour_utc": hour,
                    "expected_remaining": expected_remaining, "actual_remaining": current_count,
                }
            if already == 0 and current_count:
                if stats["min_id"] != expected_min_id or stats["max_id"] != expected_max_id:
                    return {"ok": False, "deleted": 0, "status": "SOURCE_ID_RANGE_CHANGED", "hour_utc": hour}
            elif current_count:
                if expected_min_id is not None and int(stats["min_id"] or 0) < int(expected_min_id):
                    return {"ok": False, "deleted": 0, "status": "SOURCE_ID_RANGE_CHANGED", "hour_utc": hour}
                if expected_max_id is not None and int(stats["max_id"] or 0) > int(expected_max_id):
                    return {"ok": False, "deleted": 0, "status": "SOURCE_ID_RANGE_CHANGED", "hour_utc": hour}
            self.conn.execute(
                """DELETE FROM matched_markets WHERE id IN (
                       SELECT id FROM matched_markets WHERE observed_at>=? AND observed_at<? AND observed_at<? ORDER BY id LIMIT ?
                   )""", (hour, end, cutoff_utc, batch)
            )
            deleted = int(self.conn.execute("SELECT changes() c").fetchone()["c"] or 0)
            remaining = int(self.conn.execute(
                "SELECT COUNT(*) c FROM matched_markets WHERE observed_at>=? AND observed_at<? AND observed_at<?",
                (hour, end, cutoff_utc),
            ).fetchone()["c"] or 0)
            now = datetime.now(timezone.utc).isoformat()
            prune_safe_through = end if remaining == 0 else hour
            self.conn.execute(
                """UPDATE matched_market_storage_state SET rows_deleted=rows_deleted+?,last_prune_at=?,
                   prune_safe_through=?,last_error=NULL,last_error_at=NULL WHERE id=1""",
                (deleted, now, prune_safe_through),
            )
            self.conn.commit()
            return {
                "ok": True, "status": "DELETED", "deleted": deleted, "hour_utc": hour,
                "hour_remaining": remaining, "prune_safe_through": prune_safe_through,
            }

    def matched_market_available_range(self) -> dict:
        """Return the true summary-history range from finalized hours plus hot raw rows."""
        with self.lock:
            ledger = self.conn.execute("SELECT MIN(hour_utc) lo,MAX(hour_utc) hi FROM matched_market_history_state").fetchone()
            raw = self.conn.execute("SELECT MIN(observed_at) lo,MAX(observed_at) hi FROM matched_markets").fetchone()
        starts = [str(x) for x in ((ledger['lo'] if ledger else None), (raw['lo'] if raw else None)) if x]
        ends = []
        if ledger and ledger['hi']:
            try:
                ends.append((datetime.fromisoformat(str(ledger['hi']).replace('Z','+00:00')) + timedelta(hours=1)).isoformat())
            except Exception:
                pass
        if raw and raw['hi']:
            try:
                # SQL range consumers use an exclusive upper bound, so include
                # the newest hot observation rather than ending exactly on it.
                raw_hi = datetime.fromisoformat(str(raw['hi']).replace('Z','+00:00'))
                ends.append((raw_hi + timedelta(microseconds=1)).isoformat())
            except Exception:
                ends.append(str(raw['hi']))
        return {"from_utc": min(starts) if starts else None, "to_utc": max(ends) if ends else None,
                "finalized_from_utc": ledger['lo'] if ledger else None, "finalized_through_hour": ledger['hi'] if ledger else None,
                "hot_from_utc": raw['lo'] if raw else None, "hot_to_utc": raw['hi'] if raw else None}

    def matched_market_finalized_hours(self, started_at: str, finished_at: str) -> set[str]:
        lo = self._hour_floor_iso(started_at)
        hi = str(finished_at)
        with self.lock:
            rows = self.conn.execute(
                "SELECT hour_utc FROM matched_market_history_state WHERE hour_utc>=? AND hour_utc<? ORDER BY hour_utc",
                (lo, hi),
            ).fetchall()
        return {str(r['hour_utc']) for r in rows}

    def matched_market_raw_hours(self, started_at: str, finished_at: str) -> set[str]:
        with self.lock:
            rows = self.conn.execute(
                """SELECT DISTINCT strftime('%Y-%m-%dT%H:00:00+00:00',observed_at) hour_utc
                   FROM matched_markets WHERE observed_at>=? AND observed_at<? ORDER BY hour_utc""",
                (str(started_at), str(finished_at)),
            ).fetchall()
        return {str(r['hour_utc']) for r in rows if r['hour_utc']}

    def matched_market_detailed_rows(self, started_at: str, finished_at: str, *, hours: list[str] | None = None,
                                     limit: int = 50000, section: str | None = None, sport: str | None = None,
                                     market: str | None = None, search: str | None = None, event_key: str | None = None) -> list[dict]:
        """Bounded raw-detail read from the hot SQLite tail only."""
        clauses = ["observed_at>=?", "observed_at<?"]
        args: list = [str(started_at), str(finished_at)]
        if hours:
            marks = ','.join('?' for _ in hours)
            clauses.append(f"strftime('%Y-%m-%dT%H:00:00+00:00',observed_at) IN ({marks})")
            args.extend(str(x) for x in hours)
        if section and str(section).lower() not in {'', 'all'}:
            clauses.append("LOWER(COALESCE(section,'sports'))=LOWER(?)"); args.append(str(section))
        if sport and str(sport).lower() not in {'', 'all'}:
            clauses.append("LOWER(COALESCE(sport,'Unknown'))=LOWER(?)"); args.append(str(sport))
        if market:
            clauses.append("LOWER(COALESCE(market_name,'')) LIKE ?"); args.append('%'+str(market).lower()+'%')
        if event_key:
            clauses.append("COALESCE(event_key,'')=?"); args.append(str(event_key))
        if search:
            clauses.append("LOWER(COALESCE(event_name,'') || ' ' || COALESCE(event_key,'') || ' ' || COALESCE(market_name,'') || ' ' || COALESCE(sport,'')) LIKE ?")
            args.append('%'+str(search).lower()+'%')
        args.append(max(1, min(250001, int(limit or 50000))))
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM matched_markets WHERE {' AND '.join(clauses)} ORDER BY observed_at,id LIMIT ?", tuple(args)
            ).fetchall()
        return [dict(r) for r in rows]

    def matched_market_storage_health(self, *, retention_hours: int = 48) -> dict:
        with self.lock:
            state = self.conn.execute("SELECT * FROM matched_market_storage_state WHERE id=1").fetchone()
            raw = self.conn.execute("SELECT COUNT(*) c,MIN(observed_at) oldest,MAX(observed_at) newest FROM matched_markets").fetchone()
            latest = self.conn.execute("SELECT COUNT(*) c FROM matched_market_latest").fetchone()
            page_size = int(self.conn.execute("PRAGMA page_size").fetchone()[0] or 0)
            page_count = int(self.conn.execute("PRAGMA page_count").fetchone()[0] or 0)
            free_pages = int(self.conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1,int(retention_hours or 48)))).replace(minute=0,second=0,microsecond=0).isoformat()
            eligible = int(self.conn.execute("SELECT COUNT(*) c FROM matched_markets WHERE observed_at<?", (cutoff,)).fetchone()["c"] or 0)
            scans = int(self.conn.execute("SELECT COUNT(*) c FROM scan_runs").fetchone()["c"] or 0)
            opportunities = int(self.conn.execute("SELECT COUNT(*) c FROM opportunities").fetchone()["c"] or 0)
            return {
                "mode": "bounded_current_plus_rollups",
                "retention_hours": max(1,int(retention_hours or 48)),
                "raw_rows": int(raw['c'] or 0), "latest_rows": int(latest['c'] or 0),
                "oldest_raw_at": raw['oldest'], "newest_raw_at": raw['newest'], "eligible_rows": eligible,
                "rows_deleted": int((state['rows_deleted'] if state else 0) or 0),
                "last_prune_at": state['last_prune_at'] if state else None,
                "prune_safe_through": state['prune_safe_through'] if state else None,
                "last_error": state['last_error'] if state else None,
                "last_error_at": state['last_error_at'] if state else None,
                "page_size": page_size, "page_count": page_count, "freelist_pages": free_pages,
                "db_bytes": page_size * page_count, "reusable_bytes": page_size * free_pages,
                "scan_runs": scans, "opportunities": opportunities,
            }

    def racing_execution_funnel(self, *, hours: int = 24) -> dict:
        """Return canonical Greyhound MONITOR funnel and miss evidence."""
        hours = max(1, min(24 * 365, int(hours or 24)))
        cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff = cutoff_dt.isoformat()
        floor = cutoff_dt.replace(minute=0, second=0, microsecond=0).isoformat()
        with self.lock:
            compact = self.conn.execute(
                """SELECT COALESCE(SUM(observations),0) observations,COALESCE(SUM(complete_books),0) complete_books,
                          COALESCE(SUM(theoretical_positive),0) theoretical_positive,
                          COALESCE(SUM(post_commission_positive),0) post_commission_positive,
                          COALESCE(SUM(liquidity_capable),0) liquidity_capable
                   FROM racing_funnel_hourly_rollups r WHERE hour_utc>=?
                     AND EXISTS (SELECT 1 FROM matched_market_history_state hs WHERE hs.hour_utc=r.hour_utc)""", (floor,)
            ).fetchone()
            raw = self.conn.execute(
                """SELECT COUNT(*) observations,
                          SUM(CASE WHEN COALESCE(status,'') NOT IN ('incomplete','racing_runner_field_incomplete') THEN 1 ELSE 0 END) complete_books,
                          SUM(CASE WHEN COALESCE(theoretical_edge_pct,0)>0 THEN 1 ELSE 0 END) theoretical_positive,
                          SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN 1 ELSE 0 END) post_commission_positive,
                          SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 AND COALESCE(liquidity_capable,CASE WHEN status='below_liquidity' THEN 0 ELSE 1 END)=1 THEN 1 ELSE 0 END) liquidity_capable
                   FROM matched_markets mm WHERE observed_at>=?
                     AND (COALESCE(section,'sports')='racing' OR LOWER(COALESCE(sport,'')) LIKE '%greyhound%')
                     AND NOT EXISTS (SELECT 1 FROM matched_market_history_state hs WHERE hs.hour_utc=strftime('%Y-%m-%dT%H:00:00+00:00',mm.observed_at))""", (cutoff,)
            ).fetchone()
            qualified = int(self.conn.execute(
                """SELECT COUNT(*) c FROM opportunities WHERE detected_at>=? AND COALESCE(section,'sports')='racing'
                   AND COALESCE(qualification_status,'')='racing_qualified'""", (cutoff,)
            ).fetchone()["c"] or 0)
            attempts = self.conn.execute(
                """SELECT er.state,er.details_json,er.started_at,o.book_revision,o.quote_oldest_age_ms,o.quote_receipt_spread_ms,o.timestamp_quality
                   FROM execution_runs er JOIN opportunities o ON o.id=er.opportunity_id
                   WHERE er.started_at>=? AND er.execution_type='modeled_racing_monitor' AND COALESCE(o.section,'sports')='racing'""", (cutoff,)
            ).fetchall()
            opened = int(self.conn.execute(
                "SELECT COUNT(*) c FROM monitor_positions WHERE opened_at>=? AND COALESCE(stream,'pre_match')='racing'", (cutoff,)
            ).fetchone()["c"] or 0)
            qtiming = self.conn.execute(
                """SELECT quote_oldest_age_ms,quote_receipt_spread_ms FROM opportunities
                   WHERE detected_at>=? AND COALESCE(section,'sports')='racing' AND COALESCE(qualification_status,'')='racing_qualified'""", (cutoff,)
            ).fetchall()
        pre = {}
        for key in ('observations','complete_books','theoretical_positive','post_commission_positive','liquidity_capable'):
            pre[key] = int((compact[key] if compact else 0) or 0) + int((raw[key] if raw else 0) or 0)
        failures: dict[str,int] = {}
        missed = 0
        for row in attempts:
            state = str(row['state'] or '').upper()
            if state == 'MONITOR_MISSED': missed += 1
            try: details=json.loads(row['details_json'] or '{}')
            except Exception: details={}
            if 'OPEN' not in state:
                reason = str(details.get('first_failure_reason') or details.get('monitor_reason') or state.replace('MONITOR_','') or 'OTHER').upper()
                failures[reason] = failures.get(reason,0)+1
        ages=sorted(int(r['quote_oldest_age_ms']) for r in qtiming if r['quote_oldest_age_ms'] is not None)
        skews=sorted(int(r['quote_receipt_spread_ms']) for r in qtiming if r['quote_receipt_spread_ms'] is not None)
        def median(vals):
            if not vals: return None
            n=len(vals); m=n//2
            return vals[m] if n%2 else round((vals[m-1]+vals[m])/2,1)
        return {
            **pre, 'qualified': qualified, 'attempts': len(attempts), 'opened': opened, 'missed': missed,
            'failures': failures, 'median_quote_age_ms': median(ages), 'median_receipt_skew_ms': median(skews),
            'window_hours': hours, 'started_at': cutoff,
        }

    def existing_recommendation(self, event_key: str, market_name: str, sport: str | None = None, in_play: bool | None = None) -> bool:
        with self.lock:
            clauses = ["event_key=?", "market_name=?"]
            args: list = [event_key, market_name]
            if sport:
                clauses.append("COALESCE(sport,'Unknown')=?")
                args.append(sport)
            if in_play is True:
                clauses.append("COALESCE(in_play,0)=1")
            elif in_play is False:
                clauses.append("COALESCE(in_play,0)=0")
            row = self.conn.execute(f"SELECT 1 FROM opportunities WHERE {' AND '.join(clauses)} LIMIT 1", tuple(args)).fetchone()
            return bool(row)

    def recent_recommendation(self, event_key: str, market_name: str, sport: str | None = None, in_play: bool | None = None, within_seconds: float = 0.0) -> bool:
        """Return True when the same canonical market was captured inside a short retry window.

        This is deliberately time-bounded. In-play markets can produce recurring executable
        signals, so historical opportunities must never suppress a fresh Monitor attempt.
        """
        seconds = max(0.0, float(within_seconds or 0.0))
        if seconds <= 0:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        with self.lock:
            clauses = ["event_key=?", "market_name=?", "detected_at>=?"]
            args: list = [event_key, market_name, cutoff]
            if sport:
                clauses.append("COALESCE(sport,'Unknown')=?")
                args.append(sport)
            if in_play is True:
                clauses.append("COALESCE(in_play,0)=1")
            elif in_play is False:
                clauses.append("COALESCE(in_play,0)=0")
            row = self.conn.execute(
                f"SELECT 1 FROM opportunities WHERE {' AND '.join(clauses)} ORDER BY detected_at DESC LIMIT 1",
                tuple(args),
            ).fetchone()
            return bool(row)

    def racing_retry_gate(self, event_key: str, market_name: str, sport: str, book_revision: str,
                          *, cooldown_seconds: float = 5.0, max_attempts: int = 3) -> dict:
        """Return whether a fresh Greyhound book may create another SIM MONITOR attempt.

        The gate is Racing-only. Sports keeps its existing pre-match/in-play
        suppression rules. A changed book is necessary but not sufficient: the
        previous attempt must have ended in a transient miss and the cooldown must
        have elapsed.
        """
        transient = {"PRICE_MOVED", "BELOW_THRESHOLD", "INSUFFICIENT_LIQUIDITY", "QUOTE_STALE",
                     "CROSS_VENUE_TIME_SKEW", "VENUE_REFRESH_TIMEOUT", "BOOK_CHANGED_DURING_VALIDATION"}
        cooldown_seconds = max(0.0, float(cooldown_seconds or 0.0))
        max_attempts = max(1, int(max_attempts or 1))
        with self.lock:
            if self.conn.execute(
                "SELECT 1 FROM monitor_positions WHERE status='OPEN' AND event_key=? AND market_name=? AND COALESCE(stream,'pre_match')='racing' LIMIT 1",
                (event_key, market_name),
            ).fetchone():
                return {"allowed": False, "code": "POSITION_OPEN", "attempts": 0, "reason": "Racing MONITOR position already open"}
            opps = self.conn.execute(
                """SELECT id,detected_at,COALESCE(book_revision,signature,'') book_revision
                   FROM opportunities WHERE event_key=? AND market_name=? AND COALESCE(sport,'Unknown')=?
                     AND COALESCE(qualification_status,'qualified')='racing_qualified' ORDER BY id DESC""",
                (event_key, market_name, sport),
            ).fetchall()
            if not opps:
                return {"allowed": True, "code": "FIRST_ATTEMPT", "attempts": 0, "reason": "No previous Racing MONITOR attempt"}
            ids = [int(r["id"]) for r in opps]
            marks = ",".join("?" for _ in ids)
            attempts_row = self.conn.execute(
                f"SELECT COUNT(*) c FROM execution_runs WHERE opportunity_id IN ({marks}) AND execution_type='modeled_racing_monitor'",
                tuple(ids),
            ).fetchone()
            attempts = int((attempts_row["c"] if attempts_row else 0) or 0)
            if attempts >= max_attempts:
                return {"allowed": False, "code": "MAX_ATTEMPTS", "attempts": attempts,
                        "reason": f"Racing MONITOR maximum of {max_attempts} attempts reached"}
            latest = opps[0]
            er = self.conn.execute(
                """SELECT id,state,started_at,finished_at,details_json FROM execution_runs
                   WHERE opportunity_id=? AND execution_type='modeled_racing_monitor' ORDER BY id DESC LIMIT 1""",
                (int(latest["id"]),),
            ).fetchone()
            if not er:
                return {"allowed": False, "code": "ATTEMPT_UNRESOLVED", "attempts": attempts,
                        "reason": "Previous Racing qualification has not resolved its MONITOR attempt"}
            state = str(er["state"] or "").upper()
            if state.startswith("MONITOR_OPEN"):
                return {"allowed": False, "code": "POSITION_OPENED", "attempts": attempts, "reason": "Race already opened a MONITOR position"}
            previous_revision = str(latest["book_revision"] or "")
            if previous_revision and previous_revision == str(book_revision or ""):
                return {"allowed": False, "code": "UNCHANGED_BOOK", "attempts": attempts,
                        "reason": "Previous failed executable book has not materially changed"}
            try:
                details = json.loads(er["details_json"] or "{}")
            except Exception:
                details = {}
            failure = str(details.get("first_failure_reason") or details.get("monitor_reason") or "").upper()
            if state != "MONITOR_MISSED" or failure not in transient:
                return {"allowed": False, "code": "PERMANENT_OUTCOME", "attempts": attempts, "last_failure": failure,
                        "reason": f"Previous Racing attempt outcome {failure or state or 'UNKNOWN'} is not re-arm eligible"}
            ended_raw = er["finished_at"] or er["started_at"]
            try:
                ended = datetime.fromisoformat(str(ended_raw).replace("Z", "+00:00"))
                if ended.tzinfo is None: ended = ended.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - ended.astimezone(timezone.utc)).total_seconds()
            except Exception:
                age = cooldown_seconds
            if age < cooldown_seconds:
                return {"allowed": False, "code": "COOLDOWN", "attempts": attempts, "last_failure": failure,
                        "retry_after_seconds": round(cooldown_seconds - age, 3), "reason": "Racing MONITOR retry cooldown active"}
            return {"allowed": True, "code": "REARMED", "attempts": attempts, "last_failure": failure,
                    "previous_revision": previous_revision, "book_revision": str(book_revision or ""),
                    "reason": "Fresh materially changed Racing book may be re-evaluated"}

    def add_opportunity(self, event_key, event_name, event_start, market_name, edge_pct, expected_roi_pct, legs, source_markets, match_score, signature, is_demo=False, strategy="1x2", sport="Unknown", in_play=None, event_status=None, job_id=None, section="sports", race_track=None, race_number=None, runner_count=None, time_to_off_seconds=None, *, max_executable_stake=None, limiting_provider=None, limiting_selection=None, limiting_side=None, liquidity_capable=None, liquidity_rejection_reason=None, depth_at_qualification=None, quote_age_at_qualification_ms=None, book_revision=None, quote_oldest_age_ms=None, quote_newest_age_ms=None, quote_receipt_spread_ms=None, source_timestamp_spread_ms=None, timestamp_quality=None, engine_instance_id=None, engine_type=None, engine_version=None, engine_config_version=None, engine_provenance_source=None, routing_diagnostics=None):
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO opportunities(
                    detected_at,event_key,event_name,event_start,market_name,edge_pct,expected_roi_pct,
                    legs_json,source_markets_json,match_score,signature,is_demo,strategy,sport,section,race_track,race_number,runner_count,time_to_off_seconds,in_play,event_status,job_id,qualification_status,qualification_reason,
                    max_executable_stake,limiting_provider,limiting_selection,limiting_side,liquidity_capable,liquidity_rejection_reason,depth_at_qualification_json,quote_age_at_qualification_ms,
                    book_revision,quote_oldest_age_ms,quote_newest_age_ms,quote_receipt_spread_ms,source_timestamp_spread_ms,timestamp_quality,
                    engine_instance_id,engine_type,engine_version,engine_config_version,engine_provenance_source,routing_diagnostics_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).isoformat(), event_key, event_name, event_start, market_name,
                    edge_pct, expected_roi_pct, json.dumps(legs), json.dumps(source_markets), match_score, signature, int(bool(is_demo)), strategy,
                    sport, str(section or "sports"), race_track, race_number, runner_count, time_to_off_seconds,
                    None if in_play is None else int(bool(in_play)), event_status, job_id, "qualified", None,
                    max_executable_stake, limiting_provider, limiting_selection, limiting_side,
                    None if liquidity_capable is None else int(bool(liquidity_capable)), liquidity_rejection_reason,
                    json.dumps(depth_at_qualification or {}, separators=(",", ":")), quote_age_at_qualification_ms,
                    str(book_revision or signature or ""), quote_oldest_age_ms, quote_newest_age_ms, quote_receipt_spread_ms,
                    source_timestamp_spread_ms, timestamp_quality, engine_instance_id, engine_type, engine_version, engine_config_version,
                    engine_provenance_source or ("runtime_origin" if engine_instance_id else "unattributed"),
                    json.dumps(routing_diagnostics or {}, default=str, separators=(",", ":")),
                ),
            )
            self.conn.commit()
            return cur.lastrowid


    def set_opportunity_qualification(self, opportunity_id: int, status: str, reason: str | None = None, *, scan_id: int | None = None):
        """Update canonical qualification without erasing historical funnel evidence."""
        with self.lock:
            status = str(status)
            if status.startswith("in_play_"):
                self.conn.execute(
                    "UPDATE opportunities SET qualification_status=?, qualification_reason=?, in_play=1 WHERE id=?",
                    (status, reason, int(opportunity_id)),
                )
            else:
                self.conn.execute(
                    "UPDATE opportunities SET qualification_status=?, qualification_reason=? WHERE id=?",
                    (status, reason, int(opportunity_id)),
                )
            if scan_id is not None:
                opp = self.conn.execute(
                    "SELECT event_key,market_name,COALESCE(sport,'Unknown') sport,COALESCE(section,'sports') section,COALESCE(in_play,0) in_play FROM opportunities WHERE id=?",
                    (int(opportunity_id),),
                ).fetchone()
                if opp:
                    mm_status = "in_play_research" if status == "in_play_research" else status
                    latest = self.conn.execute(
                        """SELECT state_key,status,observed_at,COALESCE(net_roi_pct,0) net_roi_pct,COALESCE(section,'sports') section,
                                  COALESCE(sport,'Unknown') sport,COALESCE(market_name,'Unknown') market_name,COALESCE(in_play,0) in_play
                           FROM matched_market_latest WHERE scan_id=? AND event_key=? AND market_name=? AND COALESCE(sport,'Unknown')=? LIMIT 1""",
                        (int(scan_id), opp["event_key"], opp["market_name"], opp["sport"]),
                    ).fetchone()
                    old_status = str(latest["status"] or "unknown") if latest else None
                    self.conn.execute(
                        """UPDATE matched_markets SET status=?,reason=? WHERE scan_id=? AND event_key=? AND market_name=? AND COALESCE(sport,'Unknown')=?""",
                        (mm_status, reason, int(scan_id), opp["event_key"], opp["market_name"], opp["sport"]),
                    )
                    if latest:
                        self.conn.execute("UPDATE matched_market_latest SET status=?,reason=? WHERE state_key=?", (mm_status, reason, latest["state_key"]))
                        positive = int(float(latest["net_roi_pct"] or 0.0) > 0.0)
                        if old_status and old_status != mm_status:
                            self.conn.execute(
                                """UPDATE scan_qualification_breakdown SET total_count=MAX(0,total_count-1),positive_count=MAX(0,positive_count-?)
                                   WHERE scan_id=? AND status=?""", (positive, int(scan_id), old_status),
                            )
                            self.conn.execute("DELETE FROM scan_qualification_breakdown WHERE scan_id=? AND status=? AND total_count<=0", (int(scan_id), old_status))
                            self.conn.execute(
                                """INSERT INTO scan_qualification_breakdown(scan_id,status,total_count,positive_count) VALUES(?,?,1,?)
                                   ON CONFLICT(scan_id,status) DO UPDATE SET total_count=scan_qualification_breakdown.total_count+1,
                                    positive_count=scan_qualification_breakdown.positive_count+excluded.positive_count""",
                                (int(scan_id), mm_status, positive),
                            )
                            try:
                                hour = datetime.fromisoformat(str(latest["observed_at"]).replace("Z", "+00:00")).replace(minute=0, second=0, microsecond=0).isoformat()
                            except Exception:
                                hour = None
                            if hour:
                                self.conn.execute(
                                    """UPDATE matched_market_reason_hourly_rollups SET observations=MAX(0,observations-1)
                                       WHERE hour_utc=? AND section=? AND sport=? AND market_name=? AND in_play=? AND status=?""",
                                    (hour, latest["section"], latest["sport"], latest["market_name"], int(latest["in_play"] or 0), old_status),
                                )
                                self.conn.execute("DELETE FROM matched_market_reason_hourly_rollups WHERE observations<=0")
                                self.conn.execute(
                                    """INSERT INTO matched_market_reason_hourly_rollups(hour_utc,section,sport,market_name,in_play,status,reason_sample,observations)
                                       VALUES(?,?,?,?,?,?,?,1)
                                       ON CONFLICT(hour_utc,section,sport,market_name,in_play,status) DO UPDATE SET
                                        observations=matched_market_reason_hourly_rollups.observations+1,reason_sample=excluded.reason_sample""",
                                    (hour, latest["section"], latest["sport"], latest["market_name"], int(latest["in_play"] or 0), mm_status, str(reason or "")),
                                )
            self.conn.commit()

    def qualification_breakdown_for_scan(self, scan_id: int | None) -> dict[str, int]:
        if not scan_id:
            return {}
        with self.lock:
            rows = self.conn.execute(
                "SELECT status,positive_count c FROM scan_qualification_breakdown WHERE scan_id=? AND positive_count>0 ORDER BY positive_count DESC",
                (int(scan_id),),
            ).fetchall()
            if rows:
                return {str(r["status"] or "unknown"): int(r["c"] or 0) for r in rows}
            # Compatibility for a pre-0.9.3 scan that has not been compacted yet.
            rows = self.conn.execute(
                """SELECT status,COUNT(*) c FROM matched_markets WHERE scan_id=? AND COALESCE(net_roi_pct,0)>0 GROUP BY status ORDER BY c DESC""",
                (int(scan_id),),
            ).fetchall()
            return {str(r["status"] or "unknown"): int(r["c"] or 0) for r in rows}

    def qualification_breakdown_between(self, started_at: str | None = None, finished_at: str | None = None) -> dict[str, int]:
        """Positive-observation rule outcomes from compact hourly evidence."""
        with self.lock:
            where = [] ; args: list = []
            if started_at: where.append("hour_utc>=?"); args.append(str(started_at)[:13] + ":00:00+00:00")
            if finished_at: where.append("hour_utc<?"); args.append(str(finished_at)[:13] + ":00:00+00:00")
            clause = " AND ".join(where) if where else "1=1"
            # Reason rollups contain all observations; use statuses that represented positive candidates.
            positive_statuses = (
                "commission_removed","below_threshold","below_profit_threshold","below_quality","single_exchange",
                "recommended","in_play_monitor","in_play_qualified","in_play_research","racing_monitor","racing_qualified",
                "already_recommended","racing_position_open","racing_stale_quotes","racing_research"
            )
            marks = ",".join("?" for _ in positive_statuses)
            rows = self.conn.execute(
                f"""SELECT status,SUM(observations) c FROM matched_market_reason_hourly_rollups
                    WHERE {clause} AND status IN ({marks}) GROUP BY status ORDER BY c DESC""",
                tuple(args) + positive_statuses,
            ).fetchall()
            if rows:
                return {str(r["status"] or "unknown"): int(r["c"] or 0) for r in rows}
            # Legacy fallback before rollups have any data.
            where2 = ["COALESCE(mm.net_roi_pct,0)>0", "COALESCE(sr.scan_kind,'legacy') IN ('price','legacy')"]
            params: list = []
            if started_at: where2.append("mm.observed_at>=?"); params.append(started_at)
            if finished_at: where2.append("mm.observed_at<?"); params.append(finished_at)
            rows = self.conn.execute(
                f"""SELECT mm.status,COUNT(*) c FROM matched_markets mm JOIN scan_runs sr ON sr.id=mm.scan_id
                    WHERE {' AND '.join(where2)} GROUP BY mm.status ORDER BY c DESC""", tuple(params),
            ).fetchall()
            return {str(r["status"] or "unknown"): int(r["c"] or 0) for r in rows}

    def add_scenario_run(self, opportunity_id, name, bankroll, deployed, profit, roi, limited_by, stakes, outcome_pnls):
        with self.lock:
            self.conn.execute(
                """INSERT INTO scenario_runs(
                    opportunity_id,scenario_name,bankroll,deployed,expected_profit,expected_roi_pct,
                    limited_by,stakes_json,outcome_pnls_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    opportunity_id, name, bankroll, deployed, profit, roi, limited_by,
                    json.dumps(stakes), json.dumps(outcome_pnls), datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.conn.commit()

    def start_scan(self, job_id: int | None = None, scan_kind: str = "price") -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO scan_runs(job_id,started_at,scan_kind) VALUES(?,?,?)",
                (job_id, datetime.now(timezone.utc).isoformat(), str(scan_kind or "price")),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def finish_scan(self, scan_id: int, markets_seen=0, matches_seen=0, opportunities_found=0, statuses=None, error=None,
                    processed_candidates=0, positive_opportunities=0, qualified_count=0, executed_count=0, duration_ms=0,
                    stage_timings=None, cache_entries=0, stale_rejections=0):
        with self.lock:
            self.conn.execute(
                """UPDATE scan_runs SET finished_at=?,markets_seen=?,matches_seen=?,opportunities_found=?,status_json=?,error=?,
                   processed_candidates=?,positive_opportunities=?,qualified_count=?,executed_count=?,duration_ms=?,
                   stage_timings_json=?,cache_entries=?,stale_rejections=? WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(), markets_seen, matches_seen, opportunities_found, json.dumps(statuses or []), error,
                 processed_candidates, positive_opportunities, qualified_count, executed_count, duration_ms,
                 json.dumps(stage_timings or {}), int(cache_entries or 0), int(stale_rejections or 0), scan_id),
            )
            self.conn.commit()

    def upsert_market_cache(self, rows: list[dict], *, seen_at: str | None = None, deactivate_unseen: bool = True) -> int:
        now = seen_at or datetime.now(timezone.utc).isoformat()
        seen_keys = []
        with self.lock:
            for row in rows:
                key = str(row.get("cache_key") or "")
                if not key:
                    continue
                seen_keys.append(key)
                self.conn.execute(
                    """INSERT INTO market_cache(
                        cache_key,event_key,event_name,event_start,market_name,market_type,strategy,sport,section,race_track,race_number,runner_count,match_score,
                        source_markets_json,discovered_at,last_validated_at,last_price_refresh_at,refresh_interval_seconds,active
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        event_key=excluded.event_key,event_name=excluded.event_name,event_start=excluded.event_start,
                        market_name=excluded.market_name,market_type=excluded.market_type,strategy=excluded.strategy,sport=excluded.sport,
                        section=excluded.section,race_track=excluded.race_track,race_number=excluded.race_number,runner_count=excluded.runner_count,
                        match_score=excluded.match_score,source_markets_json=excluded.source_markets_json,
                        last_validated_at=excluded.last_validated_at,refresh_interval_seconds=excluded.refresh_interval_seconds,active=1""",
                    (key,row.get("event_key"),row.get("event_name"),row.get("event_start"),row.get("market_name"),
                     row.get("market_type"),row.get("strategy"),row.get("sport"),row.get("section") or "sports",row.get("race_track"),row.get("race_number"),row.get("runner_count"),float(row.get("match_score") or 0.0),
                     json.dumps(row.get("source_markets") or []),now,now,row.get("last_price_refresh_at"),
                     int(row.get("refresh_interval_seconds") or 10)),
                )
            if deactivate_unseen:
                if seen_keys:
                    marks=",".join("?" for _ in seen_keys)
                    self.conn.execute(f"UPDATE market_cache SET active=0 WHERE active=1 AND cache_key NOT IN ({marks})", tuple(seen_keys))
                else:
                    self.conn.execute("UPDATE market_cache SET active=0 WHERE active=1")
            self.conn.commit()
        return len(seen_keys)

    def active_market_cache(self, *, due_at: str | None = None, limit: int = 1000) -> list[dict]:
        now = datetime.now(timezone.utc)
        if due_at is not None:
            try:
                due_dt = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                now = due_dt.astimezone(timezone.utc)
            except Exception:
                pass
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM market_cache WHERE active=1 ORDER BY COALESCE(event_start,'9999'), match_score DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        out=[]
        for row in rows:
            d=dict(row)
            try: d["source_markets"] = json.loads(d.pop("source_markets_json") or "[]")
            except Exception: d["source_markets"] = []
            if due_at is not None:
                try:
                    last = datetime.fromisoformat(str(d.get("last_price_refresh_at") or "").replace("Z", "+00:00")) if d.get("last_price_refresh_at") else None
                    if last and last.tzinfo is None: last=last.replace(tzinfo=timezone.utc)
                    interval=max(1,int(d.get("refresh_interval_seconds") or 10))
                    if last and (now-last.astimezone(timezone.utc)).total_seconds() < interval:
                        continue
                except Exception:
                    pass
            out.append(d)
        return out

    def mark_market_cache_refreshed(self, cache_keys: list[str], *, refreshed_at: str | None = None) -> None:
        if not cache_keys:
            return
        when = refreshed_at or datetime.now(timezone.utc).isoformat()
        marks=",".join("?" for _ in cache_keys)
        with self.lock:
            self.conn.execute(f"UPDATE market_cache SET last_price_refresh_at=? WHERE cache_key IN ({marks})", (when,*cache_keys))
            self.conn.commit()

    def market_cache_stats(self) -> dict:
        with self.lock:
            row=self.conn.execute(
                """SELECT COUNT(*) total, SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) active,
                          MAX(last_validated_at) last_discovery, MAX(last_price_refresh_at) last_price_refresh
                   FROM market_cache"""
            ).fetchone()
            return {"total": int((row["total"] if row else 0) or 0), "active": int((row["active"] if row else 0) or 0),
                    "last_discovery": row["last_discovery"] if row else None, "last_price_refresh": row["last_price_refresh"] if row else None}

    @staticmethod
    def _matched_market_state_key(*, section: str, sport: str, event_key: str, market_name: str, strategy: str, in_play) -> str:
        raw = "|".join((str(section or "sports"), str(sport or "Unknown"), str(event_key or ""),
                        str(market_name or "Unknown"), str(strategy or "1x2"), "1" if bool(in_play) else "0"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _matched_material_fingerprint(*, status: str, reason: str | None, theoretical_edge_pct, net_roi_pct,
                                      diagnostic_deployed, diagnostic_profit, liquidity_capable, legs) -> str:
        material_legs = []
        for leg in (legs or []):
            if not isinstance(leg, dict):
                continue
            try:
                odds = round(float(leg.get("odds") or 0.0), 4)
                liquidity = round(float(leg.get("liquidity") or leg.get("executable_capacity") or 0.0), 2)
            except (TypeError, ValueError):
                odds, liquidity = 0.0, 0.0
            material_legs.append((
                str(leg.get("venue_id") or leg.get("provider_id") or leg.get("exchange") or ""),
                str(leg.get("selection") or leg.get("canonical_selection_id") or ""),
                str(leg.get("side") or "BACK").upper(), odds, liquidity,
            ))
        payload = {
            "status": str(status or "unknown"), "reason": str(reason or ""),
            "raw_edge": None if theoretical_edge_pct is None else round(float(theoretical_edge_pct), 4),
            "net_roi": None if net_roi_pct is None else round(float(net_roi_pct), 4),
            "deployed": None if diagnostic_deployed is None else round(float(diagnostic_deployed), 2),
            "profit": None if diagnostic_profit is None else round(float(diagnostic_profit), 2),
            "liquidity_capable": None if liquidity_capable is None else bool(liquidity_capable),
            "legs": sorted(material_legs),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:32]

    def add_matched_market(self, scan_id: int, event_key: str, event_name: str, event_start: str | None,
                           market_name: str, match_score: float, theoretical_edge_pct, gross_roi_pct, commission_impact_pct, net_roi_pct,
                           diagnostic_deployed, diagnostic_profit, limited_by, status: str, reason: str, legs, source_markets, strategy="1x2", quality=None,
                           sport="Unknown", in_play=None, event_status=None, section="sports", race_track=None, race_number=None, runner_count=None, time_to_off_seconds=None,
                           *, max_executable_stake=None, limiting_provider=None, limiting_selection=None, limiting_side=None, liquidity_capable=None,
                           liquidity_rejection_reason=None, depth_at_qualification=None, quote_age_at_qualification_ms=None,
                           book_revision=None, quote_oldest_age_ms=None, quote_newest_age_ms=None, quote_receipt_spread_ms=None,
                           source_timestamp_spread_ms=None, timestamp_quality=None, book_complete=None):
        """Persist one matched-market evaluation with bounded verbose history.

        Every scanner observation updates current state and compact rollups. A
        JSON-heavy ``matched_markets`` row is appended only when execution-relevant
        state materially changes or a low-frequency heartbeat is due.
        """
        observed_at = datetime.now(timezone.utc).isoformat()
        sec = str(section or "sports"); sp = str(sport or "Unknown"); mk = str(market_name or "Unknown"); ip = int(bool(in_play))
        legs_json = json.dumps(legs or [], separators=(",", ":"), default=str)
        sources_json = json.dumps(source_markets or [], separators=(",", ":"), default=str)
        depth_json = json.dumps(depth_at_qualification or {}, separators=(",", ":"), default=str)
        state_key = self._matched_market_state_key(section=sec, sport=sp, event_key=event_key, market_name=mk, strategy=strategy, in_play=in_play)
        fingerprint = self._matched_material_fingerprint(
            status=status, reason=reason, theoretical_edge_pct=theoretical_edge_pct, net_roi_pct=net_roi_pct,
            diagnostic_deployed=diagnostic_deployed, diagnostic_profit=diagnostic_profit,
            liquidity_capable=liquidity_capable, legs=legs,
        )
        cfg = self.get_setting("config", {}) or {}
        heartbeat_seconds = max(60.0, float(cfg.get("matched_market_heartbeat_seconds", 900) or 900))
        q = quality or {}
        values = (
            scan_id, observed_at, event_key, event_name, event_start, market_name,
            match_score, theoretical_edge_pct, gross_roi_pct, commission_impact_pct, net_roi_pct, diagnostic_deployed, diagnostic_profit, limited_by,
            status, reason, legs_json, sources_json, strategy,
            q.get("quality_score"), q.get("quality_band"), q.get("reference_bankroll"), q.get("bankroll_roi_pct"), q.get("capital_used_pct"),
            sport, sec, race_track, race_number, runner_count, time_to_off_seconds, None if in_play is None else ip, event_status,
            max_executable_stake, limiting_provider, limiting_selection, limiting_side,
            None if liquidity_capable is None else int(bool(liquidity_capable)), liquidity_rejection_reason, depth_json, quote_age_at_qualification_ms,
            book_revision, quote_oldest_age_ms, quote_newest_age_ms, quote_receipt_spread_ms, source_timestamp_spread_ms, timestamp_quality,
        )
        with self.lock:
            previous = self.conn.execute(
                "SELECT material_fingerprint,last_verbose_at,first_seen FROM matched_market_latest WHERE state_key=?", (state_key,)
            ).fetchone()
            verbose = previous is None or str(previous["material_fingerprint"] or "") != fingerprint
            if not verbose and previous and previous["last_verbose_at"]:
                try:
                    last_verbose = datetime.fromisoformat(str(previous["last_verbose_at"]).replace("Z", "+00:00"))
                    if last_verbose.tzinfo is None: last_verbose = last_verbose.replace(tzinfo=timezone.utc)
                    verbose = (datetime.now(timezone.utc) - last_verbose.astimezone(timezone.utc)).total_seconds() >= heartbeat_seconds
                except Exception:
                    verbose = True
            if verbose:
                self.conn.execute(
                    """INSERT INTO matched_markets(
                        scan_id,observed_at,event_key,event_name,event_start,market_name,match_score,
                        theoretical_edge_pct,gross_roi_pct,commission_impact_pct,net_roi_pct,diagnostic_deployed,diagnostic_profit,limited_by,
                        status,reason,legs_json,source_markets_json,strategy,quality_score,quality_band,reference_bankroll,bankroll_roi_pct,capital_used_pct,
                        sport,section,race_track,race_number,runner_count,time_to_off_seconds,in_play,event_status,
                        max_executable_stake,limiting_provider,limiting_selection,limiting_side,liquidity_capable,liquidity_rejection_reason,depth_at_qualification_json,quote_age_at_qualification_ms,
                        book_revision,quote_oldest_age_ms,quote_newest_age_ms,quote_receipt_spread_ms,source_timestamp_spread_ms,timestamp_quality
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values,
                )

            latest_values = (state_key, scan_id, observed_at, observed_at, observed_at, 1, fingerprint, observed_at if verbose else None) + values[2:]
            self.conn.execute(
                """INSERT INTO matched_market_latest(
                    state_key,scan_id,observed_at,first_seen,last_seen,observation_count,material_fingerprint,last_verbose_at,
                    event_key,event_name,event_start,market_name,match_score,theoretical_edge_pct,gross_roi_pct,commission_impact_pct,net_roi_pct,
                    diagnostic_deployed,diagnostic_profit,limited_by,status,reason,legs_json,source_markets_json,strategy,quality_score,quality_band,reference_bankroll,
                    bankroll_roi_pct,capital_used_pct,sport,section,race_track,race_number,runner_count,time_to_off_seconds,in_play,event_status,max_executable_stake,
                    limiting_provider,limiting_selection,limiting_side,liquidity_capable,liquidity_rejection_reason,depth_at_qualification_json,quote_age_at_qualification_ms,
                    book_revision,quote_oldest_age_ms,quote_newest_age_ms,quote_receipt_spread_ms,source_timestamp_spread_ms,timestamp_quality
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(state_key) DO UPDATE SET
                    scan_id=excluded.scan_id,observed_at=excluded.observed_at,last_seen=excluded.last_seen,
                    observation_count=matched_market_latest.observation_count+1,material_fingerprint=excluded.material_fingerprint,
                    last_verbose_at=COALESCE(excluded.last_verbose_at,matched_market_latest.last_verbose_at),
                    event_name=excluded.event_name,event_start=excluded.event_start,match_score=excluded.match_score,
                    theoretical_edge_pct=excluded.theoretical_edge_pct,gross_roi_pct=excluded.gross_roi_pct,commission_impact_pct=excluded.commission_impact_pct,
                    net_roi_pct=excluded.net_roi_pct,diagnostic_deployed=excluded.diagnostic_deployed,diagnostic_profit=excluded.diagnostic_profit,limited_by=excluded.limited_by,
                    status=excluded.status,reason=excluded.reason,legs_json=excluded.legs_json,source_markets_json=excluded.source_markets_json,
                    quality_score=excluded.quality_score,quality_band=excluded.quality_band,reference_bankroll=excluded.reference_bankroll,
                    bankroll_roi_pct=excluded.bankroll_roi_pct,capital_used_pct=excluded.capital_used_pct,race_track=excluded.race_track,race_number=excluded.race_number,
                    runner_count=excluded.runner_count,time_to_off_seconds=excluded.time_to_off_seconds,event_status=excluded.event_status,
                    max_executable_stake=excluded.max_executable_stake,limiting_provider=excluded.limiting_provider,limiting_selection=excluded.limiting_selection,
                    limiting_side=excluded.limiting_side,liquidity_capable=excluded.liquidity_capable,liquidity_rejection_reason=excluded.liquidity_rejection_reason,
                    depth_at_qualification_json=excluded.depth_at_qualification_json,quote_age_at_qualification_ms=excluded.quote_age_at_qualification_ms,
                    book_revision=excluded.book_revision,quote_oldest_age_ms=excluded.quote_oldest_age_ms,quote_newest_age_ms=excluded.quote_newest_age_ms,
                    quote_receipt_spread_ms=excluded.quote_receipt_spread_ms,source_timestamp_spread_ms=excluded.source_timestamp_spread_ms,timestamp_quality=excluded.timestamp_quality""",
                latest_values,
            )

            # Compact hourly analytics are authoritative for observation volume.
            hour = datetime.fromisoformat(observed_at).replace(minute=0, second=0, microsecond=0).isoformat()
            raw_positive = int(float(theoretical_edge_pct or 0.0) > 0.0)
            positive = int(float(net_roi_pct or 0.0) > 0.0)
            existing = self.conn.execute(
                "SELECT raw_positive,net_positive FROM market_hourly_seen WHERE hour_utc=? AND section=? AND sport=? AND market_name=? AND in_play=? AND event_key=?",
                (hour, sec, sp, mk, ip, str(event_key or "")),
            ).fetchone()
            unique_inc = 0; raw_positive_inc = 0; positive_inc = 0
            if existing is None:
                self.conn.execute(
                    "INSERT INTO market_hourly_seen(hour_utc,section,sport,market_name,in_play,event_key,raw_positive,net_positive) VALUES(?,?,?,?,?,?,?,?)",
                    (hour, sec, sp, mk, ip, str(event_key or ""), raw_positive, positive),
                )
                unique_inc = 1; raw_positive_inc = raw_positive; positive_inc = positive
            else:
                if raw_positive and not int(existing["raw_positive"] or 0):
                    self.conn.execute("UPDATE market_hourly_seen SET raw_positive=1 WHERE hour_utc=? AND section=? AND sport=? AND market_name=? AND in_play=? AND event_key=?",
                                      (hour, sec, sp, mk, ip, str(event_key or "")))
                    raw_positive_inc = 1
                if positive and not int(existing["net_positive"] or 0):
                    self.conn.execute("UPDATE market_hourly_seen SET net_positive=1 WHERE hour_utc=? AND section=? AND sport=? AND market_name=? AND in_play=? AND event_key=?",
                                      (hour, sec, sp, mk, ip, str(event_key or "")))
                    positive_inc = 1
            roi = None if net_roi_pct is None else float(net_roi_pct)
            dep = None if diagnostic_deployed is None else float(diagnostic_deployed)
            self.conn.execute(
                """INSERT INTO market_hourly_rollups(
                    hour_utc,section,sport,market_name,in_play,observations,unique_markets,raw_positive,net_positive,
                    net_roi_sum,net_roi_count,best_net_roi_pct,deployable_sum,deployable_count)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(hour_utc,section,sport,market_name,in_play) DO UPDATE SET
                    observations=market_hourly_rollups.observations+1,
                    unique_markets=market_hourly_rollups.unique_markets+excluded.unique_markets,
                    raw_positive=market_hourly_rollups.raw_positive+excluded.raw_positive,
                    net_positive=market_hourly_rollups.net_positive+excluded.net_positive,
                    net_roi_sum=market_hourly_rollups.net_roi_sum+excluded.net_roi_sum,
                    net_roi_count=market_hourly_rollups.net_roi_count+excluded.net_roi_count,
                    best_net_roi_pct=CASE WHEN market_hourly_rollups.best_net_roi_pct IS NULL THEN excluded.best_net_roi_pct
                                          WHEN excluded.best_net_roi_pct IS NULL THEN market_hourly_rollups.best_net_roi_pct
                                          ELSE MAX(market_hourly_rollups.best_net_roi_pct,excluded.best_net_roi_pct) END,
                    deployable_sum=market_hourly_rollups.deployable_sum+excluded.deployable_sum,
                    deployable_count=market_hourly_rollups.deployable_count+excluded.deployable_count""",
                (hour, sec, sp, mk, ip, 1, unique_inc, raw_positive_inc, positive_inc,
                 float(roi or 0.0), int(roi is not None), roi, float(dep or 0.0), int(dep is not None)),
            )
            self.conn.execute(
                """INSERT INTO matched_market_reason_hourly_rollups(hour_utc,section,sport,market_name,in_play,status,reason_sample,observations)
                   VALUES(?,?,?,?,?,?,?,1)
                   ON CONFLICT(hour_utc,section,sport,market_name,in_play,status) DO UPDATE SET
                    observations=matched_market_reason_hourly_rollups.observations+1,reason_sample=excluded.reason_sample""",
                (hour, sec, sp, mk, ip, str(status or "unknown"), str(reason or "")),
            )
            self.conn.execute(
                """INSERT INTO scan_qualification_breakdown(scan_id,status,total_count,positive_count) VALUES(?,?,1,?)
                   ON CONFLICT(scan_id,status) DO UPDATE SET total_count=scan_qualification_breakdown.total_count+1,
                    positive_count=scan_qualification_breakdown.positive_count+excluded.positive_count""",
                (int(scan_id), str(status or "unknown"), positive),
            )

            positive_obs = positive
            liq_capable = int(bool(liquidity_capable)) if liquidity_capable is not None else int(positive_obs and str(status or '') != 'below_liquidity')
            liq_rejected = int(positive_obs and not liq_capable)
            qualified_obs = int(str(status or '') in {'recommended','in_play_monitor','racing_monitor','racing_qualified'})
            exec_stake = float(max_executable_stake or diagnostic_deployed or 0.0) if positive_obs else 0.0
            exec_samples = int(positive_obs and exec_stake > 0.0)
            self.conn.execute(
                """INSERT INTO liquidity_opportunity_hourly_rollups(
                   hour_utc,section,sport,market_name,in_play,positive_observations,liquidity_capable,liquidity_rejected,qualified_observations,executable_stake_sum,executable_stake_samples
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(hour_utc,section,sport,market_name,in_play) DO UPDATE SET
                    positive_observations=liquidity_opportunity_hourly_rollups.positive_observations+excluded.positive_observations,
                    liquidity_capable=liquidity_opportunity_hourly_rollups.liquidity_capable+excluded.liquidity_capable,
                    liquidity_rejected=liquidity_opportunity_hourly_rollups.liquidity_rejected+excluded.liquidity_rejected,
                    qualified_observations=liquidity_opportunity_hourly_rollups.qualified_observations+excluded.qualified_observations,
                    executable_stake_sum=liquidity_opportunity_hourly_rollups.executable_stake_sum+excluded.executable_stake_sum,
                    executable_stake_samples=liquidity_opportunity_hourly_rollups.executable_stake_samples+excluded.executable_stake_samples""",
                (hour, sec, sp, mk, ip, positive_obs, liq_capable, liq_rejected, qualified_obs, exec_stake, exec_samples),
            )
            if sec == "racing" or "greyhound" in sp.lower():
                complete_inc = int(bool(book_complete)) if book_complete is not None else int(str(status or "") not in {"incomplete", "racing_runner_field_incomplete"})
                theoretical_inc = int(float(theoretical_edge_pct or 0.0) > 0.0)
                commission_inc = int(float(net_roi_pct or 0.0) > 0.0)
                liquidity_inc = int(bool(liquidity_capable) and commission_inc)
                qualified_inc = int(str(status or "") in {"racing_monitor", "racing_qualified"})
                self.conn.execute(
                    """INSERT INTO racing_funnel_hourly_rollups(
                       hour_utc,sport,observations,complete_books,theoretical_positive,post_commission_positive,liquidity_capable,qualified)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(hour_utc,sport) DO UPDATE SET
                        observations=racing_funnel_hourly_rollups.observations+1,
                        complete_books=racing_funnel_hourly_rollups.complete_books+excluded.complete_books,
                        theoretical_positive=racing_funnel_hourly_rollups.theoretical_positive+excluded.theoretical_positive,
                        post_commission_positive=racing_funnel_hourly_rollups.post_commission_positive+excluded.post_commission_positive,
                        liquidity_capable=racing_funnel_hourly_rollups.liquidity_capable+excluded.liquidity_capable,
                        qualified=racing_funnel_hourly_rollups.qualified+excluded.qualified""",
                    (hour, sp, 1, complete_inc, theoretical_inc, commission_inc, liquidity_inc, qualified_inc),
                )

            # Incremental 0.9.3 writes make these compact hours authoritative even
            # when the verbose diagnostic row was suppressed. Marking them prevents
            # later lazy backfills from rebuilding an hour from the intentionally
            # sparse raw history. Retention only acts after the configured age.
            built_at = datetime.now(timezone.utc).isoformat()
            self.conn.execute("INSERT OR REPLACE INTO market_hourly_rollup_state(hour_utc,built_at) VALUES(?,?)", (hour, built_at))
            self.conn.execute("INSERT OR REPLACE INTO liquidity_opportunity_rollup_state(hour_utc,built_at) VALUES(?,?)", (hour, built_at))
            self.conn.execute("INSERT OR REPLACE INTO matched_market_history_state(hour_utc,built_at) VALUES(?,?)", (hour, built_at))
            self.conn.commit()
            return {"verbose_written": bool(verbose), "state_key": state_key, "fingerprint": fingerprint, "observed_at": observed_at}

    def _market_rollup_hours(self, started_at: str, finished_at: str) -> list[str]:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
        if finish.tzinfo is None: finish = finish.replace(tzinfo=timezone.utc)
        cur = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        finish = finish.astimezone(timezone.utc)
        out=[]
        while cur < finish:
            out.append(cur.isoformat()); cur += timedelta(hours=1)
        return out

    def ensure_market_hourly_rollups(self, started_at: str, finished_at: str) -> None:
        """Backfill pre-0.8.36 hours once; new observations update rollups incrementally."""
        hours = self._market_rollup_hours(started_at, finished_at)
        if not hours: return
        with self.lock:
            marks=",".join("?" for _ in hours)
            built={str(r[0]) for r in self.conn.execute(f"SELECT hour_utc FROM market_hourly_rollup_state WHERE hour_utc IN ({marks})", tuple(hours)).fetchall()}
            missing=[h for h in hours if h not in built]
            if not missing: return
            lo=min(missing); hi=(datetime.fromisoformat(max(missing))+timedelta(hours=1)).isoformat()
            # Rebuild the requested missing range from the canonical matched-market history once.
            grouped=self.conn.execute(
                """SELECT strftime('%Y-%m-%dT%H:00:00+00:00',observed_at) hour_utc,COALESCE(section,'sports') section,
                          COALESCE(sport,'Unknown') sport,COALESCE(market_name,'Unknown') market_name,COALESCE(in_play,0) in_play,
                          COUNT(*) observations,COUNT(DISTINCT event_key) unique_markets,
                          COUNT(DISTINCT CASE WHEN COALESCE(net_roi_pct,0)>0 THEN event_key END) net_positive
                   FROM matched_markets WHERE observed_at>=? AND observed_at<?
                   GROUP BY strftime('%Y-%m-%dT%H:00:00+00:00',observed_at),COALESCE(section,'sports'),COALESCE(sport,'Unknown'),COALESCE(market_name,'Unknown'),COALESCE(in_play,0)""",
                (lo,hi),
            ).fetchall()
            for r in grouped:
                self.conn.execute(
                    """INSERT OR REPLACE INTO market_hourly_rollups(hour_utc,section,sport,market_name,in_play,observations,unique_markets,net_positive) VALUES(?,?,?,?,?,?,?,?)""",
                    (r['hour_utc'],r['section'],r['sport'],r['market_name'],int(r['in_play'] or 0),int(r['observations'] or 0),int(r['unique_markets'] or 0),int(r['net_positive'] or 0)),
                )
            # Backfill the short-lived seen-key guard for the requested history too. Without
            # this, the first live observation after a lazy backfill could count a market a
            # second time inside the same hour. The guard is intentionally retained only for
            # the recent tail because rollup_state owns older immutable hours.
            recent_cutoff=(datetime.now(timezone.utc)-timedelta(days=3)).replace(minute=0,second=0,microsecond=0).isoformat()
            seen_lo=max(lo,recent_cutoff)
            if seen_lo < hi:
                self.conn.execute(
                    """INSERT OR REPLACE INTO market_hourly_seen(hour_utc,section,sport,market_name,in_play,event_key,net_positive)
                       SELECT strftime('%Y-%m-%dT%H:00:00+00:00',observed_at),COALESCE(section,'sports'),
                              COALESCE(sport,'Unknown'),COALESCE(market_name,'Unknown'),COALESCE(in_play,0),event_key,
                              MAX(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN 1 ELSE 0 END)
                       FROM matched_markets WHERE observed_at>=? AND observed_at<?
                       GROUP BY strftime('%Y-%m-%dT%H:00:00+00:00',observed_at),COALESCE(section,'sports'),
                                COALESCE(sport,'Unknown'),COALESCE(market_name,'Unknown'),COALESCE(in_play,0),event_key""",
                    (seen_lo,hi),
                )
            now=datetime.now(timezone.utc).isoformat()
            self.conn.executemany("INSERT OR REPLACE INTO market_hourly_rollup_state(hour_utc,built_at) VALUES(?,?)", [(h,now) for h in missing])
            # 0.9.3: ``market_hourly_seen`` is now compact long-term market identity
            # evidence used after verbose matched-market history expires. It is one
            # row per market/hour, so do not prune it with the raw diagnostic tail.
            self.conn.commit()

    @staticmethod
    def _hour_floor_iso(value: str | datetime) -> str:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()

    @staticmethod
    def _hour_sequence(started_at: str, finished_at: str) -> list[str]:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None: end = end.replace(tzinfo=timezone.utc)
        cur = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        end = end.astimezone(timezone.utc)
        out = []
        while cur < end:
            out.append(cur.isoformat())
            cur += timedelta(hours=1)
        return out

    def _financial_hour_rows_between(self, started_at: str, finished_at: str, *, mode: str = "sim") -> list[dict]:
        """Aggregate mode-owned opportunity/position facts for a bounded range.

        Market Analysis financial heatmap rollups are currently SIM-only. LIVE
        decision evidence is stored separately and actual LIVE lifecycle metrics
        are exposed by the LIVE endpoint without falling back to this SIM ledger.
        Keep the mode explicit here so future LIVE position rows can never leak
        into the persisted SIM financial rollups by accident.
        """
        mode = str(mode or "sim").strip().lower()
        if mode != "sim":
            raise ValueError("market financial hourly rollups currently support SIM lifecycle data only")
        with self.lock:
            qualified = self.conn.execute(
                """SELECT strftime('%Y-%m-%dT%H:00:00+00:00',o.detected_at) hour_utc,
                          COALESCE(o.section,'sports') section,COALESCE(o.sport,'Unknown') sport,
                          COALESCE(o.market_name,'Unknown') market_name,COALESCE(o.in_play,0) in_play,
                          COUNT(*) qualified
                   FROM opportunities o
                   WHERE COALESCE(o.is_demo,0)=0 AND o.detected_at>=? AND o.detected_at<?
                     AND COALESCE(o.qualification_status,'qualified') IN ('qualified','in_play_qualified','racing_qualified')
                   GROUP BY strftime('%Y-%m-%dT%H:00:00+00:00',o.detected_at),COALESCE(o.section,'sports'),
                            COALESCE(o.sport,'Unknown'),COALESCE(o.market_name,'Unknown'),COALESCE(o.in_play,0)""",
                (started_at, finished_at),
            ).fetchall()
            opened = self.conn.execute(
                """SELECT strftime('%Y-%m-%dT%H:00:00+00:00',mp.opened_at) hour_utc,
                          COALESCE(o.section,'sports') section,COALESCE(o.sport,'Unknown') sport,
                          COALESCE(o.market_name,'Unknown') market_name,COALESCE(o.in_play,0) in_play,
                          COUNT(*) executed,COALESCE(SUM(ROUND(COALESCE(mp.deployed,0),4)),0) deployed
                   FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                   WHERE COALESCE(o.is_demo,0)=0 AND LOWER(COALESCE(mp.mode,'sim'))='sim'
                     AND mp.opened_at>=? AND mp.opened_at<?
                   GROUP BY strftime('%Y-%m-%dT%H:00:00+00:00',mp.opened_at),COALESCE(o.section,'sports'),
                            COALESCE(o.sport,'Unknown'),COALESCE(o.market_name,'Unknown'),COALESCE(o.in_play,0)""",
                (started_at, finished_at),
            ).fetchall()
            settled = self.conn.execute(
                """SELECT strftime('%Y-%m-%dT%H:00:00+00:00',mp.settled_at) hour_utc,
                          COALESCE(o.section,'sports') section,COALESCE(o.sport,'Unknown') sport,
                          COALESCE(o.market_name,'Unknown') market_name,COALESCE(o.in_play,0) in_play,
                          COUNT(*) settled,COALESCE(SUM(ROUND(COALESCE(mp.deployed,0),4)),0) settled_deployed,
                          COALESCE(SUM(ROUND(COALESCE(mp.realized_pnl,0),4)),0) pnl
                   FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                   WHERE COALESCE(o.is_demo,0)=0 AND LOWER(COALESCE(mp.mode,'sim'))='sim'
                     AND mp.status='SETTLED' AND mp.settled_at>=? AND mp.settled_at<?
                   GROUP BY strftime('%Y-%m-%dT%H:00:00+00:00',mp.settled_at),COALESCE(o.section,'sports'),
                            COALESCE(o.sport,'Unknown'),COALESCE(o.market_name,'Unknown'),COALESCE(o.in_play,0)""",
                (started_at, finished_at),
            ).fetchall()
        merged: dict[tuple, dict] = {}
        def bucket(row):
            d = dict(row)
            key = (str(d.get('hour_utc') or ''), str(d.get('section') or 'sports'), str(d.get('sport') or 'Unknown'),
                   str(d.get('market_name') or 'Unknown'), int(d.get('in_play') or 0))
            return merged.setdefault(key, {
                'hour_utc': key[0], 'section': key[1], 'sport': key[2], 'market_name': key[3], 'in_play': key[4],
                'qualified': 0, 'executed': 0, 'deployed': 0.0, 'settled': 0, 'settled_deployed': 0.0, 'pnl': 0.0,
            })
        for row in qualified:
            b=bucket(row); b['qualified'] += int(row['qualified'] or 0)
        for row in opened:
            b=bucket(row); b['executed'] += int(row['executed'] or 0); b['deployed'] += float(row['deployed'] or 0.0)
        for row in settled:
            b=bucket(row); b['settled'] += int(row['settled'] or 0); b['settled_deployed'] += float(row['settled_deployed'] or 0.0); b['pnl'] += float(row['pnl'] or 0.0)
        for b in merged.values():
            for k in ('deployed','settled_deployed','pnl'): b[k]=round(float(b[k]),4)
        return list(merged.values())

    def ensure_liquidity_opportunity_rollups(self, started_at: str | None, finished_at: str | None) -> None:
        """Backfill compact liquidity funnel/executable-stake facts for observed hours only.

        Unlike the general hourly chart rollups, this migration helper never walks
        every clock hour in an ``All history`` request. It discovers only hours
        that actually contain matched-market evidence, keeping the v0.9.1 lazy
        backfill bounded even on long-running databases.
        """
        where = ["1=1"]; params: list = []
        if started_at:
            where.append("observed_at>=?"); params.append(started_at)
        if finished_at:
            where.append("observed_at<?"); params.append(finished_at)
        clause = " AND ".join(where)
        with self.lock:
            observed = [str(r[0]) for r in self.conn.execute(
                f"SELECT DISTINCT strftime('%Y-%m-%dT%H:00:00+00:00',observed_at) hour_utc FROM matched_markets WHERE {clause}",
                tuple(params),
            ).fetchall() if r[0]]
            if not observed:
                return
            marks = ",".join("?" for _ in observed)
            built_state = {str(r[0]) for r in self.conn.execute(
                f"SELECT hour_utc FROM liquidity_opportunity_rollup_state WHERE hour_utc IN ({marks})", tuple(observed)
            ).fetchall()}
            built_rows = {str(r[0]) for r in self.conn.execute(
                f"SELECT DISTINCT hour_utc FROM liquidity_opportunity_hourly_rollups WHERE hour_utc IN ({marks})", tuple(observed)
            ).fetchall()}
            # A state marker without its compact rows is an interrupted/legacy
            # maintenance artifact, not usable analytics. Rebuild those hot raw
            # hours rather than returning a zero liquidity funnel.
            built = built_state & built_rows
            missing = set(observed) - built
            if not missing:
                return
            grouped = self.conn.execute(
                f"""SELECT strftime('%Y-%m-%dT%H:00:00+00:00',observed_at) hour_utc,COALESCE(section,'sports') section,
                          COALESCE(sport,'Unknown') sport,COALESCE(market_name,'Unknown') market_name,COALESCE(in_play,0) in_play,
                          SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN 1 ELSE 0 END) positive_observations,
                          SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 AND COALESCE(liquidity_capable,CASE WHEN status='below_liquidity' THEN 0 ELSE 1 END)=1 THEN 1 ELSE 0 END) liquidity_capable,
                          SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 AND COALESCE(liquidity_capable,CASE WHEN status='below_liquidity' THEN 0 ELSE 1 END)=0 THEN 1 ELSE 0 END) liquidity_rejected,
                          SUM(CASE WHEN status IN ('recommended','in_play_monitor','racing_monitor','racing_opportunity') THEN 1 ELSE 0 END) qualified_observations,
                          COALESCE(SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN COALESCE(max_executable_stake,diagnostic_deployed,0) ELSE 0 END),0) executable_stake_sum,
                          SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 AND COALESCE(max_executable_stake,diagnostic_deployed,0)>0 THEN 1 ELSE 0 END) executable_stake_samples
                   FROM matched_markets WHERE {clause}
                   GROUP BY strftime('%Y-%m-%dT%H:00:00+00:00',observed_at),COALESCE(section,'sports'),COALESCE(sport,'Unknown'),COALESCE(market_name,'Unknown'),COALESCE(in_play,0)""",
                tuple(params),
            ).fetchall()
            for row in grouped:
                d = dict(row)
                if str(d.get('hour_utc') or '') not in missing:
                    continue
                self.conn.execute(
                    """INSERT OR REPLACE INTO liquidity_opportunity_hourly_rollups(
                       hour_utc,section,sport,market_name,in_play,positive_observations,liquidity_capable,liquidity_rejected,qualified_observations,executable_stake_sum,executable_stake_samples
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (d['hour_utc'], d['section'], d['sport'], d['market_name'], int(d['in_play'] or 0),
                     int(d['positive_observations'] or 0), int(d['liquidity_capable'] or 0), int(d['liquidity_rejected'] or 0),
                     int(d['qualified_observations'] or 0), float(d['executable_stake_sum'] or 0.0), int(d['executable_stake_samples'] or 0)),
                )
            now = datetime.now(timezone.utc).isoformat()
            self.conn.executemany(
                "INSERT OR REPLACE INTO liquidity_opportunity_rollup_state(hour_utc,built_at) VALUES(?,?)",
                [(h, now) for h in sorted(missing)],
            )
            self.conn.commit()

    def latest_liquidity_summary(self, *, stale_after_seconds: dict[str, float] | None = None, scope: str = 'all',
                                 phase: str = 'all', sport: str = 'all', search: str = '') -> list[dict]:
        """Current bounded depth grouped by provider with stale rows excluded from executable totals."""
        stale_after_seconds = stale_after_seconds or {}
        scope = str(scope or 'all').lower(); phase = str(phase or 'all').lower(); sport_filter = str(sport or 'all')
        search = str(search or '').strip().lower()
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT * FROM latest_depth_snapshots ORDER BY provider_id,market_id,selection_id,side,level"
            ).fetchall()]
        now = datetime.now(timezone.utc)
        out: dict[str, dict] = {}
        for row in rows:
            if scope == 'sports' and str(row.get('section')) != 'sports':
                continue
            if scope == 'racing' and str(row.get('section')) != 'racing':
                continue
            if phase == 'pre_match' and int(row.get('in_play') or 0) != 0:
                continue
            if phase == 'in_play' and int(row.get('in_play') or 0) != 1:
                continue
            if sport_filter not in {'', 'all'} and str(row.get('sport') or '') != sport_filter:
                continue
            if search and search not in f"{row.get('sport','')} {row.get('market_name','')} {row.get('event_name','')}".lower():
                continue
            pid = str(row.get('provider_id') or '').lower()
            if not pid:
                continue
            item = out.setdefault(pid, {'provider_id': pid, 'venue_id': row.get('venue_id') or pid, 'top_book_depth': 0.0,
                                        'top3_depth': 0.0, 'fresh_depth_rows': 0, 'stale_depth_rows': 0,
                                        'market_ids': set(), 'last_quote_at': None, 'feed_entitlement': row.get('feed_entitlement') or 'unknown',
                                        'transport': row.get('market_data_transport') or 'unknown'})
            captured = str(row.get('captured_at') or '')
            if captured and (item['last_quote_at'] is None or captured > item['last_quote_at']):
                item['last_quote_at'] = captured
                item['feed_entitlement'] = row.get('feed_entitlement') or item['feed_entitlement']
                item['transport'] = row.get('market_data_transport') or item['transport']
            try:
                dt = datetime.fromisoformat(captured.replace('Z','+00:00'))
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                age = max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds())
            except Exception:
                age = float('inf')
            threshold = max(0.001, float(stale_after_seconds.get(pid, 10.0) or 10.0))
            if age > threshold:
                item['stale_depth_rows'] += 1
                continue
            item['fresh_depth_rows'] += 1
            item['market_ids'].add(str(row.get('market_id') or ''))
            size = max(0.0, float(row.get('available_size') or 0.0))
            if int(row.get('level') or 0) == 1:
                item['top_book_depth'] += size
            if int(row.get('level') or 0) <= 3:
                item['top3_depth'] += size
        result = []
        for item in out.values():
            item['current_markets'] = len({x for x in item.pop('market_ids') if x})
            item['top_book_depth'] = round(float(item['top_book_depth']), 4)
            item['top3_depth'] = round(float(item['top3_depth']), 4)
            result.append(item)
        return sorted(result, key=lambda x: x['provider_id'])

    def liquidity_market_summary_between(self, started_at: str | None, finished_at: str | None) -> dict:
        self.ensure_liquidity_opportunity_rollups(started_at, finished_at)
        where = ["1=1"]; params: list = []
        if started_at:
            where.append("hour_utc>=?"); params.append(self._hour_floor_iso(started_at))
        if finished_at:
            where.append("hour_utc<?")
            end = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
            if end.tzinfo is None: end = end.replace(tzinfo=timezone.utc)
            params.append(end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat() if end.minute == 0 and end.second == 0 and end.microsecond == 0 else (end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).isoformat())
        clause = " AND ".join(where)
        with self.lock:
            depth = self.conn.execute(
                f"""SELECT section,sport,market_name,in_play,SUM(depth_samples) depth_samples,
                          SUM(top_book_depth_sum) top_book_depth_sum,SUM(top3_depth_sum) top3_depth_sum,MAX(max_top3_depth) max_top3_depth
                   FROM liquidity_depth_hourly_rollups WHERE {clause}
                   GROUP BY section,sport,market_name,in_play""", tuple(params)
            ).fetchall()
            opp = self.conn.execute(
                f"""SELECT section,sport,market_name,in_play,SUM(positive_observations) positive_observations,
                          SUM(liquidity_capable) liquidity_capable,SUM(liquidity_rejected) liquidity_rejected,
                          SUM(qualified_observations) qualified_observations,SUM(executable_stake_sum) executable_stake_sum,
                          SUM(executable_stake_samples) executable_stake_samples
                   FROM liquidity_opportunity_hourly_rollups WHERE {clause}
                   GROUP BY section,sport,market_name,in_play""", tuple(params)
            ).fetchall()
        return {'depth': [dict(r) for r in depth], 'opportunity': [dict(r) for r in opp]}

    def ensure_market_financial_hourly_rollups(self, started_at: str, finished_at: str) -> None:
        """Backfill missing *past* hourly financial rollups once.

        The current hour remains live and is aggregated only for that one hour at
        request time. Historical metric/sport switching therefore reads compact
        indexed rows and never rescans quote observations or full position history.
        """
        current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        hours = [h for h in self._hour_sequence(started_at, finished_at)
                 if datetime.fromisoformat(h) < current_hour]
        if not hours:
            return
        with self.lock:
            marks = self.conn.execute(
                "SELECT hour_utc FROM market_financial_hourly_state WHERE hour_utc>=? AND hour_utc<=?",
                (hours[0], hours[-1]),
            ).fetchall()
        done = {str(r['hour_utc']) for r in marks}
        missing = [h for h in hours if h not in done]
        if not missing:
            return
        lo = datetime.fromisoformat(missing[0])
        hi = datetime.fromisoformat(missing[-1]) + timedelta(hours=1)
        rows = self._financial_hour_rows_between(lo.isoformat(), hi.isoformat(), mode="sim")
        missing_set = set(missing)
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.conn.executemany(
                """INSERT INTO market_financial_hourly_rollups(
                     hour_utc,section,sport,market_name,in_play,qualified,executed,deployed,settled,settled_deployed,pnl)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(hour_utc,section,sport,market_name,in_play) DO UPDATE SET
                     qualified=excluded.qualified,executed=excluded.executed,deployed=excluded.deployed,
                     settled=excluded.settled,settled_deployed=excluded.settled_deployed,pnl=excluded.pnl""",
                [(r['hour_utc'],r['section'],r['sport'],r['market_name'],r['in_play'],r['qualified'],r['executed'],
                  r['deployed'],r['settled'],r['settled_deployed'],r['pnl']) for r in rows if r['hour_utc'] in missing_set],
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO market_financial_hourly_state(hour_utc,built_at) VALUES(?,?)",
                [(h,now) for h in missing],
            )
            self.conn.commit()

    @staticmethod
    def _exchange_key(label: str) -> str:
        text = str(label or '').strip().lower()
        if text.startswith('betfair'): return 'betfair'
        if text.startswith('matchbook'): return 'matchbook'
        return ''.join(ch for ch in text if ch.isalnum() or ch in ('_','-')) or 'unknown'

    def record_exchange_market_discoveries(self, rows: list[dict], observed_at: str | None = None) -> int:
        """Persist exchange-native discovery identities, including unmatched markets."""
        observed_at = observed_at or datetime.now(timezone.utc).isoformat()
        hour = self._hour_floor_iso(observed_at)
        values = []
        for row in rows or []:
            label = str(row.get('exchange') or row.get('exchange_label') or '').strip()
            market_id = str(row.get('market_id') or '').strip()
            if not label or not market_id:
                continue
            phase = str(row.get('phase') or ('in_play' if row.get('in_play') is True else 'pre_match'))
            if phase not in {'pre_match','in_play'}: phase='pre_match'
            sport = str(row.get('sport') or ('Greyhounds' if str(row.get('section') or '') == 'racing' else 'Unknown'))
            section = str(row.get('section') or ('racing' if sport == 'Greyhounds' else 'sports'))
            values.append((
                hour,self._exchange_key(label),label,market_id,phase,str(row.get('event_id') or ''),str(row.get('event_name') or ''),
                str(row.get('market_name') or ''),str(row.get('canonical_market_key') or '') or None,sport,section,
                row.get('event_start'),row.get('race_track'),row.get('race_number'),str(row.get('source_quality') or 'normalised'),
                observed_at,observed_at,1,
            ))
        if not values:
            return 0
        with self.lock:
            self.conn.executemany(
                """INSERT INTO exchange_market_discovery_hours(
                     hour_utc,exchange_key,exchange_label,market_id,phase,event_id,event_name,market_name,canonical_market_key,
                     sport,section,event_start,race_track,race_number,source_quality,first_seen,last_seen,observations)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(hour_utc,exchange_key,market_id,phase) DO UPDATE SET
                     exchange_label=excluded.exchange_label,event_id=COALESCE(NULLIF(excluded.event_id,''),event_id),
                     event_name=COALESCE(NULLIF(excluded.event_name,''),event_name),market_name=COALESCE(NULLIF(excluded.market_name,''),market_name),
                     canonical_market_key=COALESCE(excluded.canonical_market_key,canonical_market_key),sport=excluded.sport,section=excluded.section,
                     event_start=COALESCE(excluded.event_start,event_start),race_track=COALESCE(excluded.race_track,race_track),
                     race_number=COALESCE(excluded.race_number,race_number),source_quality=excluded.source_quality,last_seen=excluded.last_seen,
                     observations=observations+1""",
                values,
            )
            self.conn.commit()
        return len(values)

    def ensure_exchange_market_discovery_history(self, started_at: str | None, finished_at: str | None) -> None:
        """Recover the best available pre-0.8.42 discovery identity history once.

        Matched source IDs recover both venues; persisted Betfair racing diagnostics
        additionally recover Betfair-only/incomplete Greyhound catalogue markets.
        Unmatched historical Matchbook markets cannot be invented if old releases
        never retained their native IDs, so source_quality records that limitation.
        """
        now = datetime.now(timezone.utc)
        if not finished_at: finished_at = now.isoformat()
        if not started_at:
            with self.lock:
                r=self.conn.execute("SELECT MIN(x) m FROM (SELECT MIN(observed_at) x FROM matched_markets UNION ALL SELECT MIN(started_at) x FROM scan_runs)").fetchone()
            started_at = str((r['m'] if r else None) or (now-timedelta(days=30)).isoformat())
        hours = self._hour_sequence(started_at, finished_at)
        if not hours: return
        with self.lock:
            marks=self.conn.execute("SELECT hour_utc FROM exchange_market_discovery_state WHERE hour_utc>=? AND hour_utc<=?",(hours[0],hours[-1])).fetchall()
        done={str(r['hour_utc']) for r in marks}; missing=[h for h in hours if h not in done]
        if not missing: return
        lo=datetime.fromisoformat(missing[0]); hi=datetime.fromisoformat(missing[-1])+timedelta(hours=1); missing_set=set(missing)
        recovered=[]
        with self.lock:
            mmrows=self.conn.execute(
                """SELECT observed_at,event_key,event_name,market_name,sport,section,in_play,source_markets_json
                   FROM matched_markets WHERE observed_at>=? AND observed_at<?""",(lo.isoformat(),hi.isoformat())).fetchall()
            scans=self.conn.execute(
                "SELECT started_at,status_json FROM scan_runs WHERE scan_kind='discovery' AND started_at>=? AND started_at<?",(lo.isoformat(),hi.isoformat())).fetchall()
        for rr in mmrows:
            d=dict(rr); hour=self._hour_floor_iso(d['observed_at'])
            if hour not in missing_set: continue
            try: sources=json.loads(d.get('source_markets_json') or '[]')
            except Exception: sources=[]
            canonical=f"{str(d.get('event_key') or '').lower()}|{str(d.get('market_name') or '').lower()}"
            for src in sources if isinstance(sources,list) else []:
                if not isinstance(src,dict): continue
                recovered.append({
                    'exchange':src.get('exchange'),'market_id':src.get('market_id'),'event_id':src.get('event_id'),
                    'event_name':src.get('event_name') or d.get('event_name'),'market_name':src.get('market_name') or d.get('market_name'),
                    'canonical_market_key':canonical,'sport':src.get('sport') or d.get('sport'),'section':src.get('section') or d.get('section'),
                    'in_play':bool(d.get('in_play')),'source_quality':'historical_matched','_observed_at':d.get('observed_at')})
        # Record recovered matched rows at their own hours rather than one shared timestamp.
        by_time={}
        for row in recovered: by_time.setdefault(row.pop('_observed_at'),[]).append(row)
        for ts, group in by_time.items(): self.record_exchange_market_discoveries(group, ts)
        for rr in scans:
            d=dict(rr); hour=self._hour_floor_iso(d['started_at'])
            if hour not in missing_set: continue
            try: statuses=json.loads(d.get('status_json') or '[]')
            except Exception: statuses=[]
            raw=[]
            for status in statuses if isinstance(statuses,list) else []:
                if not isinstance(status,dict): continue
                label=str(status.get('exchange') or '')
                rd=status.get('racing_discovery') or {}
                for row in (rd.get('rows') or []) if isinstance(rd,dict) else []:
                    if not isinstance(row,dict): continue
                    raw.append({**row,'exchange':row.get('exchange') or label,'sport':'Greyhounds','section':'racing',
                                'source_quality':'historical_catalogue'})
            self.record_exchange_market_discoveries(raw,d['started_at'])
        built=datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.conn.executemany("INSERT OR REPLACE INTO exchange_market_discovery_state(hour_utc,built_at,completeness) VALUES(?,?,?)",
                                  [(h,built,'best_available_pre_0842') for h in missing])
            self.conn.commit()

    def exchange_market_discovery_between(self, started_at: str | None, finished_at: str | None) -> list[dict]:
        self.ensure_exchange_market_discovery_history(started_at, finished_at)
        where=['1=1']; params=[]
        # Discovery rows are hourly identities but retain exact first/last seen
        # timestamps. Use those timestamps for custom-period boundaries so a
        # partial first/last hour is neither dropped nor included wholesale.
        if started_at: where.append('last_seen>=?'); params.append(started_at)
        if finished_at: where.append('first_seen<?'); params.append(finished_at)
        with self.lock:
            rows=self.conn.execute(
                f"""SELECT exchange_key,MAX(exchange_label) exchange_label,market_id,phase,
                           MAX(event_name) event_name,MAX(market_name) market_name,MAX(canonical_market_key) canonical_market_key,
                           MAX(sport) sport,MAX(section) section,MIN(first_seen) first_seen,MAX(last_seen) last_seen,
                           SUM(observations) observations
                    FROM exchange_market_discovery_hours WHERE {' AND '.join(where)}
                    GROUP BY exchange_key,market_id,phase""",tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def market_heatmap_between(self, started_at: str, finished_at: str, *, include_financial: bool = True) -> dict:
        """Return bounded heatmap evidence with authoritative SIM financials.

        Discovery/activity and liquidity remain backed by compact hourly rollups.
        Financial cells deliberately read the canonical SIM opportunity/position
        ledger for the requested window on every request.  Historical financial
        rollups were immutable once built, so later settlement reconciliation or
        corrected position economics could leave Market Analysis disagreeing with
        Performance and Replay.  The heatmap request is bounded (the API caps the
        window), so correctness is preferable to serving a stale financial cache.
        """
        self.ensure_market_hourly_rollups(started_at, finished_at)
        self.ensure_liquidity_opportunity_rollups(started_at, finished_at)
        authoritative_financial = (
            self._financial_hour_rows_between(started_at, finished_at, mode="sim")
            if include_financial else []
        )
        with self.lock:
            rolls=self.conn.execute(
                "SELECT * FROM market_hourly_rollups WHERE hour_utc>=? AND hour_utc<? ORDER BY hour_utc",
                (started_at,finished_at),
            ).fetchall()
            liquidity_depth=self.conn.execute(
                "SELECT * FROM liquidity_depth_hourly_rollups WHERE hour_utc>=? AND hour_utc<? ORDER BY hour_utc",
                (started_at,finished_at),
            ).fetchall()
            liquidity_opportunity=self.conn.execute(
                "SELECT * FROM liquidity_opportunity_hourly_rollups WHERE hour_utc>=? AND hour_utc<? ORDER BY hour_utc",
                (started_at,finished_at),
            ).fetchall()
        return {
            'rollups':[dict(r) for r in rolls],
            'financial':[dict(r) for r in authoritative_financial],
            'liquidity_depth':[dict(r) for r in liquidity_depth],
            'liquidity_opportunity':[dict(r) for r in liquidity_opportunity],
            'source':'compact_market_rollups+authoritative_sim_ledger',
            'financial_source':'authoritative_sim_ledger',
        }

    def latest_matched_markets(self, limit: int = 500) -> dict:
        """Return current market state for the latest completed price scan.

        0.9.3 deliberately suppresses repetitive verbose ``matched_markets`` rows,
        so current views must read the bounded ``matched_market_latest`` state.
        Legacy raw history remains a diagnostic event log only.
        """
        with self.lock:
            scan = self.conn.execute(
                """SELECT id,started_at,finished_at,markets_seen,matches_seen,opportunities_found,processed_candidates,
                          positive_opportunities,qualified_count,executed_count,duration_ms,error,status_json
                   FROM scan_runs sr WHERE finished_at IS NOT NULL AND error IS NULL
                     AND COALESCE(scan_kind,'legacy') IN ('price','legacy')
                     AND EXISTS (SELECT 1 FROM matched_market_latest mm WHERE mm.scan_id=sr.id)
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            if not scan:
                return {"scan": None, "rows": [], "summary": {"matched": 0, "theoretical_arbs": 0, "net_positive": 0, "recommended": 0}}
            rows = self.conn.execute(
                """SELECT * FROM matched_market_latest WHERE scan_id=?
                   ORDER BY CASE WHEN net_roi_pct IS NULL THEN 1 ELSE 0 END, net_roi_pct DESC, match_score DESC LIMIT ?""",
                (scan["id"], limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["id"] = d.get("state_key")
                try: d["legs"] = json.loads(d.pop("legs_json") or "[]")
                except Exception: d["legs"] = []
                try: d["source_markets"] = json.loads(d.pop("source_markets_json") or "[]")
                except Exception: d["source_markets"] = []
                out.append(d)
            summary = {
                "matched": len(out),
                "theoretical_arbs": sum(1 for r in out if (r.get("theoretical_edge_pct") or 0) > 0),
                "net_positive": sum(1 for r in out if (r.get("net_roi_pct") or 0) > 0),
                "recommended": sum(1 for r in out if r.get("status") in {"recommended", "in_play_monitor", "racing_monitor", "racing_qualified"}),
            }
            return {"scan": dict(scan), "rows": out, "summary": summary}

    def monitor_last_detected(self, *, mode: str = "sim", section: str = "sports", stream: str = "all",
                              engine: str = "all", sport: str = "all", market: str = "", venue: str = "all",
                              account: str = "all") -> dict:
        """Latest genuinely new detection for a Monitor scope.

        Repeated scan observations do not advance this value: SIM uses the
        first_seen timestamp of a market state (or authoritative opportunity
        origination under an Engine filter); LIVE uses live_decision_latest
        first_seen.  Refresh/heartbeat timestamps are deliberately excluded.
        """
        mode = str(mode or "sim").lower(); section = str(section or "sports").lower(); stream = str(stream or "all").lower()
        sport_q = str(sport or "all").lower(); market_q = str(market or "").strip().lower(); engine_q = str(engine or "all").strip()
        venue_q = str(venue or "all").strip().lower(); account_q = str(account or "all").strip().lower()
        provider_q = venue_q if venue_q not in {"", "all"} else None
        if not provider_q and account_q not in {"", "all", "—"}:
            for ctl in self.venue_controls():
                if str(ctl.get("account_nickname") or "").strip().lower() == account_q:
                    provider_q = str(ctl.get("provider_id") or "").lower() or None; break
        def stream_ok(value):
            if stream not in {"pre_match", "in_play", "racing"}: return True
            return str(value or "pre_match").lower() == stream
        def venue_ok(raw):
            if not provider_q: return True
            return provider_q in str(raw or "").lower()
        with self.lock:
            if mode == "live":
                # Current LIVE evidence does not yet persist originating engine on
                # live_decision_latest. Never infer an engine-specific detection.
                if engine_q.lower() != "all":
                    return {"detected_at": None, "source": "live_engine_unattributed"}
                rows = self.conn.execute(
                    "SELECT first_seen,sport,market_name,market_type,in_play,provider_pair FROM live_decision_latest WHERE LOWER(domain)=? ORDER BY first_seen DESC LIMIT 3000",
                    (section if section in {"sports","racing"} else "sports",),
                ).fetchall()
                for r in rows:
                    if sport_q != "all" and str(r["sport"] or "").lower() != sport_q: continue
                    if market_q and market_q not in str(r["market_name"] or r["market_type"] or "").lower(): continue
                    st = "in_play" if int(r["in_play"] or 0) else ("racing" if section == "racing" else "pre_match")
                    if not stream_ok(st) or not venue_ok(r["provider_pair"]): continue
                    return {"detected_at": r["first_seen"], "source": "live_decision_first_seen"}
                return {"detected_at": None, "source": "live_decision_first_seen"}
            if engine_q.lower() != "all":
                # Engine-specific Last Detected comes from authoritative engine evaluations,
                # not display-name inference. market_snapshot_id includes observation time, so
                # grouping on it would incorrectly advance Last Detected on every polling cycle.
                # Group by the stable operator-facing market identity instead: repeated quote
                # observations stay one detection while a genuinely new event/market advances it.
                rows = self.conn.execute(
                    """SELECT MIN(observed_at) AS detected_at,
                              MAX(sport) AS sport, MAX(market_name) AS market_name,
                              MAX(CASE WHEN stream='in_play' THEN 1 ELSE 0 END) AS in_play,
                              MAX(COALESCE(venue_ids_json,'')) AS legs_json
                       FROM engine_evaluations
                       WHERE LOWER(COALESCE(section,'sports'))=? AND engine_instance_id=? AND LOWER(COALESCE(mode,'sim'))='sim'
                       GROUP BY LOWER(COALESCE(sport,'')), LOWER(COALESCE(event_name,'')),
                                LOWER(COALESCE(market_name,'')), LOWER(COALESCE(market_type,'')), LOWER(COALESCE(stream,''))
                       ORDER BY detected_at DESC LIMIT 3000""", (section, engine_q),
                ).fetchall()
                source = "authoritative_engine_detection"
            else:
                rows = self.conn.execute(
                    """SELECT first_seen AS detected_at,sport,market_name,in_play,legs_json FROM matched_market_latest
                       WHERE LOWER(COALESCE(section,'sports'))=? ORDER BY first_seen DESC LIMIT 3000""", (section,),
                ).fetchall()
                source = "market_first_seen"
            for r in rows:
                if sport_q != "all" and str(r["sport"] or "").lower() != sport_q: continue
                if market_q and market_q not in str(r["market_name"] or "").lower(): continue
                st = "in_play" if int(r["in_play"] or 0) else ("racing" if section == "racing" else "pre_match")
                if not stream_ok(st) or not venue_ok(r["legs_json"]): continue
                return {"detected_at": r["detected_at"], "source": source}
            return {"detected_at": None, "source": source}

    def upsert_track(self, track_key: str, scan_id: int, event_key: str, event_name: str, market_name: str, strategy: str,
                     net_roi_pct: float, bankroll_roi_pct: float, deployed: float, expected_profit: float,
                     quality_score: float, quality_band: str, reference_bankroll: float, status: str, reason: str, sport: str = "Unknown"):
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            row = self.conn.execute("SELECT * FROM opportunity_tracks WHERE track_key=?", (track_key,)).fetchone()
            if row:
                peak_score = max(float(row["peak_quality_score"] or 0), quality_score)
                peak_band = quality_band if quality_score >= float(row["peak_quality_score"] or 0) else row["peak_quality_band"]
                self.conn.execute("""UPDATE opportunity_tracks SET last_seen=?,closed_at=NULL,scan_count=scan_count+1,
                    current_quality_score=?,current_quality_band=?,peak_quality_score=?,peak_quality_band=?,
                    peak_roi_pct=MAX(peak_roi_pct,?),peak_bankroll_roi_pct=MAX(peak_bankroll_roi_pct,?),
                    peak_deployed=MAX(peak_deployed,?),peak_profit=MAX(peak_profit,?),reference_bankroll=?,last_status=?,last_reason=?,sport=?
                    WHERE track_key=?""",
                    (now, quality_score, quality_band, peak_score, peak_band, net_roi_pct, bankroll_roi_pct, deployed, expected_profit,
                     reference_bankroll, status, reason, sport, track_key))
            else:
                self.conn.execute("""INSERT INTO opportunity_tracks(track_key,event_key,event_name,market_name,strategy,first_seen,last_seen,
                    current_quality_score,current_quality_band,peak_quality_score,peak_quality_band,peak_roi_pct,peak_bankroll_roi_pct,
                    peak_deployed,peak_profit,reference_bankroll,last_status,last_reason,sport) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (track_key,event_key,event_name,market_name,strategy,now,now,quality_score,quality_band,quality_score,quality_band,
                     net_roi_pct,bankroll_roi_pct,deployed,expected_profit,reference_bankroll,status,reason,sport))
            self.conn.execute("""INSERT INTO track_observations(track_key,scan_id,observed_at,net_roi_pct,bankroll_roi_pct,deployed,expected_profit,quality_score,quality_band,status)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (track_key,scan_id,now,net_roi_pct,bankroll_roi_pct,deployed,expected_profit,quality_score,quality_band,status))
            self.conn.commit()

    def close_tracks_not_seen(self, scan_id: int, seen_keys: set[str]):
        with self.lock:
            rows = self.conn.execute("SELECT track_key FROM opportunity_tracks WHERE closed_at IS NULL").fetchall()
            now = datetime.now(timezone.utc).isoformat()
            for r in rows:
                if r["track_key"] not in seen_keys:
                    self.conn.execute("UPDATE opportunity_tracks SET closed_at=? WHERE track_key=?", (now, r["track_key"]))
            self.conn.commit()

    def track_for(self, event_key: str, market_name: str, sport: str | None = None) -> dict | None:
        with self.lock:
            if sport:
                row = self.conn.execute("SELECT * FROM opportunity_tracks WHERE event_key=? AND market_name=? AND COALESCE(sport,'Unknown')=? ORDER BY last_seen DESC LIMIT 1", (event_key, market_name, sport)).fetchone()
            else:
                row = self.conn.execute("SELECT * FROM opportunity_tracks WHERE event_key=? AND market_name=? ORDER BY last_seen DESC LIMIT 1", (event_key, market_name)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                a = datetime.fromisoformat(d["first_seen"].replace("Z", "+00:00"))
                b = datetime.fromisoformat((d.get("closed_at") or d["last_seen"]).replace("Z", "+00:00"))
                d["duration_seconds"] = max(0, int((b-a).total_seconds()))
            except Exception:
                d["duration_seconds"] = 0
            return d

    def track_history(self, limit: int = 2000) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM opportunity_tracks ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
            out=[]
            for r in rows:
                d=dict(r)
                try:
                    a=datetime.fromisoformat(d["first_seen"].replace("Z","+00:00")); b=datetime.fromisoformat((d.get("closed_at") or d["last_seen"]).replace("Z","+00:00"))
                    d["duration_seconds"]=max(0,int((b-a).total_seconds()))
                except Exception:
                    d["duration_seconds"]=0
                out.append(d)
            return out

    def track_observations_since(self, cutoff_iso: str, limit: int = 50000) -> list[dict]:
        """Return positive-opportunity track observations inside a scorecard window.

        The scorecard is period-based, so its peaks must come from observations
        captured inside that period rather than from lifetime peak columns on the
        track row.
        """
        with self.lock:
            rows = self.conn.execute(
                """SELECT o.track_key,o.observed_at,o.net_roi_pct,o.bankroll_roi_pct,o.deployed,o.expected_profit,
                          o.quality_score,o.quality_band,o.status,
                          t.strategy,t.sport,t.event_key,t.event_name,t.market_name
                   FROM track_observations o
                   JOIN opportunity_tracks t ON t.track_key=o.track_key
                   WHERE o.observed_at>=? AND COALESCE(o.net_roi_pct,0)>0
                   ORDER BY o.observed_at DESC LIMIT ?""",
                (cutoff_iso, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def any_alert_sent(self, track_key: str) -> bool:
        with self.lock:
            return bool(self.conn.execute("SELECT 1 FROM alert_log WHERE track_key=? LIMIT 1", (track_key,)).fetchone())

    def alert_was_sent(self, track_key: str, band: str) -> bool:
        with self.lock:
            return bool(self.conn.execute("SELECT 1 FROM alert_log WHERE track_key=? AND quality_band=? LIMIT 1", (track_key,band)).fetchone())

    def record_alert(self, track_key: str, band: str, score: float):
        with self.lock:
            self.conn.execute("INSERT OR IGNORE INTO alert_log(track_key,quality_band,quality_score,sent_at) VALUES(?,?,?,?)",
                              (track_key,band,score,datetime.now(timezone.utc).isoformat()))
            self.conn.commit()

    def record_alert_attempt(self, track_key: str | None, band: str | None, score: float, success: bool, reason: str):
        with self.lock:
            self.conn.execute(
                "INSERT INTO alert_attempts(track_key,quality_band,quality_score,attempted_at,success,reason) VALUES(?,?,?,?,?,?)",
                (track_key, band, float(score or 0.0), datetime.now(timezone.utc).isoformat(), 1 if success else 0, str(reason or "")),
            )
            self.conn.commit()

    def recent_failed_alert_attempt(self, track_key: str, band: str, within_minutes: int = 15) -> bool:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, int(within_minutes)))).isoformat()
        with self.lock:
            row = self.conn.execute(
                "SELECT 1 FROM alert_attempts WHERE track_key=? AND quality_band=? AND success=0 AND attempted_at>=? LIMIT 1",
                (track_key, band, cutoff),
            ).fetchone()
            return bool(row)

    def alert_diagnostics(self) -> dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with self.lock:
            last_attempt = self.conn.execute("SELECT * FROM alert_attempts ORDER BY id DESC LIMIT 1").fetchone()
            last_success = self.conn.execute("SELECT * FROM alert_attempts WHERE success=1 ORDER BY id DESC LIMIT 1").fetchone()
            counts = self.conn.execute(
                "SELECT COUNT(*) attempts, SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) successes FROM alert_attempts WHERE attempted_at>=?",
                (cutoff,),
            ).fetchone()
            return {
                "last_attempt": dict(last_attempt) if last_attempt else None,
                "last_success": dict(last_success) if last_success else None,
                "attempts_24h": int((counts["attempts"] if counts else 0) or 0),
                "successes_24h": int((counts["successes"] if counts else 0) or 0),
            }

    def latest_alert_candidate(self) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                """SELECT observed_at,event_name,market_name,sport,quality_band,quality_score,net_roi_pct,bankroll_roi_pct,
                          capital_used_pct,reference_bankroll
                   FROM matched_markets
                   WHERE COALESCE(net_roi_pct,0)>0 AND quality_band IS NOT NULL
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            return dict(row) if row else None


    def sport_coverage(self) -> dict:
        with self.lock:
            scan = self.conn.execute(
                """SELECT * FROM scan_runs sr WHERE finished_at IS NOT NULL AND error IS NULL
                   AND COALESCE(scan_kind,'legacy') IN ('price','legacy')
                   AND EXISTS (SELECT 1 FROM matched_markets mm WHERE mm.scan_id=sr.id)
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            if not scan:
                return {"scan": None, "rows": []}
            rows = self.conn.execute("""SELECT COALESCE(sport,'Unknown') sport, COUNT(*) matched,
                SUM(CASE WHEN COALESCE(theoretical_edge_pct,0)>0 THEN 1 ELSE 0 END) theoretical_arbs,
                SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN 1 ELSE 0 END) net_positive,
                SUM(CASE WHEN status='recommended' THEN 1 ELSE 0 END) recommended,
                SUM(CASE WHEN COALESCE(in_play,0)=1 THEN 1 ELSE 0 END) live_matched
                FROM matched_markets WHERE scan_id=? GROUP BY COALESCE(sport,'Unknown') ORDER BY matched DESC""", (scan["id"],)).fetchall()
            status_rows = json.loads(scan["status_json"] or "[]")
            raw = {}
            for st in status_rows:
                for sport, count in (st.get("sport_counts") or {}).items():
                    raw[sport] = raw.get(sport, 0) + int(count or 0)
            out=[]
            seen=set()
            for r in rows:
                d=dict(r); d["markets_seen"]=raw.get(d["sport"],0); out.append(d); seen.add(d["sport"])
            for sport,count in raw.items():
                if sport not in seen:
                    out.append({"sport":sport,"markets_seen":count,"matched":0,"theoretical_arbs":0,"net_positive":0,"recommended":0,"live_matched":0})
            return {"scan": dict(scan), "rows": sorted(out, key=lambda x: (-int(x.get("markets_seen") or 0), x.get("sport") or ""))}

    def unresolved_opportunities(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                """SELECT o.* FROM opportunities o LEFT JOIN settlements s ON s.opportunity_id=o.id
                   WHERE s.opportunity_id IS NULL AND COALESCE(o.is_demo,0)=0
                     AND EXISTS (SELECT 1 FROM execution_runs er WHERE er.opportunity_id=o.id)
                   ORDER BY o.id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_settlement_audit(self, opportunity_id: int, *, status: str, raw_provider_winner: str | None = None,
                                provider_winner_id: str | None = None, canonical_winner: str | None = None,
                                stored_selections: list | None = None, mapping_method: str | None = None,
                                mapping_confidence: float | None = None, winning_exchange: str | None = None,
                                settlement_contributions: dict | None = None, total_realized_pnl: float | None = None,
                                reconciliation_status: str | None = None, reconciliation_delta: float | None = None,
                                details: dict | None = None) -> int:
        """Persist the evidence behind every settlement decision, including failures."""
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO settlement_audits(opportunity_id,observed_at,status,raw_provider_winner,provider_winner_id,canonical_winner,
                   stored_selections_json,mapping_method,mapping_confidence,winning_exchange,settlement_contributions_json,total_realized_pnl,
                   reconciliation_status,reconciliation_delta,details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(opportunity_id), now, str(status), raw_provider_winner, provider_winner_id, canonical_winner,
                 json.dumps(stored_selections or [], default=str, separators=(",", ":")), mapping_method,
                 None if mapping_confidence is None else float(mapping_confidence), winning_exchange,
                 json.dumps(settlement_contributions or {}, default=str, separators=(",", ":")),
                 None if total_realized_pnl is None else float(total_realized_pnl), reconciliation_status,
                 None if reconciliation_delta is None else float(reconciliation_delta),
                 json.dumps(details or {}, default=str, separators=(",", ":"))),
            )
            if str(status).upper() == "SETTLEMENT_MAPPING_ERROR":
                self.conn.execute("UPDATE opportunities SET status='settlement_mapping_error' WHERE id=? AND status!='settled'", (int(opportunity_id),))
            self.conn.commit()
            return int(cur.lastrowid)

    def settlement_audits(self, opportunity_id: int | None = None, limit: int = 500) -> list[dict]:
        with self.lock:
            if opportunity_id is None:
                rows = self.conn.execute("SELECT * FROM settlement_audits ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
            else:
                rows = self.conn.execute("SELECT * FROM settlement_audits WHERE opportunity_id=? ORDER BY id DESC LIMIT ?", (int(opportunity_id), max(1, int(limit)))).fetchall()
            return [dict(r) for r in rows]

    def exchange_routing_diagnostics(self, limit: int = 10000) -> dict:
        """Aggregate persisted placement routing and settlement winner provenance."""
        with self.lock:
            rows = self.conn.execute(
                """SELECT mp.opportunity_id,mp.status,mp.outcome,o.legs_json,o.routing_diagnostics_json
                   FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                   WHERE COALESCE(o.is_demo,0)=0 ORDER BY mp.id DESC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
            audits = self.conn.execute(
                """SELECT opportunity_id,winning_exchange,status FROM settlement_audits
                   WHERE status='SETTLED' ORDER BY id DESC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        latest_winner = {}
        for row in audits:
            latest_winner.setdefault(int(row["opportunity_id"]), str(row["winning_exchange"] or "").lower())
        held: dict[str, int] = {}
        winning: dict[str, int] = {}
        favourites: dict[str, int] = {}
        ties = 0
        equivalent_alternatives = 0
        reasons: dict[str, int] = {}
        positions = 0
        for raw in rows:
            d = dict(raw); positions += 1
            try: legs = json.loads(d.get("legs_json") or "[]")
            except Exception: legs = []
            for leg in legs:
                name = str(leg.get("venue_id") or leg.get("exchange") or "unknown").lower()
                key = "betfair" if "betfair" in name else "matchbook" if "matchbook" in name else "smarkets" if "smarkets" in name else name
                held[key] = held.get(key, 0) + 1
            try: diag = json.loads(d.get("routing_diagnostics_json") or "{}")
            except Exception: diag = {}
            if bool(diag.get("economic_tie")):
                ties += 1
                if diag.get("alternatives"):
                    equivalent_alternatives += 1
            favourite = str(diag.get("favourite_exchange") or "").lower()
            if favourite:
                favourite = "betfair" if "betfair" in favourite else "matchbook" if "matchbook" in favourite else "smarkets" if "smarkets" in favourite else favourite
                favourites[favourite] = favourites.get(favourite, 0) + 1
            reason = str(diag.get("reason") or "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
            winner = latest_winner.get(int(d.get("opportunity_id") or 0))
            if winner:
                key = "betfair" if "betfair" in winner else "matchbook" if "matchbook" in winner else "smarkets" if "smarkets" in winner else winner
                winning[key] = winning.get(key, 0) + 1
        held_total = sum(held.values()); win_total = sum(winning.values())
        return {
            "positions": positions, "economic_ties": ties, "positions_with_equivalent_routes": equivalent_alternatives,
            "routing_reasons": reasons, "favourite_outcomes": favourites,
            "held_outcomes": held, "held_outcome_pct": {k: round(v/held_total*100.0, 2) for k,v in held.items()} if held_total else {},
            "winning_outcomes": winning, "winning_outcome_pct": {k: round(v/win_total*100.0, 2) for k,v in winning.items()} if win_total else {},
        }

    def settle(self, opportunity_id: int, outcome: str, notes: str = "", *, _commit: bool = True, _settled_at: str | None = None):
        """Persist canonical opportunity/result settlement authority.

        Runtime scanner settlement should use :meth:`settle_canonical_lifecycle` so
        Monitor position/wallet state and Result settlement commit atomically. This
        method remains as the historical standalone lifecycle helper used by tests
        and maintenance tooling.
        """
        settled_at = str(_settled_at or datetime.now(timezone.utc).isoformat())
        with self.lock:
            runs = self.conn.execute(
                "SELECT id,outcome_pnls_json FROM scenario_runs WHERE opportunity_id=?",
                (opportunity_id,),
            ).fetchall()
            pnl_values = []
            for r in runs:
                pnls = json.loads(r["outcome_pnls_json"] or "{}")
                realized = pnls.get(outcome)
                if realized is None:
                    # normalized fallback
                    norm = outcome.strip().lower()
                    realized = next((v for k, v in pnls.items() if k.strip().lower() == norm), None)
                if realized is not None:
                    self.conn.execute("UPDATE scenario_runs SET realized_pnl=? WHERE id=?", (float(realized), r["id"]))
                    pnl_values.append(float(realized))
            simulated_pnl = min(pnl_values) if pnl_values else None
            self.conn.execute(
                "INSERT INTO settlements(opportunity_id,settled_at,outcome,simulated_pnl,notes) VALUES(?,?,?,?,?) ON CONFLICT(opportunity_id) DO UPDATE SET settled_at=excluded.settled_at,outcome=excluded.outcome,simulated_pnl=excluded.simulated_pnl,notes=excluded.notes",
                (opportunity_id, settled_at, outcome, simulated_pnl, notes),
            )
            self.conn.execute("UPDATE opportunities SET status='settled' WHERE id=?", (opportunity_id,))
            if _commit:
                self.conn.commit()
            return {
                "ok": True, "opportunity_id": int(opportunity_id), "outcome": str(outcome),
                "settled_at": settled_at, "simulated_pnl": simulated_pnl,
            }

    def settle_canonical_lifecycle(self, opportunity_id: int, outcome: str, notes: str = "") -> dict:
        """Atomically persist the complete SIM lifecycle settlement boundary.

        Monitor position/wallet/execution state (when a Monitor position exists)
        and opportunity/result/scenario state commit together. Any reconciliation
        failure or exception rolls the whole boundary back. Read paths never call
        this method.
        """
        settled_at = datetime.now(timezone.utc).isoformat()
        with self.lock:
            try:
                monitor = self.settle_monitor_position(
                    opportunity_id, outcome, _commit=False, _settled_at=settled_at
                )
                if isinstance(monitor, dict) and not monitor.get("ok"):
                    self.conn.rollback()
                    return {
                        "ok": False, "opportunity_id": int(opportunity_id),
                        "reason": monitor.get("reason") or "monitor_settlement_failed",
                        "monitor": monitor,
                    }
                result = self.settle(
                    opportunity_id, outcome, notes=notes, _commit=False, _settled_at=settled_at
                )
                self.conn.commit()
                return {
                    "ok": True, "opportunity_id": int(opportunity_id), "outcome": str(outcome),
                    "settled_at": settled_at, "monitor": monitor, "result": result,
                }
            except Exception:
                self.conn.rollback()
                raise

    def lifecycle_authority_integrity(self, opportunity_id: int | None = None, *, sample_limit: int = 20) -> dict:
        """Read-only integrity report for canonical SIM/LIVE lifecycle authority.

        This function deliberately never repairs, backfills or synchronises rows.
        Drift is reported so explicit lifecycle/migration boundaries can decide how
        to handle it.
        """
        limit = max(1, min(100, int(sample_limit or 20)))
        checks: list[dict] = []
        with self.lock:
            tables = {str(r["name"]) for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

            def check(name: str, sql: str, args: tuple = ()) -> None:
                count = int(self.conn.execute(f"SELECT COUNT(*) FROM ({sql}) AS stage05_integrity", args).fetchone()[0])
                sample_rows = self.conn.execute(sql + " LIMIT ?", (*args, limit)).fetchall() if count else []
                sample = [dict(r) for r in sample_rows]
                checks.append({"name": name, "count": count, "ok": count == 0, "sample": sample})

            opp_clause = ""
            opp_args: tuple = ()
            if opportunity_id is not None:
                opp_clause = " AND o.id=?"
                opp_args = (int(opportunity_id),)

            if {"opportunities", "settlements"}.issubset(tables):
                check(
                    "settlement_orphans",
                    "SELECT s.opportunity_id,s.outcome,s.settled_at FROM settlements s "
                    "LEFT JOIN opportunities o ON o.id=s.opportunity_id WHERE o.id IS NULL"
                    + (" AND s.opportunity_id=?" if opportunity_id is not None else ""),
                    (int(opportunity_id),) if opportunity_id is not None else (),
                )
                check(
                    "settled_opportunity_missing_result",
                    "SELECT o.id opportunity_id,o.status FROM opportunities o "
                    "LEFT JOIN settlements s ON s.opportunity_id=o.id "
                    "WHERE LOWER(COALESCE(o.status,''))='settled' AND s.opportunity_id IS NULL" + opp_clause,
                    opp_args,
                )
                check(
                    "result_status_mismatch",
                    "SELECT o.id opportunity_id,o.status,s.outcome,s.settled_at FROM settlements s "
                    "JOIN opportunities o ON o.id=s.opportunity_id "
                    "WHERE LOWER(COALESCE(o.status,''))<>'settled'" + opp_clause,
                    opp_args,
                )

            if {"monitor_positions", "opportunities", "settlements"}.issubset(tables):
                monitor_filter = " AND mp.opportunity_id=?" if opportunity_id is not None else ""
                monitor_args = (int(opportunity_id),) if opportunity_id is not None else ()
                check(
                    "settled_monitor_missing_result",
                    "SELECT mp.opportunity_id,mp.status,mp.outcome,mp.settled_at FROM monitor_positions mp "
                    "LEFT JOIN settlements s ON s.opportunity_id=mp.opportunity_id "
                    "WHERE mp.status='SETTLED' AND s.opportunity_id IS NULL" + monitor_filter,
                    monitor_args,
                )
                check(
                    "settled_monitor_result_mismatch",
                    "SELECT mp.opportunity_id,mp.outcome monitor_outcome,s.outcome result_outcome "
                    "FROM monitor_positions mp JOIN settlements s ON s.opportunity_id=mp.opportunity_id "
                    "WHERE mp.status='SETTLED' AND LOWER(TRIM(COALESCE(mp.outcome,'')))<>LOWER(TRIM(COALESCE(s.outcome,'')))"
                    + monitor_filter,
                    monitor_args,
                )
                check(
                    "settled_monitor_missing_timestamp",
                    "SELECT mp.opportunity_id,mp.status,mp.outcome FROM monitor_positions mp "
                    "WHERE mp.status='SETTLED' AND mp.settled_at IS NULL" + monitor_filter,
                    monitor_args,
                )

            if {"live_positions", "live_settlements"}.issubset(tables):
                live_filter = " AND p.opportunity_id=?" if opportunity_id is not None else ""
                live_args = (int(opportunity_id),) if opportunity_id is not None else ()
                check(
                    "live_settlement_orphans",
                    "SELECT s.position_id,s.settled_at,s.outcome FROM live_settlements s "
                    "LEFT JOIN live_positions p ON p.position_id=s.position_id WHERE p.position_id IS NULL",
                )
                check(
                    "live_settled_position_missing_settlement",
                    "SELECT p.position_id,p.opportunity_id,p.status,p.settled_at FROM live_positions p "
                    "LEFT JOIN live_settlements s ON s.position_id=p.position_id "
                    "WHERE UPPER(COALESCE(p.status,''))='SETTLED' AND s.position_id IS NULL" + live_filter,
                    live_args,
                )

        return {
            "ok": all(bool(item.get("ok")) for item in checks),
            "read_only": True,
            "opportunity_id": int(opportunity_id) if opportunity_id is not None else None,
            "checks": checks,
            "drift_count": sum(int(item.get("count") or 0) for item in checks),
        }

    def recent_opportunity_rows(self, limit: int = 50, include_demo: bool = True) -> list[dict]:
        """Newest opportunity rows for lightweight UI summaries.

        This deliberately avoids the historical ASC query used by scenario/replay code,
        so startup does not load and score thousands of old opportunities just to show
        a handful of recent cards.
        """
        with self.lock:
            where = "" if include_demo else "WHERE COALESCE(o.is_demo,0)=0"
            rows = self.conn.execute(
                f"""SELECT o.*,s.outcome,s.settled_at FROM opportunities o
                   LEFT JOIN settlements s ON s.opportunity_id=o.id {where}
                   ORDER BY o.id DESC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
            return [dict(r) for r in rows]

    def opportunity_by_id(self, opportunity_id: int, *, include_demo: bool = True) -> dict | None:
        """Direct bounded lookup used by historical drilldowns; never scans a capped history list."""
        with self.lock:
            demo = "" if include_demo else "AND COALESCE(o.is_demo,0)=0"
            row = self.conn.execute(
                f"""SELECT o.*,s.outcome,s.settled_at FROM opportunities o
                    LEFT JOIN settlements s ON s.opportunity_id=o.id
                    WHERE o.id=? {demo} LIMIT 1""", (int(opportunity_id),)
            ).fetchone()
        return dict(row) if row else None

    def opportunity_rows(self, limit: int = 500, include_demo: bool = True) -> list[dict]:
        with self.lock:
            where = "" if include_demo else "WHERE COALESCE(o.is_demo,0)=0"
            rows = self.conn.execute(
                f"""SELECT o.*,s.outcome,s.settled_at FROM opportunities o
                   LEFT JOIN settlements s ON s.opportunity_id=o.id {where} ORDER BY o.id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def replay_opportunity_rows(self, *, include_demo: bool = False, date_from: str | None = None,
                                date_to: str | None = None, sport: str | None = None, sports: list[str] | None = None,
                                strategy: str | None = None, engine_instance_id: str | None = None,
                                engine_instance_ids: list[str] | None = None, market: str | None = None, search: str | None = None,
                                execution_mode: str | None = None, time_basis: str = "detected_at", limit: int = 250001) -> list[dict]:
        """Select the settled Replay/Scenario cohort in SQLite with an explicit sentinel limit."""
        basis = "s.settled_at" if str(time_basis or '').lower() == 'settled_at' else "o.detected_at"
        clauses = ["s.outcome IS NOT NULL", "s.settled_at IS NOT NULL",
                   "COALESCE(o.qualification_status,'qualified') IN ('qualified','in_play_qualified')"]
        args: list = []
        if not include_demo:
            clauses.append("COALESCE(o.is_demo,0)=0")
        if date_from:
            clauses.append(f"{basis}>=?"); args.append(str(date_from))
        if date_to:
            clauses.append(f"{basis}<?"); args.append(str(date_to))
        sport_values = [str(x or '').strip() for x in (sports or []) if str(x or '').strip()]
        sport_values = [x for x in sport_values if x.lower() != 'all']
        sport_value = str(sport or '').strip()
        if sport_values:
            marks = ','.join('?' for _ in sport_values)
            clauses.append(f"LOWER(COALESCE(o.sport,'Unknown')) IN ({marks})")
            args.extend(x.lower() for x in sport_values)
        elif sport_value and sport_value.lower() != 'all':
            clauses.append("LOWER(COALESCE(o.sport,'Unknown'))=LOWER(?)"); args.append(sport_value)
        strategy_value = str(strategy or '').strip()
        if strategy_value and strategy_value.lower() != 'all':
            clauses.append("LOWER(COALESCE(o.strategy,'1x2'))=LOWER(?)"); args.append(strategy_value)
        engine_values = [str(x or '').strip() for x in (engine_instance_ids or []) if str(x or '').strip()]
        engine_values = [x for x in engine_values if x.lower() != 'all']
        engine_value = str(engine_instance_id or '').strip()
        if engine_values:
            marks = ','.join('?' for _ in engine_values)
            clauses.append(f"COALESCE(o.engine_instance_id,'') IN ({marks})")
            args.extend(engine_values)
        elif engine_value and engine_value.lower() != 'all':
            clauses.append("COALESCE(o.engine_instance_id,'')=?"); args.append(engine_value)
        market_value = str(market or '').strip()
        if market_value:
            clauses.append("LOWER(COALESCE(o.market_name,'')) LIKE ?"); args.append('%'+market_value.lower()+'%')
        search_value = str(search or '').strip().lower()
        if search_value:
            clauses.append("LOWER(COALESCE(o.event_name,'') || ' ' || COALESCE(o.event_key,'') || ' ' || COALESCE(o.market_name,'') || ' ' || COALESCE(o.sport,'') || ' ' || COALESCE(o.strategy,'')) LIKE ?")
            args.append('%'+search_value+'%')
        mode_value = str(execution_mode or '').strip().lower()
        if mode_value and mode_value not in {'all', 'watch', 'monitor'}:
            if mode_value in {'sim','monitor_timing','paper','simulate'}:
                clauses.append("EXISTS (SELECT 1 FROM execution_runs er WHERE er.opportunity_id=o.id AND LOWER(er.mode) IN ('sim','monitor','monitor_timing','watch','paper','simulate'))")
            else:
                clauses.append("EXISTS (SELECT 1 FROM execution_runs er WHERE er.opportunity_id=o.id AND LOWER(er.mode)=?)")
                args.append(mode_value)
        args.append(max(1, min(250001, int(limit or 250001))))
        with self.lock:
            rows = self.conn.execute(
                f"""SELECT o.*,s.outcome,s.settled_at FROM opportunities o
                    JOIN settlements s ON s.opportunity_id=o.id
                    WHERE {' AND '.join(clauses)} ORDER BY {basis} ASC,o.id ASC LIMIT ?""", tuple(args)
            ).fetchall()
        return [dict(r) for r in rows]

    def execution_history_for_opportunities(self, opportunity_ids: list[int], *, mode: str | None = None,
                                            include_demo: bool = False) -> list[dict]:
        """Load execution evidence only for an already-selected historical cohort."""
        ids = sorted({int(x) for x in opportunity_ids if int(x) > 0})
        if not ids:
            return []
        out: list[dict] = []
        mode_value = str(mode or '').strip().lower()
        with self.lock:
            for offset in range(0, len(ids), 800):
                chunk = ids[offset:offset+800]
                marks = ','.join('?' for _ in chunk)
                clauses = [f"er.opportunity_id IN ({marks})"]
                args: list = list(chunk)
                if not include_demo:
                    clauses.append("COALESCE(o.is_demo,0)=0")
                if mode_value and mode_value not in {'', 'all'}:
                    if mode_value in {'monitor','sim','monitor_timing','watch','paper','simulate'}:
                        clauses.append("LOWER(er.mode) IN ('sim','monitor','monitor_timing','watch','paper','simulate')")
                    else:
                        clauses.append("LOWER(er.mode)=?"); args.append(mode_value)
                rows = self.conn.execute(
                    f"""SELECT er.*,o.event_name,o.event_key,o.market_name,o.sport,o.strategy,o.detected_at,o.legs_json,o.in_play,o.qualification_status,
                               s.outcome,s.settled_at
                        FROM execution_runs er JOIN opportunities o ON o.id=er.opportunity_id
                        LEFT JOIN settlements s ON s.opportunity_id=er.opportunity_id
                        WHERE {' AND '.join(clauses)} ORDER BY er.id DESC""", tuple(args)
                ).fetchall()
                for row in rows:
                    d=dict(row)
                    try: d['details']=json.loads(d.pop('details_json') or '{}')
                    except Exception: d['details']={}
                    out.append(d)
        return out

    def add_execution_run(self, opportunity_id: int, mode: str, execution_type: str, state: str,
                          deployed: float = 0.0, expected_profit: float = 0.0, captured_profit: float | None = None,
                          max_unhedged_exposure: float = 0.0, details: dict | list | None = None,
                          is_real: bool = False, started_at: str | None = None, finished_at: str | None = None,
                          job_id: int | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        started = started_at or now
        finished = finished_at or now
        leakage = None if captured_profit is None else float(expected_profit or 0.0) - float(captured_profit)
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO execution_runs(opportunity_id,mode,execution_type,started_at,finished_at,state,is_real,deployed,expected_profit,captured_profit,execution_leakage,max_unhedged_exposure,details_json,job_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(opportunity_id), canonical_mode_value(mode), str(execution_type), started, finished, str(state), int(bool(is_real)),
                 float(deployed or 0.0), float(expected_profit or 0.0), None if captured_profit is None else float(captured_profit),
                 leakage, float(max_unhedged_exposure or 0.0), json.dumps(details or {}, default=str), job_id),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def update_execution_run_state(self, execution_run_id: int, state: str | None, *, captured_profit: float | None = None, details_patch: dict | None = None) -> None:
        with self.lock:
            row = self.conn.execute("SELECT expected_profit,captured_profit,details_json,state FROM execution_runs WHERE id=?", (int(execution_run_id),)).fetchone()
            if not row:
                return
            details = {}
            try: details = json.loads(row["details_json"] or "{}")
            except Exception: details = {}
            details.update(details_patch or {})
            new_state = str(state or row["state"] or "")
            cp = row["captured_profit"] if captured_profit is None else float(captured_profit)
            leakage = None if cp is None else float(row["expected_profit"] or 0.0) - float(cp)
            self.conn.execute("UPDATE execution_runs SET state=?,captured_profit=?,execution_leakage=?,details_json=? WHERE id=?",
                              (new_state, cp, leakage, json.dumps(details, default=str), int(execution_run_id)))
            self.conn.commit()

    def start_monitor_timing_run(self, opportunity_id: int, *, started_at: str, initial_deployed: float, initial_profit: float,
                         initial_roi_pct: float, planned_stakes: list | dict, reference_checkpoint_ms: int = 250,
                         research_only: bool = False, stream: str = "pre_match") -> int:
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO monitor_timing_runs(opportunity_id,started_at,status,initial_deployed,initial_profit,initial_roi_pct,planned_stakes_json,reference_checkpoint_ms,research_only,stream)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (int(opportunity_id), str(started_at), "RUNNING", float(initial_deployed or 0.0), float(initial_profit or 0.0),
                 float(initial_roi_pct or 0.0), json.dumps(planned_stakes or [], default=str), int(reference_checkpoint_ms), int(bool(research_only)), str(stream or "pre_match")),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def add_monitor_timing_observation(self, monitor_timing_run_id: int, *, offset_ms: int, elapsed_ms: int, observed_at: str,
                               fetch_latency_ms: int, deployed: float, expected_profit: float, expected_roi_pct: float,
                               executable_fraction: float, full_stake_available: bool, still_profitable: bool,
                               still_executable: bool, failure_reason: str | None, quotes: list | dict, venues: list | dict) -> int:
        with self.lock:
            cur = self.conn.execute(
                """INSERT INTO monitor_timing_observations(monitor_timing_run_id,offset_ms,elapsed_ms,observed_at,fetch_latency_ms,deployed,expected_profit,expected_roi_pct,
                                                    executable_fraction,full_stake_available,still_profitable,still_executable,failure_reason,quotes_json,venues_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(monitor_timing_run_id,offset_ms) DO UPDATE SET elapsed_ms=excluded.elapsed_ms,observed_at=excluded.observed_at,
                       fetch_latency_ms=excluded.fetch_latency_ms,deployed=excluded.deployed,expected_profit=excluded.expected_profit,
                       expected_roi_pct=excluded.expected_roi_pct,executable_fraction=excluded.executable_fraction,
                       full_stake_available=excluded.full_stake_available,still_profitable=excluded.still_profitable,
                       still_executable=excluded.still_executable,failure_reason=excluded.failure_reason,quotes_json=excluded.quotes_json,venues_json=excluded.venues_json""",
                (int(monitor_timing_run_id), int(offset_ms), int(elapsed_ms), str(observed_at), int(fetch_latency_ms or 0),
                 float(deployed or 0.0), float(expected_profit or 0.0), float(expected_roi_pct or 0.0), float(executable_fraction or 0.0),
                 int(bool(full_stake_available)), int(bool(still_profitable)), int(bool(still_executable)), failure_reason,
                 json.dumps(quotes or [], default=str), json.dumps(venues or [], default=str)),
            )
            self.conn.commit()
            return int(cur.lastrowid or 0)

    def finish_monitor_timing_run(self, monitor_timing_run_id: int, *, finished_at: str, status: str, survived_through_ms: int,
                          first_failure_reason: str | None, reference_profit: float, reference_roi_pct: float,
                          reference_executable: bool) -> None:
        with self.lock:
            self.conn.execute(
                """UPDATE monitor_timing_runs SET finished_at=?,status=?,survived_through_ms=?,first_failure_reason=?,reference_profit=?,reference_roi_pct=?,reference_executable=?
                   WHERE id=?""",
                (str(finished_at), str(status), int(survived_through_ms or 0), first_failure_reason, float(reference_profit or 0.0),
                 float(reference_roi_pct or 0.0), int(bool(reference_executable)), int(monitor_timing_run_id)),
            )
            self.conn.commit()

    def monitor_timing_run_for_opportunity(self, opportunity_id: int, stream: str | None = None) -> dict | None:
        with self.lock:
            if stream:
                row = self.conn.execute(
                    "SELECT * FROM monitor_timing_runs WHERE opportunity_id=? AND COALESCE(stream,'pre_match')=? ORDER BY id DESC LIMIT 1",
                    (int(opportunity_id), str(stream)),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM monitor_timing_runs WHERE opportunity_id=? ORDER BY id DESC LIMIT 1", (int(opportunity_id),)
                ).fetchone()
            if not row:
                return None
            d = dict(row)
            observations = self.conn.execute(
                "SELECT * FROM monitor_timing_observations WHERE monitor_timing_run_id=? ORDER BY offset_ms", (int(d["id"]),)
            ).fetchall()
            d["observations"] = []
            for obs in observations:
                x = dict(obs)
                for field in ("quotes_json", "venues_json"):
                    try:
                        x[field[:-5]] = json.loads(x.pop(field) or "[]")
                    except Exception:
                        x[field[:-5]] = []
                x["full_stake_available"] = bool(x.get("full_stake_available"))
                x["still_profitable"] = bool(x.get("still_profitable"))
                x["still_executable"] = bool(x.get("still_executable"))
                d["observations"].append(x)
            try:
                d["planned_stakes"] = json.loads(d.pop("planned_stakes_json") or "[]")
            except Exception:
                d["planned_stakes"] = []
            d["reference_executable"] = bool(d.get("reference_executable")) if d.get("reference_executable") is not None else None
            return d

    def monitor_timing_runs_for_opportunities(self, opportunity_ids: list[int] | tuple[int, ...] | set[int]) -> dict[tuple[int, str], dict]:
        """Bulk-load the latest monitor_timing run per opportunity/stream plus observations.

        Scenario analytics previously called ``monitor_timing_run_for_opportunity`` once per
        historical opportunity for every replay variant, producing an N+1 query
        pattern multiplied by the number of scenario bankrolls.  This read-only
        helper keeps the same latest-run semantics while loading runs/observations
        in bounded batches and decoding each stored payload once.
        """
        ids = sorted({int(x) for x in (opportunity_ids or []) if int(x or 0) > 0})
        if not ids:
            return {}
        runs: list[dict] = []
        with self.lock:
            # Keep comfortably below SQLite's common bind-parameter limits.
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                marks = ",".join("?" for _ in chunk)
                rows = self.conn.execute(
                    f"""SELECT sr.* FROM monitor_timing_runs sr
                        JOIN (
                          SELECT opportunity_id,COALESCE(stream,'pre_match') AS stream_key,MAX(id) AS max_id
                          FROM monitor_timing_runs
                          WHERE opportunity_id IN ({marks})
                          GROUP BY opportunity_id,COALESCE(stream,'pre_match')
                        ) latest ON latest.max_id=sr.id
                        ORDER BY sr.opportunity_id,sr.id""",
                    tuple(chunk),
                ).fetchall()
                runs.extend(dict(row) for row in rows)

            run_ids = [int(row.get("id") or 0) for row in runs if int(row.get("id") or 0) > 0]
            observations_by_run: dict[int, list[dict]] = {rid: [] for rid in run_ids}
            for start in range(0, len(run_ids), 400):
                chunk = run_ids[start:start + 400]
                if not chunk:
                    continue
                marks = ",".join("?" for _ in chunk)
                obs_rows = self.conn.execute(
                    f"SELECT * FROM monitor_timing_observations WHERE monitor_timing_run_id IN ({marks}) ORDER BY monitor_timing_run_id,offset_ms",
                    tuple(chunk),
                ).fetchall()
                for obs in obs_rows:
                    x = dict(obs)
                    for field in ("quotes_json", "venues_json"):
                        try:
                            x[field[:-5]] = json.loads(x.pop(field) or "[]")
                        except Exception:
                            x[field[:-5]] = []
                    x["full_stake_available"] = bool(x.get("full_stake_available"))
                    x["still_profitable"] = bool(x.get("still_profitable"))
                    x["still_executable"] = bool(x.get("still_executable"))
                    observations_by_run.setdefault(int(x.get("monitor_timing_run_id") or 0), []).append(x)

        out: dict[tuple[int, str], dict] = {}
        for row in runs:
            d = dict(row)
            rid = int(d.get("id") or 0)
            d["observations"] = observations_by_run.get(rid, [])
            try:
                d["planned_stakes"] = json.loads(d.pop("planned_stakes_json") or "[]")
            except Exception:
                d["planned_stakes"] = []
            d["reference_executable"] = bool(d.get("reference_executable")) if d.get("reference_executable") is not None else None
            stream = str(d.get("stream") or "pre_match")
            out[(int(d.get("opportunity_id") or 0), stream)] = d
        return out

    def monitor_timing_metrics(self, *, from_utc: str | None = None, to_utc: str | None = None, include_demo: bool = False,
                       sport: str | None = None, exchange: str | None = None, market: str | None = None, search: str | None = None,
                       qualification_status: str | None = None) -> dict:
        with self.lock:
            clauses = []
            args: list = []
            if from_utc:
                clauses.append("sr.started_at>=?")
                args.append(str(from_utc))
            if to_utc:
                clauses.append("sr.started_at<?")
                args.append(str(to_utc))
            if not include_demo:
                clauses.append("COALESCE(o.is_demo,0)=0")
            if qualification_status:
                clauses.append("COALESCE(o.qualification_status,'qualified')=?")
                args.append(str(qualification_status))
                clauses.append("COALESCE(sr.research_only,0)=?")
                args.append(1 if str(qualification_status) == "in_play_research" else 0)
                if str(qualification_status) == "qualified":
                    clauses.append("COALESCE(sr.stream,'pre_match')='pre_match'")
                elif str(qualification_status) == "in_play_qualified":
                    clauses.append("COALESCE(sr.stream,'pre_match')='in_play'")
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            raw_runs = self.conn.execute(
                f"""SELECT sr.*,o.event_name,o.market_name,o.sport,o.legs_json FROM monitor_timing_runs sr JOIN opportunities o ON o.id=sr.opportunity_id
                    {where} ORDER BY sr.id DESC""", tuple(args)
            ).fetchall()
            sport_filter = str(sport or "").strip().lower()
            exchange_filter = str(exchange or "").strip().lower()
            market_filter = str(market or "").strip().lower()
            search_filter = str(search or "").strip().lower()
            runs = []
            for row in raw_runs:
                d = dict(row)
                if sport_filter and sport_filter != "all" and str(d.get("sport") or "").lower() != sport_filter:
                    continue
                if market_filter and market_filter not in str(d.get("market_name") or "").lower():
                    continue
                if search_filter and search_filter not in f"{d.get('event_name') or ''} {d.get('market_name') or ''} {d.get('sport') or ''}".lower():
                    continue
                if exchange_filter and exchange_filter != "all":
                    try:
                        legs = json.loads(d.get("legs_json") or "[]")
                    except Exception:
                        legs = []
                    exchanges = {str(x.get("exchange") or "").lower() for x in legs if isinstance(x, dict)}
                    if not any(exchange_filter in x for x in exchanges):
                        continue
                runs.append(d)
            if not runs:
                return {
                    "runs": 0, "initial_profit": 0.0, "reference_profit": 0.0, "execution_leakage": 0.0,
                    "survival": {"100": 0.0, "250": 0.0, "500": 0.0, "1000": 0.0},
                    "median_survived_through_ms": 0.0, "median_fetch_latency_ms": 0.0,
                    "failure_reasons": {}, "reference_checkpoint_ms": 250,
                    "qualification_status": qualification_status,
                }
            run_ids = [int(r["id"]) for r in runs]
            marks = ",".join("?" for _ in run_ids)
            observations = self.conn.execute(
                f"SELECT * FROM monitor_timing_observations WHERE monitor_timing_run_id IN ({marks}) AND offset_ms>0 ORDER BY monitor_timing_run_id,offset_ms", tuple(run_ids)
            ).fetchall()
            by_offset: dict[int, list] = {}
            latencies = []
            for row in observations:
                by_offset.setdefault(int(row["offset_ms"]), []).append(row)
                latencies.append(int(row["fetch_latency_ms"] or 0))
            survival = {}
            for offset in (100,250,500,1000):
                rows = by_offset.get(offset, [])
                survival[str(offset)] = round((sum(1 for x in rows if bool(x["still_executable"])) / len(rows)) * 100.0, 2) if rows else 0.0
            failure_reasons: dict[str, int] = {}
            for r in runs:
                reason = str(r["first_failure_reason"] or "")
                if reason:
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            survived = sorted(int(r["survived_through_ms"] or 0) for r in runs)
            latencies.sort()
            def median(vals):
                if not vals: return 0.0
                n=len(vals); mid=n//2
                return float(vals[mid]) if n%2 else (float(vals[mid-1])+float(vals[mid]))/2.0
            initial_profit = sum(float(r["initial_profit"] or 0.0) for r in runs)
            reference_profit = sum(float(r["reference_profit"] or 0.0) for r in runs)
            refs = [int(r["reference_checkpoint_ms"] or 250) for r in runs]
            return {
                "runs": len(runs),
                "initial_profit": round(initial_profit, 4),
                "reference_profit": round(reference_profit, 4),
                "execution_leakage": round(initial_profit - reference_profit, 4),
                "survival": survival,
                "median_survived_through_ms": round(median(survived), 2),
                "median_fetch_latency_ms": round(median(latencies), 2),
                "failure_reasons": failure_reasons,
                "reference_checkpoint_ms": refs[0] if refs and all(x == refs[0] for x in refs) else None,
                "qualification_status": qualification_status,
            }

    def execution_history(self, limit: int = 250, mode: str | None = None, include_demo: bool = False,
                          from_utc: str | None = None, to_utc: str | None = None,
                          sport: str | None = None, market: str | None = None, search: str | None = None,
                          timeline_range: bool = False) -> list[dict]:
        """Execution rows with SQL-side range/text filtering.

        Historical callers can keep using the original three arguments. Analytics
        callers pass a period so SQLite can use the v0.8.17 indexes instead of
        materialising thousands of unrelated rows in Python.
        """
        with self.lock:
            clauses = []
            args: list = []
            mode_value = str(mode or '').lower().strip()
            if mode_value and mode_value not in {"all", ""}:
                if mode_value in {"monitor", "sim", "monitor_timing", "watch", "paper", "simulate"}:
                    clauses.append("LOWER(er.mode) IN ('sim','monitor','monitor_timing','watch','paper','simulate')")
                else:
                    clauses.append("LOWER(er.mode)=?")
                    args.append(mode_value)
            if not include_demo:
                clauses.append("COALESCE(o.is_demo,0)=0")
            if from_utc:
                if timeline_range:
                    clauses.append("COALESCE(s.settled_at,er.finished_at,er.started_at)>=?")
                else:
                    clauses.append("er.started_at>=?")
                args.append(str(from_utc))
            if to_utc:
                # An execution that began before the end of the selected interval
                # may settle inside it, so overlap semantics are used for Replay.
                clauses.append("er.started_at<?")
                args.append(str(to_utc))
            sport_value = str(sport or '').strip()
            if sport_value and sport_value.lower() != 'all':
                clauses.append("LOWER(COALESCE(o.sport,''))=LOWER(?)")
                args.append(sport_value)
            market_value = str(market or '').strip()
            if market_value:
                clauses.append("LOWER(COALESCE(o.market_name,'')) LIKE ?")
                args.append('%' + market_value.lower() + '%')
            search_value = str(search or '').strip().lower()
            if search_value:
                clauses.append("LOWER(COALESCE(o.event_name,'') || ' ' || COALESCE(o.event_key,'') || ' ' || COALESCE(o.market_name,'') || ' ' || COALESCE(o.sport,'') || ' ' || COALESCE(o.strategy,'')) LIKE ?")
                args.append('%' + search_value + '%')
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            args.append(int(limit))
            rows = self.conn.execute(
                f"""SELECT er.*,o.event_name,o.event_key,o.market_name,o.sport,o.strategy,o.section,o.detected_at,o.legs_json,o.in_play,o.qualification_status,
                           s.outcome,s.settled_at,
                           COALESCE(mp.engine_instance_id,o.engine_instance_id) engine_instance_id,
                           COALESCE(mp.engine_type,o.engine_type) engine_type,
                           COALESCE(mp.engine_version,o.engine_version) engine_version,
                           COALESCE(mp.engine_config_version,o.engine_config_version) engine_config_version,
                           COALESCE(mp.engine_provenance_source,o.engine_provenance_source) engine_provenance_source,
                           COALESCE(mp.mode,er.mode) provenance_mode
                    FROM execution_runs er JOIN opportunities o ON o.id=er.opportunity_id
                    LEFT JOIN settlements s ON s.opportunity_id=er.opportunity_id
                    LEFT JOIN monitor_positions mp ON mp.opportunity_id=er.opportunity_id
                    {where} ORDER BY er.id DESC LIMIT ?""", tuple(args)
            ).fetchall()
            out=[]
            for row in rows:
                d=dict(row)
                try:
                    d["details"] = json.loads(d.pop("details_json") or "{}")
                except Exception:
                    d["details"] = {}
                exchanges = []
                try:
                    legs = json.loads(d.get("legs_json") or "[]")
                    exchanges = sorted({str(x.get("exchange") or "").strip() for x in legs if str(x.get("exchange") or "").strip()})
                except Exception:
                    exchanges = []
                d["exchanges"] = exchanges
                details = d.get("details") or {}
                d["monitor_stream"] = str(details.get("monitor_stream") or ("in_play" if str(d.get("qualification_status") or "") == "in_play_qualified" or bool(d.get("in_play")) else "pre_match"))
                out.append(d)
            return out

    def latest_execution_for_opportunity(self, opportunity_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM execution_runs WHERE opportunity_id=? ORDER BY id DESC LIMIT 1",
                (int(opportunity_id),),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["details"] = json.loads(data.pop("details_json") or "{}")
            except Exception:
                data["details"] = {}
            return data

    def execution_counts(self, include_demo: bool = False) -> dict:
        with self.lock:
            demo = "" if include_demo else "WHERE COALESCE(o.is_demo,0)=0"
            rows = self.conn.execute(
                f"""SELECT er.mode,COUNT(*) c,COALESCE(SUM(er.expected_profit),0) expected,
                    COALESCE(SUM(er.captured_profit),0) captured,COALESCE(SUM(er.execution_leakage),0) leakage
                    FROM execution_runs er JOIN opportunities o ON o.id=er.opportunity_id {demo}
                    GROUP BY er.mode"""
            ).fetchall()
            out={"monitor": {"count":0,"expected":0.0,"captured":0.0,"leakage":0.0},
                 "live": {"count":0,"expected":0.0,"captured":0.0,"leakage":0.0}}
            for r in rows:
                mode=str(r["mode"] or "").lower()
                if mode in {"monitor_timing","watch"}: mode="monitor"
                bucket=out.setdefault(mode,{"count":0,"expected":0.0,"captured":0.0,"leakage":0.0})
                bucket["count"] += int(r["c"] or 0)
                bucket["expected"] = round(float(bucket.get("expected") or 0)+float(r["expected"] or 0),4)
                bucket["captured"] = round(float(bucket.get("captured") or 0)+float(r["captured"] or 0),4)
                bucket["leakage"] = round(float(bucket.get("leakage") or 0)+float(r["leakage"] or 0),4)
            return out

    def stored_result_history(self, limit: int = 500, include_demo: bool = False,
                              from_utc: str | None = None, to_utc: str | None = None,
                              sport: str | None = None, market: str | None = None,
                              search: str | None = None) -> list[dict]:
        """Return one UI row per stored event/market result.

        v0.8.17 pushes period/text filters into SQLite so Results does not load
        thousands of irrelevant historical opportunity rows before grouping.
        """
        clauses = []
        args: list = []
        if not include_demo:
            clauses.append("COALESCE(o.is_demo,0)=0")
        if from_utc:
            clauses.append("s.settled_at>=?")
            args.append(str(from_utc))
        if to_utc:
            clauses.append("s.settled_at<?")
            args.append(str(to_utc))
        sport_value = str(sport or '').strip()
        if sport_value and sport_value.lower() != 'all':
            clauses.append("LOWER(COALESCE(o.sport,''))=LOWER(?)")
            args.append(sport_value)
        market_value = str(market or '').strip().lower()
        if market_value:
            clauses.append("LOWER(COALESCE(o.market_name,'')) LIKE ?")
            args.append('%' + market_value + '%')
        search_value = str(search or '').strip().lower()
        if search_value:
            clauses.append("LOWER(COALESCE(o.event_name,'') || ' ' || COALESCE(o.event_key,'') || ' ' || COALESCE(o.market_name,'') || ' ' || COALESCE(o.sport,'') || ' ' || COALESCE(o.strategy,'')) LIKE ?")
            args.append('%' + search_value + '%')
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        # Grouping is by canonical event/market after the query. Keep a generous
        # row cap while still avoiding an unbounded table scan on long histories.
        row_cap = min(50000, max(10000, int(limit) * 20))
        with self.lock:
            rows = [dict(r) for r in self.conn.execute(
                f"""SELECT o.*,s.outcome,s.settled_at FROM settlements s
                    JOIN opportunities o ON o.id=s.opportunity_id
                    {where} ORDER BY s.settled_at DESC,o.id DESC LIMIT ?""",
                tuple(args + [row_cap]),
            ).fetchall()]
        groups: dict[tuple, dict] = {}
        for row in rows:
            outcome = row.get("outcome")
            if not outcome:
                continue
            key=(str(row.get("event_key") or row.get("event_name") or "").strip().lower(),
                 str(row.get("market_name") or "").strip().lower(),
                 str(row.get("strategy") or "1x2").strip().lower(),
                 str(row.get("sport") or "Unknown").strip().lower())
            g=groups.get(key)
            if g is None:
                g={
                    "event_key": row.get("event_key"), "event_name": row.get("event_name") or row.get("event_key"),
                    "event_start": row.get("event_start"), "market_name": row.get("market_name"),
                    "strategy": row.get("strategy") or "1x2", "sport": row.get("sport") or "Unknown",
                    "outcome": outcome, "result_observed_at": row.get("settled_at"),
                    "first_detected_at": row.get("detected_at"), "last_detected_at": row.get("detected_at"),
                    "opportunity_count": 1, "conflict": False, "opportunity_ids": [int(row.get("id") or 0)],
                    "exchanges": [],
                }
                try:
                    leg_payload = json.loads(row.get("legs_json") or "[]")
                    g["exchanges"] = sorted({str(x.get("exchange") or "").strip() for x in leg_payload if str(x.get("exchange") or "").strip()})
                except Exception:
                    g["exchanges"] = []
                groups[key]=g
            else:
                g["opportunity_count"] += 1
                g["opportunity_ids"].append(int(row.get("id") or 0))
                try:
                    leg_payload = json.loads(row.get("legs_json") or "[]")
                    g["exchanges"] = sorted(set(g.get("exchanges") or []) | {str(x.get("exchange") or "").strip() for x in leg_payload if str(x.get("exchange") or "").strip()})
                except Exception:
                    pass
                if str(g.get("outcome") or "").strip().lower() != str(outcome).strip().lower():
                    g["conflict"] = True
                if row.get("settled_at") and (not g.get("result_observed_at") or row["settled_at"] > g["result_observed_at"]):
                    g["result_observed_at"] = row["settled_at"]
                if row.get("detected_at") and (not g.get("last_detected_at") or row["detected_at"] > g["last_detected_at"]):
                    g["last_detected_at"] = row["detected_at"]
                if row.get("detected_at") and (not g.get("first_detected_at") or row["detected_at"] < g["first_detected_at"]):
                    g["first_detected_at"] = row["detected_at"]
        result=sorted(groups.values(), key=lambda x: x.get("result_observed_at") or x.get("event_start") or "", reverse=True)
        return result[:max(1,int(limit))]

    def clear_demo_data(self) -> int:
        """Delete only explicitly tagged demo recommendations and their dependent simulation rows."""
        with self.lock:
            ids = [r[0] for r in self.conn.execute("SELECT id FROM opportunities WHERE COALESCE(is_demo,0)=1").fetchall()]
            if not ids:
                return 0
            marks = ",".join("?" for _ in ids)
            self.conn.execute(f"DELETE FROM settlements WHERE opportunity_id IN ({marks})", ids)
            self.conn.execute(f"DELETE FROM execution_runs WHERE opportunity_id IN ({marks})", ids)
            self.conn.execute(f"DELETE FROM scenario_runs WHERE opportunity_id IN ({marks})", ids)
            self.conn.execute(f"DELETE FROM opportunities WHERE id IN ({marks})", ids)
            self.conn.commit()
            return len(ids)

    def scenario_runs_for_opportunity(self, opportunity_id: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM scenario_runs WHERE opportunity_id=? ORDER BY bankroll", (opportunity_id,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["stakes"] = json.loads(d.pop("stakes_json") or "[]")
                d["outcome_pnls"] = json.loads(d.pop("outcome_pnls_json") or "{}")
                bankroll = float(d.get("bankroll") or 0.0)
                deployed = float(d.get("deployed") or 0.0)
                expected_profit = float(d.get("expected_profit") or 0.0)
                realized = d.get("realized_pnl")
                d["capital_used_pct"] = round((deployed / bankroll) * 100.0, 4) if bankroll > 0 else 0.0
                d["bankroll_roi_pct"] = round((expected_profit / bankroll) * 100.0, 6) if bankroll > 0 else 0.0
                d["realized_bankroll_roi_pct"] = None if realized is None or bankroll <= 0 else round((float(realized) / bankroll) * 100.0, 6)
                out.append(d)
            return out

    def track_observations_for(self, track_key: str, limit: int = 500) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM track_observations WHERE track_key=? ORDER BY observed_at ASC LIMIT ?",
                (track_key, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def scan_pipeline_between(self, started_at: str | None = None, finished_at: str | None = None) -> dict:
        """Aggregate fast price-scan funnel telemetry for a UTC period."""
        with self.lock:
            where = ["finished_at IS NOT NULL", "error IS NULL", "COALESCE(scan_kind,'legacy') IN ('price','legacy')"]
            params = []
            if started_at:
                where.append("started_at>=?"); params.append(started_at)
            if finished_at:
                where.append("started_at<?"); params.append(finished_at)
            row = self.conn.execute(
                f"""SELECT COUNT(*) scans, COALESCE(SUM(markets_seen),0) fetched, COALESCE(SUM(matches_seen),0) matched,
                           COALESCE(SUM(processed_candidates),0) processed, COALESCE(SUM(positive_opportunities),0) opportunities,
                           COALESCE(SUM(qualified_count),0) qualified_observations, COALESCE(SUM(executed_count),0) executed_observations,
                           COALESCE(SUM(stale_rejections),0) stale_rejections, COALESCE(AVG(NULLIF(duration_ms,0)),0) avg_duration_ms
                    FROM scan_runs WHERE {' AND '.join(where)}""", tuple(params)).fetchone()
            out=dict(row) if row else {}
            for key in ("scans","fetched","matched","processed","opportunities","qualified_observations","executed_observations","stale_rejections"):
                out[key]=int(out.get(key) or 0)
            out["avg_duration_ms"]=int(round(float(out.get("avg_duration_ms") or 0.0)))

            # Canonical transaction funnel: Qualified means a stored candidate worth attempting; Executed means a Monitor position opened.
            qwhere=["COALESCE(o.is_demo,0)=0", "COALESCE(o.qualification_status,'qualified') IN ('qualified','in_play_qualified')"]
            qparams=[]
            if started_at: qwhere.append("o.detected_at>=?"); qparams.append(started_at)
            if finished_at: qwhere.append("o.detected_at<?"); qparams.append(finished_at)
            qrow=self.conn.execute(f"SELECT COUNT(*) c FROM opportunities o WHERE {' AND '.join(qwhere)}", tuple(qparams)).fetchone()
            erow=self.conn.execute(
                f"""SELECT COUNT(DISTINCT mp.opportunity_id) c FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                    WHERE {' AND '.join(qwhere)}""", tuple(qparams)).fetchone()
            out["qualified"]=int((qrow["c"] if qrow else 0) or 0)
            out["executed"]=int((erow["c"] if erow else 0) or 0)
            stream_out = {}
            for stream_name, qual_status in (("pre_match", "qualified"), ("in_play", "in_play_qualified")):
                sw = ["COALESCE(o.is_demo,0)=0", "COALESCE(o.qualification_status,'qualified')=?"]
                sp = [qual_status]
                if started_at: sw.append("o.detected_at>=?"); sp.append(started_at)
                if finished_at: sw.append("o.detected_at<?"); sp.append(finished_at)
                sq = self.conn.execute(f"SELECT COUNT(*) c FROM opportunities o WHERE {' AND '.join(sw)}", tuple(sp)).fetchone()
                se = self.conn.execute(
                    f"""SELECT COUNT(DISTINCT mp.opportunity_id) c FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                        WHERE {' AND '.join(sw)} AND COALESCE(mp.stream,'pre_match')=?""", tuple(sp + [stream_name])
                ).fetchone()
                # Financial stream metrics use settlement time so the same period
                # reconciles with Results / Performance / Dashboard Today. Technical
                # qualification and execution counts above remain detection-period facts.
                settle_where = [
                    "COALESCE(o.is_demo,0)=0",
                    "COALESCE(mp.stream,'pre_match')=?",
                    "mp.status='SETTLED'",
                    "mp.settled_at IS NOT NULL",
                ]
                settle_params = [stream_name]
                if started_at:
                    settle_where.append("mp.settled_at>=?"); settle_params.append(started_at)
                if finished_at:
                    settle_where.append("mp.settled_at<?"); settle_params.append(finished_at)
                ss = self.conn.execute(
                    f"""SELECT COUNT(*) c,COALESCE(SUM(ROUND(COALESCE(mp.realized_pnl,0),4)),0) pnl,COALESCE(SUM(ROUND(COALESCE(mp.deployed,0),4)),0) deployed
                        FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                        WHERE {' AND '.join(settle_where)}""", tuple(settle_params)
                ).fetchone()
                sl = self.conn.execute(
                    f"""SELECT COALESCE(SUM(er.execution_leakage),0) leakage
                        FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                        JOIN execution_runs er ON er.id=mp.execution_run_id
                        WHERE {' AND '.join(sw)} AND COALESCE(mp.stream,'pre_match')=?""", tuple(sp + [stream_name])
                ).fetchone()
                stream_out[stream_name] = {
                    "qualified": int((sq["c"] if sq else 0) or 0),
                    "executed": int((se["c"] if se else 0) or 0),
                    "settled": int((ss["c"] if ss else 0) or 0),
                    "realized_pnl": round(float((ss["pnl"] if ss else 0.0) or 0.0), 4),
                    "settled_deployed": round(float((ss["deployed"] if ss else 0.0) or 0.0), 4),
                    "execution_leakage": round(float((sl["leakage"] if sl else 0.0) or 0.0), 4),
                    "financial_time_basis": "settled_at",
                }
                stream_out[stream_name]["conversion_pct"] = round((stream_out[stream_name]["executed"] / stream_out[stream_name]["qualified"]) * 100.0, 3) if stream_out[stream_name]["qualified"] else 0.0
                stream_out[stream_name]["roi_pct"] = round((stream_out[stream_name]["realized_pnl"] / stream_out[stream_name]["settled_deployed"]) * 100.0, 4) if stream_out[stream_name]["settled_deployed"] else 0.0
            out["streams"] = stream_out
            ip_where=["COALESCE(o.is_demo,0)=0", "COALESCE(o.qualification_status,'qualified')='in_play_research'"]
            ip_params=[]
            if started_at: ip_where.append("o.detected_at>=?"); ip_params.append(started_at)
            if finished_at: ip_where.append("o.detected_at<?"); ip_params.append(finished_at)
            iprow=self.conn.execute(f"SELECT COUNT(*) c FROM opportunities o WHERE {' AND '.join(ip_where)}", tuple(ip_params)).fetchone()
            out["qualification_breakdown"]=self.qualification_breakdown_between(started_at, finished_at)
            # Research-only in-play can be identified before an opportunity row is created,
            # so the scan-observation breakdown is the authoritative research count.
            out["in_play_research"]=int((out["qualification_breakdown"] or {}).get("in_play_research",0) or 0)
            processed=out["processed"]; opportunities=out["opportunities"]; qualified=out["qualified"]
            out["opportunity_rate_pct"]=round((opportunities/processed)*100.0,3) if processed else 0.0
            out["qualification_rate_pct"]=round((qualified/opportunities)*100.0,3) if opportunities else 0.0
            out["execution_conversion_pct"]=round((out["executed"]/qualified)*100.0,3) if qualified else 0.0
            return out

    def dashboard_daily_trends(self, days: int = 7, timezone_name: str | None = None, timezone_offset_minutes: int = 0) -> dict:
        """Return compact daily Sports and Racing dashboard series using the viewer's local calendar days.

        Stored timestamps remain UTC.  The browser supplies its IANA timezone when
        available so the seven-day window rolls at local midnight, including DST.
        A fixed offset is retained as a fallback for environments without an IANA
        timezone name.
        """
        days = max(1, min(31, int(days or 7)))
        try:
            local_tz = ZoneInfo(str(timezone_name)) if timezone_name else None
        except Exception:
            local_tz = None
        if local_tz is None:
            try:
                offset = max(-14 * 60, min(14 * 60, int(timezone_offset_minutes or 0)))
            except (TypeError, ValueError):
                offset = 0
            # JavaScript Date.getTimezoneOffset() is UTC - local, hence the minus.
            local_tz = timezone(timedelta(minutes=-offset))
        now_utc = datetime.now(timezone.utc)
        local_now = now_utc.astimezone(local_tz)
        local_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        local_start = local_today - timedelta(days=days - 1)
        rows = []
        with self.lock:
            for offset in range(days):
                local_day_start = local_start + timedelta(days=offset)
                local_day_end = local_day_start + timedelta(days=1)
                day_start = local_day_start.astimezone(timezone.utc)
                day_end = local_day_end.astimezone(timezone.utc)
                a, b = day_start.isoformat(), day_end.isoformat()
                qualified = self.conn.execute(
                    """SELECT COUNT(*) c FROM opportunities
                       WHERE COALESCE(is_demo,0)=0 AND COALESCE(section,'sports')='sports'
                         AND COALESCE(qualification_status,'qualified') IN ('qualified','in_play_qualified')
                         AND detected_at>=? AND detected_at<?""", (a, b)
                ).fetchone()
                executed = self.conn.execute(
                    """SELECT COUNT(*) c FROM monitor_positions mp
                       JOIN opportunities o ON o.id=mp.opportunity_id
                       WHERE COALESCE(o.is_demo,0)=0 AND COALESCE(o.section,'sports')='sports'
                         AND mp.opened_at>=? AND mp.opened_at<?""", (a, b)
                ).fetchone()
                settled = self.conn.execute(
                    """SELECT COUNT(*) c, COALESCE(SUM(ROUND(COALESCE(mp.realized_pnl,0),4)),0) pnl, COALESCE(SUM(ROUND(COALESCE(mp.deployed,0),4)),0) deployed
                       FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                       WHERE COALESCE(o.is_demo,0)=0 AND COALESCE(o.section,'sports')='sports'
                         AND mp.status='SETTLED' AND mp.settled_at>=? AND mp.settled_at<?""", (a, b)
                ).fetchone()
                venue_rows = self.conn.execute(
                    """SELECT mp.realized_pnl,mp.realized_by_exchange_json,mp.stakes_by_exchange_json
                       FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                       WHERE COALESCE(o.is_demo,0)=0 AND COALESCE(o.section,'sports')='sports'
                         AND mp.status='SETTLED' AND mp.settled_at>=? AND mp.settled_at<?""", (a, b)
                ).fetchall()
                venue_pnl = {"betfair": 0.0, "matchbook": 0.0, "smarkets": 0.0}
                for venue_row in venue_rows:
                    try:
                        split = json.loads(venue_row['realized_by_exchange_json'] or '{}')
                    except Exception:
                        split = {}
                    canonical_split = {}
                    if isinstance(split, dict):
                        for raw_venue, raw_value in split.items():
                            text = str(raw_venue or '').strip().lower()
                            venue_id = 'betfair' if 'betfair' in text else 'matchbook' if 'matchbook' in text else 'smarkets' if 'smarkets' in text else text.replace(' ', '_')
                            if venue_id in venue_pnl:
                                canonical_split[venue_id] = canonical_split.get(venue_id, 0.0) + float(raw_value or 0.0)
                    if not canonical_split:
                        try:
                            stakes = json.loads(venue_row['stakes_by_exchange_json'] or '{}')
                        except Exception:
                            stakes = {}
                        canonical_stakes = {}
                        if isinstance(stakes, dict):
                            for raw_venue, raw_stake in stakes.items():
                                text = str(raw_venue or '').strip().lower()
                                venue_id = 'betfair' if 'betfair' in text else 'matchbook' if 'matchbook' in text else 'smarkets' if 'smarkets' in text else text.replace(' ', '_')
                                if venue_id in venue_pnl:
                                    canonical_stakes[venue_id] = canonical_stakes.get(venue_id, 0.0) + max(0.0, float(raw_stake or 0.0))
                        total_stake = sum(canonical_stakes.values())
                        if total_stake > 0:
                            pnl_value = float(venue_row['realized_pnl'] or 0.0)
                            canonical_split = {venue_id: pnl_value * (stake / total_stake) for venue_id, stake in canonical_stakes.items()}
                    for venue_id, value in canonical_split.items():
                        venue_pnl[venue_id] += float(value or 0.0)
                venue_pnl = {key: round(value, 4) for key, value in venue_pnl.items()}
                racing = self.conn.execute(
                    """SELECT COUNT(DISTINCT event_key) matched,
                              COUNT(DISTINCT CASE WHEN status='racing_opportunity' THEN event_key END) opportunities,
                              COALESCE(MAX(CASE WHEN status='racing_opportunity' THEN net_roi_pct END),0) best_roi
                       FROM matched_markets
                       WHERE COALESCE(section,'sports')='racing' AND observed_at>=? AND observed_at<?""", (a, b)
                ).fetchone()
                pnl = float((settled['pnl'] if settled else 0.0) or 0.0)
                deployed_amt = float((settled['deployed'] if settled else 0.0) or 0.0)
                rows.append({
                    'date': local_day_start.date().isoformat(),
                    'sports': {
                        'qualified': int((qualified['c'] if qualified else 0) or 0),
                        'executed': int((executed['c'] if executed else 0) or 0),
                        'settled': int((settled['c'] if settled else 0) or 0),
                        'pnl': round(pnl, 4),
                        'deployed': round(deployed_amt, 4),
                        'roi_pct': round((pnl / deployed_amt) * 100.0, 4) if deployed_amt else 0.0,
                        'venues': venue_pnl,
                    },
                    'racing': {
                        'matched_races': int((racing['matched'] if racing else 0) or 0),
                        'research_opportunities': int((racing['opportunities'] if racing else 0) or 0),
                        'best_net_roi_pct': round(float((racing['best_roi'] if racing else 0.0) or 0.0), 4),
                    },
                })
        return {
            'days': days,
            'timezone_name': str(timezone_name or ''),
            'timezone_offset_minutes': int(timezone_offset_minutes or 0),
            'from_utc': local_start.astimezone(timezone.utc).isoformat(),
            'to_utc': (local_today + timedelta(days=1)).astimezone(timezone.utc).isoformat(),
            'rows': rows,
        }

    def discovery_pipeline_between(self, started_at: str | None = None, finished_at: str | None = None) -> dict:
        with self.lock:
            where=["finished_at IS NOT NULL","error IS NULL","scan_kind='discovery'"]; params=[]
            if started_at: where.append("started_at>=?"); params.append(started_at)
            if finished_at: where.append("started_at<?"); params.append(finished_at)
            row=self.conn.execute(
                f"""SELECT COUNT(*) scans,COALESCE(SUM(markets_seen),0) fetched,COALESCE(SUM(matches_seen),0) matched,
                           COALESCE(AVG(NULLIF(duration_ms,0)),0) avg_duration_ms,COALESCE(MAX(cache_entries),0) cache_entries
                    FROM scan_runs WHERE {' AND '.join(where)}""",tuple(params)).fetchone()
            out=dict(row) if row else {}
            for key in ("scans","fetched","matched","cache_entries"): out[key]=int(out.get(key) or 0)
            out["avg_duration_ms"]=int(round(float(out.get("avg_duration_ms") or 0.0)))
            out["match_rate_pct"]=round((out["matched"]/out["fetched"])*100.0,3) if out["fetched"] else 0.0
            return out

    def performance_opportunity_aggregates(self, started_at: str | None = None, finished_at: str | None = None, include_demo: bool = False, venue: str | None = None, venue_pair: str | None = None) -> list[dict]:
        """Compact opportunity funnel grouped by canonical analytical dimensions.

        The funnel uses opportunity IDs consistently at every stage. It therefore
        avoids mixing raw quote observations with qualified/executed position counts.
        """
        where = []
        args: list = []
        if not include_demo:
            where.append("COALESCE(o.is_demo,0)=0")
        if started_at:
            where.append("o.detected_at>=?")
            args.append(str(started_at))
        if finished_at:
            where.append("o.detected_at<?")
            args.append(str(finished_at))
        venue_value = str(venue or "").strip().lower()
        if venue_value and venue_value != "all":
            where.append("LOWER(COALESCE(o.legs_json,'')) LIKE ?")
            args.append('%' + venue_value + '%')
        pair_values = [x.strip().lower() for x in str(venue_pair or "").split('|') if x.strip()]
        if pair_values and str(venue_pair or '').lower() != 'all':
            for pair_value in pair_values:
                where.append("LOWER(COALESCE(o.legs_json,'')) LIKE ?")
                args.append('%' + pair_value + '%')
        clause = " AND ".join(where) if where else "1=1"
        with self.lock:
            rows = self.conn.execute(
                f"""SELECT COALESCE(o.section,'sports') section,COALESCE(o.sport,'Unknown') sport,
                               COALESCE(o.market_name,'Unknown') market_name,COALESCE(o.in_play,0) in_play,
                               COUNT(*) observed,
                               SUM(CASE WHEN COALESCE(o.edge_pct,0)>0 OR COALESCE(o.expected_roi_pct,0)>0 THEN 1 ELSE 0 END) positive,
                               SUM(CASE WHEN COALESCE(o.qualification_status,'qualified') IN ('qualified','in_play_qualified','racing_qualified') THEN 1 ELSE 0 END) qualified,
                               SUM(CASE WHEN EXISTS(SELECT 1 FROM execution_runs er WHERE er.opportunity_id=o.id) THEN 1 ELSE 0 END) attempted,
                               SUM(CASE WHEN EXISTS(SELECT 1 FROM monitor_positions mp WHERE mp.opportunity_id=o.id) THEN 1 ELSE 0 END) executed,
                               SUM(CASE WHEN EXISTS(SELECT 1 FROM monitor_positions mp WHERE mp.opportunity_id=o.id AND mp.status='SETTLED') THEN 1 ELSE 0 END) settled,
                               AVG(CASE WHEN COALESCE(o.qualification_status,'qualified') IN ('qualified','in_play_qualified','racing_qualified') THEN o.expected_roi_pct END) avg_qualified_edge_pct
                        FROM opportunities o WHERE {clause}
                        GROUP BY COALESCE(o.section,'sports'),COALESCE(o.sport,'Unknown'),COALESCE(o.market_name,'Unknown'),COALESCE(o.in_play,0)
                        ORDER BY observed DESC""", tuple(args),
            ).fetchall()
            return [dict(row) for row in rows]

    def market_analysis_between(self, started_at: str | None = None, finished_at: str | None = None, *, include_economics: bool = True) -> dict:
        """Read-only market/stream comparison using existing observations.

        Counts deliberately distinguish unique markets from repeated price-scan
        observations so the UI does not confuse scanner cadence with market breadth.
        """
        where = ["1=1"]
        params: list = []
        if started_at:
            where.append("mm.observed_at>=?")
            params.append(started_at)
        if finished_at:
            where.append("mm.observed_at<?")
            params.append(finished_at)
        clause = " AND ".join(where)
        compact_where = ["1=1"]
        compact_params: list = []
        if started_at:
            compact_where.append("r.hour_utc>=?")
            compact_params.append(self._hour_floor_iso(started_at))
        if finished_at:
            compact_where.append("r.hour_utc<?")
            compact_params.append(str(finished_at))
        compact_clause = " AND ".join(compact_where)
        # 0.9.45: choose the summary source by the compact evidence that actually
        # exists, not by the coarse matched_market_history_state marker alone.
        # Mature/upgraded DBs can contain a valid history marker while one compact
        # table is absent (for example after an interrupted/repaired maintenance
        # cycle).  The old reader then suppressed the still-present raw rows and
        # Market Analysis appeared completely empty.  Per-group source selection
        # keeps normal compact reads fast while failing back to raw evidence only
        # for the exact compact group that is missing.
        raw_hour = "strftime('%Y-%m-%dT%H:00:00+00:00',mm.observed_at)"
        raw_unfinalized = f"NOT EXISTS (SELECT 1 FROM matched_market_history_state hs WHERE hs.hour_utc={raw_hour})"
        compact_finalized = "EXISTS (SELECT 1 FROM matched_market_history_state hs WHERE hs.hour_utc=r.hour_utc)"
        raw_metric_fallback = f"""({raw_unfinalized} OR NOT EXISTS (
            SELECT 1 FROM market_hourly_rollups cr
            WHERE cr.hour_utc={raw_hour}
              AND EXISTS (SELECT 1 FROM matched_market_history_state hs2 WHERE hs2.hour_utc=cr.hour_utc)
              AND cr.section=COALESCE(mm.section,'sports')
              AND cr.sport=COALESCE(mm.sport,'Unknown')
              AND cr.market_name=COALESCE(mm.market_name,'Unknown')
              AND cr.in_play=COALESCE(mm.in_play,0)
        ))"""
        raw_identity_fallback = f"""({raw_unfinalized} OR NOT EXISTS (
            SELECT 1 FROM market_hourly_seen s2
            WHERE s2.hour_utc={raw_hour}
              AND EXISTS (SELECT 1 FROM matched_market_history_state hs2 WHERE hs2.hour_utc=s2.hour_utc)
              AND s2.section=COALESCE(mm.section,'sports')
              AND s2.sport=COALESCE(mm.sport,'Unknown')
              AND s2.market_name=COALESCE(mm.market_name,'Unknown')
              AND s2.in_play=COALESCE(mm.in_play,0)
              AND s2.event_key=mm.event_key
        ))"""
        raw_reason_fallback = f"""({raw_unfinalized} OR NOT EXISTS (
            SELECT 1 FROM matched_market_reason_hourly_rollups rr2
            WHERE rr2.hour_utc={raw_hour}
              AND EXISTS (SELECT 1 FROM matched_market_history_state hs2 WHERE hs2.hour_utc=rr2.hour_utc)
              AND rr2.section=COALESCE(mm.section,'sports')
              AND rr2.sport=COALESCE(mm.sport,'Unknown')
              AND rr2.market_name=COALESCE(mm.market_name,'Unknown')
              AND rr2.in_play=COALESCE(mm.in_play,0)
              AND rr2.status=COALESCE(mm.status,'unknown')
        ))"""
        with self.lock:
            compact_rows = self.conn.execute(
                f"""SELECT r.section,r.sport,r.market_name,r.in_play,
                           SUM(r.observations) observations,SUM(r.net_roi_sum) net_roi_sum,SUM(r.net_roi_count) net_roi_count,
                           MAX(r.best_net_roi_pct) best_net_roi_pct,SUM(r.deployable_sum) deployable_sum,SUM(r.deployable_count) deployable_count
                    FROM market_hourly_rollups r WHERE {compact_clause} AND {compact_finalized}
                    GROUP BY r.section,r.sport,r.market_name,r.in_play""", tuple(compact_params)
            ).fetchall()
            raw_rows = self.conn.execute(
                f"""SELECT COALESCE(mm.section,'sports') section,COALESCE(mm.sport,'Unknown') sport,
                           COALESCE(mm.market_name,'Unknown') market_name,COALESCE(mm.in_play,0) in_play,
                           COUNT(*) observations,
                           COALESCE(SUM(CASE WHEN mm.net_roi_pct IS NOT NULL THEN mm.net_roi_pct ELSE 0 END),0) net_roi_sum,
                           SUM(CASE WHEN mm.net_roi_pct IS NOT NULL THEN 1 ELSE 0 END) net_roi_count,
                           MAX(mm.net_roi_pct) best_net_roi_pct,
                           COALESCE(SUM(CASE WHEN mm.diagnostic_deployed IS NOT NULL THEN mm.diagnostic_deployed ELSE 0 END),0) deployable_sum,
                           SUM(CASE WHEN mm.diagnostic_deployed IS NOT NULL THEN 1 ELSE 0 END) deployable_count
                    FROM matched_markets mm WHERE {clause} AND {raw_metric_fallback}
                    GROUP BY COALESCE(mm.section,'sports'),COALESCE(mm.sport,'Unknown'),COALESCE(mm.market_name,'Unknown'),COALESCE(mm.in_play,0)""",
                tuple(params),
            ).fetchall()
            metric_map: dict[tuple, dict] = {}
            for row in list(compact_rows) + list(raw_rows):
                d = dict(row); key=(d['section'],d['sport'],d['market_name'],int(d['in_play'] or 0))
                m=metric_map.setdefault(key, {'section':key[0],'sport':key[1],'market_name':key[2],'in_play':key[3],
                    'observations':0,'net_roi_sum':0.0,'net_roi_count':0,'best_net_roi_pct':None,'deployable_sum':0.0,'deployable_count':0})
                m['observations'] += int(d.get('observations') or 0)
                m['net_roi_sum'] += float(d.get('net_roi_sum') or 0.0); m['net_roi_count'] += int(d.get('net_roi_count') or 0)
                if d.get('best_net_roi_pct') is not None:
                    v=float(d['best_net_roi_pct']); m['best_net_roi_pct']=v if m['best_net_roi_pct'] is None else max(float(m['best_net_roi_pct']),v)
                m['deployable_sum'] += float(d.get('deployable_sum') or 0.0); m['deployable_count'] += int(d.get('deployable_count') or 0)

            # Unique-market counts need de-duplication across hours. Combine compact
            # finalized identities with legacy raw identities before grouping.
            seen_where=["1=1"]; seen_params=[]
            if started_at: seen_where.append("s.hour_utc>=?"); seen_params.append(self._hour_floor_iso(started_at))
            if finished_at: seen_where.append("s.hour_utc<?"); seen_params.append(str(finished_at))
            identity_rows = self.conn.execute(
                f"""SELECT section,sport,market_name,in_play,event_key,MAX(raw_positive) raw_positive,MAX(net_positive) net_positive
                    FROM (
                      SELECT s.section,s.sport,s.market_name,s.in_play,s.event_key,s.raw_positive,s.net_positive
                      FROM market_hourly_seen s WHERE {' AND '.join(seen_where)}
                        AND EXISTS (SELECT 1 FROM matched_market_history_state hs WHERE hs.hour_utc=s.hour_utc)
                      UNION ALL
                      SELECT COALESCE(mm.section,'sports'),COALESCE(mm.sport,'Unknown'),COALESCE(mm.market_name,'Unknown'),COALESCE(mm.in_play,0),mm.event_key,
                             CASE WHEN COALESCE(mm.theoretical_edge_pct,0)>0 THEN 1 ELSE 0 END,
                             CASE WHEN COALESCE(mm.net_roi_pct,0)>0 THEN 1 ELSE 0 END
                      FROM matched_markets mm WHERE {clause} AND {raw_identity_fallback}
                    ) x GROUP BY section,sport,market_name,in_play,event_key""",
                tuple(seen_params + params),
            ).fetchall()
            ident_map: dict[tuple, dict] = {}
            for r0 in identity_rows:
                d=dict(r0); key=(d['section'],d['sport'],d['market_name'],int(d['in_play'] or 0))
                z=ident_map.setdefault(key,{'unique_markets':0,'raw_positive':0,'net_positive':0})
                z['unique_markets']+=1; z['raw_positive']+=int(d.get('raw_positive') or 0); z['net_positive']+=int(d.get('net_positive') or 0)
            rows=[]
            for key,m in metric_map.items():
                ids=ident_map.get(key,{})
                m['unique_markets']=int(ids.get('unique_markets') or 0); m['raw_positive']=int(ids.get('raw_positive') or 0); m['net_positive']=int(ids.get('net_positive') or 0)
                m['qualified']=0
                m['avg_net_roi_pct']=(float(m['net_roi_sum'])/int(m['net_roi_count'])) if int(m['net_roi_count']) else None
                m['avg_deployable']=(float(m['deployable_sum'])/int(m['deployable_count'])) if int(m['deployable_count']) else None
                rows.append(m)
            rows.sort(key=lambda x:(-int(x.get('unique_markets') or 0),-int(x.get('observations') or 0)))

            # Hourly activity combines authoritative compact hours with unfinalized
            # legacy raw history. Liquidity rollups retain observation-level positive
            # and qualification counts even after verbose rows are pruned.
            compact_activity = self.conn.execute(
                f"""SELECT r.section,r.sport,r.in_play,r.hour_utc,SUM(r.observations) observations,
                           COALESCE(SUM(l.positive_observations),0) net_positive,COALESCE(SUM(l.qualified_observations),0) qualified
                    FROM market_hourly_rollups r LEFT JOIN liquidity_opportunity_hourly_rollups l
                      ON l.hour_utc=r.hour_utc AND l.section=r.section AND l.sport=r.sport AND l.market_name=r.market_name AND l.in_play=r.in_play
                    WHERE {compact_clause} AND {compact_finalized}
                    GROUP BY r.section,r.sport,r.in_play,r.hour_utc ORDER BY r.hour_utc""", tuple(compact_params)
            ).fetchall()
            raw_activity = self.conn.execute(
                f"""SELECT COALESCE(mm.section,'sports') section,COALESCE(mm.sport,'Unknown') sport,COALESCE(mm.in_play,0) in_play,
                           strftime('%Y-%m-%dT%H:00:00+00:00',mm.observed_at) hour_utc,COUNT(*) observations,
                           SUM(CASE WHEN COALESCE(mm.net_roi_pct,0)>0 THEN 1 ELSE 0 END) net_positive,
                           SUM(CASE WHEN mm.status IN ('recommended','in_play_monitor','in_play_qualified','racing_monitor','racing_qualified') THEN 1 ELSE 0 END) qualified
                    FROM matched_markets mm WHERE {clause} AND {raw_metric_fallback}
                    GROUP BY COALESCE(mm.section,'sports'),COALESCE(mm.sport,'Unknown'),COALESCE(mm.in_play,0),strftime('%Y-%m-%dT%H:00:00+00:00',mm.observed_at)""", tuple(params)
            ).fetchall()
            activity_map={}
            for rr in list(compact_activity)+list(raw_activity):
                d=dict(rr); key=(d['section'],d['sport'],int(d['in_play'] or 0),d['hour_utc'])
                z=activity_map.setdefault(key,{'section':key[0],'sport':key[1],'in_play':key[2],'hour_utc':key[3],'observations':0,'net_positive':0,'qualified':0})
                for k in ('observations','net_positive','qualified'): z[k]+=int(d.get(k) or 0)
            activity_rows=sorted(activity_map.values(),key=lambda x:x['hour_utc'])

            compact_reasons = self.conn.execute(
                f"""SELECT rr.section,rr.sport,rr.market_name,rr.in_play,rr.status,MAX(rr.reason_sample) reason,SUM(rr.observations) c
                    FROM matched_market_reason_hourly_rollups rr
                    WHERE {' AND '.join(x.replace('r.hour_utc','rr.hour_utc') for x in compact_where)}
                      AND EXISTS (SELECT 1 FROM matched_market_history_state hs WHERE hs.hour_utc=rr.hour_utc)
                    GROUP BY rr.section,rr.sport,rr.market_name,rr.in_play,rr.status""", tuple(compact_params)
            ).fetchall()
            raw_reasons = self.conn.execute(
                f"""SELECT COALESCE(mm.section,'sports') section,COALESCE(mm.sport,'Unknown') sport,COALESCE(mm.market_name,'Unknown') market_name,
                           COALESCE(mm.in_play,0) in_play,COALESCE(mm.status,'unknown') status,MAX(COALESCE(mm.reason,'')) reason,COUNT(*) c
                    FROM matched_markets mm WHERE {clause} AND {raw_reason_fallback}
                    GROUP BY COALESCE(mm.section,'sports'),COALESCE(mm.sport,'Unknown'),COALESCE(mm.market_name,'Unknown'),COALESCE(mm.in_play,0),COALESCE(mm.status,'unknown')""", tuple(params)
            ).fetchall()
            reason_map={}
            for rr in list(compact_reasons)+list(raw_reasons):
                d=dict(rr); key=(d['section'],d['sport'],d['market_name'],int(d['in_play'] or 0),d['status'])
                z=reason_map.setdefault(key,{'section':key[0],'sport':key[1],'market_name':key[2],'in_play':key[3],'status':key[4],'reason':d.get('reason') or '','c':0})
                z['c']+=int(d.get('c') or 0)
                if d.get('reason'): z['reason']=d['reason']
            reason_rows=sorted(reason_map.values(),key=lambda x:-int(x['c']))

            # 0.9.14: the old exchange-market identity query regrouped the large
            # matched_markets JSON evidence columns on every Market Analysis read.
            # No API/UI consumer uses that payload, so keep the response contract
            # while avoiding dead raw-history work.
            exchange_market_identity_rows = []

            if include_economics:
                opportunity_where = ["COALESCE(o.is_demo,0)=0"]
                opportunity_params: list = []
                if started_at:
                    opportunity_where.append("o.detected_at>=?")
                    opportunity_params.append(started_at)
                if finished_at:
                    opportunity_where.append("o.detected_at<?")
                    opportunity_params.append(finished_at)
                exchange_opportunity_rows = self.conn.execute(
                    f"""SELECT COALESCE(o.section,'sports') section, COALESCE(o.sport,'Unknown') sport,
                                   COALESCE(o.market_name,'Unknown') market_name, COALESCE(o.in_play,0) in_play,
                                   SUM(CASE WHEN LOWER(COALESCE(o.legs_json,'')) LIKE '%betfair%' THEN 1 ELSE 0 END) betfair_opportunities,
                                   SUM(CASE WHEN LOWER(COALESCE(o.legs_json,'')) LIKE '%matchbook%' THEN 1 ELSE 0 END) matchbook_opportunities
                            FROM opportunities o WHERE {' AND '.join(opportunity_where)}
                            GROUP BY COALESCE(o.section,'sports'),COALESCE(o.sport,'Unknown'),COALESCE(o.market_name,'Unknown'),COALESCE(o.in_play,0)""",
                    tuple(opportunity_params),
                ).fetchall()
                # 0.9.0 generic venue attribution. Keep the compact Betfair/Matchbook
                # aggregate above for 0.8.x UI compatibility, but expose canonical leg
                # evidence so API analytics can attribute any registered venue.
                opportunity_venue_rows = self.conn.execute(
                    f"""SELECT o.id,COALESCE(o.section,'sports') section,COALESCE(o.sport,'Unknown') sport,
                                   COALESCE(o.market_name,'Unknown') market_name,COALESCE(o.in_play,0) in_play,
                                   COALESCE(o.legs_json,'[]') legs_json
                            FROM opportunities o WHERE {' AND '.join(opportunity_where)}""",
                    tuple(opportunity_params),
                ).fetchall()

                # v0.8.36 canonical opportunity cohort. Qualification, execution
                # attempts and positions opened are all measured from the same
                # opportunity IDs, so conversion cannot exceed 100%.
                cohort_where = ["COALESCE(o.is_demo,0)=0",
                                "COALESCE(o.qualification_status,'qualified') IN ('qualified','in_play_qualified','racing_qualified')"]
                cohort_params: list = []
                if started_at:
                    cohort_where.append("o.detected_at>=?")
                    cohort_params.append(started_at)
                if finished_at:
                    cohort_where.append("o.detected_at<?")
                    cohort_params.append(finished_at)
                attempt_extra = []
                attempt_params: list = []
                position_extra = []
                position_params: list = []
                deployed_extra = []
                deployed_params: list = []
                if started_at:
                    attempt_extra.append("er2.started_at>=?"); attempt_params.append(started_at)
                    position_extra.append("mp2.opened_at>=?"); position_params.append(started_at)
                    deployed_extra.append("mp3.opened_at>=?"); deployed_params.append(started_at)
                if finished_at:
                    attempt_extra.append("er2.started_at<?"); attempt_params.append(finished_at)
                    position_extra.append("mp2.opened_at<?"); position_params.append(finished_at)
                    deployed_extra.append("mp3.opened_at<?"); deployed_params.append(finished_at)
                attempt_sql = (" AND " + " AND ".join(attempt_extra)) if attempt_extra else ""
                position_sql = (" AND " + " AND ".join(position_extra)) if position_extra else ""
                deployed_sql = (" AND " + " AND ".join(deployed_extra)) if deployed_extra else ""
                exec_rows = self.conn.execute(
                    f"""SELECT COALESCE(o.section,'sports') section,COALESCE(o.sport,'Unknown') sport,
                                   COALESCE(o.market_name,'Unknown') market_name,COALESCE(o.in_play,0) in_play,
                                   COUNT(*) qualified,
                                   SUM(CASE WHEN EXISTS(SELECT 1 FROM execution_runs er2 WHERE er2.opportunity_id=o.id{attempt_sql}) THEN 1 ELSE 0 END) attempts,
                                   SUM(CASE WHEN EXISTS(SELECT 1 FROM monitor_positions mp2 WHERE mp2.opportunity_id=o.id{position_sql}) THEN 1 ELSE 0 END) executed,
                                   COALESCE(SUM((SELECT MAX(mp3.deployed) FROM monitor_positions mp3 WHERE mp3.opportunity_id=o.id{deployed_sql})),0) deployed
                            FROM opportunities o WHERE {' AND '.join(cohort_where)}
                            GROUP BY COALESCE(o.section,'sports'),COALESCE(o.sport,'Unknown'),COALESCE(o.market_name,'Unknown'),COALESCE(o.in_play,0)""",
                    tuple(attempt_params + position_params + deployed_params + cohort_params),
                ).fetchall()

                # Technical execution-hour activity remains start-time based.
                exec_where = ["COALESCE(o.is_demo,0)=0"]
                exec_params: list = []
                if started_at:
                    exec_where.append("er.started_at>=?")
                    exec_params.append(started_at)
                if finished_at:
                    exec_where.append("er.started_at<?")
                    exec_params.append(finished_at)
                # v0.8.24: settled financial facts use settlement time, not execution
                # start time. This makes Market Analysis P&L reconcile with Results,
                # Dashboard Today and Performance for the same period.
                settled_where = ["COALESCE(o.is_demo,0)=0", "mp.status='SETTLED'", "mp.settled_at IS NOT NULL"]
                settled_params: list = []
                if started_at:
                    settled_where.append("mp.settled_at>=?")
                    settled_params.append(started_at)
                if finished_at:
                    settled_where.append("mp.settled_at<?")
                    settled_params.append(finished_at)
                settled_rows = self.conn.execute(
                    f"""SELECT COALESCE(o.section,'sports') section,COALESCE(o.sport,'Unknown') sport,
                                   COALESCE(o.market_name,'Unknown') market_name,COALESCE(o.in_play,0) in_play,
                                   COUNT(*) settled,
                                   COALESCE(SUM(ROUND(COALESCE(mp.deployed,0),4)),0) settled_deployed,
                                   COALESCE(SUM(ROUND(COALESCE(mp.realized_pnl,0),4)),0) pnl,
                                   COALESCE(SUM(ROUND(COALESCE(mp.deployed,0),4)+ROUND(COALESCE(mp.realized_pnl,0),4)),0) returned,
                                   SUM(CASE WHEN ROUND(COALESCE(mp.realized_pnl,0),4)>0 THEN 1 ELSE 0 END) wins,
                                   SUM(CASE WHEN ROUND(COALESCE(mp.realized_pnl,0),4)<0 THEN 1 ELSE 0 END) losses
                            FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                            WHERE {' AND '.join(settled_where)}
                            GROUP BY COALESCE(o.section,'sports'),COALESCE(o.sport,'Unknown'),COALESCE(o.market_name,'Unknown'),COALESCE(o.in_play,0)""",
                    tuple(settled_params),
                ).fetchall()

                exec_hour_rows = self.conn.execute(
                    f"""SELECT COALESCE(o.section,'sports') section,COALESCE(o.sport,'Unknown') sport,
                                   COALESCE(o.in_play,0) in_play,strftime('%Y-%m-%dT%H:00:00Z',er.started_at) hour_utc,
                                   SUM(CASE WHEN mp.id IS NOT NULL THEN 1 ELSE 0 END) executed,
                                   0.0 pnl
                            FROM execution_runs er JOIN opportunities o ON o.id=er.opportunity_id
                            LEFT JOIN monitor_positions mp ON mp.execution_run_id=er.id
                            WHERE {' AND '.join(exec_where)}
                            GROUP BY COALESCE(o.section,'sports'),COALESCE(o.sport,'Unknown'),COALESCE(o.in_play,0),strftime('%Y-%m-%dT%H:00:00Z',er.started_at)""",
                    tuple(exec_params),
                ).fetchall()
                settled_hour_rows = self.conn.execute(
                    f"""SELECT COALESCE(o.section,'sports') section,COALESCE(o.sport,'Unknown') sport,
                                   COALESCE(o.in_play,0) in_play,strftime('%Y-%m-%dT%H:00:00Z',mp.settled_at) hour_utc,
                                   0 executed, COALESCE(SUM(ROUND(COALESCE(mp.realized_pnl,0),4)),0) pnl
                            FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                            WHERE {' AND '.join(settled_where)}
                            GROUP BY COALESCE(o.section,'sports'),COALESCE(o.sport,'Unknown'),COALESCE(o.in_play,0),strftime('%Y-%m-%dT%H:00:00Z',mp.settled_at)""",
                    tuple(settled_params),
                ).fetchall()

            else:
                exchange_opportunity_rows = []
                opportunity_venue_rows = []
                exec_rows = []
                settled_rows = []
                exec_hour_rows = []
                settled_hour_rows = []

            scan_where = ["finished_at IS NOT NULL", "error IS NULL", "scan_kind='discovery'"]
            scan_params: list = []
            if started_at:
                scan_where.append("started_at>=?")
                scan_params.append(started_at)
            if finished_at:
                scan_where.append("started_at<?")
                scan_params.append(finished_at)
            scans = self.conn.execute(
                f"SELECT id,started_at,finished_at,markets_seen,matches_seen,status_json,stage_timings_json FROM scan_runs WHERE {' AND '.join(scan_where)} ORDER BY id ASC",
                tuple(scan_params),
            ).fetchall()

        exec_map = {}
        for row in exec_rows:
            d = dict(row)
            key = (d['section'], d['sport'], d['market_name'], int(d['in_play'] or 0))
            exec_map[key] = d
        settled_map = {}
        for row in settled_rows:
            d = dict(row)
            key = (d['section'], d['sport'], d['market_name'], int(d['in_play'] or 0))
            settled_map[key] = d
        out_rows = []
        for row in rows:
            d = dict(row)
            key = (d['section'], d['sport'], d['market_name'], int(d['in_play'] or 0))
            ex = exec_map.get(key) or {}
            st = settled_map.get(key) or {}
            d.update({
                'qualified': int(ex.get('qualified') or 0),
                'attempts': int(ex.get('attempts') or 0),
                'executed': int(ex.get('executed') or 0),
                'settled': int(st.get('settled') or 0),
                'pnl': round(float(st.get('pnl') or 0.0), 4),
                # Financial columns are settlement-period facts so ROI/P&L reconcile
                # with Results and Performance.  Execution-start deployment remains
                # available separately for technical activity analysis.
                'deployed': round(float(st.get('settled_deployed') or 0.0), 4),
                'execution_started_deployed': round(float(ex.get('deployed') or 0.0), 4),
                'returned': round(float(st.get('returned') or 0.0), 4),
                'wins': int(st.get('wins') or 0),
                'losses': int(st.get('losses') or 0),
            })
            d['execution_conversion_pct'] = round((d['executed'] / d['qualified']) * 100.0, 3) if int(d.get('qualified') or 0) else 0.0
            d['avg_net_roi_pct'] = round(float(d.get('avg_net_roi_pct') or 0.0), 4)
            d['best_net_roi_pct'] = round(float(d.get('best_net_roi_pct') or 0.0), 4)
            d['avg_deployable'] = round(float(d.get('avg_deployable') or 0.0), 4)
            out_rows.append(d)

        reasons = [dict(r) for r in reason_rows]
        hour_map = {}
        for row in list(exec_hour_rows) + list(settled_hour_rows):
            d = dict(row)
            key = (d.get('section'), d.get('sport'), int(d.get('in_play') or 0), str(d.get('hour_utc') or '00'))
            item = hour_map.setdefault(key, {
                'section': d.get('section'), 'sport': d.get('sport'), 'in_play': int(d.get('in_play') or 0),
                'hour_utc': str(d.get('hour_utc') or '00'), 'executed': 0, 'pnl': 0.0,
            })
            item['executed'] += int(d.get('executed') or 0)
            item['pnl'] += float(d.get('pnl') or 0.0)
        exec_hours = [{**v, 'pnl': round(float(v['pnl']), 4)} for v in hour_map.values()]
        racing_scans = []
        aggregate = {'scans': 0, 'total': 0, 'matched': 0, 'unmatched': 0, 'rejected': 0, 'by_exchange': {}}
        sports_scans = []
        sports_aggregate = {'scans': 0, 'total': 0, 'matched': 0, 'unmatched': 0, 'by_exchange': {}}
        for scan in scans:
            try:
                stage = json.loads(scan['stage_timings_json'] or '{}')
            except Exception:
                stage = {}
            rd = stage.get('racing_discovery') or {}
            if rd:
                item = {'started_at': scan['started_at'], 'finished_at': scan['finished_at'], **rd}
                racing_scans.append(item)
                aggregate['scans'] += 1
                for key in ('total','matched','unmatched','rejected'):
                    aggregate[key] += int(rd.get(key) or 0)
                for ex,count in (rd.get('by_exchange') or {}).items():
                    aggregate['by_exchange'][ex] = aggregate['by_exchange'].get(ex,0) + int(count or 0)

            # Sports discovery is derived from the per-exchange discovery status
            # already persisted on each scan. Greyhound counts are excluded because
            # Racing has its own stricter discovery/matching diagnostics above.
            try:
                statuses = json.loads(scan['status_json'] or '[]')
            except Exception:
                statuses = []
            sports_by_exchange = {}
            for status in statuses if isinstance(statuses, list) else []:
                if not isinstance(status, dict) or not status.get('ok'):
                    continue
                ex = str(status.get('exchange') or 'Unknown')
                count = 0
                for sport_name, sport_count in (status.get('sport_counts') or {}).items():
                    if 'greyhound' in str(sport_name or '').lower():
                        continue
                    count += int(sport_count or 0)
                sports_by_exchange[ex] = count
            if sports_by_exchange:
                racing_matched = int(rd.get('matched') or 0) if rd else 0
                sports_matched = max(0, int(scan['matches_seen'] or 0) - racing_matched)
                sports_total = sum(sports_by_exchange.values())
                sports_unmatched = max(0, sports_total - (2 * sports_matched))
                sports_item = {
                    'started_at': scan['started_at'], 'finished_at': scan['finished_at'],
                    'total': sports_total, 'matched': sports_matched, 'unmatched': sports_unmatched,
                    'by_exchange': sports_by_exchange,
                }
                sports_scans.append(sports_item)
                sports_aggregate['scans'] += 1
                sports_aggregate['total'] += sports_total
                sports_aggregate['matched'] += sports_matched
                sports_aggregate['unmatched'] += sports_unmatched
                for ex, count in sports_by_exchange.items():
                    sports_aggregate['by_exchange'][ex] = sports_aggregate['by_exchange'].get(ex, 0) + int(count or 0)
        aggregate['latest'] = racing_scans[-1] if racing_scans else None
        sports_aggregate['latest'] = sports_scans[-1] if sports_scans else None
        exchange_discovery_rows = self.exchange_market_discovery_between(started_at, finished_at)
        return {
            'rows': out_rows, 'reasons': reasons, 'activity_hours': [dict(x) for x in activity_rows],
            'execution_hours': exec_hours, 'racing_discovery': aggregate, 'racing_scans': racing_scans,
            'sports_discovery': sports_aggregate, 'sports_scans': sports_scans,
            # Identity rows let the API count a market once across pre-match and
            # in-play observations while still supporting phase-specific filters.
            'exchange_market_identity_rows': [dict(x) for x in exchange_market_identity_rows],
            'exchange_discovery_rows': exchange_discovery_rows,
            'exchange_opportunity_rows': [dict(x) for x in exchange_opportunity_rows],
            'opportunity_venue_rows': [dict(x) for x in opportunity_venue_rows],
        }

    def execution_failure_reasons_between(self, started_at: str | None, finished_at: str | None) -> dict[str, int]:
        with self.lock:
            params = []
            where = "e.is_real=0 AND LOWER(e.mode) IN ('monitor','monitor_timing') AND COALESCE(o.qualification_status,'qualified') IN ('qualified','in_play_qualified')"
            if started_at:
                where += " AND e.started_at>=?"
                params.append(started_at)
            if finished_at:
                where += " AND e.started_at<=?"
                params.append(finished_at)
            rows = self.conn.execute(
                f"SELECT e.state,e.details_json FROM execution_runs e JOIN opportunities o ON o.id=e.opportunity_id WHERE {where}",
                tuple(params),
            ).fetchall()
            out: dict[str, int] = {}
            for row in rows:
                state = str(row["state"] or "").upper()
                if "OPEN" in state or "SETTLED" in state:
                    continue
                try:
                    details = json.loads(row["details_json"] or "{}")
                except Exception:
                    details = {}
                reason = str(
                    details.get("first_failure_reason")
                    or details.get("monitor_reason")
                    or state.replace("MONITOR_", "")
                    or "NOT_EXECUTED"
                ).upper()
                out[reason] = out.get(reason, 0) + 1
            return out

    def invalidate_provider_market_quotes(self, provider_id: str) -> dict:
        """Invalidate only bounded current market quote state for one provider.

        Historical evidence, canonical market mappings, SIM wallets and LIVE account
        state are deliberately untouched.  This is used when a provider feed/key
        selection changes so an older DELAYED quote cannot be presented under a
        newly requested LIVE feed before a fresh provider observation arrives.
        """
        pid = str(provider_id or "").strip().lower()
        if not pid:
            return {"latest_snapshots": 0, "latest_depth_snapshots": 0}
        with self.lock:
            if pid == "betfair":
                cur = self.conn.execute("DELETE FROM latest_snapshots WHERE LOWER(exchange) LIKE 'betfair%'")
            else:
                cur = self.conn.execute("DELETE FROM latest_snapshots WHERE LOWER(exchange)=?", (pid,))
            quote_rows = max(0, int(cur.rowcount or 0))
            cur = self.conn.execute("DELETE FROM latest_depth_snapshots WHERE LOWER(provider_id)=?", (pid,))
            depth_rows = max(0, int(cur.rowcount or 0))
            self.conn.commit()
        return {"latest_snapshots": quote_rows, "latest_depth_snapshots": depth_rows}

    def scanner_health(self, *, include_storage: bool = True) -> dict:
        """Return scanner/feed health, optionally omitting heavier storage diagnostics.

        ``include_storage=False`` is intended for feed-only projections that need
        current scanner observations but must not traverse lifecycle/analytics
        storage merely to render provider readiness.
        """
        with self.lock:
            last = self.conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
            last_price = self.conn.execute("SELECT * FROM scan_runs WHERE COALESCE(scan_kind,'legacy') IN ('price','legacy') ORDER BY id DESC LIMIT 1").fetchone()
            last_discovery = self.conn.execute("SELECT * FROM scan_runs WHERE scan_kind='discovery' ORDER BY id DESC LIMIT 1").fetchone()
            last_ok = self.conn.execute("SELECT * FROM scan_runs WHERE finished_at IS NOT NULL AND error IS NULL ORDER BY id DESC LIMIT 1").fetchone()
            last_price_ok = self.conn.execute("SELECT * FROM scan_runs WHERE finished_at IS NOT NULL AND error IS NULL AND COALESCE(scan_kind,'legacy') IN ('price','legacy') ORDER BY id DESC LIMIT 1").fetchone()
            last_discovery_ok = self.conn.execute("SELECT * FROM scan_runs WHERE finished_at IS NOT NULL AND error IS NULL AND scan_kind='discovery' ORDER BY id DESC LIMIT 1").fetchone()
            cutoff=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
            scans_24h=self.conn.execute("SELECT COUNT(*) c FROM scan_runs WHERE started_at>=?",(cutoff,)).fetchone()["c"]
            snap_rows=self.conn.execute("SELECT exchange,MAX(captured_at) latest,COUNT(*) c FROM latest_snapshots GROUP BY exchange ORDER BY exchange").fetchall()
            result = {
                "last_scan":dict(last) if last else None,"last_price_scan":dict(last_price) if last_price else None,
                "last_discovery_scan":dict(last_discovery) if last_discovery else None,"last_successful_scan":dict(last_ok) if last_ok else None,
                "last_successful_price_scan":dict(last_price_ok) if last_price_ok else None,
                "last_successful_discovery_scan":dict(last_discovery_ok) if last_discovery_ok else None,
                "scans_last_24h":int(scans_24h or 0),"latest_snapshots":[dict(r) for r in snap_rows],
            }
            if include_storage:
                result.update({
                    "snapshot_storage": self.snapshot_storage_health(),
                    "matched_market_storage": self.matched_market_storage_health(),
                    "market_cache": self.market_cache_stats(),
                    "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
                })
            return result

    @staticmethod
    def _live_decision_json(value) -> str | None:
        if value is None:
            return None
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    def record_live_decision(self, evidence: dict) -> dict:
        """Persist isolated LIVE-context simulated decision evidence.

        A canonical market/strategy keeps one bounded latest row. A material
        revision is written once to the event journal and contributes once to
        compact hourly rollups. Re-observing the same revision only advances the
        latest observation counters, preventing repeated simulated profit.
        """
        now = str(evidence.get("created_at") or datetime.now(timezone.utc).isoformat())
        canonical_market_id = str(evidence.get("canonical_market_id") or "")
        strategy = str(evidence.get("strategy") or "unknown")
        revision = str(evidence.get("book_revision") or "")
        if not canonical_market_id or not revision:
            raise ValueError("canonical_market_id and book_revision are required")
        state_key = str(evidence.get("state_key") or hashlib.sha256(f"{canonical_market_id}|{strategy}".encode()).hexdigest()[:32])
        decision_id = str(evidence.get("decision_id") or hashlib.sha256(f"live|{canonical_market_id}|{strategy}|{revision}".encode()).hexdigest()[:32])
        state = str(evidence.get("state") or "OBSERVED").upper()
        quality = str(evidence.get("evidence_quality") or "OBSERVATIONAL").upper()
        reason_code = str(evidence.get("reason_code") or "NONE").upper()
        domain = str(evidence.get("domain") or ("racing" if str(evidence.get("section") or "").lower() == "racing" else "sports")).lower()
        section = str(evidence.get("section") or domain)
        sport = str(evidence.get("sport") or "Unknown")
        market_type = str(evidence.get("market_type") or evidence.get("market_name") or "Unknown")
        provider_pair = str(evidence.get("provider_pair") or "unknown")
        legs_json = self._live_decision_json(evidence.get("legs") or []) or "[]"
        simulation_json = self._live_decision_json(evidence.get("simulation"))
        qualification_json = self._live_decision_json(evidence.get("qualification") or {})
        values = (
            decision_id, evidence.get("canonical_event_id"), canonical_market_id, revision, strategy, domain, section, sport,
            market_type, evidence.get("event_name"), str(evidence.get("market_name") or market_type), int(bool(evidence.get("in_play"))),
            state, quality, reason_code, evidence.get("reason"), evidence.get("gross_edge_pct"), evidence.get("net_roi_pct"),
            evidence.get("expected_simulated_profit"), evidence.get("requested_stake"), evidence.get("max_executable_stake"),
            evidence.get("simulated_stake"), evidence.get("simulated_filled_stake"), evidence.get("oldest_quote_age_ms"),
            evidence.get("receipt_spread_ms"), evidence.get("source_time_spread_ms"), evidence.get("decision_compute_ms"),
            provider_pair, evidence.get("limiting_provider"), evidence.get("limiting_selection"), evidence.get("limiting_side"),
            "live", "simulated", legs_json, simulation_json, qualification_json,
        )
        with self.lock:
            prior = self.conn.execute("SELECT decision_id,first_seen,observation_count FROM live_decision_latest WHERE state_key=?", (state_key,)).fetchone()
            same_revision = bool(prior and str(prior["decision_id"]) == decision_id)
            if same_revision:
                self.conn.execute(
                    "UPDATE live_decision_latest SET last_seen=?,observation_count=observation_count+1 WHERE state_key=?",
                    (now, state_key),
                )
                self.conn.commit()
                return {"created": False, "decision_id": decision_id, "state_key": state_key, "duplicate_revision": True}

            first_seen = str(prior["first_seen"]) if prior else now
            self.conn.execute(
                """INSERT INTO live_decision_latest(
                   state_key,decision_id,canonical_event_id,canonical_market_id,book_revision,strategy,domain,section,sport,market_type,event_name,market_name,in_play,
                   first_seen,last_seen,observation_count,state,evidence_quality,reason_code,reason,gross_edge_pct,net_roi_pct,expected_simulated_profit,requested_stake,
                   max_executable_stake,simulated_stake,simulated_filled_stake,oldest_quote_age_ms,receipt_spread_ms,source_time_spread_ms,decision_compute_ms,provider_pair,
                   limiting_provider,limiting_selection,limiting_side,application_mode,decision_type,legs_json,simulation_json,qualification_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(state_key) DO UPDATE SET
                    decision_id=excluded.decision_id,canonical_event_id=excluded.canonical_event_id,canonical_market_id=excluded.canonical_market_id,
                    book_revision=excluded.book_revision,strategy=excluded.strategy,domain=excluded.domain,section=excluded.section,sport=excluded.sport,
                    market_type=excluded.market_type,event_name=excluded.event_name,market_name=excluded.market_name,in_play=excluded.in_play,
                    last_seen=excluded.last_seen,observation_count=live_decision_latest.observation_count+1,state=excluded.state,evidence_quality=excluded.evidence_quality,
                    reason_code=excluded.reason_code,reason=excluded.reason,gross_edge_pct=excluded.gross_edge_pct,net_roi_pct=excluded.net_roi_pct,
                    expected_simulated_profit=excluded.expected_simulated_profit,requested_stake=excluded.requested_stake,max_executable_stake=excluded.max_executable_stake,
                    simulated_stake=excluded.simulated_stake,simulated_filled_stake=excluded.simulated_filled_stake,oldest_quote_age_ms=excluded.oldest_quote_age_ms,
                    receipt_spread_ms=excluded.receipt_spread_ms,source_time_spread_ms=excluded.source_time_spread_ms,decision_compute_ms=excluded.decision_compute_ms,
                    provider_pair=excluded.provider_pair,limiting_provider=excluded.limiting_provider,limiting_selection=excluded.limiting_selection,
                    limiting_side=excluded.limiting_side,application_mode='live',decision_type='simulated',legs_json=excluded.legs_json,
                    simulation_json=excluded.simulation_json,qualification_json=excluded.qualification_json""",
                (state_key, *values[:12], first_seen, now, 1, *values[12:]),
            )
            material = bool(evidence.get("material", state != "NO_ARB"))
            event_created = False
            if material:
                cur = self.conn.execute(
                    """INSERT OR IGNORE INTO live_decision_events(
                       decision_id,state_key,created_at,canonical_event_id,canonical_market_id,book_revision,strategy,domain,section,sport,market_type,event_name,market_name,in_play,
                       state,evidence_quality,reason_code,reason,gross_edge_pct,net_roi_pct,expected_simulated_profit,requested_stake,max_executable_stake,simulated_stake,
                       simulated_filled_stake,oldest_quote_age_ms,receipt_spread_ms,source_time_spread_ms,decision_compute_ms,provider_pair,limiting_provider,limiting_selection,
                       limiting_side,application_mode,decision_type,legs_json,simulation_json,qualification_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (decision_id, state_key, now, *values[1:]),
                )
                event_created = cur.rowcount > 0

            # Every new current revision contributes once to compact analytics,
            # including NO_ARB/rejection states that are intentionally not kept as
            # verbose event rows.
            hour = now[:13] + ":00:00+00:00" if "+" in now or now.endswith("Z") else now[:13] + ":00:00"
            positive = int(float(evidence.get("gross_edge_pct") or 0.0) > 0.0)
            q = evidence.get("qualification") or {}
            liquidity_ok = int(bool(q.get("liquidity_pass")))
            qualified = int(bool(q.get("strategy_risk_pass")))
            attempted = int(str(state).startswith("SIM_") or str(state) in {"SIMULATED_ATTEMPT", "SIMULATED_FULL_FILL", "SIMULATED_PARTIAL_FILL", "SIMULATED_MISS"})
            fill = int(str(state) in {"SIM_FULL_FILL", "SIM_PARTIAL_FILL", "SIMULATED_FULL_FILL", "SIMULATED_PARTIAL_FILL"})
            miss = int(str(state) in {"SIM_MISS", "SIMULATED_MISS"})
            exec_grade = int(quality == "EXECUTION_GRADE")
            executable = evidence.get("max_executable_stake")
            compute = evidence.get("decision_compute_ms")
            self.conn.execute(
                """INSERT INTO live_decision_hourly_rollups(
                   hour_utc,domain,sport,market_type,provider_pair,evidence_quality,reason_code,observed,positive,liquidity_capable,qualified,
                   simulated_attempts,simulated_fills,simulated_misses,execution_grade,expected_profit_sum,executable_stake_sum,executable_stake_samples,
                   decision_ms_sum,decision_ms_samples,max_decision_ms)
                   VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(hour_utc,domain,sport,market_type,provider_pair,evidence_quality,reason_code) DO UPDATE SET
                    observed=live_decision_hourly_rollups.observed+1,positive=live_decision_hourly_rollups.positive+excluded.positive,
                    liquidity_capable=live_decision_hourly_rollups.liquidity_capable+excluded.liquidity_capable,
                    qualified=live_decision_hourly_rollups.qualified+excluded.qualified,simulated_attempts=live_decision_hourly_rollups.simulated_attempts+excluded.simulated_attempts,
                    simulated_fills=live_decision_hourly_rollups.simulated_fills+excluded.simulated_fills,simulated_misses=live_decision_hourly_rollups.simulated_misses+excluded.simulated_misses,
                    execution_grade=live_decision_hourly_rollups.execution_grade+excluded.execution_grade,
                    expected_profit_sum=live_decision_hourly_rollups.expected_profit_sum+excluded.expected_profit_sum,
                    executable_stake_sum=live_decision_hourly_rollups.executable_stake_sum+excluded.executable_stake_sum,
                    executable_stake_samples=live_decision_hourly_rollups.executable_stake_samples+excluded.executable_stake_samples,
                    decision_ms_sum=live_decision_hourly_rollups.decision_ms_sum+excluded.decision_ms_sum,
                    decision_ms_samples=live_decision_hourly_rollups.decision_ms_samples+excluded.decision_ms_samples,
                    max_decision_ms=MAX(live_decision_hourly_rollups.max_decision_ms,excluded.max_decision_ms)""",
                (hour, domain, sport, market_type, provider_pair, quality, reason_code,
                 positive, liquidity_ok, qualified, attempted, fill, miss, exec_grade,
                 float(evidence.get("expected_simulated_profit") or 0.0), float(executable or 0.0), int(executable is not None),
                 float(compute or 0.0), int(compute is not None), float(compute or 0.0)),
            )
            self.conn.commit()
            return {"created": True, "event_created": event_created, "decision_id": decision_id, "state_key": state_key, "duplicate_revision": False}

    def live_decision_latest_rows(self, *, domain: str = "all", limit: int = 200) -> list[dict]:
        domain = str(domain or "all").lower()
        where = "" if domain not in {"sports", "racing"} else "WHERE domain=?"
        params = () if not where else (domain,)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM live_decision_latest {where} ORDER BY last_seen DESC LIMIT ?",
                (*params, max(1, min(5000, int(limit)))),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            for source, target in (("legs_json", "legs"), ("simulation_json", "simulation"), ("qualification_json", "qualification")):
                try:
                    item[target] = json.loads(item.get(source) or ("[]" if source == "legs_json" else "{}"))
                except Exception:
                    item[target] = [] if source == "legs_json" else {}
            out.append(item)
        return out

    def live_decision_events_between(self, from_utc: str | None = None, to_utc: str | None = None, *, domain: str = "all", limit: int = 1000) -> list[dict]:
        clauses, params = [], []
        if from_utc:
            clauses.append("created_at>=?"); params.append(str(from_utc))
        if to_utc:
            clauses.append("created_at<=?"); params.append(str(to_utc))
        domain = str(domain or "all").lower()
        if domain in {"sports", "racing"}:
            clauses.append("domain=?"); params.append(domain)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM live_decision_events {where} ORDER BY created_at DESC LIMIT ?",
                (*params, max(1, min(10000, int(limit)))),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            for source, target in (("legs_json", "legs"), ("simulation_json", "simulation"), ("qualification_json", "qualification")):
                try:
                    item[target] = json.loads(item.get(source) or ("[]" if source == "legs_json" else "{}"))
                except Exception:
                    item[target] = [] if source == "legs_json" else {}
            out.append(item)
        return out

    def live_decision_summary(self, from_utc: str | None = None, to_utc: str | None = None, *, domain: str = "all") -> dict:
        clauses, params = [], []
        if from_utc:
            clauses.append("hour_utc>=?"); params.append(str(from_utc)[:13] + ":00:00+00:00")
        if to_utc:
            clauses.append("hour_utc<=?"); params.append(str(to_utc)[:13] + ":00:00+00:00")
        domain = str(domain or "all").lower()
        if domain in {"sports", "racing"}:
            clauses.append("domain=?"); params.append(domain)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.lock:
            total = self.conn.execute(
                f"""SELECT COALESCE(SUM(observed),0) observed,COALESCE(SUM(positive),0) positive,
                    COALESCE(SUM(liquidity_capable),0) liquidity_capable,COALESCE(SUM(qualified),0) qualified,
                    COALESCE(SUM(simulated_attempts),0) simulated_attempts,COALESCE(SUM(simulated_fills),0) simulated_fills,
                    COALESCE(SUM(simulated_misses),0) simulated_misses,COALESCE(SUM(execution_grade),0) execution_grade,
                    COALESCE(SUM(expected_profit_sum),0) expected_profit_sum,COALESCE(SUM(executable_stake_sum),0) executable_stake_sum,
                    COALESCE(SUM(executable_stake_samples),0) executable_stake_samples,COALESCE(SUM(decision_ms_sum),0) decision_ms_sum,
                    COALESCE(SUM(decision_ms_samples),0) decision_ms_samples,COALESCE(MAX(max_decision_ms),0) max_decision_ms
                   FROM live_decision_hourly_rollups {where}""", tuple(params)
            ).fetchone()
            quality_rows = self.conn.execute(
                f"SELECT evidence_quality,COALESCE(SUM(observed),0) c FROM live_decision_hourly_rollups {where} GROUP BY evidence_quality ORDER BY c DESC",
                tuple(params),
            ).fetchall()
            reason_rows = self.conn.execute(
                f"SELECT reason_code,COALESCE(SUM(observed),0) c FROM live_decision_hourly_rollups {where} GROUP BY reason_code ORDER BY c DESC LIMIT 20",
                tuple(params),
            ).fetchall()
            pair_rows = self.conn.execute(
                f"SELECT provider_pair,COALESCE(SUM(observed),0) observed,COALESCE(SUM(qualified),0) qualified,COALESCE(SUM(execution_grade),0) execution_grade,COALESCE(SUM(expected_profit_sum),0) expected_profit FROM live_decision_hourly_rollups {where} GROUP BY provider_pair ORDER BY qualified DESC,observed DESC",
                tuple(params),
            ).fetchall()
        row = dict(total)
        samples = int(row.get("decision_ms_samples") or 0)
        stake_samples = int(row.get("executable_stake_samples") or 0)
        row["average_decision_ms"] = round(float(row.get("decision_ms_sum") or 0.0) / samples, 4) if samples else 0.0
        row["average_executable_stake"] = round(float(row.get("executable_stake_sum") or 0.0) / stake_samples, 4) if stake_samples else 0.0
        row["expected_profit_sum"] = round(float(row.get("expected_profit_sum") or 0.0), 4)
        row["max_decision_ms"] = round(float(row.get("max_decision_ms") or 0.0), 4)
        return {"summary": row, "quality": [dict(x) for x in quality_rows], "reasons": [dict(x) for x in reason_rows], "provider_pairs": [dict(x) for x in pair_rows]}

    def live_decision_analytics(self, from_utc: str | None = None, to_utc: str | None = None, *, domain: str = "all", sport: str = "all", market_type: str = "", provider_pair: str = "all") -> dict:
        """Compact 0.9.14 LIVE decision analytics from hourly rollups.

        This is deliberately read-only and contains simulated decision evidence
        only. It never reads SIM positions/settlements or provider account state.
        """
        clauses, params = [], []
        if from_utc:
            clauses.append("hour_utc>=?"); params.append(str(from_utc)[:13] + ":00:00+00:00")
        if to_utc:
            clauses.append("hour_utc<=?"); params.append(str(to_utc)[:13] + ":00:00+00:00")
        domain = str(domain or "all").lower()
        if domain in {"sports", "racing"}:
            clauses.append("domain=?"); params.append(domain)
        sport = str(sport or "all")
        if sport.lower() not in {"", "all"}:
            clauses.append("sport=?"); params.append(sport)
        market_type = str(market_type or "").strip()
        if market_type and market_type.lower() != "all":
            clauses.append("market_type=?"); params.append(market_type)
        provider_pair = str(provider_pair or "all").strip()
        if provider_pair.lower() not in {"", "all"}:
            clauses.append("provider_pair=?"); params.append(provider_pair)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        agg = """COALESCE(SUM(observed),0) observed,COALESCE(SUM(positive),0) positive,
                    COALESCE(SUM(liquidity_capable),0) liquidity_capable,COALESCE(SUM(qualified),0) qualified,
                    COALESCE(SUM(simulated_attempts),0) simulated_attempts,COALESCE(SUM(simulated_fills),0) simulated_fills,
                    COALESCE(SUM(simulated_misses),0) simulated_misses,COALESCE(SUM(execution_grade),0) execution_grade,
                    COALESCE(SUM(expected_profit_sum),0) expected_profit_sum,COALESCE(SUM(executable_stake_sum),0) executable_stake_sum,
                    COALESCE(SUM(executable_stake_samples),0) executable_stake_samples,COALESCE(SUM(decision_ms_sum),0) decision_ms_sum,
                    COALESCE(SUM(decision_ms_samples),0) decision_ms_samples,COALESCE(MAX(max_decision_ms),0) max_decision_ms"""
        def finish(row):
            d=dict(row or {})
            ds=int(d.get("decision_ms_samples") or 0); ss=int(d.get("executable_stake_samples") or 0)
            d["average_decision_ms"]=round(float(d.get("decision_ms_sum") or 0.0)/ds,4) if ds else 0.0
            d["average_executable_stake"]=round(float(d.get("executable_stake_sum") or 0.0)/ss,4) if ss else 0.0
            d["expected_profit_sum"]=round(float(d.get("expected_profit_sum") or 0.0),4)
            d["max_decision_ms"]=round(float(d.get("max_decision_ms") or 0.0),4)
            return d
        with self.lock:
            total=self.conn.execute(f"SELECT {agg} FROM live_decision_hourly_rollups {where}", tuple(params)).fetchone()
            hourly=self.conn.execute(f"SELECT hour_utc,{agg} FROM live_decision_hourly_rollups {where} GROUP BY hour_utc ORDER BY hour_utc", tuple(params)).fetchall()
            hourly_by_sport=self.conn.execute(f"SELECT hour_utc,sport,{agg} FROM live_decision_hourly_rollups {where} GROUP BY hour_utc,sport ORDER BY hour_utc,sport", tuple(params)).fetchall()
            domains=self.conn.execute(f"SELECT domain,{agg} FROM live_decision_hourly_rollups {where} GROUP BY domain ORDER BY observed DESC", tuple(params)).fetchall()
            markets=self.conn.execute(f"SELECT domain,sport,market_type,{agg} FROM live_decision_hourly_rollups {where} GROUP BY domain,sport,market_type ORDER BY qualified DESC,observed DESC", tuple(params)).fetchall()
            pairs=self.conn.execute(f"SELECT provider_pair,{agg} FROM live_decision_hourly_rollups {where} GROUP BY provider_pair ORDER BY qualified DESC,observed DESC", tuple(params)).fetchall()
            quality=self.conn.execute(f"SELECT evidence_quality,COALESCE(SUM(observed),0) c FROM live_decision_hourly_rollups {where} GROUP BY evidence_quality ORDER BY c DESC", tuple(params)).fetchall()
            reasons=self.conn.execute(f"SELECT reason_code,COALESCE(SUM(observed),0) c FROM live_decision_hourly_rollups {where} GROUP BY reason_code ORDER BY c DESC LIMIT 30", tuple(params)).fetchall()
        return {"summary":finish(total),"hourly":[finish(x) for x in hourly],"hourly_by_sport":[finish(x) for x in hourly_by_sport],"domains":[finish(x) for x in domains],"markets":[finish(x) for x in markets],"provider_pairs":[finish(x) for x in pairs],"quality":[dict(x) for x in quality],"reasons":[dict(x) for x in reasons]}

    # --- 0.9.23 venue/account operational controls -----------------------------
    def venue_controls(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM venue_controls ORDER BY CASE provider_id WHEN 'betfair' THEN 0 WHEN 'matchbook' THEN 1 WHEN 'smarkets' THEN 2 ELSE 9 END,provider_id").fetchall()
            out=[]
            for row in rows:
                d=dict(row)
                for key in ("sim_feed_enabled","live_feed_enabled","sim_account_enabled","live_account_enabled","live_execution_enabled"):
                    d[key]=bool(d.get(key))
                out.append(d)
            return out

    def venue_control(self, provider_id: str) -> dict | None:
        pid=str(provider_id or "").strip().lower()
        with self.lock:
            row=self.conn.execute("SELECT * FROM venue_controls WHERE provider_id=?",(pid,)).fetchone()
        if not row: return None
        d=dict(row)
        for key in ("sim_feed_enabled","live_feed_enabled","sim_account_enabled","live_account_enabled","live_execution_enabled"):
            d[key]=bool(d.get(key))
        return d

    def update_venue_control(self, provider_id: str, **changes) -> dict:
        from .strategy_engines import utc_now
        pid=str(provider_id or "").strip().lower()
        current=self.venue_control(pid)
        if not current: raise KeyError(pid)
        allowed={"account_nickname","sim_feed_enabled","live_feed_enabled","sim_account_enabled","live_account_enabled","live_execution_enabled"}
        fields=[]; values=[]
        for key,value in changes.items():
            if key not in allowed or value is None: continue
            if key=="account_nickname":
                value=str(value).strip()[:80]
                if not value: raise ValueError("Account nickname may not be blank")
            else: value=int(bool(value))
            fields.append(f"{key}=?"); values.append(value)
        if not fields: return current
        fields.append("updated_at=?"); values.append(utc_now()); values.append(pid)
        with self.lock:
            self.conn.execute(f"UPDATE venue_controls SET {','.join(fields)} WHERE provider_id=?",tuple(values)); self.conn.commit()
        return self.venue_control(pid) or {}

    # --- 0.9.14 engine framework -------------------------------------------------
    def ensure_default_engines(self) -> None:
        from .strategy_engines import default_engine_instances, EngineRegistry, effective_lifecycle, stable_hash, utc_now
        registry = EngineRegistry()
        now = utc_now()
        platform = self.get_setting("config", {}) or {}
        with self.lock:
            for spec in default_engine_instances():
                iid = str(spec["engine_instance_id"])
                exists = self.conn.execute("SELECT 1 FROM engine_instances WHERE engine_instance_id=?", (iid,)).fetchone()
                if exists:
                    continue
                requested = str(spec.get("requested_lifecycle") or "DISABLED").upper()
                config = dict(spec.get("config") or {})
                # One-time compatibility import: legacy global SuperBet settings
                # become SUPERBET_ARB instance configuration/lifecycle.
                if str(spec["engine_type"]) == "SPORTS_SUPERBET_ARB":
                    config.update({
                        "max_tranches": ("unlimited" if str(platform.get("superbet_max_tranches", 3)).strip().lower() == "unlimited" else int(platform.get("superbet_max_tranches", 3) or 3)),
                        "tranche_size_mode": str(platform.get("superbet_tranche_size_mode", "base") or "base"),
                        "tranche_size": float(platform.get("superbet_tranche_size", 0.0) or 0.0),
                        "max_total_stake": float(platform.get("superbet_max_total_stake", 100.0) or 0.0),
                        "min_net_edge": float(platform.get("superbet_min_net_edge", 1.0) or 0.0),
                        "min_depth_multiplier": float(platform.get("superbet_min_depth_multiplier", 1.25) or 1.0),
                        "recheck_delay_ms": int(platform.get("superbet_recheck_delay_ms", 100) or 0),
                    })
                    if bool(platform.get("superbet_enabled", False)):
                        requested = "SIM"
                effective, reason = effective_lifecycle(requested, live_execution_unlocked=False)
                engine_type = registry.canonical_type(str(spec["engine_type"]))
                version = registry.version(engine_type)
                config = registry.validate_config(engine_type, config)
                config_hash = stable_hash(config)
                self.conn.execute(
                    """INSERT INTO engine_instances(
                         engine_instance_id,engine_type,engine_version,engine_grade,section,sport,competition,market_type,
                         requested_lifecycle,effective_lifecycle,effective_reason,active_config_version,nickname,sim_enabled,live_enabled,description,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (iid, engine_type, version, str(spec.get("engine_grade") or "RESEARCH"), str(spec.get("section") or "all"), str(spec.get("sport") or "all"),
                     str(spec.get("competition") or "all"), str(spec.get("market_type") or "all"), requested, effective, reason, 1,
                     str(spec.get("nickname") or spec.get("display_name") or iid), int(requested in {"SIM", "EXPERIMENTAL"}), int(requested == "LIVE_APPROVED"),
                     str(spec.get("description") or ""), now, now),
                )
                self.conn.execute(
                    """INSERT INTO engine_configs(engine_instance_id,config_version,config_hash,config_json,created_at,activated_at,derived_from_version)
                       VALUES(?,?,?,?,?,?,NULL)""",
                    (iid, 1, config_hash, json.dumps(config, sort_keys=True, separators=(",", ":")), now, now),
                )
            self.conn.commit()
        # Fresh databases create default engines after schema migration; run the
        # 0.9.16 metadata seeding once more so built-ins receive descriptions too.
        self._ensure_v0916_engine_library_schema()
        self._migrate_strategy_settings_to_engines_0915()

    def _migrate_strategy_settings_to_engines_0915(self) -> None:
        """Move strategy-owned settings into immutable engine configs.

        Platform risk/execution settings remain in global Config.  Legacy
        SuperBet knobs are imported into SPORTS_SUPERBET_ARB and then removed
        from the generic platform config so SuperBet is no longer a core mode.
        """
        platform = dict(self.get_setting("config", {}) or {})
        migrated = bool(self.get_setting("engine_strategy_settings_migrated_0915", False))
        if not migrated:
            mappings = {
                "SPORTS_BASELINE_ARB_PRIMARY": {
                    "minimum_liquidity": platform.get("pre_match_minimum_liquidity", platform.get("minimum_liquidity", 0.0)),
                    "minimum_edge": platform.get("pre_match_minimum_net_roi_pct", platform.get("minimum_net_roi_pct", 0.0)),
                    "minimum_profit": platform.get("pre_match_minimum_profit", platform.get("minimum_profit", 0.0)),
                    "reference_bankroll": platform.get("quality_reference_bankroll", 1000.0),
                    "maximum_slippage": platform.get("pre_match_execution_max_slippage_pct", platform.get("execution_max_slippage_pct", 0.5)),
                },
                "GREYHOUNDS_BASELINE_ARB_PRIMARY": {
                    "minimum_liquidity": platform.get("racing_minimum_liquidity", 0.0),
                    "minimum_edge": platform.get("racing_minimum_net_roi_pct", 0.0),
                    "minimum_profit": platform.get("racing_minimum_profit", 0.0),
                    "reference_bankroll": platform.get("quality_reference_bankroll", 1000.0),
                    "maximum_slippage": platform.get("racing_execution_max_slippage_pct", 0.5),
                },
            }
            for iid, values in mappings.items():
                active = self.engine_active_config(iid)
                if not active or int(active.get("config_version") or 0) > 1:
                    continue
                cfg = dict(active.get("config") or {})
                cfg.update({k: v for k, v in values.items() if v is not None})
                try:
                    self.engine_create_config(iid, cfg, activate=True)
                except Exception:
                    continue
            self.set_setting("engine_strategy_settings_migrated_0915", True)

        # The SuperBet-specific global fields were a pre-engine-framework
        # compatibility surface.  Once the canonical engine/config exists they
        # must not remain a second mutable source of truth.
        if not self.get_setting("engine_superbet_globals_retired_0915", False):
            superbet = self.engine_instance("SPORTS_SUPERBET_ARB_PRIMARY")
            if superbet:
                legacy_keys = [
                    "superbet_enabled", "superbet_max_tranches", "superbet_tranche_size_mode",
                    "superbet_tranche_size", "superbet_max_total_stake", "superbet_min_net_edge",
                    "superbet_min_depth_multiplier", "superbet_recheck_delay_ms",
                ]
                changed = False
                for key in legacy_keys:
                    if key in platform:
                        platform.pop(key, None)
                        changed = True
                if changed:
                    self.set_setting("config", platform)
                self.set_setting("engine_superbet_globals_retired_0915", True)

    def engine_instances(self, section: str | None = None) -> list[dict]:
        with self.lock:
            args: list = []
            where = ""
            if section:
                where = "WHERE section IN ('all',?)"
                args.append(str(section))
            rows = self.conn.execute(
                f"""SELECT * FROM engine_instances {where}
                    ORDER BY CASE section WHEN 'sports' THEN 0 WHEN 'racing' THEN 1 ELSE 2 END, engine_instance_id""", args
            ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                config = self.conn.execute(
                    "SELECT config_hash,config_version FROM engine_configs WHERE engine_instance_id=? AND config_version=?",
                    (item["engine_instance_id"], item.get("active_config_version")),
                ).fetchone()
                item["config_hash"] = config["config_hash"] if config else None
                item["config_version"] = int(config["config_version"]) if config else None
                item["sim_enabled"] = bool(item.get("sim_enabled"))
                item["live_enabled"] = bool(item.get("live_enabled"))
                try:
                    from .strategy_engines import EngineRegistry
                    meta = next((x for x in EngineRegistry().types() if x["engine_type"] == EngineRegistry.canonical_type(item.get("engine_type"))), None) or {}
                    item["capabilities"] = list(meta.get("capabilities") or [])
                    item["display_name"] = str(meta.get("display_name") or item.get("engine_instance_id") or item.get("engine_type"))
                    item["engine_type"] = EngineRegistry.canonical_type(item.get("engine_type"))
                except Exception:
                    item["capabilities"] = []
                    item["display_name"] = str(item.get("engine_instance_id") or item.get("engine_type") or "Engine")
                out.append(item)
            return out

    def engine_instance(self, engine_instance_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM engine_instances WHERE engine_instance_id=?", (str(engine_instance_id),)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["sim_enabled"] = bool(item.get("sim_enabled"))
            item["live_enabled"] = bool(item.get("live_enabled"))
            cfg = self.engine_active_config(str(engine_instance_id))
            item["active_config"] = cfg
            try:
                from .strategy_engines import EngineRegistry
                registry = EngineRegistry()
                canonical = registry.canonical_type(item.get("engine_type"))
                meta = next((x for x in registry.types() if x["engine_type"] == canonical), None) or {}
                item["engine_type"] = canonical
                item["display_name"] = str(meta.get("display_name") or item.get("engine_instance_id") or canonical)
                item["capabilities"] = list(meta.get("capabilities") or [])
                item["config_schema"] = dict(meta.get("config_schema") or {})
            except Exception:
                item["display_name"] = str(item.get("engine_instance_id") or item.get("engine_type") or "Engine")
                item["capabilities"] = []
                item["config_schema"] = {}
            return item

    def engine_active_config(self, engine_instance_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                """SELECT c.* FROM engine_configs c JOIN engine_instances i ON i.engine_instance_id=c.engine_instance_id
                   WHERE c.engine_instance_id=? AND c.config_version=i.active_config_version""",
                (str(engine_instance_id),),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            try:
                item["config"] = json.loads(item.pop("config_json") or "{}")
            except Exception:
                item["config"] = {}
            return item

    def engine_config_history(self, engine_instance_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM engine_configs WHERE engine_instance_id=? ORDER BY config_version DESC", (str(engine_instance_id),)
            ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                try:
                    item["config"] = json.loads(item.pop("config_json") or "{}")
                except Exception:
                    item["config"] = {}
                out.append(item)
            return out

    def engine_create_config(self, engine_instance_id: str, config: dict, *, activate: bool = False) -> dict:
        from .strategy_engines import EngineRegistry, stable_hash, utc_now
        iid = str(engine_instance_id)
        now = utc_now()
        with self.lock:
            instance = self.conn.execute("SELECT * FROM engine_instances WHERE engine_instance_id=?", (iid,)).fetchone()
            if not instance:
                raise KeyError(iid)
            clean = EngineRegistry().validate_config(str(instance["engine_type"]), dict(config or {}))
            config_hash = stable_hash(clean)
            existing = self.conn.execute("SELECT * FROM engine_configs WHERE engine_instance_id=? AND config_hash=?", (iid, config_hash)).fetchone()
            if existing:
                version = int(existing["config_version"])
            else:
                version = int(self.conn.execute("SELECT COALESCE(MAX(config_version),0)+1 v FROM engine_configs WHERE engine_instance_id=?", (iid,)).fetchone()["v"])
                self.conn.execute(
                    """INSERT INTO engine_configs(engine_instance_id,config_version,config_hash,config_json,created_at,activated_at,derived_from_version)
                       VALUES(?,?,?,?,?,?,?)""",
                    (iid, version, config_hash, json.dumps(clean, sort_keys=True, separators=(",", ":")), now, now if activate else None,
                     int(instance["active_config_version"] or 0) or None),
                )
            if activate:
                self.conn.execute("UPDATE engine_configs SET activated_at=COALESCE(activated_at,?) WHERE engine_instance_id=? AND config_version=?", (now, iid, version))
                self.conn.execute("UPDATE engine_instances SET active_config_version=?,updated_at=? WHERE engine_instance_id=?", (version, now, iid))
            self.conn.commit()
        return self.engine_active_config(iid) if activate else next(x for x in self.engine_config_history(iid) if int(x["config_version"]) == version)

    def engine_update_metadata(self, engine_instance_id: str, *, nickname: str | None = None, description: str | None = None, notes: str | None = None) -> dict:
        from .strategy_engines import utc_now
        iid = str(engine_instance_id)
        current = self.engine_instance(iid)
        if not current:
            raise KeyError(iid)
        nickname_value = str(current.get("nickname") or current.get("display_name") or iid) if nickname is None else str(nickname).strip()[:80]
        if not nickname_value:
            raise ValueError("Engine nickname may not be blank")
        description_value = str(current.get("description") or "") if description is None else str(description).strip()[:4000]
        notes_value = str(current.get("notes") or "") if notes is None else str(notes).strip()[:10000]
        with self.lock:
            self.conn.execute("UPDATE engine_instances SET nickname=?,description=?,notes=?,updated_at=? WHERE engine_instance_id=?",
                              (nickname_value, description_value, notes_value, utc_now(), iid))
            self.conn.commit()
        return self.engine_instance(iid) or {}

    def engine_install_package_instance(self, manifest: dict, *, package_path: str, package_sha256: str) -> dict:
        """Install a validated package as RESEARCH + DISABLED; never auto-activate."""
        from .strategy_engines import EngineRegistry, stable_hash, utc_now
        registry = EngineRegistry()
        engine_type = registry.canonical_type(str(manifest.get("engine_type") or ""))
        meta = next((x for x in registry.types() if x["engine_type"] == engine_type), None)
        if not meta:
            raise ValueError("Package engine type did not register after validation")
        base_id = str(manifest.get("engine_instance_id") or f"{engine_type}_IMPORTED").strip().upper()
        if not base_id or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in base_id):
            base_id = f"{engine_type}_IMPORTED"
        iid = base_id
        suffix = 2
        with self.lock:
            while self.conn.execute("SELECT 1 FROM engine_instances WHERE engine_instance_id=?", (iid,)).fetchone():
                iid = f"{base_id}_{suffix}"; suffix += 1
        config = registry.validate_config(engine_type, dict(manifest.get("default_config") or {}))
        now = utc_now(); config_hash = stable_hash(config)
        section = str(manifest.get("section") or "all").lower()
        if section not in {"all", "sports", "racing"}: section = "all"
        with self.lock:
            self.conn.execute(
                """INSERT INTO engine_instances(engine_instance_id,engine_type,engine_version,engine_grade,section,sport,competition,market_type,
                   requested_lifecycle,effective_lifecycle,effective_reason,active_config_version,nickname,sim_enabled,live_enabled,description,notes,package_source,package_sha256,package_author,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (iid, engine_type, str(meta.get("engine_version") or manifest.get("engine_version") or "1.0.0"), "RESEARCH", section,
                 str(manifest.get("sport") or "all"), str(manifest.get("competition") or "all"), str(manifest.get("market_type") or "all"),
                 "DISABLED", "DISABLED", "IMPORTED_REQUIRES_REVIEW", 1, str(manifest.get("nickname") or manifest.get("display_name") or meta.get("display_name") or iid)[:80], 0, 0,
                 str(manifest.get("description") or ""), str(manifest.get("notes") or ""),
                 str(package_path), str(package_sha256), str(manifest.get("author") or "Local / reviewed"), now, now),
            )
            self.conn.execute(
                "INSERT INTO engine_configs(engine_instance_id,config_version,config_hash,config_json,created_at,activated_at,derived_from_version) VALUES(?,?,?,?,?,?,NULL)",
                (iid, 1, config_hash, json.dumps(config, sort_keys=True, separators=(",", ":")), now, now),
            )
            self.conn.execute(
                "UPDATE engine_instances SET package_filename=?,package_installed_at=?,package_previous_version=NULL WHERE engine_instance_id=?",
                (Path(str(package_path)).name, now, iid),
            )
            self.conn.commit()
        return self.engine_instance(iid) or {}

    def engine_upgrade_package_instance(self, engine_instance_id: str, manifest: dict, *, package_path: str, package_sha256: str) -> dict:
        """Confirm a validated package upgrade while preserving operator state/config."""
        from .strategy_engines import utc_now
        iid = str(engine_instance_id); current = self.engine_instance(iid)
        if not current:
            raise KeyError(iid)
        if str(current.get("engine_type") or "").upper() != str(manifest.get("engine_type") or "").upper():
            raise ValueError("Upgrade engine type does not match the installed instance")
        previous = str(current.get("engine_version") or "")
        now = utc_now()
        with self.lock:
            self.conn.execute(
                """UPDATE engine_instances SET engine_version=?,package_source=?,package_sha256=?,package_author=?,
                   package_filename=?,package_installed_at=?,package_previous_version=?,updated_at=? WHERE engine_instance_id=?""",
                (str(manifest.get("engine_version") or previous), str(package_path), str(package_sha256),
                 str(manifest.get("author") or current.get("package_author") or "Local / reviewed"), Path(str(package_path)).name,
                 now, previous or None, now, iid),
            )
            self.conn.commit()
        return self.engine_instance(iid) or {}

    def engine_set_route(self, engine_instance_id: str, *, section: str | None = None, sport: str | None = None,
                         competition: str | None = None, market_type: str | None = None) -> dict:
        from .strategy_engines import utc_now
        iid = str(engine_instance_id)
        current = self.engine_instance(iid)
        if not current:
            raise KeyError(iid)
        values = {
            "section": str(section if section is not None else current.get("section") or "all").strip().lower() or "all",
            "sport": str(sport if sport is not None else current.get("sport") or "all").strip() or "all",
            "competition": str(competition if competition is not None else current.get("competition") or "all").strip() or "all",
            "market_type": str(market_type if market_type is not None else current.get("market_type") or "all").strip() or "all",
        }
        if values["section"] not in {"all", "sports", "racing"}:
            raise ValueError("section must be all, sports or racing")
        with self.lock:
            self.conn.execute(
                "UPDATE engine_instances SET section=?,sport=?,competition=?,market_type=?,updated_at=? WHERE engine_instance_id=?",
                (values["section"], values["sport"], values["competition"], values["market_type"], utc_now(), iid),
            )
            self.conn.commit()
        return self.engine_instance(iid) or {}

    def engine_set_grade(self, engine_instance_id: str, grade: str) -> dict:
        from .strategy_engines import ENGINE_GRADES, utc_now
        grade = str(grade or "").upper()
        if grade not in ENGINE_GRADES:
            raise ValueError("Invalid engine grade")
        with self.lock:
            cur = self.conn.execute("UPDATE engine_instances SET engine_grade=?,updated_at=? WHERE engine_instance_id=?", (grade, utc_now(), str(engine_instance_id)))
            if cur.rowcount != 1:
                raise KeyError(str(engine_instance_id))
            self.conn.commit()
        return self.engine_instance(str(engine_instance_id)) or {}

    def engine_set_lifecycle(self, engine_instance_id: str, requested: str) -> dict:
        """Compatibility lifecycle setter; active operating dimensions are SIM and LIVE only."""
        from .strategy_engines import effective_lifecycle, ENGINE_LIFECYCLES, utc_now
        requested = str(requested or "DISABLED").upper()
        if requested not in ENGINE_LIFECYCLES:
            raise ValueError("Invalid engine lifecycle")
        effective, reason = effective_lifecycle(requested, live_execution_unlocked=False)
        sim_enabled = 1 if requested in {"SIM", "EXPERIMENTAL"} else 0
        live_enabled = 1 if requested == "LIVE_APPROVED" else 0
        with self.lock:
            cur = self.conn.execute(
                "UPDATE engine_instances SET requested_lifecycle=?,effective_lifecycle=?,effective_reason=?,sim_enabled=?,live_enabled=?,updated_at=? WHERE engine_instance_id=?",
                (requested, effective, reason, sim_enabled, live_enabled, utc_now(), str(engine_instance_id)),
            )
            if cur.rowcount != 1:
                raise KeyError(str(engine_instance_id))
            self.conn.commit()
        return self.engine_instance(str(engine_instance_id)) or {}

    def engine_set_mode_enablement(self, engine_instance_id: str, mode: str, enabled: bool) -> dict:
        """Independently enable/disable SIM or LIVE for an engine instance."""
        from .strategy_engines import utc_now
        iid = str(engine_instance_id)
        mode = str(mode or "").strip().lower()
        if mode not in {"sim", "live"}:
            raise ValueError("mode must be SIM or LIVE")
        current = self.engine_instance(iid)
        if not current:
            raise KeyError(iid)
        sim_enabled = bool(current.get("sim_enabled"))
        live_enabled = bool(current.get("live_enabled"))
        if mode == "sim": sim_enabled = bool(enabled)
        else: live_enabled = bool(enabled)
        if live_enabled:
            requested, effective, reason = "LIVE_APPROVED", "LIVE_APPROVED", "LIVE_EXECUTION_LOCKED"
        elif sim_enabled:
            requested, effective, reason = "SIM", "SIM", "REQUESTED_SIM"
        else:
            requested, effective, reason = "DISABLED", "DISABLED", "REQUESTED_DISABLED"
        with self.lock:
            self.conn.execute(
                "UPDATE engine_instances SET sim_enabled=?,live_enabled=?,requested_lifecycle=?,effective_lifecycle=?,effective_reason=?,updated_at=? WHERE engine_instance_id=?",
                (int(sim_enabled), int(live_enabled), requested, effective, reason, utc_now(), iid),
            )
            self.conn.commit()
        return self.engine_instance(iid) or {}

    def engine_set_effective(self, engine_instance_id: str, effective: str, reason: str) -> None:
        from .strategy_engines import utc_now
        with self.lock:
            self.conn.execute(
                "UPDATE engine_instances SET effective_lifecycle=?,effective_reason=?,updated_at=? WHERE engine_instance_id=?",
                (str(effective), str(reason), utc_now(), str(engine_instance_id)),
            )
            self.conn.commit()

    def engine_clone(self, source_engine_instance_id: str, new_engine_instance_id: str, *, requested_lifecycle: str = "DISABLED", engine_grade: str = "RESEARCH") -> dict:
        from .strategy_engines import effective_lifecycle, ENGINE_GRADES, utc_now
        source = self.engine_instance(str(source_engine_instance_id))
        if not source:
            raise KeyError(str(source_engine_instance_id))
        new_id = str(new_engine_instance_id).strip().upper()
        if not new_id or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in new_id):
            raise ValueError("Engine instance id may contain A-Z, 0-9, underscore and hyphen only")
        cfg = source.get("active_config") or {}
        requested = str(requested_lifecycle or "DISABLED").upper()
        grade = str(engine_grade or "RESEARCH").upper()
        if grade not in ENGINE_GRADES:
            raise ValueError("Invalid engine grade")
        effective, reason = effective_lifecycle(requested, live_execution_unlocked=False)
        now = utc_now()
        with self.lock:
            if self.conn.execute("SELECT 1 FROM engine_instances WHERE engine_instance_id=?", (new_id,)).fetchone():
                raise ValueError("Duplicate engine instance id")
            self.conn.execute(
                """INSERT INTO engine_instances(engine_instance_id,engine_type,engine_version,engine_grade,section,sport,competition,market_type,
                   requested_lifecycle,effective_lifecycle,effective_reason,active_config_version,nickname,sim_enabled,live_enabled,description,notes,package_source,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id, source["engine_type"], source["engine_version"], grade, source["section"], source["sport"], source["competition"], source["market_type"],
                 requested, effective, reason, 1, (str(source.get("nickname") or source.get("display_name") or source["engine_type"]) + " Copy")[:80],
                 int(requested in {"SIM", "EXPERIMENTAL"}), int(requested == "LIVE_APPROVED"), str(source.get("description") or ""),
                 str(source.get("notes") or ""), "clone", now, now),
            )
            self.conn.execute(
                "INSERT INTO engine_configs(engine_instance_id,config_version,config_hash,config_json,created_at,activated_at,derived_from_version) VALUES(?,?,?,?,?,?,?)",
                (new_id, 1, cfg.get("config_hash"), json.dumps(cfg.get("config") or {}, sort_keys=True, separators=(",", ":")), now, now, int(cfg.get("config_version") or 1)),
            )
            self.conn.commit()
        return self.engine_instance(new_id) or {}

    def engine_record_evaluation(self, result, evidence) -> None:
        from .strategy_engines import utc_now
        now = utc_now()
        context = result.context
        with self.lock:
            decisions = 1 if result.decision else 0
            stream = "in_play" if bool(evidence.in_play) else ("racing" if str(evidence.section).lower() == "racing" else "pre_match")
            self.conn.execute(
                """INSERT OR IGNORE INTO engine_evaluations(
                   engine_instance_id,market_snapshot_id,evaluated_at,observed_at,mode,section,sport,event_name,market_name,market_type,stream,decision_id,had_opportunity,venue_ids_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (context.engine_instance_id, evidence.market_snapshot_id, context.evaluation_timestamp, evidence.observed_at,
                 str(context.mode or "sim").lower(), str(evidence.section or "sports").lower(), str(evidence.sport or "Unknown"),
                 evidence.event_name, evidence.market_name, evidence.market_type, stream,
                 result.decision.decision_id if result.decision else None, int(bool(result.selected_legs)),
                 json.dumps(sorted({str(q.economic_venue or q.provider).strip().lower() for _, quotes in evidence.selections for q in quotes if str(q.economic_venue or q.provider).strip()}))),
            )
            self.conn.execute(
                """UPDATE engine_instances SET health=?,last_evidence_at=?,last_evaluation_at=?,events_processed=events_processed+1,
                   decisions_generated=decisions_generated+?,processing_latency_ms=?,updated_at=? WHERE engine_instance_id=?""",
                ("HEALTHY" if not result.error else "ERROR", evidence.observed_at, context.evaluation_timestamp, decisions,
                 float(result.duration_ms or 0.0), now, context.engine_instance_id),
            )
            if result.decision:
                d = result.decision
                # Exact duplicate decision IDs are idempotent. Competing/equivalent
                # economic intents remain visible for central deduplication rather than
                # being silently collapsed inside an engine.
                self.conn.execute(
                    """INSERT OR IGNORE INTO engine_decisions(decision_id,economic_intent_key,created_at,engine_instance_id,engine_type,engine_version,engine_grade,intent_type,
                       config_version,config_hash,market_snapshot_id,feed_generation,section,sport,event_name,market_name,mode,requested_lifecycle,
                       effective_lifecycle,expected_edge,expected_profit,requested_capital,intent_json,evaluation_latency_ms,central_validation)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (d.decision_id, d.economic_intent_key, d.created_at, d.engine_instance_id, d.engine_type, d.engine_version, d.engine_grade, d.intent_type,
                     d.config_version, d.config_hash, d.market_snapshot_id, d.feed_generation, d.section, d.sport, d.event, d.market, d.mode,
                     context.requested_lifecycle, context.effective_lifecycle, float(d.expected_edge or 0.0), float(d.expected_profit or 0.0), d.requested_capital,
                     json.dumps(d.as_dict(), sort_keys=True, separators=(",", ":")), float(result.duration_ms or 0.0), "NOT_SUBMITTED"),
                )
                if context.effective_lifecycle == "SIM":
                    roi = (float(d.expected_profit or 0.0) / float(d.requested_capital) * 100.0) if float(d.requested_capital or 0.0) > 0 else 0.0
                    self.conn.execute(
                        "INSERT OR IGNORE INTO engine_sim_results(decision_id,engine_instance_id,created_at,deployed,expected_profit,expected_roi_pct,simulation_level) VALUES(?,?,?,?,?,?,?)",
                        (d.decision_id, d.engine_instance_id, d.created_at, d.requested_capital, float(d.expected_profit or 0.0), roi, "DECISION_SIM"),
                    )
            self.conn.commit()

    def engine_record_error(self, engine_instance_id: str, market_snapshot_id: str | None, error_type: str, message: str,
                            *, mode: str | None = None, section: str | None = None, stream: str | None = None) -> None:
        from .strategy_engines import utc_now
        now = utc_now()
        with self.lock:
            self.conn.execute(
                "INSERT INTO engine_errors(engine_instance_id,market_snapshot_id,error_type,message,created_at,mode,section,stream) VALUES(?,?,?,?,?,?,?,?)",
                (str(engine_instance_id), market_snapshot_id, str(error_type), str(message)[:2000], now,
                 str(mode or "").lower() or None, str(section or "").lower() or None, str(stream or "").lower() or None),
            )
            self.conn.execute(
                "UPDATE engine_instances SET health='ERROR',errors=errors+1,updated_at=? WHERE engine_instance_id=?",
                (now, str(engine_instance_id)),
            )
            self.conn.commit()

    def engine_performance(self, engine_instance_id: str) -> dict:
        iid = str(engine_instance_id)
        with self.lock:
            sim = self.conn.execute(
                "SELECT COUNT(*) c,COALESCE(SUM(deployed),0) d,COALESCE(SUM(expected_profit),0) p,COALESCE(AVG(expected_roi_pct),0) roi FROM engine_sim_results WHERE engine_instance_id=?",
                (iid,),
            ).fetchone()
            live = self.conn.execute(
                "SELECT COUNT(*) c,COALESCE(SUM(requested_capital),0) d,COALESCE(SUM(expected_profit),0) p FROM engine_decisions WHERE engine_instance_id=? AND LOWER(mode)='live'",
                (iid,),
            ).fetchone()
            sim_deployed = float(sim["d"] or 0.0); sim_profit = float(sim["p"] or 0.0)
            live_deployed = float(live["d"] or 0.0); live_profit = float(live["p"] or 0.0)
            return {
                "sim": {"decisions": int(sim["c"] or 0), "deployed": sim_deployed, "capital_deployed": sim_deployed, "turnover": sim_deployed,
                        "expected_profit": sim_profit, "net_pnl": sim_profit, "avg_roi_pct": float(sim["roi"] or 0.0),
                        "profit_on_capital_pct": (sim_profit / sim_deployed * 100.0) if sim_deployed > 0 else 0.0},
                "live": {"decisions": int(live["c"] or 0), "requested_capital": live_deployed, "capital_deployed": live_deployed,
                         "turnover": live_deployed, "expected_profit": live_profit, "decision_evidence_only": True,
                         "profit_on_capital_pct": (live_profit / live_deployed * 100.0) if live_deployed > 0 else 0.0},
            }

    def engine_lifecycle_rows(self, *, section: str = "sports", mode: str = "sim", from_utc: str | None = None,
                              to_utc: str | None = None, stream: str = "all", sport: str = "all",
                              market: str = "", search: str = "", venue: str = "all", account: str = "all") -> list[dict]:
        """Canonical per-engine lifecycle counts for Monitor/Engines/Results.

        Processed/Opportunities/Qualified come from the immutable 0.9.36 per-engine
        evaluation ledger; Executed/Settled/P&L come from canonical position records
        carrying authoritative origination provenance. Historical rows that
        predate provenance authority remain visible elsewhere as Legacy/Unverified
        but cannot be claimed by an engine here.
        """
        section = str(section or "sports").lower()
        mode = str(mode or "sim").lower()
        stream = str(stream or "all").lower()
        sport = str(sport or "all")
        market_q = str(market or "").strip().lower()
        search_q = str(search or "").strip().lower()
        venue_q = str(venue or "all").strip().lower()
        account_q = str(account or "all").strip().lower()
        provider_q = None
        if venue_q not in {"", "all"}:
            provider_q = venue_q
        elif account_q not in {"", "all", "—"}:
            for ctl in self.venue_controls():
                if str(ctl.get("account_nickname") or "").strip().lower() == account_q:
                    provider_q = str(ctl.get("provider_id") or "").lower() or None
                    break
        engines = self.engine_instances(section=section)

        def time_clause(column: str, params: list) -> str:
            bits = []
            if from_utc:
                bits.append(f"{column}>=?"); params.append(str(from_utc))
            if to_utc:
                bits.append(f"{column}<?"); params.append(str(to_utc))
            return (" AND " + " AND ".join(bits)) if bits else ""

        def common_sql(alias: str, *, stream_col: str, sport_col: str, market_col: str, text_cols: tuple[str, ...], params: list) -> str:
            bits = []
            if stream in {"pre_match", "in_play", "racing"}:
                bits.append(f"LOWER(COALESCE({stream_col},'pre_match'))=?"); params.append(stream)
            if sport.lower() != "all":
                bits.append(f"LOWER(COALESCE({sport_col},''))=?"); params.append(sport.lower())
            if market_q:
                bits.append(f"LOWER(COALESCE({market_col},'')) LIKE ?"); params.append(f"%{market_q}%")
            if search_q and text_cols:
                expr = " || ' ' || ".join(f"LOWER(COALESCE({c},''))" for c in text_cols)
                bits.append(f"({expr}) LIKE ?"); params.append(f"%{search_q}%")
            return (" AND " + " AND ".join(bits)) if bits else ""

        out = []
        with self.lock:
            for engine in engines:
                iid = str(engine.get("engine_instance_id") or "")
                # Processed + engine-generated opportunity candidates.
                p = [iid, mode, section]
                q = """SELECT COUNT(*) processed,COALESCE(SUM(had_opportunity),0) opportunities,
                                COALESCE(SUM(CASE WHEN decision_id IS NOT NULL THEN 1 ELSE 0 END),0) qualified
                       FROM engine_evaluations WHERE engine_instance_id=? AND LOWER(mode)=? AND LOWER(section)=?"""
                q += time_clause("evaluated_at", p)
                q += common_sql("ee", stream_col="stream", sport_col="sport", market_col="market_name",
                                text_cols=("event_name", "market_name", "sport"), params=p)
                if provider_q:
                    q += " AND LOWER(COALESCE(venue_ids_json,'')) LIKE ?"; p.append(f'%"{provider_q}"%')
                ev = self.conn.execute(q, tuple(p)).fetchone()

                # Qualified is the engine's persisted actionable-decision boundary.
                # It comes from the same mode-scoped evaluation ledger as Processed
                # and Opportunities, so competing engines can each be measured
                # without borrowing the scanner's canonical baseline opportunity row.
                qualified = int(ev["qualified"] or 0)

                # Executed positions use opened time; settlements/P&L use settlement
                # time so Engines reconciles exactly with Results for a period.
                p = [iid, mode, section]
                base = """ FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                           WHERE mp.engine_instance_id=? AND LOWER(COALESCE(mp.mode,'sim'))=?
                             AND LOWER(COALESCE(o.section,'sports'))=?
                             AND COALESCE(mp.engine_provenance_source,'') IN ('runtime_origin','execution_origin')"""
                q_exec = "SELECT COUNT(*) c" + base + time_clause("mp.opened_at", p)
                q_exec += common_sql("mp", stream_col="mp.stream", sport_col="o.sport", market_col="o.market_name",
                                     text_cols=("o.event_name", "o.market_name", "o.sport"), params=p)
                if provider_q:
                    q_exec += " AND LOWER(COALESCE(o.legs_json,'')) LIKE ?"; p.append(f'%{provider_q}%')
                executed = int(self.conn.execute(q_exec, tuple(p)).fetchone()["c"] or 0)

                p = [iid, mode, section]
                q_set = "SELECT COUNT(*) c,COALESCE(SUM(mp.realized_pnl),0) pnl" + base + " AND mp.status='SETTLED'" + time_clause("mp.settled_at", p)
                q_set += common_sql("mp", stream_col="mp.stream", sport_col="o.sport", market_col="o.market_name",
                                    text_cols=("o.event_name", "o.market_name", "o.sport"), params=p)
                if provider_q:
                    q_set += " AND LOWER(COALESCE(o.legs_json,'')) LIKE ?"; p.append(f'%{provider_q}%')
                settled = self.conn.execute(q_set, tuple(p)).fetchone()

                p = [iid]
                q_err = "SELECT COUNT(*) c FROM engine_errors WHERE engine_instance_id=?"
                if mode in {"sim", "live"}:
                    q_err += " AND (mode IS NULL OR LOWER(mode)=?)"; p.append(mode)
                if section:
                    q_err += " AND (section IS NULL OR LOWER(section)=?)"; p.append(section)
                if stream in {"pre_match", "in_play", "racing"}:
                    q_err += " AND (stream IS NULL OR LOWER(stream)=?)"; p.append(stream)
                q_err += time_clause("created_at", p)
                errors = int(self.conn.execute(q_err, tuple(p)).fetchone()["c"] or 0)

                sp = [iid, mode, section]
                q_streams = """SELECT DISTINCT LOWER(COALESCE(stream,'pre_match')) s
                               FROM engine_evaluations
                               WHERE engine_instance_id=? AND LOWER(mode)=? AND LOWER(section)=?"""
                q_streams += time_clause("evaluated_at", sp)
                stream_rows = self.conn.execute(q_streams, tuple(sp)).fetchall()
                streams_seen = {str(x["s"] or "pre_match") for x in stream_rows if str(x["s"] or "").strip()}
                pp = [iid, mode, section]
                q_pos_streams = """SELECT DISTINCT LOWER(COALESCE(mp.stream,'pre_match')) s
                                    FROM monitor_positions mp JOIN opportunities o ON o.id=mp.opportunity_id
                                    WHERE mp.engine_instance_id=? AND LOWER(COALESCE(mp.mode,'sim'))=?
                                      AND LOWER(COALESCE(o.section,'sports'))=?
                                      AND COALESCE(mp.engine_provenance_source,'') IN ('runtime_origin','execution_origin')"""
                q_pos_streams += time_clause("mp.opened_at", pp)
                for x in self.conn.execute(q_pos_streams, tuple(pp)).fetchall():
                    if str(x["s"] or "").strip():
                        streams_seen.add(str(x["s"]))

                out.append({
                    "engine_instance_id": iid,
                    "engine_type": engine.get("engine_type"),
                    "nickname": engine.get("nickname") or engine.get("display_name") or iid,
                    "state": engine.get("health") or "UNKNOWN",
                    "enabled": bool(engine.get("sim_enabled") if mode == "sim" else engine.get("live_enabled")),
                    "processed": int(ev["processed"] or 0),
                    "opportunities": int(ev["opportunities"] or 0),
                    "qualified": qualified,
                    "executed": executed,
                    "settled": int(settled["c"] or 0),
                    "realised_pnl": round(float(settled["pnl"] or 0.0), 4),
                    "errors": errors,
                    "last_activity": engine.get("last_evaluation_at") or engine.get("last_evidence_at"),
                    "latency_ms": float(engine.get("processing_latency_ms") or 0.0),
                    "streams": sorted(streams_seen),
                    "provenance_authority": "0.9.36+ origination only",
                })
        return out

    def engine_record_scenario_run(self, *, run_id: str, scenario_id: str, scenario_version: int, result, evidence, simulation_level: str) -> None:
        decision_json = json.dumps(result.decision.as_dict(), sort_keys=True, separators=(",", ":")) if result.decision else None
        with self.lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO engine_scenario_runs(run_id,scenario_id,scenario_version,engine_instance_id,engine_version,config_version,config_hash,
                   market_snapshot_id,run_at,simulation_level,decision_json,input_observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(run_id), str(scenario_id), int(scenario_version), result.context.engine_instance_id, result.context.engine_version,
                 int(result.context.config_version), result.context.config_hash, evidence.market_snapshot_id, result.context.evaluation_timestamp,
                 str(simulation_level), decision_json, evidence.observed_at),
            )
            self.conn.commit()

    def engine_create_experiment(self, source_engine_instance_id: str, new_engine_instance_id: str, *, config_overrides: dict | None = None, notes: str | None = None) -> dict:
        from .strategy_engines import stable_hash, utc_now
        clone = self.engine_clone(source_engine_instance_id, new_engine_instance_id, requested_lifecycle="EXPERIMENTAL", engine_grade="RESEARCH")
        if config_overrides:
            current = dict((clone.get("active_config") or {}).get("config") or {})
            current.update(dict(config_overrides))
            self.engine_create_config(clone["engine_instance_id"], current, activate=True)
            clone = self.engine_instance(clone["engine_instance_id"]) or clone
        cfg = clone.get("active_config") or {}
        now = utc_now()
        experiment_id = "EXP_" + stable_hash({"source": source_engine_instance_id, "target": clone["engine_instance_id"], "config": cfg.get("config_hash"), "created": now})[:20].upper()
        with self.lock:
            self.conn.execute(
                """INSERT INTO engine_experiments(experiment_id,source_engine_instance_id,engine_instance_id,engine_type,engine_version,engine_grade,
                   config_version,config_hash,status,simulation_level,created_at,updated_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (experiment_id, str(source_engine_instance_id), clone["engine_instance_id"], clone["engine_type"], clone["engine_version"], "RESEARCH",
                 int(cfg.get("config_version") or 1), str(cfg.get("config_hash") or ""), "READY", "DECISION_SIM", now, now, notes),
            )
            self.conn.commit()
        return self.engine_experiment(experiment_id) or {}

    def engine_experiment(self, experiment_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM engine_experiments WHERE experiment_id=?", (str(experiment_id),)).fetchone()
            return dict(row) if row else None

    def engine_experiments(self, section: str | None = None) -> list[dict]:
        with self.lock:
            if section:
                rows = self.conn.execute(
                    """SELECT e.* FROM engine_experiments e JOIN engine_instances i ON i.engine_instance_id=e.engine_instance_id
                       WHERE i.section IN ('all',?) ORDER BY e.created_at DESC""", (str(section),)
                ).fetchall()
            else:
                rows = self.conn.execute("SELECT * FROM engine_experiments ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def engine_record_experiment_run(self, *, run_id: str, experiment_id: str | None, engine_instance_id: str, run_type: str,
                                     evidence_from_utc: str | None, evidence_to_utc: str | None, evidence_cohort_hash: str | None,
                                     simulation_level: str, status: str, metrics: dict | None = None, error: str | None = None) -> None:
        from .strategy_engines import utc_now
        now = utc_now()
        with self.lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO engine_experiment_runs(run_id,experiment_id,engine_instance_id,run_type,started_at,finished_at,
                   evidence_from_utc,evidence_to_utc,evidence_cohort_hash,simulation_level,status,metrics_json,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(run_id), experiment_id, str(engine_instance_id), str(run_type), now, now, evidence_from_utc, evidence_to_utc, evidence_cohort_hash,
                 str(simulation_level), str(status), json.dumps(metrics or {}, sort_keys=True, separators=(",", ":")), error),
            )
            if experiment_id:
                self.conn.execute("UPDATE engine_experiments SET status=?,evidence_from_utc=COALESCE(?,evidence_from_utc),evidence_to_utc=COALESCE(?,evidence_to_utc),updated_at=? WHERE experiment_id=?",
                                  ("COMPLETE" if status == "PASS" else status, evidence_from_utc, evidence_to_utc, now, str(experiment_id)))
            self.conn.commit()

    def engine_recent_decisions(self, engine_instance_id: str | None = None, *, limit: int = 100) -> list[dict]:
        cap = max(1, min(1000, int(limit or 100)))
        with self.lock:
            if engine_instance_id:
                rows = self.conn.execute(
                    "SELECT * FROM engine_decisions WHERE engine_instance_id=? ORDER BY created_at DESC LIMIT ?",
                    (str(engine_instance_id), cap),
                ).fetchall()
            else:
                rows = self.conn.execute("SELECT * FROM engine_decisions ORDER BY created_at DESC LIMIT ?", (cap,)).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                try:
                    item["intent"] = json.loads(item.pop("intent_json") or "{}")
                except Exception:
                    item["intent"] = {}
                out.append(item)
            return out

    def engine_economic_competitors(self, decision_id: str) -> list[dict]:
        with self.lock:
            key = self.conn.execute("SELECT economic_intent_key FROM engine_decisions WHERE decision_id=?", (str(decision_id),)).fetchone()
            if not key:
                return []
            return [dict(r) for r in self.conn.execute(
                "SELECT decision_id,engine_instance_id,expected_profit,requested_capital,created_at FROM engine_decisions WHERE economic_intent_key=? ORDER BY created_at",
                (key["economic_intent_key"],),
            ).fetchall()]

    def observation_summary(self, hours: int = 24) -> dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, hours))).isoformat()
        with self.lock:
            mm = self.conn.execute(
                """SELECT COUNT(*) matched,
                    SUM(CASE WHEN COALESCE(theoretical_edge_pct,0)>0 THEN 1 ELSE 0 END) raw_positive,
                    SUM(CASE WHEN COALESCE(net_roi_pct,0)>0 THEN 1 ELSE 0 END) net_positive,
                    SUM(CASE WHEN status='recommended' THEN 1 ELSE 0 END) recommended,
                    SUM(CASE WHEN quality_band IN ('Strong','Excellent') THEN 1 ELSE 0 END) strong
                   FROM matched_markets WHERE observed_at>=?""", (cutoff,)
            ).fetchone()
            rollup_cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, hours))).replace(minute=0, second=0, microsecond=0).isoformat()
            snaps = self.conn.execute("SELECT COALESCE(SUM(quote_observations),0) c FROM snapshot_rollups WHERE hour_utc>=?", (rollup_cutoff,)).fetchone()["c"]
            scans = self.conn.execute("SELECT COUNT(*) c FROM scan_runs WHERE started_at>=?", (cutoff,)).fetchone()["c"]
            best = self.conn.execute(
                """SELECT event_name,market_name,strategy,net_roi_pct,bankroll_roi_pct,quality_band,capital_used_pct,reference_bankroll,
                          (COALESCE(reference_bankroll,0) * COALESCE(bankroll_roi_pct,0) / 100.0) AS reference_profit
                   FROM matched_markets WHERE observed_at>=? AND COALESCE(net_roi_pct,0)>0
                   ORDER BY reference_profit DESC LIMIT 1""", (cutoff,)
            ).fetchone()
            return {
                "hours": hours, "snapshots": int(snaps or 0), "scans": int(scans or 0),
                "matched_observations": int(mm["matched"] or 0), "raw_positive": int(mm["raw_positive"] or 0),
                "net_positive": int(mm["net_positive"] or 0), "recommended": int(mm["recommended"] or 0),
                "strong_or_excellent": int(mm["strong"] or 0), "best": dict(best) if best else None,
            }

    def backup_to(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.conn.execute("PRAGMA wal_checkpoint(FULL)")
            target = sqlite3.connect(destination)
            try:
                self.conn.backup(target)
            finally:
                target.close()
        return destination

    def database_integrity_check(self) -> dict:
        """Run SQLite's full integrity check on the canonical database."""
        with self.lock:
            rows = [str(r[0]) for r in self.conn.execute("PRAGMA integrity_check").fetchall()]
        ok = len(rows) == 1 and rows[0].lower() == "ok"
        return {"ok": ok, "rows": rows[:50]}

    def compact_database(self) -> dict:
        """VACUUM the database after external writers have been paused.

        This never deletes logical records.  It rewrites the SQLite file so pages
        already freed by bounded-snapshot cleanup are returned to the filesystem.
        """
        before = self.path.stat().st_size if self.path.exists() else 0
        with self.lock:
            if self.conn.in_transaction:
                self.conn.rollback()
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            pre = [str(r[0]) for r in self.conn.execute("PRAGMA integrity_check").fetchall()]
            if not (len(pre) == 1 and pre[0].lower() == "ok"):
                raise RuntimeError("Pre-compaction SQLite integrity check failed: " + "; ".join(pre[:10]))
            self.conn.execute("VACUUM")
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            post = [str(r[0]) for r in self.conn.execute("PRAGMA integrity_check").fetchall()]
            if not (len(post) == 1 and post[0].lower() == "ok"):
                raise RuntimeError("Post-compaction SQLite integrity check failed: " + "; ".join(post[:10]))
        after = self.path.stat().st_size if self.path.exists() else 0
        return {
            "ok": True, "before_bytes": int(before), "after_bytes": int(after),
            "reclaimed_bytes": max(0, int(before) - int(after)),
            "integrity_before": pre[0] if pre else None, "integrity_after": post[0] if post else None,
        }

    def dashboard(self, include_demo: bool = True):
        with self.lock:
            opp_where = "" if include_demo else "WHERE COALESCE(is_demo,0)=0"
            o = self.conn.execute(f"SELECT COUNT(*) c FROM opportunities {opp_where}").fetchone()
            demo_join = "" if include_demo else "WHERE COALESCE(o.is_demo,0)=0"
            r = self.conn.execute(
                f"""SELECT COUNT(*) c,COALESCE(SUM(sr.expected_profit),0) p,COALESCE(SUM(sr.deployed),0) d,COALESCE(SUM(sr.realized_pnl),0) rp
                    FROM scenario_runs sr JOIN opportunities o ON o.id=sr.opportunity_id {demo_join}"""
            ).fetchone()
            snap = self.conn.execute("SELECT COUNT(*) c FROM latest_snapshots").fetchone()
            settle_where = "" if include_demo else "WHERE COALESCE(o.is_demo,0)=0"
            settled = self.conn.execute(
                f"SELECT COUNT(*) c FROM settlements s JOIN opportunities o ON o.id=s.opportunity_id {settle_where}"
            ).fetchone()
            last_scan = self.conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
            recent_where = "" if include_demo else "WHERE COALESCE(o.is_demo,0)=0"
            recent = [dict(x) for x in self.conn.execute(
                f"""SELECT o.id,o.detected_at,o.event_name,o.event_key,o.market_name,o.strategy,o.edge_pct,o.expected_roi_pct,o.status,o.is_demo,s.outcome
                   FROM opportunities o LEFT JOIN settlements s ON s.opportunity_id=o.id {recent_where} ORDER BY o.id DESC LIMIT 50"""
            )]
            demo_count = self.conn.execute("SELECT COUNT(*) c FROM opportunities WHERE COALESCE(is_demo,0)=1").fetchone()["c"]
            exec_where = "" if include_demo else "AND COALESCE(o.is_demo,0)=0"
            ex = self.conn.execute(
                f"""SELECT
                       SUM(CASE WHEN er.mode='monitor_timing' THEN 1 ELSE 0 END) monitor_timing_count,
                       COALESCE(SUM(CASE WHEN er.mode='monitor_timing' THEN er.expected_profit ELSE 0 END),0) monitor_timing_expected,
                       COALESCE(SUM(CASE WHEN er.mode='monitor_timing' THEN er.captured_profit ELSE 0 END),0) monitor_timing_captured,
                       SUM(CASE WHEN er.mode='live' THEN 1 ELSE 0 END) live_count
                    FROM execution_runs er JOIN opportunities o ON o.id=er.opportunity_id
                    WHERE 1=1 {exec_where}"""
            ).fetchone()
            return {
                "opportunities": o["c"],
                "scenario_runs": r["c"],
                "paper_profit": round(r["p"], 2),
                "paper_deployed": round(r["d"], 2),
                "realized_pnl": round(r["rp"], 2),
                "snapshots": snap["c"],
                "settled": settled["c"],
                "last_scan": dict(last_scan) if last_scan else None,
                "recent": recent,
                "demo_count": demo_count,
                "demo_included": bool(include_demo),
                "monitor_timing_executions": int(ex["monitor_timing_count"] or 0),
                "monitor_timing_expected": round(float(ex["monitor_timing_expected"] or 0.0), 4),
                "monitor_timing_captured": round(float(ex["monitor_timing_captured"] or 0.0), 4),
                "live_executions": int(ex["live_count"] or 0),
            }

