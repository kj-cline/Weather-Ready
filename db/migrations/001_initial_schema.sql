CREATE TABLE IF NOT EXISTS operators (
    operator_id TEXT PRIMARY KEY,
    restaurant_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operator_locations (
    operator_id TEXT PRIMARY KEY,
    raw_address TEXT,
    canonical_address TEXT,
    lat DOUBLE,
    lon DOUBLE,
    city TEXT,
    state_code TEXT,
    timezone TEXT,
    census_geo_id TEXT,
    nws_grid_id TEXT,
    nws_zone_id TEXT,
    geocoder_source TEXT,
    geocode_confidence TEXT,
    derived_at TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS operator_service_profile (
    operator_id TEXT PRIMARY KEY,
    primary_service_window TEXT NOT NULL,
    active_service_windows TEXT NOT NULL,
    demand_mix_self_declared TEXT,
    indoor_seat_capacity INTEGER,
    patio_enabled BOOLEAN DEFAULT FALSE,
    patio_seat_capacity INTEGER,
    patio_season_mode TEXT,
    neighborhood_type_confirmed TEXT,
    setup_mode TEXT,
    onboarding_state TEXT,
    updated_at TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS operator_weekly_baselines (
    baseline_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_window TEXT NOT NULL,
    day_group TEXT NOT NULL,
    baseline_total_covers INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    effective_from DATE DEFAULT CURRENT_DATE,
    effective_to DATE,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS baseline_id_seq START 1;

ALTER TABLE operator_weekly_baselines ALTER COLUMN baseline_id SET DEFAULT nextval('baseline_id_seq');

CREATE TABLE IF NOT EXISTS system_connections (
    connection_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    system_type TEXT NOT NULL,
    system_name TEXT NOT NULL,
    connection_state TEXT NOT NULL,
    sync_mode TEXT,
    field_mapping_version TEXT,
    last_successful_sync_at TIMESTAMP,
    truth_priority_rank INTEGER,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS connection_id_seq START 1;
ALTER TABLE system_connections ALTER COLUMN connection_id SET DEFAULT nextval('connection_id_seq');

CREATE TABLE IF NOT EXISTS location_context_profile (
    operator_id TEXT PRIMARY KEY,
    neighborhood_archetype TEXT,
    commuter_intensity DOUBLE,
    residential_intensity DOUBLE,
    transit_relevance BOOLEAN DEFAULT FALSE,
    venue_relevance BOOLEAN DEFAULT FALSE,
    hotel_travel_relevance BOOLEAN DEFAULT FALSE,
    patio_sensitivity_hint DOUBLE,
    derived_at TIMESTAMP,
    provenance_blob TEXT,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS weather_baseline_profile (
    profile_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    month INTEGER NOT NULL,
    service_window TEXT NOT NULL,
    temp_normal_low DOUBLE,
    temp_normal_mid DOUBLE,
    temp_normal_high DOUBLE,
    precip_frequency DOUBLE,
    cloudiness_frequency DOUBLE,
    humidity_normal DOUBLE,
    wind_normal DOUBLE,
    source_version TEXT,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS weather_baseline_profile_seq START 1;
ALTER TABLE weather_baseline_profile ALTER COLUMN profile_id SET DEFAULT nextval('weather_baseline_profile_seq');

CREATE TABLE IF NOT EXISTS weather_pulls (
    weather_pull_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    forecast_for_date DATE NOT NULL,
    service_window TEXT,
    weather_feature_blob TEXT,
    raw_payload_ref TEXT,
    source_freshness TEXT,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS weather_pull_id_seq START 1;
ALTER TABLE weather_pulls ALTER COLUMN weather_pull_id SET DEFAULT nextval('weather_pull_id_seq');

CREATE TABLE IF NOT EXISTS external_signal_log (
    signal_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_class TEXT NOT NULL,
    dependency_group TEXT NOT NULL,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    service_window_overlap DOUBLE,
    spatial_relevance DOUBLE,
    operator_exposure DOUBLE,
    trust_level TEXT,
    direction TEXT,
    strength DOUBLE,
    recommended_role TEXT,
    details_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS service_state_log (
    state_log_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    service_state TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT,
    confidence TEXT,
    operator_confirmed BOOLEAN DEFAULT FALSE,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    corrected_at TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS service_state_log_seq START 1;
ALTER TABLE service_state_log ALTER COLUMN state_log_id SET DEFAULT nextval('service_state_log_seq');

CREATE TABLE IF NOT EXISTS prediction_runs (
    prediction_run_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    prediction_case TEXT NOT NULL,
    forecast_regime TEXT NOT NULL,
    horizon_mode TEXT NOT NULL,
    target_name TEXT NOT NULL,
    generator_version TEXT,
    context_packet_json TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS prediction_components (
    component_row_id BIGINT PRIMARY KEY,
    prediction_run_id TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_state TEXT NOT NULL,
    predicted_value INTEGER,
    is_operator_visible BOOLEAN DEFAULT FALSE,
    is_learning_eligible BOOLEAN DEFAULT FALSE,
    FOREIGN KEY(prediction_run_id) REFERENCES prediction_runs(prediction_run_id)
);

CREATE SEQUENCE IF NOT EXISTS prediction_component_seq START 1;
ALTER TABLE prediction_components ALTER COLUMN component_row_id SET DEFAULT nextval('prediction_component_seq');

CREATE TABLE IF NOT EXISTS published_forecast_state (
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    active_service_windows TEXT NOT NULL,
    target_name TEXT NOT NULL,
    forecast_expected INTEGER NOT NULL,
    forecast_low INTEGER NOT NULL,
    forecast_high INTEGER NOT NULL,
    confidence_tier TEXT NOT NULL,
    posture TEXT NOT NULL,
    service_state TEXT NOT NULL,
    service_state_reason TEXT,
    prediction_case TEXT NOT NULL,
    forecast_regime TEXT NOT NULL,
    horizon_mode TEXT NOT NULL,
    top_drivers_json TEXT,
    major_uncertainties_json TEXT,
    target_definition_confidence TEXT,
    realized_total_truth_quality TEXT,
    component_truth_quality TEXT,
    resolved_source_summary_json TEXT,
    source_prediction_run_id TEXT,
    publish_reason TEXT,
    publish_decision TEXT,
    last_published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, service_date, service_window),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id),
    FOREIGN KEY(source_prediction_run_id) REFERENCES prediction_runs(prediction_run_id)
);

CREATE TABLE IF NOT EXISTS working_forecast_state (
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    target_name TEXT NOT NULL,
    forecast_expected INTEGER NOT NULL,
    forecast_low INTEGER NOT NULL,
    forecast_high INTEGER NOT NULL,
    confidence_tier TEXT NOT NULL,
    posture TEXT NOT NULL,
    service_state TEXT NOT NULL,
    source_prediction_run_id TEXT,
    refresh_reason TEXT NOT NULL,
    refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, service_date, service_window),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id),
    FOREIGN KEY(source_prediction_run_id) REFERENCES prediction_runs(prediction_run_id)
);

CREATE TABLE IF NOT EXISTS forecast_publication_snapshots (
    snapshot_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    target_name TEXT NOT NULL,
    forecast_expected INTEGER NOT NULL,
    forecast_low INTEGER NOT NULL,
    forecast_high INTEGER NOT NULL,
    confidence_tier TEXT NOT NULL,
    posture TEXT NOT NULL,
    service_state TEXT NOT NULL,
    source_prediction_run_id TEXT,
    snapshot_reason TEXT NOT NULL,
    snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id),
    FOREIGN KEY(source_prediction_run_id) REFERENCES prediction_runs(prediction_run_id)
);

CREATE SEQUENCE IF NOT EXISTS forecast_publication_snapshot_seq START 1;
ALTER TABLE forecast_publication_snapshots ALTER COLUMN snapshot_id SET DEFAULT nextval('forecast_publication_snapshot_seq');

CREATE TABLE IF NOT EXISTS forecast_refresh_runs (
    refresh_run_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    refresh_reason TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    event_mode_active BOOLEAN DEFAULT FALSE,
    source_summary_json TEXT,
    status TEXT NOT NULL,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS operator_actuals (
    actual_row_id BIGINT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    realized_total_covers INTEGER NOT NULL,
    realized_reserved_covers INTEGER,
    realized_walk_in_covers INTEGER,
    realized_waitlist_converted_covers INTEGER,
    inside_covers INTEGER,
    outside_covers INTEGER,
    reservation_no_show_covers INTEGER,
    reservation_cancellation_covers INTEGER,
    service_state TEXT NOT NULL,
    entry_mode TEXT,
    note TEXT,
    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    corrected_at TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS actual_row_seq START 1;
ALTER TABLE operator_actuals ALTER COLUMN actual_row_id SET DEFAULT nextval('actual_row_seq');

CREATE TABLE IF NOT EXISTS prediction_evaluations (
    evaluation_id BIGINT PRIMARY KEY,
    prediction_run_id TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    service_date DATE NOT NULL,
    service_window TEXT NOT NULL,
    actual_total_covers INTEGER NOT NULL,
    forecast_expected INTEGER NOT NULL,
    forecast_low INTEGER NOT NULL,
    forecast_high INTEGER NOT NULL,
    error_abs INTEGER NOT NULL,
    error_pct DOUBLE,
    inside_interval BOOLEAN,
    directional_bucket_correct BOOLEAN,
    service_state_learning_eligibility TEXT,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(prediction_run_id) REFERENCES prediction_runs(prediction_run_id),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE SEQUENCE IF NOT EXISTS evaluation_seq START 1;
ALTER TABLE prediction_evaluations ALTER COLUMN evaluation_id SET DEFAULT nextval('evaluation_seq');

CREATE TABLE IF NOT EXISTS baseline_learning_state (
    operator_id TEXT NOT NULL,
    service_window TEXT NOT NULL,
    day_group TEXT NOT NULL,
    baseline_mid DOUBLE,
    baseline_variability DOUBLE,
    history_depth INTEGER,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, service_window, day_group),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS weather_sensitivity_state (
    operator_id TEXT NOT NULL,
    service_window TEXT NOT NULL,
    weather_factor TEXT NOT NULL,
    sensitivity_mid DOUBLE,
    confidence TEXT,
    sample_size INTEGER,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, service_window, weather_factor),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS context_effect_state (
    operator_id TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    dependency_group TEXT NOT NULL,
    estimated_effect DOUBLE,
    confidence TEXT,
    sample_size INTEGER,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, signal_type, dependency_group),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS source_reliability_state (
    operator_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    historical_usefulness_score DOUBLE,
    staleness_penalty DOUBLE,
    trust_class TEXT,
    status TEXT,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, source_name, signal_type),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS component_learning_state (
    operator_id TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_state TEXT NOT NULL,
    semantic_clarity_score DOUBLE,
    reconciliation_quality_score DOUBLE,
    observation_count INTEGER,
    history_depth_days INTEGER,
    eligible_for_learning BOOLEAN DEFAULT FALSE,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operator_id, component_name),
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE TABLE IF NOT EXISTS operator_behavior_state (
    operator_id TEXT PRIMARY KEY,
    staffing_risk_bias DOUBLE,
    notification_sensitivity DOUBLE,
    preferred_explanation_style TEXT,
    clarification_tolerance DOUBLE,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(operator_id) REFERENCES operators(operator_id)
);

CREATE INDEX IF NOT EXISTS idx_prediction_runs_operator_date
    ON prediction_runs(operator_id, service_date, service_window);

CREATE INDEX IF NOT EXISTS idx_operator_actuals_operator_date
    ON operator_actuals(operator_id, service_date, service_window);

CREATE INDEX IF NOT EXISTS idx_external_signal_operator_time
    ON external_signal_log(operator_id, start_time, end_time);
