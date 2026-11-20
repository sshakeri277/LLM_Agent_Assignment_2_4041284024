"""Coordinator policy, driven by scripted model clients (no network)."""
import json
import unittest

from autogen_ext.models.replay import ReplayChatCompletionClient

from story_system.agents import create_author, create_editor, create_storyteller
from story_system.request import parse_request
from story_system.workflow import StoryWorkflow

INFO = {"vision": False, "function_calling": True, "json_output": True, "family": "gpt-4o",
        "structured_output": True}
REQ = parse_request({"story_id": "w1",
                     "child": {"first_name": "سارا", "age": 7, "interests": ["ستاره"]},
                     "story_request": {"theme": "دوستی", "target_word_count": 30,
                                       "must_include": ["یک جغد"], "avoid": ["مرگ"]}})  # 26-34 words


def words(n, w="کلمه"):
    return " ".join([w] * n)


def draft(story, title="عنوان", moral="پیام"):
    return json.dumps({"outline": "o", "title": title, "moral": moral, "story": story}, ensure_ascii=False)


def review(verdict="approve", score=5, issues=(), plot=True):
    s = {k: score for k in ("age_fit", "requirements", "coherence", "literary_quality", "safety",
                            "personalization")}
    return json.dumps({"checklist": "c", "scores": s, "plot_preserved": plot, "ending_preserved": True,
                       "unapproved_content": False, "verdict": verdict, "summary": "s",
                       "issues": [{"criterion": "coherence", "severity": "major", "location": "l", "problem": p, "fix": f}
                                  for p, f in issues]}, ensure_ascii=False)


def narration(story):
    return json.dumps({"story": story}, ensure_ascii=False)


def check(verdict="approve", issues=()):
    return review(verdict, 5 if verdict == "approve" else 3, issues, plot=verdict == "approve")


