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
| Requires | `layer-cuda`, `layer-supervisord` |
| Volume | `comfyui` at `~/ComfyUI` |
| Package | `aria2`, `git-lfs` (arch/fedora) |
| API | `/system_stats` on `8188` |

## How to use it

Compose the candy into a GPU box:

```yaml
my-comfyui:
  candy:
    base: cachyos.nvidia
    candy:
      - '@github.com/opencharly/pod-comfyui:<tag>'
```

```bash
charly box build my-comfyui
charly start my-comfyui
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
- `/charly-distros:nvidia` / `/charly-distros:cuda` — the GPU base and toolkit.
- [`opencharly/charly`](https://github.com/opencharly/charly) — the charly CLI and image builder
- [`opencharly/opencharly`](https://github.com/opencharly/opencharly) — the umbrella
