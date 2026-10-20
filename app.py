"""
Tweet to Reel – personal Streamlit app

Create tab
  Photo      : tweet screenshot (white / black), hide quoted tweet, show tweet replied to,
               hide @username or hide name + @username, 35+ fonts
  Video      : 1080x1920 reel – only-video mode, background (white / blur / black),
               layout (tweet top / bottom), hide quoted tweet, crop 1:1, flip
  Just Video : the original video file from a tweet, no overlay
Save video tab
  Twitter/X (via FxTwitter), Instagram and TikTok (via yt-dlp). Optional cookies.txt for
  Instagram posts that ask for a login.

Fonts: system fonts are used when installed; the rest are fetched once from the Google Fonts
repository on first use and cached in the temp directory.
"""
import glob
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
UA = {"User-Agent": "Mozilla/5.0 (TweetToReel-personal)"}
CW, CH = 1080, 1920
SAFE_X, SAFE_Y = 135, 250  # Instagram reel safe zone
ASC = 0.93  # baseline offset factor so mixed fonts line up
DEFAULT_FONT = "Inter (Default)"

THEMES = {
    "light": dict(bg=(255, 255, 255), text=(15, 20, 25), sub=(83, 100, 113),
                  link=(29, 155, 240), border=(207, 217, 222)),
    "dark": dict(bg=(0, 0, 0), text=(231, 233, 234), sub=(113, 118, 123),
                 link=(29, 155, 240), border=(47, 51, 54)),
}
MAIN_S = dict(av=96, name=38, hand=34, text=46, lh=64, gap=18, gap2=24, date=32)
QUOTE_S = dict(av=64, name=28, hand=26, text=33, lh=46, gap=14, gap2=14, date=26)

# How much of the author header to draw
IDENTITY_MODES = {
    "Show all": "show",
    "Hide @username": "hide_handle",
    "Hide name & @username": "hide_all",
}

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF\u2600-\u27BF\u2300-\u23FF"
    "\u2B00-\u2BFF\uFE00-\uFE0F\u200D\u20E3]+"
)

# ----------------------------------------------------------------------------
# Font catalogue
#   regular / bold : file names to look for on the system (first hit wins)
#   gf             : (google-fonts folder, regular file, bold file or None) – downloaded on
#                    first use when the font isn't installed. bold=None means the regular file
#                    is reused (variable fonts get their weight axis set to Bold).
# ----------------------------------------------------------------------------
FONT_CACHE = os.path.join(tempfile.gettempdir(), "tweet_to_reel_fonts")
GF_RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FONT_ROOTS = ["/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts",
              "/System/Library/Fonts", "C:/Windows/Fonts", os.path.expanduser("~/.fonts"),
              FONT_CACHE]


def F(style, regular=(), bold=(), gf=None):
    return dict(style=style, regular=list(regular), bold=list(bold), gf=gf)


FONT_CATALOGUE = {
    # ---- Sans ----
    DEFAULT_FONT: F("Sans", ["Inter-Regular.otf", "Inter-Regular.ttf", "Inter_18pt-Regular.ttf"],
                    ["Inter-Bold.otf", "Inter-Bold.ttf", "Inter_18pt-Bold.ttf"],
                    ("inter", "Inter[opsz,wght].ttf", None)),
    "Roboto": F("Sans", ["Roboto-Regular.ttf"], ["Roboto-Bold.ttf"],
                ("roboto", "Roboto[wdth,wght].ttf", None)),
    "Poppins": F("Sans", gf=("poppins", "Poppins-Regular.ttf", "Poppins-Bold.ttf")),
    "Montserrat": F("Sans", gf=("montserrat", "Montserrat[wght].ttf", None)),
    "Open Sans": F("Sans", gf=("opensans", "OpenSans[wdth,wght].ttf", None)),
    "Lato": F("Sans", ["Lato-Regular.ttf"], ["Lato-Bold.ttf"],
              ("lato", "Lato-Regular.ttf", "Lato-Bold.ttf")),
    "Nunito": F("Sans", gf=("nunito", "Nunito[wght].ttf", None)),
    "Raleway": F("Sans", gf=("raleway", "Raleway[wght].ttf", None)),
    "DM Sans": F("Sans", gf=("dmsans", "DMSans[opsz,wght].ttf", None)),
    "Work Sans": F("Sans", gf=("worksans", "WorkSans[wght].ttf", None)),
    "Rubik": F("Sans", gf=("rubik", "Rubik[wght].ttf", None)),
    "Manrope": F("Sans", gf=("manrope", "Manrope[wght].ttf", None)),
    "Outfit": F("Sans", gf=("outfit", "Outfit[wght].ttf", None)),
    "Plus Jakarta Sans": F("Sans", gf=("plusjakartasans", "PlusJakartaSans[wght].ttf", None)),
    "Space Grotesk": F("Sans", gf=("spacegrotesk", "SpaceGrotesk[wght].ttf", None)),
    "Quicksand": F("Sans", gf=("quicksand", "Quicksand[wght].ttf", None)),
    "Josefin Sans": F("Sans", gf=("josefinsans", "JosefinSans[wght].ttf", None)),
    "DejaVu Sans": F("Sans", ["DejaVuSans.ttf"], ["DejaVuSans-Bold.ttf"]),
    "Liberation Sans": F("Sans", ["LiberationSans-Regular.ttf"], ["LiberationSans-Bold.ttf"]),
    "Arial": F("Sans", ["arial.ttf", "Arial.ttf", "Arial-Regular.ttf"],
               ["arialbd.ttf", "Arial Bold.ttf", "Arial-Bold.ttf"]),
    "Verdana": F("Sans", ["verdana.ttf", "Verdana.ttf"], ["verdanab.ttf", "Verdana Bold.ttf"]),
    "Tahoma": F("Sans", ["tahoma.ttf", "Tahoma.ttf"], ["tahomabd.ttf", "Tahoma Bold.ttf"]),
    "Trebuchet MS": F("Sans", ["trebuc.ttf", "Trebuchet MS.ttf"], ["trebucbd.ttf", "Trebuchet MS Bold.ttf"]),
    # ---- Serif ----
    "Playfair Display": F("Serif", gf=("playfairdisplay", "PlayfairDisplay[wght].ttf", None)),
    "Lora": F("Serif", gf=("lora", "Lora[wght].ttf", None)),
    "Merriweather": F("Serif", gf=("merriweather", "Merriweather[opsz,wdth,wght].ttf", None)),
    "Source Serif 4": F("Serif", gf=("sourceserif4", "SourceSerif4[opsz,wght].ttf", None)),
    "PT Serif": F("Serif", gf=("ptserif", "PT_Serif-Web-Regular.ttf", "PT_Serif-Web-Bold.ttf")),
    "Georgia": F("Serif", ["georgia.ttf", "Georgia.ttf"], ["georgiab.ttf", "Georgia Bold.ttf"]),
    "Times New Roman": F("Serif", ["times.ttf", "Times New Roman.ttf", "LiberationSerif-Regular.ttf"],
                         ["timesbd.ttf", "Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf"]),
    # ---- Display (bold, poster-like) ----
    "Bebas Neue": F("Display", gf=("bebasneue", "BebasNeue-Regular.ttf", None)),
    "Anton": F("Display", gf=("anton", "Anton-Regular.ttf", None)),
    "Oswald": F("Display", gf=("oswald", "Oswald[wght].ttf", None)),
    "Archivo Black": F("Display", gf=("archivoblack", "ArchivoBlack-Regular.ttf", None)),
    "Bangers": F("Display", gf=("bangers", "Bangers-Regular.ttf", None)),
    # ---- Script / handwriting ----
    "Lobster": F("Script", gf=("lobster", "Lobster-Regular.ttf", None)),
    "Pacifico": F("Script", gf=("pacifico", "Pacifico-Regular.ttf", None)),
    "Caveat": F("Script", gf=("caveat", "Caveat[wght].ttf", None)),
    "Dancing Script": F("Script", gf=("dancingscript", "DancingScript[wght].ttf", None)),
    "Comic Sans MS": F("Script", ["comic.ttf", "Comic Sans MS.ttf"], ["comicbd.ttf", "Comic Sans MS Bold.ttf"]),
    # ---- Mono ----
    "JetBrains Mono": F("Mono", gf=("jetbrainsmono", "JetBrainsMono[wght].ttf", None)),
    "Space Mono": F("Mono", gf=("spacemono", "SpaceMono-Regular.ttf", "SpaceMono-Bold.ttf")),
    "Courier New": F("Mono", ["cour.ttf", "Courier New.ttf", "LiberationMono-Regular.ttf"],
                     ["courbd.ttf", "Courier New Bold.ttf", "LiberationMono-Bold.ttf"]),
}


