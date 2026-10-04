"""Generates the installer artwork (BMP files used by installer/StreamActivityBot.iss).

Run once on a developer PC (needs Pillow):   python scripts/make_installer_art.py
The generated .bmp files are committed to the repository, so CI does not need Pillow.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "installer"
GREEN = (46, 224, 138)
RED = (255, 107, 122)
SS = 4  # supersampling factor for crisp edges


def font(size, bold=True):
    for name in ("segoeuib.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "DejaVuSans.ttf", "arial.ttf"):
        for base in ("", "C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/"):
            try:
                return ImageFont.truetype(base + name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def app_icon(px):
    im = Image.open(ROOT / "app.ico")
    im.size = max(im.info.get("sizes", {im.size}))
    return im.convert("RGBA").resize((px, px), Image.LANCZOS)


def vgradient(w, h, top, bottom):
    base = Image.new("RGB", (w, h), top)
    px = base.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = c
    return base


def glow(img, cx, cy, radius, color, alpha):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.55))
    img.alpha_composite(layer)


def centered(draw, text, y, fnt, fill, width, spacing=0):
    widths = [draw.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = (width - total) / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += w + spacing


def side_panel(scale):
    W, H = int(164 * scale), int(314 * scale)
    w, h = W * SS, H * SS
    img = vgradient(w, h, (11, 22, 23), (6, 13, 13)).convert("RGBA")
    glow(img, w // 2, int(h * 0.27), int(w * 0.55), GREEN, 95)
    glow(img, w // 2, int(h * 1.02), int(w * 0.8), GREEN, 40)
    # faint broadcast rings (drawn on their own layer so the alpha really blends)
    cx, cy = w // 2, int(h * 0.27)
    rings = Image.new("RGBA", img.size, (0, 0, 0, 0))
    rd = ImageDraw.Draw(rings)
    for i, a in enumerate((70, 42, 24)):
        r = int(w * (0.36 + 0.17 * i))
        rd.arc((cx - r, cy - r, cx + r, cy + r), 205, 335, fill=GREEN + (a,), width=SS * 2)
        rd.arc((cx - r, cy - r, cx + r, cy + r), 25, 155, fill=GREEN + (a,), width=SS * 2)
    img.alpha_composite(rings)
    icon = app_icon(int(w * 0.50))
    img.alpha_composite(icon, (cx - icon.width // 2, cy - icon.height // 2))
    d = ImageDraw.Draw(img)
    f1, f2, f3 = font(int(18 * scale * SS)), font(int(18 * scale * SS)), font(int(7.6 * scale * SS))
    y = int(h * 0.50)
    tw1, tw2 = d.textlength("STREAM", font=f1), d.textlength("BOT", font=f2)
    x = (w - (tw1 + tw2)) / 2
    d.text((x, y), "STREAM", font=f1, fill=(255, 255, 255))
    d.text((x + tw1, y), "BOT", font=f2, fill=GREEN)
    centered(d, "KICK  +  YOUTUBE", int(h * 0.50) + int(28 * scale * SS), f3, RED, w, spacing=int(1.8 * scale * SS))
    # accent pill + feature lines
    py = int(h * 0.70)
    d.rounded_rectangle((w // 2 - int(18 * scale * SS), py, w // 2 + int(18 * scale * SS), py + int(3 * scale * SS)), radius=SS * 2, fill=GREEN)
    f4 = font(int(8 * scale * SS), bold=False)
    for i, line in enumerate(("Автоматична активност", "Точки и време", "Няколко акаунта")):
        centered(d, line, py + int((16 + i * 17) * scale * SS), f4, (150, 178, 172), w)
    return img.convert("RGB").resize((W, H), Image.LANCZOS)


def small_icon(scale):
    S = int(55 * scale)
    img = Image.new("RGB", (S * SS, S * SS), (255, 255, 255)).convert("RGBA")
    icon = app_icon(int(S * SS * 0.92))
    img.alpha_composite(icon, ((S * SS - icon.width) // 2, (S * SS - icon.height) // 2))
    return img.convert("RGB").resize((S, S), Image.LANCZOS)


def main():
    OUT.mkdir(exist_ok=True)
    for tag, scale in (("", 1.0), ("_150", 1.5), ("_200", 2.0)):
        side_panel(scale).save(OUT / f"wizard_side{tag}.bmp", format="BMP")
        small_icon(scale).save(OUT / f"wizard_small{tag}.bmp", format="BMP")
    print("written to", OUT)


if __name__ == "__main__":
    main()
