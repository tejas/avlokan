"""Extract the book into one editable record per aphorism.

Run once. After that, `avlokan/book.json` is the thing you edit — adding an
English rendering to a single aphorism without touching any other, which is the
whole point of splitting it this way. Re-running preserves every English
rendering already written.

    python3 -m avlokan_site.extract_book "/path/to/Avlokan book.md"
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "avlokan" / "book.json"
FIXES = ROOT / "avlokan" / "book_corrections.json"
FRONT = ROOT / "avlokan" / "book_front.txt"

GUJ_DIGITS = "૦૧૨૩૪૫૬૭૮૯"


def gujarati_number(text: str) -> int:
    return int("".join(str(GUJ_DIGITS.index(c)) for c in text))


def tidy(text: str) -> str:
    """Undo the artefacts of the DTP-to-markdown conversion.

    Nothing here changes a word. Every rule touches only whitespace, escape
    characters, or a stray mark the conversion introduced:

    * **Escaped punctuation.** The converter escaped hyphens and exclamation
      marks for markdown's benefit, so the text carries `\\-` and `\\!`.
    * **Space before punctuation.** The typesetting justified lines by
      widening the gaps between words, and the conversion took those gaps
      literally — leaving 617 full stops, 127 commas and 115 question marks
      standing a space away from the word they belong to.
    * **Stray dandas.** The book sets its sentences with the ordinary full
      stop, as modern Gujarati prose does — 1,135 of them against 5 dandas,
      and every one of those 5 sits directly against a comma or a full stop
      that is doing the actual work (`સત્સંગ।, ભક્તિ`, `તે વિચારો।.`). They
      are noise from the conversion, not punctuation, so the real mark is
      kept and the danda dropped. A danda standing on its own is left alone.
    """
    text = re.sub(r"\\([-_*!?.,;:()\[\]])", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)   # drop bold marks
    # An emphasis run that opens on one aphorism and closes on the next leaves
    # an unpaired `**` behind once they are split apart.
    text = text.replace("**", "")

    text = re.sub(r"।\s*([.,;:?!])", r"\1", text)              # danda + real mark
    text = re.sub(r"[ \t]+([.,;:?!।॥)])", r"\1", text)         # gap before a mark
    text = re.sub(r"\([ \t]+", "(", text)
    text = re.sub(r"([.,;:?!])(?=[^\s.,;:?!)\]\d])", r"\1 ", text)

    text = re.sub(r"[ \t]+\n", "\n", text)
    paragraphs = [re.sub(r"[ \t]+", " ", p).strip()
                  for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paragraphs if p)


def corrections() -> dict[int, list[dict]]:
    """Typing errors in the source, fixed here rather than in the source file.

    Correcting his text in place would make the fix invisible and a re-export
    of the book would silently undo it. Keeping the corrections beside the
    reasoning means every character changed can be checked and argued with.
    """
    if not FIXES.exists():
        return {}
    raw = json.loads(FIXES.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.items() if k.isdigit()}


def correct(number: int, text: str, fixes: dict[int, list[dict]]) -> tuple[str, int]:
    applied = 0
    for fix in fixes.get(number, []):
        if fix["from"] in text:
            text = text.replace(fix["from"], fix["to"])
            applied += 1
        elif fix["to"] not in text:
            print(f"  ! aphorism {number}: correction no longer matches the source "
                  f"— {fix['from'][:40]}")
    return text, applied


# The title page, which stands before the first numbered aphorism in the
# source and is not part of it.
FRONT_END = "દેવચંદ કે. શાહ"


def split_front(body: str) -> tuple[str, str]:
    """Separate the book's title page from the first aphorism's text.

    The source opens with the title, the invocation and his name, and only
    then the first numbered aphorism. Without this the title page is read as
    part of that aphorism.
    """
    at = body.find(FRONT_END)
    if at < 0 or at > 400:
        return "", body
    cut = at + len(FRONT_END)
    return body[:cut].strip(), body[cut:].strip()


def extract(source: Path) -> list[dict]:
    raw = source.read_text(encoding="utf-8")
    # The aphorism numbers are set right-aligned at the end of each one's last
    # line. Emphasis marks sometimes close after the number rather than before
    # it, and a lookahead for whitespace alone missed those — number 91 ended
    # `…નથી.    ૯૧**`, so it never split, and that aphorism was swallowed whole
    # into the next one along with the book's title page.
    pieces = re.split(r"([" + GUJ_DIGITS + r"]{1,3})(?=[*_\s]*(?:\n|$))", raw)

    existing = {}
    if BOOK.exists():
        for item in json.loads(BOOK.read_text(encoding="utf-8")):
            existing[item["n"]] = item

    fixes = corrections()
    fixed = 0
    out: list[dict] = []
    for i in range(1, len(pieces), 2):
        body = tidy(pieces[i - 1])
        number = gujarati_number(pieces[i])
        if not body:
            continue
        if not out:                      # the first aphorism in the source
            front, body = split_front(body)
            if front:
                FRONT.write_text(front + "\n", encoding="utf-8")
        body, n = correct(number, body, fixes)
        fixed += n
        prior = existing.get(number, {})
        out.append({
            "n": number,
            "gujarati": body,
            # never overwritten by a re-run
            "english": prior.get("english", ""),
            "english_by": prior.get("english_by", ""),
            "notes": prior.get("notes", ""),
            "discourses": prior.get("discourses", []),
        })
    out.sort(key=lambda x: x["n"])
    if fixed:
        print(f"  {fixed} correction(s) applied from {FIXES.name}")
    return out


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    source = Path(sys.argv[1]).expanduser()
    items = extract(source)
    BOOK.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    done = sum(1 for i in items if i["english"])
    print(f"{len(items)} aphorisms -> {BOOK}")
    print(f"  numbered {items[0]['n']}–{items[-1]['n']}, {done} with an English rendering")


if __name__ == "__main__":
    main()
