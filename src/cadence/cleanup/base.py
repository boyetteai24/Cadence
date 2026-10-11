"""Cleanup stage interface."""

from __future__ import annotations

from typing import Protocol


class Cleaner(Protocol):
    name: str

    def clean(self, text: str) -> str:
        """Return polished text. May raise; the chain falls back to the previous stage."""
        ...
