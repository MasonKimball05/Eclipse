# Dragging tabs to rearrange them in the native sidebar (native/Sources/SidebarBody.swift: SidebarDrops). SwiftUI's
# own drops never reached a row in a view inside Qt's window, so the sidebar view hands a dragged tab to the drop spot
# it's over itself; these drop tabs at real rows' places (the "drop_test" hook), as a drag would.
# Run with:  ./run.sh --live-tests

import ctypes

import pytest

from tests.live.test_spaces_benchmark import wait_until


def _native(window, monkeypatch):
    from browser import sidebar_native
    monkeypatch.setenv("ECLIPSE_NATIVE", "1")
    if not sidebar_native.available():
        pytest.skip("the native sidebar needs macOS 14 and the native pages built")
    window.resize(1100, 760)
    window.show()
    window.sidebar.use_native(True)
    return window.sidebar.native


def _ask(body, test):
    """The drop_test hook's answer to `test` (spots, or a drop)."""
    answers = []
    catch = lambda name, event: answers.append(event) if name == "drop_test" else None  # noqa: E731
    body.page.action.connect(catch)
    body.page.update({"drop_test": test})
    assert wait_until(lambda: answers, 2), "no answer from the sidebar"
    body.page.action.disconnect(catch)
    body.page.update({"drop_test": None})
    wait_until(lambda: False, 0.1)
    return answers[0]


def _drop(body, ids, spot, where):
    """`ids` dropped on `spot` ([x, y, w, h]): "top", "bottom" or "middle" of it."""
    x, y, w, h = spot
    at = {"top": 0.2, "middle": 0.5, "bottom": 0.8}[where]
    size = body.container.size()
    return _ask(body, {"ids": ids, "x": x + w / 2, "y": y + h * at, "width": size.width(), "height": size.height()})


def test_a_dragged_tab_drops_where_the_line_says(window, monkeypatch):
    from browser.models import Folder, Tab
    store = window.store
    space = store.active_space
    a, b, c = (Tab(f"about:blank#{n}", f"Tab {n}") for n in "abc")
    inside = Tab("about:blank#in", "Inside")
    folder = Folder("Reading", [inside])
    space.pinned = [a, b, c, folder]
    space.today = [Tab("about:blank#t1", "Today 1"), Tab("about:blank#t2", "Today 2")]
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    # Every row's a drop spot, where it is in the sidebar (the Space showing; its neighbours' are off to the side)
    spots = _ask(body, {"spots": True})["spots"]
    row = lambda tab: spots[f"row {space.id} {tab.id}"]  # noqa: E731
    for tab in (a, b, c, folder):
        assert row(tab)[2] > 100 and 20 <= row(tab)[3] <= 40, (tab.title, row(tab))
    assert row(a)[1] < row(b)[1] < row(c)[1] < row(folder)[1]  # (top to bottom, in order)
    # The bottom half of a row: after it
    answer = _drop(body, a.id, row(c), "bottom")
    assert answer["over"] is True and answer["dropped"] is True, answer
    assert wait_until(lambda: space.pinned[:3] == [b, c, a], 2), [n.title for n in space.pinned]
    # The top half: before it
    spots = _ask(body, {"spots": True})["spots"]
    _drop(body, a.id, row(b), "top")
    assert wait_until(lambda: space.pinned[:3] == [a, b, c], 2), [n.title for n in space.pinned]
    # The middle of a folder: into it, at its end
    spots = _ask(body, {"spots": True})["spots"]
    _drop(body, c.id, row(folder), "middle")
    assert wait_until(lambda: folder.children == [inside, c], 2), [n.title for n in folder.children]
    # From Pinned to Today
    spots = _ask(body, {"spots": True})["spots"]
    first_today = space.today[0]
    _drop(body, b.id, row(first_today), "top")
    assert wait_until(lambda: space.today[0] is b and b not in space.pinned, 2)
    # Several picked, dragged together: all of them, in order
    spots = _ask(body, {"spots": True})["spots"]
    t1, t2 = [t for t in space.today if t is not b]
    _drop(body, f"{t1.id},{t2.id}", row(a), "top")
    assert wait_until(lambda: space.pinned[:2] == [t1, t2], 2), [n.title for n in space.pinned]
    # Out of Pinned, into the empty sidebar below everything (Today empty, as it often is): the end of Today, as in
    # Arc (it had nowhere to go)
    space.today = []
    store._changed()
    wait_until(lambda: False, 0.4)
    size = body.container.size()
    answer = _ask(body, {"ids": a.id, "x": 40, "y": size.height() - 20, "width": size.width(), "height": size.height()})
    assert answer["dropped"] is True
    assert wait_until(lambda: space.today == [a] and a not in space.pinned, 2), [n.title for n in space.today]
    # On New Tab: first in Today
    spots = _ask(body, {"spots": True})["spots"]
    new_tab = spots[f"at {space.id} new tab"]
    _drop(body, t1.id, new_tab, "middle")
    assert wait_until(lambda: space.today[:2] == [t1, a], 2), [n.title for n in space.today]


