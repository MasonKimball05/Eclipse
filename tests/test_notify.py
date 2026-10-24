from browser.notify import unread_count


def test_unread_counts_from_titles():
    assert unread_count("(3) WhatsApp") == "3"
    assert unread_count("(99+) Facebook") == "99+"
    assert unread_count("Inbox (12) - you@gmail.com - Gmail") == "12"
    assert unread_count("* Slack | general") == "•"
    assert unread_count("(0) Inbox") is None
    assert unread_count("Inbox - you@gmail.com - Gmail") is None
    assert unread_count("(2026) the year in review") is None  # a year isn't an unread count
    assert unread_count("Release notes (v2)") is None and unread_count("") is None
