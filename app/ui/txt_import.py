"""Проверка TXT перед записью и просмотр неизменённого источника после импорта."""

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.maintenance import optional_integer
from app.services.records import amount_text, parse_amount
from app.services.txt_import import TxtImport
from app.services.txt_parser import parse_date, split_fragment
from app.ui.vehicles import ERRORS, plain_label


def report_text(report):
    imported, skipped = report.get("imported", []), report.get("skipped", [])
    lines = [f"Добавлено записей: {len(imported)}", f"Уже импортировано: {len(skipped)}"]
    if report.get("backup"):
        lines.append(f"Копия базы перед импортом: {report['backup']}")
    lines.extend(f"Добавлено: {row['name']}" for row in imported)
    lines.extend(f"Пропущен повтор: {row['name']}" for row in skipped)
    lines.extend(f"Не прочитано: {row}" for row in report.get("errors", []))
    return "\n".join(lines)


class SourceDialog(QDialog):
    def __init__(self, path, repair_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Исходный TXT")
        self.resize(850, 650)
        self.source = TxtImport(path).provenance(repair_id)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(f"{self.source['original_name']} · {self.source['encoding']}"))
        tabs = QTabWidget()
        for name, text in (
            ("Фрагмент записи", self.source["fragment_text"]),
            ("Весь файл", self.source["decoded_text"]),
        ):
            viewer = QPlainTextEdit(text)
            viewer.setReadOnly(True)
            tabs.addTab(viewer, name)
        layout.addWidget(tabs, 1)
        row = QHBoxLayout()
        export = QPushButton("Сохранить копию исходного файла")
        export.clicked.connect(self.export)
        row.addWidget(export)
        row.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить копию исходника",
            Path(self.source["original_name"]).name,
            "TXT (*.txt)",
        )
        if not filename:
            return
        try:
            with Path(filename).open("xb") as file:
                file.write(self.source["original_bytes"])
        except OSError as error:
            QMessageBox.warning(self, "Не удалось сохранить копию", str(error))