def test_the_sidebar_view_asks_for_dragged_tabs(window, monkeypatch):
    """Dragged tabs are offered to the native sidebar's view (it hands them to its rows): SwiftUI's view never asked
    for them in Qt's window, and they went past it to Qt's beneath."""
    from browser import macos
    body = _native(window, monkeypatch)
    view = ctypes.c_void_p(body.page.view())
    types = macos._send(view, "registeredDraggedTypes")
    registered = {ctypes.string_at(macos._send(macos._send(types, "objectAtIndex:", i, argtypes=(ctypes.c_ulong,)),
                                               "UTF8String")).decode()
                  for i in range(macos._send(types, "count", restype=ctypes.c_ulong))}
    assert {"com.trolltech.anymime.application--x-eclipse-tab",
            "com.trolltech.anymime.application--x-eclipse-tabs"} <= registered
    sel = macos._objc.sel_registerName
    for name in (b"draggingUpdated:", b"performDragOperation:"):
        assert macos._send(view, "respondsToSelector:", sel(name), restype=ctypes.c_bool, argtypes=(ctypes.c_void_p,))


def test_the_rows_make_room_where_a_dragged_tab_would_go(window, monkeypatch):
    """Rather than a line, the rows part where it'd go (a row's room), and the tab being carried fades where it was;
    both come back once it's dropped or the drag ends."""
    from browser.models import Tab
    store = window.store
    space = store.active_space
    tabs = [Tab(f"about:blank#{n}", f"Tab {n}") for n in range(3)]
    space.pinned, space.today = list(tabs), []
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    spots = _ask(body, {"spots": True})["spots"]
    before = spots[f"row {space.id} {tabs[2].id}"]
    # Over the top of the last row (held there: a hover, no drop): it makes room above itself
    x, y, w, h = before
    size = body.container.size()
    answer = _ask(body, {"hover_only": True, "x": x + w / 2, "y": y + h * 0.2, "width": size.width(),
                         "height": size.height()})
    assert answer["over"] is True
    wait_until(lambda: False, 0.4)
    # (the room's made on screen, the rows below sliding down: where they're laid out, what a drop's judged by,
    # doesn't move, so the spot under the pointer can't shift with it)
    after = _ask(body, {"spots": True})["spots"][f"row {space.id} {tabs[2].id}"]
    assert after == before, (before, after)
    answer = _ask(body, {"hover_only": True, "x": x + w / 2, "y": y + h * 0.2, "width": size.width(),
                         "height": size.height()})
    assert answer["over"] is True  # (held there still: the same spot, the same room)
    # The carried tab fades where it was, and comes back when the drag's over
    body.page.update({"dragging": tabs[0].id})
    assert body.page.state()["dragging"] == tabs[0].id
    body._drag_check()  # (no button down: the drag's over)
    assert body.page.state()["dragging"] == ""


