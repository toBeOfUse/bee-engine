import asyncio
from pathlib import Path
from .bee import SpellingBee
from .render import BeeRenderer


async def demo():
    current = await SpellingBee.fetch_from_nyt()
    print("current spelling bee letters:")
    print(", ".join([current.center]+current.outside))
    print("full-puzzle hint chart:")
    print(current.get_unguessed_hints().format_all_for_discord())
    print("unacknowledged words:")
    print(current.get_wiktionary_alternative_answers())
    print("saving puzzle graphic to images/tests")
    graphic = await current.render()
    base_path = Path(__file__).parent
    with open(
            base_path/Path("images/tests/today-test."+current.image_file_type),
            "wb+") as image_file:
        image_file.write(graphic)
    current.persist()
    retrieved = SpellingBee.retrieve_saved()
    print("current spelling bee letters after saving and retrieving from database:")
    print(", ".join([retrieved.center]+retrieved.outside))
    # other tests: retrieve from db by id, guess response function


if __name__ == '__main__':
    asyncio.run(demo())
