import json
import sqlite3
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QComboBox, QPushButton, QLabel, QHeaderView,
    QFileDialog, QMessageBox, QGroupBox
)
from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt


CHANNEL_COLORS = [
    QColor(173, 216, 230),
    QColor(255, 200, 150),
    QColor(180, 255, 180),
    QColor(255, 255, 160),
    QColor(200, 170, 255),
    QColor(255, 192, 203),
]


class SummaryPanel(QWidget):
    def __init__(self, data_reader, db_path, parent=None):
        super().__init__(parent)
        self.data_reader = data_reader
        self.db_path = db_path
        self.init_ui()
        self.load_bind_from_db()

    def init_ui(self):
        layout = QVBoxLayout(self)

        bind_group = QGroupBox("通道绑定配置")
        bind_layout = QVBoxLayout(bind_group)

        self.bind_table = QTableWidget(6, 2)
        self.bind_table.setHorizontalHeaderLabels([
            "NTC通道", "绑定电压采集通道"
        ])
        self.bind_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.bind_table.verticalHeader().setVisible(False)
        self.bind_table.setMaximumHeight(300)

        self._ntc_labels = []
        for row in range(6):
            label = QLabel(f"CH{row + 1}")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                f"background-color: {CHANNEL_COLORS[row].name()};"
            )
            self._ntc_labels.append(label)
            self.bind_table.setCellWidget(row, 0, label)

            combo = QComboBox()
            for ch in range(1, 19):
                combo.addItem(str(ch), ch)
            self.bind_table.setCellWidget(row, 1, combo)

        bind_layout.addWidget(self.bind_table)

        btn_layout = QHBoxLayout()
        btn_save = QPushButton("保存配置")
        btn_save.setObjectName("btn_success")
        btn_save.clicked.connect(self.save_bind)
        btn_layout.addWidget(btn_save)

        btn_export = QPushButton("导出模板")
        btn_export.setObjectName("btn_info")
        btn_export.clicked.connect(self.export_template)
        btn_layout.addWidget(btn_export)

        btn_import = QPushButton("导入模板")
        btn_import.setObjectName("btn_info")
        btn_import.clicked.connect(self.import_template)
        btn_layout.addWidget(btn_import)

        bind_layout.addLayout(btn_layout)
        layout.addWidget(bind_group)

        dash_group = QGroupBox("实时状态")
        dash_layout = QVBoxLayout(dash_group)

        self.dash_table = QTableWidget(6, 7)
        self.dash_table.setHorizontalHeaderLabels([
            "通道名", "设温(°C)", "实际V", "阈值V",
            "斜率", "触发", "自动"
        ])
        self.dash_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.dash_table.verticalHeader().setVisible(False)
        self.dash_table.setEditTriggers(QTableWidget.NoEditTriggers)
        dash_layout.addWidget(self.dash_table)
        layout.addWidget(dash_group)

    def update_dashboard(self, state: dict):
        self.dash_table.blockSignals(True)
        for ntc_ch, data in state.items():
            row = ntc_ch - 1
            self._set_text(row, 0, data.get('channel_name', f'CH{ntc_ch}'))
            self._set_text(row, 1, f"{data.get('target_temp', 0):.1f}")
            self._set_text(row, 2, f"{data.get('actual_voltage', 0):.3f}")
            self._set_text(row, 3, f"{data.get('threshold', 0):.3f}")
            self._set_text(row, 4, f"{data.get('slope', 0):.2f}")
            self._set_text(row, 5, "触发" if data.get('is_ramping') else "")
            self._set_text(row, 6, "是" if data.get('auto_run') else "否")

            is_over = data.get('is_over', False)
            is_ramping = data.get('is_ramping', False)
            if is_over and is_ramping:
                color = QColor(255, 150, 150)
            elif is_over:
                color = QColor(255, 255, 200)
            elif is_ramping:
                color = QColor(200, 255, 200)
            else:
                color = QColor(255, 255, 255)
            for col in range(7):
                item = self.dash_table.item(row, col)
                if item:
                    item.setBackground(color)
        self.dash_table.blockSignals(False)

    def _set_text(self, row, col, text):
        item = self.dash_table.item(row, col)
        if not item:
            item = QTableWidgetItem()
            self.dash_table.setItem(row, col, item)
        item.setText(str(text))
        item.setTextAlignment(Qt.AlignCenter)

    def refresh_bind_labels(self):
        ntc_state = self.data_reader.read_ntc_state()
        if ntc_state is None:
            return
        ch_names = ntc_state.get('ch_names', [f'CH{i}' for i in range(1, 7)])
        for row, name in enumerate(ch_names):
            self._ntc_labels[row].setText(name)

    def load_bind_from_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ntc_channel, voltage_channel "
            "FROM channel_mapping ORDER BY ntc_channel"
        )
        rows = cursor.fetchall()
        conn.close()
        for row_db in rows:
            ntc_ch, v_ch = row_db
            row_idx = ntc_ch - 1
            combo = self.bind_table.cellWidget(row_idx, 1)
            idx = combo.findData(v_ch)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self.refresh_bind_labels()

    def save_bind(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        for row in range(6):
            ntc_ch = row + 1
            combo = self.bind_table.cellWidget(row, 1)
            v_ch = combo.currentData()
            cursor.execute(
                "INSERT OR REPLACE INTO channel_mapping "
                "(ntc_channel, voltage_channel) VALUES (?,?)",
                (ntc_ch, v_ch)
            )
        conn.commit()
        conn.close()
        self.data_reader.load_mapping()
        self.refresh_bind_labels()

    def export_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出通道绑定模板", "", "JSON文件 (*.json)")
        if not path:
            return
        data = {"channel_mapping": []}
        for row in range(6):
            ntc_ch = row + 1
            combo = self.bind_table.cellWidget(row, 1)
            v_ch = combo.currentData()
            data["channel_mapping"].append({
                "ntc_channel": ntc_ch,
                "voltage_channel": v_ch,
            })
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def import_template(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入通道绑定模板", "", "JSON文件 (*.json)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        for item in data.get("channel_mapping", []):
            ntc_ch = item["ntc_channel"]
            row_idx = ntc_ch - 1
            combo = self.bind_table.cellWidget(row_idx, 1)
            idx = combo.findData(item["voltage_channel"])
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self.save_bind()
