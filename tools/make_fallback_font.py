"""Build a metric-compatible stand-in for TH SarabunIT๙ / TH Sarabun New.

    python tools/make_fallback_font.py <Laksaman.ttf dir> <output dir>

The agency's forms use TH SarabunIT๙, whose glyphs are small for their point
size (Thai 'ก' is about 0.39 em wide). When that font is not installed,
LibreOffice falls back to Laksaman (TLWG), whose glyphs are about 1.5x wider,
so labels wrap and a one-page summons spills onto two pages.

This script shrinks Laksaman's glyphs (outlines, advances and mark positioning)
to 0.665 of their size - measured against a summons printed from Word with the
real font - and the line height to 0.8, and renames the family to
"Sarabun Compat". docker/65-th-sarabun-fallback.conf maps the TH Sarabun
family names to it. Installing the real fonts in fonts/ still takes precedence.

Laksaman is © TLWG under GPL-2 with the font exception; the derived font keeps
that license (renamed, as the license asks for modified versions).
"""
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem

SCALE = 0.665  # glyph size: TH Sarabun / Laksaman, measured against a summons printed from Word
VSCALE = 0.8  # line height: tuned so the form's red warning box and rows match the Word printout
FAMILY = "Sarabun Compat"
STYLES = {"Laksaman.ttf": "Regular", "Laksaman-Bold.ttf": "Bold", "Laksaman-Italic.ttf": "Italic",
          "Laksaman-BoldItalic.ttf": "Bold Italic"}


def build(src: Path, dst: Path, style: str):
    font = TTFont(str(src))
    upm = font["head"].unitsPerEm
    vertical = {
        "hhea": (font["hhea"].ascent, font["hhea"].descent, font["hhea"].lineGap),
        "os2": (font["OS/2"].sTypoAscender, font["OS/2"].sTypoDescender, font["OS/2"].sTypoLineGap,
                font["OS/2"].usWinAscent, font["OS/2"].usWinDescent),
    }
    scale_upem(font, round(upm * SCALE))  # scales outlines, advances, GPOS/kern...
    font["head"].unitsPerEm = upm  # ...then claim the old em: everything renders at SCALE
    # line height: shrink less than the glyphs (Thai needs room for vowels and tone marks)
    font["hhea"].ascent, font["hhea"].descent, font["hhea"].lineGap = (round(v * VSCALE) for v in vertical["hhea"])
    os2 = font["OS/2"]
    (os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap, os2.usWinAscent, os2.usWinDescent) = (
        round(v * VSCALE) for v in vertical["os2"])
    name = font["name"]
    full = f"{FAMILY} {style}" if style != "Regular" else FAMILY
    ps = f"{FAMILY.replace(' ', '')}-{style.replace(' ', '')}"
    for rec in list(name.names):
        if rec.nameID in (1, 16):
            rec.string = FAMILY
        elif rec.nameID in (2, 17):
            rec.string = style
        elif rec.nameID == 4:
            rec.string = full
        elif rec.nameID == 6:
            rec.string = ps
        elif rec.nameID == 3:
            rec.string = f"{ps};derived from Laksaman (TLWG)"
    font.save(str(dst))


def main(src_dir, out_dir):
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = 0
    for filename, style in STYLES.items():
        src = src_dir / filename
        if src.exists():
            build(src, out_dir / f"SarabunCompat-{style.replace(' ', '')}.ttf", style)
            made += 1
    if not made:
        raise SystemExit(f"no Laksaman fonts found in {src_dir}")
    print(f"wrote {made} fonts to {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/truetype/tlwg",
         sys.argv[2] if len(sys.argv) > 2 else "/usr/share/fonts/truetype/sarabun-compat")
