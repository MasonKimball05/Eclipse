import time

from browser import storage
from browser.models import FAVORITES, PINNED, TODAY, Folder, Store, Tab, walk_tabs


def test_fresh_store_has_starter_spaces_and_favorites(store):
    assert store.is_new
    assert [s.name for s in store.spaces] == ["Personal", "Work"]
    assert len(store.favorites) == 4
    assert store.active_space is store.spaces[0]


def test_new_tabs_go_on_top_of_today(store):
    a, b = Tab("https://a.com"), Tab("https://b.com")
    store.add_tab(a)
    store.add_tab(b)
    assert store.active_space.today == [b, a]
    assert store.locate(a) == (TODAY, store.active_space)


def test_pinning_sets_home_url_and_unpinning_clears_it(store):
    tab = Tab("https://a.com/page")
    store.add_tab(tab)
    store.move_tab(tab, PINNED)
    assert tab.home_url == "https://a.com/page"
    tab.custom_title = "Renamed"
    store.move_tab(tab, TODAY)
    assert tab.home_url is None and tab.custom_title is None


def test_reordering_within_a_list_accounts_for_the_removed_slot(store):
    tabs = [Tab(f"https://{c}.com") for c in "abc"]
    for t in reversed(tabs):
        store.add_tab(t, PINNED, index=0)
    store.move_tab(tabs[0], PINNED, index=2)  # a to just before c
    assert [t.url for t in store.active_space.pinned] == ["https://b.com", "https://a.com", "https://c.com"]


def test_tabs_move_into_folders_and_folders_open(store):
    space = store.active_space
    folder = store.add_folder(space, "Work")
    folder.expanded = False
    tab = Tab("https://a.com")
    store.add_tab(tab)
    store.move_tab(tab, PINNED, folder=folder)
    assert folder.children == [tab]
    assert folder.expanded
    assert store.locate(tab) == (PINNED, space)
    assert store.folder_of(tab) is folder
    assert tab in store.visible_tabs()


def test_folder_cannot_go_inside_itself_or_its_children(store):
    space = store.active_space
    outer = store.add_folder(space, "Outer")
    inner = Folder("Inner")
    store.move_tab(inner, PINNED, folder=outer)
    store.move_tab(outer, PINNED, folder=inner)
    store.move_tab(outer, PINNED, folder=outer)
    assert space.pinned == [outer] and outer.children == [inner]


def test_folders_never_go_into_favorites(store):
    folder = store.add_folder(store.active_space, "F")
    store.move_tab(folder, FAVORITES)
    assert folder in store.active_space.pinned
    store.move_tab(folder, TODAY)  # Today can hold folders (Tidy Tabs makes them)
    assert folder in store.active_space.today


def test_today_folders_hold_tabs_and_vanish_when_empty(store):
    space = store.active_space
    a, b = Tab("https://a.com"), Tab("https://b.com")
    store.add_tab(a)
    store.add_tab(b)
    folder = store.add_folder(space, "Reading", kind=TODAY)
    store.move_tab(a, TODAY, folder=folder)
    store.move_tab(b, TODAY, folder=folder)
    assert store.locate(a) == (TODAY, space) and a in store.visible_tabs()
    assert space.today == [folder]
    a.last_active = b.last_active = 0
    assert {t for t, _ in store.stale_today_tabs()} == {a, b}  # archiving looks inside folders
    store.archive_tab(a, space)
    assert folder in space.today
    store.archive_tab(b, space)
    assert space.today == []  # the emptied folder went away with its last tab


def test_ungroup_keeps_tabs_in_place(store):
    space = store.active_space
    before, after = Tab("https://before.com"), Tab("https://after.com")
    store.add_tab(before, PINNED)
    folder = store.add_folder(space, "F")
    store.add_tab(after, PINNED, index=len(space.pinned))
    inside = [Tab("https://x.com"), Tab("https://y.com")]
    folder.children = list(inside)
    store.ungroup_folder(folder)
    assert space.pinned == [before, *inside, after]


def test_removing_a_folder_removes_its_tabs(store):
    folder = store.add_folder(store.active_space, "F")
    folder.children = [Tab("https://x.com")]
    store.remove_tab(folder)
    assert not list(walk_tabs(store.active_space.pinned))