def test_folders_take_a_tab_across_their_middle_and_spring_open(window, monkeypatch):
    """A folder's middle third was too thin a band to find: its middle half drops into it now, and a closed folder a
    tab's held over opens after a moment (as Arc's do), to drop it among what's inside."""
    from browser.models import Folder, Tab
    store = window.store
    space = store.active_space
    inside = Tab("about:blank#in", "Inside")
    folder = Folder("Reading", [inside], expanded=False)
    a = Tab("about:blank#a", "Tab a")
    space.pinned, space.today = [folder], [a]
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    spots = _ask(body, {"spots": True})["spots"]
    x, y, w, h = spots[f"row {space.id} {folder.id}"]
    size = body.container.size()
    # Held over its middle, closed: outlined, then it opens
    answer = _ask(body, {"hover_only": True, "x": x + w / 2, "y": y + h * 0.5, "width": size.width(),
                         "height": size.height()})
    assert answer["over"] is True
    assert wait_until(lambda: folder.expanded, 2), "the folder didn't open"
    # 30% down it (outside the old middle third): still into it
    folder.expanded = False
    store._changed()
    wait_until(lambda: False, 0.4)
    answer = _ask(body, {"ids": a.id, "x": x + w / 2, "y": y + h * 0.3, "width": size.width(), "height": size.height()})
    assert answer["dropped"] is True
    assert wait_until(lambda: folder.children == [inside, a], 2), [n.title for n in folder.children]


def test_a_tab_pinned_from_the_start_drags_back_out_to_today(window, monkeypatch):
    """With Today empty, New Tab and the end of Today were the same spot to the sidebar (both "Today, first"), each
    putting its place over the other's: the room showed by New Tab, and a drop there or below did nothing."""
    from browser.models import Tab
    store = window.store
    space = store.active_space
    pinned = [Tab(f"https://example.com/{n}", f"Pinned {n}", home_url=f"https://example.com/{n}") for n in range(3)]
    space.pinned, space.today = list(pinned), []
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    spots = _ask(body, {"spots": True})["spots"]
    new_tab, end = spots[f"at {space.id} new tab"], spots[f"at {space.id} end"]
    assert new_tab != end and new_tab[1] < end[1], (new_tab, end)  # (two spots, New Tab above the end of Today)
    # On New Tab
    answer = _drop(body, pinned[2].id, new_tab, "middle")
    assert answer["dropped"] is True
    assert wait_until(lambda: space.today == [pinned[2]] and pinned[2].home_url is None, 2)
    # Below it, at the end of Today
    spots = _ask(body, {"spots": True})["spots"]
    _drop(body, pinned[1].id, spots[f"at {space.id} end"], "middle")
    assert wait_until(lambda: space.today == [pinned[2], pinned[1]], 2), [n.title for n in space.today]
    # Further down, in the empty sidebar below everything
    size = body.container.size()
    _ask(body, {"ids": pinned[0].id, "x": 40, "y": size.height() - 10, "width": size.width(), "height": size.height()})
    assert wait_until(lambda: space.today[-1] is pinned[0] and not space.pinned, 2)


def test_the_room_never_stays_open_after_a_drag(window, monkeypatch):
    """A drop macOS called off (SwiftUI's view said it wasn't ready) left the room open and the rows' places frozen:
    the next drop landed by where they'd been. However a drag ends, the room closes and the places follow again."""
    from browser.models import Tab
    store = window.store
    space = store.active_space
    tabs = [Tab(f"about:blank#{n}", f"Tab {n}") for n in range(3)]
    space.pinned, space.today = list(tabs), []
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    spots = _ask(body, {"spots": True})["spots"]
    x, y, w, h = spots[f"row {space.id} {tabs[1].id}"]
    size = body.container.size()
    body.page.update({"dragging": tabs[0].id})
    assert _ask(body, {"hover_only": True, "x": x + w / 2, "y": y + h * 0.2, "width": size.width(),
                       "height": size.height()})["over"] is True
    # The drag ends without a drop here (the button's up: Python clears "dragging")
    body._drag_check()
    wait_until(lambda: False, 0.3)
    # The layout changes: the places follow it (frozen, the last row would still be where it was)
    store.move_tab(tabs[2], "pinned", space, 0)
    wait_until(lambda: False, 0.5)
    spots = _ask(body, {"spots": True})["spots"]
    assert spots[f"row {space.id} {tabs[2].id}"][1] < spots[f"row {space.id} {tabs[0].id}"][1]


