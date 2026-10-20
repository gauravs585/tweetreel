"""
Tweet to Reel – personal Streamlit clone of tweet-to-reel.com

Features
  Photo : background (white/black), hide quoted tweet, show tweet replied to,
          hide username, selectable fonts
  Video : only show video, background (white/blur/black), layout (tweet top/bottom),
          hide quoted tweet, crop video to 1:1, flip horizontally
  Download : download raw video from Twitter, Instagram, TikTok
Output  : 1080x1920 reel (video) or PNG (photo). No API keys needed (uses the FxTwitter API).
"""
import glob
import io
import json
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
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
UA = {"User-Agent": "Mozilla/5.0 (TweetToReel-personal)"}
CW, CH = 1080, 1920
SAFE_X, SAFE_Y = 135, 250  # Instagram reel safe zone
ASC = 0.93  # baseline offset factor so mixed fonts line up

THEMES = {
    "light": dict(bg=(255, 255, 255), text=(15, 20, 25), sub=(83, 100, 113),
                  link=(29, 155, 240), border=(207, 217, 222)),
    "dark": dict(bg=(0, 0, 0), text=(231, 233, 234), sub=(113, 118, 123),
                 link=(29, 155, 240), border=(47, 51, 54)),
}
MAIN_S = dict(av=96, name=38, hand=34, text=46, lh=64, gap=18, gap2=24, date=32)
QUOTE_S = dict(av=64, name=28, hand=26, text=33, lh=46, gap=14, gap2=14, date=26)

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U0001F1E6-\U0001F1FF\u2600-\u27BF\u2300-\u23FF"
    "\u2B00-\u2BFF\uFE00-\uFE0F\u200D\u20E3]+"
)
FONT_ROOTS = ["/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts",
              "/System/Library/Fonts", "C:/Windows/Fonts", os.path.expanduser("~/.fonts")]

# ----------------------------------------------------------------------------
# Font catalogue
# ----------------------------------------------------------------------------
FONT_CATALOGUE = {
    "Inter (Default)": {
        "regular": ["Inter-Regular.otf", "Inter-Regular.ttf", "Inter_18pt-Regular.ttf"],
        "bold":    ["Inter-Bold.otf", "Inter-Bold.ttf", "Inter_18pt-Bold.ttf"],
    },
    "Roboto": {
        "regular": ["Roboto-Regular.ttf"],
        "bold":    ["Roboto-Bold.ttf"],
    },
    "Arial": {
        "regular": ["arial.ttf", "Arial.ttf", "Arial-Regular.ttf"],
        "bold":    ["arialbd.ttf", "Arial Bold.ttf", "Arial-Bold.ttf"],
    },
    "Georgia": {
        "regular": ["georgia.ttf", "Georgia.ttf", "Georgia-Regular.ttf"],
        "bold":    ["georgiab.ttf", "Georgia Bold.ttf", "Georgia-Bold.ttf"],
    },
    "Times New Roman": {
        "regular": ["times.ttf", "Times New Roman.ttf", "TimesNewRoman.ttf", "LiberationSerif-Regular.ttf"],
        "bold":    ["timesbd.ttf", "Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf"],
    },
    "Verdana": {
        "regular": ["verdana.ttf", "Verdana.ttf", "Verdana-Regular.ttf"],
        "bold":    ["verdanab.ttf", "Verdana Bold.ttf", "Verdana-Bold.ttf"],
    },
    "Trebuchet MS": {
        "regular": ["trebuc.ttf", "Trebuchet MS.ttf", "TrebuchetMS.ttf"],
        "bold":    ["trebucbd.ttf", "Trebuchet MS Bold.ttf", "TrebuchetMS-Bold.ttf"],
    },
    "Tahoma": {
        "regular": ["tahoma.ttf", "Tahoma.ttf"],
        "bold":    ["tahomabd.ttf", "Tahoma Bold.ttf"],
    },
    "Courier New": {
        "regular": ["cour.ttf", "Courier New.ttf", "CourierNew.ttf", "LiberationMono-Regular.ttf"],
        "bold":    ["courbd.ttf", "Courier New Bold.ttf", "LiberationMono-Bold.ttf"],
    },
    "Comic Sans MS": {
        "regular": ["comic.ttf", "Comic Sans MS.ttf", "ComicSansMS.ttf"],
        "bold":    ["comicbd.ttf", "Comic Sans MS Bold.ttf", "ComicSansMS-Bold.ttf"],
    },
    "DejaVu Sans": {
        "regular": ["DejaVuSans.ttf"],
        "bold":    ["DejaVuSans-Bold.ttf"],
    },
    "Liberation Sans": {
        "regular": ["LiberationSans-Regular.ttf"],
        "bold":    ["LiberationSans-Bold.ttf"],
    },
    "Impact": {
        "regular": ["impact.ttf", "Impact.ttf"],
        "bold":    ["impact.ttf", "Impact.ttf"],
    },
    "Lucida Console": {
        "regular": ["lucon.ttf", "Lucida Console.ttf"],
        "bold":    ["lucon.ttf", "Lucida Console.ttf"],
    }
}


