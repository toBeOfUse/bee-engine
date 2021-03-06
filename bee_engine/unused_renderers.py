class PerspectiveCompositeRenderer(BeeRenderer):
    def __init__(self, fg_renderer: BeeRenderer, bg_path: PathLike, perspective_data: PerspectiveCoefficients):
        self.fg_renderer = fg_renderer
        self.bg_path = bg_path
        self.perspective_data = perspective_data
    
    def __repr__(self):
        return f"{self.__class__.__name__} combining \"{self.fg_renderer}\" and \"{self.bg_path}\""
    
    async def render(self, puzzle: SpellingBee, output_width: int=1200) -> bytes:
        bg_image = Image.open(self.bg_path)
        fg_bytes = await self.fg_renderer.render(puzzle)
        fg_image = Image.open(BytesIO(fg_bytes)).transform(
            (bg_image.width, bg_image.height),
            Image.PERSPECTIVE,
            self.perspective_data,
            Image.BICUBIC
        )
        bg_image.alpha_composite(fg_image)
        scale_factor = output_width/bg_image.width
        bg_image = bg_image.resize(
            (output_width, round(bg_image.height*scale_factor)),
            resample=Image.LANCZOS
        )
        output = BytesIO()
        bg_image.save(output, "png")
        output.seek(0)
        return output.read()

class MultiPerspectiveRenderer(BeeRenderer):
    """
    Each letter is rendered according to basic_letter.svg to a 200x170 image
    (after downsampling) and then transformed by one of the perspective
    coefficient lists
    """
    def __init__(self,
     bg_path: PathLike, 
     outer_persepctive: list[PerspectiveCoefficients], 
     center_perspective: PerspectiveCoefficients
    ):
        self.bg_path = bg_path
        self.outer_perspective = outer_persepctive
        self.center_perspective = center_perspective
    
    def __repr__(self):
        return f"{self.__class__.__name__} for {self.bg_path}"
    
    async def render(self, puzzle: SpellingBee, output_width: int=1200) -> bytes:
        composite = Image.open(self.bg_path)
        with open(Path(__file__).parent/"images/basic_letter.svg", "r", encoding="utf-8") as template_file:
            template = template_file.read()
        def pipeline(image: Image.Image, perspective_data: PerspectiveCoefficients):
            return image.resize(
                (200, 170),
                Image.LANCZOS
            ).transform(
                (composite.width, composite.height), 
                Image.PERSPECTIVE,
                perspective_data,
                Image.BICUBIC
            )
        for i in range(6):
            letter = puzzle.outside[i]
            svg = template.replace("$L", letter.upper())
            letter_image = svg2png(svg, output_width=800)
            transformed = pipeline(Image.open(
                BytesIO(letter_image)
            ), self.outer_perspective[i])
            composite.alpha_composite(transformed)
        final_svg = template.replace("$L", puzzle.center.upper())
        final_image = Image.open(BytesIO(svg2png(final_svg, output_width=800)))
        composite.alpha_composite(pipeline(final_image, self.center_perspective))
        scale_factor = output_width/composite.width
        composite = composite.resize(
            (output_width, round(composite.height*scale_factor)),
            Image.LANCZOS
        )
        output = BytesIO()
        composite.save(output, "png")
        output.seek(0)
        return output.read()