def _drag(body, ids, start, end, steps=6):
    """`ids` dragged from `start` to `end` ((x, y) in the sidebar, where they're drawn), through everything macOS
    sends a view, carried as a real drag's are (faded where it was)."""
    body.page.update({"dragging": ids.split(",")[0]})
    path = [[start[0] + (end[0] - start[0]) * i / steps, start[1] + (end[1] - start[1]) * i / steps]
            for i in range(steps + 1)]
    return _ask(body, {"ids": ids, "path": path})


def test_a_whole_drag_out_of_pinned_as_macos_sends_it(window, monkeypatch):
    """Through the sidebar view's own handlers, in the order macOS calls them (in, along, ready?, drop, done, over):
    a pinned tab dragged down onto New Tab, below it, and into the empty sidebar each goes to Today, and the room
    New Tab makes for it opens under it (Today's tabs come after it), not over it."""
    from browser.models import Folder, Tab
    store = window.store
    space = store.active_space
    pinned = [Tab(f"https://example.com/{n}", f"Pinned {n}", home_url=f"https://example.com/{n}") for n in range(4)]
    space.pinned = [Folder("F", [Tab("about:blank#x", "x")], expanded=False)] + list(pinned)
    space.today = []
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    size = body.container.size()

    def spots():
        return _ask(body, {"spots": True})["spots"]

    def centre(rect, at=0.5):
        return rect[0] + rect[2] / 2, rect[1] + rect[3] * at
    # Onto New Tab
    s = spots()
    result = _drag(body, pinned[3].id, centre(s[f"row {space.id} {pinned[3].id}"]), centre(s[f"at {space.id} new tab"]))
    assert result["ready"] is True and result["performed"] is True, result
    assert wait_until(lambda: space.today == [pinned[3]], 2), [t.title for t in space.today]
    # Below New Tab, the end of Today (aimed at where it's drawn: a row lower, the room New Tab makes above it)
    s = spots()
    end_x, end_y = centre(s[f"at {space.id} end"])
    result = _drag(body, pinned[2].id, centre(s[f"row {space.id} {pinned[2].id}"]), (end_x, end_y + 34))
    assert result["ready"] is True and result["performed"] is True, result
    assert wait_until(lambda: space.today == [pinned[3], pinned[2]], 2), [t.title for t in space.today]
    # Into the empty sidebar below everything
    s = spots()
    result = _drag(body, pinned[1].id, centre(s[f"row {space.id} {pinned[1].id}"]), (40, size.height() - 10))
    assert result["ready"] is True and result["performed"] is True, result
    assert wait_until(lambda: space.today[-1] is pinned[1], 2), [t.title for t in space.today]
    # Held over New Tab: the room's under it (New Tab itself doesn't move)
    s = spots()
    new_tab = s[f"at {space.id} new tab"]
    _ask(body, {"hover_only": True, "x": centre(new_tab)[0], "y": centre(new_tab)[1], "width": size.width(),
                "height": size.height()})
    rooms = _ask(body, {"room": True})
    assert abs(rooms["y"] - (new_tab[1] + new_tab[3])) < 1, (rooms, new_tab)


def test_the_last_pinned_tab_dragged_just_below_itself_leaves_pinned(window, monkeypatch):
    """Right under the last pinned tab was the divider, on its own the end of Pinned: a tab dragged a little way down
    landed back where it was. The divider's part of New Tab's place now (first in Today), and the tab being carried
    isn't a place to put itself."""
    from browser.models import Tab
    store = window.store
    space = store.active_space
    pinned = [Tab(f"https://example.com/{n}", f"Pinned {n}", home_url=f"https://example.com/{n}") for n in range(3)]
    space.pinned, space.today = list(pinned), []
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    s = _ask(body, {"spots": True})["spots"]
    last = s[f"row {space.id} {pinned[2].id}"]
    assert f"at {space.id} divider" not in s
    body.page.update({"dragging": pinned[2].id})
    x = last[0] + last[2] / 2
    size = body.container.size()
    # Over itself: nowhere to put it
    over = _ask(body, {"hover_only": True, "x": x, "y": last[1] + last[3] * 0.7, "width": size.width(),
                       "height": size.height()})
    assert over["over"] is False
    # A few points below itself (the divider): out of Pinned, first in Today
    result = _drag(body, pinned[2].id, (x, last[1] + last[3] / 2), (x, last[1] + last[3] + 4))
    assert result["performed"] is True, result
    assert wait_until(lambda: space.today == [pinned[2]] and space.pinned == pinned[:2], 2), \
        ([t.title for t in space.pinned], [t.title for t in space.today])


