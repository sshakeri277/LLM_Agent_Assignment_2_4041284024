import json
import os
import tempfile
import unittest

from story_system.request import (InputError, age_range, load_requests, parse_request, render_brief,
                                  word_range)

SAMPLE = {
    "story_id": "t_01",
    "child": {"first_name": "پرنیان", "age": 6, "interests": ["قطار", "ستاره‌ها"],
              "favorite_character": "جغد دانا", "reading_level": "ابتدایی"},
    "story_request": {"theme": "شجاعت", "moral": "اشتباه کردن بخشی از یادگیری است", "setting": "یک کوهستان برفی",
                      "target_word_count": 300, "must_include": ["یک جغد", "پایان آرام"],
                      "avoid": ["ترس شدید"]},
}


class ParseTests(unittest.TestCase):
    def test_fields_are_read(self):
        r = parse_request(SAMPLE)
        self.assertEqual(r.story_id, "t_01")
        self.assertEqual(r.first_name, "پرنیان")
        self.assertEqual(r.age, 6)
        self.assertEqual(r.must_include, ["یک جغد", "پایان آرام"])
        self.assertEqual(r.avoid, ["ترس شدید"])

    def test_private_fields_are_dropped_and_never_reach_the_brief(self):
        obj = json.loads(json.dumps(SAMPLE))
        obj["child"].update({"last_name": "رضایی", "school_name": "دبستان نمونه", "phone": "0912",
                             "home_address": "خیابان ۱", "email": "a@b.c"})
        r = parse_request(obj)
        self.assertEqual(sorted(r.removed_fields),
                         ["child.email", "child.home_address", "child.last_name", "child.phone",
                          "child.school_name"])
        brief = render_brief(r)
        for secret in ("رضایی", "دبستان نمونه", "0912", "خیابان ۱", "a@b.c"):
            self.assertNotIn(secret, brief)

    def test_missing_parts_get_defaults(self):
        r = parse_request({"child": {"first_name": "سارا"}})
        self.assertEqual(r.story_id, "story_1")
        self.assertIsNone(r.age)
        self.assertEqual(r.must_include, [])

    def test_string_lists_are_accepted(self):
        obj = {"child": {"first_name": "سارا", "age": 8, "interests": "نقاشی، موسیقی"},
               "story_request": {"must_include": "یک گربه"}}
        r = parse_request(obj)
        self.assertEqual(r.interests, ["نقاشی", "موسیقی"])
        self.assertEqual(r.must_include, ["یک گربه"])

    def test_persian_digit_age(self):
        self.assertEqual(parse_request({"child": {"first_name": "سارا", "age": "۷"}}).age, 7)

    def test_extra_request_fields_are_kept_for_the_brief(self):
        obj = json.loads(json.dumps(SAMPLE))
        obj["story_request"]["tone"] = "آرام‌بخش و ساده"
        self.assertIn("آرام‌بخش و ساده", render_brief(parse_request(obj)))


class SafetyNoteTests(unittest.TestCase):
    def test_unsafe_request_items_are_flagged_in_the_brief(self):
        obj = json.loads(json.dumps(SAMPLE))
        obj["story_request"]["must_include"] = ["نبرد خونین شوالیه با اژدها و کشتن اژدها"]
        r = parse_request(obj)
        self.assertEqual(r.unsafe_items, ["نبرد خونین شوالیه با اژدها و کشتن اژدها"])
        self.assertIn("SAFETY NOTE", render_brief(r))

    def test_avoid_list_is_not_flagged(self):
        r = parse_request(SAMPLE)  # avoid: ["ترس شدید"] is a rule, not a request
        self.assertEqual(r.unsafe_items, [])
        self.assertNotIn("SAFETY NOTE", render_brief(r))

    def test_harmless_words_are_not_flagged(self):
        obj = json.loads(json.dumps(SAMPLE))
        obj["story_request"]["must_include"] = ["یک کشتی بزرگ", "خونسردی در مسابقه"]
        self.assertEqual(parse_request(obj).unsafe_items, [])


class RangeTests(unittest.TestCase):
    def test_age_range(self):
        self.assertEqual(age_range(7), "6-8")
        self.assertEqual(age_range(3), "3-4")
        self.assertEqual(age_range(None), "")

    def test_word_range_from_target(self):
        self.assertEqual(word_range(parse_request(SAMPLE)), (267, 333))
        obj = json.loads(json.dumps(SAMPLE))
        obj["story_request"]["target_word_count"] = 450
        self.assertEqual(word_range(parse_request(obj)), (400, 500))
        obj["story_request"]["target_word_count"] = 225
        self.assertEqual(word_range(parse_request(obj)), (200, 250))

    def test_word_range_from_explicit_range(self):
        for value in ("400-500", "۴۰۰ تا ۵۰۰", [400, 500], {"min": 400, "max": 500}):
            obj = json.loads(json.dumps(SAMPLE))
            obj["story_request"]["target_word_count"] = value
            self.assertEqual(word_range(parse_request(obj)), (400, 500), value)

    def test_word_range_default_follows_age(self):
        self.assertEqual(word_range(parse_request({"child": {"first_name": "a", "age": 5}})), (200, 250))
        self.assertEqual(word_range(parse_request({"child": {"first_name": "a", "age": 9}})), (450, 600))


class BriefTests(unittest.TestCase):
    def test_brief_carries_every_requirement(self):
        brief = render_brief(parse_request(SAMPLE))
        for part in ("پرنیان", "6", "قطار", "ستاره‌ها", "جغد دانا", "شجاعت", "اشتباه کردن بخشی از یادگیری است",
                     "یک کوهستان برفی", "267", "333", "یک جغد", "پایان آرام", "ترس شدید"):
            self.assertIn(part, brief)


class MissingFieldTests(unittest.TestCase):
    def test_missing_fields_read_as_free_choice_not_as_requirements(self):
        brief = render_brief(parse_request({"child": {"first_name": "آوا", "age": 4}}))
        self.assertNotIn("not given", brief)
        self.assertIn("Favourite character: none given — free choice, not a requirement", brief)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, text):
        path = os.path.join(self.tmp.name, "in.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_object_and_list(self):
        reqs, is_list = load_requests(self._write(json.dumps(SAMPLE)))
        self.assertEqual((len(reqs), is_list), (1, False))
        reqs, is_list = load_requests(self._write(json.dumps([SAMPLE, SAMPLE])))
        self.assertEqual((len(reqs), is_list), (2, True))

    def test_wrapped_list(self):
        reqs, is_list = load_requests(self._write(json.dumps({"stories": [SAMPLE]})))
        self.assertEqual((len(reqs), is_list), (1, True))

    def test_bad_json_raises_input_error(self):
        with self.assertRaises(InputError):
            load_requests(self._write("{oops"))


if __name__ == "__main__":
    unittest.main()
