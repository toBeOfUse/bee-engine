from setuptools import setup
from setuptools.extension import Extension
from Cython.Build import cythonize

trie_explorer = Extension(
    name="bee_engine.trie_explorer.trieparse",
    sources=[
        "bee_engine/trie_explorer/trieparse.pyx",
        "bee_engine/trie_explorer/src/trieparse.c"],
    include_dirs=["bee_engine/trie_explorer/src/"])

setup(
    name='bee_engine',
    version='0.9.6',
    packages=['bee_engine', 'bee_engine.trie_explorer'],
    install_requires=["inflect", "cairosvg",
                      "pillow", "aiohttp[speedups]", "numpy"],
    package_data={
        "": [
            "images/*.svg",
            "images/*.png",
            "images/*.json",
            "images/fonts/**/*.*",
            "images/fonts/*.ttf",
            "data/*.txt",
            "trie_explorer/*.txt",
            "data/words.db",
            # these last two aren't data but are apparently required to be here :|
            "**/*.pyx",
            "bee_engine/trie_explorer/src/trieparse.h"],
    },
    ext_modules=cythonize([trie_explorer])
)
