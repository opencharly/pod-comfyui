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
