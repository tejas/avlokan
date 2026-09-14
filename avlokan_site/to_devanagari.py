"""Put a transcript back into one script.

    python3 -m avlokan_site.to_devanagari --job 13Feb2002           # report
    python3 -m avlokan_site.to_devanagari --job 13Feb2002 --write   # apply

The transcription engine sometimes switches to Gujarati letters partway
through a Hindi sentence — 13 February 2002 runs for three minutes in Gujarati
script while saying Hindi words: `ચૈતન્ય કા સ્મરણ ઓર સંપદા હૈ`. That is a
script error, and it matters beyond looking odd: a reader searching for
`ચૈતન્ય` will not find `चैतन्य`, so those minutes are invisible to search.

Gujarati and Devanagari descend from the same script and Unicode lays them out
in the same order, 0x180 apart, so the two are interchangeable letter for
letter. Nothing is translated here and no word is changed — only the letters
it is spelled with.

**This is not always the right thing to do.** He taught in Gujarati at the
Chinchani yatra, and he read Shrimad Rajchandra's letters in the Gujarati they
were written in. Transliterating genuine Gujarati speech into Devanagari would
be a change to the record, not a repair of it. So this runs on a named job and
never over the archive at large, and it refuses a job where the Gujarati looks
like Gujarati rather than Hindi in disguise.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"

# The two blocks run in parallel: અ U+0A85 is अ U+0905, and so on down.
SHIFT = 0x0A85 - 0x0905
GUJARATI = re.compile(r"[઀-૿]")

# Words that only a Gujarati speaker says — the verb endings and pronouns that
# Hindi does not share. If a passage is full of these it is Gujarati, and
# rewriting it in Devanagari would be a translation of the record rather than
# a correction of its spelling.
GUJARATI_ONLY = (
    "નથી", "છે", "હતું", "હતા", "હવે", "કરવું", "તમે", "અમે", "એમ",
    "શું", "જોઈએ", "થાય", "પણ", "માટે", "એટલે", "કહ્યું", "આપણે",
)


def devanagari(text: str) -> str:
    """The same words, in Devanagari letters."""
    out = []
    for ch in text:
        if not GUJARATI.match(ch):
            out.append(ch)
            continue
        moved = chr(ord(ch) - SHIFT)
        # A Gujarati codepoint with no Devanagari counterpart is left as it
        # is rather than turned into an unassigned character.
        out.append(moved if unicodedata.category(moved) != "Cn" else ch)
    return unicodedata.normalize("NFC", "".join(out))


def gujarati_runs(text: str) -> list[str]:
    """The stretches actually written in Gujarati letters."""
    return [m.group(0) for m in
            re.finditer(r"[઀-૿][઀-૿\s,.।?!\-–—'\"()]*", text)
            if len(m.group(0).strip()) > 1]


def looks_gujarati(runs: list[str]) -> int:
    """How many of these stretches are the Gujarati language, not Hindi in
    Gujarati letters."""
    return sum(1 for run in runs if any(w in run for w in GUJARATI_ONLY))


def main() -> None:
    ap = argparse.ArgumentParser(prog="to-devanagari")
    ap.add_argument("--job", required=True,
                    help="the start of an outputs/ directory name")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="convert even where the text looks like real Gujarati")
    args = ap.parse_args()

    jobs = [p for p in sorted(OUTPUTS.glob("*/subtitles.vtt"))
            if p.parent.name.startswith(args.job)]
    if not jobs:
        raise SystemExit(f"no job in outputs/ begins with {args.job!r}")

    for vtt in jobs:
        text = vtt.read_text(encoding="utf-8")
        runs = gujarati_runs(text)
        if not runs:
            print(f"{vtt.parent.name}: already in one script")
            continue
        real = looks_gujarati(runs)
        chars = len(GUJARATI.findall(text))
        print(f"{vtt.parent.name}")
        print(f"   {chars} Gujarati letters in {len(runs)} stretch(es); "
              f"{real} of them read as the Gujarati language")
        for run in runs[:3]:
            print(f"     {run.strip()[:78]}")
            print(f"  -> {devanagari(run).strip()[:78]}")
        if real and not args.force:
            print("   left alone: this is Gujarati, not Hindi in Gujarati letters.")
            print("   Re-run with --force if it should be transliterated anyway.")
            continue
        if not args.write:
            print("   nothing written — re-run with --write to apply")
            continue
        vtt.write_text(devanagari(text), encoding="utf-8")
        print(f"   written: {chars} letters now Devanagari")


if __name__ == "__main__":
    main()
