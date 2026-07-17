import sys
import sqlite3
from pathlib import Path
from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QTabWidget, QToolBar, QAction,
    QStatusBar, QLabel, QMessageBox, QScrollArea
)
from PyQt5.QtCore import QTimer, pyqtSignal, Qt
from PyQt5.QtGui import QIcon

from auto_coordinator.engine import AutoCoordinator
from auto_coordinator.widgets.bind_tab import ChannelBindWidget

from auto_coordinator.widgets.dashboard_tab import StatusDashboard
from auto_coordinator.widgets.log_tab import LogPanel
from auto_coordinator.widgets.acquisition_tab import AcquisitionTab
from auto_coordinator.widgets.ntc_sim_tab import NTCSimTab


def run_migration(db_path: str):
    migration_sql = Path(__file__).parent / 'migration_v1.sql'
    if not migration_sql.exists():
        return
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='channel_mapping'")
        if cursor.fetchone():
            conn.close()
            return
        with open(migration_sql, 'r', encoding='utf-8') as f:
            cursor.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


class AutoCoordinatorApp(QMainWindow):
    state_updated = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("联动调度引擎 - AutoCoordinator")
        self.resize(900, 600)
        self.setMinimumSize(400, 300)

        BASE_DIR = Path(__file__).parent.parent
        self.db_path = str(BASE_DIR / 'voltage_data.db')

        run_migration(self.db_path)

        self.engine = AutoCoordinator()
        self.engine.on_state_changed = self.state_updated.emit
        self.state_updated.connect(self._on_state_snapshot)

        self._init_ui()
        self._connect_signals()

        self.alive_timer = QTimer(self)
        self.alive_timer.timeout.connect(self._check_alive)
        self.alive_timer.start(2000)

    def _init_ui(self):
        toolbar = QToolBar("主工具栏")
        self.addToolBar(toolbar)

        self.btn_pin = QAction("📌 置顶", self)
        self.btn_pin.setCheckable(True)
        self.btn_pin.triggered.connect(self._on_pin)
        toolbar.addAction(self.btn_pin)

        toolbar.addSeparator()

        self.btn_start = QAction("启动", self)
        self.btn_start.triggered.connect(self._on_start)
        toolbar.addAction(self.btn_start)

        self.btn_pause = QAction("暂停", self)
        self.btn_pause.triggered.connect(self._on_pause)
        self.btn_pause.setEnabled(False)
        toolbar.addAction(self.btn_pause)

        self.btn_stop = QAction("停止", self)
        self.btn_stop.triggered.connect(self._on_stop)
        self.btn_stop.setEnabled(False)
        toolbar.addAction(self.btn_stop)

        self.tabs = QTabWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.tabs)
        self.setCentralWidget(scroll)

        self.ntc_tab = NTCSimTab(curves=self.engine.data_reader.curves)
        self.tabs.addTab(self.ntc_tab, "NTC模拟")

        self.acq_tab = AcquisitionTab(db_path=self.db_path)
        self.tabs.addTab(self.acq_tab, "电压采集")

        self.bind_tab = ChannelBindWidget(self.engine.data_reader, self.db_path)
        self.tabs.addTab(self.bind_tab, "通道绑定")

        self.dashboard_tab = StatusDashboard(self.db_path)
        self.tabs.addTab(self.dashboard_tab, "状态看板")

        self.log_tab = LogPanel(self.db_path)
        self.tabs.addTab(self.log_tab, "事件日志")

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.lbl_ntc = QLabel("NTC: ○离线")
        self.lbl_v = QLabel("1.py: ○离线")
        self.lbl_engine = QLabel("引擎: 已停止")
        self.status_bar.addWidget(self.lbl_ntc)
        self.status_bar.addWidget(self.lbl_v)
        self.status_bar.addWidget(self.lbl_engine)

    def _connect_signals(self):
        pass

    def _on_pin(self, checked):
        if checked:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.show()

    def _on_start(self):
        alive = self.engine.data_reader.check_alive(self.engine.ALIVE_TIMEOUT)
        if not alive['ntc'] and not alive['voltage']:
            QMessageBox.warning(self, "启动失败",
                                "NTC.py 和 1.py 均离线，请先启动上位机")
            return
        if not alive['ntc']:
            QMessageBox.warning(self, "启动失败",
                                "NTC.py 离线，请先启动 NTC 上位机")
            return
        if not alive['voltage']:
            QMessageBox.warning(self, "启动失败",
                                "1.py 离线，请先启动电压采集上位机")
            return

        if not self.engine.data_reader.mapping:
            QMessageBox.warning(self, "启动失败",
                                "未配置通道绑定，请先在「通道绑定」页配置")
            return

        self.engine.start()
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.lbl_engine.setText("引擎: 运行中")

    def _on_pause(self):
        if self.engine.paused:
            self.engine.resume()
            self.btn_pause.setText("暂停")
            self.lbl_engine.setText("引擎: 运行中")
        else:
            self.engine.pause()
            self.btn_pause.setText("恢复")
            self.lbl_engine.setText("引擎: 已暂停")

    def _on_stop(self):
        self.engine.stop()
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暂停")
        self.btn_stop.setEnabled(False)
        self.lbl_engine.setText("引擎: 已停止")

    def _check_alive(self):
        alive = self.engine.data_reader.check_alive(self.engine.ALIVE_TIMEOUT)
        self.lbl_ntc.setText(f"NTC: {'●在线' if alive['ntc'] else '○离线'}")
        self.lbl_v.setText(f"1.py: {'●在线' if alive['voltage'] else '○离线'}")

    def _on_state_snapshot(self, state: dict):
        self.dashboard_tab.update_from_snapshot(state)

    def closeEvent(self, event):
        if self.engine.running:
            reply = QMessageBox.question(
                self, "确认退出",
                "引擎正在运行中，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self.engine.stop()
        self.ntc_tab.shutdown()
        event.accept()


def main():
    app = QApplication(sys.argv)

    qss_path = Path(__file__).parent / 'styles' / 'theme.qss'
    if qss_path.exists():
        with open(qss_path, 'r', encoding='utf-8') as f:
            app.setStyleSheet(f.read())

    window = AutoCoordinatorApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