def test_past_an_open_folder_and_beside_a_live_one(window, monkeypatch):
    """The bottom of an open folder's last row, left of its indent, is after the folder (there was nowhere that
    meant that, past an open folder at the end of Pinned); over the indent it's inside, at the end. A Live Folder
    takes nothing in (its feed's): its middle is beside it, its open row's bottom half after it. And it's marked."""
    from browser.models import Folder, Tab
    store = window.store
    space = store.active_space
    a, b = Tab("about:blank#a", "Tab a"), Tab("about:blank#b", "Tab b")
    one, two = Tab("about:blank#1", "One"), Tab("about:blank#2", "Two")
    folder = Folder("Open folder", [one, two], expanded=True)
    space.pinned, space.today = [folder], [a, b]
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    s = _ask(body, {"spots": True})["spots"]
    last = s[f"row {space.id} {two.id}"]
    size = body.container.size()

    def drop(ids, rect, x, at):
        return _ask(body, {"ids": ids, "x": rect[0] + x, "y": rect[1] + rect[3] * at, "width": size.width(),
                           "height": size.height()})
    # Left of the indent, at the bottom of the last row inside: after the folder
    assert drop(a.id, last, 4, 0.8)["dropped"] is True
    assert wait_until(lambda: space.pinned == [folder, a] and folder.children == [one, two], 2), \
        [n.title if isinstance(n, Tab) else n.name for n in space.pinned]
    # Over the indent: inside, at the end
    s = _ask(body, {"spots": True})["spots"]
    drop(b.id, s[f"row {space.id} {two.id}"], last[2] / 2, 0.8)
    assert wait_until(lambda: folder.children == [one, two, b], 2), [n.title for n in folder.children]
    # A Live Folder: marked, and nothing goes in
    live = Folder("Feed", [Tab("https://example.com/x", "Item")], expanded=True,
                  live={"kind": "json", "url": "https://example.com/feed.json"})
    space.pinned.append(live)
    store._changed()
    wait_until(lambda: False, 0.5)
    shown = next(n for n in body.page.state()["spaces"] if n["id"] == space.id)["pinned"]
    mark = next(n for n in shown if n["id"] == live.id)
    assert mark["live"] is True and "Live Folder" in mark["live_tip"]
    assert next(n for n in shown if n["id"] == folder.id)["live"] is False
    s = _ask(body, {"spots": True})["spots"]
    row = s[f"row {space.id} {live.id}"]
    drop(a.id, row, row[2] / 2, 0.5)  # (its middle: beside it, not in)
    assert wait_until(lambda: space.pinned[-1] is a and a not in live.children, 2)
    s = _ask(body, {"spots": True})["spots"]
    row = s[f"row {space.id} {live.id}"]
    drop(b.id, row, row[2] / 2, 0.8)  # (its open row's bottom half: after it)
    assert wait_until(lambda: space.pinned[2:] == [b, a] and b not in live.children, 2), \
        [n.title if isinstance(n, Tab) else n.name for n in space.pinned]


