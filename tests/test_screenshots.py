from browser.screenshots import frame_rows


def test_one_screenful_when_the_page_fits():
    assert frame_rows(500, 800) == [0]
    assert frame_rows(800, 800) == [0]


def test_screenfuls_cover_the_page_and_the_last_ends_at_the_bottom():
    assert frame_rows(2500, 800) == [0, 800, 1600, 1700]
    assert frame_rows(1600, 800) == [0, 800]
