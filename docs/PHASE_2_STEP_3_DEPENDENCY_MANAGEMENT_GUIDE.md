# Phase 2, Step 3: reproducible environments and dependency management

This is the working reference for how this project's dependencies are
declared, locked, and installed with `uv` — the reasoning behind
`pyproject.toml`'s structure, a real mistake caught and corrected while
building it, and the commands that actually get used day to day.

Read [`ARCHITECTURE.md`](ARCHITECTURE.md) first if you haven't; this document
assumes the layered structure exists but is otherwise self-contained.

---

## Part A — Architectural rationale

### The hazard: non-deterministic builds in an AI stack

An unpinned or loosely-pinned dependency spec (`transformers>=4.40`) isn't a
version — it's an instruction to re-run a search every time the project is
built, and trust whatever wins that search *today*. For an ML inference
stack specifically, that's a correctness hazard with no error message
attached:

- **`transformers` version drift is silent.** A minor release can change
  default preprocessing or post-processing behavior inside
  `pipeline("object-detection")` without touching a single line of our code.
  `/detect` doesn't crash — it just starts returning subtly different
  bounding boxes or confidence scores than the ones the test suite was
  written against, with nothing signaling that anything changed.
- **`torch` version drift is a hardware-compatibility hazard.** A different
  build can imply a different expected CUDA toolkit underneath it. Get this
  wrong in production and the failure isn't "slightly different numbers" —
  it's an engine that won't initialize on the GPU, or one that silently
  falls back to CPU and serves every request an order of magnitude slower.
- **Dev/CI/prod skew.** If three machines each resolve dependencies
  independently, on three different days, against three different states of
  the package index, "works on my machine" becomes a literally accurate
  description of the bug: each machine may be running genuinely different
  code disguised as the same version range.

### How `uv.lock` closes the gap

A version pin (`torch==2.5.1`) still trusts the registry to hand back
whatever is published under that label. `uv.lock` pins one level deeper:
every resolved package records a cryptographic content hash alongside its
version, and installs are verified against that hash before anything is
written to disk. A build either installs the exact bytes that were resolved
and tested, or fails loudly — not a silent substitution. That's what
"deterministic" actually means here: not just the same version number, the
same file.

### Dependency Groups: minimizing the production attack surface

`[project.optional-dependencies]` (extras) are part of a package's published
metadata and ship with the built distribution. PEP 735's dependency groups —
`[dependency-groups]` — exist specifically so requirements never end up in a
built distribution or a downstream install at all. `pytest`, `httpx`, `ruff`,
and their transitive dependencies never need to exist inside the container
that receives production traffic. Every package that does exist there is
something a vulnerability scanner eventually flags a CVE against, whether or
not it's ever executed — keeping test tooling out of the deployable artifact
is a direct reduction in what has to be patched and triaged, not tidiness.

`uv sync` with no flags installs `[project]` dependencies plus the `dev`
group by default — convenient for a laptop. The production build path
inverts that explicitly (`uv sync --no-dev --frozen`), which is why `test`
and `dev` are separate groups here rather than one bucket: CI can install
exactly `test` without also pulling in a linter it doesn't run.

### Graceful shutdown for open WebSocket streams

*(Carried forward from Step 2 — restated here only because the run-command
change below directly affects it.)* `lifespan.py`'s background load task,
`active_sessions` set, and `shutting_down` flag are all per-process state.
Anything that changes how many processes serve a single container — workers,
Gunicorn — changes whether that state means what Step 2 built it to mean.
See Part E.

---

## Part B — The `uv` workflow concept: one lockfile, many platforms

A uv lockfile is universal by design: it resolves dependencies across
platforms in a single file rather than requiring one lockfile per target.
That's what makes "developed on macOS, deployed to Linux CUDA" a solved
problem rather than a process to invent.

For `torch` specifically: PyTorch publishes no CUDA wheels for macOS at all,
so the correct artifact genuinely differs by platform. Two named, `explicit
= true` package indexes are declared — one CPU-only, one CUDA 12.6 — and
`tool.uv.sources` routes `torch` to the CUDA index when resolving for Linux
and the CPU index when resolving for macOS. Both resolutions land in the
*same* `uv.lock`, each tagged with the marker condition it applies under. A
developer's `uv sync` on a MacBook reads that file and installs the `darwin`
branch; CI's `uv sync` on a Linux runner reads the identical file and
installs the `linux` branch. There's structurally one source of truth, not
two files kept in sync by discipline.

**The caveat this step actually surfaced:** that routing is only reliably
applied to **direct** dependencies. If `torch` arrives purely transitively —
which is what activating `transformers[torch]`'s extra does — `uv`'s
resolver has documented cases of silently ignoring the source override for
transitive-only packages. The fix is structural, not a workaround: anything
whose *installed artifact* needs to be controlled per-platform must be
declared as a direct dependency of the project, even if some other
dependency would have pulled a version of it in anyway. `pillow` or
`pydantic-settings` don't need this — there's no platform-specific wheel
selection happening for them — but `torch` does, so `torch` gets the direct
declaration.

`tool.uv.environments` scopes resolution to the platforms this project
actually claims to support (Apple Silicon dev laptops, Linux x86_64
production), rather than spending lock-resolution effort on platforms
nobody uses — an explicit, reviewable statement instead of an implicit
assumption.

---

## Part C — Full `pyproject.toml`

