import json
from datetime import date

import browser.member_card as mc
from browser.member_card import (ARC_SETTING, PALETTES, SETTING, SHAPES, anniversaries, arc_card_for, card_for,
                                 card_image, random_title, read_arc_card)


def _arc(folder, card=None, user=None):
    if card is not None:
        (folder / "StorableMembershipCard.json").write_text(json.dumps(card))
    if user is not None:
        (folder / "StorableAuthState.json").write_text(json.dumps(
            {"session": {"user": user, "accessToken": "never-read"}, "version": 1}))


def test_an_arc_card_comes_over_as_it_was(tmp_path):
    _arc(tmp_path, {"title": "STATUESQUE TECHNICIAN", "shapeName": "Shape2", "paletteNumber": 3, "version": 1,
                    "backgroundBlobInfo": [4, 8, 9, 13, 8, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1]},
         {"name": "Arkan", "createdAt": 727845616.82, "email": "someone@example.com", "id": "x"})
    card = read_arc_card(tmp_path)
    assert card["name"] == "Arkan" and card["title"] == "STATUESQUE TECHNICIAN"
    assert card["palette"] == 3 and PALETTES[3]["name"] == "Sea Glass" and card["shape"] == "Shape2"
    assert card["since"] in ("2024-01-24", "2024-01-25") and card["origin"] == "arc"  # (the day, wherever you are)
    assert card["blob"][:3] == [4, 8, 9]
    assert "email" not in card and "never-read" not in json.dumps(card)


def test_no_arc_card_or_a_broken_one_is_none(tmp_path):
    assert read_arc_card(tmp_path) is None
    (tmp_path / "StorableMembershipCard.json").write_text("{not json")
    assert read_arc_card(tmp_path) is None
    _arc(tmp_path, {"title": "QUIET PILOT", "shapeName": "Shape99", "paletteNumber": 42})  # (no account file)
    card = read_arc_card(tmp_path)
    assert card["shape"] in SHAPES and 0 <= card["palette"] < len(PALETTES) and "name" not in card


def test_a_stamp_for_each_whole_year():
    assert anniversaries("2024-01-24", date(2026, 10, 26)) == 2
    assert anniversaries("2024-01-24", date(2025, 1, 23)) == 0
    assert anniversaries("2024-01-24", date(2025, 1, 24)) == 1
    assert anniversaries("nonsense") == 0


def test_a_card_is_made_once_and_kept(qapp, tmp_path, monkeypatch, data_dir):
    monkeypatch.setattr(mc, "ARC_DIR", tmp_path / "no-arc")  # (no Arc here)
    monkeypatch.setattr(mc, "_maker", False)
    settings = {}
    card = card_for(settings)
    assert settings[SETTING]["title"] == card["title"] and len(random_title().split()) == 2
    assert card_for(settings)["title"] == card["title"]
    image = card_image(card, 1)
    assert image.width() == 300 and image.height() == 450


def test_the_arc_card_is_kept_apart_and_survives_arc_going(qapp, tmp_path, monkeypatch, data_dir):
    folder = tmp_path / "Arc"
    folder.mkdir()
    _arc(folder, {"title": "STATUESQUE TECHNICIAN", "shapeName": "Shape2", "paletteNumber": 3},
         {"name": "Arkan", "createdAt": 727845616.82})
    monkeypatch.setattr(mc, "ARC_DIR", folder)
    monkeypatch.setattr(mc, "_maker", False)
    settings = {}
    arc = arc_card_for(settings)
    assert arc["title"] == "STATUESQUE TECHNICIAN" and settings[ARC_SETTING]["name"] == "Arkan"
    # The Eclipse card is a fresh one, under the same name
    card = card_for(settings)
    assert card["name"] == "Arkan" and card["origin"] == "" and card["since"] != arc["since"]
    # Arc deleted: the card's still here
    for f in folder.iterdir():
        f.unlink()
    assert arc_card_for(settings)["title"] == "STATUESQUE TECHNICIAN"
    # …and even with the settings reset, from its own copy in the data folder
    assert arc_card_for({})["name"] == "Arkan"
    (data_dir / mc.ARC_FILE).unlink()
    assert arc_card_for({}) is None


def test_the_arc_card_is_copied_out_of_arc_at_launch(qapp, tmp_path, monkeypatch, data_dir):
    folder = tmp_path / "Arc"
    folder.mkdir()
    _arc(folder, {"title": "STATUESQUE TECHNICIAN", "shapeName": "Shape2", "paletteNumber": 3},
         {"name": "Arkan", "createdAt": 727845616.82})
    monkeypatch.setattr(mc, "ARC_DIR", folder)
    saved = []
    store = type("S", (), {"settings": {}, "schedule_save": lambda self: saved.append(1)})()
    mc.CardsFeature(type("W", (), {"store": store})()).ready()  # (without Settings ever being opened)
    assert store.settings[ARC_SETTING]["title"] == "STATUESQUE TECHNICIAN" and saved
    assert (data_dir / mc.ARC_FILE).exists()


def test_absolute_eclipse_is_the_makers_alone(qapp, tmp_path, monkeypatch, data_dir):
    monkeypatch.setattr(mc, "ARC_DIR", tmp_path / "no-arc")
    monkeypatch.setattr(mc, "_maker", False)
    settings = {SETTING: {"name": "Someone", "title": "ABSOLUTE  eclipse"}}  # (however it's written)
    card = card_for(settings)
    assert card["title"] != mc.MAKER_TITLE and not mc.allowed_title("Absolute Eclipse")
    assert all(mc.random_title() != mc.MAKER_TITLE for _ in range(500))
    monkeypatch.setattr(mc, "_maker", True)
    card = card_for(settings)
    assert card["title"] == mc.MAKER_TITLE and card["maker"] and mc.allowed_title("Absolute Eclipse")
    assert card_image(card, 1).width() == 300
    # Only an email whose fingerprint matches is the maker's
    monkeypatch.setattr(mc, "_maker", None)
    monkeypatch.setattr(mc, "_git_email", lambda config: "someone@example.com")
    assert not mc.is_maker()


def test_the_email_is_read_from_gits_own_settings(tmp_path):
    config = tmp_path / "config"
    config.write_text('[core]\n\temail = not-this@example.com\n[user "x"]\n[user]\n\tname = Someone\n'
                      '\tEmail = Someone@Example.com  # a comment\n')
    assert mc._git_email(config) == "someone@example.com"
    assert mc._git_email(tmp_path / "missing") == ""


def test_an_eclipse_card_gets_its_own_look(qapp, tmp_path, monkeypatch, data_dir):
    monkeypatch.setattr(mc, "ARC_DIR", tmp_path / "no-arc")
    monkeypatch.setattr(mc, "_maker", False)
    card = card_for({})
    assert (card["palette"], card["shape"]) == (mc.ECLIPSE_LOOK["palette"], mc.ECLIPSE_LOOK["shape"])
    # One an early version made in your Arc card's colours and shape gets Eclipse's own, once
    arc = {"title": "STATUESQUE TECHNICIAN", "palette": 3, "shape": "Shape2", "origin": "arc"}
    settings = {ARC_SETTING: arc, SETTING: {"name": "Arkan", "title": "QUIET PILOT", "palette": 3, "shape": "Shape2"}}
    assert card_for(settings)["palette"] == mc.ECLIPSE_LOOK["palette"]
    settings[SETTING].update(palette=3, shape="Shape2")  # (chosen again since: kept)
    assert card_for(settings)["palette"] == 3
