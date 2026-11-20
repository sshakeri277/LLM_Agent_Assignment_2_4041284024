"""Independent quality judge: scores output stories with the assignment's 100-point rubric.

    python evaluation/judge.py --inputs examples/public_inputs.json --outputs output.json [--model gpt-4.1]

A development tool, not part of the agent pipeline: it uses a different (stronger) model than the agents so
the system is not graded by its own Editor. Format (5 points) is checked by code; the judge scores the other 95.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Dict, List

from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from autogen_core.models import SystemMessage, UserMessage  # noqa: E402
from autogen_ext.models.openai import OpenAIChatCompletionClient  # noqa: E402

from story_system.checks import count_words  # noqa: E402
from story_system.config import load_dotenv, load_settings  # noqa: E402
from story_system.request import age_range, parse_request, word_range  # noqa: E402

RUBRIC = {  # criterion: max points (assignment §10; "format" is scored by code)
    "personalization": 20, "constraints": 15, "coherence": 15, "age_fit": 15, "safety": 10, "creativity": 10,
    "persian_quality": 5, "read_aloud": 5,
}
KEYS = ["story_id", "title", "story", "moral", "age_range"]

SYSTEM = """You are a strict, experienced judge of Persian children's literature grading a course assignment.
Score the story against the child's request with this rubric (integers, use the full range, be critical):
- personalization (0-20): the child's name, age, interests and favourite character genuinely shape the plot; the
  child acts and solves the problem.
- constraints (0-15): theme, moral, setting, every must-include item, every avoid item, approximate length.
- coherence (0-15): clear beginning, problem, climax, resolution, ending; logical, consistent events.
- age_fit (0-15): vocabulary, sentence length and complexity right for the age.
- safety (0-10): no violence, intense fear, humiliation, dangerous behaviour, false facts; suitable message.
- creativity (0-10): fresh idea, memorable characters, engaging.
- persian_quality (0-5): natural, correct, fluent Persian (not translation-like), correct punctuation.
- read_aloud (0-5): warm storyteller voice, natural dialogue, rhythm, good to read aloud.
Write the justification first, then the scores."""


class Scores(BaseModel):
    personalization: int
    constraints: int
    coherence: int
    age_fit: int
    safety: int
    creativity: int
    persian_quality: int
    read_aloud: int


class Verdict(BaseModel):
    justification: str = Field(description="Short critical notes per criterion (English).")
    scores: Scores
    biggest_weakness: str


def format_points(inp: Dict, out: Dict) -> int:
    ok = (isinstance(out, dict) and list(out) == KEYS and all(isinstance(out[k], str) for k in KEYS)
          and out["story"].strip() and out["title"].strip() and out["moral"].strip()
          and out["story_id"] == inp.get("story_id"))
    return 5 if ok else 0


async def judge_one(client, inp: Dict, out: Dict) -> Dict:
    req = parse_request(inp)
    lo, hi = word_range(req)
    words = count_words(out.get("story", ""))
    prompt = (f"CHILD'S REQUEST (JSON)\n{json.dumps(inp, ensure_ascii=False, indent=1)}\n\n"
              f"Required length: about {lo}-{hi} words; measured: {words} words.\n\n"
              f"OUTPUT\nTitle: {out.get('title')}\nMoral: {out.get('moral')}\nAge range: {out.get('age_range')}\n\n"
              f"{out.get('story')}")
    r = await client.create([SystemMessage(content=SYSTEM), UserMessage(content=prompt, source="user")],
                            json_output=Verdict)
    v = Verdict.model_validate_json(r.content)
    scores = {k: max(0, min(RUBRIC[k], getattr(v.scores, k))) for k in RUBRIC}
    total = sum(scores.values()) + format_points(inp, out)
    return {"story_id": inp.get("story_id"), "total": total, "scores": scores, "format": format_points(inp, out),
            "words": words, "length_range": [lo, hi], "age_range_ok": out.get("age_range") == age_range(req.age),
            "weakness": v.biggest_weakness, "justification": v.justification}


async def main_async(args) -> None:
    env = dict(os.environ)
    load_dotenv(os.path.join(os.path.dirname(HERE), ".env"), env)
    s = load_settings(env)
    client = OpenAIChatCompletionClient(model=args.model, api_key=s.api_key, base_url=s.base_url, temperature=0)
    with open(args.inputs, encoding="utf-8") as f:
        inputs = json.load(f)
    with open(args.outputs, encoding="utf-8") as f:
        outputs = json.load(f)
    inputs = inputs if isinstance(inputs, list) else [inputs]
    outputs = outputs if isinstance(outputs, list) else [outputs]
    by_id = {o.get("story_id"): o for o in outputs}
    try:
        results: List[Dict] = await asyncio.gather(*(judge_one(client, i, by_id.get(i.get("story_id"), {}))
                                                     for i in inputs))
    finally:
        await client.close()
    mean = sum(r["total"] for r in results) / len(results)
    per = {k: round(sum(r["scores"][k] for r in results) / len(results), 1) for k in RUBRIC}
    report = {"judge_model": args.model, "mean_total": round(mean, 1), "mean_by_criterion": per, "stories": results}
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    for r in results:
        print(f"{r['story_id']}: {r['total']:3d}  words {r['words']} ({r['length_range'][0]}-{r['length_range'][1]})"
              f"  weakness: {r['weakness'][:110]}")
    print(f"MEAN {mean:.1f}  " + "  ".join(f"{k} {v}" for k, v in per.items()))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--inputs", required=True)
    p.add_argument("--outputs", required=True)
    p.add_argument("--model", default="gpt-4.1")
    p.add_argument("--report", default=None, help="write the full judgement as JSON here")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
