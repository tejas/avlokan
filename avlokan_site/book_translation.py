"""Fold English renderings into the book, and refuse the ones that are short.

    python3 -m avlokan_site.book_translation work/translations/*.json
    python3 -m avlokan_site.book_translation work/translations/*.json --write

The book is published with the Gujarati whole and the English whole beneath
it. That reads better than alternating paragraphs, but it hides the failure
this checks for: an English rendering that covers only part of what is above
it. Aphorism 103 has been on the site with ten Gujarati paragraphs and one
English one — fifteen per cent of the text — and nothing about the page said
so. A reader who cannot read the Gujarati has no way to tell.

So the arithmetic is done here instead. Gujarati is dense: a faithful English
rendering of it runs longer than the original, typically half again. Anything
that comes out shorter than the Gujarati is almost certainly a summary, and a
summary published as a translation is a worse failure than no translation,
because it is the kind that gets believed.

A rendering that fails is reported and not written. Pass --anyway to record it
regardless, and it is marked `partial` so the page can say so.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "avlokan" / "book.json"
GLOSSARY = ROOT / "avlokan" / "book_glossary.json"

# Below this, measured against the Gujarati it renders, a translation is not a
# translation. Set from the four that already existed: the two complete ones
# run at 1.57 and 1.68, the two partial ones at 0.61 and 0.15.
TOO_SHORT = 1.0


def paragraphs(text: str) -> list[str]:
    return [p for p in (text or "").split("\n\n") if p.strip()]


def terms_used(english: str, glossary: list[dict]) -> list[str]:
    """Which glossary terms this rendering leans on.

    Recorded so that correcting a term later is a question a machine can
    answer — which aphorisms have to be looked at again — rather than a
    hundred and fourteen files to read.
    """
    found = []
    for term in glossary:
        roman = term["roman"]
        if re.search(rf"\b{re.escape(roman)}\b", english, re.I):
            found.append(roman)
    return sorted(set(found))


def check(item: dict, english: str) -> tuple[bool, str]:
    gu, en = item["gujarati"], english
    ratio = len(en) / max(len(gu), 1)
    gp, ep = len(paragraphs(gu)), len(paragraphs(en))
    if ratio < TOO_SHORT:
        return False, (f"{ratio:.2f}x the Gujarati ({len(gu)} -> {len(en)} chars) — "
                       f"too short to be the whole of it")
    if gp > 1 and ep < gp - 1:
        return False, (f"{gp} Gujarati paragraphs, {ep} English — "
                       f"something is not rendered")
    return True, f"{ratio:.2f}x, {gp} paragraphs -> {ep}"


def main() -> None:
    ap = argparse.ArgumentParser(prog="book-translation")
    ap.add_argument("files", nargs="+", help="JSON of {n: english} batches")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--anyway", action="store_true",
                    help="record a rendering that failed the check, marked partial")
    args = ap.parse_args()

    book = json.loads(BOOK.read_text(encoding="utf-8"))
    glossary = json.loads(GLOSSARY.read_text(encoding="utf-8"))["terms"]
    by_n = {i["n"]: i for i in book}

    batch: dict[int, str] = {}
    for path in args.files:
        for n, english in json.loads(Path(path).read_text(encoding="utf-8")).items():
            batch[int(n)] = english

    good, bad = [], []
    for n, english in sorted(batch.items()):
        item = by_n.get(n)
        if item is None:
            bad.append((n, "no such aphorism"))
            continue
        ok, why = check(item, english)
        (good if ok else bad).append((n, why))
        if ok or args.anyway:
            item["english"] = english.strip()
            item["english_by"] = ("draft, unverified — see book_glossary.json"
                                  if ok else "draft, incomplete")
            item["english_terms"] = terms_used(english, glossary)
            item["partial"] = not ok

    for n, why in good:
        print(f"  {n:>4}  {why}")
    for n, why in bad:
        print(f"  {n:>4}  REFUSED: {why}")
    done = sum(1 for i in book if i.get("english"))
    print(f"\n{len(good)} accepted, {len(bad)} refused. "
          f"{done} of {len(book)} aphorisms now have English.")

    if not args.write:
        print("nothing written — re-run with --write to apply")
        return
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(BOOK, BOOK.with_suffix(f".backup-{stamp}.json"))
    BOOK.write_text(json.dumps(book, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"written. previous book kept at book.backup-{stamp}.json")


if __name__ == "__main__":
    main()
