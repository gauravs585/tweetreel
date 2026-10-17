"""
TweetReel – Enhanced Tweet-to-Content Generator

Converts tweets into polished, platform-specific visual content for:
  • Instagram Reels/Stories (9:16, safe zones)
  • TikTok (9:16, centered)
  • Twitter/X (16:9 landscape)
  • YouTube Shorts (9:16)

Improvements over v1:
  - Modern dark-mode UI with custom Streamlit theme
  - Per-platform presets with proper aspect ratios & safe zones
  - Enhanced tweet card rendering: better typography, engagement metrics,
    gradient backgrounds, shadow effects, rounded avatars
  - Multi-platform download (Instagram, TikTok, Twitter, YouTube Shorts)
  - Improved font rendering with proper line spacing & kerning
  - Better media collage layout
  - Watermark option
  - Multiple background styles (gradient, solid, blur)

No API keys needed – uses the FxTwitter public API.
"""

import glob
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

# ============================================================================
# Constants & Platform Presets
# ============================================================================
UA = {"User-Agent": "Mozilla/5.0 (TweetReel/2.0)"}

# Platform-specific output dimensions & safe zones
PLATFORMS = {
    "Instagram Reels": dict(w=1080, h=1920, safe_x=135, safe_y=250, label="9:16 portrait"),
    "TikTok":          dict(w=1080, h=1920, safe_x=100, safe_y=200, label="9:16 portrait"),
    "Twitter/X":       dict(w=1280, h=720,  safe_x=60,  safe_y=40,  label="16:9 landscape"),
    "YouTube Shorts":  dict(w=1080, h=1920, safe_x=80,  safe_y=180, label="9:16 portrait"),
}

# Baseline offset factor so mixed-script fonts align
ASC = 0.93

# ---- Color Themes ----
THEMES = {
    "light": dict(
        bg=(255, 255, 255),
        card_bg=(255, 255, 255),
        text=(15, 20, 25),
        sub=(83, 100, 113),
        link=(29, 155, 240),
        border=(207, 217, 222),
        shadow=(0, 0, 0, 25),
    ),
    "dark": dict(
        bg=(0, 0, 0),
        card_bg=(22, 24, 28),
        text=(231, 233, 234),
        sub=(113, 118, 123),
        link=(29, 155, 240),
        border=(47, 51, 54),
        shadow=(0, 0, 0, 60),
    ),
    "dim": dict(
        bg=(21, 32, 43),
        card_bg=(30, 42, 56),
        text=(231, 233, 234),
        sub=(139, 152, 165),
        link=(29, 155, 240),
        border=(56, 68, 77),
        shadow=(0, 0, 0, 40),
    ),
}

# Background style colors for gradients
GRADIENT_COLORS = {
    "Sunset":   [(255, 94, 98), (255, 195, 113)],
    "Ocean":    [(43, 88, 118), (78, 152, 199)],
    "Midnight": [(15, 12, 41), (48, 43, 99)],
    "Forest":   [(17, 82, 45), (53, 132, 80)],
    "Lavender": [(102, 51, 153), (170, 119, 204)],
    "Flame":    [(255, 65, 54), (255, 154, 0)],
    "White":    [(255, 255, 255), (245, 245, 245)],
    "Black":    [(0, 0, 0), (20, 20, 20)],
}

# Typography scale
MAIN_S = dict(av=108, name=42, hand=36, text=48, lh=68, gap=20, gap2=28, date=34, metrics=30)
QUOTE_S = dict(av=68, name=30, hand=28, text=36, lh=50, gap=14, gap2=16, date=28, metrics=24)

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF\u2600-\u27BF\u2300-\u23FF"
    "\u2B00-\u2BFF\uFE00-\uFE0F\u200D\u20E3]+"
)

FONT_ROOTS = [
    "/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts",
    "/System/Library/Fonts", "C:/Windows/Fonts", os.path.expanduser("~/.fonts"),
]


# ============================================================================
# Font System (multi-script fallback)
# ============================================================================
@lru_cache(maxsize=None)
def find_font(*names):
    for root in FONT_ROOTS:
        if not os.path.isdir(root):
            continue
        for n in names:
            hit = glob.glob(os.path.join(root, "**", n), recursive=True)
            if hit:
                return hit[0]
    return None


