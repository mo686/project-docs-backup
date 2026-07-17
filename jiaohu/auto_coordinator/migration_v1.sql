-- 联动引擎 v1.0 数据库迁移
-- 执行时机：AutoCoordinatorApp 首次启动时自动检测并执行
-- 使用 CREATE TABLE IF NOT EXISTS 确保幂等

CREATE TABLE IF NOT EXISTS channel_mapping (
    id INTEGER PRIMARY KEY,
    ntc_channel INTEGER NOT NULL UNIQUE,
    voltage_channel INTEGER NOT NULL,
    tolerance REAL DEFAULT 0.05,
    compensation_enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS link_run_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    ntc_channel INTEGER NOT NULL,
    target_temp REAL,
    target_voltage REAL,
    actual_voltage REAL,
    deviation REAL,
    compensation REAL,
    slope_current REAL,
    is_limited INTEGER DEFAULT 0,
    event_type TEXT
);

CREATE TABLE IF NOT EXISTS link_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    event_type TEXT NOT NULL,
    ntc_channel INTEGER,
    description TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_link_state_ts ON link_run_state(timestamp);
CREATE INDEX IF NOT EXISTS idx_link_events_ts ON link_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_link_events_type ON link_events(event_type);
