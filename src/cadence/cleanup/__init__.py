"""Text cleanup: rules (always) followed by an optional LLM polish stage."""

from __future__ import annotations

import logging
import time

from cadence.cleanup.base import Cleaner
from cadence.cleanup.rules import RuleCleaner
from cadence.config import Config

log = logging.getLogger(__name__)


class CleanupChain:
    """Runs stages in order. If a stage fails, the previous stage's output is kept."""

    def __init__(self, rules: Cleaner, llm: Cleaner | None = None, min_words_for_llm: int = 4):
        self.rules = rules
        self.llm = llm
        self.min_words_for_llm = min_words_for_llm
        self.last_timings: dict[str, float] = {}

    def clean(self, text: str, continuation: bool = False) -> str:
        """continuation=True: `text` continues a sentence already typed (live word mode);
        such fragments get the rules stage only, since an LLM needs whole sentences."""
        self.last_timings = {}
        t0 = time.perf_counter()
        text = self.rules.clean(text, continuation) if continuation else self.rules.clean(text)
        self.last_timings["rules"] = time.perf_counter() - t0
        if self.llm is None or continuation or len(text.split()) < self.min_words_for_llm:
            return text
        t0 = time.perf_counter()
        try:
            polished = self.llm.clean(text)
        except Exception as e:  # network down, server not running, timeout, ...
            log.warning("LLM cleanup (%s) failed, using rule output: %s", self.llm.name, e)
            return text
        finally:
            self.last_timings[self.llm.name] = time.perf_counter() - t0
        return polished or text


def build_cleaner(cfg: Config) -> CleanupChain:
    c = cfg.cleanup
    rules = RuleCleaner(c.remove_fillers, c.voice_commands, cfg.dictionary.replacements)
    common = {"chunk_words": c.chunk_words, "vocabulary": cfg.dictionary.words}
    llm: Cleaner | None = None
    if c.llm == "local":
        from cadence.cleanup.llm import LocalLlmCleaner

        llm = LocalLlmCleaner(c.local_url, c.local_model, c.timeout_s, **common)
    elif c.llm == "claude":
        import os

        from cadence.cleanup.llm import ClaudeCleaner

        if os.environ.get("ANTHROPIC_API_KEY"):
            llm = ClaudeCleaner(c.claude_model, c.timeout_s, **common)
        else:
            # No key: nothing is sent anywhere; Cadence stays fully local (rules only).
            log.warning(
                "Claude cleanup is on but the ANTHROPIC_API_KEY environment variable isn't set; "
                "using the built-in rules only (see COMMANDS.md, 'Claude API key')"
            )
    return CleanupChain(rules, llm, c.min_words_for_llm)
