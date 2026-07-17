import sqlite3
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt


class StatusDashboard(QWidget):
    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        self.table = QTableWidget(6, 9)
        self.table.setHorizontalHeaderLabels([
            "通道名", "设温(°C)", "期望V", "实际V", "偏差V",
            "补偿V", "斜率", "限幅", "自动"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

    def update_from_snapshot(self, state: dict):
        self.table.blockSignals(True)
        for ntc_ch, data in state.items():
            row = ntc_ch - 1
            self._set_text(row, 0, data.get('channel_name', f'CH{ntc_ch}'))
            self._set_text(row, 1, f"{data.get('target_temp', 0):.1f}")
            self._set_text(row, 2, f"{data.get('target_voltage', 0):.3f}")
            self._set_text(row, 3, f"{data.get('actual_voltage', 0):.3f}")
            self._set_text(row, 4, f"{data.get('deviation', 0):.3f}")
            self._set_text(row, 5, f"{data.get('compensation', 0):.3f}")
            self._set_text(row, 6, f"{data.get('slope', 0):.2f}")
            self._set_text(row, 7, "限幅" if data.get('is_limited') else "")
            self._set_text(row, 8, "是" if data.get('auto_run') else "否")

            deviation = abs(data.get('deviation', 0))
            tolerance = data.get('tolerance', 0.05)
            if deviation <= tolerance:
                color = QColor(255, 255, 255)
            elif deviation <= 0.3:
                color = QColor(255, 255, 200)
            elif deviation <= 0.5:
                color = QColor(255, 200, 100)
            else:
                color = QColor(255, 150, 150)
            for col in range(9):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(color)
        self.table.blockSignals(False)

    def _set_text(self, row, col, text):
        item = self.table.item(row, col)
        if not item:
            item = QTableWidgetItem()
            self.table.setItem(row, col, item)
        item.setText(str(text))
        item.setTextAlignment(Qt.AlignCenter)