@lru_cache(maxsize=None)
def get_font(script, size, bold):
    w = "Bold" if bold else "Regular"
    path = None
    if script == "deva":
        path = find_font(f"NotoSansDevanagari-{w}.ttf")
    elif script == "arab":
        path = find_font(f"NotoSansArabic-{w}.ttf")
    elif script == "cjk":
        path = find_font(f"NotoSansCJK-{w}.ttc", f"NotoSansCJKjp-{w}.otf")
    if not path:
        # Prefer modern, clean fonts in order of preference
        path = find_font(
            "Inter-Bold.otf" if bold else "Inter-Regular.otf",
            "Inter-Bold.ttf" if bold else "Inter-Regular.ttf",
            "Inter_18pt-Bold.ttf" if bold else "Inter_18pt-Regular.ttf",
            "Roboto-Bold.ttf" if bold else "Roboto-Regular.ttf",
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
            "arialbd.ttf" if bold else "arial.ttf",
            "Arial Bold.ttf" if bold else "Arial.ttf",
            "segoeui.ttf" if not bold else "segoeuib.ttf",
        )
    try:
        if path:
            return ImageFont.truetype(path, size)
    except OSError:
        pass
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def script_of(s):
    if re.search(r"[\u0900-\u097F]", s):
        return "deva"
    if re.search(r"[\u0600-\u06FF]", s):
        return "arab"
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", s):
        return "cjk"
    return "latin"


def font_for(word, size, bold=False):
    return get_font(script_of(word), size, bold)


def text_w(s, size, bold=False):
    return font_for(s, size, bold).getlength(s)


def clean(s):
    return EMOJI_RE.sub("", s or "").replace("\r", "")


def is_link(w):
    return w.startswith(("@", "#", "http", "www.")) or "://" in w


# ============================================================================
# Image Helpers
# ============================================================================
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
    """Create a high-quality anti-aliased rounded rectangle mask."""
    s = 4  # supersampling factor
    m = Image.new("L", (size[0] * s, size[1] * s), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (0, 0, size[0] * s - 1, size[1] * s - 1), r * s, fill=255
    )
    return m.resize(size, Image.LANCZOS)


def circle_mask(size):
    """Create a perfect circle mask for avatars."""
    s = 4
    m = Image.new("L", (size * s, size * s), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size * s - 1, size * s - 1), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def make_gradient(w, h, colors, vertical=True):
    """Create a smooth gradient background."""
    c1, c2 = colors
    img = Image.new("RGB", (w, h))
    for i in range(h if vertical else w):
        t = i / max(1, (h if vertical else w) - 1)
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        if vertical:
            ImageDraw.Draw(img).line([(0, i), (w, i)], fill=(r, g, b))
        else:
            ImageDraw.Draw(img).line([(i, 0), (i, h)], fill=(r, g, b))
    return img.convert("RGBA")


def add_shadow(img, offset=8, blur_radius=15, shadow_color=(0, 0, 0, 40)):
    """Add a soft drop shadow behind an image."""
    w, h = img.size
    shadow = Image.new("RGBA", (w + offset * 4, h + offset * 4), (0, 0, 0, 0))
    # Use the alpha channel of the source as shadow shape
    if img.mode == "RGBA":
        shadow_layer = Image.new("RGBA", img.size, shadow_color)
        shadow.paste(shadow_layer, (offset * 2 + offset, offset * 2 + offset), img.split()[3])
    else:
        shadow_layer = Image.new("RGBA", img.size, shadow_color)
        shadow.paste(shadow_layer, (offset * 2 + offset, offset * 2 + offset))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))
    shadow.paste(img, (offset * 2, offset * 2), img if img.mode == "RGBA" else None)
    return shadow


def add_play(img, box):
    """Draw a modern play button overlay."""
    x, y, w, h = box
    cx, cy = x + w // 2, y + h // 2
    r = min(70, min(w, h) // 5)
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    # Semi-transparent circle background
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0, 0, 0, 160))
    # White play triangle
    t = r * 0.45
    d.polygon(
        [(cx - t * 0.5, cy - t), (cx - t * 0.5, cy + t), (cx + t * 0.8, cy)],
        fill=(255, 255, 255, 240),
    )
    img.alpha_composite(ov)


def media_image(items, width, th):
    """Build media collage with rounded corners and proper spacing."""
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
            boxes = [
                (0, 0, hw, hh), (hw + gap, 0, rw, hh),
                (0, hh + gap, hw, H - hh - gap), (hw + gap, hh + gap, rw, H - hh - gap),
            ]
        total_h = H

    canvas = Image.new("RGBA", (width, total_h), th["border"] + (255,))
    for (im, is_vid), (x, y, w, h) in zip(tiles, boxes):
        canvas.paste(ImageOps.fit(im, (w, h), Image.LANCZOS), (x, y))
        if is_vid:
            add_play(canvas, (x, y, w, h))

    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.paste(canvas, (0, 0), rounded_mask(canvas.size, 24))
    ImageDraw.Draw(out).rounded_rectangle(
        (0, 0, width - 1, total_h - 1), 24, outline=th["border"], width=2
    )
    return out


