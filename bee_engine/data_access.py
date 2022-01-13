import sqlite3
from math import inf
from pathlib import Path

words_db_path = Path(__file__).parent / Path("data/words.db")
words_db = sqlite3.connect(words_db_path)


def get_word_rank(word: str) -> int:
    """
    Exposes the word frequency data stored in words.db to easy python access. The
    lower the rank, the more common the word.
    """
    cur = words_db.cursor()
    rank = cur.execute(
        "select rank from words where word=?",
        (word.lower(),)
    ).fetchone()
    return inf if rank is None else rank[0]
