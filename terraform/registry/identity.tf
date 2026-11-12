# The identity a merge to `main` deploys as (#43).
#
# This is the one-time root, applied by a human with their own credentials — the same reason the
# project APIs and the registry repository live here. Nothing in CI can create or widen this identity:
# the workflow only ever *is* it, and the provider below refuses to issue it to any other repository
# or any other ref.
#
# Everything is expressed here rather than clicked or run through `gcloud`. The rule is not "the
# deployer is special"; it is that an identity with this reach is the last thing that should exist
# only in a shell history.

variable "runtime_account" {
  description = "The service account the revision runs as. Mirrors terraform/app's default; it is a name, not a credential."
  type        = string
  default     = "hyperchat-runtime"
}

variable "provider_secret" {
  description = "The Secret Manager secret holding PROVIDER_API_KEY. Mirrors terraform/app's default."
  type        = string
  default     = "provider-api-key"
}

# The workload identity pool is a project-level fact owned by the sibling project's bootstrap root.
# This root adds a provider *inside* it rather than a pool of its own: one pool per project is the
# shared fact, and one provider per repository is the per-repo child. Deleting their pool would take
# this provider with it, which is correct — the identifier would no longer mean anything.
data "google_iam_workload_identity_pool" "github" {
  project                   = var.project
  workload_identity_pool_id = "github"
}

resource "google_service_account" "deployer" {
  account_id   = "hyperchat-deployer"
  display_name = "hyperchat deploy, from a merge to main"
  description  = "Deploys hyperchat when main changes. Five scoped grants; no project-admin role."
}

# The condition is the whole security property: this provider will not issue a token to another
# repository, and not to this one on any ref but `main`. A pull request cannot deploy.
resource "google_iam_workload_identity_pool_provider" "github_hyperchat" {
  project                            = var.project
  workload_identity_pool_id          = data.google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-hyperchat"

  # 32 characters is the ceiling, which is why this reads like a label rather than a sentence.
  display_name = "hyperchat main only"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  attribute_condition = "assertion.repository == \"saxeum/hyperchat\" && assertion.ref == \"refs/heads/main\""
}

# The other half: the condition decides which workflows may ask, and this decides which account they
# get. Both are needed, and they are separate resources so that widening one is a visible change.
resource "google_service_account_iam_member" "deployer_workload_identity" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${data.google_iam_workload_identity_pool.github.name}/attribute.repository/saxeum/hyperchat"
}

# ── The five grants ─────────────────────────────────────────────────────────────────────────────────
#
# Each is scoped to the thing it acts on. There is deliberately no project-wide admin role: the project
# APIs moved to this root (#48) precisely so the deployer would not need one.

# The service, its revisions and its `allUsers` invoker binding are what an apply changes. This is the
# widest grant in the set, and it is bounded by the root it runs: terraform/app touches nothing else.
resource "google_project_iam_member" "deployer_run_admin" {
  project = var.project
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# `make push`. On the repository rather than the project, so it can push images and nothing else.
resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  project    = var.project
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

# A revision may run as the runtime account and no other. On that account, not on the project.
resource "google_service_account_iam_member" "deployer_act_as_runtime" {
  service_account_id = "projects/${var.project}/serviceAccounts/${var.runtime_account}@${var.project}.iam.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# The secret, its version and its one accessor binding are all in terraform/app. On the secret.
resource "google_secret_manager_secret_iam_member" "deployer_secret_admin" {
  project   = var.project
  secret_id = var.provider_secret
  role      = "roles/secretmanager.admin"
  member    = "serviceAccount:${google_service_account.deployer.email}"
}

# The state the deploy reads and locks. Scoped to this repository's prefix, so this account cannot
# read the sibling project's state that shares the bucket — which is the reason the grant is
# conditional rather than bucket-wide.
resource "google_storage_bucket_iam_member" "deployer_state" {
  bucket = "${var.project}-tfstate"
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.deployer.email}"

  # The second clause is not decoration: `storage.objects.list` is authorised against the *bucket*,
  # not against an object under the prefix, so a prefix-only condition fails at `terraform init` with
  # "does not have storage.objects.list access to the bucket" — which is how this grant was found to
  # be wrong the first time the workflow used it. Listing returns object names across the bucket;
  # reading or writing an object outside `hyperchat/` is still denied, because those calls are
  # authorised against the object.
  condition {
    title      = "hyperchat-prefix"
    expression = "resource.name.startsWith(\"projects/_/buckets/${var.project}-tfstate/objects/hyperchat/\") || resource.name == \"projects/_/buckets/${var.project}-tfstate\""
  }
}

output "deployer" {
  description = "The identity the deploy workflow authenticates as, so the claim is checkable."
  value       = google_service_account.deployer.email
}

output "deployer_provider" {
  description = "The full resource name a GitHub Actions `google-github-actions/auth` step needs."
  value       = google_iam_workload_identity_pool_provider.github_hyperchat.name
}
