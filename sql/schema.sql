DROP TABLE IF EXISTS tiploc;
DROP TABLE IF EXISTS schedules;
DROP TABLE IF EXISTS schedule_locations;

CREATE TABLE tiploc (
    tiploc_code TEXT PRIMARY KEY,
    transaction_type TEXT,
    nalco TEXT,
    stanox TEXT,
    crs_code TEXT,
    description TEXT,
    tps_description TEXT
);

CREATE TABLE schedules (
    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cif_train_uid TEXT,
    transaction_type TEXT,
    schedule_start_date TEXT,
    schedule_end_date TEXT,
    schedule_days_runs TEXT,
    cif_bank_holiday_running TEXT,
    train_status TEXT,
    cif_stp_indicator TEXT,
    atoc_code TEXT
);

CREATE TABLE schedule_locations (
    schedule_id INTEGER,
    location_index INTEGER,
    tiploc_code TEXT,
    arrival TEXT,
    departure TEXT,
    pass TEXT,
    platform TEXT,
    line TEXT,
    path TEXT,
    PRIMARY KEY (schedule_id, location_index)
);