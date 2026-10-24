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
