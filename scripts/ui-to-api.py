#!/usr/bin/env python3
"""ui-to-api.py — the canonical ComfyUI UI-format -> API-format converter.

THE single converter between the two ComfyUI workflow shapes:

  UI format   -- the JSON the ComfyUI frontend saves (``nodes``, ``links``,
                 ``widgets_values``, ``definitions.subgraphs``).
  API format  -- ``{node-id: {"class_type": ..., "inputs": {...}}}``, the shape
                 ComfyUI's ``/prompt`` accepts.

It runs in two stages, each a separate invocation:

  1. FLATTEN  ``ui-to-api.py --flatten <src-ui.json> --out <flat-ui.json>``
     Splices a referenced subgraph definition into the top-level graph:
       * the definition's nodes/links are re-added with offset ids (so no id
         collides with the top graph);
       * a definition OUTPUT link is rewired to the consumers of the subgraph
         instance node it fed;
       * a promoted subgraph INPUT whose instance slot is LINKED at the top
         level has each internal link's origin replaced by the top-level
         link's origin;
       * a promoted subgraph INPUT whose instance slot is UNLINKED carries its
         value in the instance node's ``widgets_values`` (when the saved
         workflow stores it there).  That value is recorded on the internal
         target node as ``_inlined_inputs[<input-name>]`` and the internal
         placeholder link is removed — mirroring the frontend's ``widgetInfo``
         inlining.  When the saved workflow stores the value on the internal
         node itself (the other serialization the frontend emits), nothing is
         inlined and the node's own widget value stands.
     Top-level non-subgraph nodes are preserved untouched.  Documentation-only
     nodes are kept in the flat UI (they are dropped only from the API twin).

  2. MAP      ``ui-to-api.py <flat-ui.json> --out <api.json>``
     Maps ``widgets_values`` to named ``inputs`` using the live
     ``/object_info`` capture, in the class's declared order
     (``input.required`` then ``input.optional``, JSON order).  A slot is a
     widget when its declared type is a list (combo), ``COMBO``, one of
     INT/FLOAT/STRING/BOOLEAN, or a ``COMFY_DYNAMICCOMBO_V3``; every other
     type is link-only.  Linked inputs become ``[origin_node_id_str,
     origin_slot]``.  Documentation-only classes (MarkdownNote, Note,
     PreviewAny) are dropped from the API twin, and so are links whose origin
     is a dropped node.  PreviewAny echoes its single ``source`` input, so a
     consumer of a dropped PreviewAny is rewired to PreviewAny's own upstream
     origin (the same effect as bypassing it); a dropped node with no such
     pass-through simply drops the link.

  Both stages are also available in bulk:

     ``ui-to-api.py --all <manifest.tsv> --src-dir <dir> --ui-dir <dir>
                    --api-dir <dir> --object-info <path>``

  The manifest is the caller-owned ``templates/manifest.tsv``:
  ``slug<TAB>source<TAB>kind<TAB>package_version<TAB>notes`` (a two-column
  ``slug<TAB>source`` scratch form is also accepted).  A ``package`` row's
  source is resolved under ``--src-dir``; an ``authored`` row's source is a
  repo-root-relative path (``-`` means no authored source, so the row is
  skipped).  Each row writes ``<ui-dir>/<slug>.json`` and
  ``<api-dir>/<slug>.json``.

Frontend behaviours this converter mirrors (verified against the pinned
frontend's ``src/utils/executionUtil.ts`` and ``src/core/graph/widgets``):

  * a synthetic ``control_after_generate`` widget follows a widget whose spec
    carries a truthy ``control_after_generate`` option (seed / noise_seed and
    the PrimitiveInt ``value``).  It is present in ``widgets_values`` and
    absent from the prompt, so it is skipped when mapping;
  * a synthetic upload widget follows a combo whose spec carries
    ``image_upload: true`` (LoadImage).  It is skipped likewise;
  * dynamic-combo nested widgets are named with a dotted prefix
    (``format.bit_depth``, ``sampling_mode.seed``), as the frontend's
    ``prefix = `${selector}.${key}`` rule produces;
  * ``forceInput`` and ``hidden`` inputs are not widgets and carry no value in
    ``widgets_values``.

SERIALIZATION (fixed, deterministic): the flat UI is ``json.dumps(indent=2,
ensure_ascii=False)`` in source key order plus one trailing newline; the API
twin is ``json.dumps(indent=2, sort_keys=True, ensure_ascii=False)`` plus one
trailing newline.  Stdlib only; runs under ``python3 -I``.
"""

