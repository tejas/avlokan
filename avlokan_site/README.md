# The site generator

Turns what Avlokan has produced into a static archive site.

```bash
python3 -m avlokan_site.build            # writes ./site
python3 -m http.server 8799 -d site      # look at it
```

Re-run it whenever transcripts change. It rewrites everything from scratch;
there is no state and nothing to migrate.

## What it makes

```
site/
  index.html                  every text, with counts
  texts/<text>.html           the sittings for one text, by date
  d/<date>-<text>[-<n>].html  one sitting: video, transcript, reference
  book/index.html             all 112 aphorisms
  book/<n>.html               one aphorism, separately correctable
  about.html
  assets/site.css
  assets/site.js
  robots.txt
```

**A date is not a sitting.** He often taught twice in a day and sometimes three
times, occasionally from different texts — 29 March 2002 is Dravya Drushti
Prakash 17 in two parts *and* Apoorva Avasar 20. Keying pages by date collapsed
those into one page and hung one text's transcript under another's title. The
unit is the sitting; the `-2` suffix is the second sitting of that text that
day, and each page links the others from the same day.

Transcripts are matched to sittings on date, then text, then order. The part
number in a filename like `Prakash17-3` counts sittings on patra 17 *across
days*, not within the day, so only the ordering is used. Anything that cannot
be placed is reported at build time and left unattached — a transcript on the
wrong discourse is worse than a discourse with none.

Current build: **1,337 discourse pages across 29 texts, ~30 MB total.**
13 have a transcript. 757 of them sit in one of 158 series.

## Why it is built this way

**The transcript is real text in the HTML.** Not fetched, not rendered by
script. That is what Google indexes, what an AI assistant can read, and what
still says something useful if the file is opened in twenty years from a web
archive. A page with a transcript is ~56 KB, of which ~40 KB is the teaching
itself.

**Everything interactive is added afterwards by script.** The synced
transcript, the resume position and the highlights are enhancements layered on
a page that is already complete. If the script never runs — old browser,
blocked, recovered from an archive — you still have the whole discourse as
readable text and a plain link to the recording.

**Nothing is requested from YouTube until you press play.** The page draws its
own play button; the iframe is created by the click. Not even a thumbnail is
fetched, so opening a discourse page contacts nobody. A `<noscript>` link and a
normal "Watch on YouTube" link are always in the markup.

This was not true for a while: the iframe was created as the page loaded,
directly under a comment claiming it was not.

### Roughly a third of the recordings refuse to be embedded

Nothing in a video's public settings says so. The ones that refuse report
`playableInEmbed: true`, `isPrivate: false`, `isUnlisted: false` and the same
category as the ones that work; `yt-dlp` fetches them happily. A bare iframe
with no parameters at all, on `www.youtube.com`, is refused just the same — so
this is a property of the video, not of how the archive asks for it. It is
visible only in YouTube Studio, under Content → Restrictions.

Two things follow.

**The page says so plainly.** When the player reports error 101 or 150 — the
owner or a rights holder disallowed embedding — the black "This video is
unavailable" box is replaced with a sentence explaining that the restriction
is on the video and a link to where it does play.

The likeliest cause is a Content ID claim on the closing stuti — a different
recording of it was used on most of the Shrimad Rajchandra patranks, which
would explain why the refusals are not spread evenly. The page reports the
refusal rate per text, which is what tests that: if the stuti is the cause,
the refusals cluster.

**`/embed-check.html` finds them all.** Linked from nowhere and marked
noindex, it walks every video in the archive asking the player to load it and
lists the refusals with their dates and pages. A pass takes about ten minutes;
results are kept in `localStorage` as they are found, so a reload resumes
rather than restarts. Two details it needs to be right about:

- **Readiness means nothing on its own.** A refused recording readies first and
  reports the refusal a moment later, so a player that says it is ready has to
  be given time to complain before being counted as fine. Settling on
  `onReady` made the first version report zero refusals out of three hundred.
- **The API has to be loaded on every page with a video**, not only the ones
  with a transcript. It sat below an early return that most pages take, so
  nothing was ever listening.

