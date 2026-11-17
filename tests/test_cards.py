# Payment cards (cards.py), with a stand-in Keychain: Stripe's published test numbers, never a real card.

import pytest
from PyQt6.QtCore import QUrl

from browser import cards


@pytest.fixture
def vault(data_dir, monkeypatch):
    from browser import password_store
    kept = {}
    monkeypatch.setattr(password_store.secure_store, "store", lambda a, secret: kept.__setitem__(a, secret) or True)
    monkeypatch.setattr(password_store.secure_store, "load", lambda a: kept.get(a))
    monkeypatch.setattr(password_store.secure_store, "delete", lambda a: kept.pop(a, None))
    return kept


def test_numbers_brands_and_expiry():
    assert cards.valid_number("4242 4242 4242 4242") and cards.valid_number("378282246310005")
    assert not cards.valid_number("4242 4242 4242 4241") and not cards.valid_number("1234")
    assert [cards.brand(n) for n in ("4242424242424242", "5555555555554444", "2223003122003222", "378282246310005",
                                     "6011111111111117")] == ["Visa", "Mastercard", "Mastercard", "American Express",
                                                              "Discover"]
    import time
    assert cards.expired({"month": 1, "year": 2020}) and not cards.expired({"month": 12, "year": time.localtime().tm_year + 1})


def test_cards_are_kept_encrypted_without_a_security_code(vault):
    card = cards.save({"number": "4242-4242-4242-4242", "name": " Arkan Example ", "month": "4", "year": "31",
                       "cvc": "123"})
    raw = (cards.storage.data_dir() / "cards.json").read_text()
    assert "4242" not in raw and "Arkan" not in raw  # (sealed)
    assert cards.all_cards() == [card] and (card["year"], card["month"], card["name"]) == (2031, 4, "Arkan Example")
    assert "cvc" not in card and "csc" not in card
    assert cards.known("4242 4242 4242 4242") and not cards.known("5555555555554444")
    assert cards.summaries()[0]["last4"] == "4242" and "number" not in cards.summaries()[0]
    assert cards.save({"number": "4242424242424242", "month": "5", "year": "2032"})["id"] == card["id"]  # (the same card)
    with pytest.raises(ValueError):
        cards.save({"number": "4242424242424241"})
    cards.delete(card["id"])
    assert cards.all_cards() == []


def test_cards_are_locked_without_their_key(vault):
    cards.save({"number": "4242424242424242", "month": "4", "year": "2031"})
    vault.clear()  # (another Mac's copy of the folder: no key here)
    with pytest.raises(cards.Locked):
        cards.all_cards()
    assert cards.known("4242424242424242")  # (can't tell: never offered to save over a locked store)


def test_only_the_pages_site_or_a_payment_companys_frame_is_filled():
    page = QUrl("https://shop.example.com/checkout")
    assert cards.frame_allowed(page, QUrl("https://js.stripe.com/v3/elements-inner-card.html"))
    assert cards.frame_allowed(page, QUrl("https://pay.example.com/card"))
    assert not cards.frame_allowed(page, QUrl("https://ads.tracker.net/frame"))
    assert not cards.frame_allowed(page, QUrl("http://js.stripe.com/v3/"))  # (only https)
    assert not cards.payment_frame("stripe.com.evil.net") and cards.payment_frame("m.stripe.network")
