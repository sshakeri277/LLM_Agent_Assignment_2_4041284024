"""Deterministic checks the coordinator adds to the Editor's judgement (models cannot count words)."""
from __future__ import annotations

import math
import re
from typing import Iterable, List, Optional, Tuple

_WORD = re.compile(r"[\w\u200c]+")
_BOUNDARY = r"(?<![\w\u200c])"

# The brief asks for "about 400 to 500 words": a miss of up to 8 % outside the range is accepted.
LENGTH_TOLERANCE = 0.08
# Measured on the model's drafts: about 40 Persian words per paragraph. A model can follow a paragraph count;
# it cannot count words.
WORDS_PER_PARAGRAPH = 40


def count_words(text: str) -> int:
    """Words as a reader sees them: half-space (ZWNJ) joined forms like «می‌رود» count once."""
    return sum(1 for w in _WORD.findall(text or "") if w.strip("\u200c"))


def avoid_hits(text: str, avoid: Iterable[str]) -> List[str]:
    """Avoid-list terms that literally appear in the text (at a word start: «نترس» is not «ترس»)."""
    hits = []
    for term in avoid:
        term = term.strip()
        if term and re.search(_BOUNDARY + re.escape(term), text or ""):
            hits.append(term)
    return hits


def count_paragraphs(text: str) -> int:
    return sum(1 for line in (text or "").split("\n") if line.strip())


def paragraph_plan(bounds: Tuple[int, int], words_per_paragraph: float = WORDS_PER_PARAGRAPH) -> int:
    """Paragraphs needed to reach the middle of the range."""
    return max(2, round(sum(bounds) / 2 / words_per_paragraph))


def revision_length_note(text: str, bounds: Tuple[int, int]) -> Optional[str]:
    """Length advice in paragraphs, based on the writer's own paragraph size; None when inside the range."""
    lo, hi = bounds
    words, paras = count_words(text), max(1, count_paragraphs(text))
    if lo <= words <= hi:
        return None
    avg = max(15.0, words / paras)
    mid = (lo + hi) / 2
    if words < lo:
        target = math.ceil(mid / avg)
        return (f"Length: your text has {words} words in {paras} paragraphs (about {avg:.0f} words each); it needs "
                f"{lo}-{hi}. Write {target} paragraphs of about {avg:.0f} words: add {target - paras} new "
                f"paragraph(s) that serve the story (a scene, dialogue, the child's idea), not filler.")
    target = max(2, math.floor(mid / avg))
    return (f"Length: your text has {words} words in {paras} paragraphs (about {avg:.0f} words each); it needs "
            f"{lo}-{hi}. Write {target} paragraphs of about {avg:.0f} words: merge or cut "
            f"{paras - target} paragraph(s) worth of text, keeping every event.")


def length_issue(text: str, bounds: Tuple[int, int], tolerance: float = LENGTH_TOLERANCE) -> Optional[str]:
    lo, hi = bounds
    n = count_words(text)
    if lo * (1 - tolerance) <= n <= hi * (1 + tolerance):
        return None
    return f"The story has {n} words; it must have {lo}-{hi} words."
