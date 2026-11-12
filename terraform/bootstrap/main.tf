terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = var.project
}

variable "project" {
  description = "The GCP project. No default, on purpose: the project id is not in this repository."
  type        = string
}

# Project-level facts with `disable_on_destroy = false`, kept out of `terraform/app` so that root only
# touches its own service. Apply this once per project, before the first deploy.
resource "google_project_service" "secretmanager" {
  project            = var.project
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "run" {
  project            = var.project
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

output "project" {
  description = "The project whose APIs this root enabled."
  value       = var.project
}
