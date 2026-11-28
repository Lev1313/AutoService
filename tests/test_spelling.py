from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu
from test_service_sheet import setup_repair

from app.services.spelling import dictionary_job, read_words
from app.ui.documents import DocumentDialog
from app.ui.spelling import SpellField, WorkshopWords
from app.ui.vehicles import RepairDialog


def choose(marks, pos, title):
    menu = QMenu()
    assert marks.menu(pos, menu)
    action = next(action for action in menu.actions() if action.text() == title)
    action.trigger()


def test_word_menu_and_local_dictionary(application, tmp_path):
    dictionary_job().result(timeout=60)
    records, _, car, _ = setup_repair(tmp_path)
    repair = RepairDialog(records, car)
    field = repair.description
    field.setPlainText("🔧 Замена потшипника. ШРУС сайлентблока а123вс77 АБ-123 WVWZZZ1JZXW000001")
    marks = field.spelling
    marks.refresh()
    assert [m.cursor.selectedText() for m in field.extraSelections()] == ["потшипника"]
    choose(marks, 12, "подшипника")
    assert "Замена подшипника" in field.toPlainText()
    field.undo()
    assert "Замена потшипника" in field.toPlainText()
    field.setPlainText("мехтестик мехтестик")
    repair.recommendations.setPlainText("Мехтестик")
    choose(marks, 1, "Пропустить")
    assert [m.cursor.selectionStart() for m in field.extraSelections()] == [10]
    choose(marks, 12, "Добавить в словарь")
    assert not field.extraSelections()
    assert not repair.recommendations.extraSelections()
    assert read_words(tmp_path / "словарь.txt") == {"мехтестик"}
    assert "мехтестик" in WorkshopWords(tmp_path / "словарь.txt").entries
    repair.accept()


def test_marks_on_sheet_are_only_an_overlay(application, tmp_path):
    dictionary_job().result(timeout=60)
    records, _, car, repair = setup_repair(tmp_path)
    dialog = DocumentDialog(records, repair)
    dialog.description.setPlainText("Замена потшипника")
    dialog.start_sheet()
    canvas = dialog.sheet_editor.canvas
    before = canvas.manual()
    pdf = canvas.pdf.render(0, QSize(600, 849))
    marks = canvas.spelling
    marks.refresh()
    pos = canvas.input.document().find("потшипника").selectionStart()
    assert pos >= 0 and canvas.manual() == before
    assert pdf == canvas.pdf.render(0, QSize(600, 849))
    choose(marks, pos + 1, "подшипника")
    canvas.refresh()
    assert "подшипника" in canvas.pdf.getAllText(0).text()
    assert dialog.description.toPlainText() == "Замена потшипника"
    assert dialog.save(close=False)
    assert records.repairs(car)[0]["description"] == "Замена потшипника"
    dialog.accept()


def test_item_title_accepts_spelling_correction(application, tmp_path):
    dictionary_job().result(timeout=60)
    records, _, _, repair = setup_repair(tmp_path)
    dialog = DocumentDialog(records, repair)
    dialog.show()
    application.processEvents()
    dialog.kind.setCurrentIndex(dialog.kind.findData("receipt"))
    dialog.add_item("part")
    editor = dialog.focusWidget()
    assert isinstance(editor, SpellField)
    editor.setPlainText("потшипника")
    opened = []

    def correct():
        menu = QApplication.activePopupWidget()
        opened.append(isinstance(menu, QMenu))
        if opened[-1]:
            action = next((a for a in menu.actions() if a.text() == "подшипника"), None)
            if action:
                action.trigger()
            menu.close()

    cursor = editor.textCursor()
    cursor.setPosition(3)
    point = editor.cursorRect(cursor).center()
    QTimer.singleShot(0, correct)
    QApplication.sendEvent(
        editor.viewport(),
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, point, editor.viewport().mapToGlobal(point)
        ),
    )
    assert opened == [True] and editor.toPlainText() == "подшипника"
    assert dialog.save(close=False)
    assert dialog.item_rows("part")[-1]["title"] == "подшипника"
    dialog.accept()
