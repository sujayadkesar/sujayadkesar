#!/usr/bin/env python3
"""Render the profile's SVG panels into assets/ and refresh the generated README blocks.

Content comes from data/profile.json, live numbers from data/stats.json (written by
scripts/refresh.py). Text is set in IBM Plex (SIL OFL 1.1): each SVG embeds a subset
of the faces it uses, so the panels look the same on every OS and in every browser.

    pip install fonttools brotli
    python scripts/render.py
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
README = ROOT / "README.md"
FONT_DIR = ROOT / "scripts" / "fonts"
PROFILE_PATH = ROOT / "data" / "profile.json"
STATS_PATH = ROOT / "data" / "stats.json"
PROFILE = json.loads(PROFILE_PATH.read_text())
STATS = json.loads(STATS_PATH.read_text())
ICONS = json.loads((ROOT / "scripts" / "icons.json").read_text())

SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
FACE_SPECS = {  # key: (file, css weight, fallback stack)
    "cond": ("IBMPlexSansCondensed-700.ttf", 700, "'Arial Narrow'," + SANS),
    "sans": ("IBMPlexSans-400.ttf", 400, SANS),
    "sans5": ("IBMPlexSans-500.ttf", 500, SANS),
    "sans6": ("IBMPlexSans-600.ttf", 600, SANS),
    "mono": ("IBMPlexMono-400.ttf", 400, MONO),
    "mono6": ("IBMPlexMono-600.ttf", 600, MONO),
}

THEMES = {
    "dark": {
        "page": "#0D1117", "card": "#10151C", "card2": "#151C25", "inset": "#0B1016",
        "line": "#262F3B", "line2": "#1C242E", "text": "#E6EDF3", "text2": "#B7C0CA",
        "muted": "#8590A0", "dim": "#5E6978", "accent": "#FFD23F", "accent_text": "#FFD23F",
        "red": "#FF7B72", "amber": "#E3B341", "green": "#3FB950", "cyan": "#5CC8D7", "bar": "#232C38",
    },
    "light": {
        "page": "#FFFFFF", "card": "#FFFFFF", "card2": "#F6F8FA", "inset": "#F6F8FA",
        "line": "#D0D7DE", "line2": "#E3E8ED", "text": "#1F2328", "text2": "#3B434C",
        "muted": "#59636E", "dim": "#8C959F", "accent": "#F2C200", "accent_text": "#8F5E00",
        "red": "#C9252F", "amber": "#9A6700", "green": "#1A7F37", "cyan": "#0A6E8A", "bar": "#D8DEE4",
    },
}


# ── fonts ────────────────────────────────────────────────────────────────────

class Face:
    def __init__(self, key: str):
        file, self.weight, self.fallback = FACE_SPECS[key]
        self.key, self.path = key, FONT_DIR / file
        tt = TTFont(self.path, lazy=True)
        self.cmap = tt.getBestCmap()
        self.adv = {g: m[0] for g, m in tt["hmtx"].metrics.items()}
        self.upm = tt["head"].unitsPerEm
        self._woff2: dict[str, str] = {}

    def width(self, s: str, size: float, ls: float = 0.0) -> float:
        missing = [c for c in s if ord(c) not in self.cmap]
        if missing:
            raise ValueError(f"{self.key}: no glyph for {missing!r} in {s!r}")
        return sum(self.adv[self.cmap[ord(c)]] for c in s) * size / self.upm + ls * len(s)

    def woff2(self, chars: set[str]) -> str:
        key = "".join(sorted(chars))
        if key not in self._woff2:
            font = TTFont(self.path, recalcTimestamp=False)
            opts = subset.Options()
            opts.flavor = "woff2"
            opts.layout_features = ["kern"]
            opts.hinting = False
            opts.desubroutinize = True
            sub = subset.Subsetter(opts)
            sub.populate(text=key)
            sub.subset(font)
            buf = io.BytesIO()
            font.flavor = "woff2"
            font.save(buf)
            self._woff2[key] = base64.b64encode(buf.getvalue()).decode()
        return self._woff2[key]


F = {k: Face(k) for k in FACE_SPECS}


def n(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def wrap(s: str, face: str, size: float, maxw: float) -> list[str]:
    lines, cur = [], ""
    for word in s.split(" "):
        trial = f"{cur} {word}".strip()
        if cur and F[face].width(trial, size) > maxw:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])


# ── svg document ─────────────────────────────────────────────────────────────

class Svg:
    def __init__(self, w: float, h: float, title: str, desc: str = ""):
        self.w, self.h, self.title, self.desc = w, h, title, desc
        self.defs: list[str] = []
        self.body: list[str] = []
        self.css: list[str] = []
        self.used: dict[str, set[str]] = {}

    def add(self, *parts: str) -> None:
        self.body.extend(parts)

    def text(self, x, y, s, face="sans", size=16, fill="#000", anchor="start", ls=0.0,
             cls="", extra="", pre=False) -> float:
        self.used.setdefault(face, set()).update(s)
        a = f'x="{n(x)}" y="{n(y)}" class="{face}{" " + cls if cls else ""}" font-size="{n(size)}" fill="{fill}"'
        if anchor != "start":
            a += f' text-anchor="{anchor}"'
        if ls:
            a += f' letter-spacing="{n(ls)}"'
        if pre:
            a += ' xml:space="preserve"'
        if extra:
            a += " " + extra
        self.body.append(f"<text {a}>{escape(s)}</text>")
        return F[face].width(s, size, ls)

    def render(self) -> str:
        css = []
        for key in sorted(self.used):
            f = F[key]
            css.append(
                f"@font-face{{font-family:'p-{key}';font-weight:{f.weight};"
                f"src:url(data:font/woff2;base64,{f.woff2(self.used[key])}) format('woff2')}}"
                f".{key}{{font-family:'p-{key}',{f.fallback};font-weight:{f.weight}}}"
            )
        css += self.css
        css.append("@media (prefers-reduced-motion:reduce){*{animation:none!important}}")
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{n(self.w)}" height="{n(self.h)}" '
            f'viewBox="0 0 {n(self.w)} {n(self.h)}" role="img" aria-labelledby="t d">'
            f'<title id="t">{escape(self.title)}</title><desc id="d">{escape(self.desc or self.title)}</desc>'
            f"<style>{''.join(css)}</style><defs>{''.join(self.defs)}</defs>"
            + "".join(self.body) + "</svg>\n"
        )


# ── drawing helpers ──────────────────────────────────────────────────────────

def rect(x, y, w, h, rx=0, fill="none", stroke=None, extra=""):
    s = f' stroke="{stroke}"' if stroke else ""
    return f'<rect x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" rx="{n(rx)}" fill="{fill}"{s}{" " + extra if extra else ""}/>'


def line(x1, y1, x2, y2, stroke, sw=1, extra=""):
    return f'<line x1="{n(x1)}" y1="{n(y1)}" x2="{n(x2)}" y2="{n(y2)}" stroke="{stroke}" stroke-width="{n(sw)}"{" " + extra if extra else ""}/>'


def star(cx, cy, r, fill):
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.46
        pts.append(f"{n(cx + rr * math.cos(a))},{n(cy + rr * math.sin(a))}")
    return f'<polygon points="{" ".join(pts)}" fill="{fill}"/>'


def diamond(cx, cy, r, fill):
    return f'<polygon points="{n(cx)},{n(cy - r)} {n(cx + r)},{n(cy)} {n(cx)},{n(cy + r)} {n(cx - r)},{n(cy)}" fill="{fill}"/>'


def check(x, y, s, stroke, sw=1.8):
    return (f'<path d="M{n(x)} {n(y + s * .5)}l{n(s * .35)} {n(s * .35)}l{n(s * .65)} {n(-s * .75)}" '
            f'stroke="{stroke}" stroke-width="{n(sw)}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')


def brand_icon(name, x, y, size, fill):
    return f'<path transform="translate({n(x)} {n(y)}) scale({n(size / 24)})" d="{ICONS[name]}" fill="{fill}"/>'


def glyph(kind, cx, cy, color):
    """Small line icons, roughly 20px, centred on (cx, cy)."""
    st = f'stroke="{color}" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    if kind == "windows":
        return "".join(rect(cx + dx, cy + dy, 7.5, 7.5, 1.2, color) for dx in (-8.5, 1) for dy in (-8.5, 1))
    if kind == "phone":
        return (f'<rect x="{n(cx - 6.5)}" y="{n(cy - 10.5)}" width="13" height="21" rx="2.6" {st}/>'
                + line(cx - 2, cy + 6.5, cx + 2, cy + 6.5, color, 1.7, 'stroke-linecap="round"'))
    if kind == "pulse":
        return f'<path d="M{n(cx - 11)} {n(cy)}h5l2.6-7 4.6 14 2.8-7h7" {st}/>'
    if kind == "flow":
        return (f'<path d="M{n(cx - 6)} {n(cy - 5)}h12M{n(cx - 6)} {n(cy - 5)}L{n(cx)} {n(cy + 6)}M{n(cx + 6)} {n(cy - 5)}L{n(cx)} {n(cy + 6)}" {st}/>'
                + "".join(f'<circle cx="{n(cx + dx)}" cy="{n(cy + dy)}" r="3.2" fill="{color}"/>'
                          for dx, dy in ((-7, -5.5), (7, -5.5), (0, 7))))
    if kind == "wifi":
        return (f'<path d="M{n(cx - 9)} {n(cy - 3)}a13 13 0 0 1 18 0M{n(cx - 5.5)} {n(cy + 1)}a8 8 0 0 1 11 0" {st}/>'
                f'<circle cx="{n(cx)}" cy="{n(cy + 5.5)}" r="1.9" fill="{color}"/>')
    if kind == "lock":
        return (f'<rect x="{n(cx - 7)}" y="{n(cy - 2)}" width="14" height="11" rx="2.2" fill="{color}"/>'
                f'<path d="M{n(cx - 4.2)} {n(cy - 2)}v-3a4.2 4.2 0 0 1 8.4 0v3" {st}/>')
    if kind == "disk":
        return (f'<rect x="{n(cx - 8)}" y="{n(cy - 6)}" width="16" height="12" rx="2.4" {st}/>'
                f'<circle cx="{n(cx + 4)}" cy="{n(cy + 1.5)}" r="1.4" fill="{color}"/>')
    raise ValueError(kind)


def barcode(x, y, w, h, seed, color):
    bits = hashlib.sha256(seed.encode()).digest()
    cx, i, d = x, 0, []
    while True:
        b = bits[i % len(bits)]
        i += 1
        bw = 1 + (b & 3) * 0.7
        if cx + bw > x + w:
            break
        d.append(f"M{n(cx)} {n(y)}h{n(bw)}v{n(h)}h{n(-bw)}z")
        cx += bw + 1.3 + ((b >> 2) & 3) * 0.55
    return f'<path d="{"".join(d)}" fill="{color}"/>'


def chip_width(label, face="mono", size=12, padx=10, ls=0.0):
    return F[face].width(label, size, ls) + 2 * padx


def chip(d: Svg, x, y, label, *, face="mono", size=12, h=24, padx=10, ls=0.0, rx=6,
         fill="none", stroke=None, color="#000"):
    w = chip_width(label, face, size, padx, ls)
    d.add(rect(x + .5, y + .5, w - 1, h - 1, rx, fill, stroke))
    d.text(x + padx, y + h / 2 + size * .35, label, face, size, color, ls=ls)
    return w


def flow(d: Svg, x, y, maxw, items, gap=8, rowgap=8, **kw):
    cx, cy, h = x, y, kw.get("h", 24)
    for label in items:
        w = chip_width(label, kw.get("face", "mono"), kw.get("size", 12), kw.get("padx", 10), kw.get("ls", 0.0))
        if cx > x and cx + w > x + maxw:
            cx, cy = x, cy + h + rowgap
        chip(d, cx, cy, label, **kw)
        cx += w + gap
    return cy + h


def card_frame(d: Svg, t, w, h, clip="k"):
    d.defs.append(f'<clipPath id="{clip}"><rect width="{n(w)}" height="{n(h)}" rx="14"/></clipPath>')
    d.add(rect(.5, .5, w - 1, h - 1, 13.5, t["card"], t["line"]))


def form_bar(d: Svg, t, w, left, right):
    d.add(f'<g clip-path="url(#k)">{rect(0, 0, w, 48, 0, t["card2"])}</g>',
          line(0, 48.5, w, 48.5, t["line"]))
    d.text(28, 29, left, "mono", 11.5, t["muted"], ls=1.4)
    d.text(w - 28, 29, right, "mono", 11.5, t["muted"], anchor="end", ls=1.4)


def stars_pill(d: Svg, t, x_end, y, count):
    label = f"{count:,}"
    w = 34 + F["mono6"].width(label, 12.5)
    x = x_end - w
    d.add(rect(x + .5, y + .5, w - 1, 25, 12.5, t["card2"], t["line"]),
          star(x + 15, y + 13, 6.4, t["accent"]))
    d.text(x + 26, y + 17.5, label, "mono6", 12.5, t["text"])
    return w


def nice_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y").upper()
    except ValueError:
        return iso


def repo_stars(name: str) -> int:
    return STATS.get("repos", {}).get(name, {}).get("stars", 0)


# ── hero ─────────────────────────────────────────────────────────────────────

HITS = [  # (label, locator, offset, bytes, signature byte range)
    ("E01 SEGMENT HEADER", "file offset 0x0", "00000000", "45 56 46 09 0D 0A FF 00 01 01 00 00 00 68 65 61", (0, 8)),
    ("NTFS VOLUME BOOT RECORD", "LBA 2048", "00100000", "EB 52 90 4E 54 46 53 20 20 20 20 00 02 08 00 00", (3, 11)),
    ("$MFT FILE RECORD", "MFT entry 0", "C0100000", "46 49 4C 45 30 00 03 00 7A 1C 4B 02 00 00 00 00", (0, 4)),
    ("REGISTRY HIVE · SYSTEM", "System32\\config", "3A4F2000", "72 65 67 66 2F 1A 00 00 2F 1A 00 00 C3 5B 8E 1D", (0, 4)),
    ("SQLITE · MANIFEST.DB", "MobileSync\\Backup", "9D27A800", "53 51 4C 69 74 65 20 66 6F 72 6D 61 74 20 33 00", (0, 16)),
]


def hero() -> str:
    p = PROFILE
    W, H = 1200, 630
    C = {"bg": "#07090D", "bar": "#0B0E14", "line": "#1B222C", "panel": "#0A0E14", "text": "#F3F6F9",
         "text2": "#C9D1D9", "hex": "#8E9AA8", "muted": "#8A96A5", "dim": "#56616F", "accent": "#FFD23F",
         "green": "#3FB950", "amber": "#E3B341", "red": "#FF7B72", "bin": "#1E2732", "bin2": "#2A3544"}
    d = Svg(W, H, f"{p['name']} — {p['kicker']}",
            f"{p['name']}. {p['role']}. {p['tagline']} Illustration: a forensic workstation carving file "
            "signatures (E01, NTFS, $MFT, regf, SQLite) from a disk image, verifying its SHA-256 hash, "
            "and a 24-hour super-timeline with a USB connection and an Eraser execution flagged.")
    d.defs.append(
        f'<clipPath id="c"><rect width="{W}" height="{H}" rx="18"/></clipPath>'
        '<pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#FFFFFF" fill-opacity=".045"/></pattern>'
        '<radialGradient id="glow" cx=".82" cy=".05" r=".75"><stop offset="0" stop-color="#FFD23F" stop-opacity=".11"/><stop offset="1" stop-color="#FFD23F" stop-opacity="0"/></radialGradient>'
        '<linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFD23F" stop-opacity="0"/>'
        '<stop offset=".86" stop-color="#FFD23F" stop-opacity=".07"/><stop offset=".97" stop-color="#FFD23F" stop-opacity=".3"/>'
        '<stop offset="1" stop-color="#FFD23F" stop-opacity="0"/></linearGradient>'
    )
    d.css.append(
        "text{text-rendering:geometricPrecision}"
        "@keyframes rise{from{opacity:0;transform:translateY(12px)}}@keyframes fade{from{opacity:0}}"
        "@keyframes blink{50%{opacity:0}}@keyframes hold{0%,100%{opacity:1}}"
        ".r1{animation:rise .8s cubic-bezier(.2,.8,.2,1) .05s backwards}.r2{animation:rise .8s cubic-bezier(.2,.8,.2,1) .15s backwards}"
        ".r3{animation:rise .8s cubic-bezier(.2,.8,.2,1) .25s backwards}.r4{animation:rise .8s cubic-bezier(.2,.8,.2,1) .4s backwards}"
        ".r5{animation:rise .8s cubic-bezier(.2,.8,.2,1) .55s backwards}.r6{animation:rise .8s cubic-bezier(.2,.8,.2,1) .7s backwards}"
        ".blink{animation:blink 1.1s steps(1) infinite}"
        ".hashing{opacity:0;animation:hold 3s linear}.verified{animation:fade .4s ease-out 3s backwards}"
        ".prog{transform-box:fill-box;transform-origin:0 50%;animation:prog 2.6s cubic-bezier(.4,0,.2,1) .4s backwards}"
        "@keyframes prog{from{transform:scaleX(0)}}"
        ".beam{animation:beam 6s linear infinite}"
        "@keyframes beam{0%{transform:translateY(0);opacity:0}6%{opacity:1}80%{transform:translateY(320px);opacity:1}86%,100%{transform:translateY(320px);opacity:0}}"
        ".play{animation:play 16s linear infinite}@keyframes play{to{transform:translateX(1112px)}}"
    )
    for k in range(8):
        d.css.append(f".h{k}{{animation:fade .2s ease-out {0.4 + 2.6 * (k + 1) / 8:.2f}s backwards}}")

    d.add('<g clip-path="url(#c)">', rect(0, 0, W, H, 0, C["bg"]), rect(0, 0, W, H, 0, "url(#dots)"),
          rect(0, 0, W, H, 0, "url(#glow)"))

    # case header bar
    d.add(rect(0, 0, W, 46, 0, C["bar"]), line(0, 46.5, W, 46.5, C["line"]), rect(28, 19, 8, 8, 1.5, C["accent"]))
    x = 46
    items = [("CASE", p["case_ref"]), ("EXAMINER", "S. ADKESAR"), ("HOST", p["host"]), ("WRITE-BLOCK", "ON")]
    for i, (k, v) in enumerate(items):
        x += d.text(x, 27.5, k, "mono", 11, C["dim"], ls=1.4) + 8
        x += d.text(x, 27.5, v, "mono6", 11, C["text2"], ls=1)
        if i < len(items) - 1:
            d.add(line(x + 15.5, 17, x + 15.5, 30, C["line"]))
            x += 32
    wv = F["mono6"].width("VERIFIED", 11, 1.2)
    wh = F["mono6"].width("HASHING…", 11, 1.2)
    d.text(1172, 27.5, "VERIFIED", "mono6", 11, C["green"], anchor="end", ls=1.2, cls="verified")
    d.text(1172, 27.5, "HASHING…", "mono6", 11, C["amber"], anchor="end", ls=1.2, cls="hashing")
    dot_x = 1172 - max(wv, wh) - 10
    d.add(f'<circle cx="{n(dot_x)}" cy="23.5" r="3.5" fill="{C["green"]}" class="verified"/>',
          f'<circle cx="{n(dot_x)}" cy="23.5" r="3.5" fill="{C["amber"]}" class="hashing"/>')
    d.text(dot_x - 12, 27.5, "INTEGRITY", "mono", 11, C["dim"], anchor="end", ls=1.4)

    # identity column
    d.add(f'<g class="r1">{rect(56, 103, 28, 2, 1, C["accent"])}')
    d.text(96, 109, p["kicker"].upper(), "mono6", 13, C["accent"], ls=2.4)
    d.add("</g>")
    l1, l2 = p["name_lines"]
    d.text(52, 214, l1, "cond", 112, C["text"], ls=-1, cls="r2")
    w2 = d.text(52, 318, l2, "cond", 112, C["text"], ls=-1, cls="r3")
    d.add(rect(52 + w2 + 12, 240, 15, 78, 1, C["accent"], extra='class="blink"'))
    d.text(56, 366, p["role"], "sans5", 21, C["text2"], cls="r4")
    for i, ln in enumerate(wrap(p["tagline"], "sans", 18, 520)):
        d.text(56, 404 + i * 28, ln, "sans", 18, C["muted"], cls="r5")
    d.add('<g class="r6">')
    cx = 56 + d.text(56, 506, "CASEWORK", "mono", 11, C["dim"], ls=1.6) + 14
    for c in p["casework_chips"]:
        cx += chip(d, cx, 490, c, face="mono6", size=11, h=24, padx=9, ls=1.1, rx=5,
                   fill="#0D1218", stroke=C["line"], color=C["text2"]) + 8
    d.add("</g>")

    # hex panel
    px, py, pw, ph = 616, 76, 552, 440
    x0, cw = 632, 0.6 * 11.5
    hex_x, asc_x = x0 + 10 * cw, x0 + 59 * cw
    d.add(rect(px + .5, py + .5, pw - 1, ph - 1, 12, C["panel"], C["line"]), glyph("disk", 642, 97, C["muted"]))
    d.text(658, 101.5, "SA-001.E01", "mono6", 12.5, C["text2"], ls=.4)
    d.text(1152, 101.5, "238.5 GiB · E01 · READ-ONLY", "mono", 11, C["dim"], anchor="end", ls=.8)
    d.add(line(px, 118.5, px + pw, 118.5, C["line"]))
    d.text(x0, 142, "OFFSET", "mono", 11.5, C["dim"])
    d.text(hex_x, 142, " ".join(f"{i:02X}" for i in range(16)), "mono", 11.5, C["dim"])
    d.text(asc_x, 142, "ASCII", "mono", 11.5, C["dim"])

    d.add('<g clip-path="url(#c)">'
          f'<rect x="{px + 1}" y="90" width="{pw - 2}" height="60" fill="url(#beam)" class="beam"/></g>')
    for i, (label, loc, off, hexs, (a, b)) in enumerate(HITS):
        ly = 170 + i * 52
        ry = ly + 20
        raw = [int(h, 16) for h in hexs.split()]
        asc = "".join(chr(v) if 32 <= v < 127 else "." for v in raw)
        f = 0.80 * (ry - 4 - 148) / 320 * 100
        d.css.append(f"@keyframes p{i}{{0%,{max(f - 4, 0):.1f}%,{f + 9:.1f}%,100%{{opacity:.45}}{f + 1:.1f}%{{opacity:1}}}}"
                     f".p{i}{{animation:p{i} 6s linear infinite}}")
        d.add(diamond(x0 + 3.5, ly - 4, 3.5, C["accent"]))
        d.text(x0 + 14, ly, label, "mono6", 11, C["accent"], ls=1)
        d.text(1152, ly, loc, "mono", 11, C["dim"], anchor="end")
        d.add(f'<g class="p{i}" opacity=".45">'
              + rect(hex_x + a * 3 * cw - 3, ry - 13, (b - a) * 3 * cw - cw + 6, 18, 3, C["accent"], extra='fill-opacity=".28"')
              + rect(asc_x + a * cw - 2, ry - 13, (b - a) * cw + 4, 18, 3, C["accent"], extra='fill-opacity=".28"')
              + "</g>")
        d.text(x0, ry, off, "mono", 11.5, C["dim"])
        parts = hexs.split()
        for lo, hi, col in ((0, a, C["hex"]), (a, b, C["accent"]), (b, 16, C["hex"])):
            if hi > lo:
                d.text(hex_x + lo * 3 * cw, ry, " ".join(parts[lo:hi]), "mono6" if col == C["accent"] else "mono", 11.5, col)
                d.text(asc_x + lo * cw, ry, asc[lo:hi], "mono6" if col == C["accent"] else "mono", 11.5, col, pre=True)

    # acquisition hash
    digest = hashlib.sha256(f"{p['login']}:{p['case_ref']}".encode()).hexdigest()
    d.add(line(px, 420.5, px + pw, 420.5, C["line"]))
    d.text(x0, 448, "SHA-256 · ACQUISITION HASH", "mono", 11, C["dim"], ls=1)
    vw = 30 + F["mono6"].width("VERIFIED", 10.5, 1.2)
    d.add(f'<g class="verified">{rect(1152 - vw + .5, 434.5, vw - 1, 20, 10, C["green"], C["green"], "fill-opacity=\".12\" stroke-opacity=\".55\"")}'
          + check(1152 - vw + 10, 439.5, 9, C["green"], 1.6))
    d.text(1152 - vw + 23, 448, "VERIFIED", "mono6", 10.5, C["green"], ls=1.2)
    d.add("</g>")
    d.text(1152, 448, "HASHING…", "mono6", 10.5, C["amber"], anchor="end", ls=1.2, cls="hashing")
    for k in range(8):
        d.text(x0 + k * 8 * cw, 474, digest[k * 8:(k + 1) * 8], "mono", 11.5, C["text2"], cls=f"h{k}")
    d.add(rect(x0, 492, 520, 4, 2, "#151B23"), rect(x0, 492, 520, 4, 2, C["accent"], extra='class="prog"'))

    # super-timeline
    d.text(56, 556, "SUPER-TIMELINE · 24 H · 15-MIN BINS", "mono6", 11, C["dim"], ls=1.4)
    step, base = 1112 / 96, 602
    spikes = {40: (30, C["accent"]), 72: (25, C["red"])}
    bars = []
    for i in range(96):
        hour = i / 4
        noise = hashlib.sha256(f"bin{i}".encode()).digest()[0] / 255
        work = max(0.0, math.sin(math.pi * (hour - 7.5) / 11)) if 7.5 < hour < 18.5 else 0.0
        h = 2.5 + (2 + 15 * work) * (0.5 + 0.9 * noise)
        col = C["bin2"] if work else C["bin"]
        if i in spikes:
            h, col = spikes[i]
        bars.append(rect(56 + i * step, base - h, 7, h, 1.5, col))
    d.add(*bars)
    for i, (lab, color, msg) in ((40, ("10:04", C["accent"], "USB\\VID_0951&PID_1666 first connected")),
                                 (72, ("18:12", C["red"], "ERASER.EXE executed · Prefetch"))):
        bx = 56 + i * step + 3.5
        d.add(line(bx, 561, bx, base - spikes[i][0] - 3, color, 1, 'stroke-dasharray="2 2.5" opacity=".7"'))
        tx = bx + 8 + d.text(bx + 8, 556, lab, "mono6", 11, color, ls=.6) + 8
        d.text(tx, 556, msg, "mono", 11, C["muted"])
    for hr in range(25):
        hx = 56 + hr * 4 * step + 3.5 if hr < 24 else 56 + 95 * step + 7
        d.add(line(hx, 606, hx, 609 if hr % 6 else 611, C["dim"]))
    for hr, anchor in ((0, "start"), (6, "middle"), (12, "middle"), (18, "middle"), (24, "end")):
        hx = 56 + hr * 4 * step if hr < 24 else 56 + 95 * step + 7
        d.text(hx, 624, f"{hr:02d}:00", "mono", 10.5, C["dim"], anchor=anchor)
    d.add(f'<g class="play">{line(56, 562, 56, 604, C["accent"], 1.5, "opacity=\".85\"")}'
          f'<polygon points="52.5,562 59.5,562 56,567" fill="{C["accent"]}"/></g>')

    d.add("</g>", rect(.5, .5, W - 1, H - 1, 17.5, "none", C["line"]))
    return d.render()


# ── small pieces ─────────────────────────────────────────────────────────────

def link_pill(link, t) -> str:
    h, pad = 40, 14
    lw = F["sans6"].width(link["label"], 14)
    hw = F["mono"].width(link["handle"], 12.5)
    w = pad + 16 + 10 + lw + 10 + hw + 12 + F["sans"].width("↗", 14) + pad
    d = Svg(w, h, f"{link['label']} — {link['handle']}")
    d.add(rect(.5, .5, w - 1, h - 1, 9, t["card"], t["line"]), brand_icon(link["icon"], pad, 12, 16, t["text"]))
    x = pad + 26
    x += d.text(x, 25, link["label"], "sans6", 14, t["text"]) + 10
    x += d.text(x, 25, link["handle"], "mono", 12.5, t["muted"]) + 12
    d.text(x, 25, "↗", "sans", 14, t["dim"])
    return d.render()


def scale_bar(d: Svg, t, x, y, w):
    """An ABFO-style photo scale: alternating blocks, then a tick ruler."""
    parts = []
    for i in range(10):
        bx = x + i * 22
        parts.append(rect(bx, y, 22, 8, 0, t["accent"]) if i % 2 == 0 else rect(bx + .5, y + .5, 21, 7, 0, "none", t["line"]))
    rx = x + 232
    parts.append(line(rx, y + 8.5, x + w, y + 8.5, t["line"]))
    for k in range(int((x + w - rx) // 10) + 1):
        tx = rx + k * 10 + .5
        parts.append(line(tx, y + 8.5 - (8 if k % 10 == 0 else 5 if k % 5 == 0 else 3), tx, y + 8.5, t["line"]))
    d.add(*parts)


def section(key, t) -> str:
    s = PROFILE["sections"][key]
    W, H = 1200, 88
    d = Svg(W, H, f"§ {s['no']} — {s['title']}", s["sub"])
    x = d.text(0, 44, f"§ {s['no']}", "mono6", 15, t["accent_text"], ls=1) + 18
    d.text(x, 46, s["title"].upper(), "cond", 30, t["text"], ls=1.2)
    d.text(W, 44, s["sub"], "sans", 15, t["muted"], anchor="end")
    scale_bar(d, t, 0, 66, W)
    return d.render()


# ── examiner profile ─────────────────────────────────────────────────────────

def dossier(t) -> str:
    p = PROFILE
    W = 1200
    rows = []
    for col, items, maxw in (("l", p["casework"], 484), ("r", p["principles"], 476)):
        for it in items:
            rows.append((col, it, wrap(it["detail"], "sans", 14.5, maxw)))
    extra = max(len(lines) for _, _, lines in rows) - 1
    step = 72 + 21 * extra
    H = 132 + 3 * step + 26 + 21 * extra + 36
    d = Svg(W, H, "Examiner profile",
            "Casework: " + "; ".join(f"{c['title']} — {c['detail']}" for c in p["casework"])
            + ". Principles: " + "; ".join(f"{c['title']} — {c['detail']}" for c in p["principles"]) + ".")
    card_frame(d, t, W, H)
    form_bar(d, t, W, "FORM DF-01 · EXAMINER PROFILE", f"REF {p['case_ref']} · PAGE 1 / 1")
    d.add(line(612.5, 76, 612.5, H - 28, t["line2"]))
    d.text(36, 92, "CASEWORK", "mono6", 12, t["accent_text"], ls=2.4)
    d.text(648, 92, "PRINCIPLES", "mono6", 12, t["accent_text"], ls=2.4)
    li = ri = 0
    for col, it, lines in rows:
        if col == "l":
            y = 132 + li * step
            d.add(rect(36.5, y - 23.5, 39, 39, 9, t["card2"], t["line"]), glyph(it["icon"], 56, y - 4, t["text2"]))
            tx = 92
            li += 1
        else:
            y = 132 + ri * step
            d.text(648, y - 1, f"{ri + 1:02d}", "mono6", 14, t["accent_text"], ls=.5)
            tx = 688
            ri += 1
        d.text(tx, y - 1, it["title"], "sans6", 17, t["text"])
        for k, ln in enumerate(lines):
            d.text(tx, y + 22 + k * 21, ln, "sans", 14.5, t["muted"])
    return d.render()


# ── exhibits ─────────────────────────────────────────────────────────────────

VERDICTS = [("USB storage device connected", "YES"), ("Files opened from removable media", "YES"),
            ("Upload to webmail or cloud storage", "INDICATED"), ("Optical disc burned", "YES"),
            ("Documents printed", "NO EVIDENCE")]


def verdict_pill(d: Svg, t, x_end, y, v):
    col = {"YES": t["red"], "INDICATED": t["amber"]}.get(v, t["muted"])
    w = chip_width(v, "mono6", 10.5, 10, 1)
    fill_op = ".12" if v in ("YES", "INDICATED") else "0"
    d.add(rect(x_end - w + .5, y + .5, w - 1, 21, 10.5, col, col, f'fill-opacity="{fill_op}" stroke-opacity=".55"'))
    d.text(x_end - w + 10, y + 15, v, "mono6", 10.5, col, ls=1)


def art_verdicts(d: Svg, t, x, y, w, h):
    d.add(rect(x + .5, y + .5, w - 1, h - 1, 10, t["inset"], t["line2"]))
    tx = x + 20 + d.text(x + 20, y + 30, "CASE PROFILE", "mono", 11, t["dim"], ls=1.4) + 10
    d.text(tx, y + 30, "DATA EXFILTRATION", "mono6", 11.5, t["accent_text"], ls=1.4)
    d.add(line(x, y + 46.5, x + w, y + 46.5, t["line2"]))
    for i, (q, v) in enumerate(VERDICTS):
        ry = y + 78 + i * 38
        d.text(x + 20, ry, q, "sans", 15, t["text2"])
        verdict_pill(d, t, x + w - 20, ry - 15, v)
    d.text(x + 20, y + h - 18, "Every answer cites the artifacts behind it", "mono", 11, t["dim"])


DEVICES = [("phone", "iPhone 15 Pro", "UDID 00008130-001A2D3E0C38001E", ["USB", "PAIRING", "BACKUP"]),
           ("phone", "OnePlus 12R (CPH2585)", "BT 94:65:2D:… → OnePlus", ["PHONE LINK", "BLUETOOTH"]),
           ("wifi", "Hotspot · 1 network", "6 random BSSIDs grouped", ["WI-FI"])]


def art_devices(d: Svg, t, x, y, w, h):
    d.add(rect(x + .5, y + .5, w - 1, h - 1, 10, t["inset"], t["line2"]))
    d.text(x + 20, y + 30, "DEVICES SEEN ON THIS COMPUTER", "mono", 11, t["dim"], ls=1.4)
    d.text(x + w - 20, y + 30, "1 DEVICE PER PHONE", "mono6", 11, t["accent_text"], anchor="end", ls=1.2)
    d.add(line(x, y + 46.5, x + w, y + 46.5, t["line2"]))
    for i, (icon, name, ident, tags) in enumerate(DEVICES):
        ry = y + 80 + i * 60
        d.add(glyph(icon, x + 30, ry + 2, t["text2"]))
        d.text(x + 52, ry, name, "sans6", 15.5, t["text"])
        d.text(x + 52, ry + 20, ident, "mono", 11.5, t["muted"])
        cx = x + w - 20
        for tag in reversed(tags):
            cw = chip_width(tag, "mono6", 10, 8, .8)
            cx -= cw
            chip(d, cx, ry - 15, tag, face="mono6", size=10, h=21, padx=8, ls=.8, rx=10.5,
                 fill=t["card2"], stroke=t["line"], color=t["text2"])
            cx -= 6
    d.text(x + 20, y + h - 18, "SYSTEM:\\ControlSet001\\Enum\\USB\\…\\0066", "mono", 11, t["dim"])
    d.add(check(x + w - 98, y + h - 27, 9, t["green"], 1.6))
    d.text(x + w - 20, y + h - 18, "SHA-256", "mono6", 11, t["green"], anchor="end", ls=1)


PPQL = [
    [("transactions", "cyan")],
    [("  | ", "accent_text"), ("where ", "text"), ("direction ", "text2"), ("= ", "muted"), ('"OUT" ', "green"),
     ("and ", "text"), ("amount_inr ", "text2"), ("> ", "muted"), ("5000", "amber")],
    [("  | ", "accent_text"), ("sort ", "text"), ("amount_inr desc ", "text2"), ("| ", "accent_text"),
     ("head ", "text"), ("50", "amber")],
]


def art_ppql(d: Svg, t, x, y, w, h):
    d.add(rect(x + .5, y + .5, w - 1, 84, 8, t["inset"], t["line2"]))
    d.text(x + w - 14, y + 22, "PPQL", "mono6", 10.5, t["dim"], anchor="end", ls=1.4)
    for i, segs in enumerate(PPQL):
        cx = x + 16
        for s, col in segs:
            cx += d.text(cx, y + 25 + i * 22, s, "mono6" if col == "cyan" else "mono", 12.5, t[col], pre=True)


def art_snapshots(d: Svg, t, x, y, w, h):
    sheets = [("VSC 1 · 2026-03-02 09:14", False), ("VSC 2 · 2026-06-14 22:01", False),
              ("VSC 3 · 2026-09-02 08:47", False), ("LIVE VOLUME · C:", True)]
    for k, (label, front) in enumerate(sheets):
        sx, sy = x + k * 10, y + k * 20
        sh = 46 if front else 40
        d.add(rect(sx + .5, sy + .5, 288, sh - 1, 7, t["card2"] if front else t["inset"],
                   t["accent"] if front else t["line"], 'stroke-opacity=".8"' if front else ""))
        d.text(sx + 12, sy + 14.5, label, "mono6" if front else "mono", 11, t["text"] if front else t["muted"], ls=.6)
        if front:
            d.text(sx + 12, sy + 34, "recover deleted & earlier versions", "mono", 10.5, t["dim"])
    rows = [("lock", "BITLOCKER", "unlocked · libbde"), ("disk", "IMAGES", "E01 · RAW · dd"),
            ("check", "EXPORT", "timestamps kept · hashed")]
    for i, (icon, k, v) in enumerate(rows):
        ry = y + 14 + i * 36
        gx = x + 344
        d.add(check(gx - 8, ry - 9, 12, t["green"]) if icon == "check" else glyph(icon, gx, ry - 3, t["text2"]))
        d.text(gx + 18, ry - 4, k, "mono6", 10.5, t["dim"], ls=1.2)
        d.text(gx + 18, ry + 12, v, "mono", 11.5, t["text2"])


def art_pipeline(d: Svg, t, x, y, w, h):
    nodes = [("ARTIFACTS", "YAML catalog"), ("CARGO BUILD", "config baked in"),
             ("COLLECTOR.EXE", "~2.5 MB · static"), ("S3 · UNC", "write-only")]
    nw, gap = 120, (w - 4 * 120) / 3
    for i, (a, b) in enumerate(nodes):
        nx = x + i * (nw + gap)
        last = i == len(nodes) - 1
        d.add(rect(nx + .5, y + .5, nw - 1, 55, 8, t["card2"] if last else t["inset"],
                   t["accent"] if last else t["line"], 'stroke-opacity=".8"' if last else ""))
        d.text(nx + 12, y + 23, a, "mono6", 11.5, t["text"], ls=.5)
        d.text(nx + 12, y + 41, b, "mono", 10.5, t["muted"])
        if not last:
            ax = nx + nw + 3
            d.add(line(ax, y + 28, ax + gap - 8, y + 28, t["dim"], 1.4),
                  f'<polygon points="{n(ax + gap - 9)},{n(y + 24)} {n(ax + gap - 3)},{n(y + 28)} {n(ax + gap - 9)},{n(y + 32)}" fill="{t["dim"]}"/>')
    d.add(glyph("lock", x + w - 18, y + 20, t["accent_text"]))
    d.text(x, y + 86, "RSA-OAEP-SHA256 wraps a per-run AES-256-GCM key", "mono", 11.5, t["muted"])
    d.text(x + w, y + 86, "GPO · SCCM · Intune", "mono6", 11.5, t["accent_text"], anchor="end")


def art_agents(d: Svg, t, x, y, w, h):
    agents = ["REGISTRY", "PREFETCH · AMCACHE · $MFT", "USN · LNK · MRU"]
    llm_x, llm_w, rep_x = x + 268, 128, x + w - 122
    mid = y + 47
    for i, a in enumerate(agents):
        ay = y + i * 34
        d.add(rect(x + .5, ay + .5, 214, 25, 6, t["inset"], t["line"]))
        d.text(x + 10, ay + 17, a, "mono", 11, t["text2"], ls=.3)
        d.add(f'<path d="M{n(x + 216)} {n(ay + 13)}C{n(x + 244)} {n(ay + 13)} {n(x + 240)} {n(mid)} {n(llm_x - 2)} {n(mid)}" '
              f'stroke="{t["dim"]}" stroke-width="1.3" fill="none"/>')
    for bx, bw, a, b, hot in ((llm_x, llm_w, "LOCAL LLM", "Ollama · offline", False),
                              (rep_x, 122, "CASE REPORT", "IOCs · timeline", True)):
        d.add(rect(bx + .5, mid - 27.5, bw - 1, 55, 8, t["card2"] if hot else t["inset"],
                   t["accent"] if hot else t["line"], 'stroke-opacity=".8"' if hot else ""))
        d.text(bx + 12, mid - 5, a, "mono6", 11.5, t["text"], ls=.5)
        d.text(bx + 12, mid + 13, b, "mono", 10.5, t["muted"])
    ax = llm_x + llm_w + 3
    d.add(line(ax, mid, rep_x - 6, mid, t["dim"], 1.4),
          f'<polygon points="{n(rep_x - 8)},{n(mid - 4)} {n(rep_x - 2)},{n(mid)} {n(rep_x - 8)},{n(mid + 4)}" fill="{t["dim"]}"/>')


ART = {"verdicts": art_verdicts, "devices": art_devices, "ppql": art_ppql, "snapshots": art_snapshots,
       "pipeline": art_pipeline, "agents": art_agents}


def exhibit(ex, t) -> str:
    stars = repo_stars(ex["repo"])
    desc = f"Exhibit {ex['letter']}: {ex['title']} ({ex['meta']}). {ex['desc']}"
    if ex.get("metrics"):
        desc += " " + ", ".join(f"{v} {k}" for v, k in ex["metrics"]) + "."
    if ex["size"] == "large":
        W, H = 1200, 328
        d = Svg(W, H, f"Exhibit {ex['letter']} — {ex['title']}", desc)
        card_frame(d, t, W, H)
        d.add(f'<g clip-path="url(#k)">{rect(0, 0, 76, H, 0, t["card2"])}</g>',
              line(76.5, 0, 76.5, H, t["line"], 1, 'stroke-dasharray="4 5"'),
              f'<circle cx="38" cy="34" r="8" fill="{t["page"]}" stroke="{t["line"]}"/>')
        d.text(38, 104, ex["letter"], "cond", 46, t["accent_text"], anchor="middle")
        d.text(42, 196, "EXHIBIT", "mono6", 11.5, t["muted"], anchor="middle", ls=3.2, extra='transform="rotate(-90 42 196)"')
        d.add(barcode(18, 254, 40, 46, ex["repo"], t["dim"]))
        cx = 108
        d.text(cx, 58, ex["meta"], "mono", 12.5, t["muted"], ls=.6)
        sw = stars_pill(d, t, 704, 40, stars)
        pushed = STATS.get("repos", {}).get(ex["repo"], {}).get("pushed")
        if pushed:
            d.text(704 - sw - 14, 58, f"PUSHED {nice_date(pushed)}", "mono", 11.5, t["dim"], anchor="end", ls=1)
        d.text(cx, 106, ex["title"], "sans6", 32, t["text"])
        for i, ln in enumerate(wrap(ex["desc"], "sans", 17, 590)):
            d.text(cx, 142 + i * 26, ln, "sans", 17, t["text2"])
        mx = cx
        for v, k in ex["metrics"]:
            wv = d.text(mx, 232, v, "cond", 36, t["text"])
            wk = d.text(mx, 256, k.upper(), "mono", 11, t["muted"], ls=1)
            mx += max(wv, wk) + 40
        flow(d, cx, 278, 600, ex["chips"], face="mono", size=12, h=26, padx=10, rx=6,
             fill=t["card2"], stroke=t["line"], color=t["text2"])
        ART[ex["art"]](d, t, 736, 24, 440, H - 48)
    else:
        W, H = 590, 360
        d = Svg(W, H, f"Exhibit {ex['letter']} — {ex['title']}", desc)
        card_frame(d, t, W, H)
        d.add(f'<g clip-path="url(#k)">{rect(0, 0, W, 52, 0, t["card2"])}</g>',
              line(0, 52.5, W, 52.5, t["line"], 1, 'stroke-dasharray="4 5"'),
              f'<circle cx="28" cy="26" r="7" fill="{t["page"]}" stroke="{t["line"]}"/>')
        x = 48 + d.text(48, 31, "EXHIBIT", "mono6", 11.5, t["muted"], ls=3) + 6
        d.text(x, 32, ex["letter"], "cond", 20, t["accent_text"])
        sw = stars_pill(d, t, 562, 13, stars)
        d.add(barcode(562 - sw - 96, 15, 82, 22, ex["repo"], t["dim"]))
        d.text(28, 98, ex["title"], "sans6", 26, t["text"])
        d.text(28, 124, ex["meta"], "mono", 12, t["muted"], ls=.5)
        for i, ln in enumerate(wrap(ex["desc"], "sans", 16, 534)[:3]):
            d.text(28, 158 + i * 24, ln, "sans", 16, t["text2"])
        ART[ex["art"]](d, t, 28, 226, 534, 110)
        if ex.get("chips"):
            flow(d, 28, 322, 534, ex["chips"], face="mono", size=11.5, h=24, padx=9, rx=6,
                 fill=t["card2"], stroke=t["line"], color=t["text2"])
    return d.render()


# ── field kit ────────────────────────────────────────────────────────────────

def kit(t) -> str:
    p = PROFILE
    W = 1200

    def draw(d: Svg) -> float:
        ends = []
        for x, heading, groups, face, size in ((36, "ARTIFACTS MY TOOLS PARSE", p["artifacts"], "mono", 13),
                                               (648, "FIELD TOOLCHAIN", p["toolchain"], "sans5", 14.5)):
            d.text(x, 92, heading, "mono6", 12, t["accent_text"], ls=2.4)
            y = 124
            for g in groups:
                d.text(x, y, g["group"].upper(), "mono", 11, t["dim"], ls=1.6)
                y = flow(d, x, y + 12, 516, g["items"], face=face, size=size, h=30, padx=11, rx=6,
                         fill=t["card2"], stroke=t["line2"], color=t["text2"]) + 30
            ends.append(y)
        return max(ends)

    H = round(draw(Svg(W, 0, "")) + 2)
    d = Svg(W, H, "Field kit",
            "Artifacts my tools parse: " + "; ".join(f"{g['group']}: {', '.join(g['items'])}" for g in p["artifacts"])
            + ". Toolchain: " + "; ".join(f"{g['group']}: {', '.join(g['items'])}" for g in p["toolchain"]) + ".")
    card_frame(d, t, W, H)
    form_bar(d, t, W, "FORM DF-03 · FIELD KIT", "ARTIFACTS · TOOLCHAIN")
    d.add(line(612.5, 76, 612.5, H - 28, t["line2"]))
    draw(d)
    return d.render()


# ── case log ─────────────────────────────────────────────────────────────────

def ledger(t) -> str:
    s = STATS
    W = 1200
    contrib = s.get("contributions")
    H = 316 if contrib else 222
    d = Svg(W, H, "Case log — live GitHub numbers",
            f"{s['user']['public_repos']} public repositories, {s['totals']['original_repos']} original, "
            f"{s['totals']['stars_original']} stars on original work, {s['user']['followers']} followers. "
            f"Most starred: {s['top_repo']['name']} ({s['top_repo']['stars']} stars). "
            f"Latest push: {s['latest_push']['name']} on {s['latest_push']['date']}."
            + (f" {contrib['total']} contributions in the last year." if contrib else ""))
    card_frame(d, t, W, H)
    form_bar(d, t, W, "LEDGER · GITHUB API", f"AS OF {nice_date(s['as_of'])}")
    tiles = [("PUBLIC REPOS", s["user"]["public_repos"], None), ("ORIGINAL WORK", s["totals"]["original_repos"], None),
             ("STARS EARNED", s["totals"]["stars_original"], None), ("FOLLOWERS", s["user"]["followers"], None),
             ("MOST STARRED", s["top_repo"]["stars"], s["top_repo"]["name"])]
    widths = [190, 190, 190, 190, 1128 - 4 * 190 - 4 * 16]
    x = 36
    for (label, value, sub), tw in zip(tiles, widths):
        d.add(rect(x + .5, 72.5, tw - 1, 91, 10, t["card2"], t["line2"]))
        d.text(x + 18, 98, label, "mono", 11, t["muted"], ls=1.4)
        vw = d.text(x + 18, 146, f"{value:,}", "cond", 40, t["text"])
        if sub:
            d.add(star(x + 18 + vw + 16, 132, 7, t["accent"]))
            d.text(x + 18 + vw + 30, 138, sub, "mono6", 12, t["accent_text"])
        x += tw + 16
    y = 196
    if contrib:
        weeks = contrib["weeks"]
        d.text(36, y, "CONTRIBUTIONS · LAST 52 WEEKS", "mono6", 11, t["dim"], ls=1.4)
        d.text(1164, y, f"{contrib['total']:,} in the last year", "mono", 11.5, t["muted"], anchor="end")
        top, base = max(weeks) or 1, y + 74
        step = 1128 / len(weeks)
        bars = []
        for i, v in enumerate(weeks):
            bh = 2 if v == 0 else 4 + 54 * v / top
            col, op = (t["line"], 1) if v == 0 else (t["accent"], .35 + .65 * v / top)
            bars.append(rect(36 + i * step + 2, base - bh, step - 6, bh, 2, col, extra=f'fill-opacity="{op:.2f}"'))
        d.add(*bars)
        y = base + 28
    else:
        y = 196
    lp = s["latest_push"]
    x = 36 + d.text(36, y, "LATEST PUSH", "mono", 11, t["dim"], ls=1.4) + 12
    x += d.text(x, y, lp["name"], "mono6", 12, t["text"]) + 8
    d.text(x, y, f"· {nice_date(lp['date'])}", "mono", 12, t["muted"])
    d.text(1164, y, "Refreshed daily by GitHub Actions", "mono", 11, t["dim"], anchor="end")
    return d.render()


def seal(t) -> str:
    W, H = 1200, 96
    digest = hashlib.sha256(PROFILE_PATH.read_bytes() + STATS_PATH.read_bytes()).hexdigest()
    d = Svg(W, H, "End of case file", f"Seal: SHA-256 of this profile's data files, {digest}.")
    label = "END OF CASE FILE"
    lw = F["cond"].width(label, 20, 5)
    d.text(W / 2 + 2.5, 40, label, "cond", 20, t["muted"], anchor="middle", ls=5)
    for x1, x2, dx in ((0, W / 2 - lw / 2 - 26, 0), (W / 2 + lw / 2 + 26, W, W)):
        d.add(line(x1, 33.5, x2, 33.5, t["line"]))
    d.add(diamond(W / 2 - lw / 2 - 18, 33.5, 4, t["accent"]), diamond(W / 2 + lw / 2 + 18, 33.5, 4, t["accent"]))
    d.text(W / 2, 72, f"SEAL SHA-256 {digest[:16]}…{digest[-8:]} · {PROFILE['case_ref']} · {nice_date(STATS['as_of'])}",
           "mono", 11.5, t["dim"], anchor="middle", ls=.8)
    return d.render()


# ── README blocks ────────────────────────────────────────────────────────────

def readme_blocks() -> dict[str, str]:
    login = PROFILE["login"]
    rows = ["| Exhibit | What it does | ★ |", "|:--|:--|--:|"]
    for m in PROFILE["more"]:
        rows.append(f"| [**{m['repo']}**](https://github.com/{login}/{m['repo']}) | {m['what']} | {repo_stars(m['repo'])} |")
    posts = STATS.get("posts") or PROFILE["blog"]["fallback"]
    notes = ["| Date | Field note |", "|:--|:--|"]
    for post in posts:
        title = post["title"].replace("|", "\\|")
        notes.append(f"| {post['date'] or '—'} | [{title}]({post['link']}) |")
    return {"LOCKER": "\n".join(rows), "NOTES": "\n".join(notes)}


def update_readme() -> None:
    text = README.read_text()
    for name, content in readme_blocks().items():
        text = re.sub(rf"(<!-- {name}:START -->).*?(<!-- {name}:END -->)",
                      lambda m: f"{m.group(1)}\n{content}\n{m.group(2)}", text, flags=re.S)
    README.write_text(text)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    out = {"hero.svg": hero()}
    for theme, t in THEMES.items():
        for link in PROFILE["links"]:
            out[f"link-{link['id']}-{theme}.svg"] = link_pill(link, t)
        for key in PROFILE["sections"]:
            out[f"section-{key}-{theme}.svg"] = section(key, t)
        out[f"dossier-{theme}.svg"] = dossier(t)
        for ex in PROFILE["exhibits"]:
            out[f"exhibit-{ex['repo'].lower()}-{theme}.svg"] = exhibit(ex, t)
        out[f"kit-{theme}.svg"] = kit(t)
        out[f"ledger-{theme}.svg"] = ledger(t)
        out[f"seal-{theme}.svg"] = seal(t)
    ASSETS.mkdir(exist_ok=True)
    for old in ASSETS.glob("*.svg"):
        if old.name not in out:
            old.unlink()
    total = 0
    for name, svg in out.items():
        (ASSETS / name).write_text(svg)
        total += len(svg)
    update_readme()
    print(f"rendered {len(out)} SVGs ({total / 1024:.0f} KiB) into {ASSETS.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
