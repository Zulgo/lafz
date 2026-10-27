import numpy as np
import pytest

import engine

SR = engine.SR
rng = np.random.default_rng(0)


def noise(seconds, amp):
    return (rng.standard_normal(int(seconds * SR)) * amp).astype("float32")


@pytest.mark.parametrize("raw, clean", [
    ("um so I I think we should uh ship it on friday", "So I think we should ship it on friday."),
    ("I had had enough, it was very very good", "I had had enough, it was very very good."),  # real repeats kept
    ("Hi Priya,", "Hi Priya,"),
])
def test_rule_clean(raw, clean):
    assert engine.rule_clean(raw) == clean


@pytest.mark.parametrize("spoken, text", [
    ("Thanks for today. New line. See you tomorrow.", "Thanks for today.\nSee you tomorrow."),
    ("Hi Priya, new paragraph, the report is ready.", "Hi Priya,\n\nThe report is ready."),
    ("Shopping list bullet point milk bullet point eggs", "Shopping list\n- Milk\n- Eggs"),
    ("We need a new linear model.", "We need a new linear model."),  # not a command
])
def test_voice_commands(spoken, text):
    assert engine.apply_voice_commands(spoken) == text


def test_dictionary_and_fixes():
    words, fixes = engine.parse_dictionary("# comment\nPriya\ndust in -> Dustin\n")
    assert words == ["Priya", "Dustin"] and fixes == [("dust in", "Dustin")]
    assert engine.apply_fixes("I told Dust in about it", fixes) == "I told Dustin about it"
    assert engine.whisper_prompt(words) == "Vocabulary: Priya, Dustin."


def test_model_setting_migration():
    assert engine.model_key("accurate") == "whisper-accurate"
    assert engine.model_key("parakeet") == "whisper-accurate"  # removed model falls back
    assert engine.model_key("whisper-fast") == "whisper-fast"


@pytest.mark.parametrize("name, audio", [
    ("silence", noise(2, 0.0015)),
    ("fan noise", noise(2, 0.01)),
    ("empty", np.zeros(100, dtype="float32")),
])
def test_noise_is_not_speech(name, audio):
    clip, _ = engine.prepare(audio)
    assert clip is None, name


def test_key_click_is_not_speech():
    audio = noise(1.5, 0.0015)
    audio[8000:8080] += 0.6 * np.hanning(80).astype("float32")
    assert engine.prepare(audio)[0] is None


def test_resample_to_16k():
    assert abs(len(engine.resample(np.zeros(48000, dtype="float32"), 48000)) - SR) <= 1


def test_cleanup_raw_style_is_untouched():
    assert engine.cleanup("um hello", "raw") == ("um hello", "")


@pytest.mark.parametrize("spoken, text", [
    ("Book the room for Tuesday. Scratch that, let's do it next week.", "Let's do it next week."),
    ("Call the client, scratch that, email the client instead.", "Email the client instead."),
    ("Thanks for the update. See you tomorrow.", "Thanks for the update. See you tomorrow."),
])
def test_scratch_that(spoken, text):
    assert engine._scratch_that(spoken) == text


@pytest.mark.parametrize("spoken, text", [
    ("We'll ship on Tuesday, I mean on Wednesday.", "We'll ship on Wednesday."),
    ("Let's meet at 3, no wait, 4 pm.", "Let's meet at 4 pm."),
    ("I mean it, thank you so much.", "I mean it, thank you so much."),  # not a correction
    ("Send it to John, I mean Priya.", "Send it to John, I mean Priya."),  # unsure without the AI: keep as spoken
])
def test_rule_corrections(spoken, text):
    assert engine._rule_corrections(spoken) == text


def test_ai_correction_may_only_delete_words():
    assert engine._only_deleted("send it to John sorry to Priya", "Send it to Priya.")
    assert not engine._only_deleted("send it to John sorry to Priya", "Please send it to Priya.")  # added a word
    assert not engine._only_deleted("meet at 3 no wait 4", "meet at 4 instead")                   # added a word
    assert not engine._only_deleted("thanks for the update", "thanks for the update")             # nothing removed
    assert not engine._only_deleted("I'm actually really happy with it", "I'm really happy with it")  # just the cue
    assert not engine._only_deleted("No problem, see you tomorrow", "See you tomorrow")             # starts with cue
    assert engine._only_deleted("We'll ship on Tuesday, I mean on Wednesday", "We'll ship on Wednesday")


def test_ai_saying_no_correction_is_trusted(monkeypatch):
    text = "I think the plan works, I mean the budget is fine."
    monkeypatch.setattr(engine, "ollama", lambda *a, **k: text)  # the AI returns the text unchanged
    assert engine.resolve_corrections(text) == (text, False)


def test_correction_with_units_and_whisper_punctuation():
    assert engine._only_deleted("Let's meet at 3. No wait. 4pm.", "Let's meet at 4 pm.")
    assert engine._rule_corrections("Let's meet at 3. No wait. 4pm.") == "Let's meet at 4pm."


def test_listener_owner_check():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert engine.listener_is_mine(port)  # our own process is listening
    assert not engine.listener_is_mine(port)  # nobody is


def test_ollama_owned_by_someone_else_gets_nothing(monkeypatch):
    monkeypatch.setattr(engine, "listener_is_mine", lambda *a: False)
    def send(*a, **k):
        raise AssertionError("dictation was sent")
    monkeypatch.setattr(engine.urllib.request, "urlopen", send)
    with pytest.raises(OSError):
        engine.ollama("private words")
    assert engine.cleanup("um so this is a private message", "clean")[0] == "So this is a private message."


def test_scratching_everything_leaves_nothing():
    assert engine.resolve_corrections("Scratch that.") == ("", True)
