#!/bin/bash
set -eu
# Build-time gate: the canonical manifest is well-formed, and every model file a
# shipped API workflow references has a manifest row (with a verified sha256).
# A missing companion (qwen3vl, the t2i PE, the Wan low-noise expert) fails HERE,
# before the image is built, instead of as a loader error at first prompt.
MANIFEST="${MANIFEST:-/opt/comfyui/model-manifest.tsv}"
WF_DIR="${WF_DIR:-/opt/comfyui/workflows}"

if [ ! -s "${MANIFEST}" ]; then
  echo "no model manifest at ${MANIFEST}" >&2
  exit 1
fi

python3 - "${MANIFEST}" "${WF_DIR}" <<'PY'
import json
import pathlib
import re
import sys

manifest = pathlib.Path(sys.argv[1])
wf_dir = pathlib.Path(sys.argv[2])
rows = []
for line in manifest.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    parts = line.split("|")
    if len(parts) != 3:
        sys.exit(f"manifest row is not <subdir>/<file>|<url>|<sha256>: {line}")
    rel, url, sha = parts
    if "/" not in rel:
        sys.exit(f"manifest row has no model subdirectory: {rel}")
    if not url.startswith("https://"):
        sys.exit(f"manifest row URL is not https: {line}")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        sys.exit(f"manifest row sha256 is not 64 lowercase hex: {rel}")
    rows.append(rel)

SUBDIR = {
    "unet_name": "diffusion_models",
    "clip_name": "text_encoders",
    "vae_name": "vae",
    "ckpt_name": "checkpoints",
    "lora_name": "loras",
}
missing = []
seen = []
for path in sorted(wf_dir.glob("*.json")):
    try:
        graph = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        sys.exit(f"{path.name}: invalid JSON: {exc}")
    if not isinstance(graph, dict):
        sys.exit(f"{path.name}: API workflow must be a node-id object")
    for node_id, node in graph.items():
        inputs = node.get("inputs", {})
        for key, value in inputs.items():
            if key in SUBDIR and isinstance(value, str) and value.endswith(".safetensors"):
                rel = f"{SUBDIR[key]}/{value}"
                if rel not in rows:
                    missing.append(f"{path.name}: node {node_id} {key} -> {rel}")

if missing:
    sys.exit("workflow models missing from the manifest:\n  " + "\n  ".join(missing))

print(f"manifest OK: {len(rows)} model rows; {len(list(wf_dir.glob('*.json')))} API workflow(s) complete")
PY