**The embed asks for as little as it can.** `enablejsapi=1` exists only to
drive the synced transcript, and it drags the `origin` parameter along with
it. Every page was asking for both — 1324 of 1337 have no transcript at all —
and some recordings answered with "This video is unavailable" over a video
that was public and perfectly embeddable. A page with no transcript now
requests:

```
https://www.youtube-nocookie.com/embed/<id>?rel=0&playsinline=1
```

and only the thirteen pages with a timed transcript add `enablejsapi` and
`origin`. If a recording still refuses to start, `EMBED_HOST` at the top of
the player code switches from `youtube-nocookie.com` to `www.youtube.com`.

**No video is self-hosted.** Storage is cheap but bandwidth is not, and a
recurring bill is a countdown timer on an archive meant to outlive the person
paying it. YouTube serves playback; the Internet Archive is the permanent
fallback. Each discourse page carries an empty `data-archive` slot ready for an
Archive identifier once the mirror exists.

**No framework, no bundler, no dependencies.** Python standard library to
build, plain CSS and one small script to run. Nothing here needs updating to
keep working.

## Enhancements, all local

- **Synced transcript** — the current line highlights and scrolls into view,
  driven by `subtitles.vtt`, which the pipeline already writes.
- **Click any timestamp** to jump the video there.
- **Resume** — where you stopped, per discourse, in `localStorage`.
- **Highlights** — double-click a passage to mark it; *Copy my highlights*
  puts them on the clipboard with timestamps and the page URL.

No account, no server, no database. Nothing leaves the browser.

## Where the content comes from

| page element | source |
|---|---|
| title, date, text, reference, location | `avlokan/master_index.json` |
| YouTube id | the channel listing, merged into the master index |
| cover image, photograph | `avlokan/images/`, copied from avlokan.org |
| timed transcript | `outputs/<job>/subtitles.vtt` |
| fallback plain transcript | `outputs/<job>/corrected_mixed_script_transcript.txt` |
| restored recitations note | `outputs/<job>/passage_matches.json` |

A sitting with no transcript still gets a page — title, date, reference and the
recording. Those pages are worth publishing: they are what makes the archive
findable while the transcription backlog is worked through.

## Keeping the video list current

The Studio CSV export stops at 500 rows, which is why the index once knew about
262 videos when the channel holds 851. The CSV also lists videos that have been
hidden from public view, which is how a school dance and some celebration
footage ended up in a discourse archive. Read the list from the channel
instead — it sees only what is actually published:

```bash
yt-dlp --flat-playlist --skip-download --ignore-errors \
  --print "%(id)s\t%(upload_date)s\t%(duration)s\t%(view_count)s\t%(title)s" \
  "https://www.youtube.com/channel/UCU7w39JdVVbVtbRMYLf8VZw/videos" \
  > work/youtube-videos.tsv

python3 -m avlokan_site.merge_youtube work/youtube-videos.tsv          # report
python3 -m avlokan_site.merge_youtube work/youtube-videos.tsv --write  # apply
```

Idempotent, and it keeps a timestamped backup of the index. Run it after each
weekend's uploads. It reports every video it could not place rather than
guessing a date.

Optionally fetch real upload dates first — the flat listing does not carry
them, and without the file the channel's own newest-first ordering is used
instead:

```bash
yt-dlp --skip-download --ignore-errors --print "%(id)s\t%(upload_date)s" \
  -a work/ids.txt > work/youtube-uploaded.tsv
```

### Series

He taught a letter across several days, so the sitting a listener wants next
is usually the next one in that run — more than anything else recorded the
same day. Each discourse page carries **Part *n* of *m***, a link to the
previous and next sitting, and the whole run behind a disclosure.

A series is **the text plus the letter, gatha or bol number** — `Patrank 247`
is a run of six sittings. Deliberately *not* the playlist: a playlist only
holds what was published, so keying on it would leave out the sittings that
survive as audio alone. Solahkaran Bhavana part 36 was never put on YouTube,
and that series is 43 sittings, not the 42 in the playlist. The playlist does
supply the name.

Three rules were each learned from getting it wrong:

