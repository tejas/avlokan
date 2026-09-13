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
          head_extra: str = "", script: str = "", trail: list[tuple[str, str]] | None = None) -> str:
    up = "../" * depth
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
  <a class="navlink" href="{up}about.html">About</a>
</header>
<main>
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
    reference, location = s["reference"], s["location"]
    pub, aud, job = s["published"], s["audio"], s["job"]
    youtube = s["youtube_id"]
    title = sitting_title(s)

    bits = [f'<article class="discourse">',
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

    # Video. The iframe is written by script so the page does not phone Google
    # just for being opened; the link below always works either way.
    if youtube:
        bits.append(
            f'<div class="video" data-youtube="{e(youtube)}">'
            f'<noscript><p><a href="https://www.youtube.com/watch?v={e(youtube)}">'
            f'Watch on YouTube</a></p></noscript></div>'
        )
        bits.append(
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
            if all(a["reason"] == "segment" for a in alts):
                note = ("The same recording is also published cut into pieces, "
                        "which together run to the same length")
            elif len(alts) == 1:
                note = ("The video above is the most recent upload of this "
                        "sitting. The earlier one is still there")
            else:
                note = ("The video above is the most recent upload of this "
                        "sitting. The earlier ones are still there")
            bits.append(f'<p class="links muted">{note}: {links}.</p>')
    elif aud:
        bits.append(
            f'<p class="links muted">Audio recording catalogued; not yet published. '
            f'Entry {e(aud.get("n", ""))} in the archive list.</p>'
        )
    elif s["uncatalogued"]:
        bits.append(
            '<p class="links muted">This sitting appears in no catalogue. The page '
            'exists because the original recording does; nothing is published for it '
            'yet.</p>'
        )
    else:
        bits.append('<p class="links muted">No recording is published for this sitting yet.</p>')

    blocks = parse_vtt(job["vtt"]) if job else []
    passages = (job or {}).get("passages") or []

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
                f'<p class="block" data-start="{b["start"]:.2f}" data-end="{b["end"]:.2f}">'
                f'<span class="t">{fmt_hms(b["start"])}</span>'
                f'<span class="w">{e(b["text"]).replace(chr(10), "<br>")}</span></p>'
            )
        bits.append("</div></section>")
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

    bits.append("</article>")
    return shell(title, "\n".join(bits), depth=depth,
                 description=f"{scripture}. {pretty_date(date)}. {reference}".strip(),
                 trail=[("Avlokan", "../index.html"),
                        (scripture, f"../texts/{slug(scripture)}.html"),
                        (pretty_date(date), "")])


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


def series_name(s: dict[str, Any]) -> str:
    """What to call the run, for a heading."""
    if s.get("run_name"):
        return s["run_name"]
    if s.get("series"):
        return s["series"]
    ref = reference_number(s)
    return f'{s["scripture"]} {ref}' if ref else s["scripture"]


def text_page(name: str, group: list[dict[str, Any]]) -> str:
    rows = []
    for s in group:
        pub = s["published"]
        when = pretty_date(s["date"])
        if s["of"] > 1:
            when += f' <span class="muted">&middot; {s["part"]} of {s["of"]}</span>'
        have = []
        if pub:
            have.append("video")
        elif s["audio"]:
            have.append("audio")
        if s["job"]:
            have.append("transcript")
        part = f'{s["part_no"]}' if s["part_no"] is not None else ""
        rows.append(
            f'<tr><td class="pt">{e(part)}</td>'
            f'<td><a href="../d/{sitting_slug(s)}.html">{when}</a></td>'
            # The audio list uses the text's own name as the subject, which
            # says nothing on a page already titled with it.
            + f'<td>{e("" if says_nothing(s["reference"], name) else s["reference"][:52])}</td>'
            f'<td>{e(pub.get("minutes", "") and str(pub["minutes"]) + " min")}</td>'
            f'<td>{e(", ".join(have)) or "&mdash;"}</td></tr>'
        )
    days = len({s["date"] for s in group})
    word = "sitting" if len(group) == 1 else "sittings"
    count = (f"{len(group)} {word}" if days == len(group)
             else f"{len(group)} {word} across {days} days")
    art = cover(name, depth=1)
    head = (f'<div class="text-head">'
            + (f'<img class="cover" src="{e(art)}" alt="Cover of {e(name)}" '
               f'width="780" height="1080">' if art else "")
            + f'<div><h1>{e(name)}</h1><p class="meta">{count}</p></div></div>')
    body = (head +
            '<table class="listing"><thead><tr><th>Part</th><th>Date</th>'
            '<th>Reference</th><th>Length</th><th>Available</th>'
            '</tr></thead><tbody>'
            + "\n".join(rows) + "</tbody></table>")
    return shell(f"{name} — {SITE_TITLE}", body, depth=1,
                 trail=[("Avlokan", "../index.html"), (name, "")])


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
    body = (
        f'<h1>{e(SITE_TAGLINE)}</h1>'
        f'<p class="lede">{e(SITE_DESC)}</p>'
        f'<p class="meta">{total} sittings &middot; {done} transcribed</p>'
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


def about_page() -> str:
    source = CONFIG / "about.md"
    if not source.exists():
        return ""
    body = render_prose(source.read_text(encoding="utf-8"))
    photo = ""
    if (IMAGES / "devchand-bhai.jpg").exists():
        photo = ('<figure class="portrait">'
                 '<img src="assets/covers/devchand-bhai.jpg" width="1024" height="649"'
                 ' alt="Shri Devchand bhai Shah, holding two of the texts he taught from">'
                 '<figcaption>Shri Devchand bhai Shah</figcaption></figure>')
    return shell(f"About — {SITE_TITLE}", f'<article class="prose">{photo}{body}</article>',
                 depth=0, description=SITE_DESC, script="none",
                 trail=[("Avlokan", "index.html"), ("About", "")])


# --------------------------------------------------------------------------
# the book
# --------------------------------------------------------------------------

def aphorism_html(item: dict[str, Any], *, linked: bool = True, depth: int = 1) -> str:
    up = "../" * depth
    n = item["n"]
    label = (f'<a class="n" href="{up}book/{n}.html">Aphorism {n}</a>' if linked
             else f'<span class="n">Aphorism {n}</span>')
    guj = "".join(f'<p>{e(par)}</p>' for par in item["gujarati"].split("\n\n") if par.strip())
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


def book_pages(out_dir: Path) -> int:
    items = read_json(CONFIG / "book.json", [])
    if not items:
        return 0
    (out_dir / "book").mkdir(parents=True, exist_ok=True)
    done = sum(1 for i in items if i.get("english"))

    intro = (
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
    body = intro + "\n".join(aphorism_html(i, depth=1) for i in items)
    (out_dir / "book" / "index.html").write_text(
        shell(f"Avlokan — the book", body, depth=1, script="none",
              description="Avlokan — the unique art of self-observation, by Shri Devchand bhai Shah.",
              trail=[("Avlokan", "../index.html"), ("The book", "")]),
        encoding="utf-8")

    for idx, item in enumerate(items):
        nav = []
        if idx:
            nav.append(f'<a href="{items[idx-1]["n"]}.html">&larr; {items[idx-1]["n"]}</a>')
        nav.append('<a href="index.html">All aphorisms</a>')
        if idx + 1 < len(items):
            nav.append(f'<a href="{items[idx+1]["n"]}.html">{items[idx+1]["n"]} &rarr;</a>')
        page = (aphorism_html(item, linked=False, depth=1)
                + f'<p class="links">{" &middot; ".join(nav)}</p>')
        (out_dir / "book" / f'{item["n"]}.html').write_text(
            shell(f'Avlokan, aphorism {item["n"]}', page, depth=1, script="none",
                  description=item["gujarati"][:150],
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

  /* type */
  --font-body:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  --font-indic:"Noto Sans Gujarati","Noto Sans Devanagari",var(--font-body);
  --size-xs:.75rem; --size-sm:.85rem; --size-md:.95rem; --size-base:1.0625rem;
  --size-lg:1.25rem; --size-xl:1.5rem;
  --leading-tight:1.3; --leading-body:1.75; --leading-indic:1.9;
  --weight-normal:400; --weight-medium:500; --weight-bold:600;
  --track-caps:.06em;

  /* space — one scale, used everywhere */
  --s-1:.25rem; --s-2:.5rem; --s-3:.75rem; --s-4:1rem;
  --s-5:1.5rem; --s-6:2rem; --s-7:3rem; --s-8:5rem;

  /* shape and layout */
  --radius:3px;
  --measure:46rem;          /* reading column           */
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
.block .w{flex:1;font-family:var(--font-indic);line-height:var(--leading-indic)}
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
 margin:var(--s-2) 0}
.aphorism .eng{color:var(--c-ink-soft);margin:var(--s-3) 0 0;
 padding-left:var(--s-4);border-left:var(--rule) solid var(--c-line)}
.aphorism .eng p{margin:var(--s-1) 0}
.aphorism .pending{color:var(--c-muted);font-size:var(--size-sm);font-style:italic;
 margin-top:var(--s-2)}
.book-head{border-bottom:var(--border);padding-bottom:var(--s-4);margin-bottom:var(--s-2)}

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

  function play(autoplay, seconds) {
    var id = holder.getAttribute("data-youtube");
    holder.innerHTML = "";
    if (apiReady && window.YT && YT.Player) {
      var mount = document.createElement("div");
      mount.id = "player-mount";      /* YT.Player needs an element with an id */
      holder.appendChild(mount);
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
    holder.appendChild(frame);
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

  if (!blocks.length) return;

  /* ---- 2. click a timestamp to jump there ---- */
  function seek(seconds) {
    if (player && player.seekTo) { player.seekTo(seconds, true); player.playVideo(); }
    else if (holder) {
      var i = holder.querySelector("iframe");
      if (i) i.src = i.src.replace(/([?&])start=\\d+/, "$1") + "&start=" + Math.floor(seconds) + "&autoplay=1";
    }
  }
  blocks.forEach(function (b) {
    var t = b.querySelector(".t");
    if (!t) return;
    t.setAttribute("role", "button");
    t.setAttribute("tabindex", "0");
    t.addEventListener("click", function () { seek(parseFloat(b.dataset.start)); });
    t.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); t.click(); }
    });
  });

  /* ---- 3. follow along, and remember where you stopped ---- */
  var KEY = "avlokan:pos:" + page;
  var MARKS = "avlokan:marks:" + page;
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

  function watch() {
    clearInterval(timer);
    timer = setInterval(function () {
      if (!player || !player.getCurrentTime) return;
      var t = player.getCurrentTime();
      highlight(t);
      try { localStorage.setItem(KEY, String(Math.floor(t))); } catch (err) {}
    }, 1000);
  }

  function toolbar() {
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
  var marked = {};
  try { marked = JSON.parse(localStorage.getItem(MARKS) || "{}"); } catch (err) {}
  blocks.forEach(function (b) {
    if (marked[b.dataset.start]) b.classList.add("is-marked");
    var w = b.querySelector(".w");
    if (!w) return;
    w.addEventListener("dblclick", function () {
      b.classList.toggle("is-marked");
      if (b.classList.contains("is-marked")) marked[b.dataset.start] = 1;
      else delete marked[b.dataset.start];
      try { localStorage.setItem(MARKS, JSON.stringify(marked)); } catch (err) {}
    });
  });

  toolbar();

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

    all_sittings: list[dict[str, Any]] = []
    day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in master:
        if not re.match(r"\d{4}-\d{2}-\d{2}", entry.get("date", "")):
            continue
        same_day = sittings(entry)
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

    pages = 0
    for name, group in groups.items():
        group.sort(key=lambda s: (s["date"], s["part"]))
        for s in group:
            path = out_dir / "d" / f"{sitting_slug(s)}.html"
            path.write_text(discourse_page(s, day[s["date"]]), encoding="utf-8")
            pages += 1
        (out_dir / "texts" / f"{slug(name)}.html").write_text(
            text_page(name, group), encoding="utf-8")

    (out_dir / "index.html").write_text(index_page(groups), encoding="utf-8")
    about = about_page()
    if about:
        (out_dir / "about.html").write_text(about, encoding="utf-8")
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
    (out_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    # GitHub Pages runs Jekyll over whatever it is given unless told not to,
    # which costs a minute a deploy and would quietly drop any file whose name
    # began with an underscore.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

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
