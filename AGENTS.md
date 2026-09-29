# AGENTS.md — pod-comfyui

Standalone candy repo for the `comfyui` candy — the GPU image-generation server
(ComfyUI + Manager + model-download tooling) running on port `8188`. The candy
lives in `charly.yml` at the repo root plus its build input `pixi.toml` /
`pixi.lock`.

Canonical files:

- `charly.yml` — the `comfyui:` candy entity (description, `require`, `distro`,
  port, volume, service, plan) and its `skill:` entity.
- `pixi.toml` / `pixi.lock` — the Python environment for the server.
- `.github/workflows/tag-on-merge.yml` — CalVer tag + `CHANGELOG/` on merge.
- `CHANGELOG/` — per-CalVer history.
- `README.md` — user overview only; never agent guidance.

## Load these skills first (R0)

- `/charly-comfyui:comfyui` — the owning skill: box properties, the candy stack,
  and verification. Load before editing, building, deploying, or troubleshooting
  this candy.
- `/charly-distros:nvidia` / `/charly-distros:cuda` — the GPU base and toolkit the
  candy requires.
- `/charly-image:layer` — the candy authoring reference (`charly.yml` schema,
  `plan:` step verbs, per-distro sections, service declarations).
- `/charly-internals:git-workflow` — before any git/PR action.

## Build / validate / test

- `charly box validate` at the repo root — the structural check: the manifest
  must parse and validate at the installed charly.
- The merge gate is the **org-wide** `charly/pr-validator` (required check
  `validate / validate`, defined in `opencharly/.github`); this repo has **no**
  per-repo candy gate. Its only workflow file is
  `.github/workflows/tag-on-merge.yml`.
- The live R10 witness is a composing GPU box's `check` bed; the candy's own
  `check:` steps assert the cloned entrypoint + Manager, the model dirs, the
  download tooling, and the live `/system_stats` API on `8188`.

## Modify this repo

- Edit the `comfyui:` candy entity in `charly.yml`; the `skill:` entity in the
  same file is the owning skill's source — a candy change and its skill change
  land together.
- Python dependency changes belong in `pixi.toml` / `pixi.lock`, not in the plan.
- The `comfyui` volume at `~/ComfyUI` is the persistent model/output store; keep
  the service's `working_directory` and the clone path in step.
- The `skill:` entity is the source for `/charly-comfyui:comfyui`; never edit the
  generated `SKILL.md` — regenerate it.

## Landing

- PR-only. Every change lands through a pull request; the org-required
  `charly/pr-validator` validates the diff and body and arms native auto-merge
  on PASS. Direct pushes to `main` are blocked.
- History lives in `CHANGELOG/` (written by `tag-on-merge` at merge time); the
  PR body IS the changelog.
- The authoritative rulebook is the umbrella `AGENTS.md` in
  `opencharly/opencharly` and `charly/AGENTS.md` in the charly repo. Do not
  restate its rules here.
