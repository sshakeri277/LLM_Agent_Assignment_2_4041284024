"""The coordinator's messages to the agents (the agents' standing instructions are in /prompts)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .checks import count_paragraphs, count_words, paragraph_plan, revision_length_note

REPLY_AGAIN = ("Your previous reply could not be used. Reply again with your complete answer in the required "
               "structured format.")


def _bullets(items: List[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def author_task(brief: str, bounds: Tuple[int, int]) -> str:
    lo, hi = bounds
    paras = paragraph_plan(bounds)
    return (f"Write a new personalised story for this brief.\n\nBRIEF\n{brief}\n\n"
            f"Length: {lo}-{hi} Persian words. Plan exactly {paras} paragraphs of 4-5 sentences each (about 40 "
            f"words per paragraph) and write every one of them in full. Write the story in Persian.")


def _automatic(words: int, bounds: Tuple[int, int], code_issues: List[str]) -> str:
    lo, hi = bounds
    if not code_issues:
        return (f"Automatic checks (length {words} words for {lo}-{hi}, avoid-list): passed. Do not list length "
                f"as an issue.")
    return ("Automatic checks FAILED (the coordinator sends these to the writer itself; do not repeat them as "
            "issues, but your verdict must be \"revise\"):\n" + _bullets(code_issues))


def review_task(brief: str, bounds: Tuple[int, int], words: int, code_issues: List[str], round_: int,
                earlier: Sequence = ()) -> str:
    which = ("the first draft" if round_ == 0 else
             f"revision {round_}. Judge it with the same bar as a new story and re-score it from scratch")
    text = f"Task A — review {which} by the Author (next message) against this brief.\n\nBRIEF\n{brief}\n\n"
    if earlier:
        text += ("ISSUES FROM THE PREVIOUS REVIEW — for each, check the new text: if it is really fixed, drop it; if "
                 "not, report it again quoting the new text:\n"
                 + _bullets([f"[{i.criterion}/{i.severity}] {i.problem} (fix asked: {i.fix})" for i in earlier])
                 + "\n\n")
    return text + _automatic(words, bounds, code_issues)


def revise_task(bounds: Tuple[int, int], text: str, code_issues: List[str]) -> str:
    msg = ("Revise your story using the Editor's review above. Fix every issue, keep what works, and return the "
           "complete story.")
    if code_issues:
        msg += "\nAutomatic checks also found:\n" + _bullets(code_issues)
    note = revision_length_note(text, bounds)
    if note:
        msg += "\n" + note
    return msg


def narration_window(approved: str, bounds: Tuple[int, int]) -> Tuple[int, int]:
    """Words the read-aloud version may have: close to the approved text, and inside the required range."""
    lo, hi = bounds
    n = count_words(approved)
    a, b = max(lo, round(n * 0.95)), min(hi, round(n * 1.12))
    return (a, b) if a < b else bounds


def narrate_task(brief: str, bounds: Tuple[int, int], approved: str) -> str:
    a, b = narration_window(approved, bounds)
    n, p = count_words(approved), count_paragraphs(approved)
    return ("The Editor approved the story in the next message. Turn it into a read-aloud version for the child. "
            "Keep every event, character, the moral and the ending.\n"
            f"Length: the approved text has {n} words in {p} paragraphs; your version must have {a}-{b} words. Keep "
            f"about {p} paragraphs: make the telling livelier by turning narration into dialogue and sound rather "
            f"than by adding paragraphs.\n\nBRIEF (for the child's age and the rules to keep)\n{brief}")


def check_task(approved: str, bounds: Tuple[int, int], words: int, code_issues: List[str]) -> str:
    return ("Task B — check the Storyteller's read-aloud version (next message) against the approved story "
            f"below.\n\nAPPROVED STORY\n{approved}\n\n" + _automatic(words, bounds, code_issues))


def renarrate_task(bounds: Tuple[int, int], text: str, code_issues: List[str]) -> str:
    msg = "Fix your read-aloud version using the Editor's check above and return the complete version."
    if code_issues:
        msg += "\nAutomatic checks also found:\n" + _bullets(code_issues)
    note = revision_length_note(text, bounds)
    if note:
        msg += "\n" + note
    return msg
