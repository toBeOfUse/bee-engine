# Bee Engine

This package lets you use Python to load, store, display, and interact with the Spelling Bee puzzles that are posted by the New York Times.

## Usage:

The letters and the words from the 1/16/2022 puzzle will be used as examples, here.

### Basic Information:

```python3
from bee_engine import SpellingBee
async def demo():
    # Basic information
    puzzle = await SpellingBee.fetch_from_nyt()
    print(puzzle.date)                         # "2022-01-16"
    print(puzzle.center)                       # "H"
    print(puzzle.outside)                      # ["U","D","C","E","K","N"]

    # Word checking
    print(puzzle.does_word_count("hunk"))      # True
    print(puzzle.does_word_count("zamboni"))   # False
    print(puzzle.is_pangram("chunked"))        # True
    print(puzzle.is_pangram("hence"))          # False
    print(puzzle.is_pangram("hudcekn"))        # False

    # The guess method returns constants defined by SpellingBee.GuessJudgement
    print(puzzle.guess("hunk"))                # {"accepted word"}
    print(puzzle.guess("chunked"))             # {"accepted word", "pangram"}

    print(puzzle.guess("chunked", {"hunk", "chunked"}))
    # {"accepted word", "already gotten", "pangram}

    print(puzzle.guess("batarang"))            # {"unaccepted word"}
```

### Obtaining Hints:

```python3
# The hint table is accessible too, through the SpellingBee.HintTable class:
print(puzzle.get_hints().format_table())
""" 4  5  6  7  8  9  Σ
    C  -  4  -  3  -  -  7
    E  -  -  1  -  -  -  1
    H  4  2  1  1  -  -  8
    N  -  -  -  1  1  -  2
    U  -  -  -  1  1  1  3
    Σ  4  6  2  6  2  1  21"""
# (no leading spaces are present in the actual outputted lines)

print(puzzle.get_hints().format_two_letters())
# 'Ch: 7, Eu: 1, He: 4, Hu: 4, Nu: 2, Un: 3'

print(puzzle.get_hints().format_pangram_count())
# 'There are two remaining pangrams.'
```

### The flashy stuff:

```python3
# Just for fun, a list of words that are on Wiktionary and have all the right letters
# but aren't accepted:

print(puzzle.get_wiktionary_alternative_answers())
['unhunched', 'unchunked', 'dechunked', 'dhunchee', 'eunuched', 'chudded', 'dechunk', 'neechee', 'dudheen', 'unhunch', 'cheekee', 'dunched', 'henched', 'deeched', 'cheeked', 'kuchen', 'unheed', 'huchen', 'henned', 'henced', 'hended', 'cheeke', 'cheche', 'hucked', 'unhued', 'eched', 'huced', 'dench', 'cunch', 'ehhed', 'hench', 'khene', 'dunch', 'uhhuh', 'euche', 'hende', 'henne', 'deech', 'kench', 'keech', 'hudud', 'hehe', 'khen', 'huck', 'huhu', 'enuh', 'nuch', 'ehhh', 'hunh', 'hend', 'khud', 'hede', 'eche', 'chek', 'unch', 'ehed', 'uhuh', 'huke', 'chud', 'kueh', 'heuk']

# And for display: there are several available rendering templates.
from bee_engine import BeeRenderer

print(BeeRenderer.get_available_renderer_names())
# ['colorpicker', 'davinci', 'ezersky', 'hexspin', 'honey', 'minecraft', 'pizza', 'rulers', 'telescope', 'worksheet']

async def render_demo():
    with open("example1.png", "wb+") as image_file:
        image = await BeeRenderer.get_renderer("colorpicker").render(puzzle, output_width=500)
        image_file.write(image)
asyncio.run(render_demo())
```

![Example puzzle graphic with Windows XP Color-picker background](example1.png)

```python3
async def other_render_demo():
    with open("example2.png", "wb+") as image_file:
        image = await BeeRenderer.get_renderer("rulers").render(puzzle)
        image_file.write(image)
asyncio.run(other_render_demo())
```

![Example puzzle graphic with a hexagon rulers motif](example2.png)

```python3
# Currently, only PNGs are rendered, but for future proofing this property exists:
print(BeeRenderer.get_renderer("rulers").output_format) # "png"

# Alternatively, you can store the rendered image in the puzzle object:
async def store_render_test(): await puzzle.render("rulers")
asyncio.run(test())
print(type(puzzle.image))         # <class 'bytes'>
print(puzzle.image_file_type)     # "png"
```

### Basic persistence:

```python3
# You can persist puzzles, including their images, easily:
puzzle.set_db("mypuzzles.db")
puzzle.save()
# At any point in the future when that file still exists:
retrieved_puzzle = SpellingBee.retrieve_saved("latest", "mypuzzles.db")
# or if you previously saved the puzzle from this date:
older_puzzle = SpellingBee.retrieve_saved("2022-01-01", "mypuzzles.db")
```

### Session-Based Persistence:

```python3
# If you only want to keep track of one guessing session at a time:
from bee_engine import SingleSessionSpellingBee
async def session_demo():
    game = await SingleSessionSpellingBee.fetch_from_nyt("mypuzzles.db")
    game.guess("hunk")
    game.guess("chunk")
    print(game.gotten_words)                # {"hunk", "chunk"}

    # At any later point in history:
    retrieved_game = SingleSessionSpellingBee.retrieve_saved("mypuzzles.db")
    print(retrieved_game.gotten_words)      # {"hunk", "chunk"}

    # Only the last SingleSessionSpellingBee is available in the database, but there is another
    # type that can deal with multiple guessing sessions:
    from bee_engine import SessionBasedSpellingBee

    sesh1 = SessionBasedSpellingBee.fetch_from_nyt("mypuzzles.db")
    print(sesh1.session_id)   # 1
    sesh2 = SessionBasedSpellingBee.fetch_from_nyt("mypuzzles.db")
    print(sesh2.session_id)   # 2

    # then later:
    SessionBasedSpellingBee.retrieve_saved(1, "mypuzzles.db")
    # and/or:
    SessionBasedSpellingBee.retrieve_saved(2, "mypuzzles.db")

    # they can even interoperate with SingleSessions, since SingleSessionSpellingBee objects
    # are just SessionBasedSpellingBee objects that all automatically access the same session
    # by keeping track of the ID of the latest session created through their class method:
    interop = SessionBasedSpellingBee.retrieve_saved(game.session_id, "mypuzzles.db")
    print(game.session_id)                # 0
    print(interop.session_id)             # 0
    print(interop.guessed_words)          # {"hunk", "chunk"}
```

And those are the main points.
