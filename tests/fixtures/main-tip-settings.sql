PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE settings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                happened_at INTEGER NOT NULL,
                label TEXT NOT NULL,
                digest TEXT NOT NULL,
                settings_json TEXT NOT NULL
            );
CREATE TABLE settings_history_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                warning TEXT NOT NULL
            );
CREATE TABLE test_sessions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                target TEXT NOT NULL,
                notes TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                ended_at INTEGER,
                settings_json TEXT NOT NULL,
                measurements_json TEXT NOT NULL DEFAULT '[]',
                events_truncated INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 0
            , captures_json TEXT NOT NULL DEFAULT '[]', captures_truncated INTEGER NOT NULL DEFAULT 0);
CREATE TABLE test_session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES test_sessions(id) ON DELETE CASCADE,
                happened_at INTEGER NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL
            );
CREATE TABLE profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                upload_json TEXT NOT NULL,
                download_json TEXT NOT NULL,
                random_seed INTEGER,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );
INSERT INTO profiles VALUES('slow','Slow','{"bandwidth_kbit": null, "bandwidth_randomness": {"enabled_probability": 100, "variation_percent": 0}, "corrupt_percent": 0, "corrupt_randomness": {"enabled_probability": 100, "variation_percent": 0}, "delay_ms": 0, "delay_randomness": {"enabled_probability": 100, "variation_percent": 0}, "duplicate_percent": 0, "duplicate_randomness": {"enabled_probability": 100, "variation_percent": 0}, "jitter_ms": 0, "jitter_randomness": {"enabled_probability": 100, "variation_percent": 0}, "loss_burst_packets": 0, "loss_percent": 0, "loss_randomness": {"enabled_probability": 100, "variation_percent": 0}, "reorder_percent": 0, "reorder_randomness": {"enabled_probability": 100, "variation_percent": 0}}','{"bandwidth_kbit": null, "bandwidth_randomness": {"enabled_probability": 100, "variation_percent": 0}, "corrupt_percent": 0, "corrupt_randomness": {"enabled_probability": 100, "variation_percent": 0}, "delay_ms": 0, "delay_randomness": {"enabled_probability": 100, "variation_percent": 0}, "duplicate_percent": 0, "duplicate_randomness": {"enabled_probability": 100, "variation_percent": 0}, "jitter_ms": 0, "jitter_randomness": {"enabled_probability": 100, "variation_percent": 0}, "loss_burst_packets": 0, "loss_percent": 0, "loss_randomness": {"enabled_probability": 100, "variation_percent": 0}, "reorder_percent": 0, "reorder_randomness": {"enabled_probability": 100, "variation_percent": 0}}',NULL,'{"outage_duration_randomness": {"enabled_probability": 100, "variation_percent": 0}, "outage_duration_seconds": 0, "outage_interval_randomness": {"enabled_probability": 100, "variation_percent": 0}, "outage_interval_seconds": 0, "outage_probability": 0, "reroll_interval_randomness": {"enabled_probability": 100, "variation_percent": 0}, "reroll_interval_seconds": 0, "website_domains": [], "website_mode": "unrestricted", "website_randomness": {"enabled_probability": 100, "variation_percent": 0}}');
CREATE TABLE devices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ipv4_address TEXT NOT NULL UNIQUE,
                profile_id TEXT NOT NULL REFERENCES profiles(id),
                mac_address TEXT
            );
INSERT INTO devices VALUES('alice','Alice','192.168.50.10','slow','02:00:00:00:00:01');
CREATE TABLE connected_device_labels (
                mac_address TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                device_type TEXT NOT NULL
            );
CREATE TABLE traffic_rate_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                upload_kbit INTEGER,
                download_kbit INTEGER,
                CHECK (
                    upload_kbit IS NULL OR (
                        typeof(upload_kbit) = 'integer'
                        AND upload_kbit BETWEEN 100 AND 1000000
                        AND upload_kbit % 100 = 0
                    )
                ),
                CHECK (
                    download_kbit IS NULL OR (
                        typeof(download_kbit) = 'integer'
                        AND download_kbit BETWEEN 100 AND 1000000
                        AND download_kbit % 100 = 0
                    )
                )
            );
INSERT INTO traffic_rate_settings VALUES(1,NULL,100000);
CREATE TABLE device_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL
            );
CREATE TABLE device_group_members (
                group_id TEXT NOT NULL REFERENCES device_groups(id) ON DELETE CASCADE,
                device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE
            );
CREATE TABLE scenarios (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                loop INTEGER NOT NULL
            );
INSERT INTO scenarios VALUES('c','Commute','[{"profile_id": "slow", "duration_seconds": 10}]',0);
CREATE TABLE scenario_runs (
                id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
                device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
                group_id TEXT REFERENCES device_groups(id) ON DELETE CASCADE,
                everyone_else INTEGER NOT NULL DEFAULT 0,
                started_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                CHECK ((device_id IS NOT NULL) + (group_id IS NOT NULL) + (everyone_else = 1) = 1)
            );
