"""Repair OCR damage in the audio catalogue, and put back what it hid.

The audio list was read off a scan, and the reader made the mistakes a reader
makes: `l` for `i`, `t` for `l`, `h` for `n`, and — the damaging one — Cyrillic
`А` and `М` for the Latin letters they are drawn identically to. Eight entries
carry their date inside the subject where the date column is empty, and
because `11-Аpr-1999` begins with a Cyrillic А, nothing recognised it as a
date. Those eight sittings are missing from the archive altogether.

    python3 -m avlokan_site.clean_audio            # report
    python3 -m avlokan_site.clean_audio --write    # apply

Rewrites `avlokan/audio_catalogue.json` and syncs the repaired entries into
`avlokan/master_index.json`, keeping a timestamped backup of both. Safe to
re-run: a second pass finds nothing left to do.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "avlokan" / "audio_catalogue.json"
INDEX = ROOT / "avlokan" / "master_index.json"

# Letters the scan read as their lookalikes from another alphabet. Only these
# are touched — the fields are romanised English, but blanket-stripping
# anything non-ASCII would eat real content.
CONFUSABLE = {
    "А": "A", "М": "M", "Т": "T", "Г": "r", "г": "r",
    "í": "i", "á": "a", "ş": "s", "»": "", "∞": "",
}

# Marks the scan put at the start of a field that are not part of the value.
# Asterisks are deliberately not among them: `***M.imp` is his own emphasis,
# and how many he wrote carries meaning.
LEADING_JUNK = "¡•·,; "

# Words it misread, corrected against the spelling the same word has
# everywhere else in the catalogue.
SPELLINGS = {
    "Soiah Karan": "Solah Karan",
    "Talva Charcha": "Tatva Charcha",
    "Tatva Chacha": "Tatva Charcha",
    "atva Charcna": "Tatva Charcha",
    "Adhyalma Ganga": "Adhyatma Ganga",
    "Dravyadrusti": "Dravyadrushti",
    "Kuipak": "Kulpak",
    "Aviokan": "Avlokan",
}

# Entries too mangled for a rule to reach, corrected by hand against their
# neighbours. #352 carries its own row number, a stray digit, and "Charcna"
# for "Charcha"; the two sittings either side of it are at Kulpak.
EXACT_SUBJECT = {
    352: "Tatva Charcha",
}

MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

FIELDS = ("subject", "place", "part", "remarks")


def unconfuse(text: str) -> str:
    for bad, good in CONFUSABLE.items():
        text = text.replace(bad, good)
    return unicodedata.normalize("NFC", text)


def respell(text: str) -> str:
    for bad, good in SPELLINGS.items():
        text = text.replace(bad, good)
    return text


def lift_date(entry: dict) -> str:
    """Move a date out of the subject and into the date column.

    Only when the date column is empty — a subject that repeats a date
    already recorded is left alone rather than second-guessed.
    """
    if entry.get("date"):
        return ""
    # A zero is allowed where a letter O belongs: #312 reads `13-0ct-1999`,
    # and that one character kept the sitting out of the archive entirely.
    # Scoped to the month of an otherwise date-shaped string, because the
    # reverse reading is everywhere in this catalogue — `Bol 10`, `Patrank
    # 108` — and a blanket substitution would rewrite the references.
    hit = re.match(r"\s*(\d{1,2})\s*-\s*([A-Za-z0]{3})[a-z]*\s*-\s*(\d{4})\s*",
                   entry.get("subject") or "")
    if not hit:
        return ""
    mon = MONTHS.get(hit.group(2).lower().replace("0", "o"))
    if not mon:
        return ""
    rest = (entry["subject"][hit.end():]).lstrip("iI|,. ")
    entry["subject"] = rest.strip()
    return f"{hit.group(3)}-{mon:02}-{int(hit.group(1)):02}"


def clean(entry: dict) -> list[str]:  # noqa: C901
    """Repair one entry, returning a note for each thing changed."""
    notes = []
    for field in FIELDS:
        was = entry.get(field) or ""
        now = respell(unconfuse(was)).lstrip(LEADING_JUNK).strip()
        # A row number that leaked in front of its own subject — but only
        # when the number *is* this row's number. Stripping any leading digits
        # ate the day out of "12 Sep-1999 Srimad Rajchandra 449".
        if field == "subject":
            fixed = EXACT_SUBJECT.get(entry.get("n"))
            if fixed:
                now = fixed
            own = str(entry.get("n") or "")
            if own and now.startswith(own):
                rest = now[len(own):]
                if not re.match(r"\s*[-/]?\s*\d", rest):
                    now = rest.lstrip("|1lI).,- ").strip()
            now = re.sub(r"\s{2,}", " ", now)
        if now != was:
            entry[field] = now
            notes.append(f"{field}: {was!r} -> {now!r}")
    found = lift_date(entry)
    if found:
        entry["date"] = found
        notes.append(f"date recovered from the subject: {found}")
    return notes


def sync(catalogue: list[dict], index: list[dict]) -> tuple[int, int]:
    """Carry the repairs into the master index, and add what was missing."""
    placed: dict[int, tuple[dict, dict]] = {}
    for entry in index:
        for audio in entry.get("audio") or []:
            if audio.get("n") is not None:
                placed[audio["n"]] = (entry, audio)
    by_date = {e["date"]: e for e in index}

    updated = added = 0
    for row in catalogue:
        n = row.get("n")
        if n is None:
            continue
        if n in placed:
            entry, audio = placed[n]
            for field in FIELDS:
                if (audio.get(field) or "") != (row.get(field) or ""):
                    audio[field] = row.get(field) or ""
                    updated += 1
            continue
        if not row.get("date"):
            continue
        target = by_date.get(row["date"])
        if target is None:
            target = {"date": row["date"], "published": [], "catalogued": [],
                      "processed": False, "audio": []}
            by_date[row["date"]] = target
            index.append(target)
        target.setdefault("audio", []).append(dict(row))
        added += 1
    index.sort(key=lambda e: e["date"])
    return updated, added


def main() -> None:
    ap = argparse.ArgumentParser(prog="clean-audio")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    changed = []
    for row in catalogue:
        notes = clean(row)
        if notes:
            changed.append((row["n"], notes))

    print(f"{len(changed)} of {len(catalogue)} entries repaired")
    for n, notes in changed:
        for note in notes:
            print(f"   #{n:<5} {note}")

    index = json.loads(INDEX.read_text(encoding="utf-8"))
    updated, added = sync(catalogue, index)
    print(f"\n{updated} fields updated in the master index")
    print(f"{added} entries added that were missing from it entirely")

    if not args.write:
        print("\nnothing written — re-run with --write to apply")
        return
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    for path, data in ((CATALOGUE, catalogue), (INDEX, index)):
        shutil.copy2(path, path.with_suffix(f".backup-{stamp}.json"))
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nwritten. previous files kept with the suffix .backup-{stamp}")


if __name__ == "__main__":
    main()
