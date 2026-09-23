"""Build a static archive site from what Avlokan has produced.

Design rules, in priority order:

1. **It must still work when nobody is maintaining it.** Plain HTML files, no
   framework, no build step beyond this script, no server, no database. Python
   standard library only.
2. **The transcript is real text in the page.** Not fetched, not rendered by
   script. That is what a search engine indexes, what an AI can read, and what
   survives in a web archive twenty years from now.
3. **Everything interactive is an enhancement.** The synced transcript, resume
   position and highlights are added by script on top of a page that is already
   complete without them. If the script never runs, nothing important is lost.
4. **Video is never self-hosted.** YouTube for playback, Internet Archive as
   the permanent fallback. Both are free to serve; a storage bill is a
   countdown timer on an archive meant to outlive its keeper.

Run:  python3 -m avlokan_site.build [--out site]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "avlokan"
OUTPUTS_DEFAULT = ROOT / "outputs"
WORK = ROOT / "work"

SITE_TITLE = "Avlokan"
SITE_DOMAIN = "avlokan.org"
SITE_TAGLINE = "Discourses of Shri Devchand bhai Shah"
SITE_DESC = (
    "An archive of spiritual discourses on Shrimad Rajchandra's Vachanamrut, "
    "Samaysar, Dravya Drushti Prakash and other texts, recorded 1994–2002."
)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "item"


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def fmt_hms(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02}:{sec:02}" if h else f"{m}:{sec:02}"


def pretty_date(iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return iso or ""
    months = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
    return f"{int(m.group(3))} {months[int(m.group(2)) - 1]} {m.group(1)}"


# --------------------------------------------------------------------------
# reading what the pipeline produced
# --------------------------------------------------------------------------

def parse_vtt(path: Path) -> list[dict[str, Any]]:
    """Timed transcript blocks from a WebVTT file."""
    if not path.exists():
        return []
    stamp = re.compile(
        r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"
    )
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        hit = stamp.search(line)
        if hit:
            if current and current["text"]:
                blocks.append(current)
            def secs(h, m, s, ms):
                return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
            current = {
                "start": secs(*hit.group(1, 2, 3, 4)),
                "end": secs(*hit.group(5, 6, 7, 8)),
                "text": [],
            }
        elif current is not None:
            stripped = line.strip()
            if stripped and stripped != "WEBVTT" and not stripped.isdigit():
                current["text"].append(stripped)
    if current and current["text"]:
        blocks.append(current)
    for b in blocks:
        b["text"] = "\n".join(b["text"])
    return blocks


MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

STOPWORDS = {"shri", "shree", "shrimad", "par", "pravachan", "part", "the"}


def fold(text: str) -> str:
    """A spelling-tolerant key. The same text is written Drushti and Drashti,
    Apoorva and Apurva, depending on who typed it; folding lets a filename
    match a catalogue entry without either being corrected first."""
    key = re.sub(r"[^a-z]+", "", str(text).lower())
    for old, new in (("sh", "s"), ("ch", "c"), ("th", "t"), ("dh", "d"),
                     ("bh", "b"), ("kh", "k"), ("ph", "f"),
                     ("oo", "u"), ("ee", "i"), ("aa", "a"), ("w", "v")):
        key = key.replace(old, new)
    key = re.sub(r"[aeiou]+", "a", key)
    return re.sub(r"(.)\1+", r"\1", key)


def scripture_tokens(name: str) -> set[str]:
    return {fold(w) for w in re.findall(r"[A-Za-z]+", str(name))
            if len(w) > 3 and w.lower() not in STOPWORDS}


def parse_label(label: str) -> tuple[str, int] | None:
    """The recording date and which sitting of that day, read off the filename.

    `Dravya Drushti Prakash17-2` is patra 17, second sitting — the trailing
    `-2` is the part, not part of the reference. A name with no part is the
    first sitting of its day.
    """
    hit = re.search(
        r"(\d{1,2})[\s_,-]*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*[\s_,-]*(\d{4})",
        label, re.I)
    if not hit:
        return None
    mon = MONTHS.get(hit.group(2)[:3].lower())
    if not mon:
        return None
    date = f"{hit.group(3)}-{mon:02}-{int(hit.group(1)):02}"
    return date, label_part(label)


def label_part(label: str) -> int:
    """Which sitting of the day, written three different ways across the
    filenames: `Part 2`, `Prakash17-2`, and `528[2]`."""
    hit = (re.search(r"\bpart\s*(\d{1,2})\b", label, re.I)
           or re.search(r"\d+\s*-\s*(\d{1,2})(?:\b|[\[(])", label)
           or re.search(r"\[(\d{1,2})\]", label))
    return int(hit.group(1)) if hit else 1


def job_index(outputs: Path) -> list[dict[str, Any]]:
    """Every processed job, with enough identity to tell two sittings on the
    same day apart. Keying these by date alone silently dropped the second
    recording of a day and hung its transcript on the wrong discourse."""
    fixes = read_json(CONFIG / "date_corrections.json", {})
    jobs: list[dict[str, Any]] = []
    for state_path in sorted((WORK / "jobs").glob("*/state.json")):
        state = read_json(state_path, {})
        if not state:
            continue
        job_id = state_path.parent.name
        source = (state.get("artifacts") or {}).get("original") or state.get("input") or ""
        name = Path(source).name
        folder = Path(source).parent.name
        label = folder if name.lower().startswith(("avseq", "vts_", "music")) else name
        parsed = parse_label(label)
        if not parsed:
            # A malformed date in the source filename, corrected by hand rather
            # than guessed at here; see avlokan/date_corrections.json.
            fix = fixes.get(label) or fixes.get(Path(label).stem)
            if not (fix and fix.get("to")):
                continue
            parsed = (fix["to"], label_part(label))
        date, part = parsed
        out_dir = outputs / job_id
        jobs.append({
            "job_id": job_id,
            "label": label,
            "date": date,
            "part": part,
            "tokens": scripture_tokens(re.sub(r"\d+", " ", label)),
            "transcript": (out_dir / "corrected_mixed_script_transcript.txt"),
            "vtt": (out_dir / "subtitles.vtt"),
            "srt": (out_dir / "subtitles.srt"),
            "search_text": (out_dir / "romanized_search_text.txt"),
            "passages": read_json(out_dir / "passage_matches.json", []),
        })
    return jobs


# --------------------------------------------------------------------------
# page assembly
# --------------------------------------------------------------------------

def crumbs(trail: list[tuple[str, str]]) -> str:
    """A breadcrumb trail. The last item is the page you are on, so it is not
    a link — that is what tells a reader, and a screen reader, where they are."""
    if not trail:
        return ""
    items = []
    for i, (label, href) in enumerate(trail):
        last = i == len(trail) - 1
        inner = e(label) if last else f'<a href="{e(href)}">{e(label)}</a>'
        aria = ' aria-current="page"' if last else ""
        items.append(f"<li{aria}>{inner}</li>")
    return ('<nav class="crumbs" aria-label="Breadcrumb"><ol>'
            + "".join(items) + "</ol></nav>")


def shell(title: str, body: str, *, depth: int = 0, description: str = "",
          head_extra: str = "", script: str = "", trail: list[tuple[str, str]] | None = None,
          wide: bool = False, reading: bool = False) -> str:
    up = "../" * depth
    room = " class=\"watching\"" if wide else (" class=\"reading\"" if reading else "")
    return f"""<!doctype html>
<html lang="hi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description or SITE_DESC)}">
<link rel="stylesheet" href="{up}assets/site.css">
{head_extra}
</head>
<body>
<header class="masthead">
  <a class="wordmark" href="{up}index.html">{e(SITE_TITLE)}</a>
  <span class="tagline">{e(SITE_TAGLINE)}</span>
  <a class="navlink" href="{up}book/index.html">The book</a>
  <a class="navlink" href="{up}search.html">Search</a>
  <a class="navlink" href="{up}photographs.html">Photographs</a>
  <a class="navlink" href="{up}about.html">About</a>
</header>
<main{room}>
{crumbs(trail or [])}
{body}
</main>
<footer>
  <p>These teachings are offered freely. Copy them, re-host them, translate them.</p>
  <p class="muted">Recorded 1994&ndash;2002. Archive built from the preserved original recordings.</p>
</footer>
{f'<script src="{up}assets/site.js" defer></script>' if script != "none" else ""}
</body>
</html>
"""


IMAGES = CONFIG / "images"


def cover(name: str, depth: int = 0) -> str:
    """The scanned cover of the text he was teaching, if we have it.

    The covers come from avlokan.org, his own site, and are copied into the
    archive rather than linked — a hotlinked image is a dependency on someone
    else's hosting staying paid for.
    """
    filename = f"cover-{slug(name)}.jpg"
    if not (IMAGES / filename).exists():
        # A plain hrim on grey, so a text with no cover of its own still sits
        # in the grid at the same size instead of leaving a hole.
        filename = "cover-default.jpg"
    if not (IMAGES / filename).exists():
        return ""
    return f'{"../" * depth}assets/covers/{filename}'


OTHER = "Other discourses"

# Three catalogues, built at different times, label the same things
# differently. Folded here at display time rather than rewritten in the
# source data, so the catalogues stay as they were recorded.
ALIASES = {
    "(other)": OTHER,
    "(unclassified)": OTHER,
    "": OTHER,
    "Bhakti / Bhajan": "Bhakti",
    "Occasions": OTHER,
}


def sittings(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Explode one dated index entry into one record per sitting.

    A date is not a sitting. He often taught twice in a day and sometimes
    three times, occasionally from different texts — 29 March 2002 is Dravya
    Drushti Prakash 17 in two parts *and* Apoorva Avasar 20. Treating the date
    as the unit collapsed those into a single page, which then showed one
    text's transcript under another text's title.

    The three sources (video sheet, YouTube uploads, audio list) each list the
    day's sittings separately, so they are grouped by text and then paired off
    in order within each text.
    """
    date = entry["date"]
    order: list[str] = []
    by_text: dict[str, dict[str, list]] = {}
    for kind in ("catalogued", "published", "audio"):
        for item in entry.get(kind) or []:
            name = item.get("scripture") or OTHER
            name = ALIASES.get(name, name)
            if name not in by_text:
                by_text[name] = {"catalogued": [], "published": [], "audio": []}
                order.append(name)
            by_text[name][kind].append(item)
    if not by_text:
        by_text[OTHER] = {"catalogued": [], "published": [], "audio": []}
        order.append(OTHER)

    out: list[dict[str, Any]] = []
    for name in order:
        lists = by_text[name]
        cats, pubs, auds = lists["catalogued"], lists["published"], lists["audio"]

        # Pair the sources off first, then drop the pairs whose video has been
        # superseded by a later upload of the same sitting. Dropping them
        # before pairing would slide every later row onto the wrong sitting.
        rows, alternates = [], defaultdict(list)
        for i in range(max(len(cats), len(pubs), len(auds)) or 1):
            meta = cats[i] if i < len(cats) else {}
            pub = pubs[i] if i < len(pubs) else {}
            aud = auds[i] if i < len(auds) else {}
            winner = pub.get("superseded_by")
            if winner:
                alternates[winner].append({
                    "youtube_id": meta.get("youtube_id") or "",
                    "title": pub.get("title") or "",
                    "minutes": pub.get("minutes") or 0,
                    "reason": pub.get("superseded_reason") or "later upload",
                })
                continue
            rows.append((meta, pub, aud))

        # Put the day's sittings in the order he gave them — the catalogues
        # list them in whatever order they were typed, so "Part 2 … Night"
        # could otherwise come out as the first sitting of the day. Only
        # sort when every row says which part it is: a row with no part
        # would otherwise default to first and displace a real "Part 16".
        parts = [named_part(pub) for _, pub, _ in rows]
        if all(p is not None for p in parts):
            rows = [r for _, r in sorted(zip(parts, rows), key=lambda x: x[0])]

        for i, (meta, pub, aud) in enumerate(rows):
            vid = meta.get("youtube_id") or ""
            out.append({
                "date": date,
                "scripture": name,
                "part": i + 1,
                "of": len(rows),
                "reference": meta.get("reference") or aud.get("subject") or "",
                "location": meta.get("location") or aud.get("place") or "",
                "youtube_id": vid,
                "published": pub,
                "audio": aud,
                "alternates": alternates.get(vid, []),
                "series": meta.get("series") or "",
                "series_id": meta.get("series_id") or "",
                "part_no": sitting_part(pub, aud, meta),
                "tokens": scripture_tokens(name),
                "job": None,
                "uncatalogued": False,
            })
    return out


CORRECTIONS_FILE = CONFIG / "sitting_corrections.json"

# Fields a reader of the page can be sure about, and nothing else. What text
# he was teaching, what it cites, and where it falls in the run: exactly the
# three things the catalogues disagree about. The date is not here — moving a
# sitting to another day rearranges that day's pairing too, and belongs in
# `date_corrections.json` where it can be reasoned about in one place.
CORRECTABLE = ("scripture", "reference", "part_no")


def sitting_key(s: dict[str, Any]) -> str:
    """A name for one sitting that survives being corrected.

    Not the slug: the slug is built from the text name, which is the thing
    most often wrong, so a correction keyed by slug would stop matching the
    moment it was applied. The YouTube id never changes, and neither does an
    audio row's number in the written list.
    """
    if s["youtube_id"]:
        return f'yt:{s["youtube_id"]}'
    n = (s["audio"] or {}).get("n")
    if n is not None:
        return f"audio:{n}"
    return f'date:{s["date"]}#{s["part"]}'


def renumber(same_day: list[dict[str, Any]]) -> None:
    """Re-count which sitting of its text each one is, within its day.

    A correction that moves a sitting to another text changes both the text it
    left and the text it joined — "sitting 2 of 3" and the `-2` on the end of
    the address are both counted per text, per day.
    """
    total = Counter(s["scripture"] for s in same_day)
    seen: Counter = Counter()
    for s in same_day:
        seen[s["scripture"]] += 1
        s["part"] = seen[s["scripture"]]
        s["of"] = total[s["scripture"]]


def apply_corrections(same_day: list[dict[str, Any]],
                      fixes: dict[str, Any]) -> list[tuple[str, str]]:
    """Lay hand corrections over what the catalogues said.

    Applied here, to the assembled sittings, rather than written back into
    `master_index.json`: the merge re-derives that file from the channel every
    week and would quietly undo anything edited into it. A correction stated
    separately outlives the data it corrects, and reads as a disagreement with
    the source rather than a replacement of it — the same reason the wrong
    dates and the OCR damage each have a file of their own.
    """
    moved, changed = [], False
    for s in same_day:
        fix = fixes.get(sitting_key(s))
        if not fix:
            continue
        before = sitting_slug(s)
        for field in CORRECTABLE:
            if field not in fix:
                continue
            value = fix[field]
            if field == "scripture" and value:
                s["scripture"] = value
                s["tokens"] = scripture_tokens(value)
                changed = True
            elif field == "reference":
                s["reference"] = value
            elif field == "part_no":
                s["part_no"] = value
        s["corrected"] = fix
        s["was"] = before
    if changed:
        renumber(same_day)
        for s in same_day:
            if s.get("was") and s["was"] != sitting_slug(s):
                moved.append((s["was"], sitting_slug(s)))
    return moved


def redirect_page(to: str) -> str:
    """A stub left at an address a correction moved a sitting away from.

    Cheap insurance. Someone has a link, or a search engine does, and a
    correction should not turn it into a dead end.
    """
    return (f'<!doctype html>\n<html lang="hi">\n<head>\n<meta charset="utf-8">\n'
            f'<meta http-equiv="refresh" content="0; url={e(to)}">\n'
            f'<link rel="canonical" href="{e(to)}">\n'
            f'<title>Moved</title>\n</head>\n<body>\n'
            f'<p>This sitting is now at <a href="{e(to)}">{e(to)}</a>.</p>\n'
            f'</body>\n</html>\n')


