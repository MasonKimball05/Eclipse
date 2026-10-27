"""Storage."""

from browser import storage


def test_write_json_is_atomic(data_dir):
    path = storage.data_dir() / "x.json"
    storage.write_json(path, {"a": 1})
    assert storage.read_json(path) == {"a": 1}
    assert not path.with_suffix(".tmp").exists()
