

def test_only_real_ids_are_notes_or_easels(data_dir):
    import pytest
    from browser.easel import easel_folder, is_easel, new_easel_url
    from browser.notes import _path, is_note, new_note_url
    assert is_note(new_note_url()) and is_easel(new_easel_url())
    for bad in ("note:../../../../tmp/x", "note:/Users/x/.ssh/id", "note:", "note:a/b"):
        assert not is_note(bad), bad
        with pytest.raises(ValueError):
            _path(bad)
    for bad in ("easel:../x", "easel:/etc"):
        assert not is_easel(bad)
        with pytest.raises(ValueError):
            easel_folder(bad)


def _typed(editor, text):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    for ch in text:
        if ch == "\n":
            QTest.keyClick(editor, Qt.Key.Key_Return)
        else:
            QTest.keyClicks(editor, ch)


def test_typed_shorthand_makes_checklists_and_headings_that_save_as_markdown(qapp, data_dir):
    from browser.notes import NoteEditor, new_note_url
    url = new_note_url()
    editor = NoteEditor(url)
    _typed(editor, "# Trip\nPack:\n[] passport\nsocks\n\nDone")
    editor.save()
    text = (data_dir / "notes" / (url[5:] + ".md")).read_text()
    assert text.startswith("# Trip\n") and "- [ ] passport" in text and "- [ ] socks" in text
    assert "Pack:" in text and "# Pack" not in text  # (the line after a heading is ordinary)
    assert "- [ ] Done" not in text and "\nDone" in text  # (Return on an empty item ended the checklist)
    last = editor.document().lastBlock()
    assert last.textList() is None and last.blockFormat().indent() == 0


def test_a_box_is_ticked_by_clicking_it_or_with_the_shortcut(qapp, data_dir):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QTextCursor
    from PyQt6.QtTest import QTest
    from browser.notes import Marker, NoteEditor, new_note_url
    editor = NoteEditor(new_note_url())
    editor.resize(600, 400)
    editor.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)  # (laid out for real, but never on your screen)
    editor.show()
    _typed(editor, "[] milk")
    block = editor.document().firstBlock()
    assert block.blockFormat().marker() == Marker.Unchecked
    editor.toggle_checklist()  # ⇧⌘L on it
    assert editor.document().firstBlock().blockFormat().marker() == Marker.Checked
    start = editor.cursorRect(QTextCursor(editor.document().firstBlock()))
    box = QPoint(start.left() - 10, start.center().y())
    assert editor.box_at(box) is not None and editor.box_at(start.center() + QPoint(40, 0)) is None
    QTest.mouseClick(editor.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, box)
    assert editor.document().firstBlock().blockFormat().marker() == Marker.Unchecked
    assert "- [ ] milk" in editor.toMarkdown()
    editor.close()


def test_a_typed_bullet_starts_a_list_without_a_stray_space(qapp, data_dir):
    from browser.notes import NoteEditor, new_note_url
    editor = NoteEditor(new_note_url())
    _typed(editor, "* one\ntwo\n\nafter")
    first = editor.document().firstBlock()
    assert first.text() == "one" and first.textList() is not None
    assert first.next().text() == "two" and first.next().textList() is first.textList()
    assert editor.document().lastBlock().textList() is None  # (an empty item ends it)
