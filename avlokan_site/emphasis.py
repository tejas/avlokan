"""His own emphasis marks, told apart from the places he taught in.

He kept the written list himself, and beside the sittings that mattered most
to him he wrote a mark: one to six asterisks, usually with a word — `Imp`,
`M.Imp` (most important), `V.Imp` (very important) — and sometimes a second
word saying what it was he wanted remembered. `***Imp Khas`. `**M.Imp
Bhavarth`. That mark is the only editorial judgement in the whole catalogue
that is unambiguously his, and how many asterisks he wrote carries meaning.

The mark went into whichever column was free on the row he was writing. In
1,229 rows it is in `place` 82 times and in `remarks` 121 times and in both
columns 0 times — he wrote it once per row, wherever there was room. So a
reader of `place` alone gets a third of them, and the site, which renders
`place` as the location, printed 68 discourse pages that read

    25 September 2002 · Gatha 4 · **M.Imp Bhavarth · 46 minutes

as though `**M.Imp Bhavarth` were a town. This module is what tells the two
apart, and it looks in both columns because he did not distinguish them.

    python3 -m avlokan_site.emphasis          # every mark in the catalogue,
                                              # with how it was read
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "avlokan" / "audio_catalogue.json"

# Debris the scan put in front of a mark: `l*Imp`, `/**Imp`, `(**M.Imp`,
# `|**|mp`, `"Imp`, `#Imp`. Only ever stripped when a mark follows it
# immediately, so `Lagni, Ruchi Purusharath` keeps its L and `- Bandh
# Adhikar` keeps its dash.
#
# These are *not* read as asterisks, even though `"` and `#` in front of
# `Imp` were almost certainly asterisks once. How many, nobody can say, and
# guessing would invent a strength he never wrote. They come through as a
# mark with no asterisk count, which is true.
_JUNK = r"[\\/|li¡•·,;:.\"'()\[\]#&-]"

# `Imp`, and the two intensifiers he used with it. The first letter of `imp`
# is written `i`, `l`, `|` or `1` depending on what the reader made of it,
# and `M.Imp` appears as `M.Imp`, `M.imp`, `M. imp` and `M.lmp`.
_WORD = r"(?:[mv]\s*\.?\s*)?[il|1]mp"

# A mark is a run of asterisks, or the word, or both. Anchored: he wrote the
# mark first and anything after it qualifies the mark, so `Bhavana` in the
# middle of a line is a subject and `*Imp` at the head of one is not.
_MARK = re.compile(rf"^\s*{_JUNK}{{0,3}}\s*(?:(\*+)\s*({_WORD})?|({_WORD}))\s*",
                   re.I)

# Once, on #829, he wrote it the other way round: `Kram Imp` — the sequence
# is what matters. One row in 1,229, and the anchored pattern above walks
# straight past it. Safe to allow only because the word is his and nothing
# else: in the whole catalogue `imp` never appears except as this mark.
_TRAILING = re.compile(rf"\s+(\**)\s*({_WORD})\s*$", re.I)

# Anything with a letter or a digit in it is something he wrote. A residue of
# punctuation — the `***` trailing `**M.Imp ***`, the `)` closing
# `**Avlokan-rahasya)` — is not.
_SUBSTANCE = re.compile(r"[0-9A-Za-zऀ-ॿ઀-૿]")

_STRENGTH = {"": 0, "Imp": 1, "V.Imp": 2, "M.Imp": 3}

_GLOSS = {"M.Imp": "most important", "V.Imp": "very important", "Imp": "important"}

_COUNTED = {1: "One asterisk", 2: "Two asterisks", 3: "Three asterisks",
            4: "Four asterisks", 5: "Five asterisks", 6: "Six asterisks"}


class Mark(NamedTuple):
    """One emphasis mark, as he wrote it and as it can be read."""

    stars: int    # how many asterisks — 0 when he wrote only the word
    word: str     # "Imp", "M.Imp", "V.Imp", or "" when he wrote only asterisks
    note: str     # what he wrote after the mark: "Khas", "Bhavarth", "Prayog"
    raw: str      # the column exactly as it stands, so nothing is lost

    @property
    def rank(self) -> tuple[int, int]:
        """How strongly he marked it. Asterisks first, the word only to break
        a tie: he wrote more asterisks *and* a stronger word as he went up,
        but the asterisks are the thing he was counting."""
        return (self.stars, _STRENGTH.get(self.word, 0))

    @property
    def gloss(self) -> str:
        """`most important` — the word in words. Empty when he wrote none."""
        return _GLOSS.get(self.word, "")

    @property
    def counted(self) -> str:
        """`Three asterisks` — for saying on the page how strong the mark is."""
        return _COUNTED.get(self.stars, f"{self.stars} asterisks" if self.stars
                            else "")


def _word(text: str) -> str:
    """`M. imp` -> `M.Imp`. The vowel is whatever the scan made of it."""
    letters = re.sub(r"[\s.]", "", text).lower()
    return {"m": "M.Imp", "v": "V.Imp"}.get(letters[:-3], "Imp")


def _note(text: str) -> str:
    text = text.strip().lstrip("-–—:,.… ").strip()
    if "(" not in text and "[" not in text:
        text = text.rstrip(")]}")
    return text.strip() if _SUBSTANCE.search(text) else ""


def read(value: str | None) -> Mark | None:
    """Read a column as an emphasis mark, or return None if it is not one."""
    raw = (value or "").strip()
    rest, stars, word, found = raw, 0, "", False
    # He sometimes wrote the mark twice on one row — `*Imp *Imp`, `**imp
    # **Imp`, `**M.Imp ***` — so keep taking marks off the front until what
    # is left is not one. The strength is the *longest* run he wrote, not the
    # total: writing `*Imp` twice is not the same as writing `**Imp` once,
    # and summing them would promote a sitting he never promoted.
    while True:
        hit = _MARK.match(rest)
        if not hit or hit.end() == 0:
            break
        found = True
        stars = max(stars, len(hit.group(1) or ""))
        written = hit.group(2) or hit.group(3) or ""
        if written and not word:
            word = _word(written)
        rest = rest[hit.end():]
    if not found:
        tail = _TRAILING.search(raw)
        if not tail:
            return None
        return Mark(len(tail.group(1)), _word(tail.group(2)),
                    _note(raw[:tail.start()]), raw)
    return Mark(stars, word, _note(rest), raw)


def place_names(rows: Iterable[dict]) -> set[str]:
    """The place names this catalogue actually uses, folded for comparison.

    Read out of the catalogue rather than listed here, because the point is
    not to know what a place is in general — it is to know what *he* recorded
    as one. A row whose whole `place` is a mark contributes nothing; every
    other non-empty `place` is, by his own use of the column, a place.

    Used only to rescue a location that shares its column with a mark. Thirty
    rows carry both, but in different columns — the mark in `remarks`, the
    place in `place` — which needs no untangling. No row as it stands puts
    both in `place`, so this currently changes nothing, and exists so that
    `*Imp Bangalore`, if it is ever typed, does not lose Bangalore.
    """
    seen = set()
    for row in rows:
        value = (row.get("place") or "").strip()
        if value and read(value) is None:
            seen.add(value.casefold())
    return seen


def split(value: str | None, places: Iterable[str] = ()) -> tuple[Mark | None, str]:
    """Separate an emphasis mark from a location sharing the same column.

    Returns the mark and whatever location is left. What follows a mark
    belongs to the mark — `Khas`, `Bhavarth`, `Prayog` say what it was about
    the sitting that he wanted remembered — unless it is a name he records
    elsewhere as a place, in which case it is a place and is returned as one.
    """
    mark = read(value)
    if mark is None:
        return None, (value or "").strip()
    if mark.note and mark.note.casefold() in set(places):
        return mark._replace(note=""), mark.note
    return mark, ""


def strongest(*marks: Mark | None) -> Mark | None:
    """The strongest of several readings of the same sitting."""
    found = [m for m in marks if m is not None]
    return max(found, key=lambda m: m.rank) if found else None


def main() -> None:
    rows = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    places = place_names(rows)
    marked = []
    for row in rows:
        mark, place = split(row.get("place"), places)
        mark = strongest(mark, read(row.get("remarks")))
        if mark:
            marked.append((row, mark, place))

    print(f"{len(marked)} of {len(rows)} sittings carry a mark he wrote\n")
    by_strength: dict[int, int] = {}
    for _, mark, _ in marked:
        by_strength[mark.stars] = by_strength.get(mark.stars, 0) + 1
    for stars in sorted(by_strength, reverse=True):
        label = _COUNTED.get(stars) or "No asterisk, the word alone"
        print(f"  {label:<28} {by_strength[stars]:>4}")

    print("\nevery distinct mark, and how it was read:")
    shown: dict[str, tuple[Mark, str]] = {}
    for _, mark, place in marked:
        shown.setdefault(mark.raw, (mark, place))
    for raw, (mark, place) in sorted(shown.items(),
                                     key=lambda kv: (-kv[1][0].rank[0], kv[0])):
        bits = [f"{mark.stars}*"]
        if mark.word:
            bits.append(mark.word)
        if mark.note:
            bits.append(f"note={mark.note!r}")
        if place:
            bits.append(f"place={place!r}")
        print(f"  {raw!r:<34} -> {'  '.join(bits)}")


if __name__ == "__main__":
    main()
