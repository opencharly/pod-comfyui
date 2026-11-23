#!/bin/bash
set -eu
# check-templates.sh — the offline structural gate for the generated template set.
#
# Checks, in order:
#   1. every UI template parses (nodes list, links list, unique node ids);
#   2. every API twin parses (node-id dict of class_type + inputs);
#   3. every API twin is byte-identical to a fresh UI -> API conversion
#      (the no-op regeneration gate);
#   4. every model-bearing string value in either form has a row in the model
#      manifest, or is the env-sourced default checkpoint;
#   5. every diffusion_models/ text_encoders/ vae/ checkpoint file in the
#      manifest is referenced by a UI template, and every loras/ file is
#      referenced or allowlisted with a non-empty reason;
#   6. templates/VERSION equals templates-version.sh output.
#
# It builds no image and needs no network.  Any finding prints a FAIL line and
# the script exits non-zero.  Defaults point at the repo's templates/ tree; every
# one can be overridden (the caller owns the curated set).

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$SCRIPT_DIR")

UI_DIR="${ROOT}/templates/ui"
API_DIR="${ROOT}/templates/api"
MANIFEST="${ROOT}/templates/manifest.tsv"
ALLOWLIST="${ROOT}/templates/allowlist.tsv"
MODEL_MANIFEST="${ROOT}/models/model-manifest.tsv"
VERSION_FILE="${ROOT}/templates/VERSION"
OBJECT_INFO="${OBJECT_INFO:-/tmp/object_info.json}"
SRC_DIR=""
MANIFEST_GIVEN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --ui-dir) UI_DIR="$2"; shift 2 ;;
    --api-dir) API_DIR="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; MANIFEST_GIVEN=1; shift 2 ;;
    --allowlist) ALLOWLIST="$2"; shift 2 ;;
    --model-manifest) MODEL_MANIFEST="$2"; shift 2 ;;
    --version-file) VERSION_FILE="$2"; shift 2 ;;
    --object-info) OBJECT_INFO="$2"; shift 2 ;;
    --src-dir) SRC_DIR="$2"; shift 2 ;;
    -h|--help)
      echo "usage: check-templates.sh [--ui-dir D] [--api-dir D] [--manifest F]"
      echo "       [--allowlist F] [--model-manifest F] [--version-file F]"
      echo "       [--object-info F] [--src-dir D]"
      exit 0 ;;
    *) echo "check-templates.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

python3 -I - \
  "$UI_DIR" "$API_DIR" "$MANIFEST" "$ALLOWLIST" "$MODEL_MANIFEST" \
  "$VERSION_FILE" "$OBJECT_INFO" "$SCRIPT_DIR/ui-to-api.py" \
  "$SCRIPT_DIR/templates-version.sh" "$SRC_DIR" "$MANIFEST_GIVEN" <<'PY'
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

(UI_DIR, API_DIR, MANIFEST, ALLOWLIST, MODEL_MANIFEST, VERSION_FILE,
 OBJECT_INFO, CONVERTER, VERSION_SCRIPT, SRC_DIR, MANIFEST_GIVEN) = sys.argv[1:12]

DOC_ONLY = {"MarkdownNote", "Note", "PreviewAny"}
DEFAULT_CHECKPOINT = "v1-5-pruned-emaonly-fp16.safetensors"
MODEL_RE = re.compile(r"\.(safetensors|ckpt|pt|pth)$")
COVERED_DIRS = {"diffusion_models", "text_encoders", "vae", "checkpoints"}
LORA_DIR = "loras"

findings = []


def fail(message):
    findings.append(message)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def is_model_ref(value):
    if not isinstance(value, str):
        return False
    if value != value.strip() or "\n" in value or "://" in value:
        return False
    return bool(MODEL_RE.search(value))


def iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)


def ui_model_refs(doc):
    refs = set()
    for node in doc.get("nodes") or []:
        if node.get("type") in DOC_ONLY:
            continue
        for value in iter_strings(node.get("widgets_values")):
            if is_model_ref(value):
                refs.add(value)
        for value in iter_strings(node.get("_inlined_inputs") or {}):
            if is_model_ref(value):
                refs.add(value)
    return refs


def api_model_refs(doc):
    refs = set()
    for node in doc.values():
        for value in iter_strings(node.get("inputs") or {}):
            if is_model_ref(value):
                refs.add(value)
    return refs


