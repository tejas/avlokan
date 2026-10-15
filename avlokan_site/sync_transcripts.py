"""Copy each job's timed transcript into transcripts/, where git can track it.

The pipeline writes `subtitles.vtt` and `passage_matches.json` into
outputs/<job>/, and since 14 October 2026 every outputs/ entry is a symlink into
~/Pravachan Process/Sittings. Git does not follow a symlink to a file, so
tracking them in place made every transcript show as deleted — and a
`git add -A` would have committed that. The build reads transcripts/ instead;
this is the step that keeps it current. Run by publish.sh before the build.

Only files whose content differs are rewritten, so a run that changes nothing
leaves no trace in `git status`.
"""
from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
TRANSCRIPTS = ROOT / "transcripts"
KEPT = ("subtitles.vtt", "passage_matches.json")


def sync() -> tuple[list[str], list[str]]:
    added, updated = [], []
    for job in sorted(OUTPUTS.iterdir()):
        # GPtest* are pipeline test runs, not sittings.
        if job.name.startswith("GPtest") or not job.is_dir():
            continue
        for name in KEPT:
            src = job / name
            if not src.is_file():
                continue
            dst = TRANSCRIPTS / job.name / name
            if dst.exists():
                if filecmp.cmp(src, dst, shallow=False):
                    continue
                updated.append(f"{job.name}/{name}")
            else:
                added.append(f"{job.name}/{name}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    return added, updated


if __name__ == "__main__":
    added, updated = sync()
    print(f"transcripts: {len(added)} added, {len(updated)} updated")
    for line in added:
        print("  + " + line)
    for line in updated:
        print("  ~ " + line)
