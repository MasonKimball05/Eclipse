# Small forms (Space Rules, a recipe, a Boost) drawn natively, as a sheet on the browser window
# (native/Sources/Form.swift), where native pages are built. Python describes the form: sections of fields and the
# buttons along the bottom. A button (other than Cancel) comes back with every field's value, to on_button(id,
# values), which returns None to close the form or a message to show on it (and keep it open).
#
# open_form returns None where native pages aren't available, and the caller shows its Qt dialog instead.

from PyQt6.QtCore import QObject, QTimer

from . import native

DAYS = ["M", "T", "W", "T", "F", "S", "S"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def native_page_of(widget):
    """The native window a hidden Qt one stands behind (Settings, the Library), for a form to be a sheet on; else
    None (the browser window)."""
    page = getattr(widget.window(), "native_page", None) if widget is not None else None
    return page if page is not None and page.is_open() else None


def section(*fields, header="", footer="") -> dict:
    return {"header": header, "footer": footer, "fields": [f for f in fields if f]}


def save_buttons(delete: str = "", confirm: str = "") -> list[dict]:
    """Cancel and Save, and a destructive button (asking `confirm` first, if given) when `delete` is its label."""
    buttons = [{"id": "cancel", "label": "Cancel", "role": "cancel"}, {"id": "save", "label": "Save", "role": "default"}]
    if delete:
        buttons.insert(0, {"id": "delete", "label": delete, "role": "destructive", "confirm": confirm})
    return buttons


_answering: set = set()  # (forms whose button is being answered, till their sheet's away: prompts.py waits)


def answering() -> bool:
    return bool(_answering)


class NativeForm(QObject):
    def __init__(self, browser, spec: dict, on_button, on_closed=None, on_field=None, on_change=None):
        super().__init__(browser)
        self.on_button = on_button
        self.on_closed = on_closed
        self.on_field = on_field
        self.on_change = on_change
        self.saved = False  # (a button other than Cancel answered, and it closed)
        self._sets = 0
        self.page = native.NativePage(spec, self)
        self.page.action.connect(self._action)
        self.page.closed.connect(self._closed)
        self._inputs = 0

    def _action(self, name, event):
        if name == "changed" and self.on_change is not None:
            self.on_change(event.get("values") or {})
            return
        if name == "field" and self.on_field is not None:
            self.on_field(self, event.get("id", ""), event.get("values") or {})
            return
        if name != "button":
            return
        from . import prompts
        _answering.add(self)
        try:
            problem = self.on_button(event.get("id", ""), event.get("values") or {})
        except BaseException:
            _answering.discard(self)
            raise
        if problem:
            _answering.discard(self)
            self.page.update({"error": problem})
            prompts.run_waiting()
        else:
            self.saved = True
            self.page.close()  # (still answering till it's closed: _closed)
            QTimer.singleShot(2000, self._stop_answering)  # (should it never say so, questions aren't held for good)

    def _stop_answering(self):
        if self in _answering:
            _answering.discard(self)
            from . import prompts
            prompts.run_waiting()

    def _closed(self):
        from . import prompts
        _answering.discard(self)
        if self.on_closed is not None:
            self.on_closed()
        self.deleteLater()
        prompts.run_waiting()

    def set(self, values: dict):
        """Fields filled in from here (a suggested password, say)."""
        self._sets += 1
        self.page.update({"set": {"n": self._sets, "values": values}})

    def is_open(self) -> bool:
        return self.page.is_open()

    def close(self):
        self.page.close()

    # --- For tests ---------------------------------------------------------------------------------------------

    def fill(self, values: dict, press: str = ""):
        """Fields set as if typed, then a button pressed (its confirmation, if it asks one, answered yes)."""
        self._inputs += 1
        self.page.update({"test_input": {"n": self._inputs, "values": values, "press": press}})


def open_form(browser, title: str, sections: list[dict], buttons: list[dict], on_button, *, heading: str | None = None,
              width: int = 480, height: int = 420, on_closed=None, on_page=None, on_field=None, on_change=None,
              popover_at=None, popover_beside=None, images=None) -> NativeForm | None:
    """`on_page`: a native page (NativePage) to be a sheet on, instead of the browser window. `on_field(form, id,
    values)`: an action field's button pressed. `on_change(values)`: each change as it's made (a live form).
    `popover_at`: a point on the browser window (global) to open from, as a popover. `images`: {key: QIcon or
    QPixmap, or a PNG already made} for choice fields. `popover_beside`: a rect (global) the popover opens beside, to
    its right, pointing at it (the Space's name), rather than from popover_at."""
    if not native.available():
        return None
    spec = {"page": "form", "title": title, "heading": title if heading is None else heading, "sections": sections, "buttons": buttons,
            "width": width, "height": height, "min_width": min(width, 420), "min_height": min(height, 300),
            **native.theme_state(browser.theme)}
    if on_change is not None:
        spec["live"] = True
    if images:
        spec["icons_add"] = {key: image if isinstance(image, str) else
                             native.png(image.pixmap(36, 36) if hasattr(image, "pixmap") else image)
                             for key, image in images.items()}
    if popover_at is not None and browser.isVisible():
        here = browser.mapFromGlobal(popover_at)
        spec.update(popover_view=int(browser.winId()), popover_at=[here.x(), here.y()], sheet=True)
        if popover_beside is not None:
            corner = browser.mapFromGlobal(popover_beside.topLeft())
            spec["popover_rect"] = [corner.x(), corner.y(), popover_beside.width(), popover_beside.height()]
    elif on_page is not None and on_page.is_open():
        spec.update(sheet_page=on_page.id, sheet=True)
    elif browser.isVisible():  # (a sheet on its window; else a window of its own)
        spec.update(sheet_view=int(browser.winId()), sheet=True)
    form = NativeForm(browser, spec, on_button, on_closed, on_field, on_change)
    return form if form.page.id else None
