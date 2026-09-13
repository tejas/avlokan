"""Bring the cover images and photographs from avlokan.org into the archive.

The old Webflow site carries a cover image for each text and a photograph of
Shri Devchand bhai. They are his own images; this copies them in rather than
linking to them, because a hotlinked image is a dependency on a subscription
staying paid, and the archive is meant to outlive that.

    python3 -m avlokan_site.fetch_images

Downloads to `avlokan/images/`, skipping anything already there, and writes
what it fetched into `avlokan/images/index.json`. Safe to re-run.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "avlokan" / "images"

CDN = "https://cdn.prod.website-files.com"
TEXTS = f"{CDN}/5f36a8cb89fc58f2b0390df2"
SITE = f"{CDN}/5f32b8d70172550865bb1c03"

# One cover per text, keyed by the canonical name used in the master index.
# The alt text on the source site says "Shrimad Rajchandra Vachanamrut Cover
# page" on every single image — a template left unedited — so these are
# matched by filename, not by what the page claims they are.
COVERS: dict[str, str] = {
    "Shrimad Rajchandra Vachanamrut": f"{TEXTS}/5f3d61099a6a08ddfad0f0f7_Shrimad%20Patrank.jpg",
    "Shri Samaysar":                  f"{TEXTS}/5f3d60ee2feb062173628aab_Samysar.jpg",
    "Shri Dravya Drushti Prakash":    f"{TEXTS}/5f3d60757dc587f72581a466_Dravyadrushti.jpg",
    "Benshri ke Vachanamrut":         f"{TEXTS}/5f3c24b2e40ad03546c595b2_Benshree.jpg",
    "Swanubhuti Darshan":             f"{TEXTS}/5f3d5fe980831edd978de2b8_Swanubhuti%20Darshan.jpg",
    "Apoorva Avasar":                 f"{TEXTS}/5f3d606408f888a4713c0281_Apoorva%20Avsar.jpg",
    "Moksh Marg Prakashak":           f"{TEXTS}/5f3d689856d469adb50f677b_Mokshmarg%20(1).jpg",
    "Solah Karan Bhavna":             f"{TEXTS}/5f3d69cfe10207545cc5cf98_Solahkaran.jpg",
    "Daslakshan Dharma":              f"{TEXTS}/5f3d5ec4fcd4a81bfcb6b0b5_Daslakshan%20dharma.jpg",
    "Gyangoshti":                     f"{TEXTS}/5f3d60fd789809ed06ed6063_Gyan%20Goshti.jpg",
    "Tatva Charcha":                  f"{TEXTS}/5f3d602afcd4a87652b6b498_Tatva%20charcha.jpg",
    "Tatkalmoksha":                   f"{TEXTS}/5f3d5ff79b28d35999565648_Tatkal%20Moksh.jpg",
    "Shri Parmagamsar":               f"{TEXTS}/5f3d608dba987f09636c32ac_Parmagamsar.jpg",
    "Shri Ratnakaranda Shravakachar": f"{TEXTS}/5f3d601f2845043de4081a3d_Ratnakaran%20Shravakachar.jpg",
}

# A blank shastra cover, used for the texts that have no cover of their own.
FALLBACK = f"{TEXTS}/5f3d5fcd1f33f746e6e70ded_Shashtraji%20Template%20(1).jpg"

# Three photographs of him on the site. The square one is a 200px avatar; the
# other two are the full pictures the home page uses.
PORTRAITS = {
    "devchand-bhai.jpg":          f"{SITE}/5f3c3a7906050ca553348150_IMG_0034.JPG",
    "devchand-bhai-seated.jpg":   f"{SITE}/5f3d1bb34a73147ad44cfc7e_guruji_mobile.jpg",
    "devchand-bhai-avatar.jpg":   f"{SITE}/5f37e23f3ec5c1c535e30eaf_Guruji.jpg",
}


def slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def fetch(url: str, target: Path) -> int:
    if target.exists() and target.stat().st_size > 0:
        return target.stat().st_size
    request = urllib.request.Request(url, headers={"User-Agent": "avlokan-archive"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    target.write_bytes(data)
    return len(data)


def main() -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    for text, url in COVERS.items():
        name = f"cover-{slugify(text)}.jpg"
        size = fetch(url, IMAGES / name)
        written[text] = name
        print(f"  {size // 1024:5} KB  {name}")

    size = fetch(FALLBACK, IMAGES / "cover-default.jpg")
    written["fallback cover"] = "cover-default.jpg"
    print(f"  {size // 1024:5} KB  cover-default.jpg")

    for name, url in PORTRAITS.items():
        size = fetch(url, IMAGES / name)
        written[Path(name).stem] = name
        print(f"  {size // 1024:5} KB  {name}")

    (IMAGES / "index.json").write_text(
        json.dumps({"source": "avlokan.org, the author's own site",
                    "covers": written}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    print(f"\n{len(written)} images in {IMAGES}")


if __name__ == "__main__":
    main()
