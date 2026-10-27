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

