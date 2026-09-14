#!/usr/bin/env bash
#
# Read the archive and correct it in place.
#
#   ./edit.sh
#
# Opens the site at http://127.0.0.1:8799. On any discourse page, press
# Shift+E to open the correction form. What you save goes to
# avlokan/sitting_corrections.json, the site rebuilds, and the page reloads
# showing the corrected record.
#
# Nothing is published from here. When you are done, ./publish.sh takes the
# corrections to the live site along with everything else.

set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "python3 is not on the PATH." >&2; exit 1; }

# Build first, so the form on each page carries that page's current values.
python3 -m avlokan_site.build >/dev/null
exec python3 -m avlokan_site.editor "$@"
