"""Word-by-word live typing ("local agreement").

While the user talks, the not-yet-typed audio is re-transcribed about twice a second.
A word is typed once it is

  * at least `lag_words` words behind the newest word, so a "no wait" or "um" that
    follows it shortly can still be handled, and
  * the same in two transcription passes in a row (the last words of a pass are still
    changing while Whisper hears more).

At a pause, everything heard so far is typed. The audio before the last typed word is then
dropped, so each pass only re-transcribes a few seconds and stays fast.
"""

from __future__ import annotations

import re
from dataclasses import replace

from flow.stt import Word

_NON_WORD = re.compile(r"[^\w']+")


def normalize(word: str) -> str:
    return _NON_WORD.sub("", word.lower())


def agreed_prefix(prev: list[Word], cur: list[Word]) -> int:
    """How many leading words two passes agree on (ignoring case and punctuation)."""
    n = 0
    for a, b in zip(prev, cur):
        if normalize(a.text) != normalize(b.text):
            break
        n += 1
    return n


def words_to_commit(
    prev: list[Word] | None, cur: list[Word], lag_words: int, at_pause: bool
) -> int:
    """Number of leading words of `cur` that are safe to type now."""
    if at_pause:
        return len(cur)
    if prev is None:
        return 0
    return max(0, min(agreed_prefix(prev, cur), len(cur) - lag_words))


def cut_seconds(words: list[Word], k: int, audio_seconds: float) -> float:
    """Where the audio for the first k words ends: halfway into the gap before word k."""
    if k <= 0:
        return 0.0
    if k < len(words):
        end, nxt = words[k - 1].end, words[k].start
        return max(0.0, min(nxt, (end + nxt) / 2))
    return min(audio_seconds, words[k - 1].end + 0.1)


def shift(words: list[Word], seconds: float) -> list[Word]:
    """Re-base word times after `seconds` of audio were dropped from the front."""
    return [replace(w, start=w.start - seconds, end=w.end - seconds) for w in words]


def join_words(words: list[Word]) -> str:
    return "".join(w.text for w in words).strip()
