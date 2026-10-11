"""Deterministic, instant cleanup. Always runs, with or without an LLM stage.

It is deliberately conservative: when a rule could change meaning it does nothing
and leaves the case to the LLM stage.
"""

from __future__ import annotations

import re

# Standalone hesitation sounds. "ah"/"oh"/"like" are excluded: too often meaningful.
_FILLER = re.compile(
    r"(?<![\w'-])(?:u+m+|u+h+m*|e+r+m+|e+r+|h+m+|m+h*m+|mhm)(?![\w'-])[,.]?[ \t]*",
    re.IGNORECASE,
)
# Phrase fillers only when set off by commas: "it was, you know, fine".
_PHRASE_FILLER = re.compile(r",[ \t]*(?:you know|I mean|kind of|sort of)[ \t]*,", re.IGNORECASE)

# "I-I-I think" -> "I think"; "w-want" -> "want".
_STUTTER_WORD = re.compile(r"\b(\w+)(?:-\1)+\b", re.IGNORECASE)
_STUTTER_PARTIAL = re.compile(r"\b(\w{1,2})-(?=\1\w)", re.IGNORECASE)

# Immediately repeated 2-4 word phrase: "I want to, I want to go" -> "I want to go".
_REPEAT_PHRASE = re.compile(
    r"\b(\w+(?:'\w+)?(?:[ \t]+\w+(?:'\w+)?){1,3})(?:[ \t,]+\1\b)+", re.IGNORECASE
)
_REPEAT_WORD = re.compile(r"\b([A-Za-z]+(?:'[A-Za-z]+)?)(?:[ \t,]+\1\b)+", re.IGNORECASE)
# Words people repeat on purpose ("had had", "very very", "no no no").
_INTENTIONAL_REPEATS = {"had", "that", "very", "no", "so", "bye", "ha", "really", "yeah", "yes", "please"}

# Explicit corrections. "scratch that" deletes the preceding sentence/clause.
_SCRATCH = re.compile(r"[,;.]?[ \t]*\b(?:scratch that|delete that)\b[,.!]?[ \t]*", re.IGNORECASE)
# "at 3, no wait, 4" -> "at 4". Only unambiguous markers ("I mean" is often not a correction).
_CORRECTION = re.compile(
    r"[,;]?[ \t]*\b(?:no[,.]?[ \t]+wait|wait[,.]?[ \t]+no|or[ \t]+sorry|sorry[,.]?[ \t]+I[ \t]+mean[t]?)\b[,.]?[ \t]*",
    re.IGNORECASE,
)
_CLAUSE_END = re.compile(r"[.!?,;\n]")

_NEW_PARAGRAPH = re.compile(r",?[ \t]*\bnew[ -]paragraph\b[,.]?[ \t]*", re.IGNORECASE)
_NEW_LINE = re.compile(r",?[ \t]*\bnew[ -]line\b[,.]?[ \t]*", re.IGNORECASE)


def remove_fillers(text: str) -> str:
    text = _PHRASE_FILLER.sub(",", text)
    return _FILLER.sub("", text)


def collapse_repetitions(text: str) -> str:
    text = _STUTTER_WORD.sub(r"\1", text)
    text = _STUTTER_PARTIAL.sub("", text)
    text = _REPEAT_PHRASE.sub(r"\1", text)

    def word(m: re.Match[str]) -> str:
        return m.group(0) if m.group(1).lower() in _INTENTIONAL_REPEATS else m.group(1)

    return _REPEAT_WORD.sub(word, text)


