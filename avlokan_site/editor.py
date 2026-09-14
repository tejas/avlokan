"""Serve the archive locally and accept corrections from the page itself.

    python3 -m avlokan_site.editor        # http://127.0.0.1:8799

The site is static and stays static. This adds exactly one thing the
published copy does not have: an endpoint that answers the correction form
built into every discourse page. Press Shift+E on a page that is wrong, say
what is wrong and why, and the correction is written to
`avlokan/sitting_corrections.json`, the site is rebuilt, and the page reloads
showing the corrected record.

Why a correction file and not an edit to the data: `merge_youtube` rebuilds
`master_index.json` from the channel every week and would quietly undo
anything typed into it. A correction stated separately survives that, carries
its reasoning, and reads as a disagreement with the catalogue rather than a
replacement of it — which is what it is.

Bound to the loopback address. Nothing here is meant to face the internet.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import date
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import build as builder

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
FILE = ROOT / "avlokan" / "sitting_corrections.json"

# What the form is allowed to set. Anything else in the request is ignored
# rather than trusted: this writes to a file that is the archive's memory.
ALLOWED = ("scripture", "reference", "part_no")


def load() -> dict:
    if not FILE.exists():
        return {}
    return json.loads(FILE.read_text(encoding="utf-8"))


def save(fixes: dict) -> None:
    FILE.write_text(json.dumps(fixes, indent=1, ensure_ascii=False, sort_keys=True)
                    + "\n", encoding="utf-8")


def record(payload: dict) -> tuple[bool, str]:
    """Fold one correction into the file. Returns (rebuild needed, message)."""
    key = str(payload.get("key") or "").strip()
    why = str(payload.get("why") or "").strip()
    if not key:
        return False, "That page did not say which sitting it is."
    if not why:
        return False, "Say why, so a later reader can disagree with it."

    fix = {k: payload[k] for k in ALLOWED if k in payload}
    if not fix:
        return False, "Nothing was different."
    fix["why"] = why
    fix["when"] = date.today().isoformat()
    # The address the sitting had before this correction, so the build can
    # leave a redirect behind if the correction moves it.
    if payload.get("was"):
        fix["was"] = payload["was"]

    fixes = load()
    # A second correction to the same sitting replaces the first rather than
    # accumulating beside it. The file says what is true now; git says what it
    # said before, and why.
    fixes[key] = fix
    save(fixes)
    return True, "Saved. Rebuilding…"


class Editor(SimpleHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802  (http.server's spelling)
        if self.path.rstrip("/") != "/correction":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(size) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.answer(400, {"message": "Could not read that."})
            return

        ok, message = record(payload)
        if not ok:
            self.answer(200, {"message": message, "reload": False})
            return
        try:
            builder.build(SITE, builder.OUTPUTS_DEFAULT)
        except Exception as err:  # the page should say so, not fail silently
            self.answer(200, {"message": f"Saved, but the rebuild failed: {err}",
                              "reload": False})
            return
        self.answer(200, {"message": message, "reload": True})

    def answer(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def end_headers(self) -> None:
        # A correction is only worth making if the next page load shows it.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # One line per correction is useful; a line per image is not.
        if self.command == "POST":
            super().log_message(fmt, *args)


def main() -> None:
    ap = argparse.ArgumentParser(prog="avlokan-editor")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    if not SITE.exists():
        raise SystemExit("site/ is not built yet — run python3 -m avlokan_site.build")

    handler = partial(Editor, directory=str(SITE))
    address = f"http://127.0.0.1:{args.port}/"
    print(f"Archive at {address}")
    print("On any discourse page, press Shift+E to correct what it says.")
    print(f"Corrections are written to {FILE.relative_to(ROOT)} — commit them.")
    print("Ctrl-C to stop.")
    if not args.no_open:
        webbrowser.open(address)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
