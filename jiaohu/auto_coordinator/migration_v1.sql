-- 联动引擎 v2.0 数据库迁移 (open-loop threshold-triggered)
-- 执行时机：首次启动时自动检测并执行
-- 事件日志改为终端 print 输出，不再持久化到 DB

CREATE TABLE IF NOT EXISTS channel_mapping (
    id INTEGER PRIMARY KEY,
    ntc_channel INTEGER NOT NULL UNIQUE,
    voltage_channel INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS link_run_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    ntc_channel INTEGER NOT NULL,
    target_temp REAL,
    actual_voltage REAL,
    slope_current REAL,
    is_triggered INTEGER DEFAULT 0,
    event_type TEXT
);

CREATE INDEX IF NOT EXISTS idx_link_state_ts ON link_run_state(timestamp);
