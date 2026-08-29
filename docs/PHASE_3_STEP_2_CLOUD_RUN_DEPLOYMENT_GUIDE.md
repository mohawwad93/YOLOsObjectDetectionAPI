# Phase 3, Step 2: manual proof-of-concept deployment to Cloud Run

This is the working reference for the first real deployment of this service
outside a local machine or CI: a manual, by-hand `gcloud` deployment to
Google Cloud Run's Always Free tier, deliberately done before any of it gets
automated. It covers the architectural reasoning, a full pricing assessment
across every GCP touchpoint this deployment has, the final consolidated
runbook, and the one real issue hit getting there.

Read [`PHASE_2_STEP_2_HEALTH_GUIDE.md`](PHASE_2_STEP_2_HEALTH_GUIDE.md) and
[`PHASE_2_STEP_4_CONTAINERIZATION_GUIDE.md`](PHASE_2_STEP_4_CONTAINERIZATION_GUIDE.md)
first — this step deploys the exact image and health-check contract those
steps built, onto a platform neither was originally written for.

---

## Part A — Architectural rationale

### What scale-to-zero does under the hood

With `--min-instances=0` and nothing currently running, an incoming request —
HTTP or a WebSocket's initial `Upgrade` handshake — isn't rejected. Cloud
Run's front end **holds the connection open** while it provisions a fresh
instance: pulling the image, starting the process, then waiting for that
instance to satisfy its configured probe before routing the held request
through. The triggering request pays the cold-start cost as latency, not as
a failure — but only if something is correctly gating that decision. Get the
probe wrong, and "wait until ready" silently becomes "the instant the
process binds a port," which is a completely different guarantee.

### Why the startup probe has to point at `/readyz`

Cloud Run's implicit default is a bare TCP check — confirms *something* is
listening on the port, nothing more. `uvicorn` binds the port within
milliseconds of container start, while `lifespan.py`'s background task is
still 10–30 seconds from finishing `engine.load()` and `engine.warm_up()`. A
default probe would pass immediately and let Cloud Run route the first held
request into a container that can't yet serve it. Overriding the startup
probe to `httpGet.path=/readyz` closes that gap: Cloud Run keeps holding the
triggering request until `/readyz` genuinely returns `200`. `/healthz` maps
onto the **liveness** probe for the identical reason it was built that way —
it should almost never fail during normal operation, checked infrequently,
and Cloud Run's own crash-loop protection backs off on restarts after
repeated failures rather than thrashing indefinitely.

Cloud Run also allocates extra CPU to a container specifically during its
startup phase by default (`--cpu-boost`) — already working in this
deployment's favor with no flag needed.

### CPU-only constraints: memory and concurrency

**Memory — 1–2GiB, not a round number.** Too low risks an OOM kill *during*
the load spike (deserializing a checkpoint briefly needs more headroom than
the model's steady-state resident size), and unlike graceful degradation,
hitting a memory ceiling on Cloud Run is an abrupt kill with no swap to fall
back on. Too high just burns free-tier GiB-seconds for no benefit, since the
actual footprint (CPU-build `torch` + `transformers` + a genuinely tiny
model) comfortably fits under 2GiB.

**Concurrency — `1`, not the platform default of 80.** Our inference call is
CPU-bound work on a single process (the same reasoning as Step 4's
one-worker-per-container decision, just on the per-instance-concurrency axis
instead of the workers-per-container axis). Concurrent requests to one
instance don't parallelize the way I/O-bound requests would; they queue
behind whatever CPU that instance has. `--concurrency=1` means each
simultaneous visitor gets their own instance instead of queuing behind a
CPU-starved one.

---

## Part B — Registry topology: the two options considered, and why GHCR won

### The lifecycle

`gcloud run deploy` never uploads a build context — it references an image
that already exists at a registry path. `docker push` puts it there once;
Cloud Run pulls from that same path on every cold start, including the very
first one and every one after a period of inactivity.

### Artifact Registry (the original plan) — has real, if small, cost

Artifact Registry's storage pricing: the first 0.5 GB per project's
billing account is free per month, $0.10/GB/month beyond that. Our actual
image — CPU-build `torch`, `transformers` and its dependencies, the
`fastapi[standard]` stack, the Debian slim base — realistically lands
somewhere in the 1–1.5 GB range, meaning a *single* stored image is likely
to exceed the free allowance on its own, before any version accumulation.
`gcloud artifacts repositories set-cleanup-policies` (keeping the most
recent N versions, deleting untagged layers after an age threshold) controls
*accumulation* over time, but doesn't fix the fact that even one copy of an
image this size is probably already over the free line. Realistic cost with
Artifact Registry: roughly $0.05–$0.15/month. Genuinely small, but not $0.

### GitHub Container Registry (what's actually deployed) — genuinely $0

Cloud Run can deploy directly from **public** images in GitHub Container
Registry, with no Artifact Registry intermediary. GHCR has no storage cap or
per-GB charge for public images at any size — not "free up to a threshold,"
actually free. The one condition: the package must be explicitly set to
**Public** in its GitHub package settings — it's private by default on first
push, regardless of the source repository's own visibility.

---

## Part C — Full pricing assessment

Cloud Run's Always Free monthly allowance (`us-central1`, an Always
Free-eligible region): **180,000 vCPU-seconds, 360,000 GiB-seconds, 2,000,000
requests, 1 GiB of network egress** (North America). Beyond that: roughly
$0.000024/vCPU-second, $0.0000025/GiB-second, $0.40/million requests,
$0.12/GB egress.

