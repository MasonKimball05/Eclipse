

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


def test_choosing_another_model_lets_answers_being_written_finish(qapp):
    """Changing the model shut every Claude process down, so a Browse for Me run or a page answer being written
    stopped with "Claude stopped unexpectedly" (and the window froze a second for each). Only the waiting one goes."""
    from PyQt6.QtCore import QObject
    from browser import ai

    class Worker:
        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True
    claude = ai.Claude(QObject())
    waiting, busy = Worker(), Worker()
    claude._warm, claude._workers = waiting, {waiting, busy}
    claude.drop_warm()
    assert waiting.killed and not busy.killed and claude._warm is None and claude._workers == {busy}
