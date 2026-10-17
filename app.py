"""
Tweet to Reel - personal Streamlit clone of tweet-to-reel.com

Features
  Photo : background (white/black), hide quoted tweet, show tweet replied to
  Video : only show video, background (white/blur/black), layout (tweet top/bottom),
          hide quoted tweet, crop video to 1:1, flip horizontally
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
        path = find_font(
            "Inter-Bold.otf" if bold else "Inter-Regular.otf",
            "Inter-Bold.ttf" if bold else "Inter-Regular.ttf",
            "Inter_18pt-Bold.ttf" if bold else "Inter_18pt-Regular.ttf",
            "Roboto-Bold.ttf" if bold else "Roboto-Regular.ttf",
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
            "arialbd.ttf" if bold else "arial.ttf",
            "Arial Bold.ttf" if bold else "Arial.ttf",
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
            while wl > max_w and len(word) > 1:  # break very long words
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
def render_block(tw, th, width, quote=False, media=True, show_quote=True, show_date=True):
    S = QUOTE_S if quote else MAIN_S
    a = tw.get("author") or {}
    text = clean(tw.get("text", ""))
    lines = wrap(text, S["text"], width)
    q = tw.get("quote") if (show_quote and not quote) else None
    H = 3500 + len(lines) * S["lh"] + (3500 if q else 0)
    canvas = Image.new("RGBA", (width, H), th["bg"] + (255,))
    d = ImageDraw.Draw(canvas)

    # avatar
    av = S["av"]
    img = load_img(a["avatar_url"].replace("_normal", "_400x400")) if a.get("avatar_url") else None
    if img is None:
        img = Image.new("RGBA", (av, av), th["border"] + (255,))
    img = ImageOps.fit(img, (av, av), Image.LANCZOS)
    canvas.paste(img, (0, 0), rounded_mask((av, av), av // 2))

    # name / handle / verified
    tx = av + 16
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
    for line in lines:
        draw_words(d, 0, y, line, S["text"], False, th["text"], th, linkify=True)
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
        qi = render_block(q, th, width - 2 * qp, quote=True, media=True, show_quote=False, show_date=False)
        box = Image.new("RGBA", (width, qi.height + 2 * qp), th["bg"] + (255,))
        box.paste(qi, (qp, qp))
        ImageDraw.Draw(box).rounded_rectangle((0, 0, width - 1, box.height - 1), 28, outline=th["border"], width=2)
        canvas.paste(box, (0, y))
        y += box.height + S["gap"]

    if show_date and not quote:
        ds = fmt_date(tw)
        if ds:
            y += 4
            draw_words(d, 0, y, [ds], S["date"], False, th["sub"], th)
            y += S["date"] + 8

    return canvas.crop((0, 0, width, max(y, av)))


def render_photo(tw, parent, theme, show_quote, safe=True):
    th = THEMES[theme]
    GAP = 28
    M, MY = (SAFE_X, SAFE_Y) if safe else (56, 56)
    inner = CW - 2 * M
    mb = render_block(tw, th, inner, show_quote=show_quote)
    pb = render_block(parent, th, inner, show_quote=False, show_date=False) if parent else None
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


def render_card(tw, theme, blur, show_quote, safe=True):
    """Text-only tweet card used above/below the video in reels."""
    th = THEMES[theme]
    mo, pad_x, pad_y = (24, 44, 40) if blur else (0, 56, 44)
    if safe:
        pad_x = SAFE_X - mo  # text starts exactly at the safe-zone edge
    inner = CW - 2 * mo - 2 * pad_x
    block = render_block(tw, th, inner, media=False, show_quote=show_quote, show_date=False)
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
# Video
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
        png = render_photo(tw, parent, "dark" if o["bg"] == "Black" else "light", not o["hide_quote"], o["safe"])
        return dict(kind="photo", data=png, mime="image/png", name=f"tweet_{tid}.png")

    # ---- video ----
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
                                   bgname == "blur", not o["hide_quote"], o["safe"])
            vf.result()
        build_reel(vpath, card, bgname, o["layout"].startswith("Video bottom"),
                   o["crop"], o["flip"], out, td, o["safe"])
        with open(out, "rb") as f:
            data = f.read()
    return dict(kind="video", data=data, mime="video/mp4", name=f"reel_{tid}.mp4")


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Tweet to Reel", page_icon="🎬", layout="centered")
    st.markdown("<style>.block-container{max-width:760px;padding-top:2rem}"
                "div.stButton>button{width:100%}</style>", unsafe_allow_html=True)
    home, instr = st.tabs(["Home", "Instructions"])

    with home:
        st.title("🎬 Tweet to Reel")
        with st.expander("Option Guide"):
            st.markdown(
                "**Safe zone** – keeps all tweet text 135px from the sides and everything 250px from the "
                "top/bottom so Instagram never cuts it off (on by default)\n\n"
                "**Photo**\n"
                "- *Background color* – white (light) or black (dark) tweet image\n"
                "- *Hide quoted tweet* – leave out the embedded quote tweet\n"
                "- *Show tweet replied to* – adds the parent tweet above with a thread line\n\n"
                "**Video** (1080×1920 reel)\n"
                "- *Only show video* – no tweet card, just the video on the background\n"
                "- *Background* – white, blurred copy of the video, or black\n"
                "- *Layout* – tweet on top or at the bottom of the video\n"
                "- *Crop to 1:1* – center-crops the video to a square before stacking\n"
                "- *Flip* – mirrors the video horizontally")

        url = st.text_input("Tweet/X URL", placeholder="https://x.com/username/status/1234567890")
        out_type = st.radio("Output type", ["Photo", "Video"], horizontal=True)
        o = dict(bg="White", hide_quote=False, show_reply=False, only_video=False,
                 layout="Video bottom – Tweet top", crop=False, flip=False, vid_num=1, safe=True)

        if out_type == "Photo":
            c1, c2 = st.columns(2)
            o["bg"] = c1.radio("Background color", ["White", "Black"], horizontal=True)
            o["hide_quote"] = c2.radio("Hide quoted tweet", ["No", "Yes"], horizontal=True, key="hq_p") == "Yes"
            o["show_reply"] = st.radio("Show tweet replied to", ["No", "Yes"], horizontal=True) == "Yes"
        else:
            c1, c2 = st.columns(2)
            o["only_video"] = c1.radio("Only show video", ["No", "Yes"], horizontal=True) == "Yes"
            o["bg"] = c2.radio("Background", ["White", "Blur", "Black"], horizontal=True)
            o["layout"] = st.radio("Layout", ["Video bottom – Tweet top", "Video top – Tweet bottom"],
                                   disabled=o["only_video"])
            o["hide_quote"] = st.radio("Hide quoted tweet", ["No", "Yes"], horizontal=True, key="hq_v",
                                       disabled=o["only_video"]) == "Yes"
            c3, c4, c5 = st.columns(3)
            o["crop"] = c3.checkbox("Crop video to 1:1 before stacking")
            o["flip"] = c4.checkbox("Flip video horizontally")
            o["vid_num"] = int(c5.number_input("Video # (if several)", 1, 4, 1))

        o["safe"] = st.checkbox("Keep text inside Instagram safe zone (135px sides, 250px top/bottom)", value=True)

        if st.button("Generate", type="primary"):
            if not url.strip():
                st.warning("Paste a tweet URL first.")
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
            st.success(f"Done in {res['secs']:.1f}s")
            if res["kind"] == "photo":
                st.image(res["data"], use_container_width=True)
            else:
                _, mid, _ = st.columns([1, 2, 1])
                mid.video(res["data"])
            st.download_button("⬇️ Download", res["data"], file_name=res["name"], mime=res["mime"])

        st.subheader("Quick Start Guide")
        a, b, c = st.columns(3)
        a.markdown("**1. Paste Tweet URL**\n\nCopy the link from any public tweet with or without video")
        b.markdown("**2. Choose Options**\n\nSelect output type, layout, and background style")
        c.markdown("**3. Download & Share**\n\nGet your optimized content ready for social media")

    with instr:
        st.header("Instructions")
        st.markdown(
            "1. Open a public tweet on X and copy its link (Share → Copy link).\n"
            "2. Paste it into **Tweet/X URL** on the Home tab.\n"
            "3. Pick **Photo** for a tweet screenshot or **Video** for a 1080×1920 reel.\n"
            "4. Adjust the options and click **Generate**.\n"
            "5. Download the result and post it to Reels, Shorts or TikTok.\n\n"
            "**Notes**\n"
            "- Only public tweets work.\n"
            "- Emojis are removed from the tweet text in the rendered card.\n"
            "- Photo mode with a video tweet shows the thumbnail with a play icon.")
    st.caption("© Tweet to Reel – personal use")


if __name__ == "__main__":
    main()
