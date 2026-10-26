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
