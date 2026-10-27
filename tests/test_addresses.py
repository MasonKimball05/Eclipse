"""Addresses: kept privately, and shaped for forms."""

import stat

from browser import addresses


def test_saving_changing_and_removing(data_dir):
    home = addresses.save({"label": "Home", "name": "Arkan Example", "street": "1 Main St\nApt 4",
                           "city": "Springfield", "email": "arkan@example.com", "unknown": "ignored"})
    assert [a["label"] for a in addresses.all_addresses()] == ["Home"] and "unknown" not in home
    assert stat.S_IMODE(addresses._path().stat().st_mode) == 0o600
    addresses.save({**home, "city": "Shelbyville"}, home["id"])
    assert [a["city"] for a in addresses.all_addresses()] == ["Shelbyville"]
    addresses.delete(home["id"])
    assert addresses.all_addresses() == []


def test_shaped_for_a_forms_fields():
    page = addresses.for_page({"name": "Arkan Q. Example", "street": "1 Main St\n Apt 4 \n\nBack door",
                               "country": "United States"})
    assert (page["given"], page["family"]) == ("Arkan", "Q. Example")
    assert (page["line1"], page["line2"], page["street"]) == ("1 Main St", "Apt 4, Back door",
                                                              "1 Main St, Apt 4, Back door")
    assert addresses.summary({"label": "Work", "name": "Arkan", "street": "2 Office Rd\nFloor 3"}) == \
        "Work · Arkan, 2 Office Rd"
    assert addresses.summary({"email": "a@example.com"}) == "a@example.com"