CREATE TABLE timers (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                started_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
CREATE TABLE progressions (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                start_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                end_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                started_at INTEGER NOT NULL,
                duration_seconds INTEGER NOT NULL,
                mode TEXT NOT NULL,
                traffic_target_queries INTEGER NOT NULL,
                traffic_baseline_queries INTEGER NOT NULL,
                traffic_target_bytes INTEGER NOT NULL DEFAULT 0,
                traffic_baseline_bytes INTEGER NOT NULL DEFAULT 0,
                website_domains_json TEXT NOT NULL,
                enabled INTEGER NOT NULL
            );
CREATE TABLE data_limits (
                device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                limit_bytes INTEGER NOT NULL,
                reset_mode TEXT NOT NULL,
                reset_interval_seconds INTEGER NOT NULL,
                reset_times_json TEXT NOT NULL,
                used_bytes INTEGER NOT NULL,
                counter_bytes INTEGER NOT NULL,
                last_reset_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
CREATE TABLE group_data_limits (
                group_id TEXT PRIMARY KEY REFERENCES device_groups(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                limit_bytes INTEGER NOT NULL,
                reset_mode TEXT NOT NULL,
                reset_interval_seconds INTEGER NOT NULL,
                reset_times_json TEXT NOT NULL,
                used_bytes INTEGER NOT NULL,
                counter_bytes INTEGER NOT NULL,
                last_reset_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
CREATE TABLE service_quotas (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                domains_json TEXT NOT NULL,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                limit_bytes INTEGER NOT NULL,
                weight_percent INTEGER NOT NULL DEFAULT 100,
                reset_mode TEXT NOT NULL,
                reset_interval_seconds INTEGER NOT NULL,
                reset_times_json TEXT NOT NULL,
                used_bytes INTEGER NOT NULL,
                counter_bytes INTEGER NOT NULL,
                last_reset_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
CREATE TABLE service_assignments (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                domains_json TEXT NOT NULL,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                enabled INTEGER NOT NULL
            );
CREATE TABLE time_quotas (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                domains_json TEXT NOT NULL,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                limit_seconds INTEGER NOT NULL,
                window_duration_seconds INTEGER NOT NULL,
                reset_mode TEXT NOT NULL,
                reset_interval_seconds INTEGER NOT NULL,
                reset_times_json TEXT NOT NULL,
                used_seconds INTEGER NOT NULL,
                counter_bytes INTEGER NOT NULL,
                last_sample_at INTEGER NOT NULL,
                last_reset_at INTEGER NOT NULL,
                started_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                enabled INTEGER NOT NULL
            );
CREATE TABLE network_policies (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                mac_mode TEXT NOT NULL,
                mac_allowlist_json TEXT NOT NULL,
                mac_denylist_json TEXT NOT NULL,
                website_mode TEXT NOT NULL,
                website_allowed_json TEXT NOT NULL,
                website_blocked_json TEXT NOT NULL
            , access_options_json TEXT NOT NULL DEFAULT '{}');
INSERT INTO network_policies VALUES(1,'disabled','[]','[]','unrestricted','[]','[]','{}');
CREATE TABLE experiment_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                persist_profiles_after_gateway_outage INTEGER NOT NULL DEFAULT 0
                    CHECK (persist_profiles_after_gateway_outage IN (0, 1))
            );
INSERT INTO experiment_settings VALUES(1,0);
CREATE TABLE dashboard_preferences (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                check_staged_updates_on_open INTEGER NOT NULL DEFAULT 1
                    CHECK (check_staged_updates_on_open IN (0, 1))
            );
INSERT INTO dashboard_preferences VALUES(1,1);
CREATE TABLE everyone_else_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                -- The API refuses to delete this profile. Older versions don't
                -- know about everyone else, so a delete there clears the choice.
                profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL
            );
INSERT INTO everyone_else_settings VALUES(1,'slow');
CREATE TABLE unaffected_devices (
                mac_address TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
INSERT INTO unaffected_devices VALUES('02:00:00:00:00:02','Tablet');
CREATE TABLE runtime_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                configuration_version INTEGER NOT NULL,
                observed_state TEXT NOT NULL,
                last_action TEXT,
                last_result TEXT,
                last_error TEXT
            );
INSERT INTO runtime_state VALUES(1,5,'unknown',NULL,NULL,NULL);
CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                happened_at INTEGER NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL
            , device_ids_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE action_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                happened_at INTEGER NOT NULL,
                action TEXT NOT NULL,
                status_code INTEGER NOT NULL
            );
CREATE TABLE domain_observations (
                ipv4_address TEXT NOT NULL,
                domain TEXT NOT NULL,
                query_count INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                PRIMARY KEY (ipv4_address, domain)
            );
CREATE TABLE domain_query_totals (
                ipv4_address TEXT PRIMARY KEY,
                query_count INTEGER NOT NULL
            );
CREATE TABLE schedules (
        id TEXT PRIMARY KEY,
        device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
        group_id TEXT REFERENCES device_groups(id) ON DELETE CASCADE,
        everyone_else INTEGER NOT NULL DEFAULT 0,
        profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        days_json TEXT NOT NULL,
        start_minute INTEGER NOT NULL,
        end_minute INTEGER NOT NULL,
        enabled INTEGER NOT NULL,
        CHECK ((device_id IS NOT NULL) + (group_id IS NOT NULL) + (everyone_else = 1) = 1)
    );
CREATE TABLE site_blocks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                domains_json TEXT NOT NULL,
                device_id TEXT REFERENCES devices(id) ON DELETE RESTRICT,
                group_id TEXT REFERENCES device_groups(id) ON DELETE RESTRICT,
                enabled INTEGER NOT NULL,
                CHECK (device_id IS NULL OR group_id IS NULL)
            );
CREATE UNIQUE INDEX one_active_test_session
                ON test_sessions ((1)) WHERE ended_at IS NULL;
CREATE INDEX session_event_order ON test_session_events (session_id, id);
CREATE INDEX domain_observations_last_seen_idx
                ON domain_observations (last_seen);
CREATE INDEX devices_mac_idx ON devices(mac_address);
CREATE INDEX timers_history_idx ON timers(enabled, started_at DESC, id DESC);
CREATE INDEX progressions_history_idx ON progressions(enabled, started_at DESC, id DESC);
CREATE INDEX scenario_runs_history_idx ON scenario_runs(enabled, started_at DESC, id DESC);
COMMIT;
