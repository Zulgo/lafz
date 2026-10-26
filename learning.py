"""Local, adaptive learning: Murmur improves from how the user corrects it. Nothing leaves the computer.

- Corrections: when the user changes a word or two of a dictation (e.g. "dust in" -> "Dustin") the pair is counted.
  After the same correction is seen LEARN_AFTER times it becomes an automatic fix.
- Vocabulary: names and unusual words that keep appearing in the user's final text are passed to Whisper as hints.

Only word pairs and word counts are stored (learning.json in Murmur's settings folder)."""
import difflib, re, time

import system

LEARN_AFTER = 2         # the same correction must be seen twice: one-off rewrites aren't mistakes
MAX_PHRASE = 3          # corrections longer than this many words are edits, not recognition errors
VOCAB_MIN = 2           # a word must appear in this many dictations before it becomes a Whisper hint
VOCAB_HINTS = 30
_WORD = re.compile(r"[\w'’.-]*\w|\w")
_COMMON = set("""a an the and or but if then so to of in on at by for with from up about into over after before
i me my we our you your he she it they them this that these those is are was were be been am do did done have has had
can could will would should may might must not no yes ok okay hi hello hey thanks thank please monday tuesday wednesday
thursday friday saturday sunday january february march april may june july august september october november december
""".split())


def words(text):
    return _WORD.findall(text)


def _norm(w):
    return w.lower().strip(".'’-")


class Learner:
    def __init__(self, name="learning.json"):
        self.name = name
        data = system.load_json(name, {})
        self.pairs = data.get("pairs", {})      # "wrong\tright" -> {"count", "last"}
        self.vocab = data.get("vocab", {})      # word -> {"count", "last"}
        self.forgotten = set(data.get("forgotten", []))

    def save(self):
        system.save_json(self.name, {"pairs": self.pairs, "vocab": self.vocab, "forgotten": sorted(self.forgotten)})

    # ---- corrections ----
    def observe_correction(self, original, corrected):
        """Compare what Murmur produced with what the user turned it into. Returns newly learned (wrong, right)."""
        a, b = words(original), words(corrected)
        if not a or not b or a == b:
            return []
        sm = difflib.SequenceMatcher(None, [_norm(w) for w in a], [_norm(w) for w in b], autojunk=False)
        if sm.ratio() < 0.5:  # mostly rewritten: not a recognition fix
            return []
        learned = []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                # same words, different capitalization: "kubernetes" -> "Kubernetes"
                for x, y in zip(a[i1:i2], b[j1:j2]):
                    if x != y and x.lower() == y.lower() and y[:1].isupper() and _norm(y) not in _COMMON:
                        learned += self._count(x, y)
            elif op == "replace" and i2 - i1 <= MAX_PHRASE and j2 - j1 <= MAX_PHRASE:
                wrong, right = " ".join(a[i1:i2]), " ".join(b[j1:j2])
                # must sound/look alike ("dust in" ~ "Dustin"), otherwise the user changed their mind, not a mishearing
                if difflib.SequenceMatcher(None, wrong.lower().replace(" ", ""),
                                           right.lower().replace(" ", "")).ratio() >= 0.6:
                    learned += self._count(wrong, right)
        if learned or any(op != "equal" for op, *_ in sm.get_opcodes()):
            self.save()
        return learned

    def _count(self, wrong, right):
        key = f"{wrong.lower()}\t{right}"
        if key in self.forgotten:
            return []
        entry = self.pairs.setdefault(key, {"count": 0, "last": 0})
        entry["count"] += 1
        entry["last"] = time.time()
        return [(wrong.lower(), right)] if entry["count"] == LEARN_AFTER else []

    def fixes(self):
        """Learned automatic fixes, most used first: [(wrong, right)]."""
        ready = [(k, v) for k, v in self.pairs.items() if v["count"] >= LEARN_AFTER and k not in self.forgotten]
        return [tuple(k.split("\t")) for k, _ in sorted(ready, key=lambda kv: -kv[1]["count"])]

    # ---- vocabulary ----
    def observe_text(self, text):
        """Count names and unusual words in a finished dictation."""
        seen = set()
        for m in _WORD.finditer(text):
            w, n = m.group(), _norm(m.group())
            if len(n) < 3 or n in _COMMON or n in seen:
                continue
            special = any(c.isdigit() for c in w) or (any(c.isupper() for c in w[1:]))  # GPT4, iOS, McDonald
            before = text[:m.start()].rstrip()
            sentence_start = not before or before[-1] in ".!?"
            # Capitalized mid-sentence is a name; at a sentence start it only counts if already known as one.
            name = w[:1].isupper() and (not sentence_start or w in self.vocab)
            if special or name:
                seen.add(n)
                entry = self.vocab.setdefault(w, {"count": 0, "last": 0})
                entry["count"] += 1
                entry["last"] = time.time()
        if seen:
            self.save()

    def hints(self, n=VOCAB_HINTS):
        """Words to give Whisper as spelling hints: frequent and recent first."""
        ready = [(w, v) for w, v in self.vocab.items() if v["count"] >= VOCAB_MIN and f"vocab\t{w}" not in self.forgotten]
        ready.sort(key=lambda wv: (-wv[1]["count"], -wv[1]["last"]))
        return list(dict.fromkeys([w for w, _ in ready[:n]] + [right for _, right in self.fixes()]))[:n]

    # ---- user control ----
    def forget_fix(self, wrong, right):
        key = f"{wrong}\t{right}"
        self.pairs.pop(key, None)
        self.forgotten.add(key)
        self.save()

    def forget_word(self, word):
        self.vocab.pop(word, None)
        self.forgotten.add(f"vocab\t{word}")
        self.save()

    def reset(self):
        self.pairs, self.vocab, self.forgotten = {}, {}, set()
        self.save()


def locate(pasted, field_text, near=None, slack=3):
    """Find what became of the pasted text inside a text field the user may have edited since.
    Returns the field's current version of that passage, or None if it can't be found reliably."""
    if not field_text:
        return None
    if near is not None:  # only look around where it was pasted; fields can be whole documents
        lo = max(0, near - len(pasted) - 400)
        field_text = field_text[lo: near + 400]
    if pasted in field_text:
        return pasted
    p, f = words(pasted), words(field_text)
    if not p or not f:
        return None
    pn, best, best_ratio = [_norm(w) for w in p], None, 0.0
    fn = [_norm(w) for w in f]
    for size in range(max(1, len(p) - slack), len(p) + slack + 1):
        for start in range(0, max(1, len(f) - size + 1)):
            r = difflib.SequenceMatcher(None, pn, fn[start:start + size], autojunk=False).ratio()
            if r > best_ratio:
                best, best_ratio = (start, size), r
    if best is None or best_ratio < 0.6:
        return None
    return " ".join(f[best[0]: best[0] + best[1]])
