# templates — the generated workflow set

Every ComfyUI workflow this pod ships is generated from one declared set, so the
served graphs cannot drift from the package templates they came from.

## The pipeline

```
pinned comfyui_workflow_templates_json
        │  (source, kind=package)
        ▼
templates/ui/<slug>.json        flattened UI graph   ── generated, committed
        │  widgets_values + /object_info
        ▼
templates/api/<slug>.json       API prompt graph     ── generated, committed
        │
        ▼
models/model-manifest.tsv + templates/allowlist.tsv  ── reference/coverage gate
```

`templates/manifest.tsv` (the caller-owned curated set) is the ONE declaration:
`slug<TAB>source<TAB>kind<TAB>package_version<TAB>notes`. A `package` source is
a file in the pinned package; an `authored` source is a repo-root-relative path
(or `-` when the row has no authored source).

## Regenerating

```bash
# one template at a time
python3 -I scripts/ui-to-api.py --flatten <src-ui.json> --out templates/ui/<slug>.json
python3 -I scripts/ui-to-api.py templates/ui/<slug>.json \
        --out templates/api/<slug>.json --object-info /tmp/object_info.json

# the whole curated set
python3 -I scripts/ui-to-api.py --all templates/manifest.tsv \
        --src-dir <pinned-package-templates-dir> \
        --ui-dir templates/ui --api-dir templates/api \
        --object-info /tmp/object_info.json
```

The converter is stdlib-only and runs under `python3 -I`. Its serialization is
fixed: the flat UI is source-ordered, `indent=2`; the API twin is
`sort_keys=True`, `indent=2`; both end in one newline. Regenerating from the
same inputs therefore reproduces the same bytes — which is what the gate proves.

After regenerating, refresh the content hash:

```bash
scripts/templates-version.sh > templates/VERSION
```

## The gate

```bash
scripts/check-templates.sh \
  --ui-dir templates/ui --api-dir templates/api \
  --manifest templates/manifest.tsv \
  --model-manifest models/model-manifest.tsv \
  --allowlist templates/allowlist.tsv \
  --version-file templates/VERSION \
  --object-info /tmp/object_info.json
```

It is offline (no image, no network) and checks:

1. every UI template parses (`nodes`, `links`, unique node ids);
2. every API twin parses (node-id object of `class_type` + `inputs`);
3. every API twin is byte-identical to a fresh UI → API conversion;
4. every model-bearing string value has a `models/model-manifest.tsv` row, or is
   the env-sourced default checkpoint `v1-5-pruned-emaonly-fp16.safetensors`;
5. every `diffusion_models/`, `text_encoders/`, `vae/` and `checkpoints/` file
   in the model manifest is referenced by a UI template, and every `loras/`
   file is referenced or listed in `templates/allowlist.tsv` with a non-empty
   reason;
6. `templates/VERSION` equals `scripts/templates-version.sh` output.

Any finding prints `FAIL: …` and the script exits non-zero. With `--src-dir`
and `--manifest`, it additionally proves each committed UI is a fresh flatten of
its source.

`templates/allowlist.tsv` is `model_file<TAB>reason<TAB>decision`: the escape
hatch for a LoRA that is installed but not wired into the curated set. It stays
header-only until such a row exists.

## The converter's two stages

* **Flatten.** A package template keeps its real graph in
  `definitions.subgraphs[0]`, with a UUID-typed instance node in the top-level
  `nodes`. The converter splices the definition's nodes/links in with offset
  ids, rewires the definition's output links to the instance's consumers, and
  maps promoted inputs: a top-level-linked input rewires the internal link to
  the top-level origin; an unlinked input's stored value is recorded on the
  internal target as `_inlined_inputs` (mirroring the frontend's widget inlining).
  Top-level nodes are preserved; documentation nodes stay in the UI.
* **Map.** `widgets_values` become named `inputs` in the class's declared order
  from the live `/object_info`. Combo, INT/FLOAT/STRING/BOOLEAN and
  `COMFY_DYNAMICCOMBO_V3` slots are widgets (dynamic-combo children are dotted,
  e.g. `format.bit_depth`); everything else is link-only. Linked inputs become
  `[origin_node_id_str, origin_slot]`. The frontend's synthetic
  `control_after_generate` and upload widgets are skipped. A widget-count
  mismatch fails loudly with the node id, class, expected and actual counts.
  `MarkdownNote`, `Note` and `PreviewAny` are dropped from the API twin only
  (a PreviewAny consumer is rewired to PreviewAny's own upstream source).