# ----------------------------------------------------------------------------
# Fonts (per-word script fallback: Latin / Devanagari / Arabic / CJK)
# ----------------------------------------------------------------------------
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
def get_font(script, size, bold, font_family="Inter (Default)"):
    w = "Bold" if bold else "Regular"
    path = None

    if script == "deva":
        path = find_font(f"NotoSansDevanagari-{w}.ttf")
    elif script == "arab":
        path = find_font(f"NotoSansArabic-{w}.ttf")
    elif script == "cjk":
        path = find_font(f"NotoSansCJK-{w}.ttc", f"NotoSansCJKjp-{w}.otf")

    if not path:
        cat = FONT_CATALOGUE.get(font_family, FONT_CATALOGUE["Inter (Default)"])
        key = "bold" if bold else "regular"
        path = find_font(*cat[key])

    if not path:
        path = find_font(
            "Inter-Bold.otf" if bold else "Inter-Regular.otf",
            "Inter-Bold.ttf" if bold else "Inter-Regular.ttf",
            "Roboto-Bold.ttf" if bold else "Roboto-Regular.ttf",
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
            "arialbd.ttf" if bold else "arial.ttf",
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


def font_for(word, size, bold=False, font_family="Inter (Default)"):
    return get_font(script_of(word), size, bold, font_family)


def text_w(s, size, bold=False, font_family="Inter (Default)"):
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
            while wl > max_w and len(word) > 1:
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
                 show_date=True, hide_username=False, font_family="Inter (Default)"):
    S = QUOTE_S if quote else MAIN_S
    a = tw.get("author") or {}
    text = clean(tw.get("text", ""))
    lines = wrap(text, S["text"], width, font_family=font_family)
    q = tw.get("quote") if (show_quote and not quote) else None
    H = 3500 + len(lines) * S["lh"] + (3500 if q else 0)
    canvas = Image.new("RGBA", (width, H), th["bg"] + (255,))
    d = ImageDraw.Draw(canvas)

    av = S["av"]
    img = load_img(a["avatar_url"].replace("_normal", "_400x400")) if a.get("avatar_url") else None
    if img is None:
        img = Image.new("RGBA", (av, av), th["border"] + (255,))
    img = ImageOps.fit(img, (av, av), Image.LANCZOS)
    canvas.paste(img, (0, 0), rounded_mask((av, av), av // 2))

    tx = av + 16
    nsz, hsz = S["name"], S["hand"]
    nh, hh = int(nsz * 1.25), int(hsz * 1.25)
    hdr = max(av, nh + hh)
    ny = (hdr - nh - hh) // 2
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

    # Conditionally hide username[cite: 3]
    if not hide_username:
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
                          show_date=False, hide_username=hide_username, font_family=font_family)
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

    return canvas.crop((0, 0, width, max(y, av)))


def render_photo(tw, parent, theme, show_quote, safe=True, hide_username=False,
                 font_family="Inter (Default)"):
    th = THEMES[theme]
    GAP = 28
    M, MY = (SAFE_X, SAFE_Y) if safe else (56, 56)
    inner = CW - 2 * M
    mb = render_block(tw, th, inner, show_quote=show_quote, hide_username=hide_username,
                      font_family=font_family)
    pb = render_block(parent, th, inner, show_quote=False, show_date=False,
                      hide_username=hide_username, font_family=font_family) if parent else None
    total = MY + (pb.height + GAP if pb else 0) + mb.height + MY
    img = Image.new("RGBA", (CW, total), th["bg"] + (255,))
    y = MY
    if pb:
        img.paste(pb, (M, y))
        x = M + MAIN_S["av"] // 2
        dl = ImageDraw.Draw(img)
        dl.line((x, y + MAIN_S["av"] + 8, x, y + MAIN_S["av"] + MAIN_S["gap2"] - 2), fill=th["border"], width=4)
        dl.line((x, y + pb.height + 2, x, y + pb.height + GAP + 2), fill=th["border"], width=4)
        y += pb.height + GAP
    img.paste(mb, (M, y))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=False)
    return buf.getvalue()


