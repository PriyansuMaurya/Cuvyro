"""
cuvyro.audio.matching — Word alignment for script following.

Takes the full script and the words coming from the speech engine and reports
how far through the script the speaker has got. Alignment only ever moves
forward and tolerates the recogniser adding filler words, dropping a word, or
transcribing one imperfectly, so a single bad result cannot throw the reader
somewhere else in the script.
"""

import re
from typing import List, NamedTuple, Optional

from thefuzz import fuzz


# Spoken numbers reach the matcher as words ("sixty") while scripts print them
# as digits ("60"). Both sides are canonicalised to the digit form so the
# comparison is exact instead of a 0% fuzzy score.
_NUMBER_WORDS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000, "million": 1000000,
}
_ORDINAL_WORDS = {
    "first": "one", "second": "two", "third": "three", "fourth": "four",
    "fifth": "five", "sixth": "six", "seventh": "seven", "eighth": "eight",
    "ninth": "nine", "tenth": "ten", "eleventh": "eleven",
    "twelfth": "twelve", "thirteenth": "thirteen", "fourteenth": "fourteen",
    "fifteenth": "fifteen", "sixteenth": "sixteen", "seventeenth": "seventeen",
    "eighteenth": "eighteen", "nineteenth": "nineteen", "twentieth": "twenty",
    "thirtieth": "thirty", "fortieth": "forty", "fiftieth": "fifty",
    "sixtieth": "sixty", "seventieth": "seventy", "eightieth": "eighty",
    "ninetieth": "ninety", "hundredth": "hundred", "thousandth": "thousand",
}
_DIGIT_ORDINAL = re.compile(r"^(\d+)(?:st|nd|rd|th)$")


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

    @property
    def raw_words(self) -> List[str]:
        """Every word as written, punctuation included.

        The predictive model reads word length and punctuation to estimate
        the next word's duration, so it needs the same tokens the offsets
        were built from rather than a re-split of the text.
        """
        return self._raw_words

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

    def word_score(self, spoken: str, word_index: int) -> int:
        """Fuzzy score for one recognised word against a script word."""
        if not self._words:
            return 0
        idx = max(0, min(word_index, len(self._words) - 1))
        return fuzz.ratio(self._normalise(spoken), self._words[idx])

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
        resync_skip: Optional[int] = None,
        reanchor: bool = False,
    ) -> Optional[Alignment]:
        """Align recognised words to the script, forward only.

        Each recognised word may match one of the next ``max_skip + 1`` script
        words, so a stumble or a dropped filler word does not desynchronise
        the reader. Words that do not match anything are treated as
        recogniser noise and leave the pointer where it is.

        ``resync_skip`` lets a longer pause or a skipped clause recover: when
        a word finds nothing in the normal window, a wider search is allowed
        only if the following recognised word also matches right after the
        candidate. That two-word agreement is what stops a stray sound from
        throwing the reader across the script. The caller's word range still
        bounds the search, so a line-gated partial cannot leave its line.

        The search is bounded by ``[start_word, end_word)``; callers use this
        to keep matching inside the line that is currently on screen.

        ``reanchor`` recovers a reader that has fallen out of step, for
        example after a sentence the recogniser missed entirely: when nothing
        in the window matches, the rest of the script is searched for a run
        of words that match strongly, and the pointer moves there. Only
        finals ask for it, and the run requirement keeps a stray phrase from
        throwing the reader somewhere else.
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
        resync = skip if resync_skip is None else max(skip, resync_skip)

        cursor = max(0, min(start_word, len(self._words) - 1))
        end = max(cursor + 1, min(end_word, len(self._words)))
        last_word: Optional[int] = None
        matched = 0

        for position, spoken in enumerate(spoken_words):
            if cursor >= end:
                break
            limit = min(end, cursor + skip + 1)
            best_index, best_score = self._best_word(spoken, cursor, limit)
            if (best_index is None or best_score < threshold) and resync > skip:
                # Nothing in the line/phrase window. Look further only when
                # the next recognised word agrees with the candidate after
                # it; shared function words alone must not cause a jump.
                wide_index, wide_score = self._best_word(
                    spoken, cursor + skip, min(end, cursor + resync + 1))
                if (wide_index is not None and wide_score >= threshold
                        and self._next_word_agrees(
                            spoken_words, position, wide_index, threshold,
                            skip, end)):
                    best_index, best_score = wide_index, wide_score
            if best_index is None or best_score < threshold:
                continue
            cursor = best_index + 1
            last_word = best_index
            matched += 1

        if last_word is None and reanchor:
            anchor = self._find_anchor(spoken_words, cursor, end, threshold)
            if anchor is not None:
                return Alignment(last_word=anchor, matched_words=3)
        if last_word is None:
            return None
        return Alignment(last_word=last_word, matched_words=matched)

    def _best_word(self, spoken: str, cursor: int, limit: int):
        """Best fuzzy match for one recognised word inside a script window."""
        best_index = None
        best_score = 0
        for index in range(cursor, limit):
            score = fuzz.ratio(spoken, self._words[index])
            if score > best_score:
                best_score = score
                best_index = index
        return best_index, best_score

    def _next_word_agrees(self, spoken_words, position: int, candidate: int,
                          threshold: int, skip: int, end: int) -> bool:
        """True when the next recognised word lands just after the candidate."""
        if position + 1 >= len(spoken_words):
            return False
        limit = min(end, candidate + skip + 2)
        index, score = self._best_word(
            spoken_words[position + 1], candidate + 1, limit)
        return index is not None and score >= threshold

    def _find_anchor(self, spoken_words, cursor: int, end: int, threshold: int):
        """Where the reader has got to, when the current window missed.

        Three consecutive spoken words have to land on three consecutive
        script words, all well above the normal bar, or the reader stays put.
        Two would be enough for a clear phrase but not for a coincidence:
        "evening ... and" appears again somewhere in most scripts, and the
        reader must not teleport on it. The returned index is the last script
        word of the run, i.e. the furthest reached.
        """
        bar = max(86, threshold + 6)
        span = 3
        if len(spoken_words) < span or end - cursor < span:
            return None
        for position in range(len(spoken_words) - span + 1):
            run = spoken_words[position:position + span]
            for index in range(cursor, end - span + 1):
                if all(fuzz.ratio(word, self._words[index + offset]) >= bar
                       for offset, word in enumerate(run)):
                    return index + span - 1
        return None

    # ── Internals ────────────────────────────────────────────────────

    @classmethod
    def _normalise(cls, word: str) -> str:
        """Lower-case, strip punctuation and canonicalise numbers.

        A hyphenated compound ("twenty-one") is folded to its value in one
        step; a number written as two separate words ("twenty one") only
        matches itself, which keeps the script word indices intact.
        """
        cleaned = re.sub(r"[^\w']", "", word.lower())
        if not cleaned:
            return cleaned
        if cleaned.isdigit():
            return str(int(cleaned))
        ordinal = _DIGIT_ORDINAL.match(cleaned)
        if ordinal:
            return str(int(ordinal.group(1)))
        if "-" in word or "_" in word:
            parts = [p for p in re.split(r"[^\w']", word.lower()) if p]
            value = cls._number_value(parts)
            if value is not None:
                return str(value)
        value = cls._number_value([cleaned])
        return str(value) if value is not None else cleaned

    @staticmethod
    def _number_value(parts: List[str]) -> Optional[int]:
        """Value of a run of number words, or None when it is not one."""
        if not parts:
            return None
        total, current, seen = 0, 0, False
        for part in parts:
            word = _ORDINAL_WORDS.get(part, part)
            if word in ("hundred", "thousand", "million"):
                scale = _NUMBER_WORDS[word]
                if not seen:
                    current = 1
                if scale == 100:
                    current *= 100
                else:
                    total += current * scale
                    current = 0
                seen = True
            elif word in _NUMBER_WORDS:
                current += _NUMBER_WORDS[word]
                seen = True
            else:
                return None
        return total + current if seen else None