| Dimension | Free allowance | What consumes it here | Risk at showcase traffic |
|---|---|---|---|
| Compute (vCPU + memory) | 180,000 vCPU-sec / 360,000 GiB-sec | Only counts while an instance is booting or serving — not idle time at `min-instances=0`. At `--cpu=1 --memory=2Gi`, both limits land on the same number: **~50 active hours/month** | Very low |
| Requests | 2,000,000 | Every hit to any endpoint, every WebSocket message | Negligible |
| Network egress | 1 GiB | Response bodies — `/detect/image`'s annotated JPEGs are the largest single response type | Low, but the tightest of the four Cloud Run dimensions |
| Image storage | GHCR: uncapped, free · Artifact Registry: 0.5 GB free, then $0.10/GB | ~1–1.5 GB image | **$0 with GHCR** |
| Image pull into Cloud Run | Not a billed dimension at all | Every cold start | **Always $0** — pulling is ingress, not egress, on either registry |
| Cloud Logging | A monthly free ingestion allowance, comfortably above our log volume | `lifespan.py` startup/health log lines | Effectively zero |
| Cloud Build | N/A | **Not used** — built and pushed locally, deliberately | Not a factor |
| Custom domain / Load Balancer | N/A | **Not used** — served from the free auto-provisioned `*.run.app` URL | Not a factor today; reassess if ever added |
| Secret Manager | N/A | Not used yet | Zero now; relevant at Phase 3, Step 3 |

**Why pulling the image costs nothing, specifically**: egress is Cloud Run
sending data *out* to the internet; a pull is data flowing *into* Google's
infrastructure — ingress, which is free on essentially every GCP service,
regardless of source registry. GHCR doesn't meter bandwidth on public
package pulls either.

**The actual guarantee mechanism isn't this table — it's the billing budget
tripwire in Part D.** This assessment is a well-reasoned expectation, not a
platform commitment; the tripwire is what turns "should be $0" into "will
know immediately if it isn't."

---

## Part D — The final runbook

### Step 0 — Billing tripwire, before anything else

```bash
gcloud billing accounts list

# Deliberately absurd thresholds — the point isn't the dollar amount,
# it's getting notified the moment ANY charge appears, from any source.
gcloud billing budgets create \
  --billing-account=YOUR_BILLING_ACCOUNT_ID \
  --display-name="yolos-detection-api zero-cost tripwire" \
  --budget-amount=1USD \
  --filter-projects="projects/YOUR_PROJECT_ID" \
  --threshold-rule=percent=0.01 \
  --threshold-rule=percent=1.0
```

### Step 1 — Project setup

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com
# Artifact Registry's API is deliberately NOT enabled — it isn't part
# of this deployment's path at all.
```

### Step 2 — Build

```bash
docker build --platform=linux/amd64 \
  --build-arg TORCH_BACKEND=cpu \
  -t yolos-detection-api:v1 .
```

### Step 3 — Push to GHCR

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin

docker tag yolos-detection-api:v1 ghcr.io/YOUR_GITHUB_USERNAME/yolos-detection-api:v1
docker push ghcr.io/YOUR_GITHUB_USERNAME/yolos-detection-api:v1
```

Then on GitHub: profile → **Packages** → the pushed package → **Package
settings** → change visibility to **Public**. See Part E for what happens if
a deploy is attempted before this fully propagates.

### Step 4 — Deploy

