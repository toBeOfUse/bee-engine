from setuptools import setup
from setuptools.extension import Extension
from Cython.Build import cythonize

trie_explorer = Extension(
    name="bee_engine.trie_explorer.trieparse",
    sources=["bee_engine/trie_explorer/trieparse.pyx", "bee_engine/trie_explorer/src/trieparse.c"],
    include_dirs=["bee_engine/trie_explorer/src/"])

setup(
    name='bee_engine',
    version='0.5.0',
    packages=['bee_engine'],
    install_requires=["inflect", "cairosvg", "pillow", "aiohttp[speedups]"],
    include_package_data=True,
    ext_modules=cythonize([trie_explorer])
)
