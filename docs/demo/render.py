"""Render a captured terminal transcript to an animated GIF: commands are typed, output appears line by line.
A line starting with "# " starts a new scene (cleared screen) and is shown as its title.
Usage: docs/demo/demo.sh > /tmp/t.txt && uv run --no-project --with pillow python docs/demo/render.py /tmp/t.txt docs/demo.gif
(macOS: uses the SF Mono system font)."""
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

COLS, ROWS, SIZE, PAD, BAR = 96, 24, 15, 18, 30
BG, FG, PROMPT, TITLE, NOTE, CHROME = "#1e1e2e", "#cdd6f4", "#a6e3a1", "#f9e2af", "#7f849c", "#313244"
font = ImageFont.truetype("/System/Library/Fonts/SFNSMono.ttf", SIZE)
CW = font.getbbox("M")[2]
LH = int(SIZE * 1.45)
W, H = PAD * 2 + COLS * CW, BAR + PAD * 2 + ROWS * LH


def wrap(text):
    return textwrap.wrap(text, COLS, drop_whitespace=False, break_on_hyphens=False) or [""]


def frame(lines):
    """lines: [(text, kind)], kind is title, cmd, out. Only the last ROWS rows are shown."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, BAR], fill=CHROME)
    for i, c in enumerate(("#f38ba8", "#f9e2af", "#a6e3a1")):
        d.ellipse([14 + i * 20, 10, 26 + i * 20, 22], fill=c)
    rows = []
    for text, kind in lines:
        for j, part in enumerate(wrap(text)):
            rows.append((part, kind, j == 0))
    for r, (part, kind, first) in enumerate(rows[-ROWS:]):
        x, y = PAD, BAR + PAD + r * LH
        if kind == "title":
            d.text((x, y), part, font=font, fill=TITLE)
        elif kind == "cmd":
            body, note = part, ""
            if "      #" in part:
                body, note = part.split("      #", 1)
                note = "      #" + note
            if first and body.startswith("$ "):
                d.text((x, y), "$", font=font, fill=PROMPT)
                d.text((x + 2 * CW, y), body[2:], font=font, fill=FG)
            else:
                d.text((x, y), body, font=font, fill=FG)
            d.text((x + len(body) * CW, y), note, font=font, fill=NOTE)
        else:
            d.text((x, y), part, font=font, fill=FG)
    return img


def main(src, out):
    frames, durations = [], []

    def add(lines, ms):
        frames.append(frame(lines))
        durations.append(ms)

    screen = []
    for raw in open(src).read().splitlines():
        if raw.startswith("Shell cwd"):
            continue
        if raw.startswith("# "):
            if screen:
                add(screen, 3500)
            screen = [(raw, "title")]
            add(screen, 1200)
        elif raw.startswith("$ "):
            for n in range(3, len(raw) + 3, 3):
                add(screen + [(raw[:n], "cmd")], 35)
            screen = screen + [(raw, "cmd")]
            add(screen, 500)
        else:
            screen = screen + [(raw, "out")]
            add(screen, 90 if raw else 40)
    add(screen, 4500)
    pal = frames[-1].quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    frames = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    print("%d frames, %.1f s" % (len(frames), sum(durations) / 1000))


main(sys.argv[1], sys.argv[2])