def test_split_view_groups(store):
    a, b, c, d, e = (Tab(f"https://{x}.com") for x in "abcde")
    for t in (a, b, c, d, e):
        store.add_tab(t)
    assert store.add_to_split(a, b)
    assert store.split_of(a) == [a, b]
    assert store.add_to_split(a, c) and store.add_to_split(a, d)
    assert not store.add_to_split(a, e)  # four panes at most
    store.remove_from_split(b)
    assert store.split_of(a) == [a, c, d]
    store.separate_split(c)
    assert store.split_of(a) is None


def test_split_with_one_tab_left_disappears(store):
    a, b = Tab("https://a.com"), Tab("https://b.com")
    store.add_tab(a)
    store.add_tab(b)
    store.add_to_split(a, b)
    store.remove_from_split(b)
    assert store.splits == [] and store.split_of(a) is None


def test_stale_today_tabs_respect_the_setting_and_skip_current(store):
    old, fresh, current = Tab("https://old.com"), Tab("https://new.com"), Tab("https://cur.com")
    old.last_active = current.last_active = time.time() - 13 * 3600
    for t in (old, fresh, current):
        store.add_tab(t)
    stale = [t for t, _ in store.stale_today_tabs(exclude=[current])]
    assert stale == [old]
    store.settings["archive_after_hours"] = 0  # "Never"
    assert store.stale_today_tabs() == []


def test_history_keeps_newest_per_url(store):
    store.record_history("https://a.com", "A")
    store.merge_history([{"url": "https://a.com", "title": "older", "ts": 1}, {"url": "https://b.com", "title": "B", "ts": 2}])
    assert [h["url"] for h in store.history] == ["https://a.com", "https://b.com"]
    assert store.history[0]["title"] == "A"
    store.record_history("note:abc", "not a web page")
    assert len(store.history) == 2


def test_save_and_reload_round_trip(store):
    space = store.active_space
    space.profile = "work"
    space.theme = {"colors": ["#ff0000", "#0000ff"], "intensity": 0.8, "mode": "dark", "grain": True}
    folder = store.add_folder(space, "Docs")
    folder.children = [Tab("https://docs.com", "Docs", home_url="https://docs.com", custom_title="My Docs")]
    a, b = Tab("https://a.com"), Tab("https://b.com")
    store.add_tab(a)
    store.add_tab(b)
    store.add_to_split(a, b)
    store.save()

    again = Store(storage.session_path())
    s = again.active_space
    assert not again.is_new
    assert s.profile == "work" and s.theme["grain"] is True
    assert s.pinned[0].name == "Docs" and s.pinned[0].children[0].label == "My Docs"
    assert again.split_of(again.find_tab(a.id)) is not None


def test_corrupt_session_is_kept_aside_and_replaced(data_dir):
    path = storage.session_path()
    path.write_text("{not json")
    fresh = Store(path)
    assert fresh.is_new
    assert path.with_suffix(".corrupt.json").exists()


def test_old_sessions_with_emoji_key_still_load(data_dir):
    from browser.models import Space
    space = Space.from_dict({"name": "Games", "color": "#f43f5e", "emoji": "🎮"})
    assert space.icon == "🎮"
    assert "emoji" not in space.to_dict() and space.to_dict()["icon"] == "🎮"


def test_space_icons_render_for_symbols_emoji_and_none():
    from PyQt6.QtGui import QColor
    from browser.icons import space_icon
    from browser.models import Space
    for icon in ("ph.planet", "🎮", "", "ph.not-a-real-icon"):
        assert not space_icon(Space("S", "#3b82f6", icon), QColor("white")).isNull()


def test_a_tabs_scroll_position_is_saved_and_a_bad_one_ignored():
    tab = Tab("https://example.com/long", "Long")
    assert "scroll" not in tab.to_dict()  # (nothing saved for a page at the top)
    tab.scroll = {"url": "https://example.com/long", "x": 0, "y": 1800}
    assert Tab.from_dict(tab.to_dict()).scroll == tab.scroll
    assert Tab.from_dict({"url": "https://example.com", "scroll": "nonsense"}).scroll is None
    assert Tab.from_dict({"url": "https://example.com", "scroll": {"y": "lots"}}).scroll is None