def render_card(tw, theme, blur, show_quote, safe=True, hide_username=False,
                font_family="Inter (Default)"):
    th = THEMES[theme]
    mo, pad_x, pad_y = (24, 44, 40) if blur else (0, 56, 44)
    if safe:
        pad_x = SAFE_X - mo
    inner = CW - 2 * mo - 2 * pad_x
    block = render_block(tw, th, inner, media=False, show_quote=show_quote, show_date=False,
                         hide_username=hide_username, font_family=font_family)
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
# URL detection helpers for multi-platform support
# ----------------------------------------------------------------------------
def detect_platform(url):
    url = (url or "").strip().lower()
    if re.search(r"(twitter|x|fxtwitter|vxtwitter|fixupx)\.com/", url):
        return "twitter"
    if re.search(r"(instagram\.com|instagr\.am)/", url):
        return "instagram"
    if re.search(r"(tiktok\.com|vm\.tiktok\.com)/", url):
        return "tiktok"
    return None


def download_with_ytdlp(url, output_dir):
    if not shutil.which("yt-dlp"):
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: `pip install yt-dlp`\n"
            "Then restart the app."
        )
    out_template = os.path.join(output_dir, "downloaded.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "-f", "best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_template,
        url,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        err = p.stderr.strip() or p.stdout.strip()
        raise RuntimeError(f"yt-dlp failed:\n{err[-600:]}")

    for f in os.listdir(output_dir):
        if f.startswith("downloaded."):
            return os.path.join(output_dir, f)
    raise RuntimeError("Download finished but no output file was found.")


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
    ff = o.get("font_family", "Inter (Default)")

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
        png = render_photo(tw, parent, "dark" if o["bg"] == "Black" else "light",
                           not o["hide_quote"], o["safe"], o["hide_username"], ff)
        return dict(kind="photo", data=png, mime="image/png", name=f"tweet_{tid}.png")

    if out_type == "Just Video":
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found.")
        vids = [m for m in (tw.get("media") or {}).get("all", []) if m.get("type") in ("video", "gif")]
        if not vids:
            raise ValueError("This tweet has no video.")
        v = vids[min(o.get("vid_num", 1), len(vids)) - 1]
        with tempfile.TemporaryDirectory() as td:
            vpath = os.path.join(td, "video.mp4")
            download_file(v["url"], vpath)
            with open(vpath, "rb") as f:
                data = f.read()
        return dict(kind="video", data=data, mime="video/mp4", name=f"video_{tid}.mp4")

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found. Add `ffmpeg` to packages.txt when hosting.")
    vids = [m for m in (tw.get("media") or {}).get("all", []) if m.get("type") in ("video", "gif")]
    if not vids:
        raise ValueError("This tweet has no video. Use Photo output instead.")
    if o["vid_num"] > len(vids):
        raise ValueError(f"This tweet only has {len(vids)} video(s).")
    v = vids[o["vid_num"] - 1]
    bgname = o["bg"].lower()
    with tempfile.TemporaryDirectory() as td:
        vpath, out = os.path.join(td, "in.mp4"), os.path.join(td, "reel.mp4")
        card = None
        with ThreadPoolExecutor(8) as ex:
            vf = ex.submit(download_file, v["url"], vpath)
            if not o["only_video"]:
                list(ex.map(prefetch, collect_urls(tw)))
                card = render_card(tw, "dark" if bgname == "black" else "light",
                                   bgname == "blur", not o["hide_quote"], o["safe"],
                                   o["hide_username"], ff)
            vf.result()
        build_reel(vpath, card, bgname, o["layout"].startswith("Video bottom"),
                   o["crop"], o["flip"], out, td, o["safe"])
        with open(out, "rb") as f:
            data = f.read()
    return dict(kind="video", data=data, mime="video/mp4", name=f"reel_{tid}.mp4")


def generate_download(url):
    with tempfile.TemporaryDirectory() as td:
        path = download_with_ytdlp(url, td)
        with open(path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(path)[1] or ".mp4"
        platform = detect_platform(url) or "video"
        ts = int(time.time())
        return dict(kind="video", data=data, mime="video/mp4",
                    name=f"{platform}_{ts}{ext}")


# ----------------------------------------------------------------------------
# Custom CSS for polished UI
# ----------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

.block-container {
    max-width: 820px !important;
    padding-top: 1.5rem !important;
    padding-bottom: 2rem !important;
}

h1 {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-weight: 800 !important;
    letter-spacing: -0.5px;
}

.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background: transparent;
}
.stTabs [data-baseweb="tab"] {
    padding: 10px 24px;
    border-radius: 10px 10px 0 0;
    font-weight: 600;
    letter-spacing: 0.3px;
}

div.stButton > button[kind="primary"],
div.stButton > button[data-testid="stBaseButton-primary"] {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 12px !important;
    padding: 0.65rem 1.5rem !important;
    font-weight: 600 !important;
    font-size: 1.05rem !important;
    letter-spacing: 0.3px;
    transition: all 0.2s ease;
    width: 100% !important;
}
div.stButton > button[kind="primary"]:hover,
div.stButton > button[data-testid="stBaseButton-primary"]:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
}