# ============================================================================
# Text Layout & Rendering
# ============================================================================
def wrap(text, size, max_w, bold=False):
    space = text_w(" ", size, bold)
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append([])
            continue
        cur, cw = [], 0
        for word in para.split():
            wl = text_w(word, size, bold)
            while wl > max_w and len(word) > 1:
                n = len(word)
                while n > 1 and text_w(word[:n], size, bold) > max_w:
                    n -= 1
                if cur:
                    lines.append(cur)
                    cur, cw = [], 0
                lines.append([word[:n]])
                word = word[n:]
                wl = text_w(word, size, bold)
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


def draw_words(d, x, y, words, size, bold, color, th, linkify=False):
    sp = text_w(" ", size, bold)
    for w in words:
        c = th["link"] if linkify and is_link(w) else color
        f = font_for(w, size, bold)
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


def fmt_number(n):
    """Format large numbers: 1200 -> 1.2K, 1500000 -> 1.5M."""
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def verified_badge(d, cx, cy, r):
    """Draw the blue verification checkmark."""
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(29, 155, 240))
    d.line(
        [(cx - r * 0.45, cy), (cx - r * 0.1, cy + r * 0.4), (cx + r * 0.5, cy - r * 0.35)],
        fill=(255, 255, 255),
        width=max(2, int(r * 0.22)),
    )


def media_items(tw):
    items = []
    for m in (tw.get("media") or {}).get("all", []) or []:
        if m.get("type") == "photo":
            items.append((m.get("url"), False))
        else:
            items.append((m.get("thumbnail_url"), True))
    return [i for i in items if i[0]]


# ============================================================================
# Engagement Metrics Bar
# ============================================================================
def draw_metrics(d, x, y, tw, size, th):
    """Draw reply / retweet / like / view counts in a row."""
    metrics = []
    replies = tw.get("replies", 0) or 0
    retweets = tw.get("retweets", 0) or 0
    likes = tw.get("likes", 0) or 0
    views = tw.get("views", 0) or 0

    if replies:
        metrics.append(("💬 " + fmt_number(replies), th["sub"]))
    if retweets:
        metrics.append(("🔁 " + fmt_number(retweets), (0, 186, 124)))
    if likes:
        metrics.append(("❤️ " + fmt_number(likes), (249, 24, 128)))
    if views:
        metrics.append(("👁 " + fmt_number(views), th["sub"]))

    sp = size * 2.5
    for label, color in metrics:
        f = font_for(label, size, False)
        d.text((x, y + int(size * ASC)), label, font=f, fill=color, anchor="ls")
        x += f.getlength(label) + sp
    return y + int(size * 1.4)


