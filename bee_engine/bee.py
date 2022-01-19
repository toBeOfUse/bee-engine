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
from uuid import uuid4 as uuid

from .render import BeeRenderer
from .data_access import get_word_rank
from .trie_explorer.queries import get_wiktionary_trie

import aiohttp
import inflect
inflecter = inflect.engine()


def copula(c: int):
    return inflecter.plural_verb("is", c)


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
    save the puzzle in a simple SQLite database, and can render itself to an image.
    """

    class GuessJudgement(Enum):
        wrong_word = "unaccepted word"
        good_word = "accepted word"
        pangram = "pangram"
        already_gotten = "already gotten"

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
        """
        Constructs the puzzle object. You will probably want to fetch a new puzzle
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
        self.db_path: Optional[str] = None

    def __eq__(self, other):
        return [self.center]+self.outside == [other.center]+other.outside

    def percentage_complete(self, gotten_words: set[str]):
        return round(len(gotten_words) / len(self.answers) * 100, 1)

    def does_word_count(self, word: str) -> bool:
        return word.lower() in self.answers

    def is_pangram(self, word: str) -> bool:
        return word.lower() in self.pangrams

    def guess(self, word: str, gotten_words: Optional[set[str]] = None) -> set[SpellingBee.GuessJudgement]:
        """
        Determines whether a word counts for a point and/or is a pangram and/or has
        already been gotten; returns the result using the GuessJudgement enum inner
        class. It automatically adds to the gotten_words set you pass in.
        """
        result = set()
        w = word.lower()
        if self.does_word_count(w):
            result.add(self.GuessJudgement.good_word)
            if self.is_pangram(w):
                result.add(self.GuessJudgement.pangram)
            if gotten_words is not None:
                if w in gotten_words:
                    result.add(self.GuessJudgement.already_gotten)
                gotten_words.add(w)
        else:
            result.add(self.GuessJudgement.wrong_word)
        return result

    def get_unguessed_words(
        self, 
        gotten_words: set[str], 
        sort_key=get_word_rank
    ) -> list[str]:
        """
        Returns the heretofore unguessed words in a list sorted from the least
        to the most common words. Or you can pass in your own sort key function,
        or None for no sorting.
        """
        unguessed = list(self.answers - gotten_words)
        if sort_key is not None:
            unguessed.sort(key=sort_key, reverse=True)
        return unguessed

    def get_hints(self) -> SpellingBee.HintTable:
        return self.HintTable(list(self.answers))

    def get_unguessed_hints(self, gotten_words: set[str]) -> SpellingBee.HintTable:
        return self.HintTable(self.get_unguessed_words(gotten_words, None))

    def get_wiktionary_alternative_answers(self) -> list[str]:
        """
        Returns the words that use the required letters and are English words
        according to Wiktionary (according to data obtained by
        https://github.com/tatuylonen/wiktextract) but aren't in the official answers
        list, sorted from longest to shortest.
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

    async def render(self, renderer_name: str = "") -> bytes:
        """
        Renders the puzzle to an image; returns the image file as bytes and
        caches it in the image instance variable. If you do not pass in the
        string name of a BeeRenderer, one will be chosen at random. You can find
        out what image format was used by accessing image_file_type.
        """
        if renderer_name == "":
            renderer = BeeRenderer.get_random_renderer()
        else:
            renderer = BeeRenderer.get_renderer(renderer_name)
        self.image = await renderer.render(self)
        self.save()
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

    def respond_to_guesses(self, guess: str, already_gotten: set[str]=set()) -> list[str]:
        """
        A wrapper around the `guess` method that takes a string with,
        potentially, multiple words in it instead of just one, and returns
        emojis instead of string constants. The list of emojis returned starts
        with a thumbs_up if there are any accepted answers, then number emojis
        signifying the count if there are more than one accepted answers, then a
        fried egg emoji if any of the accepted answers are pangrams, then a
        handshake emoji if any of the words pulled out of `guess` are also in
        `already_gotten`. As when using `guess`, the new words are then added to
        `already_gotten`.
        """
        num_emojis = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
        reactions = []
        words = set(re.sub("\W", " ", guess).split())
        points = 0
        pangram = False
        already_gotten = False
        for word in words:
            guess_result = self.guess(word, already_gotten)
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

    def list_words(
        self,
        words: set[str],
        separate_pangrams=True,
        enclose_with: list[str] = ["", ""],
        initial_capital=False
    ) -> str:
        """Displays a formatted list containing the valid answers out of the set of
        words that you pass in listed in alphabetical order.

        Args:
            words (set[str]): words!
            separate_pangrams (bool, optional): Moves the pangrams to the end of the
            list and precedes them with the text "Pangrams: ". Defaults to True.
            enclose_with (list[str], optional): allows you to automatically surround
            the words with tags like ["<em>", "</em>"] or ["||", "||"]. Defaults to
            ["", ""].
            initial_capital (bool, optional): Starts the string off with a capital
            letter. Defaults to False.

        Returns:
            Something like "Game, fame, lame, and same. Pangrams: medieval."
        """
        matching_words = words & self.answers
        found_words = sorted(
            list(
                matching_words-(self.pangrams if separate_pangrams else set())
            )
        )
        listed = inflecter.join(found_words)
        if initial_capital:
            listed = listed.capitalize
        listed = enclose_with[0]+listed+"."+enclose_with[1]
        if separate_pangrams:
            found_pangrams = sorted(list(matching_words & self.pangrams))
            if len(found_pangrams) > 0:
                listed += (
                    " Pangrams: " +
                    enclose_with[0] +
                    inflecter.join(found_pangrams) +
                    "." +
                    enclose_with[1]
                )
        return listed

    def persist_to(self, db_path: PathLike = default_db):
        """Sets a puzzle object up to be saved in the given database. This method
        must be called on a SpellingBee object for it to persist and be returnable by
        retrieve_saved. After it is called, the puzzle object will automatically
        update its record in the database whenever its state changes. Note:
        SessionBased and SingleSession spelling bees take a db path in their
        constructors and are persistent by default."""
        self.db_path = db_path
        self.save()

    @classmethod
    def get_connection(self, db_path: PathLike) -> Optional[sqlite3.Connection]:
        """Connects to the database, ensures the spelling_bee table exists with the
        correct schema, and returns the connection."""
        if db_path is None:
            return None
        db = sqlite3.connect(db_path, uri=True)
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
            cls, day: str = "latest", db_path: str = default_db) -> Optional[SpellingBee]:
        """Retrieves a saved puzzle from the SQLite database. Note that the returned
        object is separate from the database record until/unless persist_to() is called
        to save it to the same database again."""
        db = cls.get_connection(db_path)
        cur = db.cursor()
        try:
            query = """select day, center, outside, pangrams, answers, image
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
                    fetched[1],
                    json.loads(fetched[2]),
                    json.loads(fetched[3]),
                    json.loads(fetched[4])
                )
                loaded_puzzle.image = fetched[5]
                return loaded_puzzle
        except:
            print(f"couldn't load spelling bee for \"{day}\" from database")
            traceback.print_exc()
            db.close()
            return None


