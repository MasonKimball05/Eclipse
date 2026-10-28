from browser import macos


def test_unread_messages_come_before_downloads_on_the_dock_icon(qapp):
    macos.set_dock_badge("↓2", "downloads")
    assert macos.dock_badge_text() == "↓2"
    macos.set_dock_badge("5")  # (unread, from your sites)
    assert macos.dock_badge_text() == "5"
    macos.set_dock_badge("", "downloads")
    assert macos.dock_badge_text() == "5"
    macos.set_dock_badge("")
    assert macos.dock_badge_text() == ""
