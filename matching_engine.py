"""
matching_engine.py — Word alignment for script following.

Takes the full script and the words coming from the speech engine and reports
how far through the script the speaker has got. Alignment only ever moves
forward and tolerates the recogniser adding filler words, dropping a word, or
transcribing one imperfectly, so a single bad result cannot throw the reader
somewhere else in the script.
"""

import re
from typing import List, NamedTuple, Optional

from thefuzz import fuzz


class Alignment(NamedTuple):
    """Result of aligning recognised words against the script."""

    last_word: int      # furthest script word that was matched
    matched_words: int  # how many recognised words landed on the script


class MatchingEngine:
    """Tracks the user's reading position inside the loaded script."""

    # A recognised word may match this many script words ahead. This absorbs
    # the speaker skipping a word and the recogniser dropping one, while
    # keeping a noisy result from jumping across the script.
    DEFAULT_MAX_SKIP = 3

    def __init__(self, match_threshold: int = 80, max_skip: int = DEFAULT_MAX_SKIP):
        self.match_threshold = match_threshold
        self.max_skip = max_skip

        self._words: List[str] = []
        self._raw_words: List[str] = []
        self._position: int = 0
        self._word_char_offsets: List[int] = []
        self._raw_text: str = ""

    # ── Public API ───────────────────────────────────────────────────

    def load_script(self, text: str):
        """Parse the script into a word list and build offset map."""
        self._raw_text = text
        self._words = []
        self._raw_words = []
        self._word_char_offsets = []
        self._position = 0

        for match in re.finditer(r"\S+", text):
            raw = match.group()
            self._raw_words.append(raw)
            self._words.append(self._normalise(raw))
            self._word_char_offsets.append(match.start())

    @property
    def word_count(self) -> int:
        return len(self._words)

    @property
    def position(self) -> int:
        return self._position

    @property
    def progress(self) -> float:
        if not self._words:
            return 0.0
        return self._position / len(self._words)

    @property
    def char_offsets(self) -> List[int]:
        """Character offset in the raw script for every word."""
        return self._word_char_offsets

    def raw_word_at(self, word_index: int) -> str:
        """The word as written in the script (punctuation included)."""
        if not self._raw_words:
            return ""
        idx = max(0, min(word_index, len(self._raw_words) - 1))
        return self._raw_words[idx]

    def raw_words_in_range(self, first_word: int, last_word: int) -> List[str]:
        """Script words for an inclusive word-index range."""
        if not self._raw_words:
            return []
        first = max(0, first_word)
        last = min(last_word, len(self._raw_words) - 1)
        return self._raw_words[first:last + 1]

    def char_offset_at(self, word_index: int) -> int:
        """Return the character offset in the raw text for a given word index."""
        if not self._word_char_offsets:
            return 0
        idx = max(0, min(word_index, len(self._word_char_offsets) - 1))
        return self._word_char_offsets[idx]

    def word_span(self, word_index: int) -> tuple[int, int]:
        """Return (start, end) char offsets for a word, inclusive of trailing space."""
        if not self._word_char_offsets:
            return (0, 0)
        idx = max(0, min(word_index, len(self._word_char_offsets) - 1))
        start = self._word_char_offsets[idx]
        if idx + 1 < len(self._word_char_offsets):
            end = self._word_char_offsets[idx + 1]
        else:
            end = len(self._raw_text)
        return (start, end)

    def reset(self):
        self._position = 0

    def set_position(self, word_index: int) -> None:
        """Set reading position (e.g. when user scrolls to skip back/forward)."""
        if not self._words:
            return
        self._position = max(0, min(word_index, len(self._words) - 1))

    def align_forward(
        self,
        spoken_text: str,
        start_word: int,
        end_word: int,
        *,
        min_score: Optional[int] = None,
        max_skip: Optional[int] = None,
    ) -> Optional[Alignment]:
        """Align recognised words to the script, forward only.

        Each recognised word may match one of the next ``max_skip + 1`` script
        words, so a stumble or a dropped filler word does not desynchronise
        the reader. Words that do not match anything are treated as
        recogniser noise and leave the pointer where it is.

        The search is bounded by ``[start_word, end_word)``; callers use this
        to keep matching inside the line that is currently on screen.
        Returns the furthest script word reached, or None when nothing
        matched.
        """
        if not self._words:
            return None

        spoken_words = [self._normalise(w) for w in spoken_text.split()]
        spoken_words = [w for w in spoken_words if w]
        if not spoken_words:
            return None

        threshold = self.match_threshold if min_score is None else min_score
        skip = self.max_skip if max_skip is None else max(0, max_skip)

        cursor = max(0, min(start_word, len(self._words) - 1))
        end = max(cursor + 1, min(end_word, len(self._words)))
        last_word: Optional[int] = None
        matched = 0

        for spoken in spoken_words:
            if cursor >= end:
                break
            limit = min(end, cursor + skip + 1)
            best_index = None
            best_score = 0
            for index in range(cursor, limit):
                score = fuzz.ratio(spoken, self._words[index])
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index is None or best_score < threshold:
                continue
            cursor = best_index + 1
            last_word = best_index
            matched += 1

        if last_word is None:
            return None
        return Alignment(last_word=last_word, matched_words=matched)

    # ── Internals ────────────────────────────────────────────────────

    @staticmethod
    def _normalise(word: str) -> str:
        """Lower-case and strip punctuation for comparison."""
        return re.sub(r"[^\w']", "", word.lower())