# ----------------------------------------------------------------------------
# Fonts (per-word script fallback: Latin / Devanagari / Arabic / CJK)
# ----------------------------------------------------------------------------
@lru_cache(maxsize=None)
def find_font(*names):
    """Find the first of `names` installed on this machine (or already cached)."""
    for root in FONT_ROOTS:
        if not os.path.isdir(root):
            continue
        for n in names:
            hit = glob.glob(os.path.join(root, "**", glob.escape(n)), recursive=True)
            if hit:
                return hit[0]
    return None


_GF_FAIL = {}  # file name -> time of last failed download (don't hammer the network)


def gf_path(folder, fname):
    """Return a cached Google Fonts file, downloading it on first use."""
    safe = re.sub(r"[\[\],]", "_", fname)
    dest = os.path.join(FONT_CACHE, safe)
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return dest
    if time.time() - _GF_FAIL.get(fname, 0) < 120:
        return None
    try:
        os.makedirs(FONT_CACHE, exist_ok=True)
        r = requests.get(GF_RAW + folder + "/" + urllib.parse.quote(fname), headers=UA, timeout=30)
        r.raise_for_status()
        if len(r.content) < 1000:
            raise ValueError("font file too small")
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, dest)
        return dest
    except Exception:
        _GF_FAIL[fname] = time.time()
        return None


def resolve_font_path(family, bold):
    cat = FONT_CATALOGUE.get(family) or FONT_CATALOGUE[DEFAULT_FONT]
    names = cat["bold" if bold else "regular"]
    path = find_font(*names) if names else None
    if not path and cat["gf"]:
        folder, reg, bd = cat["gf"]
        path = gf_path(folder, bd if (bold and bd) else reg)
    return path


def ensure_font(family):
    """Make sure both weights can be loaded. Returns a warning string or None."""
    if not all(resolve_font_path(family, b) for b in (False, True)):
        return f"Couldn't load the “{family}” font, so {DEFAULT_FONT} was used instead."
    return None


def available_fonts():
    """Fonts that are installed here or can be downloaded."""
    return [n for n, c in FONT_CATALOGUE.items() if c["gf"] or find_font(*c["regular"])]


def _apply_weight(font, bold):
    """Variable fonts: set the weight axis (and leave every other axis at its default)."""
    try:
        axes = font.get_variation_axes()
    except (OSError, AttributeError):
        return  # static font
    vals = []
    for a in axes:
        nm = a.get("name")
        nm = nm.decode("latin-1") if isinstance(nm, bytes) else str(nm or "")
        if nm.lower().startswith("weight"):
            vals.append(max(a["minimum"], min(a["maximum"], 700 if bold else 400)))
        else:
            vals.append(a["default"])
    try:
        font.set_variation_by_axes(vals)
    except Exception:
        pass


_FONT_OBJS = {}


def get_font(script, size, bold, font_family=DEFAULT_FONT):
    """Return a PIL ImageFont for the given script, size, bold flag, and font family."""
    key = (script, size, bold, font_family)
    if key in _FONT_OBJS:
        return _FONT_OBJS[key]
    w = "Bold" if bold else "Regular"
    path, requested = None, False

    # Non-Latin scripts always use Noto
    if script == "deva":
        path = find_font(f"NotoSansDevanagari-{w}.ttf")
    elif script == "arab":
        path = find_font(f"NotoSansArabic-{w}.ttf")
    elif script == "cjk":
        path = find_font(f"NotoSansCJK-{w}.ttc", f"NotoSansCJKjp-{w}.otf")
    requested = bool(path)

    # Latin (or Noto missing): the chosen family
    if not path:
        path = resolve_font_path(font_family, bold)
        requested = bool(path)

    # Ultimate fallbacks
    if not path:
        path = find_font(
            "Inter-Bold.otf" if bold else "Inter-Regular.otf",
            "Inter-Bold.ttf" if bold else "Inter-Regular.ttf",
            "Roboto-Bold.ttf" if bold else "Roboto-Regular.ttf",
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
            "arialbd.ttf" if bold else "arial.ttf",
        )

    font = None
    try:
        if path:
            font = ImageFont.truetype(path, size)
            _apply_weight(font, bold)
    except OSError:
        font = None
    if font is None:
        try:
            font = ImageFont.load_default(size)
        except TypeError:
            font = ImageFont.load_default()
        requested = False
    if requested:
        _FONT_OBJS[key] = font
    return font


def script_of(s):
    if re.search(r"[\u0900-\u097F]", s):
        return "deva"
    if re.search(r"[\u0600-\u06FF]", s):
        return "arab"
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", s):
        return "cjk"
    return "latin"


def font_for(word, size, bold=False, font_family=DEFAULT_FONT):
    return get_font(script_of(word), size, bold, font_family)


