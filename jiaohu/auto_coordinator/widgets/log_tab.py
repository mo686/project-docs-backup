import sqlite3
import datetime
import pandas as pd
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QComboBox, QDateTimeEdit, QPushButton, QHeaderView, QFileDialog, QMessageBox
)
from PyQt5.QtCore import QTimer


class LogPanel(QWidget):
    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.init_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)

    def init_ui(self):
        layout = QVBoxLayout(self)

        filter_layout = QHBoxLayout()

        self.combo_type = QComboBox()
        self.combo_type.addItems(["全部", "COMPENSATION", "SLOPE_ADJ",
                                   "SLOPE_ADJ_ESCALATE", "HW_FAULT", "AUTO_STOP"])
        filter_layout.addWidget(self.combo_type)

        self.combo_ch = QComboBox()
        self.combo_ch.addItems(["全部通道"] + [f"CH{i}" for i in range(1, 7)])
        filter_layout.addWidget(self.combo_ch)

        self.dt_start = QDateTimeEdit()
        self.dt_start.setCalendarPopup(True)
        self.dt_start.setDateTime(datetime.datetime.now().replace(
            hour=0, minute=0, second=0))
        filter_layout.addWidget(self.dt_start)

        self.dt_end = QDateTimeEdit()
        self.dt_end.setCalendarPopup(True)
        self.dt_end.setDateTime(datetime.datetime.now())
        filter_layout.addWidget(self.dt_end)

        btn_query = QPushButton("查询")
        btn_query.setObjectName("btn_info")
        btn_query.clicked.connect(self.refresh)
        filter_layout.addWidget(btn_query)

        btn_export = QPushButton("导出Excel")
        btn_export.setObjectName("btn_info")
        btn_export.clicked.connect(self.export_excel)
        filter_layout.addWidget(btn_export)

        btn_clear = QPushButton("清除日志")
        btn_clear.setObjectName("btn_warning")
        btn_clear.clicked.connect(self.clear_logs)
        filter_layout.addWidget(btn_clear)

        layout.addLayout(filter_layout)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["时间", "事件类型", "通道", "描述"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

    def refresh(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        sql = "SELECT timestamp, event_type, ntc_channel, description FROM link_events WHERE 1=1"
        params = []

        etype = self.combo_type.currentText()
        if etype != "全部":
            sql += " AND event_type = ?"
            params.append(etype)

        ch_text = self.combo_ch.currentText()
        if ch_text != "全部通道":
            ch_num = int(ch_text.replace("CH", ""))
            sql += " AND ntc_channel = ?"
            params.append(ch_num)

        dt_start = self.dt_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        dt_end = self.dt_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        sql += " AND timestamp BETWEEN ? AND ?"
        params.extend([dt_start, dt_end])

        sql += " ORDER BY id DESC LIMIT 500"
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()

        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                ch_label = f"CH{val}" if j == 2 and val is not None else (val if val else "")
                self.table.setItem(i, j, QTableWidgetItem(str(ch_label)))

    def export_excel(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出事件日志", "events.xlsx", "Excel文件 (*.xlsx)")
        if not path:
            return
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query(
            "SELECT timestamp, event_type, ntc_channel, description, detail "
            "FROM link_events ORDER BY id DESC",
            conn
        )
        conn.close()
        df.to_excel(path, index=False)

    def clear_logs(self):
        reply = QMessageBox.question(
            self, "确认", "确定要清除所有事件日志吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM link_events")
            conn.commit()
            conn.close()
            self.refresh()
