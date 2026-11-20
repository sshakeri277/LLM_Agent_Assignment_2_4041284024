import unittest

from story_system.checks import (avoid_hits, count_paragraphs, count_words, length_issue, paragraph_plan,
                                 revision_length_note)


class CountTests(unittest.TestCase):
    def test_persian_words_with_zwnj_count_once(self):
        self.assertEqual(count_words("دایناسور کوچولو می‌خندید و می‌گفت: «سلام!»"), 6)

    def test_punctuation_and_newlines_are_not_words(self):
        self.assertEqual(count_words("یک، دو.\n\n— سه!"), 3)

    def test_digits_count(self):
        self.assertEqual(count_words("۳ ستاره و 2 ماه"), 5)


class AvoidTests(unittest.TestCase):
    def test_hits_at_word_start_only(self):
        self.assertEqual(avoid_hits("او از مرگ ستاره گفت.", ["مرگ"]), ["مرگ"])
        self.assertEqual(avoid_hits("گفت نترس، من اینجام.", ["ترس"]), [])
        self.assertEqual(avoid_hits("ترسش کم شد.", ["ترس"]), ["ترس"])

    def test_no_hits(self):
        self.assertEqual(avoid_hits("یک روز آفتابی.", ["خشونت", "ترس شدید"]), [])


class ParagraphTests(unittest.TestCase):
    def test_count_paragraphs(self):
        self.assertEqual(count_paragraphs("یک\n\nدو\nسه\n\n\n"), 3)

    def test_plan_from_typical_paragraph_size(self):
        self.assertEqual(paragraph_plan((400, 500)), 11)   # ~40 words per paragraph
        self.assertEqual(paragraph_plan((200, 250)), 6)
        self.assertEqual(paragraph_plan((200, 250), words_per_paragraph=75), 3)

    def test_revision_note_turns_words_into_paragraphs(self):
        short = "\n\n".join(["یک " * 40] * 9)          # 360 words, 9 paragraphs of 40
        note = revision_length_note(short, (450, 550))
        self.assertIn("360", note)
        self.assertIn("13 paragraphs", note)          # 500 / 40
        self.assertIsNone(revision_length_note("\n\n".join(["یک " * 50] * 10), (450, 550)))


class LengthTests(unittest.TestCase):
    def test_small_misses_are_tolerated(self):  # the brief says "about 400 to 500 words"
        self.assertIsNone(length_issue("یک " * 255, (267, 333)))
        self.assertIsNone(length_issue("یک " * 355, (267, 333)))
        self.assertIsNotNone(length_issue("یک " * 240, (267, 333)))

    def test_in_range(self):
        self.assertIsNone(length_issue("یک " * 300, (267, 333)))

    def test_too_short_and_too_long(self):
        self.assertIn("200", length_issue("یک " * 200, (267, 333)))
        self.assertIn("400", length_issue("یک " * 400, (267, 333)))


if __name__ == "__main__":
    unittest.main()