def attach_jobs(all_sittings: list[dict[str, Any]],
                jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hang each transcript on the sitting it actually belongs to.

    Matched on date, then text, then which sitting of the day. Anything that
    cannot be placed is returned rather than attached to a near-miss: a
    transcript on the wrong discourse is worse than a discourse with none.

    Returns what could not be placed, and any sittings that had to be invented
    because a recording exists for a sitting no catalogue lists.
    """
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in all_sittings:
        by_date[s["date"]].append(s)

    unplaced: list[tuple[dict[str, Any], str]] = []
    created: list[dict[str, Any]] = []
    # Collect the day's jobs per text, then hand them out in part order. The
    # number in `Prakash17-3` counts sittings on patra 17 across days, not
    # sittings within that day, so only the relative order is reliable.
    queues: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        here = by_date.get(job["date"]) or []
        if not here:
            unplaced.append((job, "no sitting is indexed for that date"))
            continue
        best, pick = max((token_overlap(job["tokens"], s["tokens"]), i)
                         for i, s in enumerate(here))
        if best == 0 and len(here) > 1:
            unplaced.append((job, f"{len(here)} sittings that day, none name this text"))
            continue
        queues[(job["date"], here[pick]["scripture"])].append(job)

    for (date, scripture), queued in queues.items():
        slots = sorted((s for s in by_date[date] if s["scripture"] == scripture),
                       key=lambda s: s["part"])
        queued.sort(key=lambda j: (j["part"], j["label"]))
        # More recordings than the catalogues list. The recording is the
        # evidence that the sitting happened, so give it a page rather than
        # dropping the transcript — it just has no video to point at.
        while len(slots) < len(queued):
            extra = dict(slots[-1], part=len(slots) + 1, job=None, uncatalogued=True,
                         published={}, audio={}, youtube_id="")
            slots.append(extra)
            created.append(extra)
        for s in slots:
            s["of"] = max(s["of"], len(slots))
        for job, pick in zip(queued, slots):
            pick["job"] = job
    return unplaced, created


def token_overlap(a: set[str], b: set[str]) -> int:
    """Count shared words, tolerating run-together filenames: SHRIMADPATRANK
    folds to one token that still contains the catalogue's `shrimad`."""
    return sum(1 for want in b
               if any(want == got or want in got or got in want for got in a))


def transcript_weight(job: dict[str, Any]) -> tuple[int, int]:
    """How much of the sitting a job actually captured. Timed blocks beat a
    plain transcript, and longer beats shorter — a truncated run is the usual
    reason the same recording was processed twice."""
    blocks = len(parse_vtt(job["vtt"]))
    size = job["transcript"].stat().st_size if job["transcript"].exists() else 0
    return (blocks, size)


def alt_label(alt: dict[str, Any]) -> str:
    """What to call an alternate upload, from its own title."""
    hit = re.search(r"\bsegment\s*(\d{1,2})\b", alt.get("title") or "", re.I)
    if hit:
        return f'segment {hit.group(1)}'
    if alt.get("reason") == "not yet premiered":
        return "the re-edited version"
    minutes = alt.get("minutes") or 0
    return f"earlier upload ({minutes} min)" if minutes else "earlier upload"


def sitting_title(s: dict[str, Any]) -> str:
    """What to call the sitting.

    The published title is used as it stands, except when the date written
    into it contradicts the date of the sitting. That happens where a title is
    wrong at the source and the real date is recorded in
    `avlokan/date_corrections.json` — Solahkaran Bhavana part 32 is titled 23
    September and was taught on the 20th. Repeating the wrong date in the
    heading of a page dated correctly would just spread the error.
    """
    fallback = f'{s["scripture"]} — {pretty_date(s["date"])}'
    title = (s["published"].get("title") or "").strip()
    if not title:
        return fallback
    hit = re.search(
        r"(\d{1,2})\s*(?:st|nd|rd|th)?[\s.,-]*([A-Za-z]{3,4})[a-z]*\.?[\s.,-]*(\d{4})",
        title)
    if hit:
        mon = MONTHS.get(hit.group(2)[:3].lower())
        if mon:
            said = f"{hit.group(3)}-{mon:02}-{int(hit.group(1)):02}"
            if said != s["date"]:
                return fallback
    return title


SERIES_GAP_DAYS = 120


def sitting_part(pub: dict[str, Any], aud: dict[str, Any],
                 meta: dict[str, Any]) -> int | None:
    """Which sitting of its series this is, as he numbered it.

    Taken from the video title first — "Solahkaran Bhavana, Part 24" — then
    from the audio list's own part column. Returns None when neither says,
    which is different from saying it is the first.
    """
    found = named_part(pub)
    if found is not None:
        return found
    raw = str(aud.get("part") or "").strip()
    hit = re.match(r"(\d{1,3})\b", raw)
    return int(hit.group(1)) if hit else None


def series_of_sitting(s: dict[str, Any]) -> tuple:
    """The run of sittings this one belongs to.

    The text plus the letter, gatha or bol number — `Patrank 247` is a run of
    six sittings — rather than the playlist. A playlist only holds what was
    published, so keying on it would leave the sittings that exist as audio
    alone out of their own series: Solahkaran Bhavana part 36 was never put on
    YouTube, and the series is 43 sittings, not the 42 in the playlist. The
    playlist still supplies the name.
    """
    return (s["scripture"], reference_number(s))


def reference_number(s: dict[str, Any]) -> str:
    for text in (s["published"].get("title") or "", s.get("reference") or "",
                 s["audio"].get("subject") or ""):
        hit = re.search(r"\b(?:Patrank|Patra|Gatha|Bol|Sloka|Adhikar|Q)\b\.?\s*(\d{1,4})",
                        text, re.I)
        if hit:
            return hit.group(1)
    for text in (s.get("reference") or "", s["audio"].get("subject") or ""):
        hit = re.search(r"(\d{1,4})", text)
        if hit:
            return hit.group(1)
    return ""


def build_series(all_sittings: list[dict[str, Any]]) -> None:
    """Order each run and tell every sitting where it sits in its own.

    Ordered by the part number he gave it, falling back to the date — never by
    playlist position, which YouTube sets by when a video was added rather
    than when it was recorded.

    A run is split where it stops for four months, so that a letter he
    returned to two years later reads as a second series rather than one long
    one with a hole in it.
    """
    runs: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for s in all_sittings:
        runs[series_of_sitting(s)].append(s)

    for (text, ref), group in runs.items():
        # Split on the calendar first, then order within each run by the part
        # number. Doing it the other way round let a letter taught in 1999 and
        # again in 2001 interleave into one run that ran backwards in time.
        group.sort(key=lambda x: (x["date"], x["part"]))
        chunks, current = [], [group[0]]
        for prev, item in zip(group, group[1:]):
            gap = as_days(item["date"]) - as_days(prev["date"])
            if gap is not None and gap > SERIES_GAP_DAYS:
                chunks.append(current)
                current = []
            current.append(item)
        chunks.append(current)

        for chunk in chunks:
            # By date, with the part number only breaking ties within a day.
            # Ordering by part number instead pushed every unnumbered sitting
            # to the end of its run, including the ones he gave first: the
            # opening Darshanmoha sitting has no number and came before part 2.
            chunk.sort(key=lambda x: (x["date"], x["part_no"] or 0, x["part"]))
        # Without a reference number, sittings of one text only form a series
        # if he numbered them. Tatva Charcha is 51 separate discussions across
        # sixteen months, not a run of 51.
        if not ref:
            chunks = [c for c in chunks
                      if sum(1 for x in c if x["part_no"] is not None) * 2 >= len(c)]
        for chunk in chunks:
            # One name for the whole run. Members disagree — the playlist says
            # "Solahkaran Bhavana" and the audio list "Solah Karan Bhavna" —
            # so the playlist's wording wins wherever any member has one.
            named = Counter(x["series"] for x in chunk if x.get("series"))
            title = named.most_common(1)[0][0] if named else ""
            for i, item in enumerate(chunk):
                item["run"] = chunk
                item["run_at"] = i
                item["run_of"] = len(chunk)
                item["run_name"] = title


def as_days(iso: str) -> int | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", iso or "")
    if not m:
        return None
    from datetime import date
    y, mo, d = (int(x) for x in m.groups())
    return date(y, mo, d).toordinal()


def named_part(pub: dict[str, Any]) -> int | None:
    """Which part the title says this is, or None if it does not say.

    Distinct from `label_part`, which assumes part 1 when a filename is
    silent. Here the difference matters: a row that does not name its part
    must not be assumed to be the first one.
    """
    hit = re.search(r"\bpart\s*(\d{1,2})\b", pub.get("title") or "", re.I)
    return int(hit.group(1)) if hit else None


def sitting_slug(s: dict[str, Any]) -> str:
    base = slug(f'{s["date"]}-{s["scripture"]}')
    return base if s["part"] == 1 else f'{base}-{s["part"]}'


def discourse_page(s: dict[str, Any], siblings: list[dict[str, Any]], depth: int = 1) -> str:
    date, scripture = s["date"], s["scripture"]
    reference, location = shown_reference(s, s["scripture"]), s["location"]
    pub, aud, job = s["published"], s["audio"], s["job"]
    youtube = s["youtube_id"]
    title = sitting_title(s)

    # What a listener needs to be offered this sitting again from the front
    # page. Written into the page rather than looked up, so the list costs no
    # extra request and keeps working if the catalogue is ever reorganised.
    bits = [f'<article class="discourse" data-slug="{e(sitting_slug(s))}" '
            f'data-text="{e(scripture)}" data-when="{e(pretty_date(date))}" '
            f'data-ref="{e(reference)}" '
            f'data-minutes="{pub.get("minutes") or 0}">',
            f'<h1>{e(scripture)}</h1>',
            '<p class="meta">']
    line = [pretty_date(date)]
    if s["of"] > 1:
        line.append(f'sitting {s["part"]} of {s["of"]}')
    if reference:
        line.append(e(reference))
    if location:
        line.append(e(location))
    if pub.get("minutes"):
        line.append(f'{pub["minutes"]} minutes')
    bits.append(" &middot; ".join(line))
    bits.append("</p>")

    # Where this sitting sits in its run, and how to get to the next one. He
    # taught a letter over several days, so the sitting after this one is
    # usually what a listener wants next — more than anything else recorded
    # the same day.
    run = s.get("run") or []
    if len(run) > 1:
        at = s.get("run_at", 0)
        bits.append('<nav class="series" aria-label="This series">')
        bits.append(f'<p class="meta"><span class="where">Part {at + 1} of {len(run)}'
                    f'</span> &middot; {e(series_name(s))}</p>')
        steps = []
        if at > 0:
            steps.append(f'<a class="prev" href="{sitting_slug(run[at - 1])}.html">'
                         f'&larr; {e(step_label(run[at - 1]))}</a>')
        if at + 1 < len(run):
            steps.append(f'<a class="next" href="{sitting_slug(run[at + 1])}.html">'
                         f'{e(step_label(run[at + 1]))} &rarr;</a>')
        if steps:
            bits.append(f'<p class="links">{" ".join(steps)}</p>')
        bits.append('<details class="run"><summary>All ' + str(len(run))
                    + ' in this series</summary><ol>')
        for i, item in enumerate(run):
            label = e(step_label(item))
            inner = (f'<strong>{label}</strong>' if item is s
                     else f'<a href="{sitting_slug(item)}.html">{label}</a>')
            marks = []
            if item["youtube_id"]:
                marks.append("video")
            elif item["audio"]:
                marks.append("audio")
            if item["job"]:
                marks.append("transcript")
            bits.append(f'<li>{inner} <span class="muted">{e(pretty_date(item["date"]))}'
                        + (f' &middot; {", ".join(marks)}' if marks else "") + '</span></li>')
        bits.append('</ol></details></nav>')

    # Everything else he taught that day, so a reader who lands on one sitting
    # can find the rest — they are usually continuous.
    others = [o for o in siblings if o is not s]
    if others:
        links = " &middot; ".join(
            f'<a href="{sitting_slug(o)}.html">{e(o["scripture"])}'
            + (f' {o["part"]}' if o["of"] > 1 else "") + "</a>"
            for o in others)
        bits.append(f'<p class="links muted">Also on this day: {links}</p>')

    # The media, gathered separately so that a page with a transcript can
    # put it beside the words rather than above them.
    stage: list[str] = []
    # Video. The iframe is written by script so the page does not phone Google
    # just for being opened; the link below always works either way.
    if youtube and youtube in REFUSED:
        stage.append(
            f'<div class="video elsewhere"><p>This recording plays on YouTube '
            f'but cannot be shown here. That is a restriction on the video '
            f'itself, not on this page.</p>'
            f'<p><a href="https://www.youtube.com/watch?v={e(youtube)}">'
            f'Watch it on YouTube</a></p></div>')
    elif youtube:
        stage.append(
            f'<div class="video" data-youtube="{e(youtube)}">'
            f'<noscript><p><a href="https://www.youtube.com/watch?v={e(youtube)}">'
            f'Watch on YouTube</a></p></noscript></div>'
        )
        stage.append(
            f'<p class="links"><a href="https://www.youtube.com/watch?v={e(youtube)}">Watch on YouTube</a>'
            f'<span class="archive-slot" data-archive=""></span></p>'
        )
        # The same sitting was published more than once as the recordings were
        # restored. The page plays the latest, but the earlier uploads are the
        # ones people have bookmarked and linked to, so they stay reachable.
        alts = [a for a in s.get("alternates") or [] if a.get("youtube_id")]
        if alts:
            links = " &middot; ".join(
                f'<a href="https://www.youtube.com/watch?v={e(a["youtube_id"])}">'
                f'{e(alt_label(a))}</a>' for a in alts)
            if all(a["reason"] == "not yet premiered" for a in alts):
                note = ("A re-edited upload of this sitting is scheduled on "
                        "YouTube and will replace the video above once it airs")
            elif all(a["reason"] == "segment" for a in alts):
                note = ("The same recording is also published cut into pieces, "
                        "which together run to the same length")
            elif len(alts) == 1:
                note = ("The video above is the most recent upload of this "
                        "sitting. The earlier one is still there")
            else:
                note = ("The video above is the most recent upload of this "
                        "sitting. The earlier ones are still there")
            stage.append(f'<p class="links muted">{note}: {links}.</p>')
    elif aud:
        stage.append(
            f'<p class="links unpublished"><span class="mark mark-unpublished">'
            f'<span class="dot" aria-hidden="true"></span>Recorded, not published'
            f'</span> &mdash; the tape exists and is listed as entry '
            f'{e(aud.get("n", ""))} in the audio catalogue, but it has not been '
            f'put online, so there is nothing to play here yet.</p>'
        )
    elif s["uncatalogued"]:
        stage.append(
            '<p class="links muted">This sitting appears in no catalogue. The page '
            'exists because the original recording does; nothing is published for it '
            'yet.</p>'
        )
    else:
        stage.append(
            '<p class="links unpublished"><span class="mark mark-missing">'
            '<span class="dot" aria-hidden="true"></span>No recording</span> '
            '&mdash; this sitting is known from the catalogues, but no recording '
            'of it is published and none is listed in the audio archive.</p>')

    blocks = parse_vtt(job["vtt"]) if job else []
    passages = (job or {}).get("passages") or []

    # With a timed transcript the two belong together: the line being spoken is
    # highlighted, and scrolling to read it should not carry the recording off
    # the screen. Everything above stays full width.
    if blocks:
        bits.append('<div class="watch">')
        bits.append('<div class="stage">' + "\n".join(stage) + '</div>')
    else:
        bits.extend(stage)

    if blocks:
        bits.append('<section class="transcript" id="transcript">')
        bits.append('<h2>Transcript</h2>')
        bits.append(
            '<p class="note">Transcribed automatically and lightly corrected. '
            'The recording is the authority where they differ.</p>'
        )
        if passages:
            names = ", ".join(sorted({p.get("title", "") for p in passages if p.get("title")}))
            bits.append(
                f'<p class="note">Recitations restored to their canonical wording: {e(names)}.</p>'
            )
        bits.append('<div class="blocks">')
        for b in blocks:
            bits.append(
                # The id lets a search result land on the moment itself.
                f'<p class="block" id="t{int(b["start"])}" '
                f'data-start="{b["start"]:.2f}" data-end="{b["end"]:.2f}">'
                f'<span class="t">{fmt_hms(b["start"])}</span>'
                f'<span class="w">{e(b["text"]).replace(chr(10), "<br>")}</span></p>'
            )
        bits.append("</div></section></div>")
    elif job and job["transcript"].exists():
        text = job["transcript"].read_text(encoding="utf-8", errors="replace")
        bits.append('<section class="transcript"><h2>Transcript</h2><div class="blocks">')
        for para in [p for p in text.split("\n") if p.strip()]:
            bits.append(f'<p class="block"><span class="w">{e(para)}</span></p>')
        bits.append("</div></section>")
    else:
        bits.append(
            '<section class="transcript"><h2>Transcript</h2>'
            '<p class="note">Not transcribed yet. The recording is available above.</p></section>'
        )

    # A correction says so on the page it corrects. Nothing about the archive
    # is changed silently, and a reader who knows better than the catalogue
    # should be able to see what was decided and on what grounds.
    fixed = s.get("corrected")
    if fixed and fixed.get("why"):
        when = f' ({e(fixed["when"])})' if fixed.get("when") else ""
        bits.append(f'<p class="corrected">Corrected by hand{when}: '
                    f'{e(fixed["why"])}</p>')

    bits.append(fix_form(s))
    bits.append("</article>")
    return shell(title, "\n".join(bits), depth=depth, wide=bool(blocks),
                 description=f"{scripture}. {pretty_date(date)}. {reference}".strip(),
                 trail=[("Avlokan", "../index.html"),
                        (scripture, f"../texts/{slug(scripture)}.html"),
                        (pretty_date(date), "")])


KNOWN_TEXTS: list[str] = []


def fix_form(s: dict[str, Any]) -> str:
    """The correction form, on every discourse page and hidden on all of them.

    Hidden rather than absent because the moment you notice an entry is wrong
    is the moment you are reading it, and anything that makes you leave the
    page to write the correction down somewhere else is a correction that does
    not get made. Shift+E opens it, or `?edit` on the address for a phone.

    It is never shown to a reader. Published, it is a few hundred bytes of
    inert markup on a memorial site; locally, `./edit.sh` answers the form and
    writes the correction to `avlokan/sitting_corrections.json`. Away from
    that server it puts the same JSON on the clipboard instead, so a mistake
    spotted on a television can still be captured where it was seen.
    """
    fix = s.get("corrected") or {}
    part = s.get("part_no")
    return f"""<form class="fix" hidden data-key="{e(sitting_key(s))}"
      data-slug="{e(sitting_slug(s))}" data-date="{e(s["date"])}">
<h2>Correct this entry</h2>
<p class="note">What the catalogues say about this sitting. The recording is
the authority; this is the record of it.</p>
<label>Which text
  <input name="scripture" list="known-texts" value="{e(s["scripture"])}"></label>
<datalist id="known-texts">{"".join(f'<option value="{e(n)}">' for n in KNOWN_TEXTS)}</datalist>
<label>What it cites
  <input name="reference" value="{e(shown_reference(s, s["scripture"]))}"
         placeholder="Patrank 466"></label>
<label>Part of the series
  <input name="part_no" type="number" min="1" max="200"
         value="{part if part is not None else ""}"></label>
<label>Why this is wrong
  <textarea name="why" rows="3" required
    placeholder="The video title says Drashti ke Nidhan; the audio list subject was paired with the wrong sitting."
    >{e(fix.get("why", ""))}</textarea></label>
<p class="links"><button type="submit">Save correction</button>
  <button type="button" class="cancel">Close</button></p>
<p class="said" role="status"></p>
</form>"""


def step_label(s: dict[str, Any]) -> str:
    """A short name for one sitting inside its series."""
    if s.get("part_no") is not None:
        return f'Part {s["part_no"]}'
    return pretty_date(s["date"])


def says_nothing(reference: str, name: str) -> bool:
    """Is this reference just the text's own name again?

    Compared loosely, because the audio list carries typos — one row reads
    "Soiah Karan Bhavna" — and an exact match would print the text's own name
    back at the reader on a page already titled with it.
    """
    a, b = fold(reference), fold(name)
    if not a:
        return True
    return a == b or SequenceMatcher(None, a, b).ratio() > 0.85


def shown_reference(s: dict[str, Any], name: str) -> str:
    """What to print in the reference column, and what not to.

    What a catalogue actually recorded is never overwritten, only ever
    supplied where it said nothing. `reference_label` reads a single citation
    off whichever source names one first, which is right for a row that cites
    nothing and wrong for one that cites three: "Gatha 45, 46, 22" is a
    sitting on three gathas, and rewriting it to "Gatha 45" would quietly
    discard two of them. It also lets the video title's wording win over the
    audio list's, which turns "Atma Khyati Shloka 3" into "Gatha 3" — a
    contradiction of the source rather than a tidying of it.

    So: keep the recorded reference whenever there is one, and fall back to
    the derived label for the 232 sittings that would otherwise show an empty
    column. Where the recorded wording is wrong rather than merely untidy,
    that is a judgement about the text, and it belongs in the correction file
    where it can carry a reason — see `apply_corrections`.
    """
    recorded = s["reference"] or ""
    if not says_nothing(recorded, name):
        return recorded[:52]
    return reference_label(s)


REFUSED: set[str] = set(read_json(CONFIG / "embed_refused.json", {}).get("ids", []))


def availability(s: dict[str, Any]) -> tuple[str, str]:
    """What a reader can actually do with this sitting, and what to call it.

    Three states, and the middle one is the point: the audio list records
    1,229 recordings, most of which have never been published. Those sittings
    are not lost — the tape exists — but there is nothing here to listen to,
    and a reader deserves to be told which is which before clicking.
    """
    if s["youtube_id"]:
        if s["youtube_id"] in REFUSED:
            return "offsite", "On YouTube only"
        return "video", "Video"
    if s["audio"]:
        return "unpublished", "Recorded, not published"
    return "missing", "No recording"


def mark(s: dict[str, Any]) -> str:
    state, label = availability(s)
    extra = ' &middot; transcript' if s["job"] else ""
    return (f'<span class="mark mark-{state}"><span class="dot" aria-hidden="true">'
            f'</span>{label}</span>{extra}')


REF_WORDS = ("Patrank", "Patra", "Gatha", "Bol", "Sloka", "Adhikar", "Question", "Q")

# What a bare number means in each text. The audio list often records only
# "Srimad Rajchandra 236", and in that text 236 is a letter — a patrank.
REF_WORD_FOR = {
    "Shrimad Rajchandra Vachanamrut": "Patrank",
    "Shri Dravya Drushti Prakash":    "Patra",
    "Dravya Drashti Jineshwar":       "Bol",
    "Shri Samaysar":                  "Gatha",
    "Shri Samaysar Kalash":           "Kalash",
    "Natak Samaysar":                 "Gatha",
    "Benshri ke Vachanamrut":         "Bol",
    "Swanubhuti Darshan":             "Question",
    "Gyangoshti":                     "Question",
    "Moksh Marg Prakashak":           "Adhikar",
    "Shri Ratnakaranda Shravakachar": "Adhikar",
    "Sahajanand Patrasudha":          "Patra",
    "Adhyatma Ganga":                 "Bol",
    "Drashti ke Nidhan":              "Bol",
    "Shri Parmagamsar":               "Bol",
    "Prayojan Siddhi":                "Bol",
}


def reference_label(s: dict[str, Any]) -> str:
    """`Patrank 108` — the place in the text, without the text's own name.

    A heading on the Shrimad Rajchandra Vachanamrut page does not need to say
    "Shrimad Rajchandra Vachanamrut" again; it needs to say which letter.
    """
    words = "|".join(REF_WORDS)
    for text in (s["published"].get("title") or "", s.get("reference") or "",
                 s["audio"].get("subject") or ""):
        hit = re.search(rf"\b({words})\b\.?\s*(\d{{1,4}})", text, re.I)
        if hit:
            word = hit.group(1).title()
            return f'{"Question" if word == "Q" else word} {hit.group(2)}'
    ref = reference_number(s)
    if not ref:
        return ""
    word = REF_WORD_FOR.get(s["scripture"])
    return f"{word} {ref}" if word else f"Number {ref}"


def run_heading(run: list[dict[str, Any]]) -> str:
    for s in run:
        label = reference_label(s)
        if label:
            return label
    name = series_name(run[0])
    return name if name != run[0]["scripture"] else ""


def series_name(s: dict[str, Any]) -> str:
    """What to call the run, for a heading."""
    if s.get("run_name"):
        return s["run_name"]
    if s.get("series"):
        return s["series"]
    ref = reference_number(s)
    return f'{s["scripture"]} {ref}' if ref else s["scripture"]


def sitting_row(s: dict[str, Any], name: str) -> str:
    pub = s["published"]
    when = pretty_date(s["date"])
    if s["of"] > 1:
        when += f' <span class="muted">&middot; {s["part"]} of {s["of"]}</span>'
    part = f'{s["part_no"]}' if s["part_no"] is not None else ""
    ref = shown_reference(s, name)
    return (f'<tr><td class="pt">{e(part)}</td>'
            f'<td><a href="../d/{sitting_slug(s)}.html">{when}</a></td>'
            f'<td>{e(ref)}</td>'
            f'<td>{e(pub.get("minutes", "") and str(pub["minutes"]) + " min")}</td>'
            f'<td>{mark(s)}</td></tr>')


def listing(rows: list[str]) -> str:
    return ('<table class="listing"><thead><tr><th>Part</th><th>Date</th>'
            '<th>Reference</th><th>Length</th><th>Recording</th>'
            '</tr></thead><tbody>' + "\n".join(rows) + "</tbody></table>")


def group_into_runs(group: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The sittings in the order they happened, split where the run changes."""
    runs: list[list[dict[str, Any]]] = []
    for s in group:
        run = s.get("run")
        key = id(run) if run is not None and len(run) > 1 else None
        if runs and runs[-1][0].get("_key") == key and key is not None:
            runs[-1].append(s)
        else:
            s = dict(s) if False else s
            runs.append([s])
        runs[-1][0]["_key"] = key
    # merge consecutive singletons into one block
    merged: list[list[dict[str, Any]]] = []
    for run in runs:
        if run[0].get("_key") is None and merged and merged[-1][0].get("_key") is None:
            merged[-1].extend(run)
        else:
            merged.append(run)
    return merged


def text_page(name: str, group: list[dict[str, Any]]) -> str:
    """One text, broken into the runs he taught it in.

    Shrimad Rajchandra's Vachanamrut is 352 sittings. As one list that is
    unusable — nobody scrolls 352 rows looking for a letter. He did not teach
    it as one list either: he took a letter and stayed with it for six or
    twenty sittings, then moved on. Those runs are the natural divisions, and
    the archive already knows them from the reference numbers.

    Sittings that belong to no run — Tatva Charcha is 77 separate discussions
    — are gathered at the end rather than given a heading each.
    """
    runs = group_into_runs(group)
    named = [r for r in runs if r[0].get("_key") is not None and run_heading(r)]

    sections, jumps, used = [], [], set()
    loose: list[dict[str, Any]] = []
    for run in runs:
        label = run_heading(run) if run[0].get("_key") is not None else ""
        if not label or len(run) < 2:
            loose.extend(run)
            continue
        anchor = slug(label)
        n = 2
        while anchor in used:
            anchor, n = f"{slug(label)}-{n}", n + 1
        used.add(anchor)
        years = sorted({x["date"][:4] for x in run})
        span = years[0] if len(years) == 1 else f"{years[0]}–{years[-1]}"
        jumps.append({"label": label, "anchor": anchor, "span": span,
                      "n": int(re.search(r"(\d+)", label).group(1))
                           if re.search(r"(\d+)", label) else 0,
                      "word": label.rsplit(" ", 1)[0] if " " in label else label})
        sections.append(
            f'<section class="run-block"><h2 id="{anchor}">{e(label)}'
            f'<span class="muted"> &middot; {len(run)} sittings &middot; {e(span)}'
            f'</span></h2>'
            + listing([sitting_row(x, name) for x in run]) + '</section>')

    if loose:
        loose.sort(key=lambda x: (x["date"], x["part"]))
        heading = ("Other sittings" if sections else "")
        sections.append(
            (f'<section class="run-block"><h2 id="other">{heading}'
             f'<span class="muted"> &middot; {len(loose)} sittings</span></h2>'
             if heading else '<section class="run-block">')
            + listing([sitting_row(x, name) for x in loose]) + '</section>')

    days = len({s["date"] for s in group})
    word = "sitting" if len(group) == 1 else "sittings"
    count = (f"{len(group)} {word}" if days == len(group)
             else f"{len(group)} {word} across {days} days")
    if named:
        count += f", in {len(named)} runs"
    art = cover(name, depth=1)
    head = (f'<div class="text-head">'
            + (f'<img class="cover" src="{e(art)}" alt="Cover of {e(name)}" '
               f'width="780" height="1080">' if art else "")
            + f'<div><h1>{e(name)}</h1><p class="meta">{count}</p></div></div>')

    tally = Counter(availability(x)[0] for x in group)
    legend = ('<ul class="legend">'
              + "".join(f'<li><span class="mark mark-{k}">'
                        f'<span class="dot" aria-hidden="true"></span>{n} {t}</span></li>'
                        for k, t in (("video", "published"),
                                     ("offsite", "on YouTube only"),
                                     ("unpublished", "recorded, not published"),
                                     ("missing", "no recording"))
                        if (n := tally.get(k)))
              + "</ul>")
    # The index is ordered by number, while the page itself stays in the order
    # he taught. Someone looking for a letter knows its number, not its date.
    # He came back to the same letter years apart, so where a number appears
    # more than once the years tell them apart.
    counts = Counter(j["label"] for j in jumps)
    chips = []
    for j in sorted(jumps, key=lambda j: (j["word"], j["n"])):
        shown = j["label"].rsplit(" ", 1)[-1] if " " in j["label"] else j["label"]
        if counts[j["label"]] > 1:
            shown += f' <span class="yr">{j["span"]}</span>'
        chips.append(f'<a href="#{j["anchor"]}" title="{e(j["label"])}, '
                     f'{e(j["span"])}">{shown}</a>')
    word = jumps[0]["word"] if jumps and len(set(j["word"] for j in jumps)) == 1 else ""
    index = (f'<nav class="jump" aria-label="Runs in this text">'
             + (f'<span class="jump-label">{e(word)}</span>' if word else "")
             + f'{" ".join(chips)}</nav>' if len(jumps) > 1 else "")

    return shell(f"{name} — {SITE_TITLE}", head + legend + index + "".join(sections),
                 depth=1, trail=[("Avlokan", "../index.html"), (name, "")])


def index_page(groups: dict[str, list[dict[str, Any]]]) -> str:
    total = sum(len(v) for v in groups.values())
    done = sum(1 for v in groups.values() for s in v if s["job"])
    cards = []
    for name, entries in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        n_t = sum(1 for s in entries if s["job"])
        years = sorted({s["date"][:4] for s in entries if s["date"][:4].isdigit()})
        span = f"{years[0]}–{years[-1]}" if len(years) > 1 else (years[0] if years else "")
        art = cover(name)
        image = (f'<img class="cover" src="{e(art)}" alt="" width="780" height="1080" '
                 f'loading="lazy">' if art else '<span class="cover cover-none"></span>')
        cards.append(
            f'<li><a href="texts/{slug(name)}.html">{image}'
            f'<span class="name">{e(name)}</span>'
            f'<span class="sub">{len(entries)} '
            f'{"sitting" if len(entries) == 1 else "sittings"} &middot; {e(span)}'
            + (f'<br>{n_t} transcribed' if n_t else '') +
            f'</span></a></li>'
        )
    # Empty, and hidden, until the browser fills it from what this person has
    # actually listened to. There is no account and nothing is sent anywhere;
    # a reader who has not played anything, or who clears their browser, sees
    # the page exactly as it has always been.
    body = (
        f'<h1>{e(SITE_TAGLINE)}</h1>'
        f'<p class="lede">{e(SITE_DESC)}</p>'
        f'<p class="meta">{total} sittings &middot; {done} transcribed</p>'
        '<section id="recent" class="recent" hidden aria-labelledby="recent-h">'
        '<h2 id="recent-h">Where you left off</h2>'
        '<ol></ol>'
        '<p class="links"><button type="button" class="forget">Forget these</button></p>'
        '</section>'
        f'<ul class="texts">{"".join(cards)}</ul>'
    )
    return shell(SITE_TITLE, body, depth=0)



# --------------------------------------------------------------------------
# about page
# --------------------------------------------------------------------------

def render_prose(text: str) -> str:
    """A deliberately small Markdown subset: headings, paragraphs, quotes, rules.

    Small enough to read in one sitting and to still work in ten years. The
    about text lives in avlokan/about.md so it can be edited as prose.
    """
    out: list[str] = []
    para: list[str] = []
    quote: list[str] = []

    def flush_para():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    def flush_quote():
        if quote:
            parts: list[str] = []
            run: list[str] = []
            for line in quote:
                if line:
                    run.append(line)
                elif run:
                    parts.append(" ".join(run)); run = []
            if run:
                parts.append(" ".join(run))
            body = "".join(f"<p>{inline(part)}</p>" for part in parts)
            if body:
                out.append(f"<blockquote>{body}</blockquote>")
            quote.clear()

    def inline(t: str) -> str:
        t = e(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
        return t

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip() == ">":
            # a bare ">" separates the original from its rendering inside one
            # quote; the empty entry becomes a paragraph break, not a stray ">"
            flush_para()
            quote.append("")
        elif line.startswith("> "):
            flush_para()
            quote.append(line[2:].strip())
        elif not line.strip():
            flush_para(); flush_quote()
        elif line.startswith("## "):
            flush_para(); flush_quote()
            out.append(f"<h2>{inline(line[3:].strip())}</h2>")
        elif line.startswith("# "):
            flush_para(); flush_quote()
            out.append(f"<h1>{inline(line[2:].strip())}</h1>")
        else:
            flush_quote()
            para.append(line.strip())
    flush_para(); flush_quote()
    return "\n".join(out)


def search_index(all_sittings: list[dict[str, Any]], out_dir: Path) -> dict[str, int]:
    """Two files: what every sitting is, and what was said in the few we have.

    They are separate because they grow at wildly different rates. The first
    describes all 1,337 sittings in a couple of hundred kilobytes and is
    always loaded. The second holds the words, and one sitting is 7,500 of
    them — the whole archive transcribed would be 10 million words and 60MB,
    which no browser should be asked to swallow to answer one query. So it is
    fetched only when someone asks to search inside the transcripts, and when
    it outgrows that it can be split by text without changing anything here.
    """
    meta = []
    for s in sorted(all_sittings, key=lambda x: x["date"]):
        state, _ = availability(s)
        meta.append({
            "d": s["date"],
            "t": s["scripture"],
            "r": reference_label(s) or "",
            "n": (s["published"].get("title") or "")[:110],
            "u": sitting_slug(s),
            "a": state,
            "x": 1 if s["job"] else 0,
        })
    book = []
    for item in read_json(CONFIG / "book.json", []):
        book.append({"n": item["n"], "g": item["gujarati"],
                     "e": item.get("english") or ""})

    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "assets" / "search.json").write_text(
        json.dumps({"sittings": meta, "book": book}, ensure_ascii=False,
                   separators=(",", ":")), encoding="utf-8")

    words = []
    for s in all_sittings:
        if not s["job"]:
            continue
        blocks = parse_vtt(s["job"]["vtt"])
        if not blocks:
            continue
        words.append({
            "u": sitting_slug(s), "d": s["date"], "t": s["scripture"],
            "b": [[round(b["start"]), b["text"]] for b in blocks],
        })
    (out_dir / "assets" / "transcripts.json").write_text(
        json.dumps(words, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    return {"sittings": len(meta), "aphorisms": len(book), "transcripts": len(words)}


def search_page() -> str:
    body = """<h1>Search</h1>
<p class="lede">Every sitting by text, letter, date or title, and the book by
its words. Tick <em>inside the transcripts</em> to search what he actually
said in the sittings that have been transcribed, and land on the moment he
said it.</p>
<form class="find" role="search" onsubmit="return false">
  <input type="search" id="q" autocomplete="off" autofocus
         placeholder="Patrank 254, or avlokan, or 1999, or a phrase">
  <label><input type="checkbox" id="deep"> inside the transcripts</label>
</form>
<p id="count" class="muted" role="status"></p>
<div id="results"></div>
<script src="assets/search.js" defer></script>"""
    return shell(f"Search — {SITE_TITLE}", body, depth=0, script="none",
                 description="Search the discourses of Shri Devchand bhai Shah.",
                 trail=[("Avlokan", "index.html"), ("Search", "")])


def embed_check_page(all_sittings: list[dict[str, Any]]) -> str:
    """A page that finds every recording YouTube will not let the site show.

    Nothing in a video's public settings says whether it can be embedded —
    the ones that refuse report `playableInEmbed: true` like the rest — so the
    only way to know is to ask the player and listen for the refusal. This
    walks the whole archive doing that, and lists what it finds.

    It is for the archive's keeper, not its readers: linked from nowhere and
    marked noindex.
    """
    videos = []
    seen = set()
    for s in sorted(all_sittings, key=lambda x: x["date"]):
        vid = s.get("youtube_id")
        if vid and vid not in seen:
            seen.add(vid)
            videos.append({"id": vid, "date": s["date"], "text": s["scripture"],
                           "title": (s["published"].get("title") or "")[:90],
                           "page": sitting_slug(s)})
    data = json.dumps(videos, ensure_ascii=False)
    body = f"""<h1>Which recordings will not embed</h1>
<p class="lede">Nothing in a video's public settings distinguishes these; the
only way to find them is to ask the player and listen for the refusal. This
checks all {len(videos)} of them, a few at a time. Leave it running.</p>
<p class="links"><button type="button" id="go">Start</button>
<button type="button" id="again">Start over</button>
<span id="count" class="muted"></span></p>
<div id="stage" hidden></div>
<h2>Refused</h2>
<p class="note">Look these up in YouTube Studio under Content — the
Restrictions column is where a copyright claim or an embedding block shows.</p>
<ol id="bad" class="listing-plain"></ol>
<h2>By text</h2>
<p class="note">If the cause is a claim on the closing stuti, the refusals
should cluster by text — the recordings that end with one version of it will
be refused and the rest will not.</p>
<table class="listing"><thead><tr><th>Text</th><th>Refused</th><th>Checked</th>
<th>Share</th></tr></thead><tbody id="bytext"></tbody></table>
<p class="links"><button type="button" id="copy">Copy the refused ids</button>
<span id="copied" class="muted"></span></p>
<h2 id="done" hidden>Finished</h2>
<p id="summary" class="muted"></p>
<script>var VIDEOS = {data};</script>
<script src="assets/check-embeds.js" defer></script>"""
    return shell("Embed check — " + SITE_TITLE, body, depth=0, script="none",
                 head_extra='<meta name="robots" content="noindex,nofollow">',
                 trail=[("Avlokan", "index.html"), ("Embed check", "")])


def gallery_page() -> str:
    """The photographs from the old site.

    They arrive named `64.jpeg` and `31 2.jpeg`, so there is nothing to caption
    them with. They are published anyway, unlabelled, because a photograph of
    him teaching is worth more than a caption and someone who was there can
    write one later.
    """
    # His own scans first — they are much better than the small copies the old
    # site was serving — then the ones rescued from it.
    shots = sorted(IMAGES.glob("photo-*.jpg")) + sorted(IMAGES.glob("gallery-*.jpg"))
    if not shots:
        return ""
    cells = "".join(
        f'<li><img src="assets/covers/{e(p.name)}" alt="" loading="lazy"></li>'
        for p in shots)
    body = (f'<h1>Photographs</h1>'
            f'<p class="lede">{len(shots)} pictures of him — teaching, reading, '
            f'in conversation. They came with no captions; if you know when or '
            f'where one was taken, it is worth writing down.</p>'
            f'<ul class="gallery">{cells}</ul>')
    return shell(f"Photographs — {SITE_TITLE}", body, depth=0, script="none",
                 trail=[("Avlokan", "index.html"), ("Photographs", "")])


def about_page() -> str:
    source = CONFIG / "about.md"
    if not source.exists():
        return ""
    body = render_prose(source.read_text(encoding="utf-8"))
    photo = ""
    if (IMAGES / "photo-05.jpg").exists():
        photo = ('<figure class="portrait">'
                 '<img src="assets/covers/photo-05.jpg" width="1600" height="1024"'
                 ' alt="Shri Devchand bhai Shah teaching outdoors, a book open on a stand">'
                 '<figcaption>Shri Devchand bhai Shah</figcaption></figure>')
    return shell(f"About — {SITE_TITLE}", f'<article class="prose">{photo}{body}</article>',
                 depth=0, description=SITE_DESC, script="none",
                 trail=[("Avlokan", "index.html"), ("About", "")])


# --------------------------------------------------------------------------
# the book
# --------------------------------------------------------------------------

HEADING = re.compile(r"^#{1,6}\s*")


def render_gujarati(text: str) -> str:
    """The aphorism as it is laid out on the page, not as one block of prose.

    Two things were being flattened. Three aphorisms carry a heading, which
    the extraction marked the way markdown does, so `### વિકાસક્રમ` was being
    printed with its hashes. And a single line break inside a paragraph was
    being dropped, which runs a numbered list — aphorism 64 is one — into a
    single unreadable line. Blank lines separate paragraphs; a line break
    inside one is a line break.
    """
    out = []
    for block in text.split("\n\n"):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        if HEADING.match(lines[0]):
            out.append(f'<h3>{e(HEADING.sub("", lines[0]).strip())}</h3>')
            lines = lines[1:]
        if lines:
            out.append("<p>" + "<br>".join(e(l.strip()) for l in lines) + "</p>")
    return "".join(out)


def aphorism_html(item: dict[str, Any], *, linked: bool = True, depth: int = 1) -> str:
    up = "../" * depth
    n = item["n"]
    label = (f'<a class="n" href="{up}book/{n}.html">Aphorism {n}</a>' if linked
             else f'<span class="n">Aphorism {n}</span>')
    guj = render_gujarati(item["gujarati"])
    bits = [f'<article class="aphorism" id="a{n}">', label,
            f'<div class="guj" lang="gu">{guj}</div>']
    if item.get("english"):
        eng = "".join(f"<p>{inline_em(par)}</p>"
                      for par in item["english"].split("\n\n") if par.strip())
        bits.append(f'<div class="eng" lang="en">{eng}</div>')
        # A single aphorism page is what a search result lands on, so the
        # caveat that sits once at the top of the index has to be repeated
        # here or the rendering reads as authoritative.
        if not linked and item.get("english_by"):
            bits.append(f'<p class="pending">English: {e(item["english_by"])}. '
                        f'The Gujarati is the text.</p>')
    else:
        bits.append('<p class="pending">No English rendering yet.</p>')
    bits.append("</article>")
    return "\n".join(bits)


def inline_em(text: str) -> str:
    out = e(text)
    return re.sub(r"\*(?!\s)(.+?)(?<!\s)\*", r"<em>\1</em>", out)


def opening_words(text: str, limit: int = 40) -> str:
    """Enough of an aphorism to recognise it by in the index.

    A column of bare numbers is a table of contents for nobody: 8, 12, 13 says
    nothing about which one is the one you were reading. The first few words
    do, and they are the words he chose to open with.
    """
    flat = " ".join(HEADING.sub("", text).split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,.-") + "…"


def book_index(items: list[dict[str, Any]], here: int | None = None) -> str:
    """The list of every aphorism, as real links.

    Written into every page of the book rather than built by script, because
    it is the only way through a hundred and fourteen of them and it has to
    work where nothing runs. On the index it points within the page; on a
    single aphorism it points at the other pages.
    """
    rows = []
    for item in items:
        n = item["n"]
        target = f"#a{n}" if here is None else f"{n}.html"
        current = ' aria-current="true"' if here == n else ""
        rows.append(
            f'<li><a href="{target}"{current}><span class="num">{n}</span>'
            f'<span class="open" lang="gu">{e(opening_words(item["gujarati"]))}</span>'
            f'</a></li>')
    return ('<nav class="book-index" aria-label="All aphorisms">'
            f'<h2>All {len(items)} aphorisms</h2>'
            f'<ol>{"".join(rows)}</ol></nav>')


def book_review_page(items: list[dict[str, Any]]) -> str:
    """The whole book laid out for reading and correcting, Gujarati beside English.

    Not part of the published site in any meaningful sense: linked from
    nowhere, marked noindex, and inert without the local editor answering it.
    It exists because a hundred and fourteen renderings have to be read
    against their Gujarati by the one person who can read both, and doing that
    through a hundred and fourteen separate pages — or, worse, through a
    spreadsheet — is how a review gets abandoned at aphorism thirty.

    Everything is on one page, in the book's own order, so a session's work is
    visible as progress down a single column.
    """
    glossary = read_json(CONFIG / "book_glossary.json", {}).get("terms", [])
    terms = "".join(
        f'<tr><td lang="gu">{e(t["gujarati"])}</td><td><em>{e(t["roman"])}</em></td>'
        f'<td>{e(t["english"])}</td><td class="muted">{e(t.get("gloss", ""))}</td></tr>'
        for t in glossary)

    done = sum(1 for i in items if i.get("english"))
    seen = sum(1 for i in items if (i.get("english_by") or "").startswith("Tejas"))

    rows = []
    for item in items:
        n = item["n"]
        english = item.get("english") or ""
        by = item.get("english_by") or ""
        state = ("yours" if by.startswith("Tejas")
                 else "draft" if english else "none")
        rows.append(
            f'<section class="review" id="r{n}" data-n="{n}" data-state="{state}">'
            f'<h2>Aphorism {n} <span class="state">{state}</span></h2>'
            f'<div class="pair">'
            f'<div class="guj" lang="gu">{render_gujarati(item["gujarati"])}</div>'
            f'<div class="edit">'
            f'<textarea spellcheck="false" aria-label="English for aphorism {n}">'
            f'{e(english)}</textarea>'
            f'<p class="links"><button type="button" class="save">Save</button>'
            f'<span class="said" role="status"></span></p>'
            f'</div></div></section>')

    body = (
        '<div class="review-head">'
        f'<h1>The book, for review</h1>'
        f'<p class="meta"><strong id="tally">{done} of {len(items)}</strong> have English; '
        f'<strong>{seen}</strong> you have been through. '
        'Edit the English and save. Saving writes to <code>avlokan/book.json</code> '
        'and marks the aphorism as yours.</p>'
        '<p class="note">The Gujarati is the text and is not editable here. '
        'This page is not part of the published site: it is linked from nowhere, '
        'and Save does nothing unless <code>./edit.sh</code> is answering it.</p>'
        '<details class="glossary"><summary>The 42 terms, and how each is rendered</summary>'
        '<table><thead><tr><th>Gujarati</th><th>as</th><th>English</th><th>meaning</th></tr>'
        f'</thead><tbody>{terms}</tbody></table></details>'
        '</div>'
        + "\n".join(rows))
    return shell("The book, for review", body, depth=1, reading=True,
                 head_extra='<meta name="robots" content="noindex,nofollow">',
                 description="Local review of the English renderings.",
                 trail=[("Avlokan", "../index.html"), ("The book", "index.html"),
                        ("Review", "")])


def book_pages(out_dir: Path) -> int:
    items = read_json(CONFIG / "book.json", [])
    if not items:
        return 0
    (out_dir / "book").mkdir(parents=True, exist_ok=True)
    done = sum(1 for i in items if i.get("english"))

    front = (CONFIG / "book_front.txt")
    titlepage = ""
    if front.exists():
        lines = [l.strip() for l in front.read_text(encoding="utf-8").splitlines() if l.strip()]
        # The title repeats three times on the printed title page; once is enough.
        seen_lines, keep = set(), []
        for line in lines:
            if line not in seen_lines:
                seen_lines.add(line)
                keep.append(line)
        titlepage = ('<div class="titlepage" lang="gu">'
                     + "".join(f"<p>{e(l)}</p>" for l in keep) + "</div>")

    intro = (
        titlepage +
        f'<div class="book-head">'
        f'<h1>Avlokan</h1>'
        f'<p class="meta">આત્મ નિરીક્ષણની અનૂઠી કળા &middot; '
        f'Shri Devchand bhai Shah &middot; {len(items)} aphorisms</p>'
        f'<p class="note">The Gujarati is the text. English renderings are working '
        f'translations offered to open a door, not to stand in its place; technical '
        f'terms are kept rather than replaced with approximations. '
        f'{done} of {len(items)} have one so far. Each aphorism has its own page, so a '
        f'correction to one changes nothing else.</p></div>'
    )
    body = ('<div class="book-layout">' + book_index(items)
            + '<div class="book-body">'
            + intro + "\n".join(aphorism_html(i, depth=1) for i in items)
            + '</div></div>')
    (out_dir / "book" / "index.html").write_text(
        shell(f"Avlokan — the book", body, depth=1, reading=True,
              description="Avlokan — the unique art of self-observation, by Shri Devchand bhai Shah.",
              trail=[("Avlokan", "../index.html"), ("The book", "")]),
        encoding="utf-8")

    (out_dir / "book" / "review.html").write_text(
        book_review_page(items), encoding="utf-8")

    for idx, item in enumerate(items):
        nav = []
        if idx:
            nav.append(f'<a href="{items[idx-1]["n"]}.html">&larr; {items[idx-1]["n"]}</a>')
        nav.append('<a href="index.html">All aphorisms</a>')
        if idx + 1 < len(items):
            nav.append(f'<a href="{items[idx+1]["n"]}.html">{items[idx+1]["n"]} &rarr;</a>')
        page = ('<div class="book-layout">' + book_index(items, here=item["n"])
                + '<div class="book-body">'
                + aphorism_html(item, linked=False, depth=1)
                + f'<p class="links">{" &middot; ".join(nav)}</p>'
                + '</div></div>')
        (out_dir / "book" / f'{item["n"]}.html').write_text(
            shell(f'Avlokan, aphorism {item["n"]}', page, depth=1, reading=True,
                  description=opening_words(item["gujarati"], 150),
                  trail=[("Avlokan", "../index.html"), ("The book", "index.html"),
                         (f'Aphorism {item["n"]}', "")]),
            encoding="utf-8")
    return len(items)

# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------

CSS = """/* ==========================================================================
   Avlokan — design tokens
   --------------------------------------------------------------------------
   Everything visual is driven by the custom properties in :root. To restyle
   the whole archive, edit this block and nothing else — every rule below
   consumes a token rather than a literal value. No build step, no toolchain,
   nothing to reinstall in five years.
   ========================================================================== */
:root{
  /* colour */
  --c-ink:#1a1a18;          /* body text                */
  --c-ink-soft:#3a3a35;     /* quotations               */
  --c-muted:#6b6b64;        /* secondary text           */
  --c-line:#e3e3dc;         /* rules and borders        */
  --c-bg:#fbfbf9;           /* page                     */
  --c-surface:#ffffff;      /* raised areas             */
  --c-accent:#2f5fa8;       /* links, active state      */
  --c-accent-ink:#ffffff;   /* text on accent           */
  --c-mark:#fbeec4;         /* the line now playing     */
  --c-hover:#f2f2ec;
  --c-letterbox:#000;       /* behind the video frame   */
  --c-have:#2f7a4f;         /* published and playable   */
  --c-unpublished:#b07d2b;  /* recorded, not published  */
  --c-missing:#b0b0a8;      /* no recording at all      */
  --c-offsite:#6b6b64;      /* published, but only YouTube will play it */

  /* type */
  --font-body:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  --font-indic:"Noto Sans Gujarati","Noto Sans Devanagari",var(--font-body);
  --size-xs:.75rem; --size-sm:.85rem; --size-md:.95rem; --size-base:1.0625rem;
  --size-lg:1.25rem; --size-xl:1.5rem;
  /* Gujarati and Devanagari carry more strokes in the same square than Latin
     does, and matras sit above and below the line. Set at the size the Latin
     text is, they read smaller than it and the conjuncts close up. The book
     is a book — it is read continuously, not glanced at — so it gets its own
     step, a little larger again. */
  --size-indic:1.25rem; --size-indic-book:1.4rem;
  --leading-tight:1.3; --leading-body:1.75; --leading-indic:1.9;
  --weight-normal:400; --weight-medium:500; --weight-bold:600;
  --track-caps:.06em;

  /* space — one scale, used everywhere */
  --s-1:.25rem; --s-2:.5rem; --s-3:.75rem; --s-4:1rem;
  --s-5:1.5rem; --s-6:2rem; --s-7:3rem; --s-8:5rem;

  /* shape and layout */
  --radius:3px;
  --measure:46rem;          /* reading column           */
  --measure-watch:76rem;    /* wider, for a transcript beside its video */
  --measure-book:68rem;     /* the book, beside its index */
  --w-index:15rem;          /* the book's index column   */
  --measure-prose:34rem;    /* narrower, for continuous prose */
  --border:1px solid var(--c-line);
  --rule:3px;               /* the thick left-hand marker */
  --w-time:4rem;            /* timestamp gutter         */
  --w-number:2.5rem;        /* sitting-count column     */
  --w-cover:10rem;          /* a book cover             */
  --h-sticky:4.5rem;        /* masthead, for scroll-margin */
  --underline-offset:2px;
}

/* ---------- base ---------- */
*{box-sizing:border-box}
body{margin:0;background:var(--c-bg);color:var(--c-ink);
 font:var(--weight-normal) var(--size-base)/var(--leading-body) var(--font-body)}
main{max-width:var(--measure);margin:0 auto;padding:var(--s-5) var(--s-4) var(--s-8)}
main.watching{max-width:var(--measure-watch)}
a{color:var(--c-accent)}
h1{font-size:var(--size-xl);line-height:var(--leading-tight);margin:var(--s-5) 0 var(--s-1)}
h2{font-size:var(--size-lg);margin:var(--s-6) 0 var(--s-2)}
:focus-visible{outline:2px solid var(--c-accent);outline-offset:2px}

/* ---------- text roles ---------- */
.tagline,.meta,.sub,.muted,.note{color:var(--c-muted);font-size:var(--size-sm)}
.lede{font-size:var(--size-lg);line-height:var(--leading-body)}
.indic{font-family:var(--font-indic);line-height:var(--leading-indic)}

/* ---------- masthead, crumbs, footer ---------- */
.masthead{display:flex;gap:var(--s-3);align-items:baseline;flex-wrap:wrap;
 padding:var(--s-4);border-bottom:var(--border);max-width:var(--measure);margin:0 auto}
.wordmark{font-weight:var(--weight-bold);letter-spacing:var(--track-caps);
 text-transform:uppercase;text-decoration:none;color:var(--c-ink)}
.navlink{margin-left:auto;font-size:var(--size-sm)}
.navlink+.navlink{margin-left:var(--s-3)}
.crumbs{margin:var(--s-3) 0 var(--s-1);font-size:var(--size-sm)}
.crumbs ol{list-style:none;display:flex;flex-wrap:wrap;gap:var(--s-1);margin:0;padding:0}
.crumbs li+li::before{content:"/";color:var(--c-line);margin-right:var(--s-1)}
.crumbs li[aria-current]{color:var(--c-muted)}
.crumbs a{text-decoration:none}
.crumbs a:hover{text-decoration:underline}
footer{max-width:var(--measure);margin:0 auto;padding:var(--s-5) var(--s-4) var(--s-7);
 border-top:var(--border);color:var(--c-muted);font-size:var(--size-sm)}

/* ---------- controls ---------- */
.btn{font:inherit;font-size:var(--size-sm);padding:var(--s-1) var(--s-3);
 border:var(--border);border-radius:var(--radius);background:var(--c-surface);
 color:var(--c-ink);cursor:pointer}
.btn:hover{background:var(--c-hover)}
.btn[aria-pressed=true]{background:var(--c-ink);color:var(--c-accent-ink);border-color:var(--c-ink)}
.toolbar{position:sticky;top:0;z-index:5;display:flex;gap:var(--s-2);flex-wrap:wrap;
 align-items:center;background:var(--c-bg);border-bottom:var(--border);
 padding:var(--s-2) 0;margin-bottom:var(--s-3)}
.toolbar button{font:inherit;font-size:var(--size-sm);padding:var(--s-1) var(--s-3);
 border:var(--border);border-radius:var(--radius);background:var(--c-surface);
 color:var(--c-ink);cursor:pointer}
.toolbar button[aria-pressed=true]{background:var(--c-ink);color:var(--c-accent-ink);
 border-color:var(--c-ink)}

/* ---------- index and listings ---------- */
ul.texts{list-style:none;padding:0;margin:var(--s-5) 0;display:grid;gap:var(--s-5) var(--s-4);
 grid-template-columns:repeat(auto-fill,minmax(var(--w-cover),1fr))}
ul.texts a{display:block;text-decoration:none;color:inherit}
ul.texts .cover{display:block;width:100%;height:auto;aspect-ratio:780/1080;object-fit:cover;
 background:var(--c-hover);border:var(--border);border-radius:var(--radius)}
ul.texts a:hover .cover{border-color:var(--c-accent)}
ul.texts .cover-none{aspect-ratio:780/1080}
ul.texts .name{display:block;margin-top:var(--s-2);color:var(--c-accent);
 line-height:var(--leading-tight)}
ul.texts .sub{display:block;margin-top:var(--s-1);color:var(--c-muted);font-size:var(--size-sm)}
.text-head{display:flex;gap:var(--s-5);align-items:flex-start;margin:var(--s-5) 0 var(--s-4)}
.text-head .cover{flex:none;width:var(--w-cover);height:auto;border:var(--border);
 border-radius:var(--radius)}
.text-head h1{margin-top:0}
nav.series{margin:var(--s-5) 0;padding:var(--s-3) var(--s-4);background:var(--c-surface);
 border:var(--border);border-radius:var(--radius)}
nav.series .meta{margin:0}
nav.series .where{color:var(--c-ink);font-weight:var(--weight-medium)}
nav.series .links{display:flex;justify-content:space-between;gap:var(--s-4);margin:var(--s-2) 0 0}
nav.series .next{margin-left:auto;text-align:right}
nav.series details{margin-top:var(--s-3)}
nav.series summary{cursor:pointer;color:var(--c-muted);font-size:var(--size-sm)}
nav.series ol{margin:var(--s-2) 0 0;padding-left:var(--s-5);font-size:var(--size-sm)}
nav.series li{margin:var(--s-1) 0}
form.find{display:flex;flex-wrap:wrap;gap:var(--s-3);align-items:center;
 margin:var(--s-5) 0 var(--s-3)}
form.find input[type=search]{flex:1;min-width:16rem;font:inherit;padding:var(--s-2) var(--s-3);
 border:var(--border);border-radius:var(--radius);background:var(--c-surface);color:var(--c-ink)}
form.find label{font-size:var(--size-sm);color:var(--c-muted);display:flex;
 align-items:center;gap:var(--s-2)}
ul.results{list-style:none;padding:0;margin:var(--s-4) 0}
ul.results li{padding:var(--s-3) 0;border-bottom:var(--border)}
ul.results a{text-decoration:none}
ul.results a:hover{text-decoration:underline}
ul.results .guj{font-family:var(--font-indic);line-height:var(--leading-indic);
 font-size:var(--size-indic)}
ul.results div{font-size:var(--size-sm);margin-top:var(--s-1)}
.block:target .w{background:var(--c-mark)}
nav.jump{margin:var(--s-4) 0 var(--s-6);line-height:2.1}
nav.jump a{display:inline-block;padding:var(--s-1) var(--s-3);margin:0 var(--s-1) var(--s-1) 0;
 border:var(--border);border-radius:var(--radius);text-decoration:none;
 font-size:var(--size-sm);color:var(--c-accent);background:var(--c-surface)}
nav.jump a:hover{background:var(--c-hover);border-color:var(--c-accent)}
nav.jump .jump-label{margin-right:var(--s-2);color:var(--c-muted);
 font-size:var(--size-sm);text-transform:uppercase;letter-spacing:var(--track-caps)}
nav.jump .yr{color:var(--c-muted);font-size:var(--size-xs)}
.run-block{margin:var(--s-6) 0}
.run-block h2{scroll-margin-top:var(--s-4);font-size:var(--size-lg);
 padding-bottom:var(--s-2);border-bottom:var(--border)}
.run-block h2 .muted{font-size:var(--size-sm);font-weight:var(--weight-normal)}
/* The player. It sits at the end of <body>, never inside the page, because
   moving an iframe reloads it and that would stop the recording every time you
   turned a page. While you are on its own page it is put exactly over the
   placeholder below; when you leave, it lets go and floats. */
#dock{z-index:20}
#dock .dock-frame{position:absolute;inset:0;background:var(--c-letterbox);
 border-radius:var(--radius);overflow:hidden}
#dock iframe,#dock .dock-frame>div{position:absolute;inset:0;width:100%;height:100%;border:0}
#dock .dock-bar{display:none}
#dock.afloat{position:fixed;right:var(--s-4);bottom:var(--s-4);left:auto;top:auto;
 width:min(26rem,84vw);height:auto;aspect-ratio:16/9;box-shadow:0 6px 28px rgba(0,0,0,.28);
 border-radius:var(--radius);background:var(--c-letterbox)}
