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
| Base | nvidia |
| Candies | agent-forwarding, comfyui, dbus, charly |
| Platforms | linux/amd64 |
| Ports | 8188 |
| Registry | ghcr.io/opencharly |

## Full Candy Stack

1. `fedora` → `nvidia` (CUDA base)
2. `pixi` → `python` → `supervisord` (transitive)
3. `comfyui` — ComfyUI server, models/output volume

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
- `/charly-distros:nvidia` — GPU runtime and CDI device auto-detection (base)
- `/charly-distros:cuda` — CUDA toolkit and libraries (via nvidia base)
- `/charly-infrastructure:dbus-layer` — session bus for desktop notifications
- `/charly-tools:charly` — in-container `charly` CLI toolchain (scripting, service management)
- `/charly-distros:agent-forwarding` — SSH/GPG/direnv agent forwarding

## Related Boxes

- `/charly-distros:nvidia` — parent (GPU without ComfyUI)
- **CachyOS variant** — `cachyos.comfyui` is the CachyOS GPU sibling (built on the `cachyos.nvidia` GPU base) in the `opencharly/distro-cachyos` submodule. See `/charly-distros:cachyos`.

## Verification

After `charly start`:
- `charly status comfyui` — container running
- `charly service status comfyui` — all services RUNNING
- `curl -s -o /dev/null -w '%{http_code}' http://localhost:8188` — ComfyUI HTTP returns 200

## When to Use This Skill

**MUST be invoked** when the task involves the comfyui box, image generation, or ComfyUI workflows. Invoke this skill BEFORE reading source code or launching Explore agents.

## Z-Image-Turbo (quantized), and LoRAs trained in pod-ai-toolkit

The candy fetches a **model SET** on first boot, not just the single checkpoint, declared in
`COMFYUI_MODEL_SET` as one `models/<subdir>/<filename>|<url>|<sha256>` line per file. The
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
