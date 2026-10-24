from browser.models import Tab
from browser.tidy import build_prompt, parse_groups


def test_prompt_lists_tabs_by_number():
    prompt = build_prompt([Tab("https://react.dev/learn", "Quick Start – React"), Tab("https://www.github.com/x", "x")])
    assert "1 | Quick Start – React | react.dev" in prompt and "2 | x | github.com" in prompt


def test_parse_groups_accepts_json_wrapped_in_prose():
    reply = 'Sure! {"folders": [{"name": "React Docs", "tabs": [1, 3]}, {"name": "Travel", "tabs": [2, 4, 5]}]} Done.'
    assert parse_groups(reply, 5) == [("React Docs", [0, 2]), ("Travel", [1, 3, 4])]


def test_parse_groups_drops_bad_numbers_overlaps_and_tiny_groups():
    reply = ('{"folders": [{"name": "A", "tabs": [1, 2, 2, 99, 0, "3"]}, {"name": "B", "tabs": [2, 3]},'
             ' {"name": "", "tabs": [4, 5]}, {"name": "C", "tabs": [4]}]}')
    # A keeps 1 and 2; B loses tab 2 (already used) and is left with one tab, so it's dropped
    assert parse_groups(reply, 5) == [("A", [0, 1])]


def test_parse_groups_handles_garbage():
    assert parse_groups("I couldn't do that.", 5) == []
    assert parse_groups('{"folders": "nope"}', 5) == []
    assert parse_groups("{not json}", 5) == []
