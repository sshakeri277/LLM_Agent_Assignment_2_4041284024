"""The explicit coordinator: Author → Editor (≤ 2 revisions) → Storyteller → Editor check.

    brief ─► Author draft ─► Editor review (+ code checks: length, avoid-list)
                 ▲                │ approve ──────────────► chosen = this draft
                 └── revise ◄─────┤ revise (rounds left)
                                  │ revise (cap reached) ─► chosen = best reviewed draft
                                  ▼
                 Storyteller (read-aloud) ─► Editor check (+ code checks)
                     ▲                          │ approve ─► final = narration
                     └──── one retry ◄──────────┤ revise
                                                │ revise again ─► final = chosen draft (already approved text)

If the Editor cannot answer (all retries failed), nothing is approved without it: the draft is marked
"unreviewed", the loop stops, the best available (reviewed if possible) draft is output with approved=False,
and an unchecked retelling is never used.

Agents talk through AutoGen messages: the Editor receives the Author's own `StructuredMessage` and the Author
receives the Editor's own review message. The Author and the Storyteller keep their conversation memory across
rounds; the Editor starts each review fresh and is given its earlier issues to verify (with its old verdict in
memory it copied it word for word).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import BaseChatMessage, StructuredMessage, TextMessage

from . import messages
from .checks import avoid_hits, count_words, length_issue
from .polish import polish_persian
from .request import StoryRequest, age_range, render_brief, word_range
from .schemas import EditorReview, Issue, Narration, StoryDraft

log = logging.getLogger("story_system.workflow")

COORDINATOR = "Coordinator"


@dataclass
class Version:
    """One reviewed Author draft."""
    round: int
    message: StructuredMessage
    review: Optional[EditorReview]
    code_issues: List[str]
    approved: bool

    @property
    def draft(self) -> StoryDraft:
        return self.message.content

    def rank(self) -> Tuple:
        """Best-version order: verified safe, then reviewed by the Editor, then right length, then the Editor's
        total, then the later round. A draft the Editor could not review ranks below every reviewed draft."""
        reviewed = self.review is not None
        # "Safe" means verified safe: no avoid-list hit and an Editor safety score of at least 4.
        safe = (not any("avoid" in i for i in self.code_issues) and reviewed
                and self.review.scores.clamped().safety >= 4)
        length_ok = not any("words" in i for i in self.code_issues)
        total = self.review.scores.total() if reviewed else 0
        return safe, reviewed, length_ok, total, self.round


@dataclass
class StoryResult:
    story_id: str
    title: str
    story: str
    moral: str
    age_range: str
    approved: bool = False
    revisions: int = 0
    narration_used: bool = False
    calls: int = 0
    path: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def output(self) -> Dict[str, str]:
        """Exactly the keys of the assignment's output contract, in its order."""
        return {"story_id": self.story_id, "title": self.title, "story": self.story, "moral": self.moral,
                "age_range": self.age_range}


