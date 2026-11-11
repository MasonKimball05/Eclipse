"""Storage."""

from browser import storage


def test_write_json_is_atomic(data_dir):
    path = storage.data_dir() / "x.json"
    storage.write_json(path, {"a": 1})
    assert storage.read_json(path) == {"a": 1}
    assert not path.with_suffix(".tmp").exists()


def test_a_second_corrupt_file_is_kept_beside_the_first(tmp_path):
    from browser import storage
    path = tmp_path / "session.json"
    for _ in range(2):
        path.write_text("{not json")
        assert storage.read_json(path) is None
    assert (tmp_path / "session.corrupt.json").exists() and (tmp_path / "session.corrupt-2.json").exists()


def test_site_data_asked_to_be_cleared_goes_before_the_profile_next_opens(tmp_path):
    """Clearing a profile only took its cookies: pages' own storage (logins kept there) is open while it runs."""
    from browser import storage
    profile = tmp_path / "Work"
    for name in ("Local Storage", "IndexedDB", "Service Worker", "cache", "Cookies"):
        (profile / name).mkdir(parents=True)
    assert not storage.clear_site_data_if_asked(profile)  # (nothing asked: nothing touched)
    assert (profile / "IndexedDB").exists()
    storage.ask_to_clear_site_data(profile)
    assert storage.clear_site_data_if_asked(profile)
    assert not any((profile / n).exists() for n in ("Local Storage", "IndexedDB", "Service Worker"))
    assert (profile / "cache").exists() and (profile / "Cookies").exists()  # (only what's site data)
    assert not storage.clear_site_data_if_asked(profile)  # (once)
