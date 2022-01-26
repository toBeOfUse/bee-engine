from unittest import TestCase
from bee_engine.bee import SessionBee, SpellingBee
GJ = SpellingBee.GuessJudgement

class SpellingBeeTest(TestCase):
    def setUp(self):
        self.bee = SpellingBee(
            "2022-01-16", "H", ["C", "D", "E", "K", "N", "U"], 
            ["unchecked", "chunked"], 
            ["unheeded", "chucked", "unchecked", "hence", "heeded", "nunchuk", 
            "chunk", "nunchuck", "hunched", "hunk", "check", "hunch", "chunked", 
            "cheek", "checked", "chuck", "hued", "heck", "heed", "uncheck", "eunuch"]
        )
    
    def test_basic_attributes(self):
        self.assertEqual(self.bee.day, "2022-01-16")
        self.assertEqual(self.bee.center, "H")
        self.assertEqual(self.bee.outside, ["C", "D", "E", "K", "N", "U"])
        self.assertEqual(len(self.bee.answers), 21)
        self.assertEqual(len(self.bee.pangrams), 2)
        self.assertTrue(all(x==x.lower() for x in self.bee.answers))
        self.assertTrue(all(x in self.bee.answers for x in self.bee.pangrams))
    
    def test_word_judgements(self):
        self.assertTrue(self.bee.does_word_count("hunk"))
        self.assertFalse(self.bee.does_word_count("zamboni"))
        self.assertTrue(self.bee.does_word_count("Chunked"))
        self.assertTrue(self.bee.is_pangram("Chunked"))
        self.assertFalse(self.bee.is_pangram("hence"))
        self.assertFalse(self.bee.is_pangram("hudcekn"))
        normal_guess = self.bee.guess("hunk")
        self.assertEqual({GJ.good_word}, normal_guess)
        pangram_judgement = self.bee.guess("chunked")
        self.assertEqual(pangram_judgement, {GJ.pangram, GJ.good_word})
        gotten_pangram_judgement = self.bee.guess("chunked", {"hunk", "chunked"})
        self.assertEqual(gotten_pangram_judgement,
            {GJ.good_word, GJ.pangram, GJ.already_gotten})
        self.assertEqual(self.bee.guess("batarang"), {GJ.wrong_word})
        # TODO: respond_to_guesses reactions
    
    def test_points(self):
        self.assertEqual(self.bee.valid_words_to_points(["hunk"]), 1)
        self.assertEqual(SpellingBee.any_words_to_points(["hunk", "dunk"]), 2)
        self.assertEqual(self.bee.valid_words_to_points(["hunk", "dunk"]), 1)
        self.assertEqual(self.bee.valid_words_to_points(["hunk", "chunk"]), 6)
        self.assertEqual(self.bee.valid_words_to_points(["chunked"]), 14)
        self.assertEqual(
            self.bee.valid_words_to_points(["hunk", "chunk", "chunked"]), 20
        )
        self.assertEqual(self.bee.max_points, 127)
        self.assertEqual(self.bee.get_ranking({"chunked", "hunk"}), "Good")

class SessionBeeWrappersTest(SpellingBeeTest):
    def setUp(self):
        super().setUp()
        self.bee = SessionBee(self.bee)
    
    def test_internal_gotten_words(self):
        self.bee.guess("chunk")
        self.bee.guess("chunked")
        self.bee.guess("hunk")
        self.assertEqual(self.bee.percentage_words_gotten(), 3/21*100)
        self.assertEqual(self.bee.guess("chunk"), {GJ.already_gotten, GJ.good_word})
        self.assertNotIn("chunk", self.bee.get_unguessed_words())
        self.assertIn("🤝", self.bee.respond_to_guesses("hunk"))
        self.assertEqual(self.bee.points_scored(), 20)
        self.assertEqual(self.bee.points_scored_percentage(), 20/127*100)
        self.assertEqual(self.bee.get_ranking(), "Solid")
