import learning


def test_correction_learned_after_two_times():
    L = learning.Learner()
    assert L.observe_correction("I told dust in about it.", "I told Dustin about it.") == []
    assert L.observe_correction("Ask dust in tomorrow.", "Ask Dustin tomorrow.") == [("dust in", "Dustin")]
    assert L.fixes() == [("dust in", "Dustin")]


def test_content_changes_and_rewrites_are_ignored():
    L = learning.Learner()
    for _ in range(3):
        L.observe_correction("Ship it on Friday.", "Ship it on Monday.")
        L.observe_correction("so I think we should go", "Let's postpone the whole project until next quarter")
    assert L.fixes() == []


def test_vocabulary_hints_and_sentence_starts():
    L = learning.Learner()
    for text in ("Meeting with Priya about the iOS app.", "Priya says the iOS build is ready. More notes later.",
                 "Ping Dustin. More notes later.", "Call Dustin now."):
        L.observe_text(text)
    assert sorted(L.hints()) == ["Dustin", "Priya", "iOS"]  # "More" only ever starts a sentence


def test_forget_and_persist():
    L = learning.Learner()
    for _ in range(2):
        L.observe_correction("is kubernetes up", "is Kubernetes up")
    L.forget_fix("kubernetes", "Kubernetes")
    assert learning.Learner().fixes() == []  # forgotten, and saved to disk


def test_locate_passage_after_edit():
    doc = "Notes. " * 30 + "I told Dustin about the GPU budget." + " More." * 30
    found = learning.locate("I told dust in about the GPU budget.", doc, near=doc.index("budget.") + 7)
    assert found == "I told Dustin about the GPU budget"
    assert learning.locate("completely different words here entirely", "Notes. " * 5) is None
