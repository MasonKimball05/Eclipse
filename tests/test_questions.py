from browser.ai import looks_like_question


def test_questions():
    assert looks_like_question("who owns this?")
    assert looks_like_question("what is the plot")
    assert looks_like_question("summarize")
    assert not looks_like_question("python docs")
    assert not looks_like_question("?")

