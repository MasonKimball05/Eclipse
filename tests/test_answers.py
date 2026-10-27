

def test_percent_dates_phones_and_utc_offsets():
    import datetime
    from browser.answers import answer
    assert answer("20% * 50").text == "10" and answer("10 % 3").text == "1" and answer("12.5% of 80").text == "10"
    assert answer("100-58").text == "42"
    for not_a_sum in ("2024-10-26", "555-1234", "415-555-1234"):
        assert answer(not_a_sum) is None, not_a_sum
    noon_utc = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
    from browser.answers import world_time
    assert world_time("time in gmt+5", noon_utc).text.startswith("5:00 PM")  # (five hours ahead of UTC)
    assert world_time("time in utc-3:30", noon_utc).text.startswith("8:30 AM")