#dock.afloat .dock-bar{display:flex;position:absolute;top:0;left:0;right:0;
 align-items:center;gap:var(--s-2);margin:0;padding:var(--s-1) var(--s-2);
 background:rgba(0,0,0,.72);color:#fff;font-size:var(--size-xs);
 border-radius:var(--radius) var(--radius) 0 0;z-index:2}
#dock.afloat .dock-title{color:#fff;text-decoration:none;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap;flex:1}
#dock.afloat .dock-title:hover{text-decoration:underline}
#dock.afloat .dock-close{flex:none;background:transparent;border:0;color:#fff;
 cursor:pointer;font-size:var(--size-base);line-height:1;padding:0 var(--s-1)}
/* The placeholder keeps the space and the sticky behaviour; the player above
   is laid over it. Once playing, its own play button is gone. */
.video.is-playing button.play{display:none}
@media (max-width:40rem){
  #dock.afloat{right:var(--s-2);bottom:var(--s-2);width:min(18rem,72vw)}
}

/* Video and transcript together. The recording stays put while the words
   scroll past it, because following a highlighted line is the whole point and
   scrolling to read used to carry the video off the top of the screen. */
.watch{margin-top:var(--s-5)}
.watch .stage{margin-bottom:var(--s-5)}
@media (min-width:64rem){
  .watch{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr);
   gap:var(--s-6);align-items:start}
  .watch .stage{position:sticky;top:var(--s-4);margin-bottom:0}
  .watch .transcript{margin-top:0}
  .watch .transcript h2{margin-top:0}
}
/* Narrow: the recording pins to the top of the screen and gives up most of
   its height, so there is still room to read. */
