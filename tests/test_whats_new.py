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
