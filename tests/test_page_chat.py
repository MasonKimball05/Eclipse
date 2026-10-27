from browser.page_chat import prompt_for, transcript


def test_the_prompt_carries_the_page_and_the_conversation_so_far():
    prompt = prompt_for("Rust 1.80", "https://blog.rust-lang.org/x", "The release adds LazyCell.",
                        [["What's new?", "LazyCell."], ["Unanswered", ""]], "Is it stable?")
    assert "<page>" in prompt and "The release adds LazyCell." in prompt
    assert "Question: What's new?\nAnswer: LazyCell." in prompt and "Unanswered" not in prompt
    assert prompt.rstrip().endswith("Question: Is it stable?")
    many = [[f"q{i}", f"a{i}"] for i in range(10)]
    assert "q0" not in prompt_for("t", "u", "x", many, "next") and "q9" in prompt_for("t", "u", "x", many, "next")


def test_the_transcript_shows_each_question_and_its_answer():
    text = transcript([["Summarize", "- one\n- two"], ["Who wrote it?", ""]])
    assert text.startswith("**Summarize**\n\n- one") and "**Who wrote it?**\n\n*Reading the page…*" in text
    assert transcript([]) == ""