@media (max-width:63.99rem){
  .watch .stage{position:sticky;top:0;z-index:5;background:var(--c-bg);
   padding:var(--s-2) 0;margin:0 0 var(--s-4)}
  .watch .stage .video{max-height:34vh;padding-top:min(56.25%,34vh);margin:0}
  .watch .stage .links{margin:var(--s-2) 0 0}
  .block{scroll-margin-top:42vh}
}
.mark{white-space:nowrap}
.mark .dot{display:inline-block;width:.5rem;height:.5rem;border-radius:50%;
 margin-right:var(--s-2);vertical-align:baseline}
.mark-video .dot{background:var(--c-have)}
.mark-unpublished .dot{background:var(--c-unpublished)}
.mark-missing .dot{background:var(--c-missing)}
.mark-offsite .dot{background:var(--c-offsite)}
.mark-missing{color:var(--c-muted)}
p.unpublished{margin:var(--s-4) 0;padding:var(--s-3) var(--s-4);
 background:var(--c-surface);border:var(--border);border-radius:var(--radius);
 font-size:var(--size-sm);color:var(--c-ink)}
.titlepage{text-align:center;font-family:var(--font-indic);
 line-height:var(--leading-indic);margin:var(--s-6) 0;padding-bottom:var(--s-5);
 border-bottom:var(--border);color:var(--c-ink-soft)}
