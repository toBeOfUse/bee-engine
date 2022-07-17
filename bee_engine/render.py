from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
import json
import re
from typing import TYPE_CHECKING, Optional
if TYPE_CHECKING:
    from bee import SpellingBee

from os import PathLike
from pathlib import Path
import base64
import asyncio
import abc
from timeit import default_timer
from xml.dom import minidom
import sys
import random
import math

from cairosvg import svg2png
from PIL import Image, ImageFont, ImageDraw
import numpy as np

wd = Path(__file__).parent


def make_base(width: float, height: float) -> list[float]:
    """outputs the points for a hexagon centered on 0, 0 with the specified width and
        height. for a regular hexagon, height should be sqrt(3)/2 times the width.
        points go clockwise starting from the leftmost. the top and bottom sides are
        parallel to the the x-axis."""
    return [(-width / 2, 0),
            (-width/4, -height/2),
            (width/4, -height/2),
            (width/2, 0),
            (width/4, height/2),
            (-width/4, height/2)]


def make_hexagon(
        centered_on: tuple[float, float],
        radius: float = 10, tilted: bool = False) -> list[tuple[int, int]]:
    width = radius*2
    height = width*(math.sqrt(3)/2)
    points = [(x+centered_on[0], y+centered_on[1])
              for x, y in make_base(width, height)]
    if tilted:
        points = [tuple(reversed(x)) for x in points]
    return points


class BeeRenderer(metaclass=abc.ABCMeta):
    """
    Base class for subclasses to override; they should implement __init__, render,
    and __repr__. Class methods are provided to organize instances of subclasses with.
    """
    available_renderers: list[BeeRenderer] = []
    _renderer_lookup: defaultdict[str, BeeRenderer] = defaultdict(lambda: None)

    @classmethod
    def get_random_renderer(cls):
        return random.choice(cls.available_renderers)

    @classmethod
    def get_renderer(cls, name: str) -> Optional[BeeRenderer]:
        return cls._renderer_lookup[name]

    @classmethod
    def register_renderer(cls, name: str, renderer: BeeRenderer):
        cls.available_renderers.append(renderer)
        cls._renderer_lookup[name] = renderer

    @classmethod
    def get_available_renderer_names(cls) -> list[str]:
        return list(cls._renderer_lookup.keys())

    @abc.abstractmethod
    def __init__(self):
        pass

    @abc.abstractmethod
    async def render(self, puzzle: SpellingBee, output_width: int) -> bytes:
        pass

    @abc.abstractmethod
    def __repr__(self) -> str:
        pass


class SVGTemplateRenderer(BeeRenderer):
    output_format = "png"

    def __init__(self, template_path: PathLike):
        self.template_path = template_path
        with open(template_path) as base_file:
            self.base_svg = base_file.read()

    def __repr__(self):
        return f"{self.__class__.__name__} for {Path(self.template_path).name}"

    def __eq__(self, other: SVGTemplateRenderer):
        return self.base_svg == other.base_svg


class SVGTextTemplateRenderer(SVGTemplateRenderer):
    class TextElement:
        def __init__(self, base: minidom.Element):
            assert base.tagName == "text"
            self.base = base

        def _get_only_text_node(self) -> minidom.Text:
            node = self.base.firstChild
            while type(node) is not minidom.Text:
                node = node.firstChild
                if node is None:
                    return None
            return node

        def is_placeholder(self) -> bool:
            """Detects svg <text> elements that are formatted to hold placeholder text
            for Puzzle letters. Such elements are expected to have content starting with a $."""
            text_node = self._get_only_text_node()
            return text_node is not None and (
                text_node.nodeValue == "$L" or
                text_node.nodeValue == "$C")

        def get_text(self) -> str:
            return self._get_only_text_node().nodeValue

        def set_text(self, new_text: str) -> None:
            self._get_only_text_node().nodeValue = new_text

    @staticmethod
    def get_other_elements_in_group(
            text_element: SVGTextTemplateRenderer.TextElement) -> list[
            SVGTextTemplateRenderer.TextElement]:
        """For placeholder <text> elements, detects whether they are in an SVG group
        (<g> tag with class "letter_group") with other placeholder <text> siblings
        and returns the other placeholder <text> siblings if so, returning an empty
        list otherwise."""
        text_element = text_element.base
        parent: minidom.Element = text_element.parentNode
        if parent.tagName == "g" and parent.getAttribute("class") == "letter_group":
            text_element_children = [
                SVGTextTemplateRenderer.TextElement(x) for x in parent.childNodes
                if type(x) is minidom.Element and x.tagName == "text"]
            placeholder_children = [
                x for x in text_element_children if x.is_placeholder()]
            return placeholder_children
        return []

    async def render(self, puzzle: SpellingBee, output_width: int = 1200) -> bytes:
        """Finds placeholder <text> nodes (those with "$L" or "$C" as their content)
        in the SVG file passed to the constructor and replaces that content with the
        letters from the puzzle. If multiple placeholder <text> nodes are in a
        g.letter_group group), they are all set to the same letter."""
        letters = iter(puzzle.outside)
        base: minidom.Document = minidom.parseString(self.base_svg)
        for text_element in map(self.TextElement, base.getElementsByTagName("text")):
            if text_element.is_placeholder():
                if text_element.get_text() == "$C":
                    text_element.set_text(puzzle.center)
                    for sibling in self.get_other_elements_in_group(text_element):
                        sibling.set_text(puzzle.center)
                elif text_element.get_text() == "$L":
                    letter = next(letters)
                    text_element.set_text(letter)
                    for sibling in self.get_other_elements_in_group(text_element):
                        sibling.set_text(letter)
        return svg2png(base.toxml(encoding="utf-8"), output_width=output_width)


