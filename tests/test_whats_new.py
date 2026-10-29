def test_whats_new_once(monkeypatch):
    from browser import whats_new
    shown = []
    monkeypatch.setattr(whats_new, "WhatsNew", lambda browser: type("W", (), {"show": lambda self: shown.append(1)})())

    class Store:
        is_new = False
        settings = {}

        def schedule_save(self):
            pass
    browser = type("B", (), {"store": Store()})()
    assert whats_new.show_if_new(browser) is not None and whats_new.show_if_new(browser) is None
    assert shown == [1]


def test_the_makers_own_apps_are_only_in_the_makers_copy():
    from browser.whats_new import MINE, NOTES, notes
    assert MINE in NOTES
    theirs, mine = notes(False), notes(True)
    assert MINE not in theirs and MINE not in mine
    for about_my_apps in ("Homebase", "Job Tracker", "Parliament", "Send To"):
        assert about_my_apps in mine and about_my_apps not in theirs, about_my_apps
    assert "Spell check" in theirs and "Private windows" in theirs and "Downloads fly home" in theirs
    assert "## Browsing" in theirs  # (the next section's back after the one left out)
