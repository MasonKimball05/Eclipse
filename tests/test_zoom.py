from browser.zoom import remember_zoom, step_zoom, zoom_for, zoom_key


def test_zoom_keys():
    assert zoom_key("https://www.github.com/x") == "github.com"
    assert zoom_key("https://gist.github.com/") == "gist.github.com"  # subdomains are their own site
    assert zoom_key("http://localhost:8080/") == "localhost:8080"
    assert zoom_key("note:abc") is None and zoom_key("pbext://x/popup.html") is None


def test_remembering_and_steps():
    settings = {}
    remember_zoom(settings, "https://github.com/a", 1.25)
    assert zoom_for(settings, "https://www.github.com/b") == 1.25
    assert zoom_for(settings, "https://example.com/") == 1.0
    remember_zoom(settings, "https://github.com/", 1.0)
    assert settings["site_zoom"] == {}  # back to 100%: forgotten
    assert step_zoom(1.0, 1) == 1.1 and step_zoom(1.0, -1) == 0.9 and step_zoom(1.6, 0) == 1.0
    assert step_zoom(3.0, 1) is None and step_zoom(0.5, -1) is None
