import unittest

from story_system.polish import polish_persian


class PolishTests(unittest.TestCase):
    def test_straight_and_mixed_quotes_become_guillemets(self):
        self.assertEqual(polish_persian('گفت: "سلام!" و رفت.'), "گفت: «سلام!» و رفت.")
        self.assertEqual(polish_persian('صدا آمد: «بوق! بوق!"'), "صدا آمد: «بوق! بوق!»")
        self.assertEqual(polish_persian('"یک" و "دو"'), "«یک» و «دو»")

    def test_arabic_letters_and_spacing(self):
        self.assertEqual(polish_persian("كتاب علي  زيبا"), "کتاب علی زیبا")

    def test_paragraphs_are_separated_by_one_blank_line(self):
        self.assertEqual(polish_persian("یک.  \n\n\n\nدو.\nسه."), "یک.\n\nدو.\n\nسه.")

    def test_unbalanced_quote_is_left_alone(self):
        self.assertEqual(polish_persian('او گفت "سلام'), 'او گفت "سلام')

    def test_valid_text_is_unchanged(self):
        text = "«سلام» گفت.\n\nبعد رفت."
        self.assertEqual(polish_persian(text), text)
