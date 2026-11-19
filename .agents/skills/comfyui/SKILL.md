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

## VRAM: used while generating, released when idle

The launcher passes `--disable-smart-memory`, so the weights leave the card when a prompt
finishes. Measured on an RTX 4080 SUPER (16,376 MiB), one generation being
CheckpointLoaderSimple → KSampler 4 steps → VAEDecode → SaveImage:

| Reading | VRAM |
|---|---|
| idle, `/queue` empty, before any generation | 2,978 MiB |
| idle after that generation completed | 3,100 MiB |
| peak during a 1024×1024 20-step run | 5,980 MiB |
| this candy's historical baseline, weights kept resident | 10,954 MiB |

ComfyUI's own process barely moves (248 MiB idle → 370 MiB after a generation), which is the
point: between prompts the model is not pinned in VRAM.

The candy proves it on a live deployment: the `comfyui-vram-offload` runtime check runs one
512×512 prompt and requires the card's used VRAM to rise by at least 1 GiB while it runs and to
settle back within 768 MiB of the pre-prompt reading. It asserts that delta rather than an
absolute footprint, because `nvidia-smi` reports the whole device and another container's models
are in the same number; a launch without the flag keeps the weights and fails the check.

`COMFYUI_VRAM_HEADROOM` (default `1`) is a different knob: the margin ComfyUI's DynamicVRAM
keeps completely free, counting VRAM held by other apps. On a card with 9+ GiB spare it is met
trivially, so it does not decide the idle footprint and must not be read as a memory bound.

## When to Use This Skill

**MUST be invoked** when the task involves the comfyui box, image generation, or ComfyUI workflows. Invoke this skill BEFORE reading source code or launching Explore agents.

## Z-Image-Turbo (quantized), and LoRAs trained in pod-ai-toolkit

The candy fetches a **model SET** on first boot, not just the single checkpoint, declared in
`COMFYUI_MODEL_SET` as one `<subdir>/<filename>|<url>|<sha256>` line per file, where `<subdir>` is relative to
`~/ComfyUI/models` (so `diffusion_models/x.safetensors`, NOT `models/diffusion_models/x.safetensors`). The
default is the quantized Z-Image-Turbo trio, chosen because bf16 does not fit a 16 GB card
alongside a LoRA stack:

| File | Lands in | Size |
|---|---|---|
| `z_image_turbo_int8_convrot.safetensors` | `models/diffusion_models/` | 6.2 GB |
| `qwen_3_4b_fp8_mixed.safetensors` | `models/text_encoders/` | 5.6 GB |
| `ae.safetensors` | `models/vae/` | 0.34 GB |

Every file is sha256-checked before it is moved into place; a partial download lands in a
`.part` and RESUMED on the next attempt (`--continue-at -`); it is renamed into place only
after its sha256 verifies. `COMFYUI_MODEL_SET=` (empty) disables the set entirely — useful
when you want the fast single-checkpoint boot and nothing else.

To generate: load the diffusion model, the Qwen3 text encoder and the `ae` VAE, then sample at
**8 steps with cfg 0** — Turbo is a Decoupled-DMD distilled checkpoint (8 NFEs, guidance
disabled), so a normal 20-step cfg 7 workflow produces noise.

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
