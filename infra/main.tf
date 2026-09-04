terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Remote state, in the bucket created by hand above. NEVER local,
  # NEVER committed — see Part 1 for what this file actually contains.
  backend "gcs" {
    bucket = "yolos-objectdetection-api-tfstate"
    prefix = "yolos-detection-api/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type    = string
  default = "us-central1" # Always Free-eligible — see Phase 3, Step 2
}

variable "container_image" {
  type        = string
  description = <<-EOT
    Full image reference INCLUDING an immutable tag — the short git
    commit SHA, e.g. ghcr.io/OWNER/yolos-detection-api:a1b2c3d.
    NEVER pass a value ending in :latest — see Part 1 for why a mutable
    tag breaks this config's diff model and this project's rollback story.
  EOT
}

resource "google_cloud_run_v2_service" "api" {
  name     = "yolos-detection-api"
  location = var.region

  # Requires deliberate action to actually delete this service — a
  # safeguard against an accidental `terraform destroy` touching the
  # one thing actually serving users.
  deletion_protection = true

  template {
    service_account = google_service_account.cloud_run_runtime.email
    containers {
      image = var.container_image

      env {
        name = "HF_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.hf_token.secret_id
            version = "latest"
          }
        }
      }

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi" # sizing reasoning: Phase 3, Step 2, Part A
        }
      }

      # Maps directly onto our own /readyz — gates traffic on the exact
      # 10-30s model-load window this project has built around since
      # Phase 2, Step 2. failure_threshold * period_seconds = 60s of
      # allowance, comfortable margin above the documented load time.
      startup_probe {
        http_get {
          path = "/readyz"
        }
        initial_delay_seconds = 0
        period_seconds         = 5
        failure_threshold      = 12
        timeout_seconds        = 3
      }

      # Maps onto /healthz — checked infrequently, deliberately.
      # Liveness should almost never fail during normal operation.
      liveness_probe {
        http_get {
          path = "/healthz"
        }
        period_seconds    = 30
        failure_threshold = 3
        timeout_seconds   = 3
      }
    }

    scaling {
      min_instance_count = 0 # non-negotiable — the entire $0/month rationale depends on this
      max_instance_count = 2 # worst-case cost ceiling, not a cost driver at normal traffic
    }

    # CPU-bound inference on a single process doesn't parallelize across
    # concurrent requests the way I/O-bound work would (Phase 4,
    # Step 4's worker-math, on a different axis). Each simultaneous
    # visitor gets their own instance instead of queuing behind one.
    max_instance_request_concurrency = 1

    timeout = "300s" # generous, for /ws/detect's long-lived WebSocket sessions
  }

  depends_on = [google_secret_manager_secret_version.hf_token]

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

# Terraform's explicit equivalent of `gcloud run deploy
# --allow-unauthenticated` — public showcase access, stated as its own
# auditable resource rather than folded silently into a deploy flag.
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "service_url" {
  value       = google_cloud_run_v2_service.api.uri
  description = "The live URL of the deployed service"
}

# Added after a real production incident: anonymous huggingface.co
# requests share Cloud Run's egress IP pool with many other GCP
# tenants and got rate-limited (429) during a cold start — which our
# own fail-fast engine.load() correctly caught and reported via
# /readyz. This fixes the actual cause, not the symptom.
resource "google_secret_manager_secret" "hf_token" {
  secret_id = "hf-token"
  replication {
    auto {}
  }
}

variable "hf_token" {
  type        = string
  description = "Hugging Face Hub access token — read scope is sufficient"
  sensitive   = true
}

# Worth being precise about what this does and doesn't protect: this
# value DOES land in Terraform state for this specific resource, in
# plaintext — this isn't "secrets that never touch state," it's why
# the state file's own security (the GCS backend, restricted IAM,
# versioning) from earlier in this step matters as much as it does.
# What Secret Manager actually buys: access-controlled runtime
# injection, rotation, and audit logging — never hardcoded in a .tf
# file, never committed, never visible in a docker inspect.
resource "google_secret_manager_secret_version" "hf_token" {
  secret      = google_secret_manager_secret.hf_token.id
  secret_data = var.hf_token
}

# A dedicated RUNTIME identity for the service itself — distinct from
# github-actions-deployer, which only ever deploys it. Least privilege:
# this identity can read exactly one secret and nothing else.
resource "google_service_account" "cloud_run_runtime" {
  account_id   = "yolos-api-runtime"
  display_name = "yolos-detection-api Cloud Run runtime identity"
}

resource "google_secret_manager_secret_iam_member" "hf_token_access" {
  secret_id = google_secret_manager_secret.hf_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_runtime.email}"
}