class SVGImageTemplateRenderer(SVGTemplateRenderer):
    def __init__(self, template_path: PathLike, alphabet_path: PathLike):
        super().__init__(template_path)
        self.alphabet_path = alphabet_path

    async def render(self, puzzle: SpellingBee, output_width: int = 1200) -> bytes:
        center_placeholder_pixel = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ" +
            "AAAADUlEQVR42mP8/5fhPwAH/AL9Ow9X5gAAAABJRU5ErkJggg=="
        )
        outside_placeholder_pixel = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1" +
            "HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with open(Path(
            self.alphabet_path,
            puzzle.center.lower()+".png"
        ), "rb") as center_letter_file:
            center_letter = base64.b64encode(
                center_letter_file.read()).decode('ascii')
            base_svg = self.base_svg.replace(
                center_placeholder_pixel, center_letter)
        for letter in puzzle.outside:
            with open(Path(
                self.alphabet_path,
                letter.lower()+".png"
            ), "rb") as letter_file:
                letter_image = base64.b64encode(
                    letter_file.read()).decode('ascii')
                base_svg = base_svg.replace(
                    outside_placeholder_pixel, letter_image, 1)
        return svg2png(base_svg, output_width=output_width)


class PerspectiveRenderer(BeeRenderer):
    output_format = "png"

    @dataclass
    class Frame:
        matrix: np.array[np.array[float]]
        is_center: bool

    class LetterColors:
        def __init__(
            self,
            fill: str | tuple[int] = "white",
            stroke: str | tuple[int] = "black",
            stroke_width: int = 3
        ):
            self.fill = fill
            self.stroke = stroke
            self.stroke_width = stroke_width

    def __init__(
        self,
        frames: list[Frame],
        bg_image_path: PathLike,
        outer_letter_color: LetterColors = LetterColors(),
        center_letter_color: LetterColors = LetterColors(),
        font_resolution: int = 200
    ):
        self.frames = frames
        self.bg_image_path = bg_image_path
        self.outer_letter_color = outer_letter_color
        self.center_letter_color = center_letter_color
        self.font_resolution = font_resolution

    @classmethod
    def from_aafine_file(
        cls,
        file_path: PathLike,
        outer_letter_color: LetterColors = LetterColors(),
        center_letter_color: LetterColors = LetterColors(),
        font_resolution: int = 200
    ):
        """
        Reads files produced by my aafine app; clone from
        https://github.com/toBeOfUse/aaffine or use the web version at
        https://mitch.website/perspective/. The project name is expected to
        contain the filename of the background image which should be in
        ./images/. The name of the center frame is expected to contain the
        string "center".
        """
        with open(file_path, encoding="utf-8") as aafine_file:
            data = json.load(aafine_file)
        frames = []
        for frame_data in data["frames"]:
            frames.append(
                PerspectiveRenderer.Frame(
                    np.array(frame_data["3x3ReverseMatrixNormalized"]),
                    "center" in frame_data["name"].lower()
                )
            )
        assert len(
            frames) == 7, f"not enough frames in aafine file {file_path}"
        assert len([x for x in frames if x.is_center]
                   ) == 1, f"wrong number of central frames in aafine file {file_path}"
        return cls(
            frames,
            wd/f"images/{data['name']}",
            outer_letter_color,
            center_letter_color,
            font_resolution
        )

    def __repr__(self):
        return f"PerspectiveRenderer for image {self.bg_image_path}"

    async def render(self, puzzle: SpellingBee, output_width: int = 1200) -> bytes:
        bg = Image.open(self.bg_image_path).convert("RGBA")
        font = ImageFont.truetype(
            str(wd/"images/fonts/LiberationSans-Bold.ttf"),
            self.font_resolution
        )

        for letter, frame in zip(
            [puzzle.center] + puzzle.outside,
            [x for x in self.frames if x.is_center] +
                list(filter(lambda x: not x.is_center, self.frames)),
            strict=True
        ):
            canvas = Image.new(
                mode="RGBA",
                size=(self.font_resolution,)*2,
                color=(0, 0, 0, 0)
            )
            color = (
                self.center_letter_color if frame.is_center else
                self.outer_letter_color
            )
            ImageDraw.Draw(canvas).text(
                xy=(round(self.font_resolution/2),)*2,
                text=letter.capitalize(),
                font=font,
                anchor="mm",
                fill=(0, 0, 0, 0) if color.fill is None else color.fill,
                stroke_width=(
                    0 if color.stroke is None else
                    (3 if color.stroke_width is None else
                     color.stroke_width)),
                stroke_fill=color.stroke
            )
            normalize_screen_space = np.array(
                [[1/bg.width, 0, 0], [0, 1/bg.height, 0], [0, 0, 1]],
            )
            to_object_space_pixels = np.array(
                [[self.font_resolution, 0, 0],
                    [0, self.font_resolution, 0], [0, 0, 1]]
            )
            coeffs = to_object_space_pixels.dot(
                frame.matrix.dot(normalize_screen_space))
            placed_letter = canvas.transform(
                size=(bg.width, bg.height),
                method=Image.PERSPECTIVE,
                data=[*coeffs[0], *coeffs[1], coeffs[2][0], coeffs[2][1]],
                resample=Image.BICUBIC
            )
            bg.alpha_composite(placed_letter)

        scale_factor = output_width/bg.width
        bg = bg.resize(
            (round(bg.width*scale_factor), round(bg.height*scale_factor)),
            resample=Image.LANCZOS
        )
        output = BytesIO()
        bg.save(output, format="png")
        output.seek(0)
        return output.read()