def make(author_replies, editor_replies, storyteller_replies, **kw):
    clients = [ReplayChatCompletionClient(r, model_info=INFO)
               for r in (author_replies, editor_replies, storyteller_replies)]
    kw.setdefault("retry_delay", 0)
    wf = StoryWorkflow(create_author(clients[0]), create_editor(clients[1]), create_storyteller(clients[2]), **kw)
    return wf, clients


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_four_calls(self):
        wf, _ = make([draft(words(30, "الف"), title="ماجرای سارا")], [review(), check()], [narration(words(31, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual(r.output(), {"story_id": "w1", "title": "ماجرای سارا", "story": words(31, "ب"),
                                      "moral": "پیام", "age_range": "6-8"})
        self.assertEqual((r.revisions, r.approved, r.narration_used, r.calls), (0, True, True, 4))

    async def test_editor_receives_the_authors_message(self):
        events = []
        wf, _ = make([draft(words(30, "الف"))], [review(), check()], [narration(words(30, "ب"))],
                     on_event=events.append)
        await wf.run(REQ)
        review_in = next(e["input"] for e in events if e.get("step") == "review")
        self.assertTrue(any(m["from"] == "Author" and "الف" in m["text"] for m in review_in))
        told = await wf.storyteller.model_context.get_messages()
        self.assertTrue(any("الف" in str(m.content) for m in told))  # the approved version, not a summary

    async def test_each_editor_review_starts_fresh_but_knows_its_earlier_issues(self):
        events = []
        wf, _ = make([draft(words(30, "الف")), draft(words(30, "دال"))],
                     [review("revise", 3, [("جمله‌ی پایانی درس می‌دهد", "با یک تصویر تمام کن")]), review(), check()],
                     [narration(words(30, "ب"))], on_event=events.append)
        await wf.run(REQ)
        second = [e for e in events if e.get("step") == "review"][1]["input"]
        self.assertTrue(any("جمله‌ی پایانی درس می‌دهد" in m["text"] for m in second if m["from"] == "Coordinator"))
        seen = await wf.editor.model_context.get_messages()
        self.assertFalse(any("الف" in str(m.content) and getattr(m, "source", "") == "Author" for m in seen))

    async def test_feedback_reaches_the_author_and_revision_is_reviewed(self):
        wf, _ = make([draft(words(30, "الف")), draft(words(30, "دال"))],
                     [review("revise", 3, [("جغد نقشی ندارد", "جغد سرنخ را پیدا کند")]), review(), check()],
                     [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual((r.revisions, r.approved), (1, True))
        author_saw = await wf.author.model_context.get_messages()
        self.assertTrue(any("جغد سرنخ را پیدا کند" in str(m.content) for m in author_saw))
        told = await wf.storyteller.model_context.get_messages()
        self.assertTrue(any("دال" in str(m.content) for m in told))

    async def test_at_most_two_revisions_then_best_version(self):
        wf, _ = make([draft(words(30, "یک")), draft(words(30, "دو")), draft(words(30, "سه"))],
                     [review("revise", 3, [("p", "f")]), review("revise", 4, [("p", "f")]),
                      review("revise", 2, [("p", "f")]), check()],
                     [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual((r.revisions, r.approved), (2, False))
        told = await wf.storyteller.model_context.get_messages()
        self.assertTrue(any("دو" in str(m.content) for m in told))  # draft 2 scored best
        self.assertFalse(any(" سه " in str(m.content) for m in told))
        self.assertEqual(r.calls, 3 + 3 + 1 + 1)

    async def test_major_issue_blocks_an_approval(self):
        major = json.loads(review("approve", 5, [("p", "f")]))
        minor = json.loads(review("approve", 5, [("p", "f")]))
        minor["issues"][0]["severity"] = "minor"
        wf, _ = make([draft(words(30, "الف")), draft(words(30, "دال"))],
                     [json.dumps(major), json.dumps(minor), check()], [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual((r.revisions, r.approved), (1, True))

    async def test_major_issue_quoting_text_that_is_gone_counts_as_fixed(self):
        stale = json.loads(review("revise", 4, [("درس را گفت", "حذفش کن")]))
        stale["issues"][0]["location"] = "و یاد گرفتند که دوستی مهم است"  # not in the draft
        live = json.loads(review("revise", 4, [("درس را گفت", "حذفش کن")]))
        live["issues"][0]["location"] = "کلمه کلمه کلمه"  # is in the draft
        wf, _ = make([draft(words(30, "کلمه"))], [json.dumps(stale), check()], [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual((r.revisions, r.approved, r.calls), (0, True, 4))
        wf, _ = make([draft(words(30, "کلمه")), draft(words(30, "دال"))], [json.dumps(live), review(), check()],
                     [narration(words(30, "ب"))])
        self.assertEqual(((await wf.run(REQ)).revisions), 1)

    async def test_a_slightly_misquoted_major_issue_still_counts(self):
        text = "سارا گفت که کمک خواستن اصلا بد نیست و همه خندیدند " + words(20)
        live = json.loads(review("revise", 4, [("درس را گفت", "حذفش کن")]))
        live["issues"][0]["location"] = "سارا گفت کمک خواستن اصلا بد نیست"  # one word dropped
        wf, _ = make([draft(text), draft(words(30, "دال"))], [json.dumps(live), review(), check()],
                     [narration(words(30, "ب"))])
        self.assertEqual((await wf.run(REQ)).revisions, 1)

    async def test_a_described_location_is_never_treated_as_stale(self):
        described = json.loads(review("revise", 4, [("the child is passive", "let سارا find the clue")]))
        described["issues"][0]["location"] = "The child does not actively solve the problem"
        wf, _ = make([draft(words(30, "الف")), draft(words(30, "دال"))], [json.dumps(described), review(), check()],
                     [narration(words(30, "ب"))])
        self.assertEqual((await wf.run(REQ)).revisions, 1)

    async def test_an_editor_length_claim_cannot_overrule_the_measured_length(self):
        wrong = json.loads(review("revise", 4, [("The story is too short at 30 words, below the required 26-34 words.",
                                                 "add more words")]))
        wrong["issues"][0].update(criterion="requirements", location="whole story")
        wf, _ = make([draft(words(30, "الف"))], [json.dumps(wrong), check()], [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual((r.revisions, r.approved), (0, True))
        wrong["issues"][0]["problem"] = "The story exceeds the word limit, making it unsuitable for the brief."
        wf, _ = make([draft(words(30, "الف"))], [json.dumps(wrong), check()], [narration(words(30, "ب"))])
        self.assertTrue((await wf.run(REQ)).approved)

    async def test_the_editor_sees_the_moral_as_a_separate_field(self):
        events = []
        wf, _ = make([draft(words(30, "الف"), moral="دوستی مهم است")], [review(), check()],
                     [narration(words(30, "ب"))], on_event=events.append)
        await wf.run(REQ)
        seen = next(e["input"] for e in events if e.get("step") == "review")
        author_text = next(m["text"] for m in seen if m["from"] == "Author")
        self.assertIn("not part of the story", author_text)

    async def test_final_text_is_polished(self):
        wf, _ = make([draft(words(29, "الف") + ' "سلام"', title='"ماه"')], [review(), check()],
                     [narration(words(29, "ب") + ' "سلام"')])
        r = await wf.run(REQ)
        self.assertTrue(r.story.endswith("«سلام»"))
        self.assertEqual(r.title, "«ماه»")

    async def test_code_checks_override_an_approval(self):
        wf, _ = make([draft(words(12, "الف")), draft(words(30, "دال"))], [review(), review(), check()],
                     [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual(r.revisions, 1)
        author_saw = await wf.author.model_context.get_messages()
        self.assertTrue(any("12 words" in str(m.content) for m in author_saw))

    async def test_avoid_term_forces_revision(self):
        wf, _ = make([draft(words(29, "الف") + " مرگ"), draft(words(30, "دال"))], [review(), review(), check()],
                     [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual(r.revisions, 1)

    async def test_failed_narration_is_retried_once(self):
        wf, _ = make([draft(words(30, "الف"))], [review(), check("revise", [("رویداد تازه", "حذفش کن")]), check()],
                     [narration(words(30, "ب")), narration(words(30, "پ"))])
        r = await wf.run(REQ)
        self.assertEqual((r.story, r.narration_used), (words(30, "پ"), True))
        told = await wf.storyteller.model_context.get_messages()
        self.assertTrue(any("حذفش کن" in str(m.content) for m in told))

    async def test_draft_level_issue_does_not_block_a_faithful_narration(self):
        old_issue = json.loads(review("revise", 3, [("the child does not solve it", "let her solve it")]))
        old_issue["issues"][0].update(criterion="personalization", location="whole story")
        old_issue["scores"]["safety"] = 5
        wf, _ = make([draft(words(30, "الف"))], [review(), json.dumps(old_issue)], [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual((r.story, r.narration_used), (words(30, "ب"), True))

    async def test_safety_issue_still_blocks_a_narration(self):
        unsafe = json.loads(review("revise", 3, [("a scary scene was added", "remove it")]))
        unsafe["issues"][0].update(criterion="safety", location="whole story")
        wf, _ = make([draft(words(30, "الف"))], [review(), json.dumps(unsafe), json.dumps(unsafe)],
                     [narration(words(30, "ب")), narration(words(30, "پ"))])
        r = await wf.run(REQ)
        self.assertFalse(r.narration_used)

    async def test_narration_failing_twice_falls_back_to_the_approved_text(self):
        wf, _ = make([draft(words(30, "الف"))], [review(), check("revise", [("p", "f")]), check("revise", [("p", "f")])],
                     [narration(words(30, "ب")), narration(words(30, "پ"))])
        r = await wf.run(REQ)
        self.assertEqual((r.story, r.narration_used), (words(30, "الف"), False))

    async def test_narration_outside_length_is_not_accepted(self):
        wf, _ = make([draft(words(30, "الف"))], [review(), check(), check()],
                     [narration(words(60, "ب")), narration(words(31, "پ"))])
        r = await wf.run(REQ)
        self.assertEqual(r.story, words(31, "پ"))

    async def test_invalid_json_reply_is_retried(self):
        wf, _ = make([draft(words(30, "الف"))], ["not json", review(), check()], [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual((r.approved, r.story), (True, words(30, "ب")))

    async def test_editor_failure_never_approves_the_draft(self):
        # The Editor has no replies at all: every review call fails (after its retries).
        wf, _ = make([draft(words(30, "الف"), title="ت")], [], [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertFalse(r.approved)
        self.assertIn("draft0:unreviewed", r.path)
        self.assertEqual(r.revisions, 0)                 # no feedback to revise from
        self.assertEqual((r.story, r.title), (words(30, "الف"), "ت"))  # best available text, still output
        self.assertFalse(r.narration_used)               # an unchecked retelling is never used
        self.assertEqual(r.output()["story_id"], "w1")

    async def test_editor_failure_on_a_revision_keeps_the_reviewed_draft(self):
        wf, _ = make([draft(words(30, "یک")), draft(words(30, "دو"))], [review("revise", 3, [("p", "f")])],
                     [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertFalse(r.approved)
        self.assertIn("draft1:unreviewed", r.path)
        self.assertEqual(r.story, words(30, "یک"))       # the reviewed draft beats the unreviewed one

    async def test_editor_failure_on_the_narration_check_falls_back_to_the_approved_text(self):
        wf, _ = make([draft(words(30, "الف"))], [review()], [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertTrue(r.approved)                      # the draft itself was reviewed and approved
        self.assertFalse(r.narration_used)
        self.assertEqual(r.story, words(30, "الف"))
        self.assertIn("narration0:unchecked", r.path)

    async def test_a_blank_field_in_a_draft_is_an_invalid_reply_and_is_retried(self):
        wf, _ = make([draft(words(30, "الف"), moral="  "), draft(words(30, "الف"), moral="پیام درست")],
                     [review(), check()], [narration(words(30, "ب"))])
        r = await wf.run(REQ)
        self.assertEqual(r.moral, "پیام درست")

    async def test_output_fields_are_never_left_empty(self):
        req = parse_request({"story_id": "w2", "child": {"first_name": "سارا", "age": 7},
                             "story_request": {"moral": "کمک خواستن خوب است", "target_word_count": 30}})
        wf, _ = make([draft(words(30, "الف"), moral=""), draft(words(30, "الف"), moral=""),
                      draft(words(30, "الف"), moral="")], [review(), check()], [narration(words(30, "ب"))],
                     reply_retries=2)
        r = await wf.run(req)
        self.assertEqual(r.moral, "کمک خواستن خوب است")   # every reply was blank: the requested moral is used
        self.assertTrue(all(v.strip() for v in r.output().values()))

    async def test_transcript_records_every_step(self):
        events = []
        wf, _ = make([draft(words(30, "الف"))], [review(), check()], [narration(words(30, "ب"))],
                     on_event=events.append)
        await wf.run(REQ)
        self.assertEqual([e["step"] for e in events if "agent" in e],
                         ["draft", "review", "narrate", "check_narration"])
        self.assertTrue(all("tokens" in e and "output" in e for e in events if "agent" in e))


if __name__ == "__main__":
    unittest.main()
