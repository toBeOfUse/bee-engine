from __future__ import annotations
from os import PathLike
from pathlib import Path
import traceback
import json
import sqlite3
from enum import Enum
from collections import defaultdict
from typing import Optional
import re
from urllib.error import HTTPError

from .render import BeeRenderer
from .data_access import get_word_rank

import aiohttp
import inflect
inflecter = inflect.engine()


def copula(c: int):
    return inflecter.plural("are", c)


def num(n: int):
    return inflecter.number_to_words(n, threshold=100)


def plural(word: str, n: int):
    return inflecter.plural(word, n)


default_db = Path(__file__).parent / Path("data/puzzles.db")


class SpellingBee():
    """
    Instance of an NYT Spelling Bee puzzle. The puzzle consists of 6 outer letters
    and one central letter; players must use the central letter and any of the outer
    letters to create words that are at least 4 letters long. At least one "pangram,"
    a word that uses every letter, can be formed. This class stores the necessary
    data to represent the puzzle and judge answers, has serialization mechanisms to
    save the puzzle and the answers that have come in so far in a simple SQLite
    database, can render itself to a PNG, and includes a functions to allow it to
    interact with discord Message objects.
    """

    class GuessJudgement(Enum):
        wrong_word = 1
        good_word = 2
        pangram = 3
        already_gotten = 4

    class HintTable:
        def __init__(self, words: list[str]):
            words = [w.lower() for w in words]
            self.empty: bool = len(words) == 0
            self.one_letters: dict[dict[int, int]] = defaultdict(lambda: defaultdict(lambda: 0))
            self.two_letters: dict[int] = defaultdict(lambda: 0)
            self.word_lengths: set[int] = set()
            self.pangram_count = 0
            for word in words:
                self.word_lengths.add(len(word))
                self.one_letters[word[0]][len(word)] += 1
                self.two_letters[word[0:2]] += 1
                if len(set(word)) == 7:
                    self.pangram_count += 1

        def format_table(self) -> str:
            if self.empty:
                return "There are no remaining words."
            f = "   "+" ".join(f"{x:<2}" for x in sorted(list(self.word_lengths)))+" Σ \n"
            sorted_lengths = sorted(list(self.word_lengths))
            sums_by_length = {x: 0 for x in sorted_lengths}
            for letter, counts in sorted(
                    list(self.one_letters.items()), key=lambda i: i[0]):
                f += f"{letter.upper()}  " + " ".join(
                    (f"{counts[c]:<2}" if counts[c] != 0 else "- ") for c in sorted_lengths)
                f += f" {sum(counts.values()):<2}\n"
                for length, count in counts.items():
                    sums_by_length[length] += count
            f += "Σ  "+" ".join(f"{c:<2}" for c in sums_by_length.values())
            f += f" {sum(sums_by_length.values())}"
            return f

        def format_two_letters(self) -> str:
            sorted_2l = sorted(
                list(self.two_letters.items()), key=lambda x: x[0]
            )
            return ", ".join(
                f"{l[0].upper()}{l[1]}: {c}" for (l, c) in sorted_2l)

        def format_pangram_count(self) -> str:
            c = self.pangram_count
            return f"There {copula(c)} {num(c)} remaining {plural('pangram', c)}."

        def format_all_for_discord(self) -> str:
            result = f"```\n{self.format_table()}\n```\n"
            result += self.format_two_letters()
            result += "\n"
            result += self.format_pangram_count()
            return result

    def __init__(
            self,
            day: str,
            center: str,
            outside: list[str],
            pangrams: list[str],
            answers: list[str]):
        """Constructs the puzzle object. You will probably want to fetch a new puzzle
        from the NYTimes or an old puzzle from the database instead of calling this
        directly.

        Args:
            day (str): YYYY-MM-DD, like "2021-12-31"
            center (str): Single-character string containing the center, required
            letter of the bee.
            outside (list[str]): Single character strings containing the other usable
            letters for the bee.
            pangrams (list[str]): Valid bee answers that use every available letter.
            answers (list[str]): All valid bee answers.
        """
        self.day = day
        self.center = center.upper()
        self.outside = [l.upper() for l in outside]
        self.pangrams = set(p.lower() for p in pangrams)
        self.answers = set(a.lower() for a in answers)
        for word in self.pangrams:
            self.answers.add(word)  # shouldn't be necessary but just in case
        self.image: Optional[bytes] = None
        self.message_id: int = -1
        self.db_path: Optional[str] = None

    def __eq__(self, other):
        return self.center+self.outside == other.center+other.outside

    def percentage_complete(self, gotten_words: set[str]):
        return round(len(gotten_words) / len(self.answers) * 100, 1)

    def does_word_count(self, word: str) -> bool:
        return word.lower() in self.answers

    def is_pangram(self, word: str) -> bool:
        return word.lower() in self.pangrams

    def guess(self, word: str, gotten_words: set[str] = set()) -> set[GuessJudgement]:
        """
        determines whether a word counts for a point and/or is a pangram and/or has
        already been gotten. uses the GuessJudgement enum inner class.
        """
        result = set()
        w = word.lower()
        if self.does_word_count(w):
            result.add(self.GuessJudgement.good_word)
            if self.is_pangram(w):
                result.add(self.GuessJudgement.pangram)
            if w in gotten_words:
                result.add(self.GuessJudgement.already_gotten)
            gotten_words.add(w)
            self.save()
        else:
            result.add(self.GuessJudgement.wrong_word)
        return result

    def get_unguessed_words(self, sort=True, gotten_words: set[str] = set()) -> list[str]:
        """returns the heretofore unguessed words in a list sorted from the least to
        the most common words."""
        unguessed = list(self.answers - gotten_words)
        if sort:
            unguessed.sort(key=lambda w: get_word_rank(w), reverse=True)
        return unguessed

    def get_unguessed_hints(self, gotten_words: set[str] = set()) -> HintTable:
        return self.HintTable(self.get_unguessed_words(False, gotten_words))

    def get_wiktionary_alternative_answers(self) -> list[str]:
        """
        Returns the words that use the required letters and are english words
        according to Wiktionary (according to data obtained by
        https://github.com/tatuylonen/wiktextract) but aren't in the official answers
        list, sorted from longest to shortest
        """
        wiktionary_words = get_wiktionary_trie()
        all_letters = [x.lower() for x in self.outside+[self.center]]
        candidates = wiktionary_words.search_words_by_letters(all_letters)

        result = []
        for word in candidates:
            # i probably filtered the dataset for some of these characteristics at
            # some point but i forget which ones so whatever better safe than sorry
            if self.center not in word.upper():
                continue
            if len(word) < 4:
                continue
            if word.lower() in self.answers:
                continue
            if word.lower() != word:
                continue
            for character in word:
                if character.upper() not in (self.outside + [self.center]):
                    break
            else:
                result.append(word)
        return sorted(result, key=len, reverse=True)

    async def render(self, renderer: BeeRenderer = None) -> bytes:
        """Renders the puzzle to an image; returns the image file as bytes and caches
        it in the image instance variable. If you do not pass in an instance of a
        subclass of PuzzleRenderer, one will be chosen at random"""
        if renderer is None:
            renderer = BeeRenderer.get_random_renderer()
        self.image = await renderer.render(self)
        return self.image

    @property
    def image_file_type(self) -> Optional[str]:
        if self.image is None:
            return None
        elif self.image[0:4] == b"\x89PNG":
            return "png"
        elif self.image[0:3] == b"GIF":
            return "gif"
        elif self.image[0:2] == b"\xff\xd8":
            return "jpg"

    @classmethod
    async def fetch_from_nyt(cls) -> SpellingBee:
        """Returns the spelling bee currently marked as today's on the NYT website.
        Raises HTTPError if the website is not accessible or AssertionError if the
        data on the website has an unexpected form."""
        async with aiohttp.ClientSession() as session:
            url = 'https://www.nytimes.com/puzzles/spelling-bee'
            async with session.get(url) as resp:
                if not resp.ok:
                    raise HTTPError(url, resp.status, "could not fetch spelling bee")
                html = await resp.text()
        game_data = re.search("window.gameData = (.*?)</script>", html)
        if game_data:
            game = json.loads(game_data.group(1))["today"]
            assert all(
                x in game
                for x in ["printDate", "centerLetter", "outerLetters", "pangrams", "answers"])
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", game["printDate"]) is not None
            return cls(
                game["printDate"],
                game["centerLetter"],
                game["outerLetters"],
                game["pangrams"],
                game["answers"])

    def respond_to_guesses(self, guess: str) -> list[str]:
        """
        Discord bot-specific function for awarding points in the form of reactions;
        returns a list of emojis.
        """
        num_emojis = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
        reactions = []
        words = set(re.sub("\W", " ", guess).split())
        points = 0
        pangram = False
        already_gotten = False
        for word in words:
            guess_result = self.guess(word)
            if SpellingBee.GuessJudgement.good_word in guess_result:
                points += 1
            if SpellingBee.GuessJudgement.pangram in guess_result:
                pangram = True
            if SpellingBee.GuessJudgement.already_gotten in guess_result:
                already_gotten = True
        if points > 0:
            reactions.append("👍")
            if points > 1:
                for num_char in str(points):
                    reactions.append(num_emojis[int(num_char)])
        if pangram:
            reactions.append("🍳")
        if already_gotten:
            reactions.append("🤝")
        return reactions

    def persist(self, db_path: PathLike = default_db):
        """Sets a puzzle object up to be saved in the given database. This method
        must be called on an object for it to persist and be returnable by
        retrieve_last_saved. After it is called, the puzzle object will automatically
        update its record in the database whenever its state changes."""
        self.db_path = db_path
        self.save()

    @classmethod
    def get_connection(self, db_path: PathLike) -> Optional[sqlite3.Connection]:
        """Connects to the database, ensures the spelling_bee table exists with the
        correct schema, and returns the connection."""
        if db_path is None:
            return None
        db = sqlite3.connect(db_path)
        cur = db.cursor()
        cur.execute("""create table if not exists spelling_bee
            (day text primary key, center text, outside text, image bytes,
            pangrams text, answers text);""")
        cur.execute("""create index if not exists chrono on spelling_bee (day);""")
        return db

    def save(self):
        """Serializes the puzzle and saves it in a SQLite database."""
        db = self.get_connection(self.db_path)
        if db is None:
            return
        cur = db.cursor()
        cur.execute(
            """insert or replace into spelling_bee
            (day, center, outside, pangrams, answers, image)
            values (?, ?, ?, ?, ?, ?)""",
            (self.day, self.center, json.dumps(list(self.outside)),
             json.dumps(list(self.pangrams)),
             json.dumps(list(self.answers)),
             self.image))
        db.commit()
        db.close()

    @classmethod
    def retrieve_saved(
            cls, db_path: str = default_db, day: str = "latest") -> Optional[SpellingBee]:
        """Retrieves a saved puzzle from the SQLite database. Note that the returned
        object is separate from the database record until/unless persist() is called
        to save it to the same database again."""
        db = cls.get_connection(db_path)
        cur = db.cursor()
        try:
            query = """select
                day, image, center, outside, pangrams, answers
                from spelling_bee """
            if day == "latest":
                query += "order by day desc limit 1"
                parameters = []
            else:
                query += "where day=?"
                parameters = [day]

            fetched = cur.execute(query, parameters).fetchone()
            if fetched is None:
                db.close()
                return None
            else:
                db.close()
                loaded_puzzle = cls(
                    fetched[0],
                    fetched[2],
                    *[json.loads(x) for x in fetched[3:]])
                loaded_puzzle.image = fetched[2]
                return loaded_puzzle
        except:
            print(f"couldn't load spelling bee for \"{day}\" from database")
            traceback.print_exc()
            db.close()
            return None