- **Split on the calendar, then order within the run.** Ordering by part
  number first let a letter taught in 1999 and again in 2001 interleave into
  a single run that went backwards in time. A gap of four months starts a new
  series.
- **Order by date, with the part number breaking ties within a day.** Sorting
  by part number pushed every unnumbered sitting to the end of its run,
  including the ones he gave first — the opening Darshanmoha sitting has no
  number and came before part 2.
- **Playlist position means nothing.** YouTube appends a video to the end of a
  playlist when you add it, so Solahkaran Bhavana part 24, added later, sits
  at position 42.

Where a text has no reference numbers, its sittings only form a series if he
numbered them. Tatva Charcha is 51 separate discussions across sixteen months,
not a run of 51.

### The playlists are the authority for which text a sitting belongs to

He keeps a playlist per series on the channel — 64 of them, organised by
letter rather than by text: *Patrank 572, Shrimad Rajchandra Vachanamrut, Oct
2000*. That is curation, not inference, so it outranks both the spreadsheet
column and anything read out of a title:

```bash
yt-dlp --flat-playlist --skip-download --no-warnings --print "%(id)s\t%(title)s" \
  "https://www.youtube.com/@SanatanTatva/playlists" > work/playlists.tsv
# then, per playlist, into work/playlist-items.tsv — see the loop in this repo's history
```

Reading a text out of a title gets it wrong in ways that are invisible until
you check against the playlists. **99 Benshri ke Vachanamrut sittings were
filed under Shrimad Rajchandra's Vachanamrut**, because "Benshri ke
Vachanamrut" contains "vachanamrut" and the general pattern was tested first.
Ten Daslakshan Parva discourses were filed under Samaysar for the same reason.

Two rules keep the matching honest:

- **Specific before general** in `CANON`. The first pattern that matches wins.
- **`VETO`**, for the pairs no ordering can separate. *Dravya Drashti
  Jineshwar* begins with a *Dravya Drushti Prakash* keyword and is a different
  text; only the word "jineshwar" tells them apart.

Playlist titles are matched on whichever text is named *earliest*, not by
`CANON` order, because they often mention a second text in passing.

### The audio catalogue and the video titles disagree about dates

Within a series the part number is reliable and the date is not. Solahkaran
Bhavana part 35 is 29 September in the audio list and 28 September on the
video; part 40 is 6 October against 5 October. Each disagreement invented a
second, video-less sitting — which is why that series showed 46 sittings when
he taught 43.

`fold_audio` moves the audio entry onto the video's date, keyed on text *and*
letter number *and* part, within a seven-day window. All three are needed:
every letter has its own Part 2, so `Patrank 548 Part 2` and `Patrank 550 Part
2` are a week apart and quite different sittings.

### Duplicate recordings

The same sitting can be published more than once: he has been restoring the
old tapes, and a restored re-upload sounds and looks better than the original.
12 July 2002 is on the channel twice over — Patrank 751 parts 1 and 2 uploaded
in 2017 and again in 2025.

The merge keeps the newest as the sitting's video and marks the earlier ones
`superseded_by` rather than deleting them, so the page still offers them and
no link that was ever published stops resolving. **22 uploads are currently
superseded.**

A second case has nothing to do with recency: a sitting published both whole
and cut into `(segment 1)` + `(segment 2)`, all on the same day. Where the
segments add up to the length of the whole — 340s + 2501s against 2841s on 27
March 2002 — the whole is the sitting and the pieces become alternates. If the
durations do not add up, nothing is folded, because then they really are
different recordings.

## Not done yet

- Internet Archive identifiers (the `data-archive` slot is empty)
- Romanized search text is generated but not yet surfaced
- English renderings exist for 4 of 112 aphorisms
- 4 channel videos are unlinked: they have no date in the title, or name no
  recognised text
- 17 more are re-uploads already linked under the id the video sheet recorded

## A text is not one long list

Shrimad Rajchandra's Vachanamrut is 352 sittings. As a single table that is
unusable — nobody scrolls 352 rows looking for a letter. He did not teach it as
one list either: he took a letter and stayed with it for six or twenty
sittings, then moved on. Those runs are the natural divisions, and the archive
already knows them from the reference numbers, so each text page is broken into
them — 77 runs for that text, each with its own heading and count.

