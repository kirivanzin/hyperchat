terraform {
  required_version = ">= 1.11"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

variable "project" {
  description = "The GCP project. No default, on purpose: the project id is not in this repository."
  type        = string
}

variable "region" {
  description = "The region the service runs in, beside the registry that holds its image."
  type        = string
  default     = "australia-southeast1"
}

variable "service" {
  description = "The Cloud Run service name."
  type        = string
  default     = "hyperchat"
}

variable "image" {
  description = "The image to run, by tag or digest. `make deploy` passes the registry image for this commit."
  type        = string

  validation {
    condition     = length(trimspace(var.image)) > 0
    error_message = "image must not be empty."
  }
}

variable "expected_audience" {
  description = "The audience a Google ID token must carry. The gcloud user-token audience is a constant, so it has a default; override only if that client id changes."
  type        = string
  default     = "32555940559.apps.googleusercontent.com"

  validation {
    condition     = length(trimspace(var.expected_audience)) > 0
    error_message = "expected_audience must not be empty."
  }
}

variable "allowed_subs" {
  description = "Comma-separated subjects allowed to call the service. Do not copy .env: that list includes the agent's subject, which must not be deployed."
  type        = string

  validation {
    condition     = length(trimspace(var.allowed_subs)) > 0
    error_message = "allowed_subs must not be empty: an empty allowlist refuses every caller."
  }
}

variable "provider_secret" {
  description = "Secret Manager secret that holds PROVIDER_API_KEY."
  type        = string
  default     = "provider-api-key"
}

variable "runtime_account" {
  description = "The service account the revision runs as. It gets one grant: secretAccessor on the provider secret."
  type        = string
  default     = "hyperchat-runtime"
}

variable "max_concurrency" {
  description = "Simultaneous requests one instance accepts. Every one of them can spend on a provider call, so this is a cost bound rather than a platform default."
  type        = number
  default     = 4

  validation {
    condition     = var.max_concurrency >= 1 && var.max_concurrency <= 1000
    error_message = "max_concurrency must be between 1 and Cloud Run's maximum of 1000."
  }
}

variable "max_instances" {
  description = "Maximum number of instances. This multiplies max_concurrency: one instance caps the service at four simultaneous provider calls, not Cloud Run's default hundred instances times four."
  type        = number
  default     = 1

  validation {
    condition     = var.max_instances >= 1
    error_message = "max_instances must be at least 1; scale to zero is set with min_instance_count, not by making the maximum zero."
  }
}

variable "provider_api_key" {
  description = "The provider API key. `make deploy` supplies it as TF_VAR_provider_api_key from outside the checkout; it is write-only and is never written to state or the plan file."
  type        = string
  sensitive   = true
  ephemeral   = true
  nullable    = false

  validation {
    condition     = length(trimspace(var.provider_api_key)) > 0
    error_message = "provider_api_key must not be empty: set TF_VAR_provider_api_key from the token file."
  }
}

variable "provider_key_version" {
  description = "Integer that triggers a new secret version when bumped. The value itself comes from provider_api_key."
  type        = number
  default     = 1

  validation {
    condition     = var.provider_key_version >= 1
    error_message = "provider_key_version must be at least 1; bump it deliberately to rotate the key."
  }
}

# A service account of its own, not the default compute account: that account has project-wide editor
# rights, and a runtime identity with those is a blast radius rather than a deployment detail.
resource "google_service_account" "runtime" {
  account_id   = var.runtime_account
  display_name = "hyperchat Cloud Run runtime"
  description  = "Runs the service. The only grant is secretAccessor on the provider secret."
}

# The secret container and its version are Terraform's. The value is write-only: it arrives as
# TF_VAR_provider_api_key, never enters state or the plan file, and `provider_key_version` is the
# explicit rotation trigger. The revision pins this version rather than `latest`, so the secret and
# the service move together in one apply.
resource "google_secret_manager_secret" "provider_key" {
  secret_id = var.provider_secret

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "provider_key" {
  secret                 = google_secret_manager_secret.provider_key.id
  secret_data_wo         = var.provider_api_key
  secret_data_wo_version = var.provider_key_version
}

resource "google_secret_manager_secret_iam_member" "runtime_accessor" {
  secret_id = google_secret_manager_secret.provider_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_service" "service" {
  name     = var.service
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  # Destroying the service is not the same as losing its state, but it is still not a step to take by
  # accident. The explicit value is the provider's default, named here so the refusal is visible.
  deletion_protection = true

  # The per-instance concurrency is multiplied by the instance count. Cloud Run's default maximum is
  # 100 instances, so the pinned four would become up to 400 simultaneous provider calls; one instance
  # makes the total four. `min_instance_count = 0` is the accepted scale-to-zero trade.
  scaling {
    min_instance_count = 0
    max_instance_count = var.max_instances
  }

  template {
    service_account = google_service_account.runtime.email

    # Pinned, not inherited. A provider call is slow and can spend money; four simultaneous calls per
    # instance is the per-instance bound. The scaling block above pins how many instances can multiply
    # it, so the total is the product rather than this number alone.
    max_instance_request_concurrency = var.max_concurrency

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      # `EXPECTED_AUDIENCE` and `ALLOWED_SUBS` are required at import, so a revision without them
      # does not start. They are supplied here, never read from .env in a container.
      env {
        name  = "EXPECTED_AUDIENCE"
        value = var.expected_audience
      }

      env {
        name  = "ALLOWED_SUBS"
        value = var.allowed_subs
      }

      # An env var is readable by anyone with run.viewer through `gcloud run revisions describe`.
      # The key is read from Secret Manager with the runtime account's one grant.
      env {
        name = "PROVIDER_API_KEY"

        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.provider_key.secret_id
            version = google_secret_manager_secret_version.provider_key.version
          }
        }
      }

      # `/readyz` gates traffic: it reads the configuration cached at import and never calls the
      # provider. `/livez` is the liveness answer: the process and its event loop, no dependency.
      # 60 seconds, not 6. The app answers /readyz in ~3s on a developer machine with a warm page
      # cache; on a revision whose image import took 5.8s the same startup exceeded 6 seconds and the
      # instance was killed before uvicorn printed its first line — ERROR_CONNECTION_FAILED, no
      # application logs, and a deploy that looks like a broken image rather than a slow one.
      #
      # This is the sibling root's working budget, 12 x 5s, kept as-is: a startup probe is the place to
      # be generous, because the cost of being wrong is a failed deploy and the cost of extra seconds
      # is seconds.
      startup_probe {
        http_get {
          path = "/readyz"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        timeout_seconds       = 3
        failure_threshold     = 12
      }

      liveness_probe {
        http_get {
          path = "/livez"
          port = 8080
        }
        period_seconds    = 10
        timeout_seconds   = 1
        failure_threshold = 3
      }

      readiness_probe {
        http_get {
          path = "/readyz"
          port = 8080
        }
        period_seconds    = 5
        timeout_seconds   = 1
        failure_threshold = 3
        success_threshold = 1
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.runtime_accessor]
}

# Public invoker, matching the rest of the project, with the app as the gate. The allowlist is what
# bounds who may spend; IAM here only decides whether the request can reach the gate at all.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.service.name
  location = google_cloud_run_v2_service.service.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  description = "The service address, read by `make smoke`."
  value       = google_cloud_run_v2_service.service.uri
}

output "service_account" {
  description = "The runtime identity, so the one-grant claim is checkable."
  value       = google_service_account.runtime.email
}

output "secret" {
  description = "The Secret Manager secret the revision reads."
  value       = google_secret_manager_secret.provider_key.secret_id
}

output "secret_version" {
  description = "The secret version the revision is pinned to."
  value       = google_secret_manager_secret_version.provider_key.version
}
