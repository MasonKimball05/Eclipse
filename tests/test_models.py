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


def test_folders_only_live_in_pinned(store):
    folder = store.add_folder(store.active_space, "F")
    store.move_tab(folder, TODAY)
    store.move_tab(folder, FAVORITES)
    assert folder in store.active_space.pinned


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
    stale = [t for t, _ in store.stale_today_tabs(exclude=current)]
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
