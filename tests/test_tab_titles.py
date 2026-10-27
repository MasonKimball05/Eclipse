from browser.models import Tab
from browser.tab_titles import SETTING, is_site_name, shown_title, tidy


def test_the_sites_name_goes():
    assert tidy("Pull requests · anthropics/claude-code · GitHub", "https://github.com/anthropics/claude-code/pulls") \
        == "Pull requests · anthropics/claude-code"
    assert tidy("Q3 plan - Google Docs", "https://docs.google.com/document/d/1") == "Q3 plan"
    assert tidy("Python (programming language) - Wikipedia", "https://en.wikipedia.org/wiki/Python") \
        == "Python (programming language)"
    assert tidy("How do I exit Vim? - Stack Overflow", "https://stackoverflow.com/q/11828270") == "How do I exit Vim?"
    assert tidy("BBC News | Home", "https://www.bbc.co.uk/news") == "BBC News"  # (at the start; "Home" says less)
    assert tidy("Home - YouTube", "https://www.youtube.com/") == "YouTube"
    assert tidy("lofi beats — YouTube", "https://www.youtube.com/watch?v=1") == "lofi beats"
    assert tidy("arc - Google Search", "https://www.google.com/search?q=arc") == "arc"
    assert tidy("pathlib — Object-oriented filesystem paths — Python 3.15.2 documentation",
                "https://docs.python.org/3/library/pathlib.html") == "pathlib — Object-oriented filesystem paths"
    assert tidy("GitHub - anthropics/claude-code: Claude Code is an agentic coding tool - all in your terminal. · GitHub",
                "https://github.com/anthropics/claude-code") == "anthropics/claude-code"
    assert tidy("BBC News - Breaking news, video and the latest top stories", "https://www.bbc.com/") == "BBC News"
    assert tidy("Spotify - Web Player: Listen to Music", "https://open.spotify.com/") == "Spotify"
    assert tidy("Lisbon - Google Maps", "https://www.google.com/maps/place/Lisbon") == "Lisbon"


def test_gmail_keeps_just_the_folder():
    assert tidy("Inbox (3) - you@gmail.com - Gmail", "https://mail.google.com/mail/u/0/#inbox") == "Inbox (3)"


def test_a_generic_rest_keeps_the_site_name():
    assert tidy("Home - YouTube", "https://www.youtube.com/") == "YouTube"
    assert tidy("Home / X", "https://x.com/home") == "X"


def test_titles_without_the_site_are_left_alone():
    for title, url in (("YouTube", "https://www.youtube.com/"),
                       ("Rust 1.80 released - what's new", "https://blog.example.org/rust"),
                       ("Python - Wikipedia", "file:///tmp/x.html"),
                       ("Email tips - Mailchimp blog", "https://news.ycombinator.com/item?id=1")):
        assert tidy(title, url) == title
    # Only spaced punctuation separates: "Re: Hello" and "Q&A-session" are one piece
    assert tidy("Re: Hello - Gmail", "https://mail.google.com/") == "Re: Hello"


def test_a_titles_own_words_are_kept():
    for title, url in (("GitHub Actions - owner/repo", "https://github.com/owner/repo/actions"),
                       ("Team standup - Calendar", "https://teams.microsoft.com/x"),
                       ("Applications - Settings", "https://app.example.com/"),
                       ("Webinar - Notes", "https://web.example.com/"),
                       ("Mail merge - Docs help", "https://mail.google.com/"),
                       ("(500) Days of Summer (2009) - Movies", "https://example.com/"),
                       ("(3) Inbox - Mail", "https://fastmail.example/")):
        assert tidy(title, url) == title, title
    assert tidy("Stripe CLI - Stripe Documentation", "https://docs.stripe.com/cli") == "Stripe CLI"
    assert tidy("(3) Inbox - Slack", "https://app.slack.com/client") == "(3) Inbox"  # (counts stay)


def test_site_names():
    assert is_site_name("Gmail", "mail.google.com") and is_site_name("GitHub", "github.com")
    assert is_site_name("X", "x.com") and not is_site_name("Example news site of the day", "example.com")
    assert not is_site_name("Google", "github.com")


def test_shown_title_respects_renames_and_the_setting():
    tab = Tab("https://docs.google.com/document/d/1", "Q3 plan - Google Docs")
    assert shown_title(tab, {}) == "Q3 plan"
    assert shown_title(tab, {SETTING: False}) == "Q3 plan - Google Docs"
    tab.custom_title = "Plan"
    assert shown_title(tab, {}) == "Plan"
    assert shown_title(Tab("https://example.com/"), {}) == "example.com"
