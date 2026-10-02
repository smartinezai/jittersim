PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                upload_json TEXT NOT NULL,
                download_json TEXT NOT NULL,
                random_seed INTEGER,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );
INSERT INTO profiles VALUES('slow','Slow','{"bandwidth_kbit": null, "bandwidth_randomness": {"enabled_probability": 100, "variation_percent": 0}, "corrupt_percent": 0, "corrupt_randomness": {"enabled_probability": 100, "variation_percent": 0}, "delay_ms": 0, "delay_randomness": {"enabled_probability": 100, "variation_percent": 0}, "duplicate_percent": 0, "duplicate_randomness": {"enabled_probability": 100, "variation_percent": 0}, "jitter_ms": 0, "jitter_randomness": {"enabled_probability": 100, "variation_percent": 0}, "loss_percent": 0, "loss_randomness": {"enabled_probability": 100, "variation_percent": 0}, "reorder_percent": 0, "reorder_randomness": {"enabled_probability": 100, "variation_percent": 0}}','{"bandwidth_kbit": null, "bandwidth_randomness": {"enabled_probability": 100, "variation_percent": 0}, "corrupt_percent": 0, "corrupt_randomness": {"enabled_probability": 100, "variation_percent": 0}, "delay_ms": 0, "delay_randomness": {"enabled_probability": 100, "variation_percent": 0}, "duplicate_percent": 0, "duplicate_randomness": {"enabled_probability": 100, "variation_percent": 0}, "jitter_ms": 0, "jitter_randomness": {"enabled_probability": 100, "variation_percent": 0}, "loss_percent": 0, "loss_randomness": {"enabled_probability": 100, "variation_percent": 0}, "reorder_percent": 0, "reorder_randomness": {"enabled_probability": 100, "variation_percent": 0}}',NULL,'{"outage_duration_randomness": {"enabled_probability": 100, "variation_percent": 0}, "outage_duration_seconds": 0, "outage_interval_randomness": {"enabled_probability": 100, "variation_percent": 0}, "outage_interval_seconds": 0, "outage_probability": 0, "reroll_interval_randomness": {"enabled_probability": 100, "variation_percent": 0}, "reroll_interval_seconds": 0, "website_domains": [], "website_mode": "unrestricted", "website_randomness": {"enabled_probability": 100, "variation_percent": 0}}');
CREATE TABLE devices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ipv4_address TEXT NOT NULL UNIQUE,
                profile_id TEXT NOT NULL REFERENCES profiles(id),
                mac_address TEXT
            );
INSERT INTO devices VALUES('alice','Alice','192.168.50.10','slow','02:00:00:00:00:01');
CREATE TABLE general_profile_setting (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                profile_id TEXT REFERENCES profiles(id) ON DELETE RESTRICT
            );
INSERT INTO general_profile_setting VALUES(1,'slow');
CREATE TABLE unaffected_devices (
                mac_address TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at INTEGER NOT NULL CHECK (created_at >= 0)
            );
INSERT INTO unaffected_devices VALUES('02:00:00:00:00:02','Tablet',5);
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
CREATE TABLE schedules (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                days_json TEXT NOT NULL,
                start_minute INTEGER NOT NULL,
                end_minute INTEGER NOT NULL,
                enabled INTEGER NOT NULL
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
CREATE TABLE service_lists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                services_json TEXT NOT NULL,
                hostnames_json TEXT NOT NULL,
                enabled INTEGER NOT NULL
            );
INSERT INTO service_lists VALUES('streaming','Streaming','slow','["netflix"]','[]',1);
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
            );
INSERT INTO network_policies VALUES(1,'disabled','[]','[]','unrestricted','[]','[]');
CREATE TABLE runtime_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                configuration_version INTEGER NOT NULL,
                observed_state TEXT NOT NULL,
                last_action TEXT,
                last_result TEXT,
                last_error TEXT
            );
INSERT INTO runtime_state VALUES(1,5,'unknown',NULL,NULL,NULL);
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
                mac_address TEXT,
                PRIMARY KEY (ipv4_address, domain)
            );
CREATE TABLE random_website_analytics (
                event_day_utc INTEGER NOT NULL,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                domain TEXT NOT NULL,
                kept_count INTEGER NOT NULL CHECK (kept_count >= 0),
                removed_count INTEGER NOT NULL CHECK (removed_count >= 0),
                PRIMARY KEY (event_day_utc, device_id, domain),
                CHECK (kept_count + removed_count > 0)
            );
CREATE INDEX domain_observations_last_seen_idx
                ON domain_observations (last_seen);
CREATE INDEX random_website_analytics_device_day_idx
                ON random_website_analytics (device_id, event_day_utc);
CREATE INDEX random_website_analytics_event_day_idx
                ON random_website_analytics (event_day_utc);
CREATE INDEX devices_mac_idx ON devices(lower(mac_address));
COMMIT;
