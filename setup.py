from setuptools import setup

setup(
    name='bee_engine',
    version='0.5.0',
    packages=['bee_engine'],
    install_requires=["inflect", "cairosvg", "pillow", "aiohttp[speedups]"],
)