The index at the top is **ordered by number while the page stays in the order
he taught**, because someone looking for a letter knows its number, not its
date. He returned to the same letter years apart — Patrank 108 appears four
times — so where a number repeats the years are shown to tell them apart.

The heading says only the letter. On a page titled *Shrimad Rajchandra
Vachanamrut* a heading reading "Patrank 108, Shrimad Rajchandra Vachanamrut" is
noise; and where the source records only a bare number, the word is supplied
from the text — 236 in the Vachanamrut is a patrank, in Samaysar a gatha.

Sittings belonging to no run are gathered at the end rather than given a
heading each. Tatva Charcha is 77 separate discussions, not 77 runs of one.

## What a reader can actually get

Each sitting is one of three things, and the middle one is the reason to say
so: the audio list records 1,229 recordings, and most have never been put
online. Those sittings are not lost — the tape exists — but there is nothing
to play, and a reader deserves to know which is which before clicking.

- **Published** — on YouTube, watchable here
- **Recorded, not published** — listed in the audio catalogue, not online
- **No recording** — known from the catalogues, nothing recorded or listed

Shown as a marked dot on every text listing, with a tally at the top of each,
and spelled out in a sentence on the discourse page itself.

## Images

`python3 -m avlokan_site.fetch_images` copies the scanned book covers and the
photographs from avlokan.org — his own site — into `avlokan/images/`, and the
build copies them into `site/assets/covers/`. Nothing is hotlinked: an image
served from someone else's CDN is a dependency on a subscription staying paid,
and this archive is meant to outlive that.

It also brings down the fourteen photographs from the old site's Gallery,
which become `/photographs.html`. They arrive named `64.jpeg` and `31 2.jpeg`,
so there is nothing to caption them with; they are published unlabelled,
because a picture of him teaching is worth more than a caption and someone who
was there can write one later. They are stored at 1600px — they were web
copies to begin with, and 20 MB of them in a repository this size is not.

Fourteen texts have their own cover. The rest fall back to a plain hrim on
grey, so every card in the index grid is the same size. Note that the alt text
on the source site reads "Shrimad Rajchandra Vachanamrut Cover page" on *every*
image — an unedited template — so the covers are mapped by filename, not by
what the page claims they are.

## The audio catalogue was read off a scan

`python3 -m avlokan_site.clean_audio [--write]` repairs what the scan got
wrong and syncs the result into the master index, keeping a backup of both.

The ordinary damage is `l` for `i` and `t` for `l` — "Soiah Karan Bhavna",
"Talva Charcha", "Adhyalma Ganga". The damaging kind is Cyrillic: the reader
put `А` and `М` where the Latin letters are drawn identically. **Eight entries
carry their date inside the subject where the date column is empty, and
because `11-Аpr-1999` begins with a Cyrillic А, nothing recognised it as a
date — those eight sittings were missing from the archive entirely.**

Two rules the first version got wrong, both destructive:

- **Asterisks are his, not the scan's.** `***M.imp` is his own emphasis and
  how many he wrote carries meaning. Stripping leading punctuation took them
  off 180 entries.
- **A leading number is only a row number when it is *that row's* number.**
  Stripping any leading digits ate the day out of `12 Sep-1999 Srimad
  Rajchandra 449`.

One entry, #352, was too mangled for any rule to reach and is corrected by
hand with the reasoning beside it.

## Things deliberately not in the archive

`avlokan/not_pravachan.json` lists videos on the channel that are not
discourses — a school annual-day performance, celebration footage. Most are
already hidden on YouTube; they reached the index through the Studio CSV, which
lists hidden videos. Keeping them named in a file means a future CSV import
cannot quietly resurrect them. Devotional singing is *not* on that list:
bhajans belong here, under Bhakti.

`avlokan/date_corrections.json` holds dates that are wrong at the source — a
title typed `2011` for a man who died in 2002, a folder named `30Mar202`. Each
one records what it was changed to and the evidence for it, because a corrected
date with no reasoning behind it is indistinguishable from a mistake.
