# Avlokan

The recorded discourses of **Shri Devchand bhai Shah** (d. 2002), and the book
he wrote, published as a website: <https://avlokan.org>

He was a householder, not a monk. Between 1994 and 2002 he taught more than
thirteen hundred sittings on Shrimad Rajchandra's *Vachanamrut*, *Samaysar*,
*Dravya Drushti Prakash* and other texts, and wrote *Avlokan — આત્મ
નિરીક્ષણની અનૂઠી કળા*, a hundred and twelve numbered aphorisms on the
observation of one's own states.

This repository holds the catalogue of those sittings, the book, and the
script that turns them into the site.

## What is here

```
avlokan/            the archive itself — catalogues, the book, corrections
avlokan_site/       the generator, and its own README explaining every rule
site/               the built website, which is what gets published
.github/workflows/  publishes site/ to GitHub Pages on push
```

The recordings are not here. They are on YouTube, and the masters are on a
drive. Nothing in this repository is larger than a scanned book cover.

## Rebuilding it

```bash
python3 -m avlokan_site.build
```

Python's standard library, nothing else. No framework, no bundler, no package
to install, no server, no database. That is deliberate: this has to still work
when nobody is maintaining it.

The one thing the generator wants that is not in this repository is
`outputs/<job>/subtitles.vtt` — the transcripts, which live beside the media
on the machine that produced them. Without them the site builds exactly the
same, with every page present and the transcript sections saying so.

## The data

| file | what it is |
|---|---|
| `master_index.json` | every sitting, by date: what text, what video, what audio |
| `audio_catalogue.json` | 1,229 recordings from the original written list |
| `catalogue.json` | the video sheet |
| `book.json` | the book, 114 aphorisms, one record each, separately correctable |
| `book_front.txt` | the book's title page, which precedes the first aphorism |
| `about.md` | the biographical note |
| `images/` | scanned book covers and 14 photographs, from avlokan.org |

Four files record deliberate changes to that data, each entry carrying its
reasoning so a later reader can disagree with it:

- `date_corrections.json` — dates wrong at the source
- `book_corrections.json` — typing errors in the book
- `audio_corrections` (applied by `clean_audio.py`) — OCR damage in the list
- `not_pravachan.json` — videos on the channel that are not discourses

Nothing about the archive is corrected silently.

## Keeping it current

```bash
# after a weekend's uploads
yt-dlp --flat-playlist --skip-download --ignore-errors \
  --print "%(id)s\t%(upload_date)s\t%(duration)s\t%(view_count)s\t%(title)s" \
  "https://www.youtube.com/@SanatanTatva/videos" > work/youtube-videos.tsv

python3 -m avlokan_site.merge_youtube work/youtube-videos.tsv --write
python3 -m avlokan_site.build
git add -A && git commit -m "..." && git push     # publishes
```

`avlokan_site/README.md` explains what each step does and, more usefully, the
mistakes each rule exists to prevent.

## Licence

The teachings are offered freely. Copy them, re-host them, translate them.
