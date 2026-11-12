def test_a_translation_is_dropped_when_its_page_closes(qapp):
    from browser.translate import Translator, _Job

    class Request:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    class Browser:
        pass
    translator = Translator.__new__(Translator)
    translator.browser = Browser()
    translator._jobs, translator._translated, translator._watched = {}, set(), set()
    job = _Job(object(), [], "English")
    job.requests = [Request(), Request()]
    translator._jobs[7] = job
    translator._translated.add(7)
    translator._watched.add(7)
    translator._drop(7)  # (its view's destroyed signal)
    assert job.cancelled and all(r.cancelled for r in job.requests)
    assert 7 not in translator._jobs and 7 not in translator._translated and 7 not in translator._watched


def test_away_queue_copes_with_the_tab_closing_while_homebase_answers(qapp):
    from browser.integrations_ui import AppsUI

    class Homebase:
        def projects(self, done):
            done(["Eclipse"], None)  # (answers: by now the tab has closed)

    class Browser:
        homebase = Homebase()
        toasts = []

        def toast(self, text, *args):
            self.toasts.append(text)

    class Tab:
        url = "https://github.com/MasonKimball05/Eclipse/issues/12"
        view = None  # closed
    apps = AppsUI.__new__(AppsUI)
    apps.browser = apps.feature = Browser()  # (the window, and the feature that has Homebase)
    apps.issue_to_away(Tab())
    assert apps.browser.toasts == ["The issue's tab was closed"]



def test_a_second_translate_press_while_its_text_is_read_stops_it_not_starts_another(qapp):
    """Idle until the page's text came back, a second quick press started a second job: the first couldn't be
    stopped (every part of it a Claude request), and Stop only reached the second."""
    from types import SimpleNamespace
    from browser.translate import Translator
    collected = []
    page = SimpleNamespace(runJavaScript=lambda code, world, callback: collected.append(callback))
    view = SimpleNamespace(page=lambda: page)
    told = []
    browser = SimpleNamespace(_current_view=lambda: view, toast=told.append, store=SimpleNamespace(settings={}),
                              feature=lambda name: SimpleNamespace(claude=SimpleNamespace(available=True)))
    translator = Translator.__new__(Translator)
    translator.browser = browser
    translator._jobs, translator._translated, translator._watched, translator._collecting = {}, set(), set(), set()
    translator._changed = lambda v: None
    translator.toggle(view)  # (pressed: its text being read)
    assert translator.state(view) == "translating"
    translator.toggle(view)  # (pressed again before it's back: that stops it)
    assert told == ["Stopped translating"] and len(collected) == 1
    collected[0]({"texts": ["Hola"]})  # (the text, back late)
    assert translator._jobs == {} and translator.state(view) == "idle"


def test_an_alert_whose_window_has_gone_doesnt_hold_up_the_next_question(qapp, monkeypatch):
    """A sheet whose window went (closed, put away) may never say it closed: every question after it waited, unasked,
    till Eclipse was restarted."""
    from PyQt6.QtWidgets import QWidget

    class Page:  # (an alert's page: only what _busy looks at)
        def __init__(self, page_id, asked_from=None):
            self.id = page_id
            if asked_from is not None:
                self.asked_from = asked_from
    from browser import forms, prompts
    monkeypatch.setattr(forms, "answering", lambda: False)
    shown, gone = QWidget(), QWidget()
    shown.show()
    stuck = Page(5, gone)
    monkeypatch.setattr(prompts, "_open", {stuck})
    gone.close()
    assert not prompts._busy() and stuck not in prompts._open
    up = Page(6, shown)  # (its window still there: it's still being asked)
    prompts._open.add(up)
    assert prompts._busy()
    prompts._open.discard(up)
    unknown = Page(7)  # (from before this was noted: left as it was)
    prompts._open.add(unknown)
    assert prompts._busy()
    shown.close()


def test_an_error_in_code_macos_calls_back_is_logged(monkeypatch):
    """Raised inside a callback from macOS (opening a Spotlight item, Touch ID's answer), it went to
    sys.unraisablehook: never in errors.log, and the Spotlight item just didn't open."""
    from browser import crash, macos
    logged = []
    monkeypatch.setattr(crash, "log_exception", lambda kind, value, tb: logged.append(str(value)))

    def broken(item):
        raise RuntimeError(f"couldn't open {item}")
    assert macos._reported(broken, "space-1") is None
    assert logged == ["couldn't open space-1"]
