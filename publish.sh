#!/usr/bin/env bash
#
# The weekend run. Reads the channel, folds any new recordings into the
# archive, rebuilds the site, shows what changed, and publishes only if you
# say so.
#
#   ./publish.sh            look at what would change, publish after confirming
#   ./publish.sh --dry-run  look only
#   ./publish.sh --yes      publish without asking
#
# Every step is safe to repeat. The merge keeps a timestamped backup of the
# index and reports anything it could not place rather than guessing.

set -euo pipefail
cd "$(dirname "$0")"

CHANNEL="https://www.youtube.com/@SanatanTatva/videos"
LIST="work/youtube-videos.tsv"

dry=0; assume_yes=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry=1 ;;
    --yes|-y)  assume_yes=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
die() { printf '\n%s\n' "$1" >&2; exit 1; }

command -v yt-dlp  >/dev/null || die "yt-dlp is not installed.  brew install yt-dlp"
command -v python3 >/dev/null || die "python3 is not on the PATH."

say "1. Reading the channel"
mkdir -p work
# Written to a temporary file first: a half-finished list is worse than the
# old one, and yt-dlp gets rate-limited often enough for that to matter.
if yt-dlp --flat-playlist --skip-download --ignore-errors --no-warnings \
     --print "%(id)s\t%(upload_date)s\t%(duration)s\t%(view_count)s\t%(title)s" \
     "$CHANNEL" > "$LIST.new" 2> work/youtube.log && [ -s "$LIST.new" ]; then
  before=$([ -f "$LIST" ] && wc -l < "$LIST" || echo 0)
  mv "$LIST.new" "$LIST"
  printf '   %s videos on the channel (was %s)\n' "$(wc -l < "$LIST" | tr -d ' ')" "$(echo "$before" | tr -d ' ')"
else
  rm -f "$LIST.new"
  [ -f "$LIST" ] || die "Could not read the channel and there is no earlier list to fall back on.
Check work/youtube.log — YouTube rate-limits this and usually clears within the hour."
  printf '   Could not read the channel; carrying on with the list from %s\n' \
    "$(date -r "$LIST" '+%d %b %H:%M')"
fi

say "2. Folding it into the archive"
python3 -m avlokan_site.merge_youtube "$LIST" $([ $dry -eq 1 ] && echo "" || echo "--write")

say "3. Building the site"
python3 -m avlokan_site.build

say "4. What changed"
if [ -z "$(git status --porcelain)" ]; then
  printf '   Nothing. The archive already matches the channel.\n'
  exit 0
fi
# Listed with awk rather than `| head -30`, which is what this used to do.
# `head` closes the pipe as soon as it has its thirty lines; git status is
# still writing, takes SIGPIPE, and under `set -euo pipefail` that killed the
# whole script — silently, after printing the changes, before committing any
# of them. It only happened when there was a lot to publish: a handful of
# files finishes writing before head closes, a thousand does not. So the
# weekly runs worked and the big one did nothing, while looking identical.
# awk reads to the end.
changed=$(git status --porcelain)
total=$(printf '%s\n' "$changed" | wc -l | tr -d ' ')
printf '%s\n' "$changed" | awk 'NR<=30 { print "   " $0 }'
if [ "$total" -gt 30 ]; then
  printf '   … and %s more\n' "$((total - 30))"
fi

if [ $dry -eq 1 ]; then
  printf '\n   Dry run — nothing written, nothing published.\n'
  exit 0
fi

if [ $assume_yes -eq 0 ]; then
  printf '\nPublish these? [y/N] '
  read -r reply
  case "$reply" in [yY]*) ;; *) printf '   Left alone. Your changes are still here.\n'; exit 0 ;; esac
fi

say "5. Publishing"
git add -A
git commit -q -m "Archive update, $(date '+%-d %B %Y')"
git push -q origin main
printf '   Pushed. It is live in about a minute:\n   https://avlokan.org\n'