def test_the_room_follows_the_pointer_as_it_moves_down(window, monkeypatch):
    """Below the room the rows are drawn a room lower than they're laid out: judged by the layout alone, the row
    drawn just below the room was taken for the next, so the room jumped a row further down than the pointer and back
    (flickering). Now it stays put while the pointer's in it, and follows it, row by row."""
    from browser.models import Tab
    store = window.store
    space = store.active_space
    tabs = [Tab(f"about:blank#{n}", f"Tab {n}") for n in range(5)]
    space.pinned, space.today = list(tabs), []
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    s = _ask(body, {"spots": True})["spots"]
    rows = [s[f"row {space.id} {t.id}"] for t in tabs]
    size = body.container.size()
    body.page.update({"dragging": tabs[4].id})

    def hover(y):
        _ask(body, {"hover_only": True, "x": rows[0][0] + rows[0][2] / 2, "y": y, "width": size.width(),
                    "height": size.height()})
        return _ask(body, {"room": True})["y"]
    # The top of row 1: room above it
    assert abs(hover(rows[1][1] + 6) - rows[1][1]) < 1
    # Into the room it opened (where row 1 was): it stays
    assert abs(hover(rows[1][1] + 20) - rows[1][1]) < 1
    # Onto row 1, now drawn a room lower: its top half still means above it (not the row after it)
    assert abs(hover(rows[1][1] + 34 + 6) - rows[1][1]) < 1
    # Its bottom half: below it, room between it and row 2
    assert abs(hover(rows[1][1] + 34 + 28) - rows[2][1]) < 1
    # And dropped there, it goes where the room was
    answer = _ask(body, {"ids": tabs[4].id, "x": rows[0][0] + rows[0][2] / 2, "y": rows[2][1] + 10,
                         "width": size.width(), "height": size.height()})
    assert answer["dropped"] is True
    assert wait_until(lambda: space.pinned.index(tabs[4]) == 2, 2), [t.title for t in space.pinned]


def test_a_folder_outlined_to_take_a_tab_leaves_the_room_where_it_is(window, monkeypatch):
    """Over a folder's middle the room closed (it's outlined instead): the rows moved back up, the folder slid out
    from under the pointer, the pointer was beside it again and the room opened again (it flickered). The room's left
    where it is now, from above the folder or below it, and a drop there goes in."""
    from browser.models import Folder, Tab
    store = window.store
    space = store.active_space
    top, inside, below, carried = (Tab(f"about:blank#{n}", f"Tab {n}") for n in ("top", "in", "below", "carried"))
    folder = Folder("Folder", [inside], expanded=False)
    space.pinned, space.today = [top, folder, below, carried], []
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    s = _ask(body, {"spots": True})["spots"]
    row = s[f"row {space.id} {folder.id}"]
    x = row[0] + row[2] / 2
    size = body.container.size()
    body.page.update({"dragging": carried.id})

    def hover(y):
        over = _ask(body, {"hover_only": True, "x": x, "y": y, "width": size.width(), "height": size.height()})
        return over["over"], _ask(body, {"room": True})["y"]
    # From above: the folder's top opens room above it (the folder's drawn a room lower)...
    assert hover(row[1] + 4)[1] == row[1]
    # ...and its middle, where it's drawn now, outlines it with the room left there, however long it's held
    for _ in range(3):
        assert hover(row[1] + 34 + row[3] / 2) == (True, row[1])
    # From below: the room under it stays as the pointer comes up onto its middle
    assert abs(hover(row[1] + 34 + row[3] - 3)[1] - (row[1] + row[3])) < 1  # (its bottom: room below it)
    for _ in range(3):
        over, room = hover(row[1] + row[3] / 2)
        assert over is True and abs(room - (row[1] + row[3])) < 1
    # Dropped there: into the folder
    answer = _ask(body, {"ids": carried.id, "x": x, "y": row[1] + row[3] / 2, "width": size.width(),
                         "height": size.height()})
    assert answer["dropped"] is True
    assert wait_until(lambda: carried in folder.children, 2), [n.title for n in folder.children]