import argparse
import json
import os
import sys

DOC_ONLY_CLASSES = {"MarkdownNote", "Note", "PreviewAny"}
SCALAR_WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN"}
WIDGET_TYPE_NAMES = SCALAR_WIDGET_TYPES | {"COMBO", "COMFY_DYNAMICCOMBO_V3"}
API_LINK_EXEMPT_CLASSES = DOC_ONLY_CLASSES


class ConversionError(Exception):
    pass


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_ui(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def dump_api(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_text(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# ---------------------------------------------------------------------------
# Link shapes.  The top-level frontend format is a list
# [id, origin_id, origin_slot, target_id, target_slot, type]; subgraph
# definitions in the pinned templates use the dict form.  Normalize to lists.
# ---------------------------------------------------------------------------

def link_as_list(link):
    if isinstance(link, list):
        if len(link) < 5:
            raise ConversionError(f"link list too short: {link!r}")
        return [link[0], link[1], link[2], link[3], link[4],
                link[5] if len(link) > 5 else None]
    return [link["id"], link["origin_id"], link["origin_slot"],
            link["target_id"], link["target_slot"], link.get("type")]


def link_id(link):
    return link[0] if isinstance(link, list) else link["id"]


def link_origin(link):
    if isinstance(link, list):
        return link[1], link[2]
    return link["origin_id"], link["origin_slot"]


def link_target(link):
    if isinstance(link, list):
        return link[3], link[4]
    return link["target_id"], link["target_slot"]


# ---------------------------------------------------------------------------
# Stage 1: flatten subgraphs.
# ---------------------------------------------------------------------------

def flatten_document(src):
    """Return a flat-UI document (subgraph definitions spliced in)."""
    doc = json.loads(json.dumps(src))  # deep copy, preserves order

    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        raise ConversionError("UI document has no 'nodes' list")
    if not isinstance(doc.get("links"), list):
        raise ConversionError("UI document has no 'links' list")

    definitions = doc.get("definitions") or {}
    subgraphs = definitions.get("subgraphs") or []
    by_id = {sg["id"]: sg for sg in subgraphs}

    instance_nodes = [n for n in nodes if n.get("type") in by_id]
    if not subgraphs and not instance_nodes:
        # Already flat: normalize link representation only.
        doc["links"] = [link_as_list(l) for l in doc["links"]]
        for node in doc["nodes"]:
            for slot in node.get("inputs") or []:
                if isinstance(slot, dict):
                    slot.pop("localized_name", None)
            for slot in node.get("outputs") or []:
                if isinstance(slot, dict):
                    slot.pop("localized_name", None)
        return doc

    top_links = [link_as_list(l) for l in doc["links"]]
    top_link_by_id = {l[0]: l for l in top_links}

    all_node_ids = [n["id"] for n in nodes]
    all_link_ids = [l[0] for l in top_links]
    for sg in subgraphs:
        all_node_ids += [n["id"] for n in sg.get("nodes") or []]
        all_link_ids += [link_id(l) for l in sg.get("links") or []]
    node_offset = max(all_node_ids) + 1 if all_node_ids else 1
    link_offset = max(all_link_ids) + 1 if all_link_ids else 1

    dropped_instance_ids = set()
    kept_top_links = []
    new_nodes = []
    new_links = []

    for instance in instance_nodes:
        sg = by_id[instance["type"]]
        dropped_instance_ids.add(instance["id"])
        instance_widget_values = instance.get("widgets_values") or []
        sg_nodes = sg.get("nodes") or []
        sg_links = [link_as_list(l) for l in sg.get("links") or []]

        # Deep-copy subgraph nodes with offset ids and rewritten link refs.
        sg_nodes_new = []
        for node in sg_nodes:
            node = json.loads(json.dumps(node))
            node["id"] = node["id"] + node_offset
            for slot in node.get("inputs") or []:
                slot.pop("localized_name", None)
                if slot.get("link") is not None:
                    slot["link"] = slot["link"] + link_offset
            for slot in node.get("outputs") or []:
                slot.pop("localized_name", None)
                if slot.get("links"):
                    slot["links"] = [x + link_offset for x in slot["links"]]
            sg_nodes_new.append(node)
        nodes_by_new_id = {n["id"]: n for n in sg_nodes_new}

        # Offset every subgraph link; real endpoints get the node offset,
        # the -10 / -20 IO pseudo-nodes stay pseudo until resolved.
        sg_links_new = []
        for link in sg_links:
            oid, oslot = link[1], link[2]
            tid, tslot = link[3], link[4]
            sg_links_new.append([
                link[0] + link_offset,
                oid + node_offset if oid >= 0 else oid,
                oslot,
                tid + node_offset if tid >= 0 else tid,
                tslot,
                link[5],
            ])
        sg_link_by_new_id = {l[0]: l for l in sg_links_new}

        # Instance input slot lookup by promoted name.
        instance_slot_by_name = {}
        for idx, slot in enumerate(instance.get("inputs") or []):
            instance_slot_by_name[slot.get("name")] = idx

        # Promoted widget values, when the workflow stores them on the
        # instance node, are ordered by the promoted widget inputs.
        promoted_widget_inputs = [si for si in sg.get("inputs") or []
                                  if is_widget_type_name(si.get("type"))]
        widget_overrides = {}
        if instance_widget_values:
            if len(instance_widget_values) != len(promoted_widget_inputs):
                raise ConversionError(
                    "subgraph instance %s: %d widgets_values but %d promoted "
                    "widget inputs — cannot map promoted values"
                    % (instance.get("id"), len(instance_widget_values),
                       len(promoted_widget_inputs)))
            for si, value in zip(promoted_widget_inputs, instance_widget_values):
                widget_overrides[si["name"]] = value

        consumed_link_ids = set()

        # Promoted OUTPUTS: rewire the instance node's top-level consumers to
        # the definition's internal origin.
        for out_idx, sout in enumerate(sg.get("outputs") or []):
            internal_ids = sout.get("linkIds") or []
            internal = None
            for lid in internal_ids:
                cand = sg_link_by_new_id.get(lid + link_offset)
                if cand is not None and cand[3] == -20:
                    internal = cand
                    break
            if internal is None:
                for cand in sg_links_new:
                    if cand[3] == -20 and cand[4] == out_idx:
                        internal = cand
                        break
            if internal is None:
                raise ConversionError(
                    "subgraph %s output %r has no internal origin link"
                    % (sg.get("id"), sout.get("name")))
            origin_id, origin_slot = internal[1], internal[2]
            consumed_link_ids.add(internal[0])
            for top in top_links:
                if top[1] == instance["id"] and top[2] == out_idx:
                    top[1] = origin_id
                    top[2] = origin_slot

        # Promoted INPUTS.
        for in_idx, sin in enumerate(sg.get("inputs") or []):
            name = sin.get("name")
            instance_slot = instance_slot_by_name.get(name)
            top_link_id = None
            if instance_slot is not None:
                top_link_id = (instance.get("inputs") or [])[instance_slot].get("link")
            internal_ids = sin.get("linkIds") or []
            target_links = []
            for lid in internal_ids:
                cand = sg_link_by_new_id.get(lid + link_offset)
                if cand is not None:
                    target_links.append(cand)
            if not target_links:
                target_links = [l for l in sg_links_new
                                if l[1] == -10 and l[2] == in_idx]

            if top_link_id is not None:
                top = top_link_by_id.get(top_link_id)
                if top is None:
                    raise ConversionError(
                        "instance input %r references missing top link %r"
                        % (name, top_link_id))
                for internal in target_links:
                    internal[1] = top[1]
                    internal[2] = top[2]
                continue

            override_present = name in widget_overrides
            override_value = widget_overrides.get(name)
            for internal in target_links:
                consumed_link_ids.add(internal[0])
                if not override_present:
                    # The value already lives on the internal target (or the
                    # promoted input is link-only and unconnected): drop the
                    # placeholder link, keep whatever the target already has.
                    continue
                target_node = nodes_by_new_id.get(internal[3])
                if target_node is None:
                    raise ConversionError(
                        "promoted input %r targets unknown node %r"
                        % (name, internal[3]))
                target_slots = target_node.get("inputs") or []
                tslot = internal[4]
                if tslot >= len(target_slots):
                    raise ConversionError(
                        "promoted input %r targets slot %d of %s (only %d)"
                        % (name, tslot, target_node.get("type"), len(target_slots)))
                target_name = target_slots[tslot].get("name")
                inlined = target_node.setdefault("_inlined_inputs", {})
                inlined[target_name] = override_value

        for link in sg_links_new:
            if link[0] in consumed_link_ids:
                continue
            if link[1] == -10 or link[3] == -20:
                continue
            new_links.append(link)

        new_nodes.extend(sg_nodes_new)

    # Keep top-level links that neither fed a removed instance node nor were
    # consumed above.
    for link in top_links:
        if link[3] in dropped_instance_ids:
            continue
        kept_top_links.append(link)

    doc["nodes"] = [n for n in nodes if n["id"] not in dropped_instance_ids] + new_nodes
    doc["links"] = kept_top_links + new_links

    # Drop references to links that were consumed or removed while splicing,
    # so every surviving slot points at a link that still exists.
    final_link_ids = {l[0] for l in doc["links"]}
    for node in doc["nodes"]:
        for slot in node.get("inputs") or []:
            if slot.get("link") is not None and slot["link"] not in final_link_ids:
                slot["link"] = None
        for slot in node.get("outputs") or []:
            if slot.get("links"):
                slot["links"] = [x for x in slot["links"] if x in final_link_ids]

    # The flat graph owns no subgraph definitions.
    if "definitions" in doc:
        doc["definitions"] = {}

    used_node_ids = [n["id"] for n in doc["nodes"]]
    used_link_ids = [l[0] for l in doc["links"]]
    if "last_node_id" in doc:
        doc["last_node_id"] = max(used_node_ids) if used_node_ids else 0
    if "last_link_id" in doc:
        doc["last_link_id"] = max(used_link_ids) if used_link_ids else 0
    return doc


# ---------------------------------------------------------------------------
# object_info widget model.
# ---------------------------------------------------------------------------

def is_widget_type_name(type_name):
    """True when a declared type renders a value widget."""
    if isinstance(type_name, list):
        return True
    return type_name in WIDGET_TYPE_NAMES


def spec_options(spec):
    if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1]
    return {}


def is_widget_spec(spec):
    type_name = spec[0] if isinstance(spec, list) and spec else None
    opts = spec_options(spec)
    if opts.get("forceInput") or opts.get("hidden"):
        return False
    return is_widget_type_name(type_name)


def declared_inputs(info):
    """Yield (name, spec, required) in declared order: required then optional."""
    inputs = info.get("input") or {}
    for group, required in (("required", True), ("optional", False)):
        for name, spec in (inputs.get(group) or {}).items():
            yield name, spec, required


def dynamic_option(spec, key):
    for option in spec_options(spec).get("options") or []:
        if option.get("key") == key:
            return option
    return None


class Slot:
    __slots__ = ("name", "required", "kind", "opts", "value")

    def __init__(self, name, required, kind, opts=None, value=None):
        self.name = name
        self.required = required
        self.kind = kind
        self.opts = opts or {}
        self.value = value


def plan_widget_slots(cls, info, values):
    """Build the ordered slot list for ``cls`` against ``values``.

    Dynamic-combo keys are read from ``values`` so the selected option's
    nested inputs are expanded.  Optional slots are planned but their
    presence is decided by ``match_widget_slots``.
    """
    slots = []
    cursor = [0]

    def walk(inputs, required, prefix, consume):
        for name, spec in (inputs or {}).items():
            if not is_widget_spec(spec):
                continue
            full = prefix + "." + name if prefix else name
            type_name = spec[0]
            opts = spec_options(spec)
            if type_name == "COMFY_DYNAMICCOMBO_V3":
                if consume:
                    if cursor[0] >= len(values):
                        raise ConversionError(
                            "%s (class %s): widgets_values ended before dynamic "
                            "combo %r (expected key)" % (prefix or "<root>", cls, full))
                    key = values[cursor[0]]
                    cursor[0] += 1
                else:
                    if cursor[0] >= len(values):
                        continue  # optional dynamic combo with no value
                    key = values[cursor[0]]
                slots.append(Slot(full, required, "widget", opts, key))
                option = dynamic_option(spec, key)
                if option is None:
                    raise ConversionError(
                        "class %s: dynamic combo %r has no option %r"
                        % (cls, full, key))
                nested = option.get("inputs") or {}
                walk(nested.get("required") or {}, required, full, consume)
                # Nested optional values may be absent from widgets_values.
                walk(nested.get("optional") or {}, False, full, False)
                continue
            slots.append(Slot(full, required, "widget", opts))
            if consume:
                cursor[0] += 1
            if opts.get("control_after_generate"):
                slots.append(Slot(full, required, "synthetic"))
                if consume:
                    cursor[0] += 1
            if opts.get("image_upload"):
                slots.append(Slot(full, required, "synthetic"))
                if consume:
                    cursor[0] += 1

    required_inputs = (info.get("input") or {}).get("required") or {}
    optional_inputs = (info.get("input") or {}).get("optional") or {}
    walk(required_inputs, True, "", True)
    walk(optional_inputs, False, "", False)
    return slots


def match_widget_slots(cls, node_id, info, values):
    """Return ordered [(name, value)] and the value index of each name."""
    slots = plan_widget_slots(cls, info, values)
    total = len(values)
    required_after = [0] * (len(slots) + 1)
    for i in range(len(slots) - 1, -1, -1):
        required_after[i] = required_after[i + 1] + (1 if slots[i].required else 0)

    pairs = []
    indices = {}
    cursor = 0
    for i, slot in enumerate(slots):
        if slot.kind == "synthetic":
            if cursor >= total:
                raise widget_mismatch(cls, node_id, slots, total)
            cursor += 1
            continue
        if slot.required:
            if cursor >= total:
                # VERSION SKEW: the template predates this widget (measured: MoGeInference in
                # workflow_templates 0.11.112 stores 5 values while ComfyUI 0.46.1 declares 6).
                # The frontend renders a missing widget at its declared DEFAULT, so the API twin
                # carries that default. No default means the value is genuinely unknowable.
                if "default" not in slot.opts:
                    raise widget_mismatch(cls, node_id, slots, total)
                pairs.append((slot.name, slot.opts["default"]))
                continue
            indices[slot.name] = cursor
            pairs.append((slot.name, values[cursor]))
            cursor += 1
        else:
            if cursor < total and (total - cursor) > required_after[i + 1]:
                indices[slot.name] = cursor
                pairs.append((slot.name, values[cursor]))
                cursor += 1
    if cursor > total:
        raise widget_mismatch(cls, node_id, slots, total)
    if cursor < total:
        # VERSION SKEW, the other direction: the template stores MORE values than this ComfyUI's
        # node declares (measured: SaveVideo in workflow_templates 0.11.112 stores 4 values while
        # ComfyUI 0.46.1 declares filename_prefix, format, format.codec = 3). The frontend assigns
        # widgets positionally and ignores values past the last widget, so the API twin drops the
        # trailing extras — but says so, because it is the only silent-ish case here.
        import sys
        sys.stderr.write(
            "ui-to-api: note: node %s class %s: ignoring %d trailing widgets_values entr%s "
            "(template newer than this ComfyUI)\n"
            % (node_id, cls, total - cursor, "y" if total - cursor == 1 else "ies"))
    return pairs, indices


def widget_mismatch(cls, node_id, slots, actual):
    names = [s.name for s in slots if s.kind != "synthetic"]
    return ConversionError(
        "node %s class %s: widgets_values has %d value(s), expected %d "
        "(declared widget order: %s)"
        % (node_id, cls, actual, len(slots), ", ".join(names) or "<none>"))


# ---------------------------------------------------------------------------
# Stage 2: map a flat UI document to the API prompt.
# ---------------------------------------------------------------------------

def collect_links(doc):
    out = {}
    for link in doc.get("links") or []:
        as_list = link_as_list(link)
        out[as_list[0]] = as_list
    return out


def convert_document(doc, object_info):
    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        raise ConversionError("flat UI document has no 'nodes' list")
    links = collect_links(doc)
    nodes_by_id = {n["id"]: n for n in nodes}
    dropped = {n["id"] for n in nodes if n.get("type") in API_LINK_EXEMPT_CLASSES}

    def effective_origin(link, seen=None):
        """Resolve a link's origin through dropped pass-through nodes.

        PreviewAny (a documentation-only class we drop from the API twin) has
        one ``source`` input and echoes it.  Its consumers therefore resolve to
        PreviewAny's own upstream origin, exactly as if the node were bypassed.
        """
        seen = set() if seen is None else seen
        origin_id, origin_slot = link[1], link[2]
        while origin_id in dropped:
            if origin_id in seen:
                return None
            seen.add(origin_id)
            dropped_node = nodes_by_id.get(origin_id) or {}
            upstream = [s.get("link") for s in dropped_node.get("inputs") or []
                        if s.get("link") is not None]
            if len(upstream) != 1:
                return None
            link = links.get(upstream[0])
            if link is None:
                return None
            origin_id, origin_slot = link[1], link[2]
        if origin_id < 0:
            return None
        return origin_id, origin_slot

    api = {}
    for node in nodes:
        cls = node.get("type")
        node_id = node.get("id")
        if cls in API_LINK_EXEMPT_CLASSES:
            continue
        if cls not in object_info:
            raise ConversionError(
                "node %s: class %r is not in /object_info" % (node_id, cls))
        info = object_info[cls]

        widget_values = node.get("widgets_values")
        if widget_values is None:
            widget_values = []
        if not isinstance(widget_values, list):
            raise ConversionError(
                "node %s class %s: widgets_values is not a list" % (node_id, cls))
        pairs, _indices = match_widget_slots(cls, node_id, info, widget_values)

        inputs = {}
        for name, value in pairs:
            inputs[name] = value
        for name, value in (node.get("_inlined_inputs") or {}).items():
            inputs[name] = value

        for slot in node.get("inputs") or []:
            link_id_value = slot.get("link")
            if link_id_value is None:
                continue
            link = links.get(link_id_value)
            if link is None:
                raise ConversionError(
                    "node %s class %s: input %r references missing link %r"
                    % (node_id, cls, slot.get("name"), link_id_value))
            resolved = effective_origin(link)
            if resolved is None:
                # Link from a dropped documentation node with no pass-through.
                continue
            origin_id, origin_slot = resolved
            inputs[slot.get("name")] = [str(origin_id), origin_slot]

        api[str(node_id)] = {"class_type": cls, "inputs": inputs}
    return api


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def parse_manifest(path):
    """Parse templates/manifest.tsv.

    Accepts the curated five-column form owned by the caller
    (``slug<TAB>source<TAB>kind<TAB>package_version<TAB>notes``) and, for a
    scratch set, the two-column ``<name><TAB><source>`` form.
    """
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 2:
                rows.append({"slug": parts[0], "source": parts[1],
                             "kind": "package"})
            elif len(parts) >= 3:
                rows.append({"slug": parts[0], "source": parts[1],
                             "kind": parts[2] or "package"})
            else:
                raise ConversionError(
                    "manifest row is not slug<TAB>source[<TAB>kind...]: %r"
                    % line)
    return rows


def resolve_source(row, src_dir, repo_root):
    """Return the source UI path for a manifest row, or None to skip it."""
    source = row["source"]
    if row["kind"] == "authored":
        if source == "-":
            candidate = os.path.join(src_dir, row["slug"] + ".json")
            return candidate if os.path.exists(candidate) else None
        return source if os.path.isabs(source) else os.path.join(repo_root, source)
    return os.path.join(src_dir, source)



def main(argv):
    parser = argparse.ArgumentParser(
        description="Convert ComfyUI UI-format workflows to API format.")
    parser.add_argument("input", nargs="?", help="flat UI JSON to convert to API")
    parser.add_argument("--flatten", metavar="SRC",
                        help="stage 1: flatten a UI-format source")
    parser.add_argument("--out", metavar="PATH", help="output path")
    parser.add_argument("--all", metavar="MANIFEST",
                        help="process every <name>\\t<source> row in MANIFEST")
    parser.add_argument("--src-dir", metavar="DIR", default="templates/src")
    parser.add_argument("--ui-dir", metavar="DIR", default="templates/ui")
    parser.add_argument("--api-dir", metavar="DIR", default="templates/api")
    parser.add_argument("--object-info", metavar="PATH",
                        default=os.environ.get("CHARLY_OBJECT_INFO",
                                               "/tmp/object_info.json"))
    args = parser.parse_args(argv)

    try:
        if args.all:
            object_info = load_json(args.object_info)
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            rows = parse_manifest(args.all)
            converted = 0
            for row in rows:
                src_path = resolve_source(row, args.src_dir, repo_root)
                if src_path is None:
                    print("skip %s: no source UI for an authored '-' row"
                          % row["slug"])
                    continue
                if not os.path.exists(src_path):
                    raise ConversionError(
                        "manifest source %s does not exist" % src_path)
                slug = row["slug"]
                ui_path = os.path.join(args.ui_dir, slug + ".json")
                api_path = os.path.join(args.api_dir, slug + ".json")
                flat = flatten_document(load_json(src_path))
                write_text(ui_path, dump_ui(flat))
                api = convert_document(flat, object_info)
                write_text(api_path, dump_api(api))
                print("%s -> %s (%d nodes)" % (src_path, api_path, len(api)))
                converted += 1
            print("converted %d template(s)" % converted)
            return 0

        if args.flatten:
            if not args.out:
                parser.error("--flatten requires --out")
            flat = flatten_document(load_json(args.flatten))
            write_text(args.out, dump_ui(flat))
            return 0

        if not args.input or not args.out:
            parser.error("give <flat-ui.json> --out <api.json>, or --flatten/--all")
        object_info = load_json(args.object_info)
        api = convert_document(load_json(args.input), object_info)
        write_text(args.out, dump_api(api))
        return 0

    except ConversionError as exc:
        print("ui-to-api: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