def apply_scratch_that(text: str) -> str:
    while m := _SCRATCH.search(text):
        before, after = text[: m.start()], text[m.end() :]
        # Drop back to the previous sentence boundary.
        cut = max(before.rfind(c) for c in ".!?\n")
        text = (before[: cut + 1] + " " + after) if cut >= 0 else after
    return text


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def apply_corrections(text: str) -> str:
    """"X, no wait, Y" / "X, or sorry, Y": replace the part of X that Y corrects.

    - If Y starts with a word said just before ("at 3pm, no wait, at 4pm"), replace from that
      word on (looking back at most 4 words; Y up to 8 words).
    - Otherwise Y replaces only the last word ("at 3pm, no wait, 4pm"), and only if Y is
      short (up to 3 words), so "there's no wait, the line is empty" is left alone.
    Anything else is ambiguous and left for the LLM stage.
    """
    for _ in range(5):
        m = _CORRECTION.search(text)
        if not m:
            break
        prefix, rest = text[: m.start()], text[m.end() :]
        end = _CLAUSE_END.search(rest)
        replacement = rest[: end.start()] if end else rest
        tail = rest[len(replacement) :]
        new_words = replacement.split()
        prefix_words = prefix.split()
        stripped = prefix.rstrip()
        if not stripped or stripped[-1] in ".!?":
            # Correction starts a new sentence: just drop the marker.
            text = prefix + rest
            continue
        if not new_words:
            break
        first = _norm(new_words[0])
        recent = [_norm(w) for w in prefix_words[-4:]]
        if first in recent and len(new_words) <= 8:
            cut = len(prefix_words) - (len(recent) - max(i for i, w in enumerate(recent) if w == first))
        elif len(new_words) <= 3:
            cut = len(prefix_words) - 1
        else:
            break  # ambiguous: leave it for the LLM stage
        text = " ".join(prefix_words[:cut] + new_words) + tail
    return text


def apply_voice_commands(text: str) -> str:
    text = _NEW_PARAGRAPH.sub("\n\n", text)
    return _NEW_LINE.sub("\n", text)


def apply_replacements(text: str, replacements: dict[str, str]) -> str:
    for spoken, written in replacements.items():
        text = re.sub(rf"(?<!\w){re.escape(spoken)}(?!\w)", written, text, flags=re.IGNORECASE)
    return text


def tidy(text: str, continuation: bool = False) -> str:
    """Normalize spacing/punctuation and capitalize sentence starts. With continuation=True
    the text continues a sentence already typed, so its first word keeps its case."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r" +([,.!?;:])", r"\1", text)
    text = re.sub(r",(?:\s*,)+", ",", text)
    text = re.sub(r",\s*([.!?])", r"\1", text)
    text = re.sub(r"^[\s,;.]+", "", text)
    # Standalone pronoun "i" / "i'm" -> "I" (but not "i.e.").
    text = re.sub(r"(?<![\w'.-])i(?=[\s,!?;:]|'[a-z]|\.(?!\w)|$)", "I", text)
    # Capitalize sentence starts (start of text, after .!? or a newline).
    text = _SENTENCE_START.sub(_capitalize_continuing if continuation else _capitalize, text)
    return text.strip()


_SENTENCE_START = re.compile(r"(^|[.!?]\s+|\n)([a-z])")
# Abbreviations and initials ("J. Smith") don't end a sentence; numbers ("at 4. Then") do.
_ABBREVIATION = re.compile(
    r"(?:\b(?:[eE]\.g|[iI]\.e|etc|vs|[mM]r|[mM]rs|[mM]s|[dD]r|[sS]t|approx|cf)|\b[A-Z])\.\s+$"
)


def _capitalize_continuing(m: re.Match[str]) -> str:
    if m.start() == 0 and m.group(1) == "":
        return m.group(0)  # first word continues the previous text's sentence
    return _capitalize(m)


def _capitalize(m: re.Match[str]) -> str:
    if _ABBREVIATION.search(m.string[: m.start(2)]):
        return m.group(0)
    return m.group(1) + m.group(2).upper()


class RuleCleaner:
    name = "rules"

    def __init__(
        self,
        remove_fillers: bool = True,
        voice_commands: bool = True,
        replacements: dict[str, str] | None = None,
    ) -> None:
        self.remove_fillers = remove_fillers
        self.voice_commands = voice_commands
        self.replacements = replacements or {}

    def clean(self, text: str, continuation: bool = False) -> str:
        if self.voice_commands:
            text = apply_voice_commands(text)
        text = apply_scratch_that(text)
        if self.remove_fillers:
            text = remove_fillers(text)
            text = collapse_repetitions(text)
        text = apply_corrections(text)
        text = apply_replacements(text, self.replacements)
        return tidy(text, continuation)
