"""Murmur speech engine: speech detection, speech-to-text (Whisper), dictionary and cleanup. No UI code."""
import difflib, gc, json, os, platform, re, sys, urllib.request

import numpy as np

SR = 16000
FRAME = int(0.02 * SR)  # 20 ms analysis frames (fallback detector)
APPLE_SILICON = sys.platform == "darwin" and platform.machine() == "arm64"
VAD_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "silero_vad.onnx")
VAD_CHUNK, VAD_CONTEXT = 512, 64  # Silero VAD at 16 kHz: 32 ms chunks plus 64 samples of context
OLLAMA = "http://localhost:11434"
LLM_MODEL = "qwen2.5:1.5b"  # Apache 2.0; ~1 GB, fast and faithful for cleanup
KEEP_ALIVE = "30m"  # how long Ollama keeps the cleanup model in memory; the app shortens it in memory-saver mode
HALLUCINATIONS = {"thank you.", "thanks for watching.", "you", "bye.", "thank you very much."}
# Only short function words are de-duplicated; repeats like "had had" or "very very" can be intentional.
STUTTER_WORDS = r"i|i'm|a|an|the|to|and|but|so|we|it|in|of|on|for|you|he|she|they|my|this|what|just"
STYLES = {"raw": "Raw (exact words)", "clean": "Clean (remove fillers)", "formal": "Formal (rewrite)",
          "bullets": "Bullet points"}
CLEAN_PROMPT = ("You are a transcript editor. Remove filler words (um, uh, like, you know), stutters and repeated "
                "words, and fix punctuation and capitalization. Do NOT rephrase, reorder, summarize or change any "
                "other word. Keep the text in its original language. Reply with the edited text only.\n\n"
                "Text: um so I I want to uh schedule it for like "
                "tomorrow\nEdited: So I want to schedule it for tomorrow.\n\nText: ")
REWRITE_PROMPTS = {
    "formal": "Rewrite this dictated text in a clear, polite, professional tone. Keep all facts and the meaning. "
              "Do not add greetings, sign-offs or anything new. Keep the original language. "
              "Reply with the rewritten text only.\n\nText: ",
    "bullets": "Turn this dictated text into concise bullet points (one idea per line, each starting with '- '). "
               "Keep all facts. Do not add anything new. Keep the original language. "
               "Reply with the bullet points only.\n\nText: ",
}
MODELS = {"whisper-accurate": "Whisper Accurate (best with Dictionary words)",
          "whisper-fast": "Whisper Fast (smaller)"}
MLX_WHISPER = {"whisper-fast": "mlx-community/whisper-small.en-mlx",
               "whisper-accurate": "mlx-community/whisper-large-v3-turbo"}
MLX_WHISPER_MULTILINGUAL = {"whisper-fast": "mlx-community/whisper-small-mlx",
                            "whisper-accurate": "mlx-community/whisper-large-v3-turbo"}
LANGUAGES = {"en": "English", "auto": "Auto-detect", "ar": "Arabic", "zh": "Chinese", "nl": "Dutch",
             "fr": "French", "de": "German", "hi": "Hindi", "it": "Italian", "ja": "Japanese", "ko": "Korean",
             "pt": "Portuguese", "ru": "Russian", "es": "Spanish", "tr": "Turkish", "ur": "Urdu"}


def model_key(choice):
    """Accepts older setting values ('accurate' / 'fast'); anything else (e.g. the removed 'parakeet')
    falls back to Whisper Accurate."""
    return {"accurate": "whisper-accurate", "fast": "whisper-fast"}.get(choice, choice if choice in MODELS else
                                                                        "whisper-accurate")


def resample(audio, rate):
    """Linear resample to 16 kHz (devices like AirPods record at other rates)."""
    if rate == SR or len(audio) == 0:
        return audio.astype("float32")
    n = int(len(audio) * SR / rate)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype("float32")