div.stDownloadButton > button {
    background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 12px !important;
    padding: 0.65rem 1.5rem !important;
    font-weight: 600 !important;
    font-size: 1.05rem !important;
    width: 100% !important;
    transition: all 0.2s ease;
}
div.stDownloadButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(56, 239, 125, 0.35);
}

div[data-testid="stTextInput"] input {
    border-radius: 12px !important;
    padding: 0.7rem 1rem !important;
    border: 2px solid #e0e0e0 !important;
    transition: border-color 0.2s ease;
}
div[data-testid="stTextInput"] input:focus {
    border-color: #667eea !important;
    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.15) !important;
}

details[data-testid="stExpander"] {
    border-radius: 12px !important;
    border: 1px solid #e8e8e8 !important;
}

div[data-testid="stAlert"] {
    border-radius: 12px !important;
}

.quick-card {
    background: linear-gradient(145deg, #f8f9ff 0%, #f0f2ff 100%);
    border-radius: 16px;
    padding: 1.2rem 1rem;
    text-align: center;
    border: 1px solid #e8ecff;
    height: 100%;
}
.quick-card h4 {
    margin: 0 0 6px 0;
    color: #667eea;
    font-size: 1.05rem;
}
.quick-card p {
    margin: 0;
    font-size: 0.88rem;
    color: #555;
    line-height: 1.4;
}

.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    margin-right: 4px;
}
.badge-twitter  { background: #e8f5fd; color: #1d9bf0; }
.badge-insta    { background: #fce4ec; color: #e1306c; }
.badge-tiktok   { background: #e8f5e9; color: #25f4ee; border: 1px solid #25f4ee; }

.styled-divider {
    height: 2px;
    background: linear-gradient(90deg, transparent, #667eea, transparent);
    border: none;
    margin: 1.5rem 0;
}

.footer-text {
    text-align: center;
    color: #999;
    font-size: 0.82rem;
    padding: 1rem 0 0.5rem 0;
}
</style>
"""


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Tweet to Reel", page_icon="🎬", layout="centered")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    st.title("🎬 Tweet to Reel")
    st.markdown(
        '<span class="badge badge-twitter">Twitter / X</span>'
        '<span class="badge badge-insta">Instagram</span>'
        '<span class="badge badge-tiktok">TikTok</span>',
        unsafe_allow_html=True,
    )

    tab_home, tab_download, tab_guide = st.tabs(["🏠  Home", "⬇️  Download Video", "📖  Guide"])

    with tab_home:
        with st.expander("ℹ️  Option Guide", expanded=False):
            st.markdown(
                "**Safe zone** – keeps all tweet text 135 px from the sides and 250 px from the "
                "top / bottom so Instagram never cuts it off (on by default)\n\n"
                "**Photo**\n"
                "- *Background color* – white (light) or black (dark) tweet image\n"
                "- *Hide quoted tweet* – leave out the embedded quote tweet\n"
                "- *Show tweet replied to* – adds the parent tweet above with a thread line\n"
                "- *Hide username* – removes the `@handle` from the rendered card\n"
                "- *Font* – choose the font for the rendered tweet text\n\n"
                "**Video** (1080 × 1920 reel)\n"
                "- *Only show video* – no tweet card, just the video on the background\n"
                "- *Background* – white, blurred copy of the video, or black\n"
                "- *Layout* – tweet on top or at the bottom of the video\n"
                "- *Crop to 1:1* – center-crops the video to a square before stacking\n"
                "- *Flip* – mirrors the video horizontally\n\n"
                "**Just Video** – downloads the raw video file from the tweet, no reel overlay")

        url = st.text_input("🔗  Tweet / X URL", placeholder="https://x.com/username/status/1234567890",
                            help="Paste any public tweet URL here")

        out_type = st.radio("Output type", ["Photo", "Video", "Just Video"], horizontal=True,
                            help="Photo = screenshot · Video = 1080×1920 reel · Just Video = raw video download")

        o = dict(bg="White", hide_quote=False, show_reply=False, only_video=False,
                 layout="Video bottom – Tweet top", crop=False, flip=False, vid_num=1,
                 safe=True, hide_username=False, font_family="Inter (Default)")

        st.markdown('<div class="styled-divider"></div>', unsafe_allow_html=True)

        if out_type == "Photo":
            c1, c2 = st.columns(2)
            o["bg"] = c1.radio("🎨 Background color", ["White", "Black"], horizontal=True)
            o["hide_quote"] = c2.radio("💬 Hide quoted tweet", ["No", "Yes"], horizontal=True, key="hq_p") == "Yes"
            c3, c4 = st.columns(2)
            o["show_reply"] = c3.radio("↩️ Show tweet replied to", ["No", "Yes"], horizontal=True) == "Yes"
            o["hide_username"] = c4.radio("👤 Hide username", ["No", "Yes"], horizontal=True, key="hu_p") == "Yes"
            o["font_family"] = st.selectbox("🔤 Font", list(FONT_CATALOGUE.keys()), index=0, key="font_p")

        elif out_type == "Video":
            c1, c2 = st.columns(2)
            o["only_video"] = c1.radio("🎥 Only show video", ["No", "Yes"], horizontal=True) == "Yes"
            o["bg"] = c2.radio("🎨 Background", ["White", "Blur", "Black"], horizontal=True)
            o["layout"] = st.radio("📐 Layout", ["Video bottom – Tweet top", "Video top – Tweet bottom"],
                                   disabled=o["only_video"])
            c3, c4 = st.columns(2)
            o["hide_quote"] = c3.radio("💬 Hide quoted tweet", ["No", "Yes"], horizontal=True, key="hq_v",
                                       disabled=o["only_video"]) == "Yes"
            o["hide_username"] = c4.radio("👤 Hide username", ["No", "Yes"], horizontal=True, key="hu_v") == "Yes"
            c5, c6, c7 = st.columns(3)
            o["crop"] = c5.checkbox("✂️ Crop to 1:1")
            o["flip"] = c6.checkbox("🔄 Flip horizontally")
            o["vid_num"] = int(c7.number_input("Video #", 1, 4, 1))
            o["font_family"] = st.selectbox("🔤 Font", list(FONT_CATALOGUE.keys()), index=0, key="font_v")

        elif out_type == "Just Video":
            o["vid_num"] = int(st.number_input("Video # (if tweet has multiple)", 1, 4, 1, key="jv_num"))

        if out_type != "Just Video":
            o["safe"] = st.checkbox("🛡️ Keep text inside Instagram safe zone (135 px sides, 250 px top/bottom)",
                                    value=True)

        st.markdown("")
        if st.button("🚀  Generate", type="primary", use_container_width=True):
            if not url.strip():
                st.warning("Paste a tweet URL first.")
            else:
                t0 = time.time()
                try:
                    with st.spinner("Generating… hang tight ✨"):
                        res = generate(url, out_type, o)
                    res["secs"] = time.time() - t0
                    st.session_state["result"] = res
                except Exception as e:
                    st.session_state.pop("result", None)
                    st.error(str(e))

        res = st.session_state.get("result")
        if res:
            st.success(f"✅  Done in {res['secs']:.1f} s")
            if res["kind"] == "photo":
                st.image(res["data"], use_container_width=True)
            else:
                _, mid, _ = st.columns([1, 2, 1])
                mid.video(res["data"])
            st.download_button("⬇️  Download", res["data"], file_name=res["name"], mime=res["mime"],
                               use_container_width=True)

        st.markdown("")
        st.markdown('<div class="styled-divider"></div>', unsafe_allow_html=True)
        a, b, c = st.columns(3)
        a.markdown(
            '<div class="quick-card"><h4>1 · Paste URL</h4>'
            '<p>Copy a public tweet link and paste it above</p></div>',
            unsafe_allow_html=True)
        b.markdown(
            '<div class="quick-card"><h4>2 · Customize</h4>'
            '<p>Pick output type, font, colors & layout</p></div>',
            unsafe_allow_html=True)
        c.markdown(
            '<div class="quick-card"><h4>3 · Download</h4>'
            '<p>Get your optimized content for social media</p></div>',
            unsafe_allow_html=True)

    with tab_download:
        st.subheader("⬇️  Download Video from Any Platform")
        st.markdown(
            "Paste a video URL from **Instagram**, **TikTok**, or **Twitter/X** "
            "and download the video file directly.\n\n"
            "Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) — "
            "no API keys needed."
        )
        dl_url = st.text_input(
            "🔗  Video URL",
            placeholder="https://www.instagram.com/reel/… or https://www.tiktok.com/@…/video/…",
            key="dl_url",
            help="Works with Instagram Reels/posts, TikTok videos, and Twitter/X video tweets"
        )

        platform = detect_platform(dl_url) if dl_url.strip() else None
        if platform:
            badge_cls = {"twitter": "badge-twitter", "instagram": "badge-insta", "tiktok": "badge-tiktok"}.get(
                platform, "badge-twitter"
            )
            st.markdown(
                f'Detected: <span class="badge {badge_cls}">{platform.title()}</span>',
                unsafe_allow_html=True,
            )

        if st.button("⬇️  Download Video", type="primary", key="dl_btn", use_container_width=True):
            if not dl_url.strip():
                st.warning("Paste a video URL first.")
            elif not detect_platform(dl_url):
                st.error("Unsupported URL. Please paste an Instagram, TikTok, or Twitter/X link.")
            else:
                t0 = time.time()
                try:
                    with st.spinner(f"Downloading from {platform.title()}… 🔄"):
                        res = generate_download(dl_url)
                    res["secs"] = time.time() - t0
                    st.session_state["dl_result"] = res
                except Exception as e:
                    st.session_state.pop("dl_result", None)
                    st.error(f"Download failed: {e}")

        dl_res = st.session_state.get("dl_result")
        if dl_res:
            st.success(f"✅  Downloaded in {dl_res['secs']:.1f} s  ({len(dl_res['data']) / (1024*1024):.1f} MB)")
            _, mid, _ = st.columns([1, 2, 1])
            mid.video(dl_res["data"])
            st.download_button(
                "⬇️  Save to Device", dl_res["data"],
                file_name=dl_res["name"], mime=dl_res["mime"],
                key="dl_save", use_container_width=True,
            )

        st.markdown("")
        st.markdown('<div class="styled-divider"></div>', unsafe_allow_html=True)
        st.markdown("**Supported platforms:**")
        cols = st.columns(3)
        cols[0].markdown("🐦 **Twitter / X**\nVideo tweets, GIFs")
        cols[1].markdown("📸 **Instagram**\nReels, video posts")
        cols[2].markdown("🎵 **TikTok**\nPublic videos")

    with tab_guide:
        st.header("📖  How to Use")
        st.markdown(
            "### Tweet → Photo / Video\n"
            "1. Open a public tweet on X and copy its link (**Share → Copy link**).\n"
            "2. Go to the **Home** tab and paste it into the URL field.\n"
            "3. Pick **Photo** for a tweet screenshot, **Video** for a 1080 × 1920 reel, "
            "or **Just Video** to grab only the raw video file.\n"
            "4. Adjust options — font, background, layout, hide username, etc.\n"
            "5. Click **Generate** and download the result.\n\n"
            "### Download from Instagram / TikTok\n"
            "1. Go to the **Download Video** tab.\n"
            "2. Paste an Instagram Reel, TikTok, or Twitter video URL.\n"
            "3. Click **Download Video** and save the file.\n\n"
            "### Tips\n"
            "- Only **public** tweets / posts work.\n"
            "- Emojis are removed from rendered text to avoid font issues.\n"
            "- Photo mode with a video tweet shows the thumbnail with a play icon.\n"
            "- The **Hide username** toggle removes `@handle` from the card.\n"
            "- Choose a **font** to match your brand or aesthetic.\n"
            "- **Just Video** pulls the original video file — no overlays, no reel format.\n"
            "- Instagram / TikTok download requires `yt-dlp` installed on the server."
        )

    st.markdown('<p class="footer-text">© Tweet to Reel – personal use · '
                'Powered by FxTwitter API & yt-dlp</p>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