class SessionBee(SpellingBee):
    """
    Extends the SpellingBee class by also storing a set of successful guesses
    so far that are associated with a specific session UUID. These guesses can
    also be persisted in the database, with the session ID being the key used
    for retrieval. You can store a "primary" session ID using the class method
    `save_primary_session_id`. Along with the guesses, an arbitrary dict
    (`metadata`) is stored, that can be used for any non-puzzle-related
    information that you want to store. The contents of the dict must be
    JSON-serializable.
    """

    def __init__(
            self,
            base: SpellingBee,
            gotten_words: set[str] = None,
            metadata: dict = {}):
        """
        Constructs a new SessionBee object with a unique string ID
        and arbitrary starting data.
        """
        super().__init__(
            base.day, base.center, base.outside, base.pangrams, base.answers
        )
        self.image = base.image
        self.gotten_words = gotten_words if gotten_words is not None else set()
        self.session_id: str = str(uuid())
        self.db_path = None
        self._metadata = metadata

    @property
    def metadata(self):
        return self._metadata

    @metadata.setter
    def set_metadata(self, new_data: dict):
        self._metadata = new_data
        self.save_session()

    @classmethod
    def get_connection(self, db_path: PathLike) -> Optional[sqlite3.Connection]:
        conn = super().get_connection(db_path)
        if conn is None:
            return None
        cur = conn.cursor()
        cur.execute("""create table if not exists bee_sessions
            (session_id text primary key, day text, gotten text, metadata text);""")
        exists = cur.execute(
            """select name from sqlite_master where
                type='table' AND name='primary_session_id';""").fetchone()
        if exists is None:
            cur.execute("""create table if not exists primary_session_id
                (primary_session_id text primary key);""")
            cur.execute("""insert into primary_session_id (primary_session_id)
                values ('None');""")
            conn.commit()
        return conn

    @classmethod
    def save_primary_session_id(cls, session_id: str, db_path: str):
        """
        This method gives you the ability to save a specific session id into
        the database to a table devoted to storing the current "primary"
        session. You can then retrieve the session with this ID later, by
        calling `retrieve_saved` with `"primary"` as the `session_id`
        argument. This is intended as a convenience method for anyone who only
        wants to persist a single session at once.
        """
        conn = cls.get_connection(db_path)
        conn.execute(
            "update primary_session_id set primary_session_id=?;",
            (session_id,)
        )
        conn.commit()
        conn.close()
    
    @classmethod
    def get_primary_session_id(cls, db_path: str) -> Optional[str]:
        """
        Gets the primary session ID saved into the given database with
        `save_primary_session_id`, if any. If you want to retrieve the session
        itself, you can call `retrieve_saved` with `"primary"` as the
        `session_id` argument.
        """
        conn = cls.get_connection(db_path)
        session_id = conn.execute(
            "select primary_session_id from primary_session_id;"
        ).fetchone()[0]
        conn.close()
        if session_id == "None":
            return None
        else:
            return session_id
    
    def make_primary_session(self):
        """
        Convenience method to call `save_primary_session_id` with this session's
        ID and database file. This only has an effect if this object has already
        had its database file path set with a call to `persist_to`. This is
        because I assume that if you don't need persistence, you can keep track
        of a specific session just by storing it in a variable called `primary`
        or something.
        """
        if self.db_path is None:
            return
        SessionBee.save_primary_session_id(
            self.session_id, self.db_path
        )
        
    def save_session(self):
        if self.db_path is None:
            return
        conn = self.get_connection(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """insert or replace into bee_sessions (session_id, day, gotten, metadata)
            values (?, ?, ?, ?);""",
            (self.session_id, self.day, json.dumps(list(self.gotten_words)),
             json.dumps(self.metadata)))
        conn.commit()
        conn.close()

    def save(self):
        """
        This method saves the puzzle and the current set of guesses into the
        database. There should be no need to call this manually; just call
        persist_to with a database path and all changes will be auto-saved then
        and thereafter.
        """
        super().save()
        self.save_session()

    @classmethod
    def retrieve_saved(
            cls, 
            session_id: str, 
            db_path: str = default_db
        ) -> Optional[SessionBee]:
        """
        Method allowing you to retrieve a specific session from the database.
        If you only want to persist one session at a time and don't want to
        track session IDs yourself, just use `specifiy_primary_session` to save
        an ID in a database, and then retrieve the session with that ID from the
        database by putting in `"primary"` as the `session_id` argument here.
        Note that you will still have to call `persist_to` for further changes
        to this session to be saved in the database.
        """
        conn = cls.get_connection(db_path)
        cur = conn.cursor()
        if session_id == "primary":
            session_id = SessionBee.get_primary_session_id(db_path)
            if session_id is None:
                return None
        active_session = cur.execute(
            "select day, gotten, metadata from bee_sessions where session_id=?;",
            (session_id, )
        ).fetchone()
        conn.close()
        if active_session is None:
            return None
        base = SpellingBee.retrieve_saved(active_session[0], db_path)
        if base is None:
            return None
        gotten = set(json.loads(active_session[1]))
        metadata = json.loads(active_session[2])
        result = cls(base, gotten, metadata)
        result.session_id = session_id
        return result

    @classmethod
    async def fetch_from_nyt(cls) -> SessionBee:
        """Convenience method for fetching a SpellingBee from the NYT and
        creating a new session for it in one swoop."""
        return cls(await SpellingBee.fetch_from_nyt())

    @property
    def percentage_complete(self):
        return super().percentage_complete(self.gotten_words)

    def guess(self, word: str) -> set[SpellingBee.GuessJudgement]:
        result = super().guess(word, self.gotten_words)
        self.save_session()
        return result

    def get_unguessed_words(self, sort_key=get_word_rank) -> list[str]:
        return super().get_unguessed_words(self.gotten_words, sort_key)

    def get_unguessed_hints(self) -> SpellingBee.HintTable:
        return super().get_unguessed_hints(self.gotten_words)

    def list_gotten_words(
            self, separate_pangrams=True, enclose_with: list[str] = ["", ""],
            initial_capital=False) -> str:
        """Lists the words gotten in this session so far in accordance with the
        formatting rules documented in the superclass method."""
        return super().list_words(
            self.gotten_words,
            separate_pangrams,
            enclose_with,
            initial_capital
        )
    
    def respond_to_guesses(self, guess: str) -> list[str]:
        return super().respond_to_guesses(guess, self.gotten_words)