def level(block):
    """0..1 loudness of an audio block, for the on-screen meter."""
    rms = float(np.sqrt(np.mean(block ** 2))) if len(block) else 0.0
    return min(1.0, rms * 12)


def load_stt(choice="whisper-accurate", language="en"):
    """Return stt(audio, prompt="") -> text for float32 16 kHz audio. language: a LANGUAGES key ('auto' detects).
    English uses the smaller English-only models where they exist; other languages use multilingual ones."""
    choice = model_key(choice)
    english = language == "en"
    lang = None if language == "auto" else language
    if APPLE_SILICON:
        import mlx_whisper
        repo = (MLX_WHISPER if english else MLX_WHISPER_MULTILINGUAL)[choice]
        mlx_whisper.transcribe(np.zeros(SR, dtype="float32"), path_or_hf_repo=repo)  # download + warm up

        def run(a, prompt=""):
            return mlx_whisper.transcribe(a, path_or_hf_repo=repo, language=lang, temperature=0.0,
                                          condition_on_previous_text=False, no_speech_threshold=0.6,
                                          initial_prompt=prompt or None)["text"].strip()
        return run
    from faster_whisper import WhisperModel

    def make(cuda):
        accurate = choice == "whisper-accurate"
        name = ("large-v3-turbo" if accurate else "small.en") if cuda else ("small.en" if accurate else "base.en")
        if not english:
            name = name.replace(".en", "")
        model = WhisperModel(name, device="cuda" if cuda else "cpu", compute_type="float16" if cuda else "int8")

        def run(a, prompt="", vad=True):
            segs, _ = model.transcribe(a, language=lang, beam_size=1, vad_filter=vad,
                                       condition_on_previous_text=False, initial_prompt=prompt or None)
            return " ".join(s.text.strip() for s in segs).strip()
        run(np.zeros(SR, dtype="float32"), vad=False)  # warm up; surfaces missing CUDA libraries now
        return run
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return make(cuda=True)
    except Exception as e:
        print(f"GPU unavailable, using CPU: {e}")
    return make(cuda=False)


def free_memory():
    """Release a model dropped by the caller (used after model comparisons)."""
    gc.collect()
    if APPLE_SILICON:
        import mlx.core as mx
        mx.clear_cache()


# ---- speech detection ----
_vad = None


def silero():
    """The Silero VAD network (a small, pre-trained speech/no-speech classifier), or None if unavailable."""
    global _vad
    if _vad is None:
        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = opts.intra_op_num_threads = 1
            _vad = ort.InferenceSession(VAD_MODEL, opts, providers=["CPUExecutionProvider"])
        except Exception as e:
            print(f"Silero VAD unavailable, using basic detector: {e}")
            _vad = False
    return _vad or None


def speech_probs(audio):
    """Speech probability for each 32 ms chunk."""
    sess = silero()
    state, ctx, probs = np.zeros((2, 1, 128), "float32"), np.zeros((1, VAD_CONTEXT), "float32"), []
    sr = np.array(SR, dtype=np.int64)
    for i in range(0, len(audio) - VAD_CHUNK + 1, VAD_CHUNK):
        x = np.concatenate([ctx, audio[None, i:i + VAD_CHUNK]], axis=1).astype("float32")
        p, state = sess.run(None, {"input": x, "state": state, "sr": sr})
        ctx = x[:, -VAD_CONTEXT:]
        probs.append(float(p[0, 0]))
    return np.array(probs)