def text_w(s, size, bold=False, font_family=DEFAULT_FONT):
    return font_for(s, size, bold, font_family).getlength(s)


def clean(s):
    return EMOJI_RE.sub("", s or "").replace("\r", "")


def is_link(w):
    return w.startswith(("@", "#", "http", "www.")) or "://" in w


# ----------------------------------------------------------------------------
# Image helpers
# ----------------------------------------------------------------------------
@lru_cache(maxsize=256)
def fetch_bytes(url):
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.content


def load_img(url):
    try:
        return Image.open(io.BytesIO(fetch_bytes(url))).convert("RGBA")
    except Exception:
        return None


def prefetch(url):
    try:
        fetch_bytes(url)
    except Exception:
        pass


def rounded_mask(size, r):
    s = 3
    m = Image.new("L", (size[0] * s, size[1] * s), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] * s - 1, size[1] * s - 1), r * s, fill=255)
    return m.resize(size, Image.LANCZOS)


def add_play(img, box):
    x, y, w, h = box
    cx, cy = x + w // 2, y + h // 2
    r = min(70, min(w, h) // 5)
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0, 0, 0, 150), outline=(255, 255, 255, 255), width=4)
    t = r * 0.45
    d.polygon([(cx - t * 0.6, cy - t), (cx - t * 0.6, cy + t), (cx + t, cy)], fill=(255, 255, 255, 255))
    img.alpha_composite(ov)


def media_image(items, width, th):
    """items: list of (url, is_video). Returns RGBA image with rounded corners."""
    tiles = []
    for url, is_vid in items[:4]:
        im = load_img(url) if url else None
        if im is None:
            im = Image.new("RGBA", (400, 300), th["border"] + (255,))
        tiles.append((im, is_vid))
    n, gap = len(tiles), 6
    if n == 1:
        im = tiles[0][0]
        h = max(int(width * 0.4), min(int(width * im.height / im.width), int(width * 1.2)))
        boxes, total_h = [(0, 0, width, h)], h
    else:
        H = int(width * 0.75)
        hw = (width - gap) // 2
        rw = width - hw - gap
        hh = (H - gap) // 2
        if n == 2:
            boxes = [(0, 0, hw, H), (hw + gap, 0, rw, H)]
        elif n == 3:
            boxes = [(0, 0, hw, H), (hw + gap, 0, rw, hh), (hw + gap, hh + gap, rw, H - hh - gap)]
        else:
            boxes = [(0, 0, hw, hh), (hw + gap, 0, rw, hh),
                     (0, hh + gap, hw, H - hh - gap), (hw + gap, hh + gap, rw, H - hh - gap)]
        total_h = H
    canvas = Image.new("RGBA", (width, total_h), th["border"] + (255,))
    for (im, is_vid), (x, y, w, h) in zip(tiles, boxes):
        canvas.paste(ImageOps.fit(im, (w, h), Image.LANCZOS), (x, y))
        if is_vid:
            add_play(canvas, (x, y, w, h))
    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.paste(canvas, (0, 0), rounded_mask(canvas.size, 24))
    ImageDraw.Draw(out).rounded_rectangle((0, 0, width - 1, total_h - 1), 24, outline=th["border"], width=2)
    return out


# ----------------------------------------------------------------------------
# Text layout
# ----------------------------------------------------------------------------
def wrap(text, size, max_w, bold=False, font_family="Inter (Default)"):
    space = text_w(" ", size, bold, font_family)
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append([])
            continue
        cur, cw = [], 0
        for word in para.split():
            wl = text_w(word, size, bold, font_family)
            while wl > max_w and len(word) > 1:  # break very long words
                n = len(word)
                while n > 1 and text_w(word[:n], size, bold, font_family) > max_w:
                    n -= 1
                if cur:
                    lines.append(cur)
                    cur, cw = [], 0
                lines.append([word[:n]])
                word = word[n:]
                wl = text_w(word, size, bold, font_family)
            if not word:
                continue
            add = wl if not cur else cw + space + wl
            if add <= max_w:
                cur.append(word)
                cw = add
            else:
                lines.append(cur)
                cur, cw = [word], wl
        if cur:
            lines.append(cur)
    return lines


def draw_words(d, x, y, words, size, bold, color, th, linkify=False, font_family="Inter (Default)"):
    sp = text_w(" ", size, bold, font_family)
    for w in words:
        c = th["link"] if linkify and is_link(w) else color
        f = font_for(w, size, bold, font_family)
        d.text((x, y + int(size * ASC)), w, font=f, fill=c, anchor="ls")
        x += f.getlength(w) + sp
    return x


def fmt_date(tw):
    dt = None
    ts = tw.get("created_timestamp")
    if ts:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    elif tw.get("created_at"):
        try:
            dt = datetime.strptime(tw["created_at"], "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return ""
    if not dt:
        return ""
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'} · {dt:%b} {dt.day}, {dt.year}"


def verified_badge(d, cx, cy, r):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(29, 155, 240))
    d.line([(cx - r * 0.45, cy), (cx - r * 0.1, cy + r * 0.4), (cx + r * 0.5, cy - r * 0.35)],
           fill=(255, 255, 255), width=max(2, int(r * 0.22)))


def media_items(tw):
    items = []
    for m in (tw.get("media") or {}).get("all", []) or []:
        if m.get("type") == "photo":
            items.append((m.get("url"), False))
        else:
            items.append((m.get("thumbnail_url"), True))
    return [i for i in items if i[0]]


