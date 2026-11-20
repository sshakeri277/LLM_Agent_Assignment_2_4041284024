"""Structured replies of the three agents (AutoGen `output_content_type`, enforced by the model API)."""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

Criterion = Literal["age_fit", "requirements", "coherence", "literary_quality", "safety", "personalization",
                    "length"]


class StoryDraft(BaseModel):
    """Author's reply. `outline` comes first so the model plans before it writes."""
    outline: str = Field(description="Short plan of the story (English is fine).")
    title: str = Field(description="Short Persian title.")
    moral: str = Field(description="One short Persian sentence: the message of the story.")
    story: str = Field(description="The full story in Persian, paragraphs separated by a blank line.")


class Issue(BaseModel):
    criterion: Criterion
    severity: Literal["major", "minor"] = Field(description="major: a reader or a parent would notice it; "
                                                            "minor: polish.")
    location: str = Field(description="A short quote from the story, or 'whole story'.")
    problem: str
    fix: str = Field(description="A concrete change the writer can apply.")


class Scores(BaseModel):
    age_fit: int
    requirements: int
    coherence: int
    literary_quality: int
    safety: int
    personalization: int

    def clamped(self) -> "Scores":
        return Scores(**{k: min(5, max(1, int(v))) for k, v in self.model_dump().items()})

    def total(self) -> int:
        return sum(self.clamped().model_dump().values())

    def minimum(self) -> int:
        return min(self.clamped().model_dump().values())


class EditorReview(BaseModel):
    """Editor's reply, for both kinds of request (one schema, so one Editor agent keeps one memory).

    `checklist` comes first so the judge checks before it scores. The three read-aloud fields matter only when
    the Editor checks the Storyteller's version; for a draft review they are true / true / false.
    """
    checklist: str = Field(description="Draft review: each requirement PASS/FAIL with evidence. Read-aloud check: "
                                       "the approved story's key events in order, each kept or not.")
    scores: Scores
    plot_preserved: bool
    ending_preserved: bool
    unapproved_content: bool
    issues: List[Issue]
    verdict: Literal["approve", "revise"]
    summary: str


class Narration(BaseModel):
    """Storyteller's reply."""
    story: str = Field(description="The full read-aloud story in Persian, paragraphs separated by a blank line.")
