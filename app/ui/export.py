"""User-selected portable export, always into a new directory."""

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.services.export_data import export_database
from app.ui.vehicles import ERRORS, plain_label


class ExportDialog(QDialog):
    def __init__(self, records, parent=None):
        super().__init__(parent)
        self.records = records
        self.setWindowTitle("Экспорт базы в TXT / Excel")
        self.resize(620, 300)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(
                "Все автомобили, клиенты, ремонты, ТО и звонки, включая удалённые записи. "
                "Рабочая база останется прежней."
            )
        )
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItem("TXT — читаемые записи и история по автомобилям", "txt")
        self.format.addItem("Excel — отдельные листы (.xlsx)", "xlsx")
        form.addRow("Формат", self.format)
        self.directory = QLineEdit()
        self.directory.setPlaceholderText("Выберите папку на компьютере или флешке")
        form.addRow("Куда сохранить", self.directory)
        choose = QPushButton("Выбрать папку…")
        choose.clicked.connect(self.choose)
        form.addRow(choose)
        self.files = QCheckBox("Включить оригиналы TXT и выданные PDF")
        self.files.setChecked(True)
        form.addRow(self.files)
        layout.addLayout(form)
        layout.addWidget(
            plain_label(
                "В выбранном месте появится новая папка AutoService-export с датой. "
                "Существующие файлы не перезаписываются.\n"
                "Для восстановления программы используйте «Создать резервную копию»."
            )
        )
        row = QHBoxLayout()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addStretch()
        self.export_button = QPushButton("Выгрузить")
        self.export_button.clicked.connect(self.export)
        row.addWidget(self.export_button)
        layout.addLayout(row)

    def choose(self):
        directory = QFileDialog.getExistingDirectory(self, "Куда сохранить выгрузку")
        if directory:
            self.directory.setText(directory)

    def export(self):
        if not self.directory.text().strip():
            QMessageBox.warning(self, "Выберите папку", "Укажите, куда сохранить выгрузку.")
            return
        parent = Path(self.directory.text().strip()).expanduser()
        destination = (
            parent / f"AutoService-export-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
        )
        self.export_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        error = None
        try:
            self.metadata = export_database(
                self.records.path, destination, self.format.currentData(), self.files.isChecked()
            )
            self.destination = destination
        except (*ERRORS, RuntimeError) as caught:
            error = caught
        finally:
            QApplication.restoreOverrideCursor()
            self.export_button.setEnabled(True)
        if error is not None:
            QMessageBox.warning(self, "Не удалось выгрузить данные", str(error))
            return
        QMessageBox.information(self, "Выгрузка готова", str(destination))
        self.accept()
