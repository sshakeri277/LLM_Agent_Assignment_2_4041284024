"""Personalised children's stories by three AutoGen agents (Author, Editor, Storyteller).

    python main.py --input input.json --output output.json

Configuration comes from environment variables or a `.env` file next to this script (see `.env.example`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Callable, Dict, List, Optional

from story_system.agents import create_author, create_editor, create_storyteller
from story_system.config import Settings, create_model_client, load_dotenv, load_settings
from story_system.request import InputError, StoryRequest, age_range, load_requests
from story_system.transcript import StoryTranscript, new_run_dir
from story_system.workflow import StoryResult, StoryWorkflow

log = logging.getLogger("story_system.main")
HERE = os.path.dirname(os.path.abspath(__file__))


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", required=True, help="input JSON (one story request, or a list of them)")
    p.add_argument("--output", required=True, help="output JSON (same shape as the input: object or list)")
    p.add_argument("--log-dir", default=os.path.join(HERE, "logs"), help="where collaboration logs go ('none' = off)")
    p.add_argument("--model", default=None, help="model name (default: AVALAI_MODEL / OPENAI_MODEL / gpt-4o-mini)")
    p.add_argument("--max-revisions", type=int, default=None, help="Author revision rounds, 0-2 (default 2)")
    p.add_argument("--concurrency", type=int, default=None, help="stories processed in parallel (default 2)")
    p.add_argument("--debug", action="store_true", help="verbose console log and AutoGen's own event log")
    return p.parse_args(argv)


def write_json_atomic(path: str, data) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def placeholder(req: StoryRequest) -> Dict[str, str]:
    return StoryResult(req.story_id, "", "", "", age_range(req.age)).output()


async def run_all(requests: List[StoryRequest], settings: Settings, client_factory: Callable,
                  run_dir: Optional[str], on_done: Callable[[int, StoryResult], None]) -> None:
    clients = {role: client_factory(settings, role) for role in ("creative", "editor")}
    gate = asyncio.Semaphore(settings.concurrency)

    async def one(i: int, req: StoryRequest) -> None:
        async with gate:
            transcript = StoryTranscript(run_dir, req.story_id)
            if req.removed_fields:  # names only: the values never reach a model or a log
                transcript({"step": "privacy", "removed_fields": req.removed_fields})
                log.warning("%s: ignored private fields %s", req.story_id, ", ".join(req.removed_fields))
            if req.unsafe_items:  # the brief asks the Author to rewrite these safely; the Editor checks safety
                transcript({"step": "safety", "unsafe_items": req.unsafe_items})
                log.warning("%s: unsafe request items will be rewritten safely: %s", req.story_id,
                            "; ".join(req.unsafe_items))
            # Fresh agents per story: each story's conversation memory starts empty.
            workflow = StoryWorkflow(create_author(clients["creative"]), create_editor(clients["editor"]),
                                     create_storyteller(clients["creative"]), max_revisions=settings.max_revisions,
                                     on_event=transcript, retry_delay=settings.retry_delay)
            started = time.monotonic()
            try:
                result = await workflow.run(req)
            except Exception as exc:  # one story must never cost the others
                log.exception("%s crashed", req.story_id)
                result = StoryResult(req.story_id, "", "", "", age_range(req.age), error=repr(exc),
                                     calls=workflow.calls)
            transcript({"step": "result", "output": result.output(), "path": result.path, "calls": result.calls,
                        "approved": result.approved, "revisions": result.revisions,
                        "narration_used": result.narration_used, "error": result.error,
                        "seconds": round(time.monotonic() - started, 1)})
            transcript.write_markdown(req.story_id)
            log.info("%s: %s, %d revision(s), narration %s, %d calls, %.0f s%s", req.story_id,
                     "approved" if result.approved else "best version", result.revisions,
                     "used" if result.narration_used else "not used", result.calls, time.monotonic() - started,
                     f" — ERROR {result.error}" if result.error else "")
            on_done(i, result)

    try:
        await asyncio.gather(*(one(i, r) for i, r in enumerate(requests)))
    finally:
        for client in clients.values():
            close = getattr(client, "close", None)
            if close:
                await close()


def main(argv: Optional[List[str]] = None, client_factory: Callable = create_model_client,
         env: Optional[Dict[str, str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, stream=sys.stderr, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "openai", "autogen_core", "autogen_agentchat"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if env is None:
        env = os.environ
        load_dotenv(os.path.join(HERE, ".env"), env)
        load_dotenv(os.path.join(os.getcwd(), ".env"), env)
    settings = load_settings(env, model=args.model, max_revisions=args.max_revisions,
                             concurrency=args.concurrency)

    try:
        requests, is_list = load_requests(args.input)
    except InputError as exc:
        log.error("%s", exc)
        write_json_atomic(args.output, [])
        return 1
    outputs = [placeholder(r) for r in requests]

    def save() -> None:
        write_json_atomic(args.output, outputs if is_list else outputs[0])

    save()  # a valid output file exists from the start
    if not settings.api_key:
        log.error("no API key: set AVALAI_API_KEY (or OPENAI_API_KEY) in the environment or in .env")
        return 2

    log_dir = "" if (args.log_dir or "").lower() == "none" else args.log_dir
    run_dir = new_run_dir(log_dir)
    if args.debug and run_dir:  # AutoGen's own message/event log
        from autogen_agentchat import EVENT_LOGGER_NAME
        handler = logging.FileHandler(os.path.join(run_dir, "autogen_events.log"), encoding="utf-8")
        events = logging.getLogger(EVENT_LOGGER_NAME)
        events.addHandler(handler)
        events.setLevel(logging.DEBUG)
    log.info("provider=%s model=%s stories=%d max_revisions=%d logs=%s", settings.provider, settings.model,
             len(requests), settings.max_revisions, run_dir or "off")

    def done(i: int, result: StoryResult) -> None:
        outputs[i] = result.output()
        save()

    started = time.monotonic()
    asyncio.run(run_all(requests, settings, client_factory, run_dir, done))
    log.info("done: %d stories in %.0f s -> %s", len(requests), time.monotonic() - started, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