def test_a_tab_kept_awake_stays_that_way_after_a_restart():
    tab = Tab("https://example.com/dashboard", "Dashboard")
    assert "awake" not in tab.to_dict()
    tab.awake = True
    assert Tab.from_dict(tab.to_dict()).awake is True
    assert Tab.from_dict({"url": "https://example.com", "awake": "yes"}).awake is False


def test_put_back_restores_tabs_into_their_folders(store):
    from browser.models import node_path
    space = store.active_space
    space.today[:] = []
    a, b, c, d = (Tab(f"https://{n}.com") for n in "abcd")
    for tab in (d, c, b, a):
        store.add_tab(tab)
    folder = store.add_folder(space, "Reading", kind=TODAY)
    store.move_tab(folder, TODAY, index=0)
    store.move_tab(a, TODAY, folder=folder)
    store.move_tab(b, TODAY, folder=folder)
    assert space.today == [folder, c, d] and folder.children == [a, b]
    closing = [a, b, c]
    paths = {t.id: node_path(space.today, t, leaving=closing) for t in closing}
    assert paths[b.id] == [(0, folder, d), (1, b, None)] and paths[c.id] == [(1, c, d)]
    for tab in closing:  # the folder goes with its last tab
        store.remove_tab(tab)
    assert space.today == [d]
    new = Tab("https://new.com")
    store.add_tab(new)  # opened on top since
    for tab in closing:
        store.put_back(space, paths[tab.id], Tab.from_dict(tab.to_dict()))
    assert [n.name if isinstance(n, Folder) else n.url for n in space.today] == ["https://new.com", "Reading", "https://c.com",
                                                                 "https://d.com"]
    assert [t.url for t in folder.children] == ["https://a.com", "https://b.com"]


def test_put_back_after_another_tab_closed_since(store):
    from browser.models import node_path
    space = store.active_space
    space.today[:] = []
    a, b, c, d = (Tab(f"https://{n}.com") for n in "abcd")
    for tab in (d, c, b, a):
        store.add_tab(tab)
    paths = {t.id: node_path(space.today, t, leaving=[b, c]) for t in (b, c)}
    store.remove_tab(b)
    store.remove_tab(c)
    store.remove_tab(a)  # closed on its own afterwards
    for tab in (b, c):
        store.put_back(space, paths[tab.id], tab)
    assert space.today == [b, c, d]


def test_put_back_when_the_folder_moved_away(store):
    from browser.models import node_path
    space = store.active_space
    a, b = Tab("https://a.com"), Tab("https://b.com")
    store.add_tab(a)
    store.add_tab(b)
    folder = store.add_folder(space, "F", kind=TODAY)
    store.move_tab(a, TODAY, folder=folder)
    store.move_tab(b, TODAY, folder=folder)
    path = node_path(space.today, a, leaving=[a])
    store.remove_tab(a)
    store.move_tab(folder, PINNED)  # (moved to Pinned since)
    store.put_back(space, path, a)
    assert space.today[0] is a and a not in folder.children


def test_moving_the_last_tab_out_of_a_today_folder_takes_the_folder_away(store):
    space = store.active_space
    space.today[:] = []
    a, b = Tab("https://a.com"), Tab("https://b.com")
    store.add_tab(a)
    store.add_tab(b)
    folder = store.add_folder(space, "Reading", kind=TODAY)
    store.move_tab(a, TODAY, folder=folder)
    store.move_tab(b, TODAY, folder=folder)
    store.move_tab(a, PINNED)
    assert folder in space.today  # (still has b)
    store.move_tab(b, TODAY, index=0)
    assert folder not in space.today and space.today == [b]


def test_a_folder_moving_between_pinned_and_today_changes_its_tabs(store):
    space = store.active_space
    a, b = Tab("https://a.com/x"), Tab("https://b.com/y")
    store.add_tab(a)
    store.add_tab(b)
    folder = store.add_folder(space, "Group", kind=TODAY)
    store.move_tab(a, TODAY, folder=folder)
    store.move_tab(b, TODAY, folder=folder)
    store.move_tab(folder, PINNED)
    assert [t.home_url for t in folder.children] == ["https://a.com/x", "https://b.com/y"]  # (pinned: a home page)
    a.custom_title = "Mine"
    store.move_tab(folder, TODAY)
    assert [t.home_url for t in folder.children] == [None, None] and a.custom_title is None
