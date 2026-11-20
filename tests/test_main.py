"""Config and the CLI end to end, with scripted model clients injected (no network, no key)."""
import glob
import json
import os
import tempfile
import unittest

from autogen_ext.models.replay import ReplayChatCompletionClient

import main as app
from story_system.config import load_dotenv, load_settings
from tests.test_workflow import INFO, draft, narration, review, words

STORY = {"story_id": "e2e_1",
         "child": {"first_name": "سارا", "age": 7, "interests": ["ستاره"], "last_name": "محرمانه"},
         "story_request": {"theme": "دوستی", "target_word_count": 30}}


class ConfigTests(unittest.TestCase):
    def test_defaults_and_env(self):
        s = load_settings({"AVALAI_API_KEY": "k"})
        self.assertEqual((s.api_key, s.base_url, s.model, s.max_revisions),
                         ("k", "https://api.avalai.ir/v1", "gpt-4o-mini", 2))
        s = load_settings({"AVALAI_API_KEY": "k", "AVALAI_MODEL": "gpt-4.1-mini", "AGENT_MAX_REVISIONS": "1",
                           "AVALAI_BASE_URL": "https://example.test/v1"})
        self.assertEqual((s.model, s.max_revisions, s.base_url), ("gpt-4.1-mini", 1, "https://example.test/v1"))

    def test_avalai_is_the_default_provider(self):
        s = load_settings({"AVALAI_API_KEY": "aa-k"})
        self.assertEqual((s.provider, s.api_key, s.base_url), ("avalai", "aa-k", "https://api.avalai.ir/v1"))

    def test_an_openai_key_alone_is_not_used_silently(self):
        s = load_settings({"OPENAI_API_KEY": "sk-o"})
        self.assertEqual((s.provider, s.api_key, s.base_url), ("avalai", "", "https://api.avalai.ir/v1"))

    def test_openai_only_on_explicit_opt_in(self):
        s = load_settings({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "sk-o", "OPENAI_MODEL": "gpt-4.1-mini"})
        self.assertEqual((s.provider, s.api_key, s.base_url, s.model),
                         ("openai", "sk-o", "https://api.openai.com/v1", "gpt-4.1-mini"))

    def test_avalai_key_is_used_even_when_openai_is_set(self):
        s = load_settings({"OPENAI_API_KEY": "sk-o", "AVALAI_API_KEY": "aa"})
        self.assertEqual((s.api_key, s.base_url, s.provider), ("aa", "https://api.avalai.ir/v1", "avalai"))

    def test_revision_cap_cannot_exceed_two(self):
        self.assertEqual(load_settings({"AGENT_MAX_REVISIONS": "9"}).max_revisions, 2)

    def test_bad_number_keeps_default(self):
        with self.assertLogs("story_system", "WARNING"):
            self.assertEqual(load_settings({"AGENT_CONCURRENCY": "two"}).concurrency, 2)

    def test_dotenv_does_not_override_the_environment(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w") as f:
                f.write("# comment\nAVALAI_API_KEY=from_file\nexport AVALAI_MODEL='m-file'\n")
            env = {"AVALAI_API_KEY": "from_env"}
            load_dotenv(path, env)
            self.assertEqual(env, {"AVALAI_API_KEY": "from_env", "AVALAI_MODEL": "m-file"})


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.inp, self.out, self.logs = (os.path.join(d, n) for n in ("in.json", "out.json", "logs"))

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, payload, creative, editor):
        with open(self.inp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        clients = {"creative": ReplayChatCompletionClient(creative, model_info=INFO),
                   "editor": ReplayChatCompletionClient(editor, model_info=INFO)}
        return app.main(["--input", self.inp, "--output", self.out, "--log-dir", self.logs],
                        client_factory=lambda settings, role: clients[role], env={"AVALAI_API_KEY": "test", "AGENT_RETRY_DELAY": "0"})

    def _output(self):
        with open(self.out, encoding="utf-8") as f:
            return json.load(f)

    def test_object_in_object_out_with_exact_keys(self):
        code = self._run(STORY, [draft(words(30, "الف"), title="ت"), narration(words(30, "ب"))], [review(), review()])
        self.assertEqual(code, 0)
        out = self._output()
        self.assertEqual(list(out), ["story_id", "title", "story", "moral", "age_range"])
        self.assertEqual(out, {"story_id": "e2e_1", "title": "ت", "story": words(30, "ب"), "moral": "پیام",
                               "age_range": "6-8"})

    def test_list_in_list_out(self):
        code = self._run([STORY], [draft(words(30, "الف")), narration(words(30, "ب"))], [review(), review()])
        self.assertEqual(code, 0)
        self.assertEqual([o["story_id"] for o in self._output()], ["e2e_1"])

    def test_logs_hold_the_collaboration_but_not_private_fields(self):
        self._run(STORY, [draft(words(30, "الف")), narration(words(30, "ب"))], [review(), review()])
        jsonl = glob.glob(os.path.join(self.logs, "run-*", "e2e_1.jsonl"))
        md = glob.glob(os.path.join(self.logs, "run-*", "e2e_1.md"))
        self.assertEqual((len(jsonl), len(md)), (1, 1))
        with open(jsonl[0], encoding="utf-8") as f:
            events = [json.loads(line) for line in f]
        self.assertEqual([e.get("agent") for e in events if e.get("agent")],
                         ["Author", "Editor", "Storyteller", "Editor"])
        with open(md[0], encoding="utf-8") as f:
            report = f.read()
        for name in ("Author", "Editor", "Storyteller"):
            self.assertIn(name, report)
        for path in jsonl + md:
            with open(path, encoding="utf-8") as f:
                self.assertNotIn("محرمانه", f.read())

    def test_failing_model_still_writes_valid_output(self):
        code = self._run(STORY, [], [])  # the scripted clients have nothing to say
        self.assertEqual(code, 0)
        out = self._output()
        self.assertEqual(list(out), ["story_id", "title", "story", "moral", "age_range"])
        self.assertEqual(out["story_id"], "e2e_1")

    def test_missing_avalai_key_is_reported_clearly(self):
        with open(self.inp, "w", encoding="utf-8") as f:
            json.dump(STORY, f, ensure_ascii=False)
        for env in ({}, {"OPENAI_API_KEY": "sk-o"}):  # an OpenAI key does not stand in for the AvalAI key
            with self.assertLogs("story_system", "ERROR") as logs:
                code = app.main(["--input", self.inp, "--output", self.out, "--log-dir", self.logs], env=env)
            self.assertEqual(code, 2)
            self.assertIn("AVALAI_API_KEY", "\n".join(logs.output))
            self.assertEqual(list(self._output()), ["story_id", "title", "story", "moral", "age_range"])

    def test_bad_input(self):
        with open(self.inp, "w") as f:
            f.write("{oops")
        self.assertEqual(app.main(["--input", self.inp, "--output", self.out, "--log-dir", self.logs],
                                  env={"AVALAI_API_KEY": "k"}), 1)


if __name__ == "__main__":
    unittest.main()