# ============================================================================
# Tweet Block Renderer (core card)
# ============================================================================
def render_block(tw, th, width, quote=False, media=True, show_quote=True,
                 show_date=True, show_metrics=True):
    """Render a single tweet block (avatar, name, text, media, metrics)."""
    S = QUOTE_S if quote else MAIN_S
    a = tw.get("author") or {}
    text = clean(tw.get("text", ""))
    lines = wrap(text, S["text"], width)
    q = tw.get("quote") if (show_quote and not quote) else None
    H = 4000 + len(lines) * S["lh"] + (4000 if q else 0)
    canvas = Image.new("RGBA", (width, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)

    # ── Avatar (circular) ──
    av = S["av"]
    img = load_img(a["avatar_url"].replace("_normal", "_400x400")) if a.get("avatar_url") else None
    if img is None:
        img = Image.new("RGBA", (av, av), th["border"] + (255,))
    img = ImageOps.fit(img, (av, av), Image.LANCZOS)
    canvas.paste(img, (0, 0), circle_mask(av))

    # ── Name / Handle / Verified ──
    tx = av + 18
    nsz, hsz = S["name"], S["hand"]
    nh, hh = int(nsz * 1.25), int(hsz * 1.25)
    hdr = max(av, nh + hh)
    ny = (hdr - nh - hh) // 2
    name = clean(a.get("name") or a.get("screen_name") or "").strip()
    verified = bool(a.get("verified") or (a.get("verification") or {}).get("verified"))
    max_nw = width - tx - (nsz + 10 if verified else 0)
    if text_w(name, nsz, True) > max_nw:
        while len(name) > 1 and text_w(name + "…", nsz, True) > max_nw:
            name = name[:-1]
        name += "…"
    draw_words(d, tx, ny, name.split(" "), nsz, True, th["text"], th)
    if verified:
        verified_badge(d, tx + text_w(name, nsz, True) + nsz * 0.65, ny + nh * 0.55, nsz * 0.4)
    draw_words(d, tx, ny + nh, ["@" + (a.get("screen_name") or "")], hsz, False, th["sub"], th)

    y = hdr + S["gap2"]

    # ── Tweet text ──
    for line in lines:
        draw_words(d, 0, y, line, S["text"], False, th["text"], th, linkify=True)
        y += S["lh"]
    if lines:
        y += S["gap"]

    # ── Media ──
    if media:
        items = media_items(tw)
        if items:
            m = media_image(items, width, th)
            canvas.paste(m, (0, y), m)
            y += m.height + S["gap"]

    # ── Quoted tweet ──
    if q:
        qp = 24
        qi = render_block(q, th, width - 2 * qp, quote=True, media=True,
                          show_quote=False, show_date=False, show_metrics=False)
        box = Image.new("RGBA", (width, qi.height + 2 * qp), (0, 0, 0, 0))
        box.paste(qi, (qp, qp))
        ImageDraw.Draw(box).rounded_rectangle(
            (0, 0, width - 1, box.height - 1), 28, outline=th["border"], width=2
        )
        canvas.paste(box, (0, y), box)
        y += box.height + S["gap"]

    # ── Engagement metrics ──
    if show_metrics and not quote:
        y = draw_metrics(d, 0, y, tw, S["metrics"], th)
        y += 8

    # ── Date ──
    if show_date and not quote:
        ds = fmt_date(tw)
        if ds:
            y += 4
            draw_words(d, 0, y, [ds], S["date"], False, th["sub"], th)
            y += S["date"] + 8

    return canvas.crop((0, 0, width, max(y, av)))


# ============================================================================
# Card with Background (for embedding in reels/photos)
# ============================================================================
def render_tweet_card(tw, theme_name, card_style="floating", show_quote=True,
                      show_metrics=True, platform_w=1080, safe_x=135):
    """Render the tweet as a styled card with optional background effects."""
    th = THEMES[theme_name]

    if card_style == "floating":
        pad_x, pad_y = 48, 44
        mo = 24
    elif card_style == "flat":
        pad_x, pad_y = 56, 44
        mo = 0
    else:  # minimal
        pad_x, pad_y = 40, 36
        mo = 0

    inner = platform_w - 2 * max(mo, safe_x) - 2 * pad_x + 2 * mo
    inner = max(inner, 300)

    block = render_block(tw, th, inner, media=False, show_quote=show_quote,
                         show_date=True, show_metrics=show_metrics)
    h = block.height + 2 * pad_y
    card_w = platform_w - 2 * max(0, safe_x - mo)

    if card_style == "floating":
        card = Image.new("RGBA", (card_w, h), (0, 0, 0, 0))
        inner_w = card_w - 2 * mo
        # Card background with rounded corners
        card_bg = Image.new("RGBA", (inner_w, h), th["card_bg"] + (255,))
        card.paste(card_bg, (mo, 0), rounded_mask((inner_w, h), 36))
        # Subtle border
        ImageDraw.Draw(card).rounded_rectangle(
            (mo, 0, mo + inner_w - 1, h - 1), 36, outline=th["border"], width=2
        )
    else:
        card = Image.new("RGBA", (card_w, h), th["card_bg"] + (255,))

    card.paste(block, (max(mo, pad_x + (safe_x - mo if safe_x > mo else 0)), pad_y), block)
    return card


# ============================================================================
# Photo Output
# ============================================================================
def render_photo(tw, parent, theme_name, show_quote, show_metrics, bg_style,
                 platform="Instagram Reels", watermark=""):
    """Render the full photo output for a tweet."""
    plat = PLATFORMS[platform]
    CW, CH_out = plat["w"], plat["h"]
    SAFE_X, SAFE_Y = plat["safe_x"], plat["safe_y"]
    th = THEMES[theme_name]

    GAP = 28
    inner = CW - 2 * SAFE_X
    mb = render_block(tw, th, inner, show_quote=show_quote, show_metrics=show_metrics)
    pb = render_block(parent, th, inner, show_quote=False, show_date=False, show_metrics=False) if parent else None

    content_h = SAFE_Y + (pb.height + GAP if pb else 0) + mb.height + SAFE_Y
    total_h = max(content_h, CH_out) if platform != "Twitter/X" else content_h

    # Background
    if bg_style in GRADIENT_COLORS:
        img = make_gradient(CW, total_h, GRADIENT_COLORS[bg_style])
    elif bg_style == "Blur":
        img = Image.new("RGBA", (CW, total_h), th["bg"] + (255,))
    else:
        img = Image.new("RGBA", (CW, total_h), th["bg"] + (255,))

    # Center content vertically in the available space
    start_y = max(SAFE_Y, (total_h - (content_h - 2 * SAFE_Y)) // 2)
    y = start_y

    if pb:
        img.paste(pb, (SAFE_X, y), pb)
        # Thread line
        x = SAFE_X + MAIN_S["av"] // 2
        dl = ImageDraw.Draw(img)
        dl.line((x, y + MAIN_S["av"] + 8, x, y + MAIN_S["av"] + MAIN_S["gap2"] - 2),
                fill=th["border"], width=4)
        dl.line((x, y + pb.height + 2, x, y + pb.height + GAP + 2),
                fill=th["border"], width=4)
        y += pb.height + GAP

    img.paste(mb, (SAFE_X, y), mb)

    # Watermark
    if watermark:
        wm_size = 26
        wm_font = font_for(watermark, wm_size, False)
        wm_w = wm_font.getlength(watermark)
        d = ImageDraw.Draw(img)
        d.text((CW - wm_w - 30, total_h - 50), watermark, font=wm_font,
               fill=th["sub"] + (128,) if len(th["sub"]) == 3 else th["sub"])

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=False)
    return buf.getvalue()


# ============================================================================
# Video Card (overlay for reels)
# ============================================================================
def render_video_card(tw, theme_name, card_style, show_quote, show_metrics,
                      platform_w=1080, safe_x=135):
    """Render a tweet card specifically for video overlay."""
    return render_tweet_card(tw, theme_name, card_style, show_quote, show_metrics,
                             platform_w, safe_x)


# ============================================================================
# Tweet Fetching
# ============================================================================
def parse_url(url):
    url = (url or "").strip()
    m = re.search(
        r"(?:twitter|x|fxtwitter|vxtwitter|fixupx)\.com/(?:(\w+)/)?status(?:es)?/(\d+)", url
    )
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


# ============================================================================
# Video Pipeline
# ============================================================================
def download_file(url, path):
    with requests.get(url, headers=UA, stream=True, timeout=30) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


def build_reel(video_path, card, bg, tweet_on_top, crop, flip, out_path, workdir,
               platform="Instagram Reels"):
    plat = PLATFORMS[platform]
    CW_p, CH_p = plat["w"], plat["h"]
    SAFE_Y = plat["safe_y"]

    w, h = probe(video_path)
    ar = 1.0 if crop else h / w
    card_h = card.height if card is not None else 0

    avail = CH_p - 2 * SAFE_Y if card is not None else CH_p
    f = min(1.0, avail / (card_h + CW_p * ar))
    vw = max(2, int(CW_p * f) // 2 * 2)
    vh = max(2, int(round(vw * ar)) // 2 * 2)
    ch = int(round(card_h * vw / CW_p))
    y0 = max(0, (CH_p - (ch + vh)) // 2)
    vx = (CW_p - vw) // 2

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
        g += (
            "[v]split[v1][vb];[vb]scale=270:480:force_original_aspect_ratio=increase,"
            f"crop=270:480,boxblur=6:2,scale={CW_p}:{CH_p}[bg];"
        )
        vl = "v1"
    elif bg in GRADIENT_COLORS:
        # For gradient backgrounds, we generate a gradient image and use it
        grad = make_gradient(CW_p, CH_p, GRADIENT_COLORS.get(bg, [(0, 0, 0), (20, 20, 20)]))
        grad_path = os.path.join(workdir, "bg_grad.png")
        grad.convert("RGB").save(grad_path)
        # Will be handled below as extra input
        g = f"[0:v]{','.join(steps)}[v];"
        g += f"color=c=black:s={CW_p}x{CH_p}:r=30[bg];"
        vl = "v"
    else:
        g += f"color=c={'white' if bg == 'white' else 'black'}:s={CW_p}x{CH_p}:r=30[bg];"
        vl = "v"

    g += f"[bg][{vl}]overlay=x={vx}:y={vy}:shortest=1:format=auto[t];"

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path]
    if card is not None:
        card_path = os.path.join(workdir, "card.png")
        (card.resize((vw, ch), Image.LANCZOS) if vw != CW_p else card).save(card_path)
        cmd += ["-loop", "1", "-framerate", "30", "-i", card_path]
        g += (f"[1:v]format=rgba[c];[t][c]overlay=x={vx}:y={cy}:shortest=1"
              ":format=auto,format=yuv420p[out]")
    else:
        g += "[t]format=yuv420p[out]"

    cmd += [
        "-filter_complex", g, "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", "-r", "30", "-shortest", out_path,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + p.stderr[-800:])


# ============================================================================
# Main Pipeline
# ============================================================================
def generate(url, out_type, o):
    user, tid = parse_url(url)
    tw = fetch_tweet(user, tid)
    platform = o.get("platform", "Instagram Reels")
    plat = PLATFORMS[platform]

    if out_type == "Photo":
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

        theme_name = o.get("theme", "dark")
        png = render_photo(
            tw, parent, theme_name,
            not o["hide_quote"], o.get("show_metrics", True),
            o.get("bg_style", "Black"), platform,
            o.get("watermark", ""),
        )
        return dict(kind="photo", data=png, mime="image/png", name=f"tweet_{tid}_{platform.replace('/', '_')}.png")

    # ---- Video ----
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found. Add `ffmpeg` to packages.txt when hosting.")

    vids = [m for m in (tw.get("media") or {}).get("all", []) if m.get("type") in ("video", "gif")]
    if not vids:
        raise ValueError("This tweet has no video. Use Photo output instead.")
    if o["vid_num"] > len(vids):
        raise ValueError(f"This tweet only has {len(vids)} video(s).")
    v = vids[o["vid_num"] - 1]
    bgname = o["bg"].lower()

    theme_name = o.get("theme", "dark" if bgname == "black" else "light")
    card_style = o.get("card_style", "floating")

    with tempfile.TemporaryDirectory() as td:
        vpath, out = os.path.join(td, "in.mp4"), os.path.join(td, "reel.mp4")
        card = None
        with ThreadPoolExecutor(8) as ex:
            vf = ex.submit(download_file, v["url"], vpath)
            if not o["only_video"]:
                list(ex.map(prefetch, collect_urls(tw)))
                card = render_video_card(
                    tw, theme_name, card_style,
                    not o["hide_quote"], o.get("show_metrics", True),
                    plat["w"], plat["safe_x"],
                )
            vf.result()
        build_reel(
            vpath, card, bgname,
            o["layout"].startswith("Video bottom"),
            o["crop"], o["flip"], out, td, platform,
        )
        with open(out, "rb") as f:
            data = f.read()
    return dict(kind="video", data=data, mime="video/mp4",
                name=f"reel_{tid}_{platform.replace('/', '_')}.mp4")


# ============================================================================
# Streamlit UI
# ============================================================================
CUSTOM_CSS = """
<style>
/* ── Global ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

.block-container {
    max-width: 820px !important;
    padding-top: 1.5rem !important;
}

/* ── Header ── */
.app-header {
    text-align: center;
    padding: 2rem 0 1rem;
}
.app-header h1 {
    font-family: 'Inter', sans-serif;
    font-size: 2.4rem;
    font-weight: 700;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 0.3rem;
}
.app-header p {
    color: #8899a6;
    font-size: 1.05rem;
    margin-top: 0;
}

/* ── Cards ── */
.option-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 1.2rem 1.5rem;
    margin-bottom: 1rem;
}
.option-card h3 {
    font-family: 'Inter', sans-serif;
    font-size: 1rem;
    font-weight: 600;
    color: #e7e9ea;
    margin-bottom: 0.8rem;
    display: flex;
    align-items: center;
    gap: 8px;
}

/* ── Platform pills ── */
.platform-badge {
    display: inline-block;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 0.8rem;
    font-weight: 600;
    margin-left: 8px;
}

/* ── Buttons ── */
div.stButton > button {
    width: 100%;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 12px !important;
    padding: 0.7rem 2rem !important;
    font-weight: 600 !important;
    font-size: 1rem !important;
    transition: all 0.3s ease !important;
}
div.stButton > button:hover {
    opacity: 0.9;
    transform: translateY(-1px);
    box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3) !important;
}

/* ── Download button ── */
div.stDownloadButton > button {
    width: 100%;
    background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 12px !important;
    font-weight: 600 !important;
}

/* ── Input field ── */
div[data-testid="stTextInput"] input {
    border-radius: 12px !important;
    border: 2px solid rgba(102, 126, 234, 0.3) !important;
    padding: 0.8rem 1rem !important;
    font-size: 1rem !important;
    transition: border-color 0.3s ease !important;
}
div[data-testid="stTextInput"] input:focus {
    border-color: #667eea !important;
    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.15) !important;
}

/* ── Tabs ── */
div[data-testid="stHorizontalBlock"] {
    gap: 0.5rem;
}

/* ── Steps section ── */
.step-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 14px;
    padding: 1.5rem;
    text-align: center;
    height: 100%;
}
.step-number {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    background: linear-gradient(135deg, #667eea, #764ba2);
    border-radius: 50%;
    color: white;
    font-weight: 700;
    font-size: 1rem;
    margin-bottom: 0.5rem;
}

/* ── Footer ── */
.footer {
    text-align: center;
    color: #536471;
    font-size: 0.85rem;
    padding: 2rem 0 1rem;
    border-top: 1px solid rgba(255,255,255,0.05);
    margin-top: 2rem;
}
</style>
"""


def main():
    st.set_page_config(
        page_title="TweetReel – Tweet to Social Content",
        page_icon="🎬",
        layout="centered",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    # ── Header ──
    st.markdown("""
    <div class="app-header">
        <h1>🎬 TweetReel</h1>
        <p>Convert tweets into stunning visuals for Instagram, TikTok, Twitter & YouTube</p>
    </div>
    """, unsafe_allow_html=True)

    # ── Main Tabs ──
    tab_create, tab_guide = st.tabs(["✨ Create", "📖 Guide"])

    with tab_create:
        # ── URL Input ──
        url = st.text_input(
            "🔗 Tweet / X URL",
            placeholder="https://x.com/username/status/1234567890",
            help="Paste any public tweet URL from X (Twitter)",
        )

        # ── Platform & Output Type ──
        col_plat, col_type = st.columns(2)
        with col_plat:
            platform = st.selectbox(
                "📱 Platform",
                list(PLATFORMS.keys()),
                help="Choose the target platform for optimized dimensions",
            )
        with col_type:
            out_type = st.radio("📸 Output Type", ["Photo", "Video"], horizontal=True)

        plat_info = PLATFORMS[platform]
        st.caption(f"Output: **{plat_info['w']}×{plat_info['h']}** ({plat_info['label']}) — "
                   f"Safe zone: {plat_info['safe_x']}px sides, {plat_info['safe_y']}px top/bottom")

        # ── Options ──
        o = dict(
            bg="Black", hide_quote=False, show_reply=False, only_video=False,
            layout="Video bottom – Tweet top", crop=False, flip=False, vid_num=1,
            safe=True, platform=platform, theme="dark", card_style="floating",
            show_metrics=True, bg_style="Black", watermark="",
        )

        with st.expander("🎨 Appearance", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                o["theme"] = st.selectbox(
                    "Theme",
                    ["dark", "dim", "light"],
                    format_func=lambda x: {"dark": "🌑 Dark", "dim": "🌓 Dim", "light": "☀️ Light"}[x],
                )
            with c2:
                if out_type == "Photo":
                    o["bg_style"] = st.selectbox(
                        "Background",
                        list(GRADIENT_COLORS.keys()),
                        index=list(GRADIENT_COLORS.keys()).index("Black"),
                    )
                else:
                    o["bg"] = st.selectbox("Video Background", ["Black", "White", "Blur"])

            c3, c4 = st.columns(2)
            with c3:
                o["show_metrics"] = st.checkbox("Show engagement metrics", value=True)
            with c4:
                o["watermark"] = st.text_input("Watermark text (optional)", placeholder="@yourhandle")

        if out_type == "Photo":
            with st.expander("📷 Photo Options"):
                c1, c2 = st.columns(2)
                with c1:
                    o["hide_quote"] = st.toggle("Hide quoted tweet", value=False)
                with c2:
                    o["show_reply"] = st.toggle("Show parent reply", value=False)
        else:
            with st.expander("🎥 Video Options"):
                c1, c2 = st.columns(2)
                with c1:
                    o["only_video"] = st.toggle("Only show video (no tweet card)", value=False)
                with c2:
                    o["card_style"] = st.selectbox(
                        "Card style",
                        ["floating", "flat", "minimal"],
                        format_func=lambda x: {
                            "floating": "🪟 Floating (rounded + shadow)",
                            "flat": "📋 Flat (full width)",
                            "minimal": "✨ Minimal",
                        }[x],
                        disabled=o["only_video"],
                    )
                o["layout"] = st.radio(
                    "Layout",
                    ["Video bottom – Tweet top", "Video top – Tweet bottom"],
                    horizontal=True,
                    disabled=o["only_video"],
                )
                o["hide_quote"] = st.toggle("Hide quoted tweet ", value=False, key="hq_v",
                                            disabled=o["only_video"])

                c3, c4, c5 = st.columns(3)
                with c3:
                    o["crop"] = st.checkbox("Crop to 1:1")
                with c4:
                    o["flip"] = st.checkbox("Flip horizontal")
                with c5:
                    o["vid_num"] = int(st.number_input("Video #", 1, 4, 1))

        # ── Generate Button ──
        st.markdown("---")
        if st.button("🚀 Generate", type="primary", use_container_width=True):
            if not url.strip():
                st.warning("⚠️ Paste a tweet URL first!")
            else:
                t0 = time.time()
                try:
                    with st.spinner("✨ Generating your content..."):
                        res = generate(url, out_type, o)
                    res["secs"] = time.time() - t0
                    st.session_state["result"] = res
                except Exception as e:
                    st.session_state.pop("result", None)
                    st.error(f"❌ {str(e)}")

        # ── Result Display ──
        res = st.session_state.get("result")
        if res:
            st.success(f"✅ Done in {res['secs']:.1f}s")

            if res["kind"] == "photo":
                st.image(res["data"], use_container_width=True)
            else:
                _, mid, _ = st.columns([1, 2, 1])
                mid.video(res["data"])

            # Download buttons for different platforms
            st.markdown("### ⬇️ Download")
            dl_cols = st.columns(len(PLATFORMS))
            for i, (pname, pinfo) in enumerate(PLATFORMS.items()):
                with dl_cols[i]:
                    emoji = {"Instagram Reels": "📸", "TikTok": "🎵",
                             "Twitter/X": "🐦", "YouTube Shorts": "📺"}.get(pname, "📥")
                    st.download_button(
                        f"{emoji} {pname.split('/')[0]}",
                        res["data"],
                        file_name=res["name"],
                        mime=res["mime"],
                        key=f"dl_{pname}",
                        use_container_width=True,
                    )

        # ── Quick Start ──
        st.markdown("---")
        st.markdown("### How It Works")
        cols = st.columns(3)
        steps = [
            ("🔗", "Paste URL", "Copy any public tweet link from X and paste it above"),
            ("🎨", "Customize", "Pick your platform, theme, background & layout options"),
            ("📥", "Download", "Get optimized content ready for Instagram, TikTok & more"),
        ]
        for col, (icon, title, desc) in zip(cols, steps):
            with col:
                st.markdown(f"""
                <div class="step-card">
                    <div style="font-size:2rem">{icon}</div>
                    <h4>{title}</h4>
                    <p style="color:#8899a6;font-size:0.9rem">{desc}</p>
                </div>
                """, unsafe_allow_html=True)

    with tab_guide:
        st.markdown("""
        ## 📖 Usage Guide

        ### Getting Started
        1. **Find a tweet** – Open any public tweet on X (Twitter) and copy its URL
           (Share → Copy link)
        2. **Paste the URL** – Enter it in the Tweet/X URL field
        3. **Choose your platform** – Select where you'll post (Instagram, TikTok, Twitter, YouTube)
        4. **Pick output type** – Photo for static images, Video for reels
        5. **Customize** – Adjust theme, background, and layout
        6. **Generate & download** – Click Generate and download your content

        ---

        ### 📱 Platform Presets

        | Platform | Dimensions | Best For |
        |----------|-----------|----------|
        | Instagram Reels | 1080×1920 (9:16) | Stories, Reels |
        | TikTok | 1080×1920 (9:16) | TikTok videos |
        | Twitter/X | 1280×720 (16:9) | Twitter posts |
        | YouTube Shorts | 1080×1920 (9:16) | Shorts |

        ---

        ### 🎨 Themes
        - **Dark** – Pure black background, high contrast
        - **Dim** – Dark blue-grey, easier on the eyes
        - **Light** – Clean white background

        ### 🖼️ Background Styles (Photo mode)
        - **Gradient backgrounds** – Sunset, Ocean, Midnight, Forest, Lavender, Flame
        - **Solid colors** – White, Black

        ### 🎥 Video Options
        - **Card Style** – Floating (rounded with shadow), Flat, or Minimal
        - **Background** – Black, White, or Blurred video
        - **Layout** – Tweet on top or bottom
        - **Crop** – Center-crop video to 1:1 square
        - **Flip** – Mirror video horizontally

        ---

        ### 📊 Engagement Metrics
        When enabled, displays reply, retweet, like, and view counts
        on the generated card — great for credibility on reposts.

        ### 💡 Tips
        - Only **public tweets** are supported
        - Emojis are stripped from rendered text (font limitations)
        - For video tweets in Photo mode, a thumbnail with play button is shown
        - Use the **watermark** field to add your handle to generated content
        - The **safe zone** ensures text isn't cut off by platform UI elements
        """)

    # ── Footer ──
    st.markdown("""
    <div class="footer">
        TweetReel v2.0 — Enhanced tweet-to-content generator for all platforms
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
