"""Merge the channel's full video list into the master index.

The Studio CSV export stops at 500 rows, which is why the index knew about
248 videos when the channel holds 851. This reads the whole list straight from
the channel instead:

    yt-dlp --flat-playlist --skip-download --ignore-errors \\
      --print "%(id)s\\t%(upload_date)s\\t%(duration)s\\t%(view_count)s\\t%(title)s" \\
      "https://www.youtube.com/channel/UCU7w39JdVVbVtbRMYLf8VZw/videos" \\
      > work/youtube-videos.tsv

    python3 -m avlokan_site.merge_youtube work/youtube-videos.tsv [--write]

Without --write it only reports. It is idempotent: a video already in the
index by id is left alone, so re-running after new uploads adds only the new
ones.

Nothing is guessed. A video whose title carries no date, or names no text we
recognise, is listed in the report and left out — the index is the spine of
the archive and a wrong date there is worse than a missing row.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from datetime import date as _date, datetime
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "avlokan" / "master_index.json"

# Canonical text names, matched against the title in order — the first hit
# wins, so the more specific patterns come first. "Dravya Drashti Jineshwar"
# is a different text from "Dravya Drushti Prakash" and must be tested before
# it; the spelling of both varies from one upload to the next.
CANON: list[tuple[str, list[str]]] = [
    # Order matters: the first pattern that matches wins, so the specific
    # names come before the general ones. "Benshri ke Vachanamrut" contains
    # "vachanamrut" and was being filed under Shrimad Rajchandra's Vachanamrut
    # — 97 sittings on the wrong text. Likewise "Uttam Tyag Dharma, Shri
    # Samaysar Gatha 12" is a Daslakshan Parva discourse, not a Samaysar one.
    ("Benshri ke Vachanamrut",        ["benshri", "ben shri", "benshree",
                                       "bahenshri", "gurudevshri ke vachanamrut",
                                       "gurudevshri ke vachnamrut"]),
    ("Daslakshan Dharma",             ["daslakshan", "dashlakshan", "uttam kshama",
                                       "uttam shauch", "uttam mardav", "uttam arjav",
                                       "uttam aarjav", "uttam satya", "uttam sanyam",
                                       "uttam sayyam", "uttam sayam", "uttam tap",
                                       "uttam tyag", "uttam akinchan",
                                       "uttam bhramacharya", "uttam brahmacharya"]),
    ("Shrimad Rajchandra Vachanamrut", ["vachanamrut", "vachnamrut", "patrank",
                                        "shrimad rajchandra"]),
    ("Shri Samaysar Kalash",           ["samaysar kalash", "samaysar kalsh"]),
    ("Natak Samaysar",                 ["natak samaysar"]),
    ("Shri Samaysar",                  ["samaysar", "samayasar"]),
    ("Dravya Drashti Jineshwar",       ["jineshwar", "jineshawar", "jinesvar"]),
    ("Shri Dravya Drushti Prakash",    ["dravya drishti", "dravya drushti",
                                        "dravya drashti", "dravyadrushti",
                                        "dravyadrishti", "dravyadrashti"]),
    ("Drashti ke Nidhan",              ["drashti ke nidhan", "drishti ke nidhan"]),
    ("Swanubhuti Darshan",             ["swanubhuti"]),
    ("Apoorva Avasar",                 ["apoorva avasar", "apurva avasar",
                                        "apoorva avsar", "apurva avsar"]),
    ("Moksh Marg Prakashak",           ["moksh marg prakashak", "mokshmarg prakashak"]),
    ("Solah Karan Bhavna",             ["solah karan", "solahkaran"]),
    ("Shri Parmagamsar",               ["parmagamsar", "paramagamsar"]),
    ("Shri Ratnakaranda Shravakachar", ["ratnakarand", "ratnakaranda", "ratnakaranad"]),
    ("Sahajanand Patrasudha",          ["patrasudha", "sahajanand"]),
    ("Shri Anubhav Prakash",           ["anubhav prakash"]),
    ("Prayojan Siddhi",                ["prayojan siddhi", "prayojan sidhi",
                                        "prayojansiddhi"]),
    ("Samadhi Tantra",                 ["samadhi tantra", "samadhitantra"]),
    ("Bhavnabodh",                     ["bhavnabodh", "bhavna bodh"]),
    ("Darshanmoha",                    ["darshanmoha", "darshan moha", "darshanmoh"]),
    ("Adhyatma Ganga",                 ["adhyatma ganga", "adhyatmaganga",
                                        "adhyatama ganga"]),
    ("Tatva Charcha",                  ["tatva charcha", "tattva charcha",
                                        "tatvacharcha"]),
    ("Gyangoshti",                     ["gyangoshti", "gyan goshti", "jnangoshti"]),
    ("Bhedgnan",                       ["bhedgnan", "bhedjnan", "bhed gyan"]),
    ("Tatkalmoksha",                   ["tatkalmoksh", "tatkal moksh"]),
    ("Guru Vinay",                     ["guru vinay"]),
    ("Bhakti",                         ["bhakti", "bhajan", "stuti", "stavan",
                                        "mere man baso"]),
]

# Words that rule a text out however well its own keys match. "Dravya Drashti
# Jineshwar" begins with a Dravya Drushti Prakash keyword and is a different
# text entirely; nothing but the word "jineshwar" tells them apart.
VETO: dict[str, tuple[str, ...]] = {
    "Shri Dravya Drushti Prakash": ("jineshwar", "jineshawar", "jinesvar"),
}


MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
# Month spellings that appear mistyped in the upload titles.
MONTHS.update({"seo": 9, "setp": 9, "sept": 9, "jly": 7, "augu": 8, "apri": 4,
               "marc": 3, "octo": 10, "nove": 11, "dece": 12, "janu": 1, "febr": 2})

FIRST_YEAR, LAST_YEAR = 1994, 2002


def title_key(title: str) -> str:
    """A title reduced to the letters and digits in it, so that the same
    upload recorded twice — once by the CSV export, once from the channel —
    matches despite differing punctuation and spacing."""
    return re.sub(r"[^a-z0-9]+", "", str(title).lower())


def classify(title: str) -> str | None:
    low = title.lower()
    for name, keys in CANON:
        if any(v in low for v in VETO.get(name, ())):
            continue
        if any(k in low for k in keys):
            return name
    return None


EXCLUDED = {title_key(t) for t in json.loads(
    (ROOT / "avlokan" / "not_pravachan.json").read_text(encoding="utf-8")
)["titles"]} if (ROOT / "avlokan" / "not_pravachan.json").exists() else set()

CORRECTIONS = json.loads((ROOT / "avlokan" / "date_corrections.json").read_text(
    encoding="utf-8")) if (ROOT / "avlokan" / "date_corrections.json").exists() else {}

# Uploads a better one has replaced, dropped from the archive on purpose.
# Kept as a list rather than forgotten for two reasons: the merge would
# otherwise find them on the channel, not recognise them, and add each one
# back as a sitting of its own; and an archive that quietly discards things
# should at least be able to say what it discarded and in favour of what.
RETIRED_FILE = ROOT / "avlokan" / "superseded_uploads.json"
RETIRED: dict = json.loads(RETIRED_FILE.read_text(encoding="utf-8")) \
    if RETIRED_FILE.exists() else {}

# What `classify` calls the devotional singing.
BHAKTI = "Bhakti"

# Where a recording goes when it has no date and never will. Not a date, so
# every pass that walks the index by date steps over it — which is the point:
# the archive is organised by the day he taught, and a bhajan has no such day.
# The older import parked its undated rows under `?pub<upload date>` keys, and
# those are left where they are; this is for the ones arriving now, which have
# no upload date either since the channel listing does not report one.
UNDATED = "?undated"


def title_date(title: str) -> tuple[str | None, str]:
    """The recording date from the title, or a reason it could not be read."""
    fix = CORRECTIONS.get(title.strip())
    if fix and fix.get("to"):
        return fix["to"], ""
    hit = re.search(
        r"(\d{1,2})\s*(?:st|nd|rd|th)?[\s.,-]*([A-Za-z]{3,4})[a-z]*\.?[\s.,-]*(\d{4})",
        title)
    if hit:
        day, month, year = hit.group(1), hit.group(2), hit.group(3)
    else:
        # A handful are written month-first: "Jan 25, 2002".
        hit = re.search(r"\b([A-Za-z]{3,4})[a-z]*\.?[\s.,-]*(\d{1,2})"
                        r"(?:st|nd|rd|th)?[\s.,-]*(\d{4})", title)
        if not hit:
            return None, "no date in the title"
        month, day, year = hit.group(1), hit.group(2), hit.group(3)
    mon = MONTHS.get(month.lower())
    if not mon:
        return None, f'unknown month "{month}"'
    day, year = int(day), int(year)
    try:
        datetime(year, mon, day)
    except ValueError:
        return None, f"impossible date {day}/{mon}/{year}"
    if not FIRST_YEAR <= year <= LAST_YEAR:
        # He died in 2002; a later year is a typo in the title, and which typo
        # is a judgement call, so it goes to avlokan/date_corrections.json.
        return None, f"year {year} is outside 1994–2002"
    return f"{year}-{mon:02}-{day:02}", ""


def reference(title: str) -> str:
    """The part of the title that says where in the text he was."""
    hit = re.search(r"\b(?:Patra|Patrank|Bol|Sloka|Gatha|Kalash|Adhikar)\b[\s.]*"
                    r"[\d,\s–—-]*\d", title, re.I)
    return re.sub(r"\s+", " ", hit.group(0)).strip(" ,-") if hit else ""


def upload_order(rows: list[dict[str, str]]) -> dict[str, int]:
    """Rank every video by how recently it was uploaded, 0 being newest.

    `work/youtube-uploaded.tsv` holds real upload dates if it has been
    fetched. Without it the channel listing itself is the answer: YouTube
    returns a channel's videos newest first, so a video's position in the
    list is its recency.
    """
    dates: dict[str, str] = {}
    path = ROOT / "work" / "youtube-uploaded.tsv"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\\t") if "\\t" in line else line.split("\t")
            if len(parts) == 2 and parts[1].isdigit():
                dates[parts[0]] = parts[1]
    # All or nothing. A partial file is worse than none: every id it is
    # missing would sort as the oldest video on the channel, and a duplicate
    # pair split across the two groups would then pick the wrong winner.
    # Fetching the dates is rate-limited by YouTube and often stops half way.
    if dates and len(dates) < len(rows):
        print(f"  note: {len(rows) - len(dates)} of {len(rows)} upload dates are "
              f"missing, so the channel's own newest-first order is used instead")
    if len(dates) >= len(rows):
        ranked = sorted(rows, key=lambda r: dates[r["id"]], reverse=True)
        return {r["id"]: i for i, r in enumerate(ranked)}
    return {r["id"]: i for i, r in enumerate(rows)}


def read_tsv(path: Path) -> list[dict[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # yt-dlp emits the separator literally when --print is given "\t".
        parts = line.split("\\t") if "\\t" in line else line.split("\t")
        if len(parts) >= 5 and re.fullmatch(r"[\w-]{11}", parts[0]):
            rows.append({"id": parts[0], "uploaded": parts[1], "duration": parts[2],
                         "views": parts[3], "title": "\t".join(parts[4:]).strip()})
    return rows


def closest(title: str, pubs: list[dict], taken: dict) -> int | None:
    scored = sorted(
        ((SequenceMatcher(None, title_key(title),
                          title_key(p.get("title", ""))).ratio(), i)
         for i, p in enumerate(pubs) if i not in taken and p.get("title")),
        reverse=True)
    if not scored or scored[0][0] < 0.75:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
        return None          # two rows equally close: refuse to guess
    return scored[0][1]


PLACEHOLDER = {"(other)", "(unclassified)", "Other discourses", "", None}


def playlist_text(title: str) -> str | None:
    """Which text a playlist is about.

    Matched on whichever text is named *earliest* in the playlist title rather
    than by the order of CANON, because these titles often mention a second
    text in passing: "Apoorva Avsar, Pravachan by Sri Devchand Bhai on Apoorva
    Avsar written by Shrimad Rajchandra" is an Apoorva Avasar playlist.

    Returns None for the playlists that are not about one text — the catch-all
    "Shri Devguru Pravachan", and thematic ones like "Prayog ki vidhi".
    """
    low = title.lower()
    best = None
    for name, keys in CANON:
        if any(v in low for v in VETO.get(name, ())):
            continue
        for key in keys:
            at = low.find(key)
            if at >= 0 and (best is None or at < best[0]):
                best = (at, name)
    return best[1] if best else None


def read_playlists(path: Path) -> dict[str, str]:
    """video id -> the text of the first single-text playlist holding it."""
    found: dict[str, str] = {}
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        text = playlist_text(parts[1])
        if not text:
            continue
        bits = parts[2].split("|", 2)
        if len(bits) >= 3:
            found.setdefault(bits[1], text)
    return found


def read_series(path: Path) -> dict[str, tuple[str, str]]:
    """video id -> (playlist id, playlist title) for the tightest series it is in.

    A sitting can be in several playlists — the four Daslakshan discourses on
    Samaysar Gatha 12 are in both the Daslakshan and the Samaysar playlist —
    so the smallest one wins, being the most specific run of sittings.

    Playlist *order* is not used. YouTube appends a video to the end when you
    add it, so Solahkaran Bhavana part 24, added after the rest, sits at
    position 42. The part number in the title is what orders a series.
    """
    members: dict[str, list[str]] = defaultdict(list)
    titles: dict[str, str] = {}
    if not path.exists():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not playlist_text(parts[1]):
            continue
        bits = parts[2].split("|", 2)
        if len(bits) >= 3:
            members[parts[0]].append(bits[1])
            titles[parts[0]] = parts[1]
    best: dict[str, tuple[str, str]] = {}
    for pid, ids in sorted(members.items(), key=lambda kv: len(kv[1])):
        for vid in ids:
            best.setdefault(vid, (pid, titles[pid]))
    return best


def attach_series(index: list[dict], lookup: dict[str, tuple[str, str]]) -> int:
    """Record which of his playlists a sitting belongs to, as its series."""
    n = 0
    for entry in index:
        for cat in entry.get("catalogued") or []:
            found = lookup.get(cat.get("youtube_id") or "")
            if not found:
                continue
            if cat.get("series_id") != found[0]:
                n += 1
            cat["series_id"], cat["series"] = found
    return n


def apply_playlists(index: list[dict], lookup: dict[str, str]) -> list[tuple]:
    """Let his own playlists say which text a sitting belongs to.

    They are curation rather than inference, so they outrank both the
    spreadsheet column and anything read out of a title. The titles alone got
    99 Benshri ke Vachanamrut sittings filed under Shrimad Rajchandra's
    Vachanamrut, because "Benshri ke Vachanamrut" contains "vachanamrut".
    """
    changed = []
    for entry in index:
        cats = entry.get("catalogued") or []
        pubs = entry.get("published") or []
        for i, cat in enumerate(cats):
            text = lookup.get(cat.get("youtube_id") or "")
            if not text:
                continue
            was = cat.get("scripture") or ""
            if was == text:
                continue
            cat["scripture"] = text
            cat["text_source"] = "playlist"
            if i < len(pubs):
                pubs[i]["scripture"] = text
                pubs[i]["text_source"] = "playlist"
                changed.append((entry["date"], was, text, pubs[i].get("title", "")))
            else:
                changed.append((entry["date"], was, text, ""))
    return changed


def relocate(index: list[dict]) -> list[tuple]:
    """Move a sitting whose published date is wrong at the source.

    Usually the video title is the better authority for when a sitting
    happened, because it is what he typed when publishing. It is not always
    right: Solahkaran Bhavana part 32 is titled 23 September and was taught on
    the 20th, which the audio list has correctly. Where that is known, it is
    written down in `avlokan/date_corrections.json` with the reasoning, rather
    than inferred here.

    Corrections carry a video id as well as a title, so one still applies
    after the title has been fixed on YouTube.
    """
    by_id = {fix["youtube_id"]: fix for fix in CORRECTIONS.values()
             if isinstance(fix, dict) and fix.get("youtube_id")}
    by_date = {e["date"]: e for e in index}
    moved = []
    for entry in list(index):
        pubs = entry.get("published") or []
        cats = entry.get("catalogued") or []
        for i in range(len(pubs) - 1, -1, -1):
            vid = cats[i].get("youtube_id") if i < len(cats) else ""
            fix = by_id.get(vid or "") or CORRECTIONS.get((pubs[i].get("title") or "").strip())
            target = (fix or {}).get("to")
            if not target or target == entry["date"]:
                continue
            if target not in by_date:
                by_date[target] = {"date": target, "published": [], "catalogued": [],
                                   "processed": False, "audio": []}
                index.append(by_date[target])
            pub = pubs.pop(i)
            cat = cats.pop(i) if i < len(cats) else {}
            by_date[target].setdefault("published", []).append(pub)
            by_date[target].setdefault("catalogued", []).append(cat)
            moved.append((entry["date"], target, pub.get("title", "")))
    return moved


def reclassify(index: list[dict]) -> list[tuple]:
    """Name the text for rows the CSV import could not place.

    The export left 38 videos filed as `(other)`, and the spreadsheet column
    is wrong on others outright. It matters more than tidiness: the page
    for a sitting is grouped by text, so a video filed as `(other)` lands on
    the Other discourses page while the audio entry for the same sitting —
    which does name the text — becomes a second, video-less sitting on the
    right page. Solahkaran Bhavana Part 1, 5 August 1999, went missing that
    way.

    The catalogued row is moved with its published row, since the two are
    paired by position.
    """
    renamed = []
    for entry in index:
        pubs = entry.get("published") or []
        cats = entry.get("catalogued") or []
        for i, pub in enumerate(pubs):
            # His own playlist already settled this one; re-deriving it from
            # the title would undo that, and the next run would redo it.
            if pub.get("text_source") == "playlist" or not pub.get("title"):
                continue
            name = classify(pub["title"])
            if not name or name == pub.get("scripture"):
                continue
            pub["scripture"] = name
            if i < len(cats):
                cats[i]["scripture"] = name
                if not cats[i].get("reference"):
                    cats[i]["reference"] = reference(pub["title"])
            renamed.append((entry["date"], name, pub["title"]))
    return renamed


# How far apart the two catalogues may place the same sitting before we stop
# believing they are the same sitting. The real disagreements are one to three
# days; beyond a week, a matching part number means nothing, because "Part 3"
# recurs in every letter he ever taught.
AUDIO_WINDOW_DAYS = 7

# Answers about which text an audio row belongs to that were reached by
# looking at the day as a whole. Re-deriving them from the row's own subject
# would undo the very thing they were made to fix.
SETTLED = ("paired with the day's video", "matched by reference number")


def as_date(iso: str) -> _date | None:
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return _date(y, m, d)
    except ValueError:
        return None


def series_of(title: str) -> str:
    """Which letter, gatha or bol a video title names, as a bare number.

    Needed because the part number alone does not identify a sitting: every
    letter he taught has its own Part 2. Patrank 548 Part 2 and Patrank 550
    Part 2 are a week apart and quite different sittings.
    """
    hit = re.search(r"\b(?:Patrank|Patra|Gatha|Bol|Sloka|Adhikar|Q)\b\.?\s*(\d{1,4})",
                    title, re.I)
    return hit.group(1) if hit else ""


def audio_series(entry: dict) -> str:
    """The same number as `series_of`, read off an audio-list subject like
    `Srimad Rajchandra 548` or `Dravyadrushti Prakash 22`."""
    hit = re.search(r"(\d{1,4})", str(entry.get("subject") or ""))
    return hit.group(1) if hit else ""


def title_part(title: str) -> int | None:
    """`Part 2`, and the `Par 2` that one title was typed with."""
    hit = re.search(r"\bpar(?:t)?\s*(\d{1,2})\b", title, re.I)
    return int(hit.group(1)) if hit else None


def reclassify_audio(index: list[dict]) -> list[tuple]:
    """Name the text for an audio entry from what its own subject says.

    The audio list carries a text name assigned when it was first imported,
    and it disagrees with the subject beside it 103 times — most of the
    "Dravyadrushti Jineshwar" entries are filed under Dravya Drushti Prakash,
    which is a different text. It matters because `sittings()` groups by text
    before pairing: an audio entry filed under the wrong one cannot pair with
    the video of the same sitting, and becomes a second, video-less sitting.

    Subjects that name no text keep whatever they had.
    """
    changed = []
    for entry in index:
        for audio in entry.get("audio") or []:
            # A row already matched to the day's video keeps that answer.
            # Re-deriving it from the subject here would undo the pairing,
            # which would then redo it, and the merge would never settle.
            if audio.get("text_source") in SETTLED:
                continue
            text = classify(audio.get("subject") or "")
            if text and text != audio.get("scripture"):
                changed.append((entry["date"], audio.get("scripture") or "",
                                text, audio.get("subject") or ""))
                audio["scripture"] = text
    return changed


def pair_same_day(index: list[dict]) -> list[tuple]:
    """Pair a day's leftover audio entry with its leftover video.

    After every other correction there are still days holding one video with
    no audio entry and one audio entry with no video, filed under different
    texts — the audio list and the video title simply disagree about what he
    was teaching. On a single day that is one sitting, not two.

    Only surplus rows move, and only into a genuine gap: a text with more
    audio entries than videos gives one up to a text with more videos than
    audio entries. A text whose counts already balance is left alone.
    """
    paired = []
    for entry in index:
        pubs = [p for p in entry.get("published") or [] if not p.get("superseded_by")]
        auds = entry.get("audio") or []
        if not pubs or not auds:
            continue
        want: list[str] = []          # texts short of an audio entry
        spare: list[dict] = []        # audio entries with no video to sit with
        counts: dict[str, int] = {}
        for pub in pubs:
            counts[pub.get("scripture") or ""] = counts.get(pub.get("scripture") or "", 0) + 1
        seen: dict[str, int] = {}
        for audio in auds:
            text = audio.get("scripture") or ""
            seen[text] = seen.get(text, 0) + 1
            if seen[text] > counts.get(text, 0):
                spare.append(audio)
        for text, n in counts.items():
            for _ in range(n - seen.get(text, 0)):
                want.append(text)
        for audio, text in zip(spare, want):
            paired.append((entry["date"], audio.get("scripture") or "", text,
                           audio.get("subject") or ""))
            audio["scripture"] = text
            audio["text_source"] = "paired with the day's video"
    return paired


# A day with more sittings than this is not brute-forced. Six is already 720
# arrangements, and he never taught seven times in a day.
MAX_SITTINGS_A_DAY = 6

_DATE_TAIL = re.compile(r",?\s*\d{1,2}\s*[A-Za-z]{3,9}\.?\s*\d{2,4}\s*$")
_PART_WORDS = re.compile(r"\b(?:part|par)\s*\d{1,2}\b", re.I)


def cited_number(text: str) -> str:
    """The letter, gatha or bol a title or subject names, as a bare number.

    Unlike `series_of` this does not require a keyword in front of the number.
    The audio list writes `Dravyadrushti Prakash 405` and the video of the
    same sitting is titled `Drashti ke Nidhan 405`; neither says "Patrank",
    and the number is the only thing they agree on.

    The trailing date and any "Part 3" come off first. Without that,
    `Mokshmarg Prakashak, Adhikar 4, Part 3, 23 May 2000` would answer 4 only
    by luck, and a title that led with its date would answer 23 — every
    recording would appear to cite its own upload day.
    """
    plain = _PART_WORDS.sub("", _DATE_TAIL.sub("", str(text or "")))
    hit = re.search(r"(\d{1,4})", plain)
    return hit.group(1) if hit else ""


def _as_built(pubs: list[dict], auds: list[dict]) -> list[tuple]:
    """The pairing the site will make from this day: grouped by text, then
    zipped in list order. Mirrors `build.sittings`, which is what we are
    checking against."""
    rows: dict[str, list] = defaultdict(list)
    for audio in auds:
        rows[audio.get("scripture") or ""].append(audio)
    pairs = []
    for pub in pubs:
        here = rows[pub.get("scripture") or ""]
        pairs.append((here.pop(0) if here else None, pub))
    return pairs


def _agreement(pairs: list[tuple]) -> int:
    """How many of these pairs cite the same number on both sides."""
    total = 0
    for audio, pub in pairs:
        if not audio:
            continue
        n = cited_number(audio.get("subject"))
        if n and n == cited_number(pub.get("title")):
            total += 1
    return total


def align_by_number(index: list[dict]) -> tuple[list, list, list]:
    """Re-file a day's audio rows when the reference numbers say they crossed.

    Everything before this decides which text an audio row belongs to by
    reading its subject, and where that fails, by handing surplus rows to
    whichever video is short. Both are blind to the one thing the two
    catalogues reliably agree on: the number of the letter he was teaching.

    On 14 January 2000 he taught three times. The audio list and the videos
    name the same three sittings in different orders, so pairing them by
    position put the Dravya Drushti Prakash 17 row under the Drashti ke Nidhan
    405 video — Tejas found it on the page. On 23 May 2000 two sittings are
    simply swapped.

    So: try every arrangement of the day and keep the one where the most
    numbers agree, but only when it beats the current pairing outright and
    only when it is the single best answer. 19 May 2000 has an audio row
    reading `Srimad Rajchandra 172 Samaysar Sloka 19`, which cites both of
    that day's videos; two arrangements score equally and the day is reported
    rather than guessed at.
    """
    fixed, resorted, ambiguous = [], [], []
    for entry in index:
        pubs = [p for p in entry.get("published") or [] if not p.get("superseded_by")]
        auds = list(entry.get("audio") or [])
        if not 2 <= len(pubs) <= MAX_SITTINGS_A_DAY:
            continue
        if not 2 <= len(auds) <= MAX_SITTINGS_A_DAY:
            continue

        now = _as_built(pubs, auds)
        # Keyed by which rows are paired, not by the order the permutation
        # happened to produce them in: when there are more audio rows than
        # videos, many permutations describe the same pairing.
        arrangements: dict[tuple, list] = {}
        for perm in permutations(range(len(auds)), min(len(auds), len(pubs))):
            cand = [(auds[j], pubs[i]) for i, j in enumerate(perm)]
            arrangements.setdefault(
                tuple(sorted((id(a), id(p)) for a, p in cand)), cand)
        scored = {k: _agreement(v) for k, v in arrangements.items()}
        best_score = max(scored.values())
        if best_score <= _agreement(now):
            continue
        winners = [k for k, v in scored.items() if v == best_score]
        if len(winners) > 1:
            ambiguous.append((entry["date"], _agreement(now), best_score, len(winners)))
            continue

        best = arrangements[winners[0]]
        where = {id(pub): i for i, pub in enumerate(pubs)}
        rank: dict[int, int] = {}
        for audio, pub in best:
            rank[id(audio)] = where[id(pub)]
            was, now_text = audio.get("scripture") or "", pub.get("scripture") or ""
            if was == now_text:
                continue
            fixed.append((entry["date"], was, now_text,
                          audio.get("subject") or "", pub.get("title") or ""))
            audio["scripture"] = now_text
            audio["text_source"] = "matched by reference number"
        # Order matters as much as the label: two sittings from one text are
        # paired off by position, so the rows have to end up in the same order
        # as the videos they were matched to. On 8 April 2000 this is the
        # whole repair — three Adhyatma Ganga bols, correctly filed, listed in
        # an order the videos do not share.
        entry["audio"] = sorted(
            auds, key=lambda a: rank.get(id(a), len(pubs) + auds.index(a)))
        if entry["audio"] != auds:
            resorted.append((entry["date"],
                             [a.get("subject") or "" for a in entry["audio"]]))
    return fixed, resorted, ambiguous


def fold_audio(index: list[dict]) -> list[tuple]:
    """Put an audio-list entry on the same day as the video of that sitting.

    The audio catalogue and the video titles sometimes disagree about the
    date. Solahkaran Bhavana part 35 is 29 September in the audio list and 28
    September on the video; part 40 is 6 October against 5 October. Each
    disagreement invented a second, video-less sitting, which is why that
    series showed 46 sittings when he taught 43.

    Within a text, the part number is the reliable identity and the date is
    not, so the audio row moves to the video's date. Only inside a week — the
    same part number a year apart is a different letter, not a disagreement.
    """
    videos: dict[tuple, list[str]] = defaultdict(list)
    for entry in index:
        # Rows parked under a `?pub…` key have no real date to compare against.
        if not as_date(entry["date"]):
            continue
        for pub in entry.get("published") or []:
            if pub.get("superseded_by"):
                continue
            title = pub.get("title") or ""
            part = title_part(title)
            text = pub.get("scripture") or classify(title)
            if part and text:
                videos[(text, series_of(title), part)].append(entry["date"])

    by_date = {e["date"]: e for e in index}
    moved = []
    for entry in list(index):
        here = as_date(entry["date"])
        if not here:
            continue
        for audio in list(entry.get("audio") or []):
            part = re.fullmatch(r"\s*(\d{1,2})\s*", str(audio.get("part") or ""))
            text = audio.get("scripture")
            if not part or not text:
                continue
            dates = videos.get((text, audio_series(audio), int(part.group(1)))) or []
            if not dates or entry["date"] in dates:
                continue
            near = min(dates, key=lambda d: abs((as_date(d) - here).days))
            if abs((as_date(near) - here).days) > AUDIO_WINDOW_DAYS:
                continue
            entry["audio"].remove(audio)
            audio = dict(audio, moved_from=entry["date"])
            by_date[near].setdefault("audio", []).append(audio)
            moved.append((entry["date"], near, text, int(part.group(1))))
    return moved


# A reference stated in words, which is what tells a re-upload marker from a
# real number: `Dravya Drashti Prakash–1, Patra 22` says which patra it is, so
# the `–1` is not one.
NAMES_ITS_REFERENCE = re.compile(
    r"\b(?:Patra|Patrank|Gatha|Bol|Sloka|Adhikar|Q)\b\.?\s*\d{1,4}", re.I)

# `Prakash–1,` — a dash and a number hanging off the end of the text's name.
# The letter before the dash is what keeps `Bol 21–24,` out of this: that is a
# range of bols, and dropping the 24 would lose one of them.
VARIANT_MARKER = re.compile(r"(?<=[A-Za-z])\s*[-–—]\s*\d{1,2}\s*(?=,)")


def without_variant_marker(title: str) -> str:
    """Drop the `–1` the channel hangs off a text's name on a re-upload.

    Five Patra 22 sittings were published in 2020 as whole recordings and
    again in 2025, edited, under `Dravya Drashti Prakash–1, Patra 22, …`. The
    `–1` distinguishes the upload, not the letter — but it read as a reference
    number, so the two uploads signed differently, nothing was recognised as a
    re-upload, and every one of those dates carried two sittings where he
    taught once.

    Only stripped from a title that names its reference in words, which is
    what keeps `Sri Dravya Drushti Prakash - 28, Part 1` intact: there the 28
    *is* the patra, and it is the only thing saying so.
    """
    return VARIANT_MARKER.sub("", title) if NAMES_ITS_REFERENCE.search(title) else title


# The ten dharmas of the Daslakshan festival, in the order they are observed,
# each with the spellings the channel actually uses for it.
#
# The festival runs ten days and each dharma is the subject of exactly one of
# them, so two uploads naming the same dharma on the same date are one
# sitting however differently they are titled. That is what earns this a
# table: seven of the ten days of September 2002 were uploaded twice, once as
# `Uttam Tapa Dharma, उत्तम तप धर्म` and once as `Uttam Tap Dharma, Shri
# Samaysar Gatha 12`, and nothing else in the two titles agrees — `signature`
# reads the first as Daslakshan Dharma and the second as Shri Samaysar, so
# they sign differently and the archive carried both.
#
# Length cannot stand in for this, which was the first thing tried. These
# were cassette recordings, so distinct sittings share a length: Patrank 609
# Part 3 and Part 4 of 10 August 1998 are five seconds apart and are two
# different discourses. Anything keyed on duration would have merged them.
DASLAKSHAN = (
    ("kshama",       r"kshama|क्षमा"),
    ("mardhav",      r"mardhav|mardav|मार्दव"),
    ("arjav",        r"arjav|आर्जव"),
    ("satya",        r"sat+ya|सत्य"),
    ("shauch",       r"sh[ao]uch\w*|शौच"),
    ("sanyam",       r"say+am|sanyam|संयम"),
    ("tap",          r"\btapa?\b|तप"),
    ("tyag",         r"tyag|त्याग"),
    ("aakinchanya",  r"a+kinchan\w*|आकिंचन्य"),
    ("brahmacharya", r"bh?ra[mh]+acharya|ब्रह्मचर्य"),
)

# What marks a title as belonging to the festival at all, so that a stray
# "tap" in an ordinary title cannot be read as Uttam Tap Dharma. Spelled
# `Utttam`, with three t's, on 14 September 2002.
FESTIVAL = re.compile(r"ut+am|उत्तम|daslakshan|दसलक्षण", re.I)


def festival_subject(title: str) -> str:
    """Which day of the Daslakshan festival a title names, if any.

    A title naming two of the dharmas is answered with nothing rather than
    with the first of them: that is a day this table does not understand, and
    guessing at it would merge two sittings on the strength of a coincidence.
    """
    if not FESTIVAL.search(title):
        return ""
    named = [name for name, pat in DASLAKSHAN if re.search(pat, title, re.I)]
    return named[0] if len(named) == 1 else ""


def split_conflicting_parts(groups: dict, pubs: list[dict]) -> None:
    """Undo a festival grouping that folded two parts of one day together.

    Two uploads naming one dharma are one sitting, unless the day was given
    in two parts and each names it. Where the part numbers disagree the part
    is the truth and the group is split back apart.

    A part number on only one of them is not a disagreement. On 12 September
    2002 the `part 2` of `Shri Samaysar, Gatha 12, part 2` counts the reading
    of the gatha rather than the half of a day, and the re-upload leaves it
    out entirely — requiring the two to agree would keep that duplicate and
    the 16 September one as well.
    """
    for key in [k for k in groups if k and k[0] == "daslakshan"]:
        by_part: dict[str, list[int]] = defaultdict(list)
        for i in groups[key]:
            said = re.search(r"\bpart\s*(\d{1,2})\b", pubs[i].get("title", ""), re.I)
            by_part[said.group(1) if said else ""].append(i)
        if len([p for p in by_part if p]) < 2:
            continue
        del groups[key]
        for part, members in by_part.items():
            groups[(*key, part)].extend(members)


def signature(title: str) -> tuple:
    """What sitting a title names, ignoring how it was worded.

    `Srimad Rajchandra Patrank 751, part 1, 12 Jul 2002` and `Patrank 751,
    Shrimad Rajchandra Vachanamrut, Part 1, 12 Jul 2002` are the same sitting
    uploaded twice. The text, the reference numbers, the part and the segment
    identify it; the date is stripped first so it cannot masquerade as a
    reference number. Segments are kept distinct — a sitting split into
    `segment 1` and `segment 2` is two videos, not a duplicate.
    """
    title = without_variant_marker(title)
    part = re.search(r"\bpart\s*(\d{1,2})\b", title, re.I)
    seg = re.search(r"\bsegment\s*(\d{1,2})\b", title, re.I)
    body = re.sub(r"\b\d{1,2}\s*[a-z]{3,}\w*\.?,?\s*\d{4}\b", "", title, flags=re.I)
    body = re.sub(r"\b(19|20)\d{2}\b", "", body)
    # The part and segment numbers are captured separately; leaving them in
    # the reference numbers would make "Part 16" and "Part 16 (segment 1)"
    # look like different places in the text.
    body = re.sub(r"\b(?:part|segment)\s*\d{1,2}\b", "", body, flags=re.I)
    nums = tuple(sorted(set(re.findall(r"\d+", body))))
    which = (part.group(1) if part else "", seg.group(1) if seg else "")
    if not nums and not any(which):
        # Nothing numeric to tell one from another. Devotional songs are the
        # case that matters: "Bhakti – Dhayna re Divas" and "Bhakti – Guru
        # Charankamal Balihari" are different bhajans, and keying on the text
        # alone would call them the same sitting. Fall back to the wording,
        # which still folds two uploads of one identically-titled sitting.
        return (classify(title), title_key(body), *which)
    return (classify(title), nums, *which)


def group_key(title: str) -> tuple:
    """How a title is filed when looking for two uploads of one sitting.

    `signature` reads the wording, which is the right answer everywhere the
    two uploads of a sitting were worded from the same habit. A festival day
    is the exception: the dharma it teaches identifies it outright, and the
    two uploads of it agree about nothing else.
    """
    day = festival_subject(title)
    return ("daslakshan", day) if day else signature(title)


def fold_segments(index: list[dict]) -> list[tuple]:
    """Fold a sitting uploaded in pieces back into the whole recording.

    Some sittings exist both as one video and as `(segment 1)` + `(segment 2)`
    — 27 March 2002 is 340s + 2501s against a single 2841s upload, the same
    seconds either way, all three published on the same day. Recency cannot
    choose between them, but the arithmetic can: where the segments add up to
    a whole that is also published, the whole is the sitting and the pieces
    become alternates.

    If the durations do not add up, nothing is folded — the pieces are then
    genuinely different recordings.
    """
    folded = []
    for entry in index:
        pubs = entry.get("published") or []
        cats = entry.get("catalogued") or []
        whole: dict[tuple, int] = {}
        pieces: dict[tuple, list[int]] = defaultdict(list)
        for i, pub in enumerate(pubs):
            if not pub.get("title") or pub.get("superseded_by"):
                continue
            sig = signature(pub["title"])
            key = sig[:3]
            if sig[3]:
                pieces[key].append(i)
            elif key not in whole:
                whole[key] = i
        for key, idxs in pieces.items():
            if key not in whole or len(idxs) < 2:
                continue
            w = whole[key]
            total = sum(pubs[i].get("minutes") or 0 for i in idxs)
            target = pubs[w].get("minutes") or 0
            if not target or abs(total - target) > max(2, target * 0.05):
                continue
            winner = cats[w].get("youtube_id") if w < len(cats) else ""
            if not winner:
                continue
            for i in idxs:
                pubs[i]["superseded_by"] = winner
                pubs[i]["superseded_reason"] = "segment"
                folded.append((entry["date"], pubs[i]["title"], pubs[w]["title"]))
    return folded


def refresh_lengths(index: list[dict], by_id: dict[str, dict]) -> list[tuple]:
    """Take each video's length from the channel, for videos already listed.

    The merge skips a row whose id it already knows — that is what makes it
    idempotent. But it means a video's length is recorded once, when it is
    first added, and never looked at again. A scheduled premiere is added
    while the channel still reports no duration at all, so it is stored as
    zero minutes and stays zero after it airs: the Patra 22 sittings showed a
    blank length column for a week, and nothing could work out that a
    recording had been heard to the end, because nine tenths of zero is
    nothing.

    The channel is the recording itself, so it wins here as it already does
    when a video is first attached. Most of what this corrects is a minute of
    rounding; a few are the zeros.
    """
    fixed = []
    for entry in index:
        cats = entry.get("catalogued") or []
        for i, pub in enumerate(entry.get("published") or []):
            vid = (cats[i].get("youtube_id") or "") if i < len(cats) else ""
            row = by_id.get(vid)
            if not row or not row["duration"].isdigit():
                continue
            real = round(int(row["duration"]) / 60)
            if real == (pub.get("minutes") or 0):
                continue
            fixed.append((entry["date"], vid, pub.get("minutes") or 0, real))
            pub["minutes"] = real
            if row["views"].isdigit():
                pub["views"] = int(row["views"])
    return fixed


def name_orphans(index: list[dict], by_id: dict[str, dict]) -> list[tuple]:
    """Give a catalogued row that has a video but no published row its record.

    `sittings()` pairs the two lists by position, so a catalogued row past the
    end of `published` still becomes a sitting — but a nameless one, and
    `dedupe` cannot see it at all because it works from published titles. That
    is how 6 February 2002 kept a third page for the 2020 recording of a
    sitting whose other two uploads had already been reconciled.

    The channel knows what the id is. Writing that down is enough to let every
    other pass treat it like any other upload.
    """
    named = []
    for entry in index:
        cats = entry.get("catalogued") or []
        pubs = entry.setdefault("published", [])
        for i in range(len(pubs), len(cats)):
            row = by_id.get(cats[i].get("youtube_id") or "")
            if not row:
                continue
            when, _ = title_date(row["title"])
            if when and when != entry["date"]:
                continue
            pubs.append({
                "title": row["title"],
                "minutes": (round(int(row["duration"]) / 60)
                            if row["duration"].isdigit() else 0),
                "views": int(row["views"]) if row["views"].isdigit() else 0,
                "uploaded": row["uploaded"] if row["uploaded"].isdigit() else "",
                "scripture": classify(row["title"]) or cats[i].get("scripture", ""),
            })
            named.append((entry["date"], row["id"], row["title"]))
    return named


def merge_renumbered(groups: dict[tuple, list[int]], cats: list[dict],
                     pending: set[str], pubs_of: list[dict]) -> None:
    """Fold a premiere in with the upload it replaces when the re-edit
    renumbered the parts.

    A sitting is identified by its text, the letter it teaches and which part
    of that letter it is. The re-edited Patra 22 uploads keep the first two
    and change the third: the 9 February cassette was found after the others
    were published and inserted as Part 3, so everything from 12 February on
    moved up by one. `Part 4, 12 Feb 2002` and `Part 3, 12 Feb 2002` are one
    sitting recorded once.

    Only a premiere is folded this way, only into a group that already exists
    on the same day for the same text and the same letter, and only when
    there is exactly one such group. A day where the letter genuinely ran to
    two sittings offers two candidates and is left alone rather than guessed
    at — the part number is the only thing that tells those apart, and this is
    precisely the case where it cannot be trusted.
    """
    def is_premiere(i: int) -> bool:
        vid = cats[i].get("youtube_id") if i < len(cats) else ""
        return bool(vid) and vid in pending

    for key in [k for k in groups if all(is_premiere(i) for i in groups[k])]:
        text, nums = key[0], key[1]
        hosts = [k for k in groups
                 if k != key and k[0] == text and k[1] == nums
                 and not all(is_premiere(i) for i in groups[k])]
        if len(hosts) != 1:
            continue
        # Written down, because this only holds while the upload is still a
        # premiere. Once it airs it is an ordinary video with a part number
        # one higher than its neighbour's, nothing groups the two again, and
        # the premiere stays marked superseded by the very upload it was
        # meant to replace — which is what kept 12, 13 and 14 February on
        # their old edits for a week after the new ones went live.
        anchor = next((cats[i].get("youtube_id") for i in groups[hosts[0]]
                       if i < len(cats) and cats[i].get("youtube_id")), "")
        if anchor:
            for i in groups[key]:
                pubs_of[i]["sitting_of"] = anchor
        groups[hosts[0]].extend(groups.pop(key))


def dedupe(index: list[dict], recency: dict[str, int],
           pending: set[str] | None = None) -> list[tuple]:
    """Mark re-uploads of a sitting that is already in the archive.

    He has been restoring the old recordings and re-uploading them, so a
    sitting can appear two or three times with the later upload sounding and
    looking better. The newest becomes the sitting's video; the earlier ones
    are marked `superseded_by` rather than deleted, so the page can still
    offer them and nothing that was ever published stops resolving.

    `recency` ranks ids with 0 as the most recent. `pending` holds ids that
    are on the channel but not yet watchable — scheduled premieres.

    A premiere loses to anything that plays. It is the version that will
    replace the rest, and it takes over of its own accord the moment it airs
    and gains a duration; until then a page that carries it has nothing on it
    to listen to. Six discourses went dark for up to five days when it was the
    other way round.
    """
    pending = pending or set()
    marked = []
    for entry in index:
        pubs = entry.get("published") or []
        cats = entry.get("catalogued") or []
        if len(pubs) < 2:
            continue
        groups: dict[tuple, list[int]] = defaultdict(list)
        where = {(cats[i].get("youtube_id") or ""): i for i in range(len(cats))}
        for i, pub in enumerate(pubs):
            if not pub.get("title"):
                continue
            # A judgement an earlier run already made that these two uploads
            # are one sitting — either the fold written while one of them was
            # still a premiere, or the supersede that fold produced. Read back
            # so it survives: once a premiere airs, its part number no longer
            # matches its neighbour's and nothing else would group them, so
            # the premiere would sit marked superseded by the very upload it
            # was published to replace.
            anchor = pub.get("sitting_of") or pub.get("superseded_by")
            host = where.get(anchor) if anchor else None
            if host is not None and host < len(pubs) and pubs[host].get("title"):
                groups[group_key(pubs[host]["title"])].append(i)
            else:
                groups[group_key(pub["title"])].append(i)
        split_conflicting_parts(groups, pubs)
        merge_renumbered(groups, cats, pending, pubs)
        for idxs in groups.values():
            if len(idxs) < 2:
                continue
            ids = {i: (cats[i].get("youtube_id") if i < len(cats) else "") for i in idxs}
            if not any(ids.values()):
                continue
            best = min(idxs, key=lambda i: (ids[i] in pending,
                                            recency.get(ids[i] or "", 10 ** 6)))
            for i in idxs:
                if i == best or not ids[best]:
                    pubs[i].pop("superseded_by", None)
                    pubs[i].pop("superseded_reason", None)
                    continue
                reason = ("not yet premiered" if ids[i] in pending
                          else "later upload")
                if (pubs[i].get("superseded_by") == ids[best]
                        and pubs[i].get("superseded_reason") == reason):
                    continue
                pubs[i]["superseded_by"] = ids[best]
                pubs[i]["superseded_reason"] = reason
                marked.append((entry["date"], pubs[i].get("title", ""),
                               pubs[best].get("title", "")))
    return marked


def realign(index: list[dict], channel: dict[str, str]) -> tuple[list, list]:
    """Put each video id on the sitting it actually belongs to.

    The video sheet paired ids with rows by hand, and on a dozen dates the
    pairing slipped: on 29 March 2002 the id filed under "Dravya Drushti
    Prakash 17 Part 1" is really the Apoorva Avasar 20 recording, and the two
    Dravya Drushti ids are swapped with each other. `sittings()` reads the id
    from the catalogued row at the same position as the published row, so a
    slipped pairing puts the wrong video on the page.

    The channel knows what each id actually is. Every id it recognises is
    matched to the published row with that title; an id whose channel title
    belongs to another date is dropped, because it is filed under the wrong
    day entirely.
    """
    moved, stale, misfiled = [], [], []
    for entry in index:
        cats = entry.get("catalogued") or []
        pubs = entry.get("published") or []
        if not cats or not pubs:
            continue

        keep = []
        for cat in cats:
            vid = cat.get("youtube_id")
            real = channel.get(vid) if vid else None
            if real:
                when, _ = title_date(real)
                if when and when != entry["date"]:
                    stale.append((entry["date"], vid, real))
                    cat = dict(cat, youtube_id="")
            keep.append(cat)

        # Slot each recognised id against the published row it names.
        want = {title_key(pubs[i].get("title", "")): i for i in range(len(pubs))
                if pubs[i].get("title")}
        placed: dict[int, dict] = {}
        spare = []
        for cat in keep:
            real = channel.get(cat.get("youtube_id") or "")
            i = want.get(title_key(real)) if real else None
            if i is None and real:
                # The CSV and the channel spell the same sitting differently
                # — "Adhyatma Ganga" against "Adhyatmaganga" — so an exact key
                # misses. Fall back to the closest title, but only when one
                # row is clearly closer than the rest.
                i = closest(real, pubs, placed)
            if i is not None and i not in placed:
                placed[i] = cat
            else:
                spare.append(cat)

        # An id left over whose channel title names a different text than the
        # row it was filed under is simply misfiled. Clearing it lets the merge
        # add the video as its own sitting, under the title the channel gives
        # it — otherwise the wrong id makes the video look already-known and
        # its real sitting never gets created.
        for cat in spare:
            real = channel.get(cat.get("youtube_id") or "")
            if not real:
                continue
            here = classify(real)
            if here and not any(classify(p.get("title", "")) == here for p in pubs):
                misfiled.append((entry["date"], cat["youtube_id"], real))
                cat["youtube_id"] = ""

        out = []
        for i in range(max(len(keep), len(pubs))):
            if i in placed:
                out.append(placed[i])
            elif spare:
                out.append(spare.pop(0))
            elif i < len(pubs):
                # No video for this sitting. The slot still has to exist, or
                # every id after it slides up onto the wrong row.
                out.append({"scripture": pubs[i].get("scripture", ""),
                            "reference": reference(pubs[i].get("title", "")),
                            "youtube_id": "", "location": ""})
        out.extend(spare)
        while out and not any(out[-1].get(k) for k in ("youtube_id", "reference",
                                                       "scripture", "location")):
            out.pop()
        # Compare what each id ends up *labelled as*, not its position. Where
        # two sittings on a day share a title, swapping them changes the order
        # without changing the meaning, and reporting that as a move every run
        # would make an idempotent merge look unstable.
        def labels(rows):
            return sorted((c.get("youtube_id") or "",
                           title_key(pubs[i].get("title", "")) if i < len(pubs) else "")
                          for i, c in enumerate(rows))
        if labels(out) != labels(cats):
            moved.append(entry["date"])
        entry["catalogued"] = out
    return moved, stale, misfiled


def attach_id(entry: dict, i: int, row: dict[str, str]) -> int:
    """Give an already-listed video its id.

    `sittings()` pairs the catalogued and published lists by position, so the
    id has to land at the same index — the list is padded if it is short.
    """
    cat = entry.setdefault("catalogued", [])
    scripture = (entry["published"][i].get("scripture")
                 or classify(row["title"]) or "")
    while len(cat) <= i:
        cat.append({"scripture": scripture, "reference": "", "youtube_id": "",
                    "location": ""})
    if cat[i].get("youtube_id"):
        return 0
    pub = entry["published"][i]
    real = round(int(row["duration"]) / 60) if row["duration"].isdigit() else 0
    if real and real != pub.get("minutes"):
        # A few CSV rows record a 58-minute sitting as 1 minute. The channel
        # is the recording itself, so it wins.
        pub["minutes"] = real
    if row["views"].isdigit():
        pub["views"] = int(row["views"])
    cat[i]["youtube_id"] = row["id"]
    cat[i]["source"] = "channel listing"
    if not cat[i].get("reference"):
        cat[i]["reference"] = reference(row["title"])
    if not cat[i].get("scripture"):
        cat[i]["scripture"] = scripture
    return 1


def retire(index: list[dict], pending: set[str]) -> list[dict]:
    """Drop an upload that a better one has replaced.

    He has been restoring the old recordings and re-uploading them, so a
    sitting can carry two or three uploads of itself. Until now the older ones
    were kept and offered under the video — but the newer upload is the same
    discourse, sounding and looking better, and a memorial archive offering an
    inferior copy of the same hour is clutter, not generosity.

    A scheduled premiere is never retired. It is marked superseded only
    because nobody can watch it yet, and it is the version that will replace
    everything else the moment it airs.

    Segments are not retired either. A sitting published whole and also cut
    into pieces is not an older version of itself.
    """
    dropped = []
    for entry in index:
        pubs = entry.get("published") or []
        cats = entry.get("catalogued") or []
        # By position and nothing else. The two lists are parallel but not
        # always the same length — `realign` leaves spare catalogue rows at
        # the end — so padding them to match, or zipping them, either invents
        # empty rows or silently drops the spares. Both wreck the pairing
        # `sittings()` depends on: 16 sittings lost their text and fell into
        # "Other discourses" the first time this was written that way.
        drop = set()
        for i, pub in enumerate(pubs):
            vid = (cats[i].get("youtube_id") or "") if i < len(cats) else ""
            why = pub.get("superseded_reason")
            # "not yet premiered" is never retired: the upload that displaced
            # it cannot be played, so this is still the only way to hear the
            # sitting. It is retired on the run after the premiere airs.
            if (pub.get("superseded_by")
                    and why == "later upload"
                    and vid not in pending):
                drop.add(i)
                dropped.append({
                    "youtube_id": vid,
                    "date": entry["date"],
                    "title": pub.get("title", ""),
                    "replaced_by": pub["superseded_by"],
                    "why": ("a re-edited upload replaced it, premiering shortly"
                            if why == "replaced by a scheduled premiere"
                            else "a later upload of the same sitting replaced it"),
                })
        if not drop:
            continue
        # The catalogue row at the same position is not always the same
        # sitting's record. On 9 November 2001 it reads "Shri Parmagamsar,
        # Gatha 291, 279, 280, 292" and only happens to sit beside a Tatva
        # Charcha upload; deleting it with the video would throw away the one
        # record that sitting has. `sittings()` groups by text before pairing,
        # so a row naming a different text can stay where it is — it just
        # loses the id of the video that is going.
        keep_cats = []
        for i, cat in enumerate(cats):
            if i not in drop:
                keep_cats.append(cat)
                continue
            same = (cat.get("scripture") or "") == (pubs[i].get("scripture") or "")
            if same or not (cat.get("reference") or cat.get("location")):
                continue
            keep_cats.append(dict(cat, youtube_id=""))
        entry["published"] = [p for i, p in enumerate(pubs) if i not in drop]
        entry["catalogued"] = keep_cats
    return dropped


def repoint(entry: dict, i: int, row: dict[str, str],
            channel: dict[str, str]) -> str:
    """Give a listed sitting the video whose title it is actually carrying.

    A re-upload arrives with a new id and the archive recognises the title, so
    it says "already listed" and moves on. But the id already on that row is
    the *older* upload — the channel gives it a different title entirely. The
    page then claims to be the re-upload while playing the original.

    That is how eight sittings ended up mislabelled. Five Patra 22 discourses
    carried the 2025 edited titles over the 2020 whole-recording ids, and when
    those recordings were re-transcribed against the edits, the transcripts
    were eleven minutes out of step with the video actually on the page.

    The row keeps the title it has and gains the id that belongs to it. The
    displaced video is not discarded: it is added as an upload of the same
    sitting under the name the channel gives it, so `dedupe` can rank the two
    and keep the older one reachable as an alternate.

    Returns the displaced id, or "" if there was nothing to correct.
    """
    cats = entry.setdefault("catalogued", [])
    if i >= len(cats):
        return ""
    was = cats[i].get("youtube_id") or ""
    older = channel.get(was)
    # Only when the channel is sure the id is a different video. An id the
    # channel does not recognise may simply be unlisted, and guessing at that
    # would throw away the only link a sitting has.
    if not was or not older:
        return ""
    if title_key(older) == title_key(row["title"]):
        return ""
    when, _ = title_date(older)
    if when and when != entry["date"]:
        # It belongs to another day; `realign` deals with that, not this.
        return ""

    cats[i]["youtube_id"] = row["id"]
    cats[i]["source"] = "channel listing"
    pub = entry["published"][i]
    if row["duration"].isdigit():
        pub["minutes"] = round(int(row["duration"]) / 60)
    if row["views"].isdigit():
        pub["views"] = int(row["views"])

    entry["published"].append({
        "title": older,
        "minutes": pub.get("minutes", 0),
        "views": 0,
        "uploaded": "",
        "scripture": classify(older) or pub.get("scripture", ""),
    })
    entry["catalogued"].append({
        "scripture": classify(older) or pub.get("scripture", ""),
        "reference": reference(older),
        "youtube_id": was,
        "location": "",
        "source": "displaced by the upload that carries this title",
    })
    return was


def merge(rows: list[dict[str, str]], index: list[dict]) -> dict:
    # Titles are unique on the channel except for three, where two genuinely
    # different sittings were uploaded under the same name. Everywhere else a
    # title collision means the same recording reached us twice — once from
    # the CSV, once from the channel — and must not become a second sitting.
    shared = {k for k, n in Counter(title_key(r["title"]) for r in rows).items()
              if n > 1}
    by_date = {entry["date"]: entry for entry in index}
    known = {c["youtube_id"] for e in index for c in (e.get("catalogued") or [])
             if c.get("youtube_id")}
    # The earlier CSV import recorded videos under `published` only, with no
    # id anywhere, so an id check alone does not recognise them and the same
    # video gets added a second time. Titles come from the same source and are
    # specific enough (they carry the date) to match on.
    # …and where the CSV row and the channel row are the same video, the
    # channel knows the id the CSV never had. Attaching it is what puts a
    # player on a page that until now had only a title.
    seen_titles: dict[str, tuple[dict, int]] = {}
    for entry in index:
        for i, pub in enumerate(entry.get("published") or []):
            if pub.get("title"):
                seen_titles.setdefault(title_key(pub["title"]), (entry, i))

    # Rows the earlier CSV import could not date were parked under a `?pub…`
    # key. Some of them are datable after all, once month-first titles are
    # understood; move those onto the real date and leave the rest parked.
    repaired = []
    for entry in [e for e in index if not e["date"][:4].isdigit()]:
        pubs = entry.get("published") or []
        cats = entry.get("catalogued") or []
        # Which rows can be dated, by position. The published row and the
        # catalogued row at the same position are the same sitting seen from
        # two sources, so they have to travel together: the title is in the
        # published row and the video id is in the catalogued one. Moving the
        # title alone published 13 October 1999 as a page with nothing on it
        # to listen to, the id still parked under `?pub2024-07-20`.
        moving = []
        for i, pub in enumerate(pubs):
            date, _ = title_date(pub.get("title", ""))
            if date:
                moving.append((i, date))
        for i, date in moving:
            target = by_date.get(date)
            if target is None:
                target = {"date": date, "published": [], "catalogued": [],
                          "processed": False, "audio": []}
                by_date[date] = target
            target.setdefault("published", []).append(pubs[i])
            if i < len(cats):
                target.setdefault("catalogued", []).append(cats[i])
            repaired.append((pubs[i].get("title", ""), date))
        if moving:
            gone = {i for i, _ in moving}
            entry["published"] = [p for i, p in enumerate(pubs) if i not in gone]
            entry["catalogued"] = [c for i, c in enumerate(cats) if i not in gone]

    dropped = []
    for entry in index:
        for key in ("published", "catalogued", "audio"):
            keep = []
            for item in entry.get(key) or []:
                if title_key(item.get("title", "")) in EXCLUDED:
                    dropped.append(item.get("title", ""))
                else:
                    keep.append(item)
            if key in entry:
                entry[key] = keep

    channel = {r["id"]: r["title"] for r in rows}
    moved, stale, misfiled = realign(index, channel)
    # the realignment may have freed slots, so rebuild the lookups
    known = {c["youtube_id"] for e in index for c in (e.get("catalogued") or [])
             if c.get("youtube_id")}
    seen_titles = {}
    for entry in index:
        for i, pub in enumerate(entry.get("published") or []):
            if pub.get("title"):
                seen_titles.setdefault(title_key(pub["title"]), (entry, i))

    added, skipped, new_days, enriched = 0, [], 0, 0
    relinked: list[str] = []
    repointed: list[tuple] = []
    texts = Counter()
    for row in rows:
        if row["id"] in known:
            continue
        if row["id"] in RETIRED:
            # Replaced by a better upload and dropped on purpose; finding it
            # on the channel again is not a reason to bring it back.
            continue
        if title_key(row["title"]) in EXCLUDED:
            continue
        key = title_key(row["title"])
        match = seen_titles.get(key)
        if match:
            if attach_id(match[0], match[1], row):
                enriched += 1
                known.add(row["id"])
                continue
            # The title is listed, but under the id of a different video. The
            # row is carrying this upload's name over the older upload's
            # recording; give it the id that matches what it says it is.
            displaced = repoint(match[0], match[1], row, channel)
            if displaced:
                repointed.append((match[0]["date"], row["title"], displaced, row["id"]))
                known.add(row["id"])
                continue
            if key not in shared:
                # Already listed, under an id the video sheet recorded
                # differently — a re-upload, not a second sitting.
                relinked.append(row["title"])
                continue
            # One of the three shared titles: a real second sitting. Fall
            # through and give it its own page.
        date, why = title_date(row["title"])
        text = classify(row["title"])
        if not date and text == BHAKTI:
            # A bhajan has no date to be filed under. It was sung at a sitting
            # the written list does not number, and the only date anywhere
            # near it is the day it was uploaded to YouTube — which is not
            # when it was sung, and saying so on the page would be a claim the
            # archive cannot support. Park it under a key that is not a date,
            # where the build can publish it without inventing one.
            #
            # Deliberately outside `new_days`, which counts days of teaching
            # this merge learned about; this is not one.
            date = UNDATED
            by_date.setdefault(date, {"date": date, "published": [],
                                      "catalogued": [], "processed": False,
                                      "audio": []})
        if not date:
            skipped.append((row, why))
            continue
        if not text:
            skipped.append((row, "names no text we recognise"))
            continue

        entry = by_date.get(date)
        if entry is None:
            entry = {"date": date, "published": [], "catalogued": [],
                     "processed": False, "audio": []}
            by_date[date] = entry
            new_days += 1
        entry.setdefault("catalogued", []).append({
            "scripture": text,
            "reference": reference(row["title"]),
            "youtube_id": row["id"],
            "location": "",
            "source": "channel listing",
        })
        entry.setdefault("published", []).append({
            "title": row["title"],
            "minutes": (round(int(row["duration"]) / 60)
                        if row["duration"].isdigit() else 0),
            "views": int(row["views"]) if row["views"].isdigit() else 0,
            "uploaded": row["uploaded"] if row["uploaded"].isdigit() else "",
            "scripture": text,
        })
        known.add(row["id"])
        seen_titles[title_key(row["title"])] = (entry, len(entry["published"]) - 1)
        texts[text] += 1
        added += 1

    # Realign once more. The first pass ran before the new rows existed, so an
    # id whose sitting only arrived during this merge had nowhere to go.
    again, more_stale, more_misfiled = realign(list(by_date.values()), channel)
    misfiled.extend(more_misfiled)
    relocated = relocate(list(by_date.values()))
    by_date = {e["date"]: e for e in by_date.values()}
    for entry in index:
        by_date.setdefault(entry["date"], entry)

    # Titles first, then his playlists, so curation overrides inference.
    renamed = reclassify(list(by_date.values()))
    from_playlists = apply_playlists(list(by_date.values()),
                                     read_playlists(ROOT / "work" / "playlist-items.tsv"))
    in_series = attach_series(list(by_date.values()),
                              read_series(ROOT / "work" / "playlist-items.tsv"))
    audio_named = reclassify_audio(list(by_date.values()))
    audio_moved = fold_audio(list(by_date.values()))
    same_day = pair_same_day(list(by_date.values()))
    # Last, because it checks the answer every earlier pass arrived at against
    # the reference numbers, and only overrules them when the numbers are
    # unambiguous.
    crossed, resorted, ambiguous = align_by_number(list(by_date.values()))
    # yt-dlp reports no duration for a video that has not premiered yet.
    pending = {r["id"] for r in rows if not r["duration"].isdigit()}
    # Before deduping: a catalogued row with a video but no published row is
    # invisible to dedupe, and becomes a nameless extra page.
    by_id = {r["id"]: r for r in rows}
    orphans = name_orphans(list(by_date.values()), by_id)
    # Before dedupe: `fold_segments` compares the length of a whole recording
    # against the lengths of its pieces, and a stale length breaks that sum.
    relengthed = refresh_lengths(list(by_date.values()), by_id)
    superseded = dedupe(list(by_date.values()), upload_order(rows), pending)
    superseded += fold_segments(list(by_date.values()))
    # Last: dedupe has to have decided what replaces what before anything is
    # dropped, and fold_segments must have run so segments are not mistaken
    # for older versions.
    retired = retire(list(by_date.values()), pending)
    moved = sorted(set(moved) | set(again))
    stale.extend(more_stale)

    merged = sorted((e for e in by_date.values()
                     if e["date"][:4].isdigit() or any(e.get(k) for k in
                        ("published", "catalogued", "audio"))),
                    key=lambda e: e["date"])
    return {"index": merged, "added": added, "skipped": skipped,
            "new_days": new_days, "texts": texts, "repaired": repaired,
            "enriched": enriched, "dropped": dropped, "relinked": relinked,
            "moved": moved, "stale": stale, "misfiled": misfiled,
            "superseded": superseded, "renamed": renamed,
            "audio_moved": audio_moved, "audio_named": audio_named, "same_day": same_day, "relocated": relocated, "in_series": in_series, "from_playlists": from_playlists,
            "crossed": crossed, "resorted": resorted, "ambiguous": ambiguous,
            "repointed": repointed, "retired": retired, "orphans": orphans,
            "relengthed": relengthed,
            "pending": [r for r in rows if r["id"] in pending]}


def main() -> None:
    ap = argparse.ArgumentParser(prog="merge-youtube")
    ap.add_argument("tsv")
    ap.add_argument("--write", action="store_true",
                    help="update avlokan/master_index.json (a backup is kept)")
    args = ap.parse_args()

    rows = read_tsv(Path(args.tsv))
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    before = sum(len(e.get("catalogued") or []) for e in index)
    result = merge(rows, index)

    print(f"{len(rows)} videos on the channel, {before} already in the index")
    print(f"{result['enriched']} videos already listed gained their YouTube id")
    print(f"{result['added']} added across {len(result['texts'])} texts "
          f"({result['new_days']} dates the index did not have)")
    for name, n in result["texts"].most_common():
        print(f"   {n:4}  {name}")
    if result["dropped"]:
        print(f"{len(result['dropped'])} non-pravachan rows removed "
              f"(see avlokan/not_pravachan.json)")
    for title, date in result["repaired"]:
        print(f"   dated from its title: {date}  {title[:60]}")
    if result["moved"]:
        print(f"{len(result['moved'])} dates had video ids on the wrong sitting, "
              f"realigned against the channel: {', '.join(result['moved'][:6])}"
              + (" …" if len(result['moved']) > 6 else ""))
    for date, vid, real in result["misfiled"]:
        print(f'   misfiled link cleared on {date}: {vid} is really "{real[:46]}"')
    for date, vid, real in result["stale"]:
        print(f'   stale link dropped from {date}: {vid} is really "{real[:50]}"')
    if result["from_playlists"]:
        counts = Counter((was or "(none)", now) for _, was, now, _ in result["from_playlists"])
        print(f"{len(result['from_playlists'])} sittings re-filed to the text his own "
              f"playlist puts them in:")
        for (was, now), n in counts.most_common():
            print(f"   {n:4}  {was[:34]:34} -> {now}")
    for was, now, title in result["relocated"]:
        print(f"   date corrected: {was} -> {now}  {title[:50]}")
    if result["in_series"]:
        print(f"{result['in_series']} sittings placed in the series his playlists define")
    if result["renamed"]:
        print(f"{len(result['renamed'])} videos filed as \"(other)\" identified "
              f"from their own titles:")
        for date, name, title in result["renamed"]:
            print(f"   {date}  {name:32} {title[:42]}")
    if result["audio_named"]:
        counts = Counter((w or "(none)", n) for _, w, n, _ in result["audio_named"])
        print(f"{len(result['audio_named'])} audio entries re-filed to the text their "
              f"own subject names:")
        for (was, now), n in counts.most_common(6):
            print(f"   {n:4}  {was[:30]:30} -> {now}")
    if result["same_day"]:
        print(f"{len(result['same_day'])} audio entries paired with the day's video "
              f"that had none, across a disagreeing text name")
    if result["crossed"]:
        print(f"{len(result['crossed'])} audio entries re-filed because the "
              f"reference numbers say the day's sittings were crossed:")
        for date, was, now, subject, title in result["crossed"]:
            print(f"   {date}  {was[:26]:26} -> {now}")
            print(f"             {subject[:44]:44} with  {title[:48]}")
    if result["resorted"]:
        print(f"{len(result['resorted'])} days had their audio entries put back "
              f"into the order the videos give:")
        for date, subjects in result["resorted"]:
            print(f"   {date}  {' | '.join(s[:24] for s in subjects)}")
    for date, base, best, n in result["ambiguous"]:
        print(f"   ? {date}: {n} arrangements agree on {best} numbers "
              f"(currently {base}) — left as it is, decide it by hand")
    if result["audio_moved"]:
        print(f"{len(result['audio_moved'])} audio entries moved onto the day the "
              f"video gives for that sitting:")
        for was, now, text, part in result["audio_moved"]:
            print(f"   {was} -> {now}  {text} part {part}")
    if result["relengthed"]:
        zeros = [r for r in result["relengthed"] if r[2] == 0]
        print(f"{len(result['relengthed'])} video length(s) taken from the channel "
              f"({len(zeros)} that had none at all):")
        for date, vid, was, now in zeros:
            print(f"   {date}  {vid}  {was} -> {now} min")
    if result["orphans"]:
        print(f"{len(result['orphans'])} video(s) had a catalogue row but no record "
              f"of what they are; taken from the channel:")
        for date, vid, title in result["orphans"]:
            print(f"   {date}  {vid}  {title[:52]}")
    if result["retired"]:
        print(f"{len(result['retired'])} older upload(s) removed from the archive, "
              f"replaced by a better one of the same sitting:")
        for r in result["retired"]:
            print(f"   {r['date']}  {r['youtube_id'] or '(no id)':12} {r['title'][:50]}")
    if result["repointed"]:
        print(f"{len(result['repointed'])} sitting(s) were carrying a title over the "
              f"wrong video; given the upload that matches what they say:")
        for date, title, was, now in result["repointed"]:
            print(f"   {date}  {title[:56]}")
            print(f"             {was} -> {now}  (the displaced upload is kept)")
    if result["pending"]:
        print(f"{len(result['pending'])} scheduled premiere(s), not watchable yet — "
              f"listed, but a video that plays keeps the sitting until each airs:")
        for r in result["pending"]:
            print(f"   {r['title'][:66]}")
    if result["superseded"]:
        print(f"{len(result['superseded'])} re-uploads marked as superseded by a "
              f"later, better upload of the same sitting:")
        for date, old, new in result["superseded"]:
            print(f"   {date}  {old[:44]}\n            superseded by  {new[:44]}")
    if result["relinked"]:
        print(f"{len(result['relinked'])} already listed under a different id "
              f"(re-uploads); left as they were")
    if result["skipped"]:
        print(f"\n{len(result['skipped'])} left out:")
        for row, why in result["skipped"]:
            print(f"   {why:34}  {row['title'][:70]}")

    if not args.write:
        print("\nnothing written — re-run with --write to apply")
        return
    backup = INDEX.with_suffix(f".backup-{datetime.now():%Y%m%d-%H%M%S}.json")
    shutil.copy2(INDEX, backup)
    INDEX.write_text(json.dumps(result["index"], indent=1, ensure_ascii=False),
                     encoding="utf-8")
    if result["retired"]:
        # Recorded before they are needed: without this the next run finds
        # them on the channel and adds each one back as a sitting of its own.
        keeping = dict(RETIRED)
        for r in result["retired"]:
            if r["youtube_id"]:
                keeping[r["youtube_id"]] = {k: v for k, v in r.items()
                                            if k != "youtube_id"}
        RETIRED_FILE.write_text(
            json.dumps(keeping, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"{len(result['retired'])} upload(s) recorded in "
              f"{RETIRED_FILE.name} so they are not added back")
    print(f"\nwritten. previous index kept at {backup.name}")


if __name__ == "__main__":
    main()