def test_a_quick_drag_out_of_pinned_lands_where_it_was_let_go(window, monkeypatch):
    """The room's top half is where it was opened for, its bottom half what comes after it. Between two rows that's
    the same place; where Pinned ends, the room after the last pinned tab is the end of Pinned in its top half and
    Today in its bottom half (let go there, past the room, a tab dragged quickly out of Pinned went back in). And a
    quick drag between two rows lands where its room was (judged by the layout instead, it landed a row off, often
    right back where it started)."""
    from browser.models import Tab
    store = window.store
    space = store.active_space
    a, b, c, d = (Tab(f"https://example.com/{n}", f"Pinned {n}", home_url=f"https://example.com/{n}") for n in "abcd")
    space.pinned, space.today = [a, b, c], [d]
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    s = _ask(body, {"spots": True})["spots"]
    rb, rc = s[f"row {space.id} {b.id}"], s[f"row {space.id} {c.id}"]
    x = rc[0] + rc[2] / 2
    # Down over c's bottom half (room after it) and let go in the room's bottom half: Today
    result = _drag(body, b.id, (x, rb[1] + rb[3] / 2), (x, rc[1] + rc[3] + 26), steps=8)
    assert result["performed"] is True, result
    assert wait_until(lambda: space.today[0] is b, 2), ([t.title for t in space.pinned], [t.title for t in space.today])
    # The room at the end of Pinned is all Today's: let go in its top half, just past the last pinned tab, it's out
    s = _ask(body, {"spots": True})["spots"]
    rc = s[f"row {space.id} {c.id}"]
    size = body.container.size()
    body.page.update({"dragging": a.id})
    _ask(body, {"hover_only": True, "x": x, "y": rc[1] + rc[3] * 0.8, "width": size.width(), "height": size.height()})
    answer = _ask(body, {"ids": a.id, "x": x, "y": rc[1] + rc[3] + 4, "width": size.width(), "height": size.height()})
    assert answer["dropped"] is True
    assert wait_until(lambda: a in space.today and space.pinned == [c], 2), [t.title for t in space.pinned]
    # The end of Pinned is the last pinned tab's bottom half
    t = space.today[0]
    s = _ask(body, {"spots": True})["spots"]
    rc = s[f"row {space.id} {c.id}"]
    body.page.update({"dragging": t.id})
    answer = _ask(body, {"ids": t.id, "x": x, "y": rc[1] + rc[3] * 0.8, "width": size.width(), "height": size.height()})
    assert answer["dropped"] is True
    assert wait_until(lambda: space.pinned == [c, t], 2), [n.title for n in space.pinned]
    # Quickly up from Today, past d's own row, into the room above c: before c
    s = _ask(body, {"spots": True})["spots"]
    rd, rc = s[f"row {space.id} {d.id}"], s[f"row {space.id} {c.id}"]
    result = _drag(body, d.id, (x, rd[1] + rd[3] / 2), (x, rc[1] + 6), steps=10)
    assert result["performed"] is True, result
    assert wait_until(lambda: space.pinned[0] is d and space.pinned[1] is c, 2), [t.title for t in space.pinned]


def test_quick_drags_across_the_divider_land_on_the_side_they_were_let_go(window, monkeypatch):
    """Which side of the divider a drop is on goes by where the divider's laid out (it never moves while a tab's
    carried), not by rows still sliding to a room just opened: let go just past the last pinned tab in a quick drag,
    a tab went back into Pinned."""
    from browser.models import Folder, Tab
    store = window.store
    space = store.active_space
    body = None
    for offset in (0, 6, 14, 30, 60):  # (down, out of Pinned: let go this far past the last pinned tab)
        pinned = [Tab(f"https://example.com/p{n}", f"Pinned {n}", home_url=f"https://example.com/p{n}")
                  for n in range(4)]
        space.pinned = [Folder("F", [Tab("about:blank#x", "x")], expanded=False)] + pinned
        space.today = [Tab(f"https://example.com/t{n}", f"Today {n}") for n in range(2)]
        store._changed()
        body = body or _native(window, monkeypatch)
        wait_until(lambda: False, 0.5)
        s = _ask(body, {"spots": True})["spots"]
        src, last = s[f"row {space.id} {pinned[1].id}"], s[f"row {space.id} {pinned[3].id}"]
        x = src[0] + src[2] / 2
        _drag(body, pinned[1].id, (x, src[1] + src[3] / 2), (x, last[1] + last[3] + offset), steps=5)
        assert wait_until(lambda: pinned[1] in space.today, 2), (offset, "went back into Pinned")
        body.page.update({"dragging": ""})
    for offset in (2, 10, 20):  # (up, into Pinned: let go this far above the divider)
        pinned = [Tab(f"https://example.com/q{n}", f"Pinned {n}", home_url=f"https://example.com/q{n}")
                  for n in range(3)]
        today = [Tab(f"https://example.com/u{n}", f"Today {n}") for n in range(3)]
        space.pinned, space.today = list(pinned), list(today)
        store._changed()
        wait_until(lambda: False, 0.5)
        s = _ask(body, {"spots": True})["spots"]
        src, divider = s[f"row {space.id} {today[1].id}"], s[f"at {space.id} new tab"]
        x = src[0] + src[2] / 2
        _drag(body, today[1].id, (x, src[1] + src[3] / 2), (x, divider[1] - offset), steps=5)
        assert wait_until(lambda: today[1] in space.pinned, 2), (offset, "stayed in Today")
        body.page.update({"dragging": ""})


