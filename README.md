# pod-comfyui

The `comfyui` candy of the OpenCharly candy library, as a standalone repo (the
candy de-submodule cutover, kind-prefixed naming). It ships a GPU
image-generation server with ComfyUI + the Manager and model-download tooling
baked in.

## What it provides

Clones ComfyUI to `~/ComfyUI` plus the ComfyUI-Manager custom node and the full
model directory tree, and installs the `aria2` + `git-lfs` tooling used to fetch
model checkpoints. A supervisord service runs `main.py --listen 0.0.0.0` on port
`8188`.

| Property | Value |
|---|---|
| Port | `8188` (ComfyUI web UI) |
| Service | `comfyui` (`python ~/ComfyUI/main.py --listen 0.0.0.0`, `restart: always`) |
| Requires | `layer-supervisord` (the GPU **substrate** is the box's — see below) |
| Volume | `comfyui` at `~/ComfyUI` |
| Package | `aria2`, `git-lfs` (arch/fedora) |
| API | `/system_stats` on `8188` |

## How to use it

The candy declares only what it needs to **run** — the supervisord service. It ships no
CUDA toolkit and no GPU driver: PyTorch arrives as the `cu130` wheel (which bundles its own
CUDA runtime) and the **driver comes from the substrate**, which the **box** chooses.

**A pod** — the kernel module is host-global, so the runtime injects the driver user-space
per container through `/etc/cdi/nvidia.yaml`. Base the box on a CDI base (`pod-cdi`, or a
distro's `nvidia-cdi`), which composes `layer-cdi`:

```yaml
my-comfyui-pod:
  candy:
    base: nvidia-cdi          # pod base: layer-cdi's contract, injected host driver
    candy:
      - '@github.com/opencharly/pod-comfyui:<tag>'
```

**A VM** — the guest owns its kernel module, so the driver user-space must be baked in.
Base the box on `vm-nvidia`, which composes `layer-nvidia` + `layer-cuda`; composing this
candy on any other base, name those two layers yourself at the box's `candy:` level:

```yaml
my-comfyui-vm:
  candy:
    base: vm-nvidia
    candy:
      - '@github.com/opencharly/pod-comfyui:<tag>'
```

```bash
charly box build my-comfyui-pod
charly start my-comfyui-pod
# open http://localhost:8188
```

## Layout

- `charly.yml` — the `comfyui` candy entity plus its `skill:` entity.
- `pixi.toml` / `pixi.lock` — the Python environment for the server.
- `.github/workflows/tag-on-merge.yml` — CalVer tag + `CHANGELOG/` on merge.
- `README.md` — this user overview.

## Related

- Owning skill: `/charly-comfyui:comfyui` — the box properties, the candy stack,
  and verification.
- `/charly-distros:cdi` — the POD GPU contract (`layer-cdi`), and `/charly-distros:nvidia`
  / `/charly-distros:cuda` — the VM driver + toolkit layers a **VM box** composes.
- [`opencharly/charly`](https://github.com/opencharly/charly) — the charly CLI and image builder
- [`opencharly/opencharly`](https://github.com/opencharly/opencharly) — the umbrella
