terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

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
  default = "us-central1"
}

variable "container_image" {
  type        = string
  description = "Full image reference INCLUDING an immutable tag (short git SHA) — never :latest"
}

variable "hf_token" {
  type        = string
  description = "Hugging Face Hub access token — read scope is sufficient"
  sensitive   = true
}

# ----------------------------------------------------------------------
# Secret Manager: Hugging Face Hub token
# ----------------------------------------------------------------------
resource "google_secret_manager_secret" "hf_token" {
  secret_id = "hf-token"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "hf_token" {
  secret      = google_secret_manager_secret.hf_token.id
  secret_data = var.hf_token
}

# ----------------------------------------------------------------------
# Runtime identity for the Cloud Run SERVICE itself
# ----------------------------------------------------------------------
resource "google_service_account" "cloud_run_runtime" {
  account_id   = "yolos-api-runtime"
  display_name = "yolos-detection-api Cloud Run runtime identity"
}

resource "google_secret_manager_secret_iam_member" "hf_token_access" {
  secret_id = google_secret_manager_secret.hf_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_runtime.email}"
}

# ----------------------------------------------------------------------
# The Cloud Run service itself
# ----------------------------------------------------------------------
resource "google_cloud_run_v2_service" "api" {
  name     = "yolos-detection-api"
  location = var.region

  deletion_protection = true

  template {
    service_account = google_service_account.cloud_run_runtime.email

    containers {
      image = var.container_image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
      }

      env {
        name = "HF_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.hf_token.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/readyz"
          port = 8000
        }
        initial_delay_seconds = 0
        period_seconds        = 5
        failure_threshold     = 12
        timeout_seconds       = 3
      }

      liveness_probe {
        http_get {
          path = "/healthz"
          port = 8000
        }
        period_seconds    = 30
        failure_threshold = 3
        timeout_seconds   = 3
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    max_instance_request_concurrency = 1
    timeout                          = "300s"
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  # <-- ADDED (two reasons, both listed). The required APIs must exist
  # before Cloud Run can be created at all, AND the secret VERSION (the
  # actual token value) must exist before Cloud Run tries to reference
  # it as an environment variable. Both go in the same list.
  depends_on = [
    google_secret_manager_secret_version.hf_token,
  ]
}

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