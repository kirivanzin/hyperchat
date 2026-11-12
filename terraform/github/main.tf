terraform {
  required_version = ">= 1.9"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
}

# Authenticates from GITHUB_TOKEN in the environment: a token that can create the repository and
# change its settings. docs/repository-as-code.md records what that costs and why it is not in CI.
provider "github" {
  owner = var.owner
}

variable "owner" {
  description = "The account that owns the repository. In terraform.tfvars, which is git-ignored, for the same reason as the project id: one identifier, one home."
  type        = string
}

variable "name" {
  description = "The repository name."
  type        = string
  default     = "hyperchat"
}

variable "default_branch" {
  description = "The branch protection attaches to. It has to exist before this root is applied in full; see docs/repository-as-code.md."
  type        = string
  default     = "main"
}

resource "github_repository" "repo" {
  name        = var.name
  description = "A containerised hello-world, and the private registry that holds its image"
  visibility  = "public"
  has_issues  = true
  has_wiki    = false
  auto_init   = false

  # `auto_init = false` on purpose: the history in this working directory is the history of the
  # repository, and an initial README commit would have to be reconciled with it before the first
  # push — which is a merge in a repository whose whole point is that it has one clean root.

  topics = ["fastapi", "docker", "terraform", "google-cloud", "artifact-registry"]

  # Both free on a public repository, and both are the mechanism that makes "no secret has ever
  # been near this" a property rather than an intention.
  security_and_analysis {
    secret_scanning {
      status = "enabled"
    }
    secret_scanning_push_protection {
      status = "enabled"
    }
  }
}

resource "github_branch_protection" "default" {
  repository_id = github_repository.repo.node_id
  pattern       = var.default_branch

  enforce_admins      = true
  allows_force_pushes = false
  allows_deletions    = false

  # Nothing else, and each absence is a decision taken elsewhere and recorded here:
  #
  # - no required_pull_request_reviews: one account cannot approve its own pull request, and a
  #   required approval that nobody can give blocks every merge.
  # - no required_status_checks: there is no CI in this repository, and a required check that never
  #   reports blocks every merge in the same way.
  #
  # `enforce_admins = true` is what makes the other two real rather than advisory for the only
  # account that exists.
}

output "full_name" {
  description = "owner/name, read by the Makefile so the remote and the API calls agree."
  value       = github_repository.repo.full_name
}

output "ssh_clone_url" {
  description = "What `make github-remote` points origin at."
  value       = github_repository.repo.ssh_clone_url
}

output "html_url" {
  description = "The address, once there is a branch to look at."
  value       = github_repository.repo.html_url
}
