# -*- coding: utf-8 -*-
"""
项目环境一键配置脚本。
在新电脑上只需运行此脚本即可完成所有依赖安装和初始化配置，
之后可直接运行 `python -m auto_coordinator.app` 启动项目。
"""
import json
import subprocess
import sys
from pathlib import Path

# ---------- 基础要求 ----------
MIN_PYTHON = (3, 8)

# ---------- 依赖包清单 ----------
REQUIRED_PACKAGES = [
    "PyQt5",
    "pyqtgraph",
    "numpy",
    "pyserial",
    "pymodbus",
    "minimalmodbus",
    "pandas",
    "openpyxl",
]

# ---------- 路径 ----------
BASE_DIR = Path(__file__).parent
CONFIG_ROOT = BASE_DIR / "config.json"
CONFIG_ENGINE = BASE_DIR / "auto_coordinator" / "config.json"
NTC_JSON_ROOT = BASE_DIR / "NTC.json"
NTC_JSON_ENGINE = BASE_DIR / "auto_coordinator" / "NTC.json"
NTC_XLSX_ROOT = BASE_DIR / "NTC.xlsx"
NTC_XLSX_ENGINE = BASE_DIR / "auto_coordinator" / "NTC.xlsx"
DB_FILE = BASE_DIR / "voltage_data.db"
MIGRATION_SQL = BASE_DIR / "auto_coordinator" / "migration_v1.sql"
TESTS_DIR = BASE_DIR / "tests"

PYTHON = sys.executable


def check_python():
    v = sys.version_info[:2]
    if v < MIN_PYTHON:
        print(f"[错误] Python 版本过低: {v[0]}.{v[1]}，需要 >= "
              f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
        return False
    print(f"[OK] Python {v[0]}.{v[1]}")
    return True


def install_packages():
    print("\n--- 安装依赖包 ---")
    failed = []
    for pkg in REQUIRED_PACKAGES:
        try:
            subprocess.check_call(
                [PYTHON, "-m", "pip", "install", pkg],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"  [OK] {pkg}")
        except subprocess.CalledProcessError:
            print(f"  [FAIL] {pkg}")
            failed.append(pkg)
    if failed:
        print(f"\n[警告] 以下包安装失败: {', '.join(failed)}")
        print("请确认网络连接正常后重试，或手动安装。")
        return False
    return True


def create_config_json():
    """创建根目录 config.json（电压采集配置）"""
    if CONFIG_ROOT.exists():
        print(f"  [跳过] {CONFIG_ROOT.name} 已存在")
        return
    default = {
        "thresholds": [5.0] * 18,
        "channel_names": [f"通道{i+1}" for i in range(18)],
        "event_display_mode": 0,
        "plot_mode": 0,
    }
    CONFIG_ROOT.write_text(
        json.dumps(default, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  [创建] {CONFIG_ROOT.name}")


def create_engine_config_json():
    """创建 engine config.json（引擎运行时参数）"""
    if CONFIG_ENGINE.exists():
        print(f"  [跳过] {CONFIG_ENGINE.name} (engine) 已存在")
        return
    default = {
        "control_interval_ms": 100,
        "max_deviation_hw_v": 0.5,
        "compensation_damping": 0.5,
        "compensation_max_per_cycle_v": 0.2,
        "compensation_retry_max": 10,
        "slope_reduce_default": 0.5,
        "slope_restore_cycles": 5,
        "alive_timeout_s": 10.0,
        "state_retention_days": 7,
        "log_level": "INFO",
    }
    CONFIG_ENGINE.write_text(
        json.dumps(default, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  [创建] auto_coordinator/{CONFIG_ENGINE.name}")


def create_ntc_json():
    """创建 NTC.json（NTC 模拟器状态）"""
    for path in [NTC_JSON_ROOT, NTC_JSON_ENGINE]:
        if path.exists():
            print(f"  [跳过] {path.parent.name}/{path.name} 已存在")
            continue
        default = {
            "port": "COM1",
            "addr": 1,
            "baud": 9600,
            "ntc_temps": [25.0] * 6,
            "ntc_curves": ["10k"] * 6,
            "limit_temp_high": [100] * 6,
            "limit_temp_low": [-50] * 6,
            "slope": [5.0] * 6,
            "auto_run": [False] * 6,
            "ch_names": [f"CH{i}" for i in range(1, 7)],
            "pullup_resistors": [10.0] * 6,
            "last_loaded_excel": "",
        }
        path.write_text(
            json.dumps(default, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  [创建] {path.parent.name}/{path.name}")


def create_ntc_xlsx():
    """创建 NTC.xlsx（NTC 曲线数据）"""
    try:
        import pandas as pd
    except ImportError:
        print("  [跳过] NTC.xlsx — pandas 未安装")
        return

    for path in [NTC_XLSX_ROOT, NTC_XLSX_ENGINE]:
        if path.exists():
            print(f"  [跳过] {path.parent.name}/{path.name} 已存在")
            continue
        default_data = {
            "Temp1": list(range(-50, 151)),
            "R1": [361.8 - i * 0.5 for i in range(201)],
        }
        df = pd.DataFrame(default_data)
        df.to_excel(str(path), index=False, sheet_name="10k")
        print(f"  [创建] {path.parent.name}/{path.name}")


def init_database():
    """运行数据库迁移脚本，创建表结构"""
    import sqlite3
    if not MIGRATION_SQL.exists():
        print("  [警告] migration_v1.sql 不存在，跳过数据库初始化")
        return

    # 如果 DB 已存在且有 channel_mapping 表，则跳过
    if DB_FILE.exists():
        conn = sqlite3.connect(str(DB_FILE))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='channel_mapping'"
        )
        if cursor.fetchone():
            conn.close()
            print(f"  [跳过] {DB_FILE.name} 已初始化")
            return
        conn.close()

    conn = sqlite3.connect(str(DB_FILE))
    try:
        cursor = conn.cursor()
        with open(MIGRATION_SQL, "r", encoding="utf-8") as f:
            cursor.executescript(f.read())
        conn.commit()
        print(f"  [创建] {DB_FILE.name} (表结构已创建)")
    except Exception as e:
        print(f"  [FAIL] 数据库初始化失败: {e}")
    finally:
        conn.close()


def create_config_files():
    print("\n--- 创建配置文件 ---")
    create_config_json()
    create_engine_config_json()
    create_ntc_json()
    create_ntc_xlsx()
    init_database()


def run_tests():
    print("\n--- 运行测试 ---")
    if not TESTS_DIR.exists():
        print("  [跳过] tests 目录不存在")
        return True
    result = subprocess.run(
        [PYTHON, "-m", "pytest", str(TESTS_DIR), "-v"],
        capture_output=False,
        cwd=str(BASE_DIR),
    )
    if result.returncode == 0:
        print("\n[OK] 所有测试通过！")
        return True
    else:
        print("\n[FAIL] 部分测试未通过，但基本功能可能正常。")
        return False


def main():
    print("=" * 50)
    print("  联动调度引擎 AutoCoordinator — 环境配置")
    print("=" * 50)

    if not check_python():
        sys.exit(1)

    if not install_packages():
        print("\n可手动执行: pip install " + " ".join(REQUIRED_PACKAGES))
        sys.exit(1)

    create_config_files()

    run_tests()

    print("\n" + "=" * 50)
    print("配置完成！启动项目:")
    print(f"  cd {BASE_DIR}")
    print("  python -m auto_coordinator.app")
    print("或双击 run_coordinator.bat")
    print("=" * 50)


if __name__ == "__main__":
    main()
