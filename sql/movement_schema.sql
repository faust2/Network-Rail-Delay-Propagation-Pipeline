DROP TABLE IF EXISTS train_movements;

CREATE TABLE train_movements (
    movement_id INTEGER PRIMARY KEY AUTOINCREMENT,

    received_at_utc TEXT,

    msg_queue_timestamp_ms INTEGER,
    msg_queue_time_utc TEXT,
    msg_type TEXT,
    original_data_source TEXT,
    source_system_id TEXT,

    train_id TEXT,
    event_type TEXT,
    planned_event_type TEXT,
    event_source TEXT,

    loc_stanox TEXT,
    reporting_stanox TEXT,
    next_report_stanox TEXT,
    next_report_run_time INTEGER,

    planned_timestamp_ms INTEGER,
    actual_timestamp_ms INTEGER,
    gbtt_timestamp_ms INTEGER,

    planned_time_utc TEXT,
    actual_time_utc TEXT,
    gbtt_time_utc TEXT,

    timetable_variation INTEGER,
    variation_status TEXT,

    direction_ind TEXT,
    platform TEXT,
    route TEXT,

    train_service_code TEXT,
    division_code TEXT,
    toc_id TEXT,

    train_terminated TEXT,
    delay_monitoring_point TEXT,
    auto_expected TEXT,
    correction_ind TEXT,
    offroute_ind TEXT,

    raw_body_json TEXT
);

CREATE INDEX idx_tm_train_id
    ON train_movements (train_id);

CREATE INDEX idx_tm_event_type
    ON train_movements (event_type);

CREATE INDEX idx_tm_loc_stanox
    ON train_movements (loc_stanox);

CREATE INDEX idx_tm_actual_time
    ON train_movements (actual_time_utc);

CREATE INDEX idx_tm_planned_time
    ON train_movements (planned_time_utc);