.titlepage p{margin:var(--s-1) 0}
.titlepage p:first-child{font-size:var(--size-lg);color:var(--c-ink)}
ul.gallery{list-style:none;padding:0;margin:var(--s-5) 0;display:grid;gap:var(--s-4);
 grid-template-columns:repeat(auto-fill,minmax(15rem,1fr))}
ul.gallery img{width:100%;height:auto;border:var(--border);border-radius:var(--radius);
 display:block}
ul.legend{list-style:none;display:flex;flex-wrap:wrap;gap:var(--s-4);padding:0;
 margin:var(--s-3) 0 0;font-size:var(--size-sm);color:var(--c-muted)}
table.listing td.pt{text-align:right;color:var(--c-muted);font-variant-numeric:tabular-nums;
 width:var(--w-number)}
figure.portrait{margin:0 0 var(--s-6)}
figure.portrait img{width:100%;height:auto;border-radius:var(--radius)}
figure.portrait figcaption{margin-top:var(--s-2);color:var(--c-muted);
 font-size:var(--size-sm)}
@media (max-width:34rem){.text-head .cover{width:7rem}}
table.listing{width:100%;border-collapse:collapse;font-size:var(--size-sm);margin-top:var(--s-4)}
table.listing th{text-align:left;font-size:var(--size-xs);text-transform:uppercase;
 letter-spacing:var(--track-caps);color:var(--c-muted);border-bottom:var(--border);
 padding:var(--s-1) var(--s-2)}
table.listing td{padding:var(--s-1) var(--s-2);border-bottom:var(--border)}

/* ---------- discourse ---------- */
.video{position:relative;padding-top:56.25%;background:var(--c-letterbox);border-radius:var(--radius);
 overflow:hidden;margin:var(--s-4) 0}
.video iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
/* The play button stands in for the player until it is pressed, so opening a
   page fetches nothing at all — not even a thumbnail. */
.video.elsewhere{padding-top:0;background:var(--c-surface);border:var(--border);
 display:grid;place-content:center;gap:var(--s-2);padding:var(--s-6) var(--s-4);
 text-align:center;color:var(--c-ink)}
.video.elsewhere p{margin:0;max-width:var(--measure-prose)}
button.play{position:absolute;inset:0;width:100%;height:100%;border:0;cursor:pointer;
 background:transparent;display:grid;place-items:center}
button.play:hover .play-mark,button.play:focus-visible .play-mark{background:var(--c-accent)}
.play-mark{width:4.5rem;height:3.2rem;border-radius:var(--radius);background:#5a5a5a;
 display:block;position:relative;transition:background .12s}
.play-mark::after{content:"";position:absolute;top:50%;left:50%;
 transform:translate(-42%,-50%);border-style:solid;border-width:.62rem 0 .62rem 1.05rem;
 border-color:transparent transparent transparent var(--c-accent-ink)}
.links{font-size:var(--size-sm)}
.links a{margin-right:var(--s-4)}
.transcript .note{margin:var(--s-1) 0 var(--s-4)}
.block{display:flex;gap:var(--s-3);margin:0 0 var(--s-3);scroll-margin-top:var(--h-sticky)}
.block .t{flex:none;width:var(--w-time);color:var(--c-muted);font-size:var(--size-sm);
 font-variant-numeric:tabular-nums;padding-top:.35rem}
.block .w{flex:1;font-family:var(--font-indic);line-height:var(--leading-indic);
 font-size:var(--size-indic)}
.block.is-live .w{background:var(--c-mark);box-shadow:0 0 0 var(--s-1) var(--c-mark);
 border-radius:var(--radius)}
.block .t[role=button]{cursor:pointer;color:var(--c-accent);text-decoration:underline;
 text-underline-offset:var(--underline-offset)}
.block.is-marked .w{border-left:var(--rule) solid var(--c-accent);padding-left:var(--s-2)}

/* ---------- prose and quotations ---------- */
.prose{max-width:var(--measure-prose)}
.prose h1{margin-top:var(--s-5)}
.prose blockquote{margin:var(--s-5) 0;padding:0 0 0 var(--s-4);
 border-left:var(--rule) solid var(--c-line);color:var(--c-ink-soft)}
.prose blockquote p{margin:var(--s-1) 0}
.prose blockquote p:first-child{font-family:var(--font-indic);line-height:var(--leading-indic)}
.prose blockquote p:last-child{color:var(--c-muted);font-size:var(--size-md);font-family:var(--font-body)}

/* ---------- the book ---------- */
.aphorism{padding:var(--s-5) 0;border-bottom:var(--border)}
.aphorism:last-child{border-bottom:0}
.aphorism .n{display:inline-block;font-size:var(--size-xs);letter-spacing:var(--track-caps);
 text-transform:uppercase;color:var(--c-muted);text-decoration:none}
.aphorism .n:hover{color:var(--c-accent)}
.aphorism .guj{font-family:var(--font-indic);line-height:var(--leading-indic);
 font-size:var(--size-indic-book);margin:var(--s-3) 0}
.aphorism .guj p{margin:0 0 var(--s-4)}
.aphorism .guj p:last-child{margin-bottom:0}
.aphorism .guj h3{font-size:var(--size-lg);line-height:var(--leading-tight);
 margin:var(--s-5) 0 var(--s-3);font-weight:var(--weight-medium)}
.aphorism .eng{color:var(--c-ink-soft);margin:var(--s-3) 0 0;
 padding-left:var(--s-4);border-left:var(--rule) solid var(--c-line)}
.aphorism .eng p{margin:var(--s-1) 0}
.aphorism .pending{color:var(--c-muted);font-size:var(--size-sm);font-style:italic;
 margin-top:var(--s-2)}
.book-head{border-bottom:var(--border);padding-bottom:var(--s-4);margin-bottom:var(--s-2)}

/* ---------- the book, beside its index ----------
   The index is real links in the page, not built by script: it is how you
   find your way through a hundred and fourteen aphorisms, and it has to work
   in a browser that runs nothing. Script only marks which one you are at. */
main.reading{max-width:var(--measure-book)}
/* `minmax(0,1fr)` and not `1fr`: a grid track's default minimum is its
   content's own width, and the index is a row of a hundred and fourteen
   links that scrolls sideways. Left to size itself the track grew to hold all
   of them, the column overflowed the screen, and the centred title page went
   with it — off the right-hand edge, leaving a band of nothing behind. */
.book-layout{display:grid;grid-template-columns:minmax(0,1fr);
 gap:var(--s-6);align-items:start}
@media (min-width:62rem){
  .book-layout{grid-template-columns:var(--w-index) minmax(0,1fr)}
  .book-index{position:sticky;top:var(--h-sticky);max-height:calc(100vh - var(--h-sticky) - var(--s-5));
   overflow-y:auto;overscroll-behavior:contain;border-right:var(--border);
   padding-right:var(--s-4)}
}
.book-index h2{margin:0 0 var(--s-3);font-size:var(--size-sm);
 letter-spacing:var(--track-caps);text-transform:uppercase;color:var(--c-muted);
 font-weight:var(--weight-medium)}
.book-index ol{list-style:none;margin:0;padding:0}
.book-index li{margin:0}
.book-index a{display:grid;grid-template-columns:2.5rem minmax(0,1fr);gap:var(--s-2);
 padding:var(--s-2) var(--s-2) var(--s-2) 0;text-decoration:none;color:var(--c-ink-soft);
 border-radius:var(--radius);line-height:var(--leading-tight)}
.book-index a:hover{background:var(--c-hover);color:var(--c-ink)}
.book-index .num{font-size:var(--size-sm);color:var(--c-muted);
 font-variant-numeric:tabular-nums;text-align:right}
.book-index .open{font-family:var(--font-indic);font-size:var(--size-sm);
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.book-index a[aria-current]{background:var(--c-hover);color:var(--c-ink)}
.book-index a[aria-current] .num{color:var(--c-accent);font-weight:var(--weight-bold)}
/* On a phone the same links become one scrollable row of numbers, so the book
   still begins at the top of the screen. A hundred and fourteen entries with
   their opening words would be a page of its own before the text started, and
   collapsing it behind a control would need script to stay collapsed. */
@media (max-width:61.999rem){
  .book-index{position:sticky;top:0;z-index:5;margin:0 calc(-1 * var(--s-4));
   padding:var(--s-2) var(--s-4);background:var(--c-bg);
   border-bottom:var(--border)}
  .book-index h2{position:absolute;width:1px;height:1px;overflow:hidden;
   clip-path:inset(50%);white-space:nowrap}
  .book-index ol{display:flex;gap:var(--s-1);overflow-x:auto;
   overscroll-behavior-x:contain;scroll-snap-type:x proximity;
   -webkit-overflow-scrolling:touch}
  .book-index li{scroll-snap-align:center}
  .book-index a{display:block;padding:var(--s-2) var(--s-3);min-width:2.75rem;
   text-align:center}
  .book-index .open{display:none}
  .book-index .num{text-align:center;font-size:var(--size-md)}
}
.aphorism:target{scroll-margin-top:var(--h-sticky)}

/* ---------- heard ----------
   Quiet by design. The point is to see at a glance which of forty sittings
   are behind you, not to decorate the ones that are. A tick in the margin
   and the row stepping back a shade; nothing that competes with the text. */
tr.is-heard td,li.is-heard{color:var(--c-muted)}
/* The tick is the character itself, not a CSS unicode escape. This file is
   an ordinary Python string, where such an escape starts with a backslash
   and a zero — which Python reads as a null byte and writes into the CSS.
   The build refuses that. It refused this comment, when the comment first
   tried to explain it by example. */
tr.is-heard td:first-child::before,li.is-heard::before{content:"✓ ";
 color:var(--c-have);font-weight:var(--weight-bold)}
tr.is-heard a,li.is-heard a{color:var(--c-muted)}
tr.is-heard a:hover,li.is-heard a:hover{color:var(--c-accent)}
.heard-toggle{margin-left:var(--s-3);font:inherit;font-size:var(--size-sm);
 padding:var(--s-1) var(--s-3);color:var(--c-muted);background:none;
 border:var(--border);border-radius:var(--radius);cursor:pointer}
.heard-toggle:hover{color:var(--c-ink);background:var(--c-surface)}
.heard-toggle[aria-pressed=true]{color:var(--c-have);border-color:var(--c-have)}
.heard-toggle[aria-pressed=true]::before{content:"✓ "}

/* ---------- where you left off ----------
   Quiet on purpose. It sits above the texts and must not compete with them:
   a way back in for someone who was already listening, not an invitation. */
.recent[hidden]{display:none}
.recent{margin:var(--s-6) 0 var(--s-7);padding-top:var(--s-5);
 border-top:var(--border)}
.recent h2{margin:0 0 var(--s-4);font-size:var(--size-sm);
 letter-spacing:var(--track-caps);text-transform:uppercase;color:var(--c-muted);
 font-weight:var(--weight-medium)}
.recent ol{list-style:none;margin:0;padding:0}
.recent li{border-bottom:var(--border)}
.recent li:last-child{border-bottom:0}
.recent a{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s-2);
 padding:var(--s-3) 0;text-decoration:none;color:var(--c-ink)}
/* The ring starts at twelve o'clock rather than three, so a part-heard
   sitting reads the way a clock face does. */
.resume-mark{flex:none;width:1.5rem;height:1.5rem;transform:rotate(-90deg)}
.resume-mark .ring{fill:none;stroke:var(--c-line);stroke-width:2}
.resume-mark .ring-done{fill:none;stroke:var(--c-accent);stroke-width:2;
 stroke-linecap:round}
.resume-mark .play-mark{fill:var(--c-muted);transform:rotate(90deg);
 transform-origin:12px 12px}
.recent a:hover .play-mark{fill:var(--c-accent)}
.recent a:hover{background:var(--c-hover)}
.recent a strong{font-weight:var(--weight-medium)}
.recent a:hover strong{color:var(--c-accent);
 text-decoration:underline;text-underline-offset:var(--underline-offset)}
.recent .muted{font-size:var(--size-sm);color:var(--c-muted)}
.recent .at{margin-left:auto;font-size:var(--size-sm);color:var(--c-muted);
 font-variant-numeric:tabular-nums;white-space:nowrap}
.recent .forget{margin-top:var(--s-3);font:inherit;font-size:var(--size-sm);
 padding:var(--s-1) var(--s-3);color:var(--c-muted);background:none;
 border:var(--border);border-radius:var(--radius);cursor:pointer}
.recent .forget:hover{color:var(--c-ink);background:var(--c-surface)}

@media (max-width:34rem){
  .recent .at{margin-left:0;width:100%}
}

/* ---------- the book, for review ----------
   A working surface, not a page of the archive. Wide, plain, and arranged so
   that a long session reading Gujarati against English stays legible. */
.review-head{border-bottom:var(--border);padding-bottom:var(--s-4);margin-bottom:var(--s-5)}
.review-head .note{font-size:var(--size-sm)}
.glossary{margin-top:var(--s-4);font-size:var(--size-sm)}
.glossary summary{cursor:pointer;color:var(--c-accent)}
.glossary table{width:100%;border-collapse:collapse;margin-top:var(--s-3)}
.glossary th{text-align:left;font-size:var(--size-xs);letter-spacing:var(--track-caps);
 text-transform:uppercase;color:var(--c-muted);padding:var(--s-1) var(--s-2);
 border-bottom:var(--border)}
.glossary td{padding:var(--s-2);border-bottom:var(--border);vertical-align:top}
.glossary td[lang=gu]{font-family:var(--font-indic);white-space:nowrap}
.review{padding:var(--s-5) 0;border-bottom:var(--border)}
.review h2{font-size:var(--size-md);letter-spacing:var(--track-caps);
 text-transform:uppercase;color:var(--c-muted);margin:0 0 var(--s-3)}
.review .state{font-size:var(--size-xs);padding:0 var(--s-2);border-radius:var(--radius);
 background:var(--c-hover);color:var(--c-muted);text-transform:none;letter-spacing:normal}
.review[data-state=yours] .state{background:var(--c-have);color:var(--c-accent-ink)}
.review[data-state=none] .state{background:var(--c-unpublished);color:var(--c-accent-ink)}
.review .pair{display:grid;gap:var(--s-5);align-items:start}
@media (min-width:62rem){ .review .pair{grid-template-columns:1fr 1fr} }
.review .guj{font-family:var(--font-indic);font-size:var(--size-indic);
 line-height:var(--leading-indic)}
.review .guj p{margin:0 0 var(--s-3)}
.review textarea{width:100%;min-height:16rem;padding:var(--s-3);font:inherit;
 font-size:var(--size-md);line-height:var(--leading-body);color:var(--c-ink);
 background:var(--c-surface);border:var(--border);border-radius:var(--radius);
 resize:vertical}
.review textarea:focus{outline:2px solid var(--c-accent);outline-offset:1px}
.review button{font:inherit;font-size:var(--size-md);padding:var(--s-2) var(--s-4);
 border:var(--border);border-radius:var(--radius);background:var(--c-accent);
 border-color:var(--c-accent);color:var(--c-accent-ink);cursor:pointer}
.review .said{margin-left:var(--s-3);font-size:var(--size-sm);color:var(--c-muted)}

/* ---------- the correction form ----------
   Hidden until Shift+E or ?edit. Deliberately plain: it is a tool, not part
   of the archive, and it should not look like something a reader was meant
   to find. */
.fix[hidden]{display:none}
.fix{margin:var(--s-6) 0 0;padding:var(--s-5);background:var(--c-hover);
 border:var(--border);border-radius:var(--radius)}
.fix h2{margin:0 0 var(--s-2);font-size:var(--size-lg)}
.fix .note{margin:0 0 var(--s-4)}
.fix label{display:block;margin-bottom:var(--s-3);font-size:var(--size-sm);
 letter-spacing:var(--track-caps);text-transform:uppercase;color:var(--c-muted)}
.fix input,.fix textarea{display:block;width:100%;margin-top:var(--s-1);
 padding:var(--s-2) var(--s-3);font:inherit;font-size:var(--size-md);
 text-transform:none;letter-spacing:normal;color:var(--c-ink);
 background:var(--c-surface);border:var(--border);border-radius:var(--radius)}
.fix textarea{line-height:var(--leading-body);resize:vertical}
.fix input:focus,.fix textarea:focus{outline:2px solid var(--c-accent);
 outline-offset:1px}
.fix button{font:inherit;font-size:var(--size-md);padding:var(--s-2) var(--s-4);
 border:var(--border);border-radius:var(--radius);background:var(--c-surface);
 color:var(--c-ink);cursor:pointer}
.fix button[type=submit]{background:var(--c-accent);border-color:var(--c-accent);
 color:var(--c-accent-ink)}
.fix .said{margin:var(--s-3) 0 0;font-size:var(--size-sm);color:var(--c-muted);
 white-space:pre-wrap;word-break:break-word}
/* A sitting whose record has been corrected by hand. */
.corrected{font-size:var(--size-sm);color:var(--c-muted);margin-top:var(--s-4);
 padding-left:var(--s-4);border-left:var(--rule) solid var(--c-unpublished)}

@media (max-width:34rem){
  .block{flex-direction:column;gap:var(--s-1)}
  .block .t{width:auto;padding:0}
}
"""

JS = """/* Enhancement only. The page is complete without any of this.
   Everything is kept in localStorage; there is no account and no server. */
(function () {
  "use strict";
  var page = location.pathname;
  var blocks = [].slice.call(document.querySelectorAll(".block[data-start]"));
  var holder = document.querySelector(".video[data-youtube]");
  var player = null, timer = null;

  /* ---- 1. the player, built when it is asked for ----------------------------
     Two things were wrong here. The iframe was created as soon as the page
     opened, so every visit contacted YouTube whether or not anyone pressed
     play — the opposite of what the comment claimed. And `enablejsapi=1` was
     passed without the `origin` parameter it requires, which is what makes a
     player refuse to start and show "This video is unavailable" over a video
     that is public and embeddable.

     Now nothing is requested until the play button is pressed, and the
     parameters are complete when it is. */
  /* youtube-nocookie sets no tracking cookie until playback starts. It is
     occasionally stricter than the ordinary host about what it will play; if
     a recording refuses to start, change this to "www.youtube.com". */
  var EMBED_HOST = "www.youtube-nocookie.com";

  function embedUrl(id) {
    /* As few parameters as the page can get away with. `enablejsapi` is only
       needed to drive a synced transcript, and it drags `origin` along with
       it — 1324 of 1337 pages have no transcript and were asking for both for
       nothing, which is what stopped their recordings from playing. */
    var src = "https://" + EMBED_HOST + "/embed/" + id + "?rel=0&playsinline=1";
    /* enablejsapi drives the synced transcript and is also the only way to
       hear that a recording was refused. It requires `origin` alongside it;
       a page opened from disk has an origin of "null", which YouTube rejects,
       so both are left off there and the player is simply left alone. */
    if (location.origin && location.origin !== "null") {
      src += "&enablejsapi=1&origin=" + encodeURIComponent(location.origin);
    }
    return src;
  }

  /* Built through YouTube's own API when it is available, because that is the
     only thing that reports a refusal. Without the API — blocked, offline,
     opened from disk — a plain iframe is used instead and the page is exactly
     as it was before. */
  var apiReady = false;

  /* ---- the dock -------------------------------------------------------------
     The player is never a child of the page content. Moving an iframe in the
     DOM reloads it, so anything that re-parents the player stops the
     recording — which is exactly what carrying it to the next sitting would
     do. Instead it lives in one element at the end of <body> and is put over
     the placeholder in the page, which the stylesheet keeps sticky by itself.
     Navigating swaps the page around it and never touches it. */
  var dock = document.getElementById("dock");
  if (!dock) {
    dock = document.createElement("div");
    dock.id = "dock";
    dock.hidden = true;
    document.body.appendChild(dock);
  }
  var floating = false, ticking = false, playingId = null;

  function place() {
    ticking = false;
    if (dock.hidden) return;
    if (floating || !holder || !document.contains(holder)) {
      dock.classList.add("afloat");
      dock.style.cssText = "";
      return;
    }
    var box = holder.getBoundingClientRect();
    /* The placeholder has scrolled away entirely — let go and float. */
    if (box.bottom < 8 || box.top > window.innerHeight - 8) {
      dock.classList.add("afloat");
      dock.style.cssText = "";
      return;
    }
    dock.classList.remove("afloat");
    dock.style.cssText = "position:fixed;left:" + box.left + "px;top:" + box.top +
                         "px;width:" + box.width + "px;height:" + box.height + "px";
  }

  function reposition() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(place);
  }
  window.addEventListener("scroll", reposition, { passive: true });
  window.addEventListener("resize", reposition, { passive: true });

  function dockChrome(title, href) {
    var bar = document.createElement("p");
    bar.className = "dock-bar";
    bar.innerHTML = '<a class="dock-title" href="' + href + '"></a>' +
                    '<button type="button" class="dock-close" ' +
                    'aria-label="Close the recording">&times;</button>';
    bar.querySelector(".dock-title").textContent = title;
    bar.querySelector(".dock-close").addEventListener("click", stop);
    return bar;
  }

  function stop() {
    try { if (player && player.destroy) player.destroy(); } catch (err) {}
    player = null;
    playingId = null;
    clearInterval(timer);
    dock.hidden = true;
    dock.innerHTML = "";
    dock.classList.remove("afloat");
    dock.style.cssText = "";
    if (holder) holder.classList.remove("is-playing");
  }

  function play(autoplay, seconds) {
    var id = holder.getAttribute("data-youtube");
    playingId = id;
    holder.classList.add("is-playing");
    dock.hidden = false;
    dock.innerHTML = "";
    /* The whole path, not the file name. Once you have navigated away the
       link is being resolved against a different directory, and a bare
       "2000-11-06-….html" points at nothing. */
    dock.appendChild(dockChrome(document.title, location.pathname));
    var shell = document.createElement("div");
    shell.className = "dock-frame";
    dock.appendChild(shell);
    floating = false;
    place();

    if (apiReady && window.YT && YT.Player) {
      var mount = document.createElement("div");
      mount.id = "player-mount";      /* YT.Player needs an element with an id */
      shell.appendChild(mount);
      player = new YT.Player(mount, {
        videoId: id,
        playerVars: { rel: 0, playsinline: 1, autoplay: autoplay ? 1 : 0,
                      start: seconds ? Math.floor(seconds) : 0 },
        events: { onReady: watch, onStateChange: watch, onError: refused }
      });
      return null;
    }
    var frame = document.createElement("iframe");
    frame.src = embedUrl(id) + (autoplay ? "&autoplay=1" : "") +
                (seconds ? "&start=" + Math.floor(seconds) : "");
    frame.title = "Recording";
    frame.allow = "accelerometer; autoplay; encrypted-media; picture-in-picture";
    frame.allowFullscreen = true;
    shell.appendChild(frame);
    if (autoplay) watchByClock(seconds || 0);
    return frame;
  }

  /* Some recordings will not play outside YouTube however they are embedded —
     a bare iframe with no parameters at all is refused just the same. Nothing
     in the video's public metadata says so, so it can only be discovered by
     the player failing. Rather than leave YouTube's black "This video is
     unavailable" box sitting in the page, say what happened and send the
     reader where it does play.

     101 and 150 both mean the owner or a rights holder has disallowed
     embedding; 2 and 5 are a bad id and a playback failure. */
  function refused(event) {
    var code = event && event.data;
    if (code !== 101 && code !== 150 && code !== 2 && code !== 5) return;
    var id = holder.getAttribute("data-youtube");
    stop();
    holder.classList.add("elsewhere");
    holder.innerHTML =
      '<p>This recording plays on YouTube but cannot be shown here. ' +
      'That is a restriction on the video itself, not on this page.</p>' +
      '<p><a href="https://www.youtube.com/watch?v=' + id + '">Watch it on YouTube</a></p>';
  }

  if (holder) {
    var start = document.createElement("button");
    start.type = "button";
    start.className = "play";
    start.setAttribute("aria-label", "Play the recording");
    start.innerHTML = '<span class="play-mark" aria-hidden="true"></span>';
    start.addEventListener("click", function () { play(true); });
    holder.appendChild(start);

    /* Loaded here, above the transcript-only code below, because most pages
       have no transcript and returned early — so the API never arrived and a
       refused recording could never be noticed. */
    var tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
    window.onYouTubeIframeAPIReady = function () { apiReady = true; };
  }

  /* ---- carrying it to the next sitting --------------------------------------
     A link is followed by fetching the next page and swapping what is inside
     <main>. The dock is outside <main>, so the recording is never touched and
     keeps playing while you read ahead. Anything that goes wrong here falls
     through to an ordinary page load, which is what the browser would have
     done anyway. */
  function samePage(href) {
    try {
      var url = new URL(href, location.href);
      if (url.origin !== location.origin || !/\.html$/.test(url.pathname)) return false;
      /* A link to somewhere on the page you are already on is the browser's
         own job. Taking it over replaces <main> and scrolls to the top, which
         is the opposite of what was clicked: it broke every entry in the
         book's index the moment that page began loading this script. */
      if (url.hash && url.pathname === location.pathname) return false;
      return true;
    } catch (err) { return false; }
  }

  function swap(url, push) {
    return fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.querySelector("main");
        var here = document.querySelector("main");
        if (!fresh || !here) return Promise.reject("no main");
        here.replaceWith(fresh);
        /* The masthead sits outside <main> and its links are relative to the
           page they were written for. Leaving it alone meant that after
           moving to a sitting the "The book" link still read
           `book/index.html`, which from inside /d/ resolves to
           /d/book/index.html and is nothing at all. Every link up there was
           wrong by one directory for as long as you stayed on the site. */
        var head = doc.querySelector("header.masthead");
        var oldHead = document.querySelector("header.masthead");
        if (head && oldHead) oldHead.replaceWith(head);
        document.title = doc.title;
        if (push) history.pushState({}, "", url);
        atPath = location.pathname;
        window.scrollTo(0, 0);
        bind();
        /* The recording carries over. On any other page it floats; come back
           to the page it belongs to and it settles into place again. */
        if (playingId) {
          var home = document.querySelector('.video[data-youtube="' + playingId + '"]');
          floating = !home;
          if (home) home.classList.add("is-playing");
          place();
        }
        return true;
      });
  }

  document.addEventListener("click", function (ev) {
    if (ev.defaultPrevented || ev.button || ev.metaKey || ev.ctrlKey ||
        ev.shiftKey || ev.altKey) return;
    var a = ev.target.closest && ev.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download") || !samePage(a.href)) return;
    ev.preventDefault();
    swap(a.href, true).catch(function () { location.href = a.href; });
  });

  /* Which page we are on, as distinct from where we are in it. */
  var atPath = location.pathname;

  window.addEventListener("popstate", function () {
    /* Going to a fragment is a history entry too, and some browsers deliver
       it here as a popstate rather than only as a hashchange. Rebuilding the
       page for it replaces <main> and scrolls to the top — which is how every
       link in the book's index came to do nothing at all: you arrived at the
       aphorism and were pulled straight back up. Only a change of page is a
       change of page; moving within one is the browser's own business. */
    if (location.pathname === atPath) return;
    atPath = location.pathname;
    swap(location.href, false).catch(function () { location.reload(); });
  });

  /* Everything below depends on the page currently in <main>, so it is run
     again after a swap. */
  function bind() {
    page = location.pathname;
    blocks = [].slice.call(document.querySelectorAll(".block[data-start]"));
    holder = document.querySelector(".video[data-youtube]");
    if (holder && !holder.querySelector("button.play")) addPlayButton();
    bindTranscript();
    place();
    resumeFromHash();
    /* Following a link swaps what is in <main> and leaves the script running,
       so anything that decorated the old content has to be run again over the
       new. The heard ticks were applied once, at load, and so disappeared the
       moment you moved through a series the way anyone would — by clicking
       Part 2 from Part 1. Fourth time this has caught something: the
       masthead's links, resume-from-hash and the heard toggle were the
       others. Anything that decorates or adds to the page belongs here. */
    addHeardToggle();
    mark();
  }

  function addPlayButton() {
    var start = document.createElement("button");
    start.type = "button";
    start.className = "play";
    start.setAttribute("aria-label", "Play the recording");
    start.innerHTML = '<span class="play-mark" aria-hidden="true"></span>';
    start.addEventListener("click", function () { play(true); });
    holder.appendChild(start);
  }

  function bindTranscript() {
    if (!blocks.length) return;
    wireTimestamps();
    KEY = "avlokan:pos:" + page;
    MARKS = "avlokan:marks:" + page;
    live = null;
    toolbar();
    restoreMarks();
  }

  var KEY = "avlokan:pos:" + page;
  var MARKS = "avlokan:marks:" + page;

  /* ---- the book's index ---------------------------------------------------
     The index is already complete in the page and every link already works.
     All this adds is telling you where you are in it — which of a hundred and
     fourteen aphorisms is on the screen — and keeping that entry in view in a
     column that is itself scrollable. */
  (function () {
    var index = document.querySelector(".book-index");
    var marks = index && [].slice.call(document.querySelectorAll(".aphorism[id]"));
    if (!index || !marks || marks.length < 2 || !window.IntersectionObserver) return;

    var links = {};
    [].forEach.call(index.querySelectorAll('a[href^="#a"]'), function (a) {
      links[a.getAttribute("href").slice(1)] = a;
    });
    if (!Object.keys(links).length) return;

    var shown = {}, current = null;
    function settle() {
      var first = null;
      marks.forEach(function (m) {
        if (shown[m.id] && (!first || m.offsetTop < first.offsetTop)) first = m;
      });
      var want = first ? links[first.id] : null;
      if (want === current) return;
      if (current) current.removeAttribute("aria-current");
      current = want;
      if (!current) return;
      current.setAttribute("aria-current", "true");
      keepInView(current);
    }

    /* Scroll the index, and only the index. `scrollIntoView` walks up the
       ancestors until the element is visible, and below the sidebar
       breakpoint the index is a strip stuck to the top of the document — so
       bringing an entry into view there meant scrolling the *page* to the
       top. Every link in the book's index appeared to do nothing: you landed
       on the aphorism and were pulled straight back up. */
    function keepInView(link) {
      var box = index.getBoundingClientRect(), at = link.getBoundingClientRect();
      if (index.scrollHeight > index.clientHeight) {
        if (at.top < box.top) index.scrollTop -= box.top - at.top;
        else if (at.bottom > box.bottom) index.scrollTop += at.bottom - box.bottom;
      }
      if (index.scrollWidth > index.clientWidth) {
        if (at.left < box.left) index.scrollLeft -= box.left - at.left;
        else if (at.right > box.right) index.scrollLeft += at.right - box.right;
      }
    }

    var watcher = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) { shown[entry.target.id] = entry.isIntersecting; });
      settle();
    }, { rootMargin: "-20% 0px -70% 0px" });
    marks.forEach(function (m) { watcher.observe(m); });
  })();

  /* ---- heard and not heard ------------------------------------------------
     A text can run to three hundred and fifty sittings. Working through one
     over weeks, the only question that matters on its page is which ones are
     already behind you, and until now the only thing answering it was memory.

     A sitting counts as heard when the recording reaches nine tenths of its
     length — the last minutes are the closing stuti, and waiting for the very
     end would mean almost nothing ever counted. It can also be set by hand,
     for the ones listened to on YouTube or on a tape thirty years ago. */
  var PLAYED = "avlokan:played";
  var NEARLY_ALL = 0.9;

  function heard() {
    try { return JSON.parse(localStorage.getItem(PLAYED) || "{}") || {}; }
    catch (err) { return {}; }
  }

  function setHeard(slug, yes) {
    var all = heard();
    if (yes) all[slug] = 1; else delete all[slug];
    try { localStorage.setItem(PLAYED, JSON.stringify(all)); } catch (err) {}
    mark();
  }

  /* The listings are plain HTML built long before anyone played anything, so
     the marks are put on here, from the address each row already links to. */
  function mark() {
    var all = heard();
    [].forEach.call(document.querySelectorAll('a[href$=".html"]'), function (a) {
      /* The resolved path, not the attribute as written. One sitting is
         linked as ../d/x.html from a text page, as d/x.html from the front
         page, and as plain x.html from a sitting standing beside it in its
         own series — and that last form is the series list, which matched
         neither pattern and so was never marked at all. */
      var path = a.pathname || "";
      if (path.indexOf("/d/") === -1) return;
      var hit = /([^/]+)\.html$/.exec(path);
      if (!hit) return;
      var row = a.closest("tr") || a.closest("li") || a;
      row.classList.toggle("is-heard", !!all[hit[1]]);
    });
    /* The sitting you are reading appears in its own series list as plain
       text rather than a link, so nothing above reaches it. */
    var mine = document.querySelector("article.discourse[data-slug]");
    if (mine) {
      [].forEach.call(document.querySelectorAll(".run li > strong"), function (el) {
        var li = el.parentNode;
        if (li) li.classList.toggle("is-heard", !!all[mine.dataset.slug]);
      });
    }
    var here = document.querySelector("article.discourse[data-slug]");
    var toggle = document.querySelector(".heard-toggle");
    if (here && toggle) {
      var on = !!all[here.dataset.slug];
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
      toggle.textContent = on ? "Heard" : "Mark as heard";
    }
  }

  function watchProgress(seconds) {
    var here = document.querySelector("article.discourse[data-slug]");
    if (!here) return;
    var mins = parseInt(here.dataset.minutes || "0", 10);
    if (mins > 0 && seconds > mins * 60 * NEARLY_ALL && !heard()[here.dataset.slug]) {
      setHeard(here.dataset.slug, true);
    }
  }

  /* Built in `bind`, not once at load. Following a link replaces everything
     inside <main> with markup that has never had a button added to it, so a
     control created at load exists only on the first page you open — this one
     vanished on every sitting reached by clicking, which is most of them. The
     play button has always been made this way; so is this now. */
  function addHeardToggle() {
    var here = document.querySelector("article.discourse[data-slug]");
    if (!here || here.querySelector(".heard-toggle")) return;
    var bar = here.querySelector(".links");
    var button = document.createElement("button");
    button.type = "button";
    button.className = "heard-toggle";
    button.addEventListener("click", function () {
      setHeard(here.dataset.slug, !heard()[here.dataset.slug]);
    });
    (bar || here).appendChild(button);
  }

  addHeardToggle();
  mark();

  /* ---- where you left off -------------------------------------------------
     The position of a recording was already being kept, under the page's own
     address, so that "Resume at 12:34" could appear on the sitting itself.
     What it could not do was tell you, from the front page, which sittings
     those were — an address is not a title.

     So alongside it a short list is kept: the last few sittings played, each
     with what it is, when it was given and where you stopped. Entirely in
     this browser. There is no account, nothing is sent anywhere, and clearing
     the browser clears it. The archive cannot see it and neither can I. */
  var RECENT = "avlokan:recent";
  var KEEP = 8;

  function recent() {
    try {
      var got = JSON.parse(localStorage.getItem(RECENT) || "[]");
      return Object.prototype.toString.call(got) === "[object Array]" ? got : [];
    } catch (err) { return []; }
  }

  /* A play mark, ringed by how much of the recording is behind you. The ring
     is the informative half: eight rows of identical triangles would say only
     that these are recordings, which the page already says. An entry from
     before lengths were remembered has no fraction to draw, so it gets the
     mark alone rather than a ring pretending to be at zero. */
  var RING = 2 * Math.PI * 9;

  function resumeMark(x) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "resume-mark");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    function circle(cls, dash) {
      var c = document.createElementNS(svg.namespaceURI, "circle");
      c.setAttribute("cx", "12"); c.setAttribute("cy", "12"); c.setAttribute("r", "9");
      c.setAttribute("class", cls);
      if (dash) { c.setAttribute("stroke-dasharray", RING);
                  c.setAttribute("stroke-dashoffset", dash); }
      return c;
    }
    var whole = x.mins > 0 ? Math.min(x.t / (x.mins * 60), 1) : 0;
    svg.appendChild(circle("ring"));
    if (whole > 0) svg.appendChild(circle("ring-done", RING * (1 - whole)));
    var play = document.createElementNS(svg.namespaceURI, "path");
    play.setAttribute("class", "play-mark");
    play.setAttribute("d", "M10 8.5l5.5 3.5-5.5 3.5z");
    svg.appendChild(play);
    return svg;
  }

  function clock(seconds) {
    var s = Math.max(0, Math.floor(seconds));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    var mm = (h && m < 10 ? "0" : "") + m;
    return (h ? h + ":" : "") + mm + ":" + (r < 10 ? "0" : "") + r;
  }

  /* Under half a minute is not a sitting you were listening to, it is one you
     opened and thought better of. Offering to resume those would bury the
     ones you meant. */
  var WORTH_KEEPING = 30;

  function remember(seconds) {
    var art = document.querySelector("article.discourse[data-slug]");
    if (!art || !(seconds > WORTH_KEEPING)) return;
    var kept = recent().filter(function (x) { return x && x.slug !== art.dataset.slug; });
    kept.unshift({ slug: art.dataset.slug, text: art.dataset.text,
                   when: art.dataset.when, ref: art.dataset.ref,
                   mins: parseInt(art.dataset.minutes || "0", 10) || 0,
                   t: Math.floor(seconds), at: Date.now() });
    try { localStorage.setItem(RECENT, JSON.stringify(kept.slice(0, KEEP))); } catch (err) {}
  }

  /* Someone who has been using the archive already has positions saved but no
     list, because the list did not exist when they listened. The page knows
     what it is, so the first visit back to a sitting puts it in. */
  (function () {
    var art = document.querySelector("article.discourse[data-slug]");
    if (!art) return;
    var was = 0;
    try { was = parseInt(localStorage.getItem("avlokan:pos:" + location.pathname) || "0", 10) || 0; }
    catch (err) { return; }
    var listed = recent().some(function (x) { return x && x.slug === art.dataset.slug; });
    if (was > WORTH_KEEPING && !listed) remember(was);
  })();

  /* Arriving from the front page: start the recording where it was left.
     Deliberately not the `#t…` that a search result uses — that one lands on
     a line to read, and should not begin playing under you.

     Called again after every in-page navigation. Following a link does not
     reload the document, it swaps what is in <main>, so anything that only
     runs when the script first loads never runs again. */
  function resumeFromHash() {
    var at = /^#at([0-9]+)$/.exec(location.hash || "");
    if (!at || !holder) return;
    if (playingId === holder.getAttribute("data-youtube")) return;
    play(true, parseInt(at[1], 10));
  }
  resumeFromHash();

  /* Positions saved before this list existed, or on a visit too short to be
     recorded in it. The page has the address but not the name, so it asks:
     the lookup is fetched only when there is something in it to look up. */
  function resolveOrphans() {
    var known = {}, orphans = [];
    recent().forEach(function (x) { if (x && x.slug) known[x.slug] = 1; });
    for (var i = 0; i < localStorage.length; i++) {
      var key = localStorage.key(i);
      if (!key || key.indexOf("avlokan:pos:") !== 0) continue;
      var hit = /([^/]+)\.html$/.exec(key);
      if (!hit || known[hit[1]]) continue;
      var at = parseInt(localStorage.getItem(key) || "0", 10) || 0;
      if (at > WORTH_KEEPING) orphans.push({ slug: hit[1], t: at });
    }
    if (!orphans.length) return Promise.resolve([]);
    return fetch("assets/sittings.json").then(function (r) { return r.json(); })
      .then(function (all) {
        return orphans.map(function (o) {
          var row = all[o.slug];
          return row ? { slug: o.slug, text: row[0], when: row[1], ref: row[2],
                         t: o.t, at: 0 } : null;
        }).filter(Boolean);
      }).catch(function () { return []; });
  }

  (function () {
    var panel = document.getElementById("recent");
    if (!panel) return;
    resolveOrphans().then(function (found) {
      if (found.length) {
        var merged = recent().concat(found);
        merged.sort(function (a, b) { return (b.at || 0) - (a.at || 0); });
        try { localStorage.setItem(RECENT, JSON.stringify(merged.slice(0, KEEP))); }
        catch (err) {}
      }
      show();
    });

    function show() {
    var items = recent().filter(function (x) { return x && x.slug && x.t; });
    if (!items.length) return;
    var list = panel.querySelector("ol");
    items.forEach(function (x) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "d/" + x.slug + ".html#at" + x.t;
      a.appendChild(resumeMark(x));
      a.appendChild(document.createElement("strong")).textContent = x.text || x.slug;
      var sub = document.createElement("span");
      sub.className = "muted";
      sub.textContent = [x.when, x.ref].filter(Boolean).join(" · ");
      a.appendChild(sub);
      var at = document.createElement("span");
      at.className = "at";
      at.textContent = "stopped at " + clock(x.t);
      a.appendChild(at);
      li.appendChild(a);
      list.appendChild(li);
    });
    panel.hidden = false;
    panel.querySelector(".forget").addEventListener("click", function () {
      try { localStorage.removeItem(RECENT); } catch (err) {}
      panel.hidden = true;
    });
    }
  })();

  /* ---- the book, for review ------------------------------------------------
     Only ever on review.html, and only ever useful while ./edit.sh is
     answering. Each aphorism saves on its own: a hundred and fourteen of them
     behind one Save at the bottom is a page you cannot leave half-done. */
  [].forEach.call(document.querySelectorAll(".review[data-n]"), function (block) {
    var area = block.querySelector("textarea");
    var said = block.querySelector(".said");
    var was = area.value;
    block.querySelector(".save").addEventListener("click", function () {
      if (area.value.trim() === was.trim()) { said.textContent = "Unchanged."; return; }
      said.textContent = "Saving…";
      fetch("/aphorism", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ n: parseInt(block.dataset.n, 10), english: area.value })
      }).then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      }).then(function (answer) {
        was = area.value;
        said.textContent = answer.message || "Saved.";
        block.dataset.state = "yours";
        block.querySelector(".state").textContent = "yours";
        var tally = document.getElementById("tally");
        if (tally && answer.tally) tally.textContent = answer.tally;
      }).catch(function () {
        said.textContent = "Not saved — is ./edit.sh running?";
      });
    });
  });

  /* ---- corrections -------------------------------------------------------
     The form is on every discourse page and hidden on all of them. Shift+E
     opens it; so does `?edit` on the address, which is the only way in from a
     phone or a television.

     Saving tries the local editor first. `./edit.sh` answers POST /correction
     and writes the file; anywhere else that request fails and the same JSON
     goes to the clipboard instead, to be pasted wherever it can be acted on.
     Either way nothing is lost between noticing and recording. */
  var fix = document.querySelector("form.fix");
  if (fix) {
    var said = fix.querySelector(".said");

    function show() {
      fix.hidden = false;
      fix.scrollIntoView({ block: "center" });
      var first = fix.querySelector("input, textarea");
      if (first) first.focus();
    }

    /* `\\b` doubled on purpose: these scripts live inside ordinary Python
       strings, where a single backslash-b is a backspace character. It
       compiled to /[?&]edit<BS>/, which matches nothing. */
    if (/[?&]edit\\b/.test(location.search)) show();

    document.addEventListener("keydown", function (ev) {
      var on = ev.target && ev.target.tagName || "";
      if (/INPUT|TEXTAREA|SELECT/.test(on)) return;
      if (ev.shiftKey && (ev.key === "E" || ev.key === "e")) { ev.preventDefault(); show(); }
    });

    fix.querySelector(".cancel").addEventListener("click", function () {
      fix.hidden = true;
    });

    function tell(message) { said.textContent = message; }

    fix.addEventListener("submit", function (ev) {
      ev.preventDefault();
      /* Only what actually differs. A correction that restates the current
         value is noise in the history and, worse, reads later as a decision
         somebody made on purpose. */
      var body = { key: fix.dataset.key, was: fix.dataset.slug,
                   date: fix.dataset.date };
      var any = false;
      ["scripture", "reference", "part_no"].forEach(function (name) {
        var field = fix.elements[name];
        if (!field) return;
        var now = field.value.trim();
        if (now === field.defaultValue.trim()) return;
        body[name] = (name === "part_no")
          ? (now === "" ? null : parseInt(now, 10)) : now;
        any = true;
      });
      body.why = fix.elements.why.value.trim();
      if (!any) { tell("Nothing is different from what the page already says."); return; }
      if (!body.why) { tell("Say why, so a later reader can disagree with it."); return; }

      tell("Saving…");
      fetch("/correction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (r) {
        if (!r.ok) throw new Error("editor said " + r.status);
        return r.json();
      }).then(function (answer) {
        tell(answer.message || "Saved. Rebuilding…");
        if (answer.reload) setTimeout(function () { location.reload(); }, 900);
      }).catch(function () {
        var text = JSON.stringify(body, null, 1);
        var copy = navigator.clipboard && navigator.clipboard.writeText(text);
        if (copy) {
          copy.then(function () {
            tell("The editor is not running, so this is on your clipboard instead.");
          }).catch(function () { tell(text); });
        } else {
          tell(text);
        }
      });
    });
  }

  if (!blocks.length) { bind(); return; }

  /* ---- 2. click a timestamp to jump there ---- */
  function seek(seconds) {
    if (player && player.seekTo) { player.seekTo(seconds, true); player.playVideo(); }
    else if (holder) {
      var i = holder.querySelector("iframe");
      if (i) i.src = i.src.replace(/([?&])start=\\d+/, "$1") + "&start=" + Math.floor(seconds) + "&autoplay=1";
    }
  }
  function wireTimestamps() {
    blocks.forEach(function (b) {
      var t = b.querySelector(".t");
      if (!t || t.dataset.wired) return;
      t.dataset.wired = "1";
      t.setAttribute("role", "button");
      t.setAttribute("tabindex", "0");
      t.addEventListener("click", function () { seek(parseFloat(b.dataset.start)); });
      t.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); t.click(); }
      });
    });
  }
  wireTimestamps();

  /* ---- 3. follow along, and remember where you stopped ---- */
  var follow = true, live = null;

  function highlight(seconds) {
    var found = null;
    for (var i = 0; i < blocks.length; i++) {
      if (seconds >= parseFloat(blocks[i].dataset.start)) found = blocks[i]; else break;
    }
    if (found === live) return;
    if (live) live.classList.remove("is-live");
    live = found;
    if (live) {
      live.classList.add("is-live");
      if (follow) live.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  function noteTime(t, ticks) {
    highlight(t);
    try { localStorage.setItem(KEY, String(Math.floor(t))); } catch (err) {}
    /* The front-page list is rewritten whole each time, so it is kept to
       once every few seconds rather than every tick. */
    if (ticks % 5 === 0) { remember(t); watchProgress(t); }
  }

  function watch() {
    clearInterval(timer);
    var ticks = 0;
    timer = setInterval(function () {
      if (!player || !player.getCurrentTime) return;
      noteTime(player.getCurrentTime(), ++ticks);
    }, 1000);
  }

  /* ---- keeping time without the API ------------------------------------------
     Everything above depends on YouTube's iframe API, which is a script from
     youtube.com and is therefore one of the first things an ad blocker stops.
     When it does not arrive, `play` falls back to a plain iframe — the
     recording plays perfectly and there is no handle on it, so nothing was
     recorded at all: no position, no resume, no marking a sitting heard. The
     archive looked, to anyone running a blocker, as though the feature simply
     did not exist. It is also what a reader on a strict network gets.

     So when there is no player to ask, the clock is used instead. It cannot
     see a pause taken inside the iframe, so it can run ahead of the recording;
     it is capped at the sitting's own length, and a resume that lands a little
     early is a far smaller failure than no resume at all. */
  function watchByClock(from) {
    clearInterval(timer);
    var began = Date.now(), ticks = 0;
    var here = document.querySelector("article.discourse[data-minutes]");
    var limit = here ? parseInt(here.dataset.minutes || "0", 10) * 60 : 0;
    timer = setInterval(function () {
      var t = (from || 0) + (Date.now() - began) / 1000;
      if (limit && t > limit) { clearInterval(timer); return; }
      noteTime(t, ++ticks);
    }, 1000);
  }

  function toolbar() {
    var was = document.querySelector(".transcript .toolbar");
    if (was) was.remove();
    var bar = document.createElement("div");
    bar.className = "toolbar";
    var saved = 0;
    try { saved = parseInt(localStorage.getItem(KEY) || "0", 10) || 0; } catch (err) {}

    if (saved > 30) {
      var resume = document.createElement("button");
      var m = Math.floor(saved / 60), s = saved % 60;
      resume.textContent = "Resume at " + m + ":" + (s < 10 ? "0" : "") + s;
      resume.addEventListener("click", function () { seek(saved); });
      bar.appendChild(resume);
    }

    var f = document.createElement("button");
    f.textContent = "Follow along";
    f.setAttribute("aria-pressed", "true");
    f.addEventListener("click", function () {
      follow = !follow;
      f.setAttribute("aria-pressed", follow ? "true" : "false");
    });
    bar.appendChild(f);

    var copy = document.createElement("button");
    copy.textContent = "Copy my highlights";
    copy.addEventListener("click", function () {
      var picked = blocks.filter(function (b) { return b.classList.contains("is-marked"); });
      if (!picked.length) { copy.textContent = "Nothing highlighted yet"; setTimeout(function(){ copy.textContent = "Copy my highlights"; }, 1800); return; }
      var out = picked.map(function (b) {
        return "[" + b.querySelector(".t").textContent + "] " + b.querySelector(".w").innerText;
      }).join("\\n\\n") + "\\n\\n" + location.href;
      var ta = document.createElement("textarea");
      ta.value = out; ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;left:-9999px";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (err) {}
      ta.remove();
      copy.textContent = "Copied " + picked.length + " passages";
      setTimeout(function () { copy.textContent = "Copy my highlights"; }, 2200);
    });
    bar.appendChild(copy);

    var section = document.getElementById("transcript");
    if (section) section.insertBefore(bar, section.querySelector(".blocks"));
  }

  /* ---- 4. highlight a passage by clicking its text ---- */
  function restoreMarks() {
    var marked = {};
    try { marked = JSON.parse(localStorage.getItem(MARKS) || "{}"); } catch (err) {}
    blocks.forEach(function (b) {
      if (marked[b.dataset.start]) b.classList.add("is-marked");
      var w = b.querySelector(".w");
      if (!w || w.dataset.wired) return;
      w.dataset.wired = "1";
      w.addEventListener("dblclick", function () {
        b.classList.toggle("is-marked");
        if (b.classList.contains("is-marked")) marked[b.dataset.start] = 1;
        else delete marked[b.dataset.start];
        try { localStorage.setItem(MARKS, JSON.stringify(marked)); } catch (err) {}
      });
    });
  }

  bindTranscript();

  /* The player itself is built when play is pressed; see section 1. */
})();
"""



CHECK_JS = """/* Asks YouTube to play every recording in a hidden player and notes the ones
   it refuses. Four at a time, because the point is to finish, not to be fast.
   Error 101 and 150 are "the owner or a rights holder disallowed embedding";
   2 is a malformed id and 5 a playback failure. */
