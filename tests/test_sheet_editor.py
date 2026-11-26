import sqlite3

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from test_service_sheet import setup_repair

from app.ui.documents import DocumentDialog


def select(canvas, text):
    doc = canvas.input.document()
    first, *rest = text.splitlines()
    cursor = doc.find(first)
    if rest:
        end = doc.find(rest[-1], cursor)
        begin = cursor.selectionStart()
        cursor.setPosition(begin)
        cursor.setPosition(end.selectionEnd(), QTextCursor.MoveMode.KeepAnchor)
    assert not cursor.isNull()
    canvas.input.setTextCursor(cursor)


def test_edit_select_move_fit_save_and_reprint(application, tmp_path, monkeypatch):
    records, service, car, repair = setup_repair(tmp_path)
    dialog = DocumentDialog(records, repair)
    dialog.show()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _, title, msg: warnings.append((title, msg)))
    dialog.description.setPlainText("\n".join(f"Работа {i + 1}" for i in range(21)))
    dialog.recommendations.setPlainText(
        "Первая строка\nВторая строка\nТретья строка\nПоследняя строка"
    )
    dialog.preview()
    dialog.edit_sheet.click()
    application.processEvents()
    assert dialog.editing_sheet(), warnings
    canvas = dialog.sheet_editor.canvas
    assert canvas.pdf.pageCount() == 2
    first = canvas.lines[0]
    y = round(6 + (first.top + first.height / 2) * canvas.scale())
    x = round(6 + (first.edges[0] - 12) * canvas.scale())
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    QTest.mouseMove(canvas.viewport(), QPoint(x, y - 43))
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, y - 43))
    canvas.refresh()
    moved = canvas.blocks()[0]["gap"]
    assert moved < 20
    QTest.keyClick(canvas, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert canvas.blocks()[0]["gap"] > 50
    QTest.keyClick(canvas, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert canvas.blocks()[0]["gap"] == moved
    select(canvas, "Первая строка\nВторая строка\nТретья строка\nПоследняя строка")
    canvas.set_size(10)
    canvas.refresh()
    assert canvas.pdf.pageCount() == 1
    assert canvas.blocks()[0]["runs"][0]["size"] == 14.04
    select(canvas, "Последняя")
    QTest.keyClicks(canvas, "Final")
    assert dialog.save(close=False), warnings
    saved = service.draft(repair)["manual_sheet"]
    dialog.accept()
    reopened = DocumentDialog(records, repair)
    assert reopened.state()["manual_sheet"] == saved
    assert "Final" in reopened.pdf.getAllText(0).text()
    assert reopened.pdf.pageCount() == 1
    assert reopened.create(), warnings
    assert records.repairs(car)[0]["recommendations"].endswith("Последняя строка")
    assert reopened.current_document["payload"]["manual_sheet"] == saved
    assert not warnings
    reopened.accept()


def test_archived_copy_is_independent(application, tmp_path, monkeypatch):
    records, service, car, repair = setup_repair(tmp_path)
    original = service.create(repair, layout="service_sheet")
    content = service.pdf_content(original["id"])
    records.save_vehicle(records.vehicle(car) | {"plate": "другой"}, car)
    dialog = DocumentDialog(records, repair)
    dialog.show()
    dialog.start_sheet()
    canvas = dialog.sheet_editor.canvas
    assert dialog.editing_sheet()
    assert canvas.buffer.data().data() == content
    select(canvas, "Учебная")
    QTest.keyClicks(canvas, "Edited")
    assert dialog.copy_changed()
    printed = []
    monkeypatch.setattr(dialog, "print_document", lambda: printed.append(dialog.content))
    dialog.print_or_create()
    assert dialog.current_document["revision"] == 1
    assert printed == [service.pdf_content(original["id"], 1)]
    assert "Edited" in dialog.pdf.getAllText(0).text()
    dialog.print_or_create()
    dialog.close()
    reopened = DocumentDialog(records, repair)
    assert "копия 1" in reopened.history.currentText()
    reopened.history.setCurrentIndex(1)
    assert reopened.content == content
    with sqlite3.connect(service.path) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM document_copies").fetchone() == (1,)
    assert service.create(repair, layout="service_sheet")["number"] == "СЛ-000002"
    reopened.close()


@pytest.mark.parametrize("rebuild", [True, False])
def test_source_changed_requires_visible_choice(application, tmp_path, monkeypatch, rebuild):
    records, _, _, repair = setup_repair(tmp_path)
    dialog = DocumentDialog(records, repair)
    dialog.show()
    dialog.start_sheet()
    canvas = dialog.sheet_editor.canvas
    select(canvas, "Учебная")
    QTest.keyClicks(canvas, "Manual")
    dialog.finish_sheet()
    dialog.description.setPlainText("Обновлённые данные")
    prompts = []

    def choose(box):
        prompts.append(box.windowTitle())
        label = "Собрать из обновлённых данных" if rebuild else "Оставить мой лист"
        next(button for button in box.buttons() if button.text() == label).click()

    monkeypatch.setattr(QMessageBox, "exec", choose)
    assert dialog.create() == (not rebuild)
    assert prompts == ["Изменились данные для печати"]
    if rebuild:
        assert dialog.editing_sheet()
        assert "Обновлённые данные" in canvas.input.toPlainText()
        canvas.input.undo()
        assert "Manual" in canvas.input.toPlainText()
        canvas.input.redo()
        assert dialog.create()
    assert ("Обновлённые данные" if rebuild else "Manual") in dialog.pdf.getAllText(0).text()
    dialog.accept()


def test_multiline_wrap_copy_cancel_and_last_keystroke(application, tmp_path, monkeypatch):
    records, service, _, repair = setup_repair(tmp_path)
    original = service.create(
        repair,
        layout="service_sheet",
        description="Проверить давление в шинах и затяжку крепежа. " * 140,
    )
    dialog = DocumentDialog(records, repair)
    dialog.show()
    dialog.start_sheet()
    canvas = dialog.sheet_editor.canvas
    assert canvas.pdf.pageCount() > 2
    QTest.keyClick(canvas, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(canvas, Qt.Key.Key_Return)
    QTest.keyClicks(canvas, "Last")
    assert dialog.copy_changed()

    def choose(box):
        next(b for b in box.buttons() if b.text() == selection).click()

    monkeypatch.setattr(QMessageBox, "exec", choose)
    selection = "Продолжить редактирование"
    dialog.reject()
    assert dialog.isVisible()
    selection = "Не сохранять"
    dialog.reject()
    assert not dialog.isVisible()
    assert len(service.history(repair)) == 1
    dialog = DocumentDialog(records, repair)
    dialog.start_sheet()
    canvas = dialog.sheet_editor.canvas
    QTest.keyClick(canvas, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(canvas, "Last")
    selection = "Сохранить"
    dialog.reject()
    assert len(service.history(repair)) == 2
    from test_service_sheet import open_pdf

    pdf, buf = open_pdf(service.pdf_content(original["id"], 1))
    assert "Last" in pdf.getAllText(pdf.pageCount() - 1).text()
    pdf.close()
    buf.close()


def test_copy_export_backup_and_v10_migration(application, tmp_path):
    from app.database.connection import backup_database, initialize_database, restore_database
    from app.services.documents import Documents
    from app.services.export_data import export_database

    records, service, _, repair = setup_repair(tmp_path)
    original = service.create(repair, layout="service_sheet")
    with sqlite3.connect(service.path) as db:
        db.execute("DROP TABLE document_copies")
        db.execute("PRAGMA user_version=10")
    before = service.pdf_content(original["id"])
    initialize_database(service.path)
    (backup,) = list((tmp_path / "backups").glob("*.sqlite3"))
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (10,)
    dialog = DocumentDialog(records, repair)
    dialog.start_sheet()
    canvas = dialog.sheet_editor.canvas
    select(canvas, "Учебная")
    QTest.keyClicks(canvas, "Edit")
    assert dialog.save(close=False)
    edited = dialog.content
    assert before != edited
    folder = tmp_path / "export"
    export_database(service.path, folder)
    assert (folder / "pdf/document-1-copy-1.pdf").read_bytes() == edited
    assert (folder / "pdf/document-1.pdf").read_bytes() == before
    dialog.close()
    snapshot = backup_database(service.path)
    restored = tmp_path / "restored.sqlite3"
    initialize_database(restored)
    restore_database(restored, snapshot)
    assert Documents(restored).pdf_content(original["id"], 1) == edited
    assert Documents(restored).pdf_content(original["id"]) == before