for path in (wd/"images/").glob("puzzle_template_*.svg"):
    name = re.match("^puzzle_template_(\w*?).svg$", path.name).group(1)
    BeeRenderer.register_renderer(name, SVGTextTemplateRenderer(str(path)))

BeeRenderer.register_renderer(
    "sketchbook",
    SVGImageTemplateRenderer(
        wd/"images/image_puzzle_template_1.svg",
        wd/"images/fonts/pencil/")
)

BeeRenderer.register_renderer(
    "dice",
    PerspectiveRenderer.from_aafine_file(wd/"images/dice.json")
)

BeeRenderer.register_renderer(
    "cereal",
    PerspectiveRenderer.from_aafine_file(
        wd/"images/cereal.json",
        PerspectiveRenderer.LetterColors("#ddd", "black"),
        PerspectiveRenderer.LetterColors("white", "black"),
    )
)

BeeRenderer.register_renderer(
    "earth",
    PerspectiveRenderer.from_aafine_file(
        wd/"images/earth.json",
        PerspectiveRenderer.LetterColors("#fff8", "black", 6),
        PerspectiveRenderer.LetterColors("white", None)
    )
)


async def test():
    from .bee import SpellingBee
    base_path = Path(__file__).parent
    print("available renderers:")
    renderers = BeeRenderer.get_available_renderer_names()
    print(renderers)
    letters = random.sample(["B", "C", "D", "E", "F", "G"], 6)
    if len(sys.argv) > 1:
        print(f"looking for renderers with {sys.argv[1]} in name")
    else:
        print(f"{len(renderers)} renderers available. testing...")
    for r in renderers:
        if len(sys.argv) > 1:
            if sys.argv[1].lower() not in str(r).lower():
                continue
        start = default_timer()
        test_puzzle = SpellingBee(-1, "A", letters, [], [])
        render = await test_puzzle.render(r)
        type = test_puzzle.image_file_type
        renderer_name_slug = str(r).replace(
            " ", "_").replace("\\", "-").replace("/", "-")
        with open(
                base_path/Path(f'images/tests/{renderer_name_slug}.{type}'),
                "wb+") as output:
            output.write(render)
        print(r, "took", round((default_timer()-start)*1000), "ms")

if __name__ == "__main__":
    asyncio.run(test())
