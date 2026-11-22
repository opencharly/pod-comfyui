#!/bin/bash
set -eu
# The workflow JSON has ONE source: workflows/*.api.json. The env_provide values
# are a base64 transport copy because env_provide rejects raw JSON braces. This
# gate decodes each copy from the candy manifest and byte-compares it to the
# committed file, so the two can never drift silently.
SRC="${SRC:-/opt/comfyui/charly.yml}"
WF="${WF:-/opt/comfyui/workflows}"

[ -s "${SRC}" ] || { echo "no candy manifest at ${SRC}" >&2; exit 1; }
[ -d "${WF}" ] || { echo "no workflow directory at ${WF}" >&2; exit 1; }

check_one() {
  var="$1"
  file="$2"
  line=$(grep -m1 "^[[:space:]]*${var}: '" "${SRC}") || {
    echo "no env_provide value for ${var}" >&2
    exit 1
  }
  b64=$(printf '%s\n' "${line}" | sed -e "s/^[^']*'//" -e "s/'[[:space:]]*$//")
  [ -n "${b64}" ] || { echo "${var} is empty" >&2; exit 1; }
  tmp=$(mktemp)
  printf '%s' "${b64}" | base64 -d > "${tmp}" || {
    echo "${var} is not valid base64" >&2
    rm -f "${tmp}"
    exit 1
  }
  if ! cmp -s "${tmp}" "${WF}/${file}"; then
    echo "${var} does not match workflows/${file}: the transport copy has drifted" >&2
    rm -f "${tmp}"
    exit 1
  fi
  rm -f "${tmp}"
  echo "${var} == workflows/${file}"
}

check_one COMFYUI_WORKFLOW_B64 qwen-image-2.1-turbo-t2i.api.json
check_one IMAGES_EDIT_COMFYUI_WORKFLOW_B64 qwen-image-edit-2511-lightning.api.json

# The node mappings are a second hand-maintained projection of the SAME graph:
# every node id and key they name must exist in the corresponding workflow file.
python3 - "${SRC}" "${WF}" <<'PYEOF'
import json
import pathlib
import re
import sys

src, wfdir = sys.argv[1], sys.argv[2]
text = pathlib.Path(src).read_text()
lanes = [
    ("COMFYUI_WORKFLOW_NODES", "qwen-image-2.1-turbo-t2i.api.json"),
    ("IMAGES_EDIT_COMFYUI_WORKFLOW_NODES", "qwen-image-edit-2511-lightning.api.json"),
]
for var, file in lanes:
    match = re.search(rf"^[ \t]*{var}: '(.*)'[ \t]*$", text, re.M)
    if not match:
        sys.exit(f"no env_provide value for {var}")
    nodes = json.loads(match.group(1))
    graph = json.loads((pathlib.Path(wfdir) / file).read_text())
    for entry in nodes:
        for node_id in entry.get("node_ids", []):
            if node_id not in graph:
                sys.exit(f"{var}: node {node_id} is absent from {file}")
            key = entry.get("key")
            if key and key not in graph[node_id].get("inputs", {}):
                sys.exit(f"{var}: node {node_id} has no input {key!r} in {file}")
print("node maps match the workflows")
PYEOF