class StoryWorkflow:
    def __init__(self, author: AssistantAgent, editor: AssistantAgent, storyteller: AssistantAgent, *,
                 max_revisions: int = 2, narration_retries: int = 1, reply_retries: int = 2,
                 on_event: Optional[Callable[[Dict[str, Any]], None]] = None, retry_delay: float = 2.0):
        self.author, self.editor, self.storyteller = author, editor, storyteller
        self.max_revisions = max_revisions
        self.narration_retries = narration_retries
        self.reply_retries = reply_retries
        self.on_event = on_event or (lambda e: None)
        self.retry_delay = retry_delay
        self.calls = 0

    # ------------------------------------------------------------------ plumbing
    def _emit(self, event: Dict[str, Any]) -> None:
        try:
            self.on_event(event)
        except Exception:  # logging must never break a story
            log.exception("event logger failed")

    async def _ask(self, agent: AssistantAgent, step: str, round_: int, task: Sequence[BaseChatMessage],
                   expected: type, code_checks: Optional[List[str]] = None) -> Optional[StructuredMessage]:
        """Run one agent turn and return its structured reply (retrying invalid or failed replies).

        A reply with an empty required field (e.g. a blank moral) is retried; if every attempt is incomplete,
        the last one that still has a story is returned and the coordinator fills the gap (see `_fill_fields`).
        """
        pending: Sequence[BaseChatMessage] = task
        incomplete_reply: Optional[StructuredMessage] = None
        for attempt in range(self.reply_retries + 1):
            started = time.monotonic()
            self.calls += 1
            try:
                result = await agent.run(task=list(pending))
                reply = result.messages[-1]
                if not (isinstance(reply, StructuredMessage) and isinstance(reply.content, expected)):
                    raise ValueError(f"unexpected reply type {type(reply).__name__}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._emit({"step": step, "agent": agent.name, "round": round_, "attempt": attempt + 1,
                            "input": _texts(pending), "output": None, "tokens": None,
                            "seconds": round(time.monotonic() - started, 2),
                            "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
                log.warning("%s/%s attempt %d failed: %s", agent.name, step, attempt + 1, exc)
                pending = [TextMessage(source=COORDINATOR, content=messages.REPLY_AGAIN)]
                if attempt < self.reply_retries:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                continue
            usage = reply.models_usage
            blank = _blank_fields(reply.content)
            if blank:
                self._emit({"step": step, "agent": agent.name, "round": round_, "attempt": attempt + 1,
                            "input": _texts(pending), "output": reply.content.model_dump(), "tokens": None,
                            "seconds": round(time.monotonic() - started, 2),
                            "error": f"incomplete reply: empty {', '.join(blank)}"})
                if "story" not in blank:
                    incomplete_reply = reply
                pending = [TextMessage(source=COORDINATOR, content=messages.fill_fields(blank))]
                continue
            self._emit({"step": step, "agent": agent.name, "round": round_, "attempt": attempt + 1,
                        "input": _texts(pending), "output": reply.content.model_dump(),
                        "tokens": {"prompt": usage.prompt_tokens, "completion": usage.completion_tokens}
                        if usage else None,
                        "seconds": round(time.monotonic() - started, 2), "code_checks": code_checks or []})
            return reply
        return incomplete_reply  # None, unless an incomplete reply with a story was received

    @staticmethod
    def _code_issues(text: str, req: StoryRequest, bounds: Tuple[int, int]) -> List[str]:
        issues = []
        too = length_issue(text, bounds)
        if too:
            issues.append(too)
        for term in avoid_hits(text, req.avoid):
            issues.append(f"The text contains «{term}», which is on the avoid list. Remove it and anything like it.")
        return issues

    # ------------------------------------------------------------------ policy
    async def run(self, req: StoryRequest) -> StoryResult:
        self.calls = 0
        brief, bounds = render_brief(req), word_range(req)
        result = StoryResult(req.story_id, "", "", "", age_range(req.age))

        # 1) Author writes, Editor reviews, at most `max_revisions` rewrites ------------------------
        draft_msg = await self._ask(self.author, "draft", 0,
                                    [TextMessage(source=COORDINATOR, content=messages.author_task(brief, bounds))],
                                    StoryDraft)
        if draft_msg is None:
            result.error = "the Author produced no draft"
            result.calls = self.calls
            return result
        versions: List[Version] = []
        for round_ in range(self.max_revisions + 1):
            text = draft_msg.content.story
            issues = self._code_issues(text, req, bounds)
            # Fresh eyes for every review: the Editor's memory is cleared and its earlier issues are handed back
            # explicitly, so it verifies them instead of copying its previous verdict.
            earlier = versions[-1].review.issues if versions and versions[-1].review else []
            await self.editor.model_context.clear()
            review_msg = await self._ask(
                self.editor, "review", round_,
                [TextMessage(source=COORDINATOR,
                             content=messages.review_task(brief, bounds, count_words(text), issues, round_, earlier)),
                 draft_msg],
                EditorReview, issues)
            review = review_msg.content if review_msg else None
            # Only a draft the Editor actually reviewed can be approved. If the Editor failed (no reply after
            # the retries), the draft stays unapproved and the loop stops: there is no feedback to revise from.
            approved = review is not None and not issues and _draft_ok(review, text)
            versions.append(Version(round_, draft_msg, review, issues, approved))
            state = "approved" if approved else ("unreviewed" if review is None else "revise")
            result.path.append(f"draft{round_}:{state}")
            self._emit({"step": "decision", "round": round_, "approved": approved, "words": count_words(text),
                        "editor_verdict": review.verdict if review else None,
                        "editor_total": review.scores.total() if review else None, "code_checks": issues,
                        **({"reason": "the Editor did not reply; the draft is not approved"} if review is None
                           else {})})
            if approved or review is None or round_ == self.max_revisions:
                break
            feedback: List[BaseChatMessage] = [review_msg] if review_msg else []
            feedback.append(TextMessage(source=COORDINATOR,
                                        content=messages.revise_task(bounds, text, issues)))
            new_msg = await self._ask(self.author, "revise", round_ + 1, feedback, StoryDraft)
            if new_msg is None:
                break
            draft_msg = new_msg

        chosen = next((v for v in versions if v.approved), None) or max(versions, key=Version.rank)
        result.approved = chosen.approved
        result.revisions = len(versions) - 1
        result.title, result.moral, result.story = chosen.draft.title, chosen.draft.moral, chosen.draft.story
        _fill_fields(result, req, versions)
        if not chosen.approved:
            editor_failed = versions[-1].review is None
            result.path.append(f"{'editor-unavailable' if editor_failed else 'cap-reached'}:best=draft{chosen.round}")
            self._emit({"step": "decision", "chosen": chosen.round,
                        "reason": "the Editor did not reply; best available version (not approved)" if editor_failed
                        else "revision cap reached; best version",
                        "ranks": {v.round: list(v.rank()) for v in versions}})

        # 2) Storyteller narrates the chosen text; the Editor checks it ------------------------------
        approved_text = chosen.draft.story
        pending: List[BaseChatMessage] = [
            TextMessage(source=COORDINATOR, content=messages.narrate_task(brief, bounds, chosen.draft.story)),
            chosen.message]
        for attempt in range(self.narration_retries + 1):
            narr_msg = await self._ask(self.storyteller, "narrate" if attempt == 0 else "renarrate", attempt,
                                       pending, Narration)
            if narr_msg is None:
                break
            text = narr_msg.content.story
            issues = self._code_issues(text, req, bounds)
            await self.editor.model_context.clear()
            check_msg = await self._ask(
                self.editor, "check_narration", attempt,
                [TextMessage(source=COORDINATOR,
                             content=messages.check_task(approved_text, bounds, count_words(text), issues)),
                 narr_msg],
                EditorReview, issues)
            check = check_msg.content if check_msg else None
            # The retelling is used only if the Editor checked it; an unchecked version is never output.
            ok = check is not None and not issues and _narration_ok(check, text)
            state = "approved" if ok else ("unchecked" if check is None else "rejected")
            result.path.append(f"narration{attempt}:{state}")
            self._emit({"step": "decision", "narration_attempt": attempt, "approved": ok,
                        "words": count_words(text), "code_checks": issues,
                        "editor_verdict": check.verdict if check else None,
                        **({"reason": "the Editor did not reply; the retelling is not used"} if check is None
                           else {})})
            if ok:
                result.story, result.narration_used = text, True
                break
            if check is None:  # no Editor feedback to retry with: keep the reviewed text
                break
            pending = ([check_msg] if check_msg else []) + [
                TextMessage(source=COORDINATOR, content=messages.renarrate_task(bounds, text, issues))]
        if not result.narration_used:
            result.path.append("final=approved-draft")
        result.title, result.moral, result.story = (polish_persian(result.title), polish_persian(result.moral),
                                                    polish_persian(result.story))
        result.calls = self.calls
        return result


def _norm(text: str) -> str:
    text = re.sub(r"[«»\"'“”‘’.,!?؟،؛:;…\-—\u200c]", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _is_live(issue: Issue, text: str) -> bool:
    """False for an issue whose quoted location is not in the text (the writer removed it, or the Editor
    quoted something that is not in the story). At least 80 % of the quote's words must be present.

    Only real quotes (three or more words in Persian script, like the story) are checked; a description such as
    "whole story" or "The child does not solve the problem" always counts as live.
    """
    quote = _norm(issue.location).split()
    letters = [c for c in issue.location if c.isalpha()]
    persian = sum("\u0600" <= c <= "\u06ff" for c in letters)
    if len(quote) < 3 or persian < 0.5 * len(letters):  # a description ("The child does not act…"), not a quote
        return True
    words = set(_norm(text).split())
    return sum(w in words for w in quote) / len(quote) >= 0.8  # tolerate a slightly inexact quote


_LENGTH_CLAIM = re.compile(r"\b(?:\d+\s*words?|word (?:count|limit|range)|words? long|length|too short|too long)\b"
                           r"|تعداد کلمه",
                           re.IGNORECASE)


def _is_length_claim(issue: Issue) -> bool:
    return issue.criterion == "length" or bool(_LENGTH_CLAIM.search(issue.problem))


def live_majors(review: EditorReview, text: str) -> List[Issue]:
    """Major issues that still apply to `text`. Length is measured by code (`checks.length_issue`), so an
    Editor's length claim is never used here: the code check alone decides whether the length is right."""
    return [i for i in review.issues if i.severity == "major" and _is_live(i, text) and not _is_length_claim(i)]


def _draft_ok(review: EditorReview, text: str) -> bool:
    """The gate is the Editor's open major issues (verified against the text), not its verdict word."""
    return not live_majors(review, text) and review.scores.minimum() >= 3


# What a faithful retelling can still get wrong. If the plot and ending are kept and nothing was added,
# personalization, coherence and requirements are those of the approved story, so they cannot block it.
NARRATION_CRITERIA = {"safety", "age_fit", "literary_quality", "length"}


def _narration_ok(check: EditorReview, text: str) -> bool:
    s = check.scores.clamped()
    blocking = [i for i in live_majors(check, text) if i.criterion in NARRATION_CRITERIA]
    return (check.plot_preserved and check.ending_preserved and not check.unapproved_content
            and s.safety >= 4 and s.age_fit >= 3 and not blocking)


def _blank_fields(content: Any) -> List[str]:
    """Required text fields of a Author/Storyteller reply that are empty."""
    names = [n for n in ("title", "moral", "story") if hasattr(content, n)]
    return [n for n in names if not str(getattr(content, n) or "").strip()]


def _fill_fields(result: StoryResult, req: StoryRequest, versions: List[Version]) -> None:
    """Never output an empty title or moral: take it from another draft of the same story, else from the request."""
    def first(*values: str) -> str:
        return next((v.strip() for v in values if v and v.strip()), "")
    later_first = list(reversed(versions))
    result.title = first(result.title, *(v.draft.title for v in later_first),
                         f"قصه‌ی {req.first_name}" if req.first_name else "", req.theme, "یک قصه‌ی تازه")
    result.moral = first(result.moral, *(v.draft.moral for v in later_first), req.moral, req.theme)


def _texts(msgs: Sequence[BaseChatMessage]) -> List[Dict[str, str]]:
    return [{"from": m.source, "text": m.to_model_text()} for m in msgs]