```bash
gcloud run deploy yolos-detection-api \
  --image=ghcr.io/YOUR_GITHUB_USERNAME/yolos-detection-api:v1 \
  --region=us-central1 \
  --platform=managed \
  --allow-unauthenticated \
  --port=8000 \
  --memory=2Gi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=2 \
  --concurrency=1 \
  --timeout=300 \
  --startup-probe=httpGet.path=/readyz,httpGet.port=8000,initialDelaySeconds=0,periodSeconds=5,failureThreshold=12,timeoutSeconds=3 \
  --liveness-probe=httpGet.path=/healthz,httpGet.port=8000,periodSeconds=30,timeoutSeconds=3,failureThreshold=3
```

Notes on the two values most likely to look arbitrary:

- **`--max-instances=2`** — doesn't change cost at normal traffic (usage
  drives cost, not the ceiling), but caps the worst case if the public URL
  ever sees abusive or unexpectedly heavy traffic.
- **No `--no-cpu-throttling` flag, deliberately.** That flag governs CPU
  allocation for an instance idling *between* requests — but at
  `min-instances=0` there's no persistently idle instance for it to apply
  to. `--cpu-boost` (on by default) already covers the phase that matters:
  the cold-start load window itself.

### Step 5 — Watch it happen

```bash
gcloud beta run services logs tail yolos-detection-api --region=us-central1
```

Or Console → **Cloud Run → yolos-detection-api → Logs**, streaming on.
Trigger a cold start and confirm, in order: container start, `lifespan.py`'s
"Startup: loading hustvl/yolos-tiny," the load/warm-up gap, "Startup
complete: engine status=ready" — with no 502/504 anywhere in that window,
because `/readyz` was what Cloud Run was actually waiting on.

### Plan B, if GHCR ever proves too flaky

Artifact Registry (Part B) is a completely legitimate fallback — proven
reliable, at a real but small cost the billing tripwire will confirm rather
than leave you guessing about. Genuinely-$0-but-newer-and-occasionally-rough
and reliably-$0.10/month-but-mature are both reasonable choices; this
project chose the former, deliberately, for the education in doing so.

---

## Part E — The debugging narrative

| Issue | Symptom | Root cause | Fix |
|---|---|---|---|
| `ERROR: (gcloud.run.deploy) Cloud Run does not have permission to pull the image 'cache.us-docker.pkg.dev/ghcr.io/.../yolos-detection-api:v1'` | Deployment failed at the image-pull stage, after IAM policy was already set | Cloud Run doesn't pull directly from GitHub — public-GHCR support is implemented by routing through **Google's own caching proxy** in front of external registries. That proxy pulls anonymously, the same as any logged-out stranger, and had a **stale cached failure** for this exact tag from an earlier attempt (made before the package's visibility had fully propagated to Public) | Push a new tag (`v2`) rather than retrying the same one — this forces the proxy to make a genuinely fresh pull attempt instead of potentially serving a cached failure for the old tag. Confirmed as the actual cause: the fix that worked was specifically the tag change, not a further visibility change |
| (Diagnostic step, not a bug) | — | Distinguishing "stale cache" from "not actually public yet" required a source of truth independent of Cloud Run's proxy | `docker logout ghcr.io && docker pull ghcr.io/.../image:tag` — genuinely anonymous, bypasses Google's proxy entirely, and directly answers "is this actually publicly reachable" without Cloud Run's caching layer in the way |

**The general lesson**: a caching proxy sitting in front of an external
dependency is one more layer where "it should work now" and "it will
actually respond correctly on the next attempt" can diverge — the same
category of gap as Docker's own build-layer caching from Step 4, just one
hop further out, on infrastructure this project doesn't control. When a
fix that should have worked doesn't, changing the identifier (a new tag,
here) rather than repeating the identical request is often the fastest way
to rule a stale cache in or out.

---

## Status

Live, on Cloud Run, in `us-central1`, serving from a public GHCR image at
$0/month per the Part C assessment, with a billing tripwire confirming it.
The manual proof-of-concept this step set out to prove — that the image runs
correctly on GCP, and that Cloud Run's native probes correctly read this
project's `/readyz`/`/healthz` contract through a real cold start — is
complete and observed firsthand, not just reasoned about.

**What's next**: with a working, by-hand deployment as the reference, Phase
3's original Step 2 (Infrastructure as Code) now has something concrete to
codify — the exact `gcloud run deploy` flags above, rather than a guess at
what they should be.