class GIFTemplateRenderer(BeeRenderer):
    def __init__(
            self, first_frame_file: str, gif_file: str,
            center_coords: tuple[int, int],
            text_radius: float,
            font_size: int = 50):
        self.gif_file = gif_file
        self.first_frame_file = first_frame_file
        self.text_radius = text_radius
        self.center_coords = center_coords
        self.font_size = font_size

    def __repr__(self):
        return f"GIFTemplateRenderer for {self.gif_file}"

    async def render(self, puzzle: SpellingBee) -> bytes:
        base = Image.open(self.first_frame_file)
        palette = base.palette
        darkest_available_color = (255, 255, 255)
        darkest_index = -1
        for i, color in enumerate(palette.colors):
            if (statistics.mean(color) < statistics.mean(darkest_available_color)
                    and i != base.info["transparency"]):
                darkest_available_color = color
                darkest_index = i
        font = ImageFont.truetype("./fonts/LiberationSans-Bold.ttf", self.font_size)
        surface = ImageDraw.Draw(base)
        base.seek(0)
        surface.text(self.center_coords, puzzle.center,
                     fill=darkest_index, font=font, anchor="mm")
        for letter, coords in zip(
            puzzle.outside,
            make_hexagon(self.center_coords, self.text_radius, True)
        ):
            surface.text(coords, letter, fill=darkest_index, font=font, anchor="mm")
        image_bytes = BytesIO()
        base.seek(0)
        base.save(image_bytes, format="GIF")
        image_bytes.seek(0)
        gifsicle = await asyncio.create_subprocess_exec(
            "gifsicle", self.gif_file, "--replace", "#0", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        gifsicle_output = await gifsicle.communicate(input=image_bytes.read())
        if len(gifsicle_output[1]) > 0:
            print("gifsicle errors:")
            print(gifsicle_output[1].decode("ascii"))
        return gifsicle_output[0]


class BlenderRenderer(BeeRenderer):
    def __init__(self, blender_file_path: PathLike):
        self.blender_file_path = blender_file_path

    async def render(self, puzzle: SpellingBee):
        letters = puzzle.center+("".join(puzzle.outside))
        output_path = Path.cwd()/("images/temp/"+letters)
        blender = await asyncio.create_subprocess_exec(
            "blender", "-b", self.blender_file_path,
            "-E", "CYCLES",
            "-o", str(output_path)+"#",
            "--python-text", "AddLetters",
            "-F", "PNG",
            "-f", "1",
            "--", letters,
            stdout=asyncio.subprocess.PIPE
        )
        async for line in blender.stdout:
            line = line.decode("ascii").strip()
            print("\r"+line, end="\x1b[1K")
            if line.startswith("Saved:"):
                result_file_path = line[line.find("'")+1: -1]
        await blender.wait()
        print("\r", end="")

        with open(result_file_path, "rb") as result_file:
            result = result_file.read()
        return result

    def __repr__(self):
        return f"BlenderRenderer for {self.blender_file_path}"


class LetterSwapRenderer(BeeRenderer):
    def __init__(
            self,
            base_image_path: PathLike,
            image_palette: PathLike,
            letter_locations: list[tuple[int, int]],
            frames_path: PathLike,
            frames_size: tuple[int, int],
            frames_per_letter: int,
            pause_length: int):
        """Creates a renderer that creates a GIF animation in which letters switch back
        and forth and transition between each other. Created with split-flap displays
        in mind.

        Args:
            base_image_path (PathLike): Path to the image upon which the letters will
                be superimposed
            base_image_palette (PathLike): Path to the palette for the gif. This should
                have been generated with the ffmpeg palettegen filter.
            letter_locations (list[tuple[int, int]]): List of the upper left corners
                of the 7 boxes that letters should be placed in, starting with the box
                for the center letter
            frames_path (PathLike): Path to the folder that contains the frames that
                display the letters and transition between them.
            frames_size (tuple[int, int]): Size of the boxes that the letters should
                be placed in: (width, height)
            frames_per_letter (int): How many frames it takes to transition from one
                letter to the other using the frames in `frames_path`.
            pause_length (int): How long to pause, in seconds, between each letter 
                transition.
        """
        self.base_image_path = base_image_path
        self.image_palette = image_palette
        self.letter_locations = letter_locations
        self.frames_path = frames_path
        self.frames_size = frames_size
        self.frames_per_letter = frames_per_letter
        self.pause_length = pause_length

    @property
    def total_frames(self):
        return self.frames_per_letter*26

    def __repr__(self):
        return f"LetterSwapRenderer for {self.base_image_path}"

    def get_frame_for_letter(self, letter: str) -> list[int]:
        return (ord(letter)-ord("A"))*self.frames_per_letter

    def get_frames_between_letters(self, start_letter: str, end_letter: str):
        start = self.get_frame_for_letter(start_letter)
        end = self.get_frame_for_letter(end_letter)
        if end < start:
            end += self.total_frames
        return list(x % self.total_frames for x in range(start, end))

    def resize_frame(self, frame: Image.Image) -> Image.Image:
        if (frame.width, frame.height) != self.frames_size:
            return frame.resize(self.frames_size)
        else:
            return frame

    def open_frame(self, frame_path: PathLike) -> Image.Image:
        return self.resize_frame(Image.open(frame_path))

    async def render(self, puzzle: SpellingBee):
        base_image = Image.open(self.base_image_path)
        frame_paths = sorted(
            list(Path(self.frames_path).glob("*")),
            key=lambda x: int(x.stem))
        center_frame = self.get_frame_for_letter(puzzle.center)
        center_frame_image = self.open_frame(frame_paths[center_frame])
        base_image.paste(center_frame_image, self.letter_locations[0])
        frame_count = 0
        freeze_frames = [0]
        for i in range(6):
            frame_image = self.open_frame(frame_paths[self.get_frame_for_letter(puzzle.outside[i])])
            base_image.paste(frame_image, self.letter_locations[i+1])
        base_image.save("images/temp/"+str(frame_count)+".bmp")
        frame_count += 1
        swappable_index_pairs = [(0, 1), (2, 3), (4, 5), (1, 0), (3, 2), (5, 4)]
        total_frames = 1
        for indexes in swappable_index_pairs:
            total_frames += max(
                [len(
                    self.get_frames_between_letters(
                        puzzle.outside[indexes[0]], puzzle.outside[indexes[1]])),
                 len(
                    self.get_frames_between_letters(
                        puzzle.outside[indexes[1]], puzzle.outside[indexes[0]]))
                 ]
            )
        swappable_locations = self.letter_locations[1:]
        for indexes in swappable_index_pairs:
            pos_1_frames = self.get_frames_between_letters(
                puzzle.outside[indexes[0]], puzzle.outside[indexes[1]])
            pos_2_frames = self.get_frames_between_letters(
                puzzle.outside[indexes[1]], puzzle.outside[indexes[0]])
            for i in range(max(len(pos_1_frames), len(pos_2_frames))):
                if i < len(pos_1_frames):
                    base_image.paste(
                        self.open_frame(frame_paths[pos_1_frames[i]]),
                        swappable_locations[min(indexes)]
                    )
                if i < len(pos_2_frames):
                    base_image.paste(
                        self.open_frame(frame_paths[pos_2_frames[i]]),
                        swappable_locations[max(indexes)]
                    )
                base_image.save("images/temp/"+str(frame_count)+".bmp")
                frame_count += 1
                if frame_count % 10 == 0:
                    print(
                        f"\rLetterSwapRenderer emitted {frame_count}/{total_frames} frames",
                        end="")
            freeze_frames.append(frame_count-1)
        print(f"\rLetterSwapRenderer emitted all {total_frames} frames")
        ffmpeg_pauses = "+".join([f"gt(N,{x})*{self.pause_length}/TB" for x in freeze_frames])
        ffmpeg_command = (
            f"ffmpeg -framerate 45 -i images/temp/%d.bmp -i {self.image_palette} " +
            f"-filter_complex \"setpts='PTS-STARTPTS+({ffmpeg_pauses})'," +
            "paletteuse\" -loop 0 -y images/temp/letter_swap_output.gif")

        ffmpeg = await asyncio.create_subprocess_shell(ffmpeg_command)
        await ffmpeg.wait()
        for temp_frame in Path("images/temp/").glob("*.bmp"):
            temp_frame.unlink()
        with open("images/temp/letter_swap_output.gif", "rb") as result_file:
            result = result_file.read()
            return result


class AnimationCompositorRenderer(BeeRenderer):
    def __init__(
            self, frames_path: PathLike, top_layer_path: PathLike, base_framerate: int,
            ffmpeg_filter: str):
        self.frames_path = frames_path
        self.top_layer_path = top_layer_path
        self.base_framerate = base_framerate
        self.ffmpeg_filter = ffmpeg_filter

    def __repr__(self):
        return f"Animation Compositor Renderer for frames in {self.frames_path}"

    async def render(self, puzzle: SpellingBee):
        overlay = Image.open(BytesIO(await SVGTextTemplateRenderer(
            self.top_layer_path).render(puzzle)), formats=("PNG",))
        temp_path = Path(f"images/temp/ACR/{''.join([puzzle.center]+puzzle.outside)}")
        temp_path.mkdir(parents=True, exist_ok=True)
        result_path = f"{temp_path}.gif"
        frames = sorted(list(Path(self.frames_path).glob("*.png")), key=lambda x: int(x.stem))
        for i, frame_path in enumerate(frames, start=1):
            frame = Image.open(frame_path)
            if overlay.width != frame.width or overlay.height != frame.height:
                # the basic assumption is that all the frames will be the same size
                # so this will only be done once
                overlay = overlay.resize((frame.width, frame.height))
            Image.alpha_composite(frame, overlay).save(f"{temp_path}/{i}.png")
            if i % 10 == 0:
                print(f"\rComposited {i}/{len(frames)} frames", end="")
        print()
        ffmpeg_command = (
            f"ffmpeg -framerate {self.base_framerate} " +
            f"-i {temp_path}/%d.png -loop 0 -y " +
            f"-filter_complex \"{self.ffmpeg_filter}\" {result_path}")
        ffmpeg = await asyncio.create_subprocess_shell(ffmpeg_command)
        await ffmpeg.wait()
        for temp_frame in Path(temp_path).glob("*.png"):
            temp_frame.unlink()
        gifsicle = await asyncio.create_subprocess_shell(
            f"gifsicle -b -O1 --lossy {result_path}"
        )
        await gifsicle.wait()
        with open(result_path, "rb") as result_file:
            result = result_file.read()
            return result

for path in (Path(__file__).parent/Path("images/")).glob("blender_template_*.blend"):
    name = re.match("^blender_template_(\w*?).blend$", path.name).group(1)
    BeeRenderer.register_renderer(name, BlenderRenderer(str(path)))

BeeRenderer.available_renderers.append(
    GIFTemplateRenderer(
        Path("images", "spinf1.gif"), Path("images", "spin.gif"),
        (300, 300), 90
    ))

BeeRenderer.available_renderers.append(
    LetterSwapRenderer(
        "images/trainstationbase.png",
        "images/trainstationpalette.png",
        [(586, 277), (479, 277), (693, 277), (532, 138), (640, 415), (533, 415), (640, 138)],
        "images/animations/split-flap/",
        (40, 66), 5, 20
    )
)

BeeRenderer.available_renderers.append(
    AnimationCompositorRenderer(
        "images/animations/clock/", "images/clock_overlay.svg", 24,
        "[0:v]setpts=(PTS-STARTPTS)+(trunc((N+5)/6)*(0.75/TB))," +
        "split [a][b];[a] palettegen [p];[b][p] paletteuse=new=1"))

BeeRenderer.register_renderer(
    "earth",
    PerspectiveCompositeRenderer(
        SVGTextTemplateRenderer(Path(__file__).parent/"images/earth_foreground.svg"),
        Path(__file__).parent/"images/blank-earth.png",
        [1.938636, -0.486944, -1167.37341, 0.100808, 1.751237, -380.330737, -0.000132, -0.000108])
    )

BeeRenderer.register_renderer(
    "cereal",
    MultiPerspectiveRenderer(
        Path(__file__).parent/"images/blank-cereal.png",
        [[2.017291, 0.252161, -1298.126801, -0.0, 2.204611, -88.184438, -0.0, 0.00072],
        [3.042172, -0.269289, -2247.436275, 0.494028, 3.21118, -690.156731, 0.000619, 0.000917],
        [1.842264, -0.102348, -1371.303432, 0.100341, 1.846277, -457.17339, 0.0, 0.0],
        [1.286469, 0.055507, -844.839105, -0.024127, 1.27873, -344.388435, -0.000296, -0.000336],
        [2.143393, 0.454151, -1224.599485, -0.349601, 2.123544, -258.656838, 0.000221, 9.6e-05],
        [1.818048, 0.131206, -963.166921, -0.174243, 1.814884, -74.190556, 7.8e-05, -0.000394]], 
        [1.632081, -0.346411, -970.995657, 0.349815, 1.653672, -506.341644, -0.000169, 7.6e-05]
    )
)