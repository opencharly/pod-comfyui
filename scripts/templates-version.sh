#!/bin/bash
set -eu
# templates-version.sh — the stable content hash of the generated template set.
#
# A sha256 over every file under the UI dir and the API dir, ordered by its
# path relative to that dir (ui/... then api/...), each contributing
# "<relative-path> NUL <bytes> NUL".  No timestamps, no file metadata: the same
# content always prints the same hash, so it can gate templates/VERSION.

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$SCRIPT_DIR")
UI_DIR="${ROOT}/templates/ui"
API_DIR="${ROOT}/templates/api"

while [ $# -gt 0 ]; do
  case "$1" in
    --ui-dir) UI_DIR="$2"; shift 2 ;;
    --api-dir) API_DIR="$2"; shift 2 ;;
    -h|--help)
      echo "usage: templates-version.sh [--ui-dir DIR] [--api-dir DIR]"
      exit 0 ;;
    *) echo "templates-version.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

python3 -I - "$UI_DIR" "$API_DIR" <<'PY'
import hashlib
import pathlib
import sys

entries = []
for label, root in (("ui", sys.argv[1]), ("api", sys.argv[2])):
    base = pathlib.Path(root)
    if not base.is_dir():
        continue
    for path in sorted(base.rglob("*.json")):
        rel = path.relative_to(base).as_posix()
        entries.append(("%s/%s" % (label, rel), path.read_bytes()))

digest = hashlib.sha256()
for name, data in sorted(entries):
    digest.update(name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(data)
    digest.update(b"\0")
print(digest.hexdigest())
PY
