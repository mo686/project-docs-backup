import json
import sqlite3
from pathlib import Path
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QComboBox, QDoubleSpinBox, QCheckBox, QPushButton, QLabel, QHeaderView,
    QFileDialog, QMessageBox
)
from PyQt5.QtCore import Qt


class ChannelBindWidget(QWidget):
    def __init__(self, data_reader, db_path, parent=None):
        super().__init__(parent)
        self.data_reader = data_reader
        self.db_path = db_path
        self.init_ui()
        self.load_from_db()

    def init_ui(self):
        layout = QVBoxLayout(self)

        self.table = QTableWidget(6, 4)
        self.table.setHorizontalHeaderLabels(["NTC通道", "绑定1.py通道", "容差(V)", "自动补偿"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)

        for row in range(6):
            label = QLabel(f"CH{row + 1}")
            label.setAlignment(Qt.AlignCenter)
            self.table.setCellWidget(row, 0, label)

            combo = QComboBox()
            for ch in range(1, 19):
                combo.addItem(str(ch), ch)
            self.table.setCellWidget(row, 1, combo)

            spin_tol = QDoubleSpinBox()
            spin_tol.setRange(0.01, 1.0)
            spin_tol.setSingleStep(0.01)
            spin_tol.setDecimals(2)
            spin_tol.setValue(0.05)
            self.table.setCellWidget(row, 2, spin_tol)

            chk = QCheckBox()
            chk.setChecked(True)
            container = QWidget()
            chk_layout = QHBoxLayout(container)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 3, container)

        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_save = QPushButton("保存配置")
        btn_save.setObjectName("btn_success")
        btn_save.clicked.connect(self.save_to_db)
        btn_layout.addWidget(btn_save)

        btn_export = QPushButton("导出模板")
        btn_export.setObjectName("btn_info")
        btn_export.clicked.connect(self.export_template)
        btn_layout.addWidget(btn_export)

        btn_import = QPushButton("导入模板")
        btn_import.setObjectName("btn_info")
        btn_import.clicked.connect(self.import_template)
        btn_layout.addWidget(btn_import)

        layout.addLayout(btn_layout)

    def _get_combo_values(self) -> list:
        used = set()
        values = []
        for row in range(6):
            combo = self.table.cellWidget(row, 1)
            values.append(combo.currentData())
            used.add(combo.currentData())
        return values, used

    def load_from_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ntc_channel, voltage_channel, tolerance, compensation_enabled "
            "FROM channel_mapping ORDER BY ntc_channel"
        )
        rows = cursor.fetchall()
        conn.close()
        for row_db in rows:
            ntc_ch, v_ch, tol, comp = row_db
            row_idx = ntc_ch - 1
            combo = self.table.cellWidget(row_idx, 1)
            idx = combo.findData(v_ch)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            spin = self.table.cellWidget(row_idx, 2)
            spin.setValue(tol)
            container = self.table.cellWidget(row_idx, 3)
            chk = container.findChild(QCheckBox)
            if chk:
                chk.setChecked(bool(comp))

    def save_to_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        for row in range(6):
            ntc_ch = row + 1
            combo = self.table.cellWidget(row, 1)
            v_ch = combo.currentData()
            spin = self.table.cellWidget(row, 2)
            tol = spin.value()
            container = self.table.cellWidget(row, 3)
            chk = container.findChild(QCheckBox)
            comp = 1 if chk and chk.isChecked() else 0
            cursor.execute(
                "INSERT OR REPLACE INTO channel_mapping "
                "(ntc_channel, voltage_channel, tolerance, compensation_enabled) "
                "VALUES (?,?,?,?)",
                (ntc_ch, v_ch, tol, comp)
            )
        conn.commit()
        conn.close()
        self.data_reader.load_mapping()

    def export_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出通道绑定模板", "", "JSON文件 (*.json)")
        if not path:
            return
        data = {"channel_mapping": []}
        for row in range(6):
            ntc_ch = row + 1
            combo = self.table.cellWidget(row, 1)
            v_ch = combo.currentData()
            spin = self.table.cellWidget(row, 2)
            tol = spin.value()
            container = self.table.cellWidget(row, 3)
            chk = container.findChild(QCheckBox)
            comp = chk.isChecked() if chk else True
            data["channel_mapping"].append({
                "ntc_channel": ntc_ch,
                "voltage_channel": v_ch,
                "tolerance": tol,
                "compensation_enabled": comp,
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
            combo = self.table.cellWidget(row_idx, 1)
            idx = combo.findData(item["voltage_channel"])
            if idx >= 0:
                combo.setCurrentIndex(idx)
            self.table.cellWidget(row_idx, 2).setValue(item["tolerance"])
            container = self.table.cellWidget(row_idx, 3)
            chk = container.findChild(QCheckBox)
            if chk:
                chk.setChecked(item.get("compensation_enabled", True))
        self.save_to_db()
