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
    containers {
      image = var.container_image

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