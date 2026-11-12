terraform {
  required_version = ">= 1.9"

  # State lives in the bucket the sibling project's bootstrap root owns, under a prefix of our own.
  # This root is where `PROJECT` comes from, so it is reachable rather than being a file on one
  # laptop. `terraform init -backend=false` in CI still works; nothing here needs a credential to
  # validate.
  # The bucket is passed by the Makefile as -backend-config, because a backend block cannot read a
  # variable and writing it here would put the project id in the repository. The prefix is ours.
  backend "gcs" {
    prefix = "hyperchat/registry"
  }

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
  description = "The GCP project. No default, on purpose: an identifier with a default is an identifier that gets committed."
  type        = string
}

variable "region" {
  description = "Where the repository lives. Changing it changes the image path, not the images."
  type        = string
  default     = "australia-southeast1"
}

variable "repository" {
  description = "The repository name. It appears in every image path, so renaming it orphans every tag."
  type        = string
  default     = "hyperchat"
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = var.repository
  format        = "DOCKER"
  description   = "Container images for hyperchat"

  # A tag that can be overwritten is not provenance: two images answer to the same name and the
  # only record of which one was pushed is a shell history. `make push` tags with the commit.
  docker_config {
    immutable_tags = true
  }
}

# No IAM resource, and that absence is the setting. The repository is reachable by the project's own
# identities and nobody else; granting a reader is a change with a reason attached, not a default.
# `make registry-status` prints the policy so the absence is observable rather than assumed.

output "project" {
  description = "Read by the Makefile, so the image path has one source of truth."
  value       = google_artifact_registry_repository.images.project
}

output "image_prefix" {
  description = "Where `make push` sends an image."
  value       = "${var.region}-docker.pkg.dev/${google_artifact_registry_repository.images.project}/${google_artifact_registry_repository.images.repository_id}"
}