(function () {
  "use strict";
  var LANES = 4;
  /* A full pass takes about ten minutes, which is long enough that the tab
     will sometimes be reloaded part way. Results are kept as they are found
     so a reload picks up where it stopped instead of starting again. */
  var KEY = "avlokan.embedcheck";
  var seen = {};
  try { seen = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { seen = {}; }
  function remember(id, code) {
    seen[id] = code;
    try { localStorage.setItem(KEY, JSON.stringify(seen)); } catch (e) {}
  }

  var next = 0, checked = 0, refused = 0, players = [];
  var stage = document.getElementById("stage");
  var bad = document.getElementById("bad");
  var count = document.getElementById("count");

  function report(item, code) {
    refused++;
    var li = document.createElement("li");
    li.innerHTML = '<a href="https://www.youtube.com/watch?v=' + item.id + '">' +
      item.id + '</a> &middot; ' + item.date + ' &middot; ' + item.text +
      '<br><span class="muted">' + (item.title || "") +
      ' &middot; error ' + code + ' &middot; <a href="d/' + item.page +
      '.html">page</a></span>';
    bad.appendChild(li);
  }

  function byText() {
    var rows = {};
    VIDEOS.forEach(function (v) {
      var code = seen[v.id];
      if (code === undefined) return;
      rows[v.text] = rows[v.text] || { bad: 0, all: 0 };
      rows[v.text].all++;
      if (code) rows[v.text].bad++;
    });
    var names = Object.keys(rows).sort(function (a, b) {
      return (rows[b].bad / rows[b].all) - (rows[a].bad / rows[a].all) ||
             rows[b].all - rows[a].all;
    });
    var out = "";
    names.forEach(function (n) {
      var r = rows[n];
      out += "<tr><td>" + n + "</td><td>" + r.bad + "</td><td>" + r.all +
             "</td><td>" + Math.round(r.bad / r.all * 100) + "%</td></tr>";
    });
    document.getElementById("bytext").innerHTML = out;
  }

  function tick() {
    count.textContent = checked + " of " + VIDEOS.length + " checked, " +
                        refused + " refused";
    if (checked % 20 === 0 || checked === VIDEOS.length) byText();
    if (checked === VIDEOS.length) {
      document.getElementById("done").hidden = false;
      document.getElementById("summary").textContent =
        refused + " of " + VIDEOS.length + " recordings cannot be embedded.";
    }
  }

  function lane(slot) {
    /* Skip anything a previous pass already settled. */
    while (next < VIDEOS.length && seen[VIDEOS[next].id] !== undefined) {
      next++;      /* already counted and listed by replay() above */
    }
    tick();
    if (next >= VIDEOS.length) return;
    var item = VIDEOS[next++];
    var mount = document.createElement("div");
    mount.id = "probe-" + slot + "-" + item.id;
    stage.appendChild(mount);
    var settled = false;
    function finish(code) {
      if (settled) return;
      settled = true;
      remember(item.id, code || 0);
      if (code) report(item, code);
      checked++; tick();
      try { players[slot].destroy(); } catch (e) {}
      mount.remove();
      lane(slot);
    }
    players[slot] = new YT.Player(mount.id, {
      videoId: item.id, height: 90, width: 160,
      playerVars: { autoplay: 0, rel: 0 },
      events: {
        /* A refused recording readies first and only then reports the
           refusal, so readiness on its own means nothing — it has to be
           given a moment to complain before being counted as fine. */
        onReady: function () { setTimeout(function () { finish(0); }, 3000); },
        onError: function (e) { finish(e.data); }
      }
    });
    /* A player that neither readies nor errors would stall the lane. */
    setTimeout(function () { finish(0); }, 20000);
  }

  /* Show whatever a previous pass already found, straight away. This used to
     wait for YouTube's API to load before drawing anything, so a run whose
     results were all in hand still showed an empty page if the API could not
     be reached. */
  (function replay() {
    VIDEOS.forEach(function (v) {
      var code = seen[v.id];
      if (code === undefined) return;
      checked++;
      if (code) report(v, code);
    });
    if (checked) { next = 0; tick(); byText(); }
  })();

  document.getElementById("copy").addEventListener("click", function () {
    var lines = VIDEOS.filter(function (v) { return seen[v.id]; })
      .map(function (v) {
        return v.id + "\\t" + v.date + "\\t" + v.text + "\\t" + (v.title || "");
      });
    var box = document.createElement("textarea");
    box.value = lines.join("\\n");
    document.body.appendChild(box);
    box.select();
    try { document.execCommand("copy"); } catch (e) {}
    box.remove();
    document.getElementById("copied").textContent = lines.length + " copied";
  });

  document.getElementById("again").addEventListener("click", function () {
    try { localStorage.removeItem(KEY); } catch (e) {}
    location.reload();
  });

  document.getElementById("go").addEventListener("click", function () {
    this.disabled = true;
    stage.hidden = false;
    stage.style.cssText = "position:fixed;left:-9999px;top:0";
    var tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
    window.onYouTubeIframeAPIReady = function () {
      for (var i = 0; i < LANES; i++) lane(i);
    };
  });
})();
"""


SEARCH_JS = """/* Search, entirely in the browser. No server, no index service, nothing to
   keep paid for — the whole thing is two JSON files and this. */
(function () {
  "use strict";
  var q = document.getElementById("q");
  var deep = document.getElementById("deep");
  var out = document.getElementById("results");
  var count = document.getElementById("count");
  var data = null, words = null, wordsAsked = false, timer = null;

  function fold(t) {
    return (t || "").toLowerCase()
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/\s+/g, " ").trim();
  }

  function terms(s) {
    return fold(s).split(" ").filter(Boolean);
  }

  function hit(hay, parts) {
    for (var i = 0; i < parts.length; i++) {
      if (hay.indexOf(parts[i]) < 0) return false;
    }
    return true;
  }

  function esc(t) {
    var d = document.createElement("div");
    d.textContent = t == null ? "" : t;
    return d.innerHTML;
  }

  /* Show where the match is, not the first 200 characters of something else. */
  function around(text, parts) {
    var low = fold(text), at = -1;
    for (var i = 0; i < parts.length && at < 0; i++) at = low.indexOf(parts[i]);
    if (at < 0) at = 0;
    var from = Math.max(0, at - 70), to = Math.min(text.length, at + 170);
    return (from ? "…" : "") + esc(text.slice(from, to)) + (to < text.length ? "…" : "");
  }

  function stamp(sec) {
    var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function run() {
    var raw = q.value.trim();
    if (!data || raw.length < 2) {
      out.innerHTML = "";
      count.textContent = raw && raw.length < 2 ? "Keep typing…" : "";
      return;
    }
    var parts = terms(raw), rows = [], n = 0;

    data.sittings.forEach(function (s) {
      if (n >= 300) return;
      var hay = fold([s.d, s.t, s.r, s.n].join(" "));
      if (!hit(hay, parts)) return;
      n++;
      rows.push('<li><a href="d/' + s.u + '.html">' +
        esc(s.r || s.t) + ' <span class="muted">' + esc(s.d) + '</span></a>' +
        '<div class="muted">' + esc(s.n || s.t) +
        (s.x ? ' &middot; transcript' : '') + '</div></li>');
    });

    var found = n;
    data.book.forEach(function (b) {
      if (n >= 300) return;
      if (!hit(fold(b.g + " " + b.e + " aphorism " + b.n), parts)) return;
      n++;
      rows.push('<li><a href="book/' + b.n + '.html">Aphorism ' + b.n + '</a>' +
        '<div class="guj" lang="gu">' + around(b.g, parts) + '</div></li>');
    });

    if (deep.checked && words) {
      words.forEach(function (w) {
        w.b.forEach(function (blk) {
          if (n >= 300) return;
          if (!hit(fold(blk[1]), parts)) return;
          n++;
          rows.push('<li><a href="d/' + w.u + '.html#t' + blk[0] + '">' +
            esc(w.t) + ' <span class="muted">' + esc(w.d) + ' &middot; ' +
            stamp(blk[0]) + '</span></a>' +
            '<div>' + around(blk[1], parts) + '</div></li>');
        });
      });
    }

    count.textContent = n ? (n >= 300 ? "First 300 matches" : n + " found") : "Nothing found";
    out.innerHTML = rows.length ? '<ul class="results">' + rows.join("") + "</ul>" : "";
  }

  function later() { clearTimeout(timer); timer = setTimeout(run, 120); }

  fetch("assets/search.json").then(function (r) { return r.json(); })
    .then(function (d) { data = d; run(); })
    .catch(function () { count.textContent = "The search index did not load."; });

  deep.addEventListener("change", function () {
    if (!deep.checked || wordsAsked) { run(); return; }
    wordsAsked = true;
    count.textContent = "Fetching the transcripts…";
    fetch("assets/transcripts.json").then(function (r) { return r.json(); })
      .then(function (d) { words = d; run(); })
      .catch(function () { count.textContent = "The transcripts did not load."; });
  });

  q.addEventListener("input", later);
})();
"""

# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def build(out_dir: Path, outputs: Path) -> dict[str, int]:
    master = read_json(CONFIG / "master_index.json", [])
    if not master:
        raise SystemExit("avlokan/master_index.json is missing — build the index first.")
    jobs = job_index(outputs)

    for sub in ("d", "texts", "assets"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    fixes = read_json(CORRECTIONS_FILE, {})
    all_sittings: list[dict[str, Any]] = []
    day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    moved: list[tuple[str, str]] = []
    for entry in master:
        if not re.match(r"\d{4}-\d{2}-\d{2}", entry.get("date", "")):
            continue
        same_day = sittings(entry)
        moved.extend(apply_corrections(same_day, fixes))
        all_sittings.extend(same_day)
        day[entry["date"]] = same_day

    unplaced, created = attach_jobs(all_sittings, jobs)
    for job, why in unplaced:
        print(f"  ! transcript not placed: {job['label']} — {why}")
    for s in created:
        print(f'  + sitting not in any catalogue: {s["date"]} {s["scripture"]} '
              f'{s["part"]} — page built from the recording alone')
    for s in created:
        all_sittings.append(s)
        day[s["date"]].append(s)

    build_series(all_sittings)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in all_sittings:
        groups[s["scripture"]].append(s)
    # So the correction form can offer the texts that already exist rather
    # than inviting a fifth spelling of Dravya Drushti Prakash.
    KNOWN_TEXTS[:] = sorted(groups)

    pages = 0
    for name, group in groups.items():
        group.sort(key=lambda s: (s["date"], s["part"]))
        for s in group:
            path = out_dir / "d" / f"{sitting_slug(s)}.html"
            path.write_text(discourse_page(s, day[s["date"]]), encoding="utf-8")
            pages += 1
        (out_dir / "texts" / f"{slug(name)}.html").write_text(
            text_page(name, group), encoding="utf-8")

    # Written after the real pages, and only where a real page did not take
    # the address back: two sittings that swapped texts each leave an address
    # the other one now occupies.
    written = {sitting_slug(s) for s in all_sittings}
    for was, now in moved:
        if was in written:
            continue
        written.add(was)
        (out_dir / "d" / f"{was}.html").write_text(
            redirect_page(f"{now}.html"), encoding="utf-8")

    # Sweep up pages that no longer correspond to anything. A sitting that
    # changes text — because a merge reclassified it or a correction moved it
    # — is written at its new address, and the old file simply stayed behind:
    # 9 February 2002 sat under "Other discourses" for weeks after it became a
    # Dravya Drushti Prakash sitting, as a full page, indexed and linkable.
    # A correction leaves a redirect; everything else leaves nothing.
    stale = 0
    for page in (out_dir / "d").glob("*.html"):
        if page.stem not in written:
            page.unlink()
            stale += 1
    for page in (out_dir / "texts").glob("*.html"):
        if page.stem not in {slug(name) for name in groups}:
            page.unlink()
            stale += 1
    if stale:
        print(f"  - {stale} page(s) removed: nothing in the archive is at "
              f"that address any more")

    (out_dir / "index.html").write_text(index_page(groups), encoding="utf-8")
    about = about_page()
    if about:
        (out_dir / "about.html").write_text(about, encoding="utf-8")
    shots = gallery_page()
    if shots:
        (out_dir / "photographs.html").write_text(shots, encoding="utf-8")
    # Every sitting, small: slug -> text, date, reference, length. The front
    # page keeps what you were listening to under the page's own address, and
    # an address is not a title — so anyone whose positions were saved before
    # that list existed has history the page cannot name. This lets it ask.
    # Fetched only when there is something to resolve; 12 KB over the wire,
    # and nothing at all for a first-time reader.
    (out_dir / "assets" / "sittings.json").write_text(
        json.dumps({sitting_slug(s): [s["scripture"], pretty_date(s["date"]),
                                      reference_label(s) or "",
                                      s["published"].get("minutes") or 0]
                    for s in all_sittings},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    counts = search_index(all_sittings, out_dir)
    (out_dir / "search.html").write_text(search_page(), encoding="utf-8")
    (out_dir / "assets" / "search.js").write_text(SEARCH_JS, encoding="utf-8")
    (out_dir / "embed-check.html").write_text(
        embed_check_page(all_sittings), encoding="utf-8")
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "assets" / "check-embeds.js").write_text(CHECK_JS, encoding="utf-8")
    aphorisms = book_pages(out_dir)
    if IMAGES.exists():
        covers = out_dir / "assets" / "covers"
        covers.mkdir(parents=True, exist_ok=True)
        for image in IMAGES.glob("*.jpg"):
            shutil.copy2(image, covers / image.name)
    (out_dir / "assets" / "site.css").write_text(CSS, encoding="utf-8")
    (out_dir / "assets" / "site.js").write_text(JS, encoding="utf-8")
    # These scripts are written as ordinary Python strings, where `\b` is a
    # backspace and `\n` is a newline. Both have shipped: a regex that
    # compiled to /[?&]edit<BS>/ and matched nothing, and a join that emitted
    # a real line break in the middle of a statement. Neither raises anything
    # — the browser just quietly does the wrong thing — so the build checks.
    for name in ("site.js", "search.js", "check-embeds.js", "site.css"):
        asset = out_dir / "assets" / name
        if not asset.exists():
            continue
        stray = [c for c in asset.read_text(encoding="utf-8")
                 if ord(c) < 32 and c not in "\t\n\r"]
        if stray:
            raise SystemExit(
                f"{name} contains {len(stray)} control character(s) "
                f"({', '.join(sorted({hex(ord(c)) for c in stray}))}) — almost "
                f"certainly a backslash escape in build.py that Python ate. "
                f"Double the backslash.")
    (out_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    # GitHub Pages runs Jekyll over whatever it is given unless told not to,
    # which costs a minute a deploy and would quietly drop any file whose name
    # began with an underscore.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    # The domain, written into the site itself and not only into a setting on
    # the repository. A workflow deploy uploads this directory as the whole of
    # the site, and GitHub reads the custom domain back out of it: without
    # this file a later deploy can quietly drop the domain and the archive
    # goes back to answering only at github.io. It is also the one place a
    # reader of this repository can see where the site is supposed to live.
    (out_dir / "CNAME").write_text(f"{SITE_DOMAIN}\n", encoding="utf-8")

    return {"discourses": pages, "texts": len(groups), "aphorisms": aphorisms,
            "transcribed": sum(1 for g in groups.values() for s in g if s["job"]),
            "unplaced": len(unplaced)}


def main() -> None:
    ap = argparse.ArgumentParser(prog="avlokan-site")
    ap.add_argument("--out", default=str(ROOT / "site"))
    ap.add_argument("--outputs", default=str(OUTPUTS_DEFAULT))
    args = ap.parse_args()
    out = Path(args.out)
    stats = build(out, Path(args.outputs))
    print(f"built {stats['discourses']} discourse pages across {stats['texts']} texts "
          f"({stats['transcribed']} with transcripts) and {stats['aphorisms']} aphorisms")
    print(f"  {out}/index.html")


if __name__ == "__main__":
    main()
