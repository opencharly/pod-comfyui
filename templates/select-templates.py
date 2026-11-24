#!/usr/bin/env python3
"""select-templates.py -- install only the curated templates that can actually RUN here.

Two gates, both measured rather than assumed:

  1. MODELS: every `.safetensors`/`.ckpt`/`.sft`/`.gguf` file a template's nodes name must exist
     under the pod's model root. A template whose model is still downloading (or was never in the
     manifest) would otherwise appear in the Templates dialog and fail on Queue.
  2. VRAM: every template must have a `requirements.tsv` row naming the GPU memory it needs, and
     that must be <= the card's total. The 14B Wan pair, for instance, needs ~30 GiB and can never
     run on a 16 GiB card; it stays out of the dialog instead of failing at sampling time.

A missing requirement row is a PACKAGING ERROR and aborts: silently copying an unclassified
template is how an unusable one reaches the dialog.

The destination is the pod-owned custom-node module, so the Templates dialog lists exactly what was
copied, under the module's own name. A generated README.md records the installed set and every skip
with its reason, so the pod's own workflow set is self-describing on disk as well as in the UI.
"""
import argparse
import json
import pathlib
import sys

MODEL_SUFFIXES = (".safetensors", ".sft", ".ckpt", ".gguf", ".bin")
MODEL_KEYS = ("ckpt_name", "unet_name", "clip_name", "vae_name", "lora_name", "model_name")


def referenced_models(template: dict) -> set[str]:
    """Every model file name the graph's nodes reference, by input key."""
    out = set()
    for node in template.get("nodes", []):
        widgets = node.get("widgets_values")
        if not isinstance(widgets, list):
            continue
        for value in widgets:
            if isinstance(value, str) and value.endswith(MODEL_SUFFIXES):
                out.add(pathlib.Path(value).name)
        # subgraph-bearing templates keep inputs under `inputs`
        for value in (node.get("inputs") or {}).values() if isinstance(node.get("inputs"), dict) else ():
            if isinstance(value, str) and value.endswith(MODEL_SUFFIXES):
                out.add(pathlib.Path(value).name)
    return out


def load_requirements(path: pathlib.Path) -> dict[str, int]:
    rows = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            sys.exit("requirements row is not slug<TAB>min_gib<TAB>note: %r" % raw)
        rows[parts[0]] = int(parts[1])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", required=True, help="directory of UI template JSON files")
    ap.add_argument("--requirements", required=True, help="slug<TAB>min_gib<TAB>note")
    ap.add_argument("--models", required=True, help="the pod's model root (the state volume)")
    ap.add_argument("--vram-gib", required=True, help="measured total GPU memory, GiB")
    ap.add_argument("--dest", required=True, help="the module's example_workflows directory")
    a = ap.parse_args()

    vram = int(a.vram_gib)
    req = load_requirements(pathlib.Path(a.requirements))
    models_root = pathlib.Path(a.models)
    present = {p.name for p in models_root.rglob("*") if p.is_file() and not p.name.endswith(".part")}

    dest = pathlib.Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    installed, skipped = [], []

    for path in sorted(pathlib.Path(a.templates).glob("*.json")):
        slug = path.stem
        if slug not in req:
            sys.exit("packaging error: no requirements.tsv row for template %r — classify it "
                     "(slug<TAB>min_gib<TAB>note) rather than shipping it unclassified" % slug)
        need = req[slug]
        missing = sorted(m for m in referenced_models(json.loads(path.read_text(encoding="utf-8")))
                         if m not in present)
        if need > vram:
            skipped.append((slug, "needs %d GiB, card has %d" % (need, vram)))
            continue
        if missing:
            skipped.append((slug, "models not present: " + ", ".join(missing)))
            continue
        (dest / path.name).write_bytes(path.read_bytes())
        installed.append((slug, need))

    lines = ["# charly_templates — the pod's curated workflow set", "",
             "GPU budget: %d GiB. Installed only when every model the graph names is present." % vram, "",
             "## Installed", ""]
    lines += ["- `%s` (needs %d GiB)" % (s, n) for s, n in installed] or ["- (none)"]
    lines += ["", "## Not installed, and why", ""]
    lines += ["- `%s` — %s" % (s, why) for s, why in skipped] or ["- (none skipped)"]
    (dest.parent / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("charly_templates: %d installed, %d skipped (vram %d GiB)" % (len(installed), len(skipped), vram))
    for s, why in skipped:
        print("  skipped %s: %s" % (s, why))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
