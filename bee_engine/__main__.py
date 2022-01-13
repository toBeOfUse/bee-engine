import asyncio
from .bee import SpellingBee


async def demo():
    current = await SpellingBee.fetch_from_nyt()
    print("current spelling bee letters:")
    print(", ".join([current.center]+current.outside))
    # other tests: save to db and retrieve by id, render graphics and save somewhere,
    # test guess response function


if __name__ == '__main__':
    asyncio.run(demo())
