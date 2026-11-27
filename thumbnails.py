"""
thumbnails.py - 4 thumbnail styles, har run par baari-baari se:
  mystery (bold patti) -> vintage (sunehre akshar) -> poster -> split (side panel)
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter, ImageChops, ImageOps, features

W, H = 1280, 720
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")


# ------------------------------------------------------------------ helpers
def font(size, weight=900, serif=False):
    layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    name = "RozhaOne-Regular.ttf" if serif else "NotoSansDevanagari.ttf"
    f = ImageFont.truetype(os.path.join(FONT_DIR, name), size, layout_engine=layout)
    if not serif:  # variable font: weight set karo, width normal
        f.set_variation_by_axes([weight if a["name"] in (b"Weight", "Weight") else 100
                                 for a in f.get_variation_axes()])
    return f


def fit(text, size, maxw, weight=900, serif=False):
    """Sabse bada font jisme text maxw ke andar aa jaye."""
    f = font(size, weight, serif)
    while f.getlength(text) > maxw and size > 24:
        size -= 4
        f = font(size, weight, serif)
    return f


def gradient(start, end_alpha):
    """Upar se neeche kaala hone wala mask."""
    col = Image.new("L", (1, H))
    for y in range(H):
        col.putpixel((0, y), int(end_alpha * max(0, y / (H - 1) - start) / (1 - start)))
    return col.resize((W, H))


def side_gradient(alpha, end):
    row = Image.new("L", (W, 1))
    for x in range(W):
        row.putpixel((x, 0), int(alpha * max(0, 1 - x / end) ** 0.8))
    return row.resize((W, H))


def shade(img, mask, color=(0, 0, 0)):
    return Image.composite(Image.new("RGB", img.size, color), img, mask)


def put(img, xy, text, f, fill, stroke=0, stroke_fill=(0, 0, 0), anchor="ms", shadow=True):
    if shadow:
        m = Image.new("L", img.size)
        ImageDraw.Draw(m).text((xy[0] + 6, xy[1] + 10), text, font=f, fill=230,
                               anchor=anchor, stroke_width=stroke)
        img.paste((0, 0, 0), (0, 0), m.filter(ImageFilter.GaussianBlur(9)))
    ImageDraw.Draw(img).text(xy, text, font=f, fill=fill, anchor=anchor,
                             stroke_width=stroke, stroke_fill=stroke_fill)


# ------------------------------------------------------------------ styles
def style_mystery(bg, t):
    img = ImageEnhance.Color(bg).enhance(1.35)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = shade(img, side_gradient(170, 900))
    boxes = [(t["line1"], 50, 90, 92, (255, 255, 255), (15, 15, 15)),
             (t["line2"], 55, 285, 124, (255, 214, 0), (15, 15, 15)),
             (t["line3"], 65, 510, 76, (215, 20, 28), (255, 255, 255))]
    for text, x, y, size, back, front in boxes:
        f = fit(text, size, 900)
        l, top, r, bottom = ImageDraw.Draw(img).textbbox((0, 0), text, font=f, anchor="ls")
        top, bottom = min(top, -int(f.size * .95)), max(bottom, int(f.size * .25))
        patch = Image.new("RGBA", (r - l + 60, bottom - top + 30), back + (255,))
        ImageDraw.Draw(patch).text((30 - l, 15 - top), text, font=f, fill=front, anchor="ls")
        patch = patch.rotate(2.5, expand=True, resample=Image.BICUBIC)
        sh = Image.new("L", img.size)
        sh.paste(patch.split()[3], (x + 10, y + 14))
        img.paste((0, 0, 0), (0, 0), sh.filter(ImageFilter.GaussianBlur(10)))
        img.paste(patch, (x, y), patch)
    ImageDraw.Draw(img).rectangle((0, 0, W - 1, H - 1), outline=(215, 20, 28), width=16)
    return img


def style_vintage(bg, t):
    img = ImageEnhance.Color(bg).enhance(.25)
    img = ImageChops.multiply(img, Image.new("RGB", (W, H), (255, 222, 170)))
    vig = Image.new("L", (W, H), 200)
    ImageDraw.Draw(vig).ellipse((-150, -200, W + 150, H + 120), fill=0)
    img = shade(img, vig.filter(ImageFilter.GaussianBlur(120)))
    img = shade(img, gradient(.35, 245), (8, 5, 3))
    noise = Image.effect_noise((W, H), 22).convert("RGB")
    img = Image.blend(img, ImageChops.overlay(img, noise), .35)
    gold = (226, 182, 92)
    d = ImageDraw.Draw(img)
    d.rectangle((26, 26, W - 27, H - 27), outline=gold, width=3)
    d.rectangle((38, 38, W - 39, H - 39), outline=gold, width=1)
    f = fit(t["kicker"], 40, 640, 700)
    put(img, (640, 112), t["kicker"], f, (240, 220, 180))
    half = f.getlength(t["kicker"]) / 2 + 40
    d = ImageDraw.Draw(img)
    for side in (-1, 1):
        d.line((640 + side * half, 98, 640 + side * (half + 160), 98), fill=gold, width=2)
    for text, y, size in ((t["line1"], 478, 120), (t["line2"], 640, 150)):
        f = fit(text, size, 1130, serif=True)
        put(img, (640, y), text, f, (0, 0, 0), 5)
        m = Image.new("L", (W, H))
        ImageDraw.Draw(m).text((640, y), text, font=f, fill=255, anchor="ms")
        box = m.getbbox()
        if not box:
            continue
        top, bottom = box[1], box[3]
        colors = Image.new("RGB", (1, bottom - top))
        for i in range(bottom - top):
            k = i / max(1, bottom - top - 1)
            c1, c2, k = (((255, 244, 190), (242, 190, 70), k / .55) if k < .55
                         else ((242, 190, 70), (176, 112, 30), (k - .55) / .45))
            colors.putpixel((0, i), tuple(int(a + (b - a) * k) for a, b in zip(c1, c2)))
        layer = Image.new("RGB", (W, H))
        layer.paste(colors.resize((W, bottom - top)), (0, top))
        img.paste(layer, (0, 0), m)
    return img


def style_poster(bg, t):
    img = shade(ImageEnhance.Contrast(bg).enhance(1.15), gradient(.35, 248))
    put(img, (640, 372), t["kicker"], fit(t["kicker"], 36, 1000, 700), (235, 235, 235))
    title = t["line2"]
    put(img, (640, 535), title, fit(title, 150, 1160), "white")
    ImageDraw.Draw(img).rectangle((520, 577, 760, 585), fill=(229, 9, 20))
    tags = "  •  ".join(t["keywords"])
    put(img, (640, 652), tags, fit(tags, 38, 1100, 700), (255, 196, 40))
    return img


def style_split(bg, t):
    img = Image.new("RGB", (W, H))
    img.paste(ImageOps.fit(bg, (W - 560, H), Image.LANCZOS), (560, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(0, 0), (700, 0), (560, H), (0, H)], fill=(12, 14, 24))
    d.polygon([(700, 0), (724, 0), (584, H), (560, H)], fill=(255, 204, 0))
    for text, y, size, maxw, color in ((t["line1"], 190, 125, 570, (255, 204, 0)),
                                       (t["line2"], 338, 115, 545, "white"),
                                       (t["line3"], 484, 135, 520, "white")):
        put(img, (55, y), text, fit(text, size, maxw), color, anchor="ls", shadow=False)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((55, 555, 430, 635), radius=40, fill=(220, 30, 36))
    d.text((242, 595), "पूरी डॉक्यूमेंट्री", font=font(40, 800), fill="white", anchor="mm")
    return img


STYLES = (("mystery", style_mystery), ("vintage", style_vintage),
          ("poster", style_poster), ("split", style_split))


# ------------------------------------------------------------------ text + entry
def thumb_texts(data):
    """Gemini ka 'thumb' object saaf karo; kuch missing ho to title se bharo."""
    raw = data.get("thumb") if isinstance(data.get("thumb"), dict) else {}
    words = str(data.get("title") or "अनसुनी कहानी").split()
    clean = lambda v: " ".join(v.split()) if isinstance(v, str) else ""
    defaults = {"line1": " ".join(words[:2]), "line2": " ".join(words[2:5]) or "अनसुनी कहानी",
                "line3": "बड़ा रहस्य", "kicker": "एक सच्ची कहानी"}
    t = {k: clean(raw.get(k)) or v for k, v in defaults.items()}
    kw = raw.get("keywords") if isinstance(raw.get("keywords"), list) else []
    t["keywords"] = [clean(k) for k in kw if clean(k)][:3] or ["इतिहास", "रहस्य", "तथ्य"]
    return t


def make_thumbnail(data, bg_path, out_path, number=0):
    bg = ImageOps.fit(Image.open(bg_path).convert("RGB"), (W, H), Image.LANCZOS)  # bina khinche crop
    name, draw = STYLES[number % len(STYLES)]
    draw(bg, thumb_texts(data)).save(out_path, "JPEG", quality=92)
    return name