def pitch_strength(audio, n):
    """Per 20 ms frame: peak normalized autocorrelation at 80-400 Hz pitch periods, over a 40 ms window.
    Voiced speech scores ~0.7; noise scores ~0.1-0.4."""
    win = audio[np.arange(n)[:, None] * FRAME + np.arange(2 * FRAME)[None, :]]
    win = win - win.mean(axis=1, keepdims=True)
    ac = np.fft.irfft(np.abs(np.fft.rfft(win, 4 * FRAME, axis=1)) ** 2, axis=1)
    return (ac[:, SR // 400: SR // 80 + 1] / (ac[:, :1] + 1e-12)).max(axis=1)


def prepare(audio):
    """Keep the spoken part of the recording and normalize its volume.
    Returns (audio, speech_seconds), or (None, speech_seconds) when there's no real speech (silence, clicks, breaths)."""
    if silero() is None:
        return prepare_basic(audio)
    probs = speech_probs(audio)
    speech = np.where(probs > 0.5)[0]
    speech_s = len(speech) * VAD_CHUNK / SR
    if speech_s < 0.2:
        return None, speech_s
    pad = int(0.3 * SR / VAD_CHUNK) + 1
    clip = audio[max(speech[0] - pad, 0) * VAD_CHUNK: min(speech[-1] + 1 + pad, len(probs)) * VAD_CHUNK]
    return (clip * (0.9 / max(np.abs(clip).max(), 1e-6))).astype("float32"), speech_s


def prepare_basic(audio):
    """Fallback detector (no Silero): loudness above the noise floor plus voice pitch."""
    n = len(audio) // FRAME - 1
    if n <= 0:
        return None, 0.0
    rms = np.sqrt((audio[:n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1))
    # Loud enough: above the noise floor, capped so a clip that is all speech (no pauses) still counts.
    loud = rms > max(0.006, min(3 * np.percentile(rms, 20), 0.3 * rms.max()))
    # Voice has a pitch; fans, hiss, breath and clicks don't.
    voiced = np.where(loud & (pitch_strength(audio, n) > 0.5))[0]
    voiced_s = len(voiced) * FRAME / SR
    if voiced_s < 0.25:
        return None, voiced_s
    pad = int(0.25 * SR / FRAME)
    clip = audio[max(voiced[0] - pad, 0) * FRAME: min(voiced[-1] + 1 + pad, n) * FRAME]
    return (clip * (0.9 / np.abs(clip).max())).astype("float32"), voiced_s


def is_hallucination(text, voiced_s):
    return not text or (text.lower() in HALLUCINATIONS and voiced_s < 0.6)


def parse_dictionary(raw):
    """Lines like 'Dustin' add vocabulary; lines like 'dust in -> Dustin' add a replacement."""
    words, fixes = [], []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "->" in line:
            wrong, right = (s.strip() for s in line.split("->", 1))
            if wrong:
                fixes.append((wrong, right))
                words.append(right)
        else:
            words.append(line)
    return words, fixes


def whisper_prompt(words):
    """Whisper spells words it has seen in the prompt; keep it short so it doesn't dominate."""
    return ("Vocabulary: " + ", ".join(dict.fromkeys(words)) + ".")[:400] if words else ""


def apply_fixes(text, fixes):
    for wrong, right in fixes:
        text = re.sub(rf"\b{re.escape(wrong)}\b", right, text, flags=re.I)
    return text


def rule_clean(text):
    """Deterministic cleanup: drop fillers and stutters, fix capitalization and final punctuation."""
    text = re.sub(r"\b(?:u+m+|u+h+|e+r+m+|h+m+)\b[,.]?\s*", "", text, flags=re.I)
    text = re.sub(rf"\b({STUTTER_WORDS})(?:\s+\1\b)+", r"\1", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    if text:
        text = text[0].upper() + text[1:]
        if text[-1] not in ".?!,;:":
            text += "."
    return text


def ollama(prompt, timeout=30):
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate",
        json.dumps({"model": LLM_MODEL, "prompt": prompt, "stream": False, "keep_alive": KEEP_ALIVE,
                    "options": {"temperature": 0}}).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))["response"].strip()


def warm_ollama():
    """Load the cleanup model into memory ahead of the first dictation (an empty prompt just loads it)."""
    try:
        req = urllib.request.Request(f"{OLLAMA}/api/generate", json.dumps(
            {"model": LLM_MODEL, "prompt": "", "keep_alive": KEEP_ALIVE}).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=60).read()
    except Exception:
        pass


def ollama_status():
    """'ok', 'no-model' or 'not-running'."""
    try:
        tags = json.load(urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=2))
    except Exception:
        return "not-running"
    return "ok" if LLM_MODEL in {m.get("name") for m in tags.get("models", [])} else "no-model"


def cleanup(text, style):
    """Return (text, note). note explains anything the user should know (e.g. Ollama missing)."""
    if style == "raw":
        return text, ""
    base = rule_clean(text)
    if style == "clean":
        if len(base.split()) < 4:
            return base, ""
        try:
            out = ollama(CLEAN_PROMPT + base + "\nEdited:")
        except Exception:
            return base, "Ollama isn't running — used basic cleanup"
        norm = lambda s: re.sub(r"[\W_]", "", s.lower())  # letters and digits in any alphabet
        if out and difflib.SequenceMatcher(None, norm(base), norm(out)).ratio() >= 0.9:
            return out, ""
        return base, "Kept your exact words (AI cleanup changed too much)"
    try:
        out = ollama(REWRITE_PROMPTS[style] + base, timeout=60)
    except Exception:
        return base, f"Ollama isn't running — can't apply '{STYLES[style]}', used basic cleanup"
    return (out or base), ""


# ---- voice commands ----
_COMMANDS = [  # (spoken phrase, replacement); punctuation Whisper puts around the phrase is absorbed
    (r"new paragraph", "\n\n"),
    (r"new line", "\n"),
    (r"(?:bullet point|next bullet)", "\n- "),
]


def apply_voice_commands(text):
    """Turn spoken formatting commands into formatting: "...today. New line. Thanks" -> "...today.\nThanks"."""
    for phrase, out in _COMMANDS:
        text = re.sub(rf"\s*\b{phrase}\b[.,;:!]?\s*", out, text, flags=re.I)  # keeps punctuation said before it
    text = re.sub(r"^\n+", "", text)  # a command at the very start shouldn't leave an empty first line
    text = re.sub(r"[ \t]+\n", "\n", text)
    # Capitalize the first letter of each new line or bullet
    return re.sub(r"(^|\n)(- )?([a-z])", lambda m: m.group(1) + (m.group(2) or "") + m.group(3).upper(), text)


def cleanup_lines(text, style):
    """Clean each line separately, so formatting from voice commands survives (the AI would merge the lines).
    Returns (text, note)."""
    out, notes = [], []
    for line in text.split("\n"):
        bullet = line.startswith("- ")
        body = line[2:] if bullet else line
        if body.strip():
            said_period = body.rstrip().endswith(".")
            body, note = cleanup(body, style)
            if bullet and not said_period:
                body = body.rstrip(".")  # list items read better without a full stop
            notes.append(note)
        out.append(("- " if bullet else "") + body)
    return "\n".join(out), next((n for n in notes if n), "")


# ---- command mode: an instruction applied to selected text ----
def run_command(instruction, selection):
    """Apply a spoken instruction to the selected text (or, without a selection, write what was asked).
    Returns (text, note); text is None if it couldn't be done."""
    if selection.strip():
        prompt = ("You are a writing assistant. Apply the instruction to the text. Reply with the resulting text "
                  f"only, with no explanations or quotes.\n\nInstruction: {instruction}\n\nText:\n{selection}\n\n"
                  "Result:")
    else:
        prompt = ("You are a writing assistant. Write what the instruction asks for. Reply with the text only, "
                  f"with no explanations or quotes.\n\nInstruction: {instruction}\n\nResult:")
    try:
        out = ollama(prompt, timeout=90).strip().strip('"')
    except Exception:
        return None, "Command mode needs Ollama — start it from the Setup tab"
    return (out or None), ("" if out else "The AI returned nothing")
