import asyncio
from pathlib import Path
from . import SpellingBee, SessionBasedSpellingBee

GJ = SpellingBee.GuessJudgement

test_db = Path(__file__).parent/Path("data/test.db")


async def demo():
    current = await SpellingBee.fetch_from_nyt()
    print("current spelling bee letters:")
    print(", ".join([current.center]+current.outside))
    print("full-puzzle hint chart:")
    print(current.get_unguessed_hints(set()).format_all_for_discord())
    print("unacknowledged words:")
    print(current.get_wiktionary_alternative_answers())
    print("saving puzzle graphic to images/tests")
    graphic = await current.render()
    base_path = Path(__file__).parent
    with open(
            base_path/Path("images/tests/today-test."+current.image_file_type),
            "wb+") as image_file:
        image_file.write(graphic)
    current.persist_to(test_db)
    retrieved = SpellingBee.retrieve_saved("latest", test_db)
    print("current spelling bee letters after saving and retrieving from database:")
    print(", ".join([retrieved.center]+retrieved.outside))
    retrieved_by_day = SpellingBee.retrieve_saved(retrieved.day, test_db)
    print("current spelling bee letters after retrieving from database by day:")
    print(", ".join([retrieved_by_day.center]+retrieved_by_day.outside))
    for attr in ["day", "center", "outside", "pangrams", "answers", "image"]:
        try:
            assert (getattr(current, attr) ==
                    getattr(retrieved, attr) ==
                    getattr(retrieved_by_day, attr))
        except AssertionError:
            print(f"problem with {attr} attribute after database retrieval!")
            print(f"values sfor {attr} are:")
            print(getattr(current, attr)[:100])
            print(getattr(retrieved, attr)[:100])
            print(getattr(retrieved_by_day, attr)[:100])

    an_answer = next(filter(lambda x: x not in current.pangrams, current.answers))
    a_pangram = next(iter(current.pangrams))
    assert GJ.good_word in current.guess(an_answer)
    assert GJ.good_word in current.guess(a_pangram)
    assert GJ.good_word in current.guess(a_pangram.capitalize())
    assert GJ.good_word not in current.guess("whangdoodles")  # like, presumably
    assert GJ.pangram not in current.guess(an_answer)
    assert GJ.pangram in current.guess(a_pangram)
    assert GJ.pangram in current.guess(a_pangram.capitalize())
    assert GJ.pangram not in current.guess("whangdoodles")
    assert GJ.wrong_word not in current.guess(an_answer)
    assert GJ.wrong_word not in current.guess(a_pangram)
    assert GJ.wrong_word not in current.guess(a_pangram.capitalize())
    assert GJ.wrong_word in current.guess("whangdoodles")
    assert GJ.already_gotten not in current.guess(an_answer)
    assert GJ.already_gotten in current.guess(an_answer, {an_answer})

    session = SessionBasedSpellingBee(current)
    session.guess(an_answer)
    assert an_answer in session.gotten_words
    assert GJ.already_gotten in session.guess(an_answer)
    pangram_guess = session.guess(a_pangram)
    assert (GJ.pangram in pangram_guess and
            GJ.good_word in pangram_guess and
            GJ.already_gotten not in pangram_guess and
            GJ.wrong_word not in pangram_guess)
    assert GJ.already_gotten in session.guess(a_pangram)
    session.persist_to(test_db)

    retrieved_session = SessionBasedSpellingBee.retrieve_saved(session.session_id, test_db)
    assert an_answer in retrieved_session.gotten_words
    assert a_pangram in retrieved_session.gotten_words
    assert GJ.already_gotten in retrieved_session.guess(an_answer)
    assert GJ.already_gotten in retrieved_session.guess(a_pangram)

    session.make_primary_session()
    assert SessionBasedSpellingBee.get_primary_session_id(test_db) == session.session_id
    assert SessionBasedSpellingBee.retrieve_saved("primary", test_db) == session

    print("demo complete; tests passed")


if __name__ == '__main__':
    try:
        asyncio.run(demo())
    finally:
        test_db.unlink()