```toml
[project]
name = "yolos-detection-api"
version = "0.2.0"
description = "FastAPI object-detection service wrapping hustvl/yolos-tiny"
readme = "README.md"
# An upper bound, not just a lower one: tested against 3.12.x specifically.
requires-python = ">=3.12,<3.13"

dependencies = [
    # fastapi[standard] pulls in uvicorn[standard] (websockets — required
    # for /ws/detect; bare uvicorn has NO WebSocket implementation) and
    # python-multipart (required for File()/UploadFile — silently no-ops
    # into a confusing 422 without it). Version-matched by FastAPI's own
    # maintainers against this exact release; do not unbundle these into
    # separately-versioned lines.
    "fastapi[standard]>=0.141.1,<1",

    # Declared as TWO separate direct dependencies — not
    # `transformers[torch]`. See Part B: uv's tool.uv.sources index
    # routing (below) is only reliably honored for direct dependencies.
    # torch arriving only transitively, via transformers' `torch` extra,
    # risks the CUDA/CPU platform routing being silently ignored during
    # resolution. Upper-bounded below the next major version: a
    # transformers major bump is exactly the kind of change likely to
    # alter pipeline() defaults in ways that change detection results
    # without changing our code.
    "transformers>=4.49.0,<5",
    "torch>=2.5,<3",
    "pillow>=12.3.0,<13",
  
    "pydantic-settings>=2.15.0,<3",
]

[dependency-groups]
# PEP 735 — never bundled into a built distribution or a production
# install (`uv sync --no-dev`). Keeps pytest and its transitive deps
# entirely out of the deployed container's CVE surface.
test = [
    "pytest>=9.1.1,<10", 
    "pytest-asyncio>=1.4.0,<2", 
    # Required by FastAPI's TestClient (built on httpx, not `requests`).
    "httpx>=0.28.1,<1",
]
dev = [
    { include-group = "test" },  # local dev gets test tooling too, without duplicating the list
    "ruff>=0.16.3,<1"
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
# Maps the existing src/backend/ layout to an installable "backend"
# package — this is what makes `fastapi run src/backend/app.py` and
# `fastapi dev src/backend/app.py` work directly, with no --app-dir
# flag, because the package is genuinely installed (editable) into
# .venv rather than located via a path trick at runtime.
packages = ["src/backend"]

[tool.uv]
# Scopes lock resolution to the platforms this project actually claims
# to support. An explicit, reviewable line instead of an implicit
# assumption about what's supported.
environments = [
    "sys_platform == 'darwin' and platform_machine == 'arm64'",
    "sys_platform == 'linux' and platform_machine == 'x86_64'",
]
# Supply-chain hardening: resolution ignores any package version
# published after this timestamp. Protects against a compromised or
# typo-squatted release landing on the index moments before a build
# runs. Deliberately maintained — bump forward on a known cadence
# during dependency review, never silently.
exclude-newer = "2026-08-16T00:00:00Z"

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true  # only used for packages that name it directly below

[[tool.uv.index]]
name = "pytorch-cu126"
url = "https://download.pytorch.org/whl/cu126"
explicit = true

[tool.uv.sources]
# PyTorch publishes no CUDA wheels for macOS — this isn't a preference,
# it's the only correct mapping. Both branches are resolved and hashed
# into the one committed uv.lock.
torch = [
    { index = "pytorch-cu126", marker = "sys_platform == 'linux'" },
    { index = "pytorch-cpu", marker = "sys_platform == 'darwin'" },
]
```

---

## Part D — Commands

| Command | Where | What it guarantees |
|---|---|---|
| `uv sync` | Local development | Installs production deps + the `dev` group (which includes `test`) — convenient default for a laptop |
| `uv run fastapi dev src/backend/app.py` | Local development only | Auto-reload, binds `127.0.0.1` — unreachable outside its own machine by design |
| `uv lock` | Whenever `pyproject.toml`'s ranges change | Re-resolves and rewrites `uv.lock` — the only command allowed to modify it |
| `uv sync --locked` | CI, as a gate | Fails immediately if `uv.lock` is out of sync with `pyproject.toml` |
| `uv sync --no-dev --frozen` | Docker build stage | Installs exactly what's pinned in `uv.lock`, skips `dev`, never re-resolves |
| `uv run fastapi run src/backend/app.py` | Container / production | No reload, binds `0.0.0.0`, single process |

---

## Part E — Lessons and gotchas worth remembering

| Issue | Where | Takeaway |
|---|---|---|
| `torch` declared via `transformers[torch]` instead of directly | `pyproject.toml` (caught during review, corrected here) | `tool.uv.sources` index routing is only reliably honored for direct dependencies. Anything whose installed artifact needs platform-specific control must be a direct dependency, regardless of whether another package would have pulled a version of it anyway. |
| `fastapi dev` used as the only run command | Local workflow | `dev` and `run` differ in exactly two defaults — `--reload` and bind address (`127.0.0.1` vs `0.0.0.0`). The bind address alone makes `dev` unreachable inside a container; this isn't a performance concern, it's a hard connectivity failure. |
| Instinct to add `--workers N` or front the app with Gunicorn | Container / production launch | `lifespan.py`'s background load task and `app.state` (engine, `active_sessions`, `shutting_down`, from Step 2) are per-process. Multiple workers per container means multiple loaded model copies in memory, and `/readyz` can flap depending on which worker answers a given probe. Scale via Kubernetes replica count instead — it sidesteps both problems and matches an orchestrator that already supervises and restarts the process. |
| `fastapi[standard]` vs. manually unbundling `uvicorn[standard]` + `python-multipart` | `pyproject.toml` | The extra is the better choice, not a shortcut — it inherits a compatibility matrix maintained by FastAPI itself against each specific release. |

---

## Part F — What's next

Dependency management is now deterministic and minimal. The natural next
step is packaging this into a container image: a multi-stage Dockerfile
where `uv.lock` becomes the cache-invalidation key for the dependency layer,
`uv sync --no-dev --frozen` runs in the build stage, and the final image
runs `fastapi run src/backend/app.py` as a single process per container —
consistent with the replica-based scaling decision in Part E.
