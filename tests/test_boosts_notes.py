"""Boosts and Notes."""

from browser.boosts import Boost, BoostManager, site_of


def test_boosts_match_sites_and_collect_zaps(store):
    manager = BoostManager(store, None)
    manager.save(Boost("example.com", css="h1 { color: red }"))
    assert manager.for_site("docs.example.com") and not manager.for_site("notexample.com")
    boost = manager.add_zapped("news.com", "div.ad")
    manager.add_zapped("news.com", "div.ad")
    assert boost.zapped == ["div.ad"]
    assert len(store.settings["boosts"]) == 2
    source = BoostManager._script(boost).sourceCode()
    assert '"news.com"' in source and "div.ad" in source and "new Function" in source
    assert site_of("https://www.Example.com/page") == "example.com"


def test_note_saves_markdown_and_titles_itself(data_dir):
    from browser.notes import NoteEditor, new_note_url
    url = new_note_url()
    editor = NoteEditor(url)
    assert editor.title == "Untitled Note"
    editor.insertPlainText("# Trip plan\n\nPack bags")  # like typing
    assert editor.title == "Trip plan"
    editor.save()
    reopened = NoteEditor(url)
    assert "Pack bags" in reopened.toPlainText()


def test_stale_note_editor_does_not_overwrite_newer_text(data_dir):
    from browser.notes import NoteEditor, new_note_url
    url = new_note_url()
    stale, fresh = NoteEditor(url), NoteEditor(url)  # e.g. the same note opened twice
    fresh.insertPlainText("Newer text")
    fresh.save()
    stale.save()  # never edited, so it must not write its empty copy over the file
    assert "Newer text" in NoteEditor(url).toPlainText()