def parse_model_manifest(path):
    rows = []
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        fail("model manifest %s is unreadable: %s" % (path, exc))
        return rows
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) != 3:
            fail("model manifest row is not <subdir>/<file>|<url>|<sha256>: %r" % line)
            continue
        rel = parts[0]
        if "/" not in rel:
            fail("model manifest row has no subdirectory: %r" % rel)
            continue
        subdir, name = rel.split("/", 1)
        rows.append((subdir, name))
    return rows


def parse_allowlist(path):
    allowed = {}
    if not os.path.exists(path):
        return allowed
    for raw in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if parts[0] == "model_file":
            continue  # header
        if len(parts) != 3:
            fail("allowlist row is not model_file<TAB>reason<TAB>decision: %r" % line)
            continue
        model_file, reason, _decision = parts
        if not reason.strip():
            fail("allowlist row for %r has an empty reason" % model_file)
            continue
        allowed[model_file] = reason
    return allowed


# ---------------------------------------------------------------------------
# 1-3: parse both forms and prove the API twin is a fresh conversion.
# ---------------------------------------------------------------------------
ui_dir = pathlib.Path(UI_DIR)
api_dir = pathlib.Path(API_DIR)
ui_files = sorted(p for p in ui_dir.rglob("*.json")) if ui_dir.is_dir() else []
api_files = sorted(p for p in api_dir.rglob("*.json")) if api_dir.is_dir() else []

for path in ui_files:
    try:
        doc = load_json(path)
    except (json.JSONDecodeError, OSError) as exc:
        fail("UI template %s does not parse: %s" % (path, exc))
        continue
    if not isinstance(doc, dict) or not isinstance(doc.get("nodes"), list):
        fail("UI template %s has no nodes list" % path)
        continue
    if not isinstance(doc.get("links"), list):
        fail("UI template %s has no links list" % path)
    ids = [n.get("id") for n in doc.get("nodes") or [] if isinstance(n, dict)]
    if len(ids) != len(set(ids)):
        fail("UI template %s has duplicate node ids" % path)

for path in api_files:
    try:
        doc = load_json(path)
    except (json.JSONDecodeError, OSError) as exc:
        fail("API twin %s does not parse: %s" % (path, exc))
        continue
    if not isinstance(doc, dict):
        fail("API twin %s is not a node-id object" % path)
        continue
    for node_id, node in doc.items():
        if not isinstance(node, dict):
            fail("API twin %s node %s is not an object" % (path, node_id))
            continue
        if not isinstance(node.get("class_type"), str):
            fail("API twin %s node %s has no class_type" % (path, node_id))
        if not isinstance(node.get("inputs"), dict):
            fail("API twin %s node %s has no inputs object" % (path, node_id))

if not os.path.exists(OBJECT_INFO):
    fail("object_info capture %s is missing" % OBJECT_INFO)