class ImportDialog(QDialog):
    def __init__(self, records, parent=None):
        super().__init__(parent)
        self.records, self.service = records, TxtImport(records.path)
        self.sources, self.entries, self.errors = [], [], []
        self.current = None
        self.busy = False
        self.edited = False
        self.setWindowTitle("Импорт старых TXT")
        self.resize(1250, 820)
        self.setMinimumSize(950, 620)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        folder = QPushButton("Выбрать папку TXT")
        folder.clicked.connect(self.choose_folder)
        top.addWidget(folder)
        files = QPushButton("Добавить файлы")
        files.clicked.connect(self.choose_files)
        top.addWidget(files)
        self.encoding = QComboBox()
        for title, value in (
            ("Определить кодировку", "auto"),
            ("UTF-8", "utf-8-sig"),
            ("Windows-1251", "cp1251"),
            ("DOS-866", "cp866"),
            ("KOI8-R", "koi8-r"),
            ("UTF-16", "utf-16"),
            ("UTF-16 LE", "utf-16-le"),
            ("UTF-16 BE", "utf-16-be"),
        ):
            self.encoding.addItem(title, value)
        top.addWidget(self.encoding)
        reload = QPushButton("Перечитать кодировку")
        reload.clicked.connect(self.reload_source)
        top.addWidget(reload)
        layout.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("Файл / визит", "Проверка"))
        self.tree.setColumnWidth(0, 230)
        self.tree.currentItemChanged.connect(self.select_entry)
        split.addWidget(self.tree)
        self.tabs = QTabWidget()
        review = QWidget()
        right = QVBoxLayout(review)
        self.target = QComboBox()
        self.target.currentIndexChanged.connect(self.changed)
        right.addWidget(self.target)
        right.addWidget(
            plain_label("Поля машины ниже используются только при создании новой карточки.")
        )
        grid = QGridLayout()
        self.fields = {}
        for index, (key, label) in enumerate(
            (
                ("make", "Марка"),
                ("model", "Модель"),
                ("plate", "Госномер"),
                ("vin", "VIN"),
                ("contact_name", "Контакт"),
                ("phone", "Телефон"),
                ("visit_date", "Дата визита"),
                ("km", "Пробег, км"),
                ("amount", "Стоимость, ₽"),
            )
        ):
            widget = QLineEdit()
            widget.setAccessibleName(label)
            widget.setPlaceholderText(
                "Неизвестно" if key in ("visit_date", "km", "amount") else label
            )
            widget.textChanged.connect(self.changed)
            self.fields[key] = widget
            row, column = divmod(index, 2)
            grid.addWidget(plain_label(label), row, column * 2)
            grid.addWidget(widget, row, column * 2 + 1)
        self.fields["visit_date"].setPlaceholderText("дд.мм.гггг или пусто")
        self.completed = QComboBox()
        for title, value in (
            ("Состояние не уточнено", None),
            ("Завершён, в истории", True),
            ("Текущий", False),
        ):
            self.completed.addItem(title, value)
        self.completed.currentIndexChanged.connect(self.changed)
        grid.addWidget(self.completed, 4, 2, 1, 2)
        right.addLayout(grid)
        flags = QHBoxLayout()
        self.verified = QCheckBox("Пробег проверен")
        self.verified.toggled.connect(self.changed)
        flags.addWidget(self.verified)
        self.allow_duplicate = QCheckBox("Это отдельная запись при совпадении данных")
        self.allow_duplicate.toggled.connect(self.changed)
        flags.addWidget(self.allow_duplicate)
        right.addLayout(flags)
        right.addWidget(plain_label("Описание ремонта (исходник останется отдельно)"))
        self.description = QPlainTextEdit()
        self.description.textChanged.connect(self.changed)
        right.addWidget(self.description, 1)
        self.warnings = QPlainTextEdit()
        self.warnings.setReadOnly(True)
        self.warnings.setMaximumHeight(100)
        right.addWidget(self.warnings)
        confirm_row = QHBoxLayout()
        self.confirm_button = QPushButton("Подтвердить эту запись")
        self.confirm_button.clicked.connect(self.confirm_current)
        confirm_row.addWidget(self.confirm_button)
        separate = QPushButton("Разделить по строке…")
        separate.clicked.connect(self.split_current)
        confirm_row.addWidget(separate)
        right.addLayout(confirm_row)
        self.tabs.addTab(review, "Проверка записи")
        self.original = QPlainTextEdit()
        self.original.setReadOnly(True)
        self.tabs.addTab(self.original, "Исходный фрагмент со строками")
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.tabs.addTab(self.report, "Отчёты импорта")
        self.load_history()
        split.addWidget(self.tabs)
        split.setSizes([340, 910])
        layout.addWidget(split, 1)
        self.summary = plain_label(
            "Выберите папку или файлы. До подтверждения импорт не выполняется."
        )
        layout.addWidget(self.summary)
        layout.addWidget(
            plain_label(
                "Перед импортом создаётся копия базы. "
                "Исходные TXT не меняются и сохраняются внутри базы. "
                "Для независимого архива можно заранее скопировать папку на другой носитель."
            )
        )
        bottom = QHBoxLayout()
        ready = QPushButton("Подтвердить готовые записи")
        ready.clicked.connect(self.confirm_ready)
        bottom.addWidget(ready)
        bottom.addStretch()
        self.import_button = QPushButton("Импортировать подтверждённые")
        self.import_button.clicked.connect(self.commit)
        bottom.addWidget(self.import_button)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.refresh_summary()

    def choose_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Папка с исходными TXT")
        if directory:
            root = Path(directory)
            self.add_paths(
                sorted(
                    path
                    for path in root.rglob("*")
                    if path.is_file() and path.suffix.casefold() == ".txt"
                ),
                root,
            )

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Выбрать исходные TXT", "", "TXT (*.txt)")
        if paths:
            self.add_paths([Path(path) for path in paths])

    def add_paths(self, paths, root=None):
        self.store_current()
        known = {source.path for source in self.sources}
        progress = QProgressDialog(
            "Чтение TXT для предварительного просмотра…", "Остановить чтение", 0, len(paths), self
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        for index, path in enumerate(paths):
            progress.setValue(index)
            if progress.wasCanceled():
                break
            if Path(path).resolve() in known:
                continue
            try:
                source = self.service.preview(path, root, self.encoding.currentData())
                self.sources.append(source)
                known.add(source.path)
                self.entries.extend(
                    self.new_entry(source, fragment) for fragment in source.fragments
                )
            except (*ERRORS, UnicodeError) as error:
                self.errors.append(f"{path}: {error}")
        progress.close()
        self.current = None
        self.rebuild_tree()
        self.load_history()

    @staticmethod
    def new_entry(source, fragment):
        values = deepcopy(fragment.values)
        return {
            "source": source,
            "fragment": fragment,
            "values": values,
            "target": source.bound_vehicle_id or f"file:{source.digest}",
            "confirmed": False,
            "allow_duplicate": False,
            "draft": {
                key: str(values.get(key) or "")
                for key in ("make", "model", "plate", "vin", "contact_name", "phone")
            }
            | {
                "visit_date": values["visit_date"] or "",
                "km": str(values["km"]) if values["km"] is not None else "",
                "amount": amount_text(values["amount_kopecks"]),
                "description": values["description"],
                "completed": values["completed"],
                "verified": False,
            },
        }

    def rebuild_tree(self):
        self.busy = True
        self.tree.clear()
        grouped = {}
        for index, entry in enumerate(self.entries):
            grouped.setdefault(id(entry["source"]), []).append((index, entry))
        for source in self.sources:
            parent = QTreeWidgetItem((source.relative_name, source.encoding))
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(parent)
            for index, entry in grouped.get(id(source), []):
                title = entry["draft"]["visit_date"] or "Дата неизвестна"
                state = (
                    "Импортировано"
                    if entry["fragment"].imported
                    else "Подтверждено"
                    if entry["confirmed"]
                    else "Проверить"
                )
                item = QTreeWidgetItem((title, state))
                item.setData(0, Qt.ItemDataRole.UserRole, index)
                parent.addChild(item)
                entry["item"] = item
            parent.setExpanded(True)
        self.busy = False
        if self.entries:
            self.tree.setCurrentItem(self.entries[0]["item"])
            self.select_entry(self.entries[0]["item"], None)
        self.refresh_summary()

    def store_current(self):
        if self.current is None or self.busy:
            return
        self.current["draft"] = {key: field.text() for key, field in self.fields.items()} | {
            "description": self.description.toPlainText(),
            "completed": self.completed.currentData(),
            "verified": self.verified.isChecked(),
        }
        self.current["target"] = self.target.currentData()
        self.current["allow_duplicate"] = self.allow_duplicate.isChecked()

    def select_entry(self, item, previous):
        if self.busy or item is None:
            return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is None:
            return
        self.store_current()
        self.busy = True
        self.current = self.entries[index]
        entry = self.current
        self.target.clear()
        self.target.addItem("Новая машина для этого файла", f"file:{entry['source'].digest}")
        self.target.addItem(
            "Отдельная новая машина для этой записи",
            f"fragment:{entry['source'].digest}:{entry['fragment'].start}",
        )
        for car in self.records.vehicles():
            self.target.addItem(
                f"{car['plate'] or 'без номера'} · {car['make']} {car['model']}", car["id"]
            )
        self.target.setCurrentIndex(max(0, self.target.findData(entry["target"])))
        for key, field in self.fields.items():
            field.setText(entry["draft"][key])
        index = next(
            (
                i
                for i in range(self.completed.count())
                if self.completed.itemData(i) is entry["draft"]["completed"]
            ),
            0,
        )
        self.completed.setCurrentIndex(index)
        self.verified.setChecked(entry["draft"]["verified"])
        self.allow_duplicate.setChecked(entry["allow_duplicate"])
        self.description.setPlainText(entry["draft"]["description"])
        self.warnings.setPlainText("\n".join(entry["source"].warnings + entry["fragment"].warnings))
        self.original.setPlainText(
            "".join(
                f"{number:4} | {line}"
                for number, line in enumerate(
                    entry["fragment"].original.splitlines(keepends=True), 1
                )
            )
        )
        self.confirm_button.setEnabled(not entry["fragment"].imported)
        self.busy = False
        self.update_vehicle_fields()

    def update_vehicle_fields(self):
        existing = type(self.target.currentData()) is int
        for key in ("make", "model", "plate", "vin", "contact_name", "phone"):
            self.fields[key].setEnabled(not existing)

    def changed(self, *_):
        if self.busy or self.current is None:
            return
        self.current["confirmed"] = False
        self.current["item"].setText(1, "Проверить")
        self.edited = True
        self.update_vehicle_fields()
        self.refresh_summary()

    def validate_entry(self, entry):
        from app.services.txt_import import validate_values

        draft = entry["draft"]
        value = {
            key: draft[key].strip()
            for key in ("make", "model", "plate", "vin", "contact_name", "phone")
        }
        supplied_date = draft["visit_date"].strip()
        parsed_date = parse_date(supplied_date) if supplied_date else None
        if supplied_date and parsed_date is None:
            raise ValueError("Укажите полную дату: дд.мм.гггг, либо оставьте поле пустым.")
        value |= {
            "visit_date": parsed_date,
            "km": optional_integer(draft["km"], "Пробег"),
            "amount_kopecks": parse_amount(draft["amount"]),
            "completed": draft["completed"],
            "mileage_verified": draft["verified"],
            "description": draft["description"],
        }
        validate_values(value)
        if type(entry["target"]) is int:
            if (
                self.service.possible_repair_duplicate(entry["target"], value)
                and not entry["allow_duplicate"]
            ):
                raise ValueError(
                    "Похожий ремонт уже есть. "
                    "Проверьте и при необходимости отметьте отдельную запись."
                )
        else:
            if not any(value[key] for key in ("make", "model", "plate", "vin")):
                raise ValueError(
                    "Заполните хотя бы одно обозначение новой машины или выберите существующую."
                )
            if (
                self.records.duplicates(value["plate"], value["vin"])
                and not entry["allow_duplicate"]
            ):
                raise ValueError(
                    "Такая машина уже есть. Выберите существующую или подтвердите отдельную запись."
                )
        entry["values"] = value

    def confirm_current(self):
        if self.current is None or self.current["fragment"].imported:
            return
        self.store_current()
        try:
            self.validate_entry(self.current)
        except ERRORS as error:
            QMessageBox.warning(self, "Проверьте запись", str(error))
            return
        self.current["confirmed"] = True
        self.current["item"].setText(1, "Подтверждено")
        self.edited = True
        self.refresh_summary()

    def confirm_ready(self):
        self.store_current()
        count = 0
        for entry in self.entries:
            if (
                entry["fragment"].imported
                or entry["source"].warnings
                or len(entry["fragment"].warnings) > 1
            ):
                continue
            try:
                self.validate_entry(entry)
            except ERRORS:
                continue
            entry["confirmed"] = True
            entry["item"].setText(1, "Подтверждено")
            count += 1
        self.edited = self.edited or count > 0
        self.refresh_summary()

    def refresh_summary(self):
        count = sum(entry["confirmed"] and not entry["fragment"].imported for entry in self.entries)
        self.summary.setText(
            f"Файлов: {len(self.sources)} · Записей: {len(self.entries)} · "
            f"Подтверждено: {count} · Ошибок чтения: {len(self.errors)}"
        )
        self.import_button.setEnabled(count > 0)

    def split_current(self):
        if self.current is None or self.current["fragment"].imported:
            return
        self.store_current()
        entry = self.current
        initial = self.new_entry(entry["source"], entry["fragment"])["draft"]
        if (entry["confirmed"] or entry["draft"] != initial) and QMessageBox.question(
            self,
            "Заменить подготовленные правки?",
            "Разделение заново разберёт исходный текст этой записи. "
            "Подготовленные правки потребуется проверить заново. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        lines = entry["fragment"].original.splitlines(keepends=True)
        if len(lines) < 2:
            return
        line, accepted = QInputDialog.getInt(
            self,
            "Разделить запись",
            "Номер строки, с которой начинается следующий визит (см. вкладку исходника)",
            2,
            2,
            len(lines),
        )
        if not accepted:
            return
        source = entry["source"]
        index = source.fragments.index(entry["fragment"])
        split_fragment(source, index, line)
        old_index = self.entries.index(entry)
        self.entries[old_index : old_index + 1] = [
            self.new_entry(source, fragment) for fragment in source.fragments[index : index + 2]
        ]
        self.current = None
        self.edited = True
        self.rebuild_tree()

    def reload_source(self):
        if self.current is None:
            return
        old = self.current["source"]
        if (
            QMessageBox.question(
                self,
                "Перечитать файл?",
                "Подготовленные изменения этого файла будут заменены новым разбором. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            source = self.service.preview(old.path, encoding=self.encoding.currentData())
        except (*ERRORS, UnicodeError) as error:
            QMessageBox.warning(self, "Не удалось прочитать TXT", str(error))
            return
        self.sources[self.sources.index(old)] = source
        self.entries = [entry for entry in self.entries if entry["source"] is not old]
        self.entries.extend(self.new_entry(source, fragment) for fragment in source.fragments)
        self.current = None
        self.rebuild_tree()

    def load_history(self):
        pieces = [
            f"{row['created_at']}\n{report_text(row['report'])}" for row in self.service.history()
        ]
        if self.errors:
            pieces.insert(0, "Ошибки текущего чтения:\n" + "\n".join(self.errors))
        self.report.setPlainText("\n\n".join(pieces) or "Импорт ещё не выполнялся.")

    def commit(self):
        self.store_current()
        selected = [
            entry for entry in self.entries if entry["confirmed"] and not entry["fragment"].imported
        ]
        if not selected:
            return
        if (
            QMessageBox.question(
                self,
                "Импортировать записи?",
                f"Добавить подтверждённых записей: {len(selected)}? "
                "Перед записью создаётся резервная копия.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            result = self.service.commit(selected, self.errors)
        except (*ERRORS, RuntimeError) as error:
            QMessageBox.warning(self, "Импорт не выполнен", str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        for entry in selected:
            entry["fragment"].imported = True
            entry["confirmed"] = False
            entry["item"].setText(1, "Импортировано")
        self.report.setPlainText(report_text(result))
        self.tabs.setCurrentIndex(2)
        self.edited = any(entry["confirmed"] for entry in self.entries)
        self.refresh_summary()

    def reject(self):
        if (
            self.edited
            and QMessageBox.question(
                self,
                "Закрыть предварительный просмотр?",
                "Неимпортированные правки предварительного просмотра будут потеряны. Закрыть?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        super().reject()
