from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePermission

from browser.permissions import capture_rule, describe, origin_key, set_capture_rule

T = QWebEnginePermission.PermissionType


def test_origin_key():
    assert origin_key(QUrl("https://meet.google.com/abc-defg-hij?authuser=0#x")) == "https://meet.google.com"
    assert origin_key(QUrl("http://localhost:8080/app")) == "http://localhost:8080"
    assert origin_key(QUrl("https://user:pw@site.example/")) == "https://site.example"


def test_rules_are_per_exact_origin():
    settings = {}
    set_capture_rule(settings, "https://a.com", "MediaVideoCapture", "allow")
    assert capture_rule(settings, "https://a.com", "MediaVideoCapture") == "allow"
    assert capture_rule(settings, "http://a.com", "MediaVideoCapture") is None  # not the same site
    assert capture_rule(settings, "https://sub.a.com", "MediaVideoCapture") is None
    set_capture_rule(settings, "https://a.com", "MediaVideoCapture", None)
    assert settings["site_permissions"] == {}  # nothing left behind


def test_describe_merges_camera_and_microphone():
    assert describe([T.MediaAudioCapture, T.MediaVideoCapture]) == "use your camera and microphone"
    assert describe([T.Notifications]) == "show notifications"
    assert describe([T.Geolocation, T.Notifications, T.ClipboardReadWrite]).count(" and ") == 1


def test_only_websites_get_rules():
    assert origin_key(QUrl("file:///Users/me/a.html")) == ""
    assert origin_key(QUrl("data:text/html,hi")) == ""


def test_a_new_question_on_the_card_waits_and_only_what_was_shown_is_answered(qapp, monkeypatch):
    import time
    from types import SimpleNamespace
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QColor
    from PyQt6.QtWebEngineCore import QWebEnginePermission
    from PyQt6.QtWidgets import QWidget
    import browser.permissions as perms
    T = QWebEnginePermission.PermissionType

    class Request:
        def __init__(self, kind):
            self.kind, self.answer = kind, None

        def origin(self):
            return QUrl("https://site.example")

        def permissionType(self):
            return self.kind

        def isValid(self):
            return self.answer is None

        def grant(self):
            self.answer = "granted"

        def deny(self):
            self.answer = "denied"
    host = QWidget()
    view = QWidget(host)
    view.page = lambda: SimpleNamespace(profile=lambda: SimpleNamespace(isOffTheRecord=lambda: False))
    browser = SimpleNamespace(store=SimpleNamespace(settings={}, schedule_save=lambda: None),
                              theme=SimpleNamespace(accent=QColor("#7c5cff"), accent_text=QColor("#9d85ff"), panel_text=QColor("#ffffff")),
                              content=host)
    manager = perms.PermissionManager.__new__(perms.PermissionManager)
    perms.QObject.__init__(manager)
    manager.browser, manager._waiting, manager._prompts, manager._capturing = browser, {}, {}, set()
    notifications = Request(T.Notifications)
    manager._waiting[id(view)] = [(view, notifications)]
    manager._show(view)
    prompt = manager._prompts[id(view)]
    assert not prompt.allow.isEnabled()  # (just shown: a moment first)
    end = time.time() + 3  # (a busy machine, with the live tests' windows about, can take a while)
    while time.time() < end and not prompt.allow.isEnabled():
        qapp.processEvents()
        time.sleep(0.02)
    assert prompt.allow.isEnabled()
    camera = Request(T.MediaVideoCapture)
    manager._waiting[id(view)].append((view, camera))  # (asked just as you go to click)
    manager.answer(prompt, True)
    assert notifications.answer == "granted" and camera.answer is None  # the camera waits for its own card
    assert "camera" not in str(browser.store.settings)


def test_which_links_belong_to_other_apps_and_remembering_them():
    from PyQt6.QtCore import QUrl
    from browser.external_links import all_rules, allowed, is_external, set_allowed
    for url in ("zoommtg://zoom.us/join", "vscode://file/x", "mailto:a@b.c", "tel:123", "daybook://add-task"):
        assert is_external(QUrl(url)), url
    for url in ("https://a.com", "http://a.com", "file:///tmp/x", "data:text/html,x", "about:blank", "blob:https://a/b",
                "javascript:void(0)", "pbext://abc/popup.html", "view-source:https://a.com"):
        assert not is_external(QUrl(url)), url
    settings = {}
    assert not allowed(settings, "https://zoom.us", "zoommtg")
    set_allowed(settings, "https://zoom.us", "zoommtg", True)
    assert allowed(settings, "https://zoom.us", "zoommtg") and not allowed(settings, "https://zoom.us", "vscode")
    assert not allowed(settings, "https://evil.example", "zoommtg") and not allowed(settings, "", "zoommtg")
    assert all_rules(settings) == [("https://zoom.us", "zoommtg")]
    set_allowed(settings, "https://zoom.us", "zoommtg", False)
    assert all_rules(settings) == [] and settings["external_apps"] == {}