pairs = []
if os.path.exists(OBJECT_INFO):
    for ui_path in ui_files:
        rel = ui_path.relative_to(ui_dir).as_posix()
        api_path = api_dir / rel
        pairs.append((ui_path, api_path))
        if not api_path.exists():
            fail("UI template %s has no API twin %s" % (ui_path, api_path))
            continue
        with tempfile.NamedTemporaryFile(suffix=".api.json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            proc = subprocess.run(
                [sys.executable, "-I", CONVERTER, str(ui_path), "--out", tmp_path,
                 "--object-info", OBJECT_INFO],
                capture_output=True, text=True)
            if proc.returncode != 0:
                fail("fresh conversion of %s failed: %s"
                     % (ui_path, proc.stderr.strip()))
                continue
            expected = pathlib.Path(tmp_path).read_bytes()
            actual = api_path.read_bytes()
            if expected != actual:
                fail("API twin %s is not a fresh conversion (regenerate it)"
                     % api_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

seen_api = {p for _u, p in pairs}
for api_path in api_files:
    if api_path not in seen_api:
        fail("API twin %s has no UI template" % api_path)

# Optional: prove the committed UI is a fresh flatten of its source template.
if MANIFEST_GIVEN == "1" and SRC_DIR:
    try:
        # A manifest source like `templates/src/<name>.ui.json` is written relative to the
        # DIRECTORY THAT CONTAINS templates/ — which is the repo root in a checkout and
        # `/opt/comfyui` in the image (the tree is copied to /opt/comfyui/templates). Derive it
        # from the MANIFEST, not from the converter's own path: the converter is copied to
        # /opt/comfyui/ui-to-api.py, whose grandparent is `/opt` — the bug this replaced.
        repo_root = pathlib.Path(os.path.dirname(os.path.dirname(
            os.path.abspath(MANIFEST))))
        rows = []
        for raw in pathlib.Path(MANIFEST).read_text(encoding="utf-8").splitlines():
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 2:
                rows.append((parts[0], parts[1], "package"))
            elif len(parts) >= 3:
                rows.append((parts[0], parts[1], parts[2] or "package"))
            else:
                fail("manifest row is not slug<TAB>source[<TAB>kind...]: %r" % line)
        for slug, source, kind in rows:
            if kind == "authored":
                if source == "-":
                    src_path = pathlib.Path(SRC_DIR) / (slug + ".json")
                    if not src_path.exists():
                        continue  # no authored source to re-flatten
                else:
                    src_path = pathlib.Path(source)
                    if not src_path.is_absolute():
                        src_path = repo_root / src_path
            else:
                src_path = pathlib.Path(SRC_DIR) / source
            ui_path = ui_dir / (slug + ".json")
            if not src_path.exists():
                fail("manifest source %s does not exist" % src_path)
                continue
            if not ui_path.exists():
                fail("flattened UI %s does not exist" % ui_path)
                continue
            with tempfile.NamedTemporaryFile(suffix=".ui.json", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", CONVERTER, "--flatten", str(src_path),
                     "--out", tmp_path],
                    capture_output=True, text=True)
                if proc.returncode != 0:
                    fail("fresh flatten of %s failed: %s"
                         % (src_path, proc.stderr.strip()))
                    continue
                if pathlib.Path(tmp_path).read_bytes() != ui_path.read_bytes():
                    fail("flattened UI %s is not a fresh flatten of %s"
                         % (ui_path, src_path))
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
    except OSError as exc:
        fail("manifest %s is unreadable: %s" % (MANIFEST, exc))

# ---------------------------------------------------------------------------
# 4-5: model references and manifest coverage.
# ---------------------------------------------------------------------------
model_rows = parse_model_manifest(MODEL_MANIFEST)
manifest_names = {name for _subdir, name in model_rows}
allowlist = parse_allowlist(ALLOWLIST)

ui_refs = set()
for path in ui_files:
    try:
        doc = load_json(path)
    except (json.JSONDecodeError, OSError):
        continue
    ui_refs.update(ui_model_refs(doc))

api_refs = set()
for path in api_files:
    try:
        doc = load_json(path)
    except (json.JSONDecodeError, OSError):
        continue
    api_refs.update(api_model_refs(doc))

for ref in sorted(ui_refs | api_refs):
    if ref == DEFAULT_CHECKPOINT:
        continue
    if ref not in manifest_names:
        fail("model file %r is referenced but has no model-manifest row" % ref)

for subdir, name in model_rows:
    if subdir in COVERED_DIRS:
        if name not in ui_refs:
            fail("manifest model %s/%s is not referenced by any UI template"
                 % (subdir, name))
    elif subdir == LORA_DIR:
        if name in ui_refs:
            continue
        if name in allowlist or ("%s/%s" % (subdir, name)) in allowlist:
            continue
        fail("manifest lora %s/%s is not referenced and not allowlisted"
             % (subdir, name))

# ---------------------------------------------------------------------------
# 6: templates/VERSION.
# ---------------------------------------------------------------------------
if not os.path.exists(VERSION_FILE):
    fail("version file %s is missing" % VERSION_FILE)
else:
    proc = subprocess.run(
        ["bash", VERSION_SCRIPT, "--ui-dir", UI_DIR, "--api-dir", API_DIR],
        capture_output=True, text=True)
    if proc.returncode != 0:
        fail("templates-version.sh failed: %s" % proc.stderr.strip())
    else:
        expected_version = proc.stdout.strip()
        actual_version = pathlib.Path(VERSION_FILE).read_text(encoding="utf-8").strip()
        if expected_version != actual_version:
            fail("templates/VERSION is %r, expected %r"
                 % (actual_version, expected_version))

# ---------------------------------------------------------------------------
# Summary.
# ---------------------------------------------------------------------------
for finding in findings:
    print("FAIL: %s" % finding, file=sys.stderr)
print("check-templates: %d UI template(s), %d API twin(s), %d finding(s)"
      % (len(ui_files), len(api_files), len(findings)))
sys.exit(1 if findings else 0)
PY
