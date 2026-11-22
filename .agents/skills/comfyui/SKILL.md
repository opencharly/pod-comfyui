---
name: comfyui
description: 'ComfyUI image generation server with CUDA GPU support.

  Runs as a supervisord service on port 8188 with persistent storage.

  MUST be invoked before building, deploying, configuring, or troubleshooting the
  comfyui box.'
---

# comfyui

GPU-accelerated ComfyUI image generation server with node-based workflow UI.

## Box Properties

| Property | Value |
|----------|-------|
| Base | `podcdi.pod-cdi` (pod) / `vm-nvidia` (VM) |
| Candies | agent-forwarding, comfyui, dbus, charly |
| Platforms | linux/amd64 |
| Ports | 8188 |
| Registry | ghcr.io/opencharly |

## Full Candy Stack

1. the **box** chooses the GPU substrate: `cachyos`/`fedora` → `nvidia-cdi`/`pod-cdi`
   (`layer-cdi`, injected host driver) **or** `vm-nvidia` (`layer-nvidia` + `layer-cuda`,
   baked driver)
2. `pixi` → `python` → `supervisord` (the candy's only `require:`)
3. `comfyui` — ComfyUI server, models/output volume

The candy declares NO CUDA dependency: it needs the supervisord service, clones ComfyUI
(Python), and takes its CUDA runtime from the `cu130` PyTorch wheels and its driver from
whatever substrate the box picked.

## Ports

| Port | Service | Protocol |
|------|---------|----------|
| 8188 | ComfyUI web UI | HTTP |

## Quick Start

```bash
charly box build comfyui
charly config comfyui
charly start comfyui
# Open http://localhost:8188
```

## Key Candies

- `/charly-comfyui:comfyui` — ComfyUI installation, supervisord service, volume
- `/charly-distros:cdi` — the POD GPU contract (host-injected driver, no baked user-space)
- `/charly-distros:nvidia` / `/charly-distros:cuda` — the VM driver + toolkit layers, composed
  by the box (`vm-nvidia`), never by this candy
- `/charly-infrastructure:dbus-layer` — session bus for desktop notifications
- `/charly-tools:charly` — in-container `charly` CLI toolchain (scripting, service management)
- `/charly-distros:agent-forwarding` — SSH/GPG/direnv agent forwarding

## Related Boxes

- `podcdi.pod-cdi` — the pod GPU base (`layer-cdi`); its sibling `vm-nvidia` is the VM base
- **CachyOS/Fedora variants** — `comfyui-cdi` in the `opencharly/distro-cachyos` and
  `opencharly/distro-fedora` submodules. See `/charly-distros:cachyos`.

## Verification

After `charly start`:
- `charly status comfyui` — container running
- `charly service status comfyui` — all services RUNNING
- `curl -s -o /dev/null -w '%{http_code}' http://localhost:8188` — ComfyUI HTTP returns 200

## Dynamic VRAM (the 0.46.1 default)

ComfyUI 0.46.1 runs **Dynamic VRAM** (`comfy_aimdo`) by default on Nvidia with torch >= 2.8.
It faults model weights in per layer and evicts under pressure, so a model larger than the
card (Qwen-Image-Edit-2511 int8 is ~19 GiB on a 16 GiB RTX 4080 SUPER) runs without OOM.
Do **not** pass `--lowvram`, `--novram`, `--highvram`, `--gpu-only` or
`--disable-dynamic-vram`: each disables the allocator. `--disable-smart-memory` is the legacy
aggressive-offload switch and is also forbidden: it forces a release after every prompt
instead of letting the allocator keep what fits.

The launcher passes exactly one memory knob, `--vram-headroom "${COMFYUI_VRAM_HEADROOM}"`
(default `1`): VRAM kept completely free, counting other apps. It is a shared-GPU policy, not
a memory bound.

Two checks defend the contract:
- `comfyui-vram-offload` runs one small prompt and asserts the load DELTA (>= 1 GiB) and a
  successful completion. It does NOT assert a post-prompt release; Dynamic VRAM deliberately
  keeps what fits.
- `comfyui-launch-flags` isolates the `exec` line and requires `--vram-headroom` while
  forbidding every legacy mode flag. It fails on the pre-cutover launcher.

## When to Use This Skill

**MUST be invoked** when the task involves the comfyui box, image generation, or ComfyUI workflows. Invoke this skill BEFORE reading source code or launching Explore agents.

## Model manifest, workflows and the shared model store

Every model is declared once in `models/model-manifest.tsv` (format
`<subdir>/<filename>|<url>|<sha256>`), copied into the image at
`/opt/comfyui/model-manifest.tsv` and read by `start-comfyui.sh` on every boot. The file is
the single source: the launcher writes it to the state volume, the background fetcher verifies
each sha256 before moving a file into place, and an existing file is skipped. The core set is
~128 GiB (the Qwen-Image-2.1/Edit-2511, Z-Image-Turbo bf16/int8 and Wan 2.2 files plus the
required companions `qwen3vl_8b`, the t2i PE and the Wan low-noise expert). The build-time
`comfyui-model-manifest-complete` check fails when a shipped API workflow references a model
with no manifest row.

The shared XDG model store is a deploy overlay, not a candy field: a candy may not pin a host
path. Bind `$XDG_DATA_HOME/charly/models/comfyui/models` at `/home/user/ComfyUI/models` and
keep per-deploy state under `$XDG_DATA_HOME/charly/volumes/<deploy>/comfyui` (the merged
distro-cachyos#219 pattern).

API workflows shipped for Open WebUI (`workflows/*.api.json`):
- `qwen-image-2.1-turbo-t2i.api.json` — Qwen-Image-2.1 Turbo int8, `ManualSigmas` 8-step
  schedule, `qwen3vl_8b` text encoder (`CLIPLoader.type=qwen_image`); the PE prompt-enhancer
  branch is pruned so the 8.8 GiB PE is not loaded for Open WebUI generation.
- `qwen-image-edit-2511-lightning.api.json` — Edit-2511 int8 + Lightning 4-step LoRA,
  `qwen_2.5_vl_7b_fp8_scaled`, `qwen_image` CLIP type, `LoadImage` + `FluxKontextImageScale`.
- `z-image-turbo-int8.api.json` / `z-image-turbo-bf16.api.json` — the reliable fallback and
  the quality variant. bf16 weights are ~18 GiB and stream; prefer int8 at 1024².
- `wan2.2-5b-ti2v.api.json` — direct ComfyUI only (Open WebUI 0.13.4 has no video backend).

The bundled templates are the direct-UI entry points; `#<uuid>` is not a route. Use
`?template=<slug>&source=default`:

```
http://localhost:8188/?template=image_qwen_image_2_1_turbo_t2i&source=default
http://localhost:8188/?template=image_qwen_image_edit_2511_int8&source=default
http://localhost:8188/?template=image_z_image_turbo_int8&source=default
http://localhost:8188/?template=video_wan2_2_5B_ti2v&source=default
http://localhost:8188/?template=video_wan2_2_14B_t2v&source=default
```

Every file is sha256-checked before it is moved into place; a partial download lands in a
`.part` and is RESUMED on the next attempt (`--continue-at -`).

To generate with Z-Image-Turbo: load the diffusion model, the Qwen3 text encoder and the `ae`
VAE, then sample at **8 steps with cfg 1** — Turbo is a Decoupled-DMD distilled checkpoint
(8 NFEs); `res_multistep`/`simple` is the bundled schedule. A normal 20-step cfg 7 workflow
produces noise. Qwen-Image-2.1 Turbo uses its `ManualSigmas` schedule and Edit-2511 uses the
Lightning 4-step LoRA.

### The shared LoRA shelf

A LoRA trained in **pod-ai-toolkit** reaches this pod through ONE host directory mounted on both
sides, so there is no copy step:

| Pod | Mount |
|---|---|
| `ai-toolkit` | host dir → `/workspace/shared-loras` |
| `comfyui` | the same host dir → `~/ComfyUI/models/loras` |

Declare it on the deploy (a candy can never pin a host path):

```yaml
volume:
    - name: shared-loras
      type: bind
      host: /home/<user>/charly-shared/zimage-loras
      path: /home/user/ComfyUI/models/loras
```

Because it replaces the `loras` directory inside the `comfyui` volume, treat it as the pod's LoRA
shelf: anything you drop there shows up in ComfyUI's LoraLoader, and anything trained in the
toolkit pod appears without a restart.

## Related

- `/charly-image:image` — image family umbrella (`candy:` image entries — those carrying `base:`/`from:` — in `charly.yml`, build/validate/inspect/list)
- `/charly-build:build` — the embedded build vocabulary (distros, builders, init-systems)
