"""Collaboration logs: one JSONL file (every event, machine-readable) and one Markdown file (readable) per story."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional


def new_run_dir(log_dir: str) -> Optional[str]:
    if not log_dir:
        return None
    path = os.path.join(log_dir, time.strftime("run-%Y%m%d-%H%M%S") + f"-{os.getpid()}")
    os.makedirs(path, exist_ok=True)
    return path


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE)[:80] or "story"


class StoryTranscript:
    """Collects the events of one story; `write_markdown` renders them at the end."""

    def __init__(self, run_dir: Optional[str], story_id: str):
        self.events: List[Dict[str, Any]] = []
        self.path = os.path.join(run_dir, _safe(story_id)) if run_dir else None

    def __call__(self, event: Dict[str, Any]) -> None:
        event = {"t": round(time.time(), 2), **event}
        self.events.append(event)
        if self.path:
            with open(self.path + ".jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write_markdown(self, title: str) -> None:
        if self.path:
            with open(self.path + ".md", "w", encoding="utf-8") as f:
                f.write(render_markdown(title, self.events))


def _quote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in (text or "").splitlines())


def _render_output(agent: str, out: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    if "scores" in out:  # Editor
        scores = ", ".join(f"{k} {v}" for k, v in out["scores"].items())
        lines += [f"**Verdict:** `{out.get('verdict')}` · scores: {scores}",
                  f"**Summary:** {out.get('summary', '')}"]
        if out.get("plot_preserved") is False or out.get("ending_preserved") is False or out.get("unapproved_content"):
            lines.append(f"**Read-aloud check:** plot kept={out.get('plot_preserved')}, ending kept="
                         f"{out.get('ending_preserved')}, unapproved content={out.get('unapproved_content')}")
        if out.get("issues"):
            lines.append("**Issues:**")
            for i in out["issues"]:
                lines.append(f"- [{i['criterion']}] «{i['location']}» — {i['problem']} → **fix:** {i['fix']}")
        lines += ["<details><summary>checklist</summary>", "", out.get("checklist", ""), "", "</details>"]
    else:  # Author / Storyteller
        if out.get("outline"):
            lines += ["<details><summary>outline</summary>", "", out["outline"], "", "</details>", ""]
        if out.get("title"):
            lines.append(f"**Title:** {out['title']}  ")
        if out.get("moral"):
            lines.append(f"**Moral:** {out['moral']}")
        lines += ["", _quote(out.get("story", ""))]
    return lines


def render_markdown(title: str, events: List[Dict[str, Any]]) -> str:
    lines = [f"# Collaboration log — {title}", ""]
    n = 0
    for e in events:
        if e.get("agent"):
            n += 1
            tokens = e.get("tokens") or {}
            lines += ["", f"## {n}. {e['agent']} — {e['step']} (round {e.get('round', 0)}, attempt "
                          f"{e.get('attempt', 1)}) · {e.get('seconds', 0)} s · tokens "
                          f"{tokens.get('prompt', '?')}/{tokens.get('completion', '?')}", ""]
            for msg in e.get("input") or []:
                if msg["from"] == "Coordinator":
                    lines += [f"**Coordinator → {e['agent']}:**", "", _quote(msg["text"]), ""]
                else:
                    lines += [f"*(also received the message from **{msg['from']}** shown above)*", ""]
            if e.get("error"):
                lines.append(f"**Error:** `{e['error']}`")
            elif e.get("output") is not None:
                lines += [f"**{e['agent']} replied:**", ""] + _render_output(e["agent"], e["output"])
        elif e.get("step") == "privacy":
            lines += ["", f"**🔒 Privacy:** ignored fields {', '.join(e['removed_fields'])} (values never sent or logged)"]
        elif e.get("step") == "safety":
            lines += ["", "**🛡 Safety:** requested items to rewrite safely: "
                      + "; ".join(f"«{i}»" for i in e["unsafe_items"])]
        elif e.get("step") == "decision":
            detail = ", ".join(f"{k}={v}" for k, v in e.items() if k not in ("t", "step") and v not in (None, []))
            lines += ["", f"**➜ Coordinator decision:** {detail}"]
        elif e.get("step") == "result":
            lines += ["", "## Result", "", "```json", json.dumps(e.get("output"), ensure_ascii=False, indent=2),
                      "```", f"path: {' → '.join(e.get('path') or [])} · calls {e.get('calls')}"]
    return "\n".join(lines) + "\n"