# ----------------------------------------------------------------------------
# Tweet block / card renderers
# ----------------------------------------------------------------------------
def render_block(tw, th, width, quote=False, media=True, show_quote=True,
                 show_date=True, identity="show", font_family=DEFAULT_FONT):
    """identity: 'show' (avatar + name + @handle) · 'hide_handle' · 'hide_all' (text only)."""
    S = QUOTE_S if quote else MAIN_S
    a = tw.get("author") or {}
    text = clean(tw.get("text", ""))
    lines = wrap(text, S["text"], width, font_family=font_family)
    q = tw.get("quote") if (show_quote and not quote) else None
    H = 3500 + len(lines) * S["lh"] + (3500 if q else 0)
    canvas = Image.new("RGBA", (width, H), th["bg"] + (255,))
    d = ImageDraw.Draw(canvas)
    av = S["av"]
    anon = identity == "hide_all"

    if anon:
        hdr, y = 0, 0
    else:
        # avatar
        img = load_img(a["avatar_url"].replace("_normal", "_400x400")) if a.get("avatar_url") else None
        if img is None:
            img = Image.new("RGBA", (av, av), th["border"] + (255,))
        img = ImageOps.fit(img, (av, av), Image.LANCZOS)
        canvas.paste(img, (0, 0), rounded_mask((av, av), av // 2))

        # name / handle / verified
        tx = av + 16
        nsz, hsz = S["name"], S["hand"]
        nh, hh = int(nsz * 1.25), int(hsz * 1.25)
        show_handle = identity == "show"
        # With the handle hidden, centre the name next to the avatar
        hdr = max(av, nh + (hh if show_handle else 0))
        ny = (hdr - nh - (hh if show_handle else 0)) // 2
        name = clean(a.get("name") or a.get("screen_name") or "").strip()
        verified = bool(a.get("verified") or (a.get("verification") or {}).get("verified"))
        max_nw = width - tx - (nsz + 10 if verified else 0)
        if text_w(name, nsz, True, font_family) > max_nw:
            while len(name) > 1 and text_w(name + "…", nsz, True, font_family) > max_nw:
                name = name[:-1]
            name += "…"
        draw_words(d, tx, ny, name.split(" "), nsz, True, th["text"], th, font_family=font_family)
        if verified:
            verified_badge(d, tx + text_w(name, nsz, True, font_family) + nsz * 0.65, ny + nh * 0.55, nsz * 0.4)
        if show_handle:
            draw_words(d, tx, ny + nh, ["@" + (a.get("screen_name") or "")], hsz, False, th["sub"], th,
                       font_family=font_family)
        y = hdr + S["gap2"]

    for line in lines:
        draw_words(d, 0, y, line, S["text"], False, th["text"], th, linkify=True, font_family=font_family)
        y += S["lh"]
    if lines:
        y += S["gap"]

    if media:
        items = media_items(tw)
        if items:
            m = media_image(items, width, th)
            canvas.paste(m, (0, y), m)
            y += m.height + S["gap"]

    if q:
        qp = 24
        qi = render_block(q, th, width - 2 * qp, quote=True, media=True, show_quote=False,
                          show_date=False, identity=identity, font_family=font_family)
        box = Image.new("RGBA", (width, qi.height + 2 * qp), th["bg"] + (255,))
        box.paste(qi, (qp, qp))
        ImageDraw.Draw(box).rounded_rectangle((0, 0, width - 1, box.height - 1), 28, outline=th["border"], width=2)
        canvas.paste(box, (0, y))
        y += box.height + S["gap"]

    if show_date and not quote:
        ds = fmt_date(tw)
        if ds:
            y += 4
            draw_words(d, 0, y, [ds], S["date"], False, th["sub"], th, font_family=font_family)
            y += S["date"] + 8

    return canvas.crop((0, 0, width, max(y, 1 if anon else av)))


def render_photo(tw, parent, theme, show_quote, safe=True, identity="show",
                 font_family=DEFAULT_FONT):
    th = THEMES[theme]
    GAP = 28
    M, MY = (SAFE_X, SAFE_Y) if safe else (56, 56)
    inner = CW - 2 * M
    mb = render_block(tw, th, inner, show_quote=show_quote, identity=identity,
                      font_family=font_family)
    pb = render_block(parent, th, inner, show_quote=False, show_date=False,
                      identity=identity, font_family=font_family) if parent else None
    total = MY + (pb.height + GAP if pb else 0) + mb.height + MY
    img = Image.new("RGBA", (CW, total), th["bg"] + (255,))
    y = MY
    if pb:
        img.paste(pb, (M, y))
        if identity != "hide_all":  # thread line hangs off the avatar, so skip it without one
            x = M + MAIN_S["av"] // 2
            dl = ImageDraw.Draw(img)
            dl.line((x, y + MAIN_S["av"] + 8, x, y + MAIN_S["av"] + MAIN_S["gap2"] - 2), fill=th["border"], width=4)
            dl.line((x, y + pb.height + 2, x, y + pb.height + GAP + 2), fill=th["border"], width=4)
        else:  # no avatar to hang the thread line on – separate the two tweets with a hairline
            ly = y + pb.height + GAP // 2
            ImageDraw.Draw(img).line((M, ly, CW - M, ly), fill=th["border"], width=2)
        y += pb.height + GAP
    img.paste(mb, (M, y))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=False)
    return buf.getvalue()


def render_card(tw, theme, blur, show_quote, safe=True, identity="show",
                font_family=DEFAULT_FONT):
    """Text-only tweet card used above/below the video in reels."""
    th = THEMES[theme]
    mo, pad_x, pad_y = (24, 44, 40) if blur else (0, 56, 44)
    if safe:
        pad_x = SAFE_X - mo  # text starts exactly at the safe-zone edge
    inner = CW - 2 * mo - 2 * pad_x
    block = render_block(tw, th, inner, media=False, show_quote=show_quote, show_date=False,
                         identity=identity, font_family=font_family)
    h = block.height + 2 * pad_y
    if blur:
        card = Image.new("RGBA", (CW, h), (0, 0, 0, 0))
        w = CW - 2 * mo
        card.paste(Image.new("RGBA", (w, h), th["bg"] + (255,)), (mo, 0), rounded_mask((w, h), 36))
    else:
        card = Image.new("RGBA", (CW, h), th["bg"] + (255,))
    card.paste(block, (mo + pad_x, pad_y))
    return card




# ----------------------------------------------------------------------------
# Tweet fetching
# ----------------------------------------------------------------------------
def parse_url(url):
    url = (url or "").strip()
    m = re.search(r"(?:twitter|x|fxtwitter|vxtwitter|fixupx)\.com/(?:(\w+)/)?status(?:es)?/(\d+)", url)
    if m:
        return m.group(1) or "i", m.group(2)
    if url.isdigit():
        return "i", url
    raise ValueError("That doesn't look like a tweet/X URL.")


@st.cache_data(ttl=600, show_spinner=False)
def fetch_tweet(user, tid):
    r = requests.get(f"https://api.fxtwitter.com/{user}/status/{tid}", headers=UA, timeout=15)
    if r.status_code == 404:
        raise ValueError("Tweet not found (deleted, private, or wrong link).")
    r.raise_for_status()
    tw = r.json().get("tweet")
    if not tw:
        raise ValueError("Could not read this tweet.")
    return tw


def collect_urls(*tweets):
    urls = []
    for tw in tweets:
        if not tw:
            continue
        a = tw.get("author") or {}
        if a.get("avatar_url"):
            urls.append(a["avatar_url"].replace("_normal", "_400x400"))
        urls += [u for u, _ in media_items(tw)]
        urls += collect_urls(tw.get("quote")) if tw.get("quote") else []
    return urls


# ----------------------------------------------------------------------------
# Video download helpers
# ----------------------------------------------------------------------------
def download_file(url, path):
    with requests.get(url, headers=UA, stream=True, timeout=30) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


# ----------------------------------------------------------------------------
# URL detection + yt-dlp (Instagram / TikTok)
# ----------------------------------------------------------------------------
def detect_platform(url):
    """Returns 'twitter', 'instagram', 'tiktok' or None."""
    url = (url or "").strip().lower()
    # The host must start at the beginning, after "//", or after a "." – otherwise
    # "netflix.com/" would be read as "x.com/".
    if re.search(r"(?:^|//|\.)(?:twitter|x|fxtwitter|vxtwitter|fixupx)\.com/", url):
        return "twitter"
    if re.search(r"(?:^|//|\.)(?:instagram\.com|instagr\.am)/", url):
        return "instagram"
    if re.search(r"(?:^|//|\.)tiktok\.com/", url):
        return "tiktok"
    return None


class DownloadError(Exception):
    """Friendly message for the user + raw tool output for the 'details' box."""

    def __init__(self, friendly, raw=""):
        super().__init__(friendly)
        self.friendly, self.raw = friendly, raw


def ytdlp_cmd():
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    exe = shutil.which("yt-dlp")
    return [exe] if exe else None


def explain_ytdlp_error(raw, platform):
    low = raw.lower()
    if any(k in low for k in ("login required", "log in", "sign in", "rate-limit", "rate limit",
                              "empty media response", "cookies", "checkpoint")):
        return ("The site asked for a login or blocked this server. Upload a cookies.txt from a "
                "browser where you're signed in (under “Login needed?”) and try again.")
    if any(k in low for k in ("private", "not available", "unavailable", "removed", "deleted", "404")):
        return "This post is private, deleted, or not available in this region."
    if "no video" in low or "there is no video" in low:
        return "This post has no video. Photo-only posts can't be downloaded here."
    if "unable to extract" in low or "unsupported url" in low:
        return (f"{(platform or 'The site').title()} changed something and the downloader needs an update. "
                "Redeploy the app so it installs the newest yt-dlp, then try again.")
    if any(k in low for k in ("failed to resolve", "name or service not known", "connection refused",
                              "network is unreachable", "temporary failure in name resolution")):
        return "Couldn't reach the site from this server. Check that it has internet access."
    if "timed out" in low or "timeout" in low:
        return "The download timed out. Try again in a minute."
    return "The download failed. Open the details below for the exact error."


def download_with_ytdlp(url, output_dir, cookies=None, platform=None):
    """Download a video with yt-dlp. `cookies` = bytes of a Netscape cookies.txt (optional)."""
    base = ytdlp_cmd()
    if not base:
        raise DownloadError("yt-dlp isn't installed. Add `yt-dlp` to requirements.txt and redeploy "
                            "(or run `pip install yt-dlp`).")
    fmt = "bv*+ba/b" if shutil.which("ffmpeg") else "b"
    cmd = base + [
        "--no-playlist", "--no-warnings", "--no-part", "--retries", "3", "--socket-timeout", "20",
        "-f", fmt, "-S", "vcodec:h264,res,acc",  # prefer H.264 so it plays everywhere
        "--merge-output-format", "mp4",
        "-o", os.path.join(output_dir, "downloaded.%(ext)s"),
    ]
    if cookies:
        cpath = os.path.join(output_dir, "cookies.txt")
        with open(cpath, "wb") as f:
            f.write(cookies)
        cmd += ["--cookies", cpath]
    cmd.append(url)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise DownloadError("The download timed out. Try again in a minute.")
    if p.returncode != 0:
        raw = (p.stderr.strip() or p.stdout.strip())[-900:]
        raise DownloadError(explain_ytdlp_error(raw, platform), raw)
    for f in sorted(os.listdir(output_dir)):
        if f.startswith("downloaded."):
            return os.path.join(output_dir, f)
    raise DownloadError("The download finished but no video file was produced.")


def twitter_videos(tw):
    return [m for m in (tw.get("media") or {}).get("all", []) if m.get("type") in ("video", "gif")]


def twitter_video_file(tw, tid, n=1):
    """Original video file of a tweet via FxTwitter (no yt-dlp, no login)."""
    vids = twitter_videos(tw)
    if not vids:
        raise ValueError("This tweet has no video.")
    if n > len(vids):
        raise ValueError(f"This tweet only has {len(vids)} video(s).")
    with tempfile.TemporaryDirectory() as td:
        vpath = os.path.join(td, "video.mp4")
        download_file(vids[n - 1]["url"], vpath)
        with open(vpath, "rb") as f:
            data = f.read()
    return dict(kind="video", data=data, mime="video/mp4", name=f"video_{tid}.mp4")


# ----------------------------------------------------------------------------
# Reel builder (FFmpeg pipeline)
# ----------------------------------------------------------------------------
def build_reel(video_path, card, bg, tweet_on_top, crop, flip, out_path, workdir, safe=True):
    w, h = probe(video_path)
    ar = 1.0 if crop else h / w
    card_h = card.height if card is not None else 0
    avail = CH - 2 * SAFE_Y if (safe and card is not None) else CH
    f = min(1.0, avail / (card_h + CW * ar))
    vw = max(2, int(CW * f) // 2 * 2)
    vh = max(2, int(round(vw * ar)) // 2 * 2)
    ch = int(round(card_h * vw / CW))
    y0 = max(0, (CH - (ch + vh)) // 2)
    vx = (CW - vw) // 2
    if tweet_on_top:
        cy, vy = y0, y0 + ch
    else:
        vy, cy = y0, y0 + vh

    steps = []
    if crop:
        steps.append("crop='min(iw,ih)':'min(iw,ih)'")
    if flip:
        steps.append("hflip")
    steps += ["fps=30", f"scale={vw}:{vh}", "setsar=1"]
    g = f"[0:v]{','.join(steps)}[v];"
    if bg == "blur":
        g += ("[v]split[v1][vb];[vb]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
              "boxblur=6:2,scale=1080:1920[bg];")
        vl = "v1"
    else:
        g += f"color=c={'white' if bg == 'white' else 'black'}:s={CW}x{CH}:r=30[bg];"
        vl = "v"
    g += f"[bg][{vl}]overlay=x={vx}:y={vy}:shortest=1:format=auto[t];"

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path]
    if card is not None:
        card_path = os.path.join(workdir, "card.png")
        (card.resize((vw, ch), Image.LANCZOS) if vw != CW else card).save(card_path)
        cmd += ["-loop", "1", "-framerate", "30", "-i", card_path]
        g += f"[1:v]format=rgba[c];[t][c]overlay=x={vx}:y={cy}:shortest=1:format=auto,format=yuv420p[out]"
    else:
        g += "[t]format=yuv420p[out]"
    cmd += ["-filter_complex", g, "-map", "[out]", "-map", "0:a?", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", "-r", "30", "-shortest", out_path]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + p.stderr[-800:])


# ----------------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------------
def generate(url, out_type, o):
    user, tid = parse_url(url)
    tw = fetch_tweet(user, tid)
    ff = o.get("font_family", DEFAULT_FONT)
    ident = o.get("identity", "show")

    if out_type == "Photo":
        warn = ensure_font(ff)
        parent = None
        if o["show_reply"]:
            rt = tw.get("replying_to")
            pid = tw.get("replying_to_status") or (rt.get("post") if isinstance(rt, dict) else None)
            if pid:
                try:
                    parent = fetch_tweet("i", str(pid))
                except Exception:
                    parent = None
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(prefetch, collect_urls(tw, parent)))
        png = render_photo(tw, parent, "dark" if o["bg"] == "Black" else "light",
                           not o["hide_quote"], o["safe"], ident, ff)
        return dict(kind="photo", data=png, mime="image/png", name=f"tweet_{tid}.png", warn=warn)

    # ---- Just Video: the original file, nothing added ----
    if out_type == "Just Video":
        return twitter_video_file(tw, tid, o.get("vid_num", 1))

    # ---- video reel ----
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found. Add `ffmpeg` to packages.txt when hosting.")
    vids = twitter_videos(tw)
    if not vids:
        raise ValueError("This tweet has no video. Use Photo output instead.")
    if o["vid_num"] > len(vids):
        raise ValueError(f"This tweet only has {len(vids)} video(s).")
    v = vids[o["vid_num"] - 1]
    bgname = o["bg"].lower()
    warn = None if o["only_video"] else ensure_font(ff)
    with tempfile.TemporaryDirectory() as td:
        vpath, out = os.path.join(td, "in.mp4"), os.path.join(td, "reel.mp4")
        card = None
        with ThreadPoolExecutor(8) as ex:
            vf = ex.submit(download_file, v["url"], vpath)
            if not o["only_video"]:
                list(ex.map(prefetch, collect_urls(tw)))
                card = render_card(tw, "dark" if bgname == "black" else "light",
                                   bgname == "blur", not o["hide_quote"], o["safe"], ident, ff)
            vf.result()
        build_reel(vpath, card, bgname, o["layout"].startswith("Video bottom"),
                   o["crop"], o["flip"], out, td, o["safe"])
        with open(out, "rb") as f:
            data = f.read()
    return dict(kind="video", data=data, mime="video/mp4", name=f"reel_{tid}.mp4", warn=warn)


MIME = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}


def generate_download(url, vid_num=1, cookies=None):
    """Save the video from a Twitter/X, Instagram or TikTok link."""
    platform = detect_platform(url)
    if platform == "twitter":
        user, tid = parse_url(url)
        return twitter_video_file(fetch_tweet(user, tid), tid, vid_num)
    with tempfile.TemporaryDirectory() as td:
        path = download_with_ytdlp(url, td, cookies, platform)
        with open(path, "rb") as f:
            data = f.read()
    ext = os.path.splitext(path)[1].lower() or ".mp4"
    return dict(kind="video", data=data, mime=MIME.get(ext, "video/mp4"),
                name=f"{platform}_{int(time.time())}{ext}")


@st.cache_data(show_spinner=False, max_entries=96)
def font_preview(family, dark):
    """Small PNG sample so people can see a font before generating."""
    th = THEMES["dark" if dark else "light"]
    W, H = 760, 150
    img = Image.new("RGB", (W, H), th["bg"])
    d = ImageDraw.Draw(img)
    d.text((28, 70), "The quick brown fox", font=get_font("latin", 46, True, family), fill=th["text"], anchor="ls")
    d.text((28, 118), "jumps over the lazy dog · 0123456789", font=get_font("latin", 28, False, family),
           fill=th["sub"], anchor="ls")
    d.rounded_rectangle((0, 0, W - 1, H - 1), 18, outline=th["border"], width=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ----------------------------------------------------------------------------
# Styling – everything uses translucent greys / inherited colours so it looks right in
# both Streamlit's light and dark themes.
# ----------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap');

:root {
    --ttr-accent: #d6336c;
    --ttr-accent-soft: rgba(214, 51, 108, 0.12);
    --ttr-line: rgba(128, 128, 128, 0.30);
    --ttr-soft: rgba(128, 128, 128, 0.07);
}

.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea,
.stApp button, .stApp h1, .stApp h2, .stApp h3, .stApp h4 {
    font-family: 'Manrope', system-ui, -apple-system, 'Segoe UI', sans-serif;
}

.block-container {
    max-width: 780px !important;
    padding-top: 2rem !important;
    padding-bottom: 2.5rem !important;
}

/* ── Hero ── */
.hero-title {
    font-size: 2.6rem;
    font-weight: 800;
    letter-spacing: -0.03em;
    line-height: 1.1;
    margin: 0 0 0.4rem 0;
    padding: 0;
}
.hero-sub {
    font-size: 1.02rem;
    line-height: 1.55;
    opacity: 0.75;
    max-width: 34rem;
    margin: 0 0 0.9rem 0;
}

/* ── Platform badges ── */
.badge {
    display: inline-block;
    padding: 3px 11px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
    margin-right: 6px;
}
.badge-twitter { background: rgba(29, 155, 240, 0.14); color: #1d9bf0; }
.badge-insta   { background: rgba(225, 48, 108, 0.14); color: #e1306c; }
.badge-tiktok  { background: rgba(15, 157, 154, 0.16); color: #14a8a5; }

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] { padding: 10px 18px; font-weight: 700; }

/* ── Buttons ── */
div.stButton > button[kind="primary"],
div.stButton > button[data-testid="stBaseButton-primary"],
div.stDownloadButton > button {
    background: var(--ttr-accent) !important;
    color: #fff !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 0.7rem 1.4rem !important;
    font-weight: 700 !important;
    font-size: 1rem !important;
    transition: filter 0.15s ease;
}
div.stButton > button[kind="primary"]:hover,
div.stButton > button[data-testid="stBaseButton-primary"]:hover,
div.stDownloadButton > button:hover { filter: brightness(0.92); }
div.stButton > button[kind="primary"]:focus-visible,
div.stDownloadButton > button:focus-visible { outline: 3px solid var(--ttr-accent-soft); outline-offset: 2px; }

/* ── Inputs ── */
div[data-baseweb="input"], div[data-baseweb="select"] > div, div[data-baseweb="base-input"] {
    border-radius: 10px !important;
}
div[data-baseweb="input"]:focus-within {
    border-color: var(--ttr-accent) !important;
    box-shadow: 0 0 0 3px var(--ttr-accent-soft) !important;
}
div[data-testid="stTextInput"] input { padding: 0.65rem 0.9rem !important; }

/* ── Option panels / expanders / alerts ── */
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; }
details[data-testid="stExpander"], div[data-testid="stExpander"] { border-radius: 12px !important; }
div[data-testid="stAlert"] { border-radius: 12px !important; }
div[data-testid="stRadio"] label[data-baseweb="radio"] { margin-right: 0.9rem; }

/* ── “How it works” steps (a real sequence, so numbered) ── */
.step {
    border: 1px solid var(--ttr-line);
    background: var(--ttr-soft);
    border-radius: 14px;
    padding: 1rem 1.1rem;
    height: 100%;
}
.step b { display: block; font-size: 1rem; margin-bottom: 4px; }
.step span { font-size: 0.88rem; line-height: 1.45; opacity: 0.75; }
.step i {
    font-style: normal; font-weight: 800; color: var(--ttr-accent); margin-right: 6px;
}

/* ── Platform support row ── */
.platform {
    border: 1px solid var(--ttr-line);
    border-radius: 12px;
    padding: 0.8rem 1rem;
    font-size: 0.9rem;
    line-height: 1.4;
}
.platform b { display: block; margin-bottom: 2px; }
.platform span { opacity: 0.72; }

.rule { height: 1px; background: var(--ttr-line); border: none; margin: 1.4rem 0; }
.footer-text { text-align: center; opacity: 0.55; font-size: 0.82rem; padding: 1.2rem 0 0.4rem 0; }

@media (max-width: 640px) {
    .hero-title { font-size: 2.1rem; }
    .block-container { padding-left: 1rem !important; padding-right: 1rem !important; }
}
</style>
"""


def stretch(fn, *args, **kwargs):
    """Full-width widget that works on old (use_container_width) and new (width='stretch') Streamlit."""
    try:
        return fn(*args, width="stretch", **kwargs)
    except (TypeError, st.errors.StreamlitAPIException):
        return fn(*args, use_container_width=True, **kwargs)


def rule():
    st.markdown('<hr class="rule">', unsafe_allow_html=True)


def font_picker(key, dark):
    """Font dropdown with a live sample underneath."""
    fonts = available_fonts()
    fam = st.selectbox(
        "Font", fonts, index=0, key=key,
        format_func=lambda n: f"{n}  ·  {FONT_CATALOGUE[n]['style']}",
        help=f"{len(fonts)} fonts. Any that aren't installed here are downloaded the first time you pick them.")
    with st.spinner(f"Loading {fam}…"):
        warn = ensure_font(fam)
        sample = None if warn else font_preview(fam, dark)
    if warn:
        st.caption(f"⚠️ {warn}")
    else:
        stretch(st.image, sample)
    return fam


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Tweet to Reel", page_icon="🎬", layout="centered")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    st.markdown(
        '<h1 class="hero-title">Tweet to Reel</h1>'
        '<p class="hero-sub">Turn a public tweet into a screenshot or a 1080×1920 reel, '
        'or save the video from Twitter/X, Instagram and TikTok.</p>'
        '<span class="badge badge-twitter">Twitter / X</span>'
        '<span class="badge badge-insta">Instagram</span>'
        '<span class="badge badge-tiktok">TikTok</span>',
        unsafe_allow_html=True,
    )
    st.markdown("")

    tab_create, tab_save, tab_guide = st.tabs(["Create", "Save video", "Guide"])

    # ================================================================
    # CREATE  (Tweet → Photo / Video / Just Video)
    # ================================================================
    with tab_create:
        url = st.text_input("Tweet link", placeholder="https://x.com/username/status/1234567890",
                            help="Any public tweet. Private or deleted tweets won't load.")

        out_type = st.radio(
            "What do you want to make?", ["Photo", "Video", "Just Video"], horizontal=True,
            captions=["Tweet screenshot · PNG", "Reel, 1080×1920 · MP4", "Original video file · MP4"])

        o = dict(bg="White", hide_quote=False, show_reply=False, only_video=False,
                 layout="Video bottom – Tweet top", crop=False, flip=False, vid_num=1,
                 safe=True, identity="show", font_family=DEFAULT_FONT)

        if out_type == "Photo":
            with st.container(border=True):
                c1, c2, c3 = st.columns(3)
                o["bg"] = c1.radio("Background", ["White", "Black"], horizontal=True)
                o["hide_quote"] = c2.radio("Quoted tweet", ["Show", "Hide"], horizontal=True, key="hq_p") == "Hide"
                o["show_reply"] = c3.radio("Replied-to tweet", ["Hide", "Show"], horizontal=True) == "Show"
                o["identity"] = IDENTITY_MODES[st.radio(
                    "Profile info", list(IDENTITY_MODES), horizontal=True, key="id_p",
                    help="Hide @username keeps the avatar and name. Hide name & @username leaves only the tweet text.")]
                o["font_family"] = font_picker("font_p", o["bg"] == "Black")

        elif out_type == "Video":
            with st.container(border=True):
                c1, c2 = st.columns(2)
                o["only_video"] = c1.radio("Only show video", ["No", "Yes"], horizontal=True,
                                           help="Skip the tweet card and show just the video.") == "Yes"
                o["bg"] = c2.radio("Background", ["White", "Blur", "Black"], horizontal=True)
                o["layout"] = st.radio("Layout", ["Video bottom – Tweet top", "Video top – Tweet bottom"],
                                       horizontal=True, disabled=o["only_video"])
                c3, c4 = st.columns(2)
                o["hide_quote"] = c3.radio("Quoted tweet", ["Show", "Hide"], horizontal=True, key="hq_v",
                                           disabled=o["only_video"]) == "Hide"
                c5, c6, c7 = st.columns(3)
                o["crop"] = c5.checkbox("Crop to 1:1")
                o["flip"] = c6.checkbox("Flip horizontally")
                o["vid_num"] = int(c7.number_input("Video #", 1, 4, 1, help="If the tweet has several videos."))
                if not o["only_video"]:
                    o["identity"] = IDENTITY_MODES[st.radio(
                        "Profile info", list(IDENTITY_MODES), horizontal=True, key="id_v",
                        help="Hide @username keeps the avatar and name. Hide name & @username leaves only the tweet text.")]
                    o["font_family"] = font_picker("font_v", o["bg"] == "Black")

        else:  # Just Video
            with st.container(border=True):
                o["vid_num"] = int(st.number_input("Video # (if the tweet has several)", 1, 4, 1, key="jv_num"))
                st.caption("Downloads the original file from the tweet. No overlay, no reel layout.")

        if out_type != "Just Video":
            o["safe"] = st.toggle("Keep text inside the Instagram safe zone", value=True,
                                  help="135 px from the sides and 250 px from the top and bottom, "
                                       "so Instagram's buttons never cover your text.")

        if stretch(st.button, "Generate", type="primary"):
            if not url.strip():
                st.warning("Paste a tweet link first.")
            else:
                t0 = time.time()
                try:
                    with st.spinner("Generating…"):
                        res = generate(url, out_type, o)
                    res["secs"] = time.time() - t0
                    st.session_state["result"] = res
                except Exception as e:
                    st.session_state.pop("result", None)
                    st.error(str(e))

        res = st.session_state.get("result")
        if res:
            with st.container(border=True):
                st.success(f"Done in {res['secs']:.1f} s")
                if res.get("warn"):
                    st.warning(res["warn"])
                _, mid, _ = st.columns([1, 2, 1])
                if res["kind"] == "photo":
                    stretch(mid.image, res["data"])
                else:
                    mid.video(res["data"])
                stretch(st.download_button, "Download", res["data"], file_name=res["name"], mime=res["mime"])
        else:
            rule()
            a, b, c = st.columns(3)
            a.markdown('<div class="step"><b><i>1</i>Paste the link</b>'
                       '<span>Copy a public tweet link and drop it in the box above.</span></div>',
                       unsafe_allow_html=True)
            b.markdown('<div class="step"><b><i>2</i>Pick the look</b>'
                       '<span>Choose Photo, Video or Just Video, then the font, background and layout.</span></div>',
                       unsafe_allow_html=True)
            c.markdown('<div class="step"><b><i>3</i>Download</b>'
                       '<span>Save the file and post it straight to Instagram.</span></div>',
                       unsafe_allow_html=True)

    # ================================================================
    # SAVE VIDEO  (Twitter/X · Instagram · TikTok)
    # ================================================================
    with tab_save:
        st.markdown("Paste a link to a public video post and save the file. "
                    "Twitter/X works through FxTwitter; Instagram and TikTok use "
                    "[yt-dlp](https://github.com/yt-dlp/yt-dlp). No API keys needed.")
        dl_url = st.text_input(
            "Video link", key="dl_url",
            placeholder="instagram.com/reel/…  ·  tiktok.com/@user/video/…  ·  x.com/user/status/…")

        platform = detect_platform(dl_url) if dl_url.strip() else None
        if platform:
            cls = {"twitter": "badge-twitter", "instagram": "badge-insta", "tiktok": "badge-tiktok"}[platform]
            label = {"twitter": "Twitter / X", "instagram": "Instagram", "tiktok": "TikTok"}[platform]
            st.markdown(f'Detected <span class="badge {cls}">{label}</span>', unsafe_allow_html=True)
        elif dl_url.strip():
            st.warning("That link isn't from Twitter/X, Instagram or TikTok.")

        dl_n = 1
        if platform == "twitter":
            dl_n = int(st.number_input("Video # (if the tweet has several)", 1, 4, 1, key="dl_vn"))

        cookies = None
        with st.expander("Login needed? (Instagram)"):
            st.markdown(
                "Instagram often blocks servers and asks for a login. If a download fails with that "
                "message, export a **cookies.txt** from a browser where you're signed in "
                "(a “Get cookies.txt” browser extension does this) and upload it here. "
                "The file is used for this download only and isn't saved.")
            up = st.file_uploader("cookies.txt", type=["txt"], key="dl_cookies")
            if up is not None:
                cookies = up.getvalue()

        if stretch(st.button, "Download video", type="primary", key="dl_btn"):
            if not dl_url.strip():
                st.warning("Paste a video link first.")
            elif not platform:
                st.error("Use a link from Twitter/X, Instagram or TikTok.")
            else:
                t0 = time.time()
                try:
                    with st.spinner(f"Downloading from {platform.title()}…"):
                        r = generate_download(dl_url, dl_n, cookies)
                    r["secs"] = time.time() - t0
                    st.session_state["dl_result"] = r
                except DownloadError as e:
                    st.session_state.pop("dl_result", None)
                    st.error(e.friendly)
                    if e.raw:
                        with st.expander("Details"):
                            st.code(e.raw)
                except Exception as e:
                    st.session_state.pop("dl_result", None)
                    st.error(f"Download failed: {e}")

        dl_res = st.session_state.get("dl_result")
        if dl_res:
            with st.container(border=True):
                st.success(f"Saved in {dl_res['secs']:.1f} s · {len(dl_res['data']) / (1024 * 1024):.1f} MB")
                _, mid, _ = st.columns([1, 2, 1])
                mid.video(dl_res["data"])
                stretch(st.download_button, "Save to device", dl_res["data"],
                        file_name=dl_res["name"], mime=dl_res["mime"], key="dl_save")
        else:
            rule()
            cols = st.columns(3)
            cols[0].markdown('<div class="platform"><b>Twitter / X</b><span>Video tweets and GIFs</span></div>',
                             unsafe_allow_html=True)
            cols[1].markdown('<div class="platform"><b>Instagram</b><span>Reels and video posts</span></div>',
                             unsafe_allow_html=True)
            cols[2].markdown('<div class="platform"><b>TikTok</b><span>Public videos</span></div>',
                             unsafe_allow_html=True)

    # ================================================================
    # GUIDE
    # ================================================================
    with tab_guide:
        st.markdown(
            "### Tweet → Photo / Video\n"
            "1. On X, open a public tweet and copy its link (**Share → Copy link**).\n"
            "2. In **Create**, paste it and choose **Photo**, **Video** (a 1080 × 1920 reel) or **Just Video**.\n"
            "3. Adjust the options, press **Generate**, then **Download**.\n\n"
            "### Options\n"
            "- **Safe zone** keeps text 135 px from the sides and 250 px from the top and bottom, "
            "so Instagram never covers it. On by default.\n"
            "- **Profile info** has three settings: show everything, hide the `@username`, or hide the "
            "name and `@username` so only the tweet text is left.\n"
            "- **Font** lists 40+ typefaces. The first time you pick one that isn't installed, it is "
            "downloaded and cached. The sample under the menu shows how it looks.\n"
            "- **Video** adds a background (white, blur, black), tweet on top or bottom, crop to 1:1 and flip.\n"
            "- **Just Video** gives you the original file, nothing added.\n\n"
            "### Save video\n"
            "Paste a Twitter/X, Instagram or TikTok link and press **Download video**. "
            "Instagram sometimes asks for a login. Upload a `cookies.txt` under **Login needed?** if it does.\n\n"
            "### Good to know\n"
            "- Only **public** posts work.\n"
            "- Emojis are removed from rendered text, because most fonts can't draw them.\n"
            "- A photo of a video tweet shows its thumbnail with a play icon.\n"
            "- Instagram blocks many cloud servers. Running the app on your own computer is the most reliable way to save from it."
        )

    st.markdown('<p class="footer-text">Tweet to Reel · personal use · '
                'FxTwitter API & yt-dlp</p>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