def test_a_quick_drop_moves_the_tab_carried_not_the_drag_befores(window, monkeypatch):
    """The drag pasteboard is every drag's, filled when it's read: at a quick drop it could still hold the drag
    before's tab, which was moved instead (often nowhere: the tab carried went back). A drag that started in the
    sidebar drops what the sidebar knows it's carrying."""
    from browser.models import Tab
    store = window.store
    space = store.active_space
    search = Tab("https://google.com/", "Search", home_url="https://google.com/")
    google = Tab("https://google.com/?q", "Google")
    space.pinned, space.today = [Tab("https://example.com/p", "Portfolio", home_url="https://example.com/p"), search], [google]
    store._changed()
    body = _native(window, monkeypatch)
    wait_until(lambda: False, 1.0)
    s = _ask(body, {"spots": True})["spots"]
    src, new_tab = s[f"row {space.id} {search.id}"], s[f"at {space.id} new tab"]
    x = src[0] + src[2] / 2
    body.page.update({"dragging": search.id})
    path = [[x, src[1] + src[3] / 2], [x, new_tab[1] + new_tab[3] / 2]]
    result = _ask(body, {"ids": search.id, "stale": google.id, "path": path})
    assert result["performed"] is True, result
    assert wait_until(lambda: search in space.today, 2), ([t.title for t in space.pinned], [t.title for t in space.today])


def test_a_drop_only_goes_to_the_space_showing(window, monkeypatch):
    """The other Spaces' pages, scrolled away, keep the places they had when they last showed, just where this one's
    are: a drop was taken for one of theirs, and nothing moved (Python won't move a tab into a Space not showing)."""
    from browser.models import Tab
    store = window.store
    while len(store.spaces) < 2:
        store.add_space(f"Space {len(store.spaces) + 1}", "#10b981")
    one, two = store.spaces[:2]
    mine = Tab("https://example.com/mine", "Mine", home_url="https://example.com/mine")
    one.pinned, one.today = [Tab("https://example.com/a", "A", home_url="https://example.com/a"), mine], []
    two.pinned, two.today = [Tab(f"https://example.com/b{n}", f"B {n}", home_url=f"https://example.com/b{n}")
                             for n in range(3)], []
    store._changed()
    body = _native(window, monkeypatch)
    window.switch_space(one)
    wait_until(lambda: False, 0.6)
    window.switch_space(two)  # (its page shows, and its places are taken)
    wait_until(lambda: False, 0.8)
    window.switch_space(one)
    wait_until(lambda: False, 0.8)
    s = _ask(body, {"spots": True})["spots"]
    src, new_tab = s[f"row {one.id} {mine.id}"], s[f"at {one.id} new tab"]
    x = src[0] + src[2] / 2
    # (as a page caught half on screen mid-swipe, then scrolled away, leaves them: another Space's spots over these)
    _ask(body, {"ghosts": True})
    for offset in (4, 20, 40):
        _drag(body, mine.id, (x, src[1] + src[3] / 2), (x, new_tab[1] + offset), steps=5)
        assert wait_until(lambda: mine in one.today, 2), (offset, [t.title for t in one.pinned])
        body.page.update({"dragging": ""})
        one.pinned, one.today = [one.pinned[0], mine], []
        store._changed()
        wait_until(lambda: False, 0.5)
