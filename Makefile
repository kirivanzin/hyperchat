# Every operation this repository supports, in the order it happens:
#   github -> registry -> login -> build -> push.  `make help` lists them.
#
# The image tag is the commit, not "dev", and the registry refuses to overwrite a tag
# (docker_config.immutable_tags in terraform/registry). Two things follow, both intended: pushing the
# same commit twice fails, and any digest in the registry can be traced back to the source that
# produced it. Re-pushing a commit is `make push TAG=<sha>-2`.

IMAGE      ?= hyperchat
TAG        ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
REGION     ?= australia-southeast1
REPOSITORY ?= hyperchat
PORT       ?= 8080

TERRAFORM = terraform -chdir=terraform/registry
TERRAFORM_GITHUB = terraform -chdir=terraform/github
BRANCH ?= main
# The project is read from the applied registry root rather than repeated here. Two places holding
# the same identifier is how an image ends up in a registry that nothing reads.
PROJECT ?= $(shell $(TERRAFORM) output -raw project 2>/dev/null)
# And the repository comes from the applied GitHub root, for the same reason.
FULL_NAME ?= $(shell $(TERRAFORM_GITHUB) output -raw full_name 2>/dev/null)

REGISTRY = $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPOSITORY)
LOCAL    = $(IMAGE):$(TAG)
REMOTE   = $(REGISTRY)/$(IMAGE):$(TAG)

.DEFAULT_GOAL := help

.PHONY: help info test build run stop github github-plan github-create github-remote \
        github-apply github-verify registry-plan registry-apply login push registry-verify

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

info: ## Print what the next command will use, before it uses it
	@printf '  project     %s\n' "$(if $(PROJECT),$(PROJECT),<unset: run make registry-apply>)"
	@printf '  region      %s\n' "$(REGION)"
	@printf '  repository  %s\n' "$(REPOSITORY)"
	@printf '  tag         %s\n' "$(TAG)"
	@printf '  push to     %s\n' "$(if $(PROJECT),$(REMOTE),<unset>)"

test: ## Run the tests
	uv run pytest -q

build: ## Build the image for this commit, pulling the base image
	docker build --pull -t $(LOCAL) .

run: ## Run the image on PORT, in the foreground
	docker run --rm --name $(IMAGE) -p $(PORT):8080 $(LOCAL)

stop: ## Stop the running container
	@docker stop $(IMAGE) 2>/dev/null || true

github: ## Create the repository, point origin at it, push, then protect the branch
	@[ -n "$$GITHUB_TOKEN" ] || { \
		echo "GITHUB_TOKEN is unset: the GitHub provider authenticates with it." >&2; \
		echo "see docs/repository-as-code.md for what that token can do" >&2; \
		exit 1; }
	@[ -z "$$(git status --porcelain)" ] || { \
		echo "uncommitted changes: nothing publishes them, and they are easy to lose" >&2; \
		echo "commit them first, then run this again" >&2; \
		git status --short; \
		exit 1; }
	@echo "== repository"; $(MAKE) --no-print-directory github-create
	@echo "== origin"; $(MAKE) --no-print-directory github-remote
	@echo "== first push"; git push -u origin $(BRANCH)
	@echo "== protection"; $(MAKE) --no-print-directory github-apply
	@echo "== published: $$($(TERRAFORM_GITHUB) output -raw html_url)"

github-plan: ## Show what the GitHub root creates, without creating it
	$(TERRAFORM_GITHUB) init -input=false
	$(TERRAFORM_GITHUB) plan -input=false

github-create: ## Create the repository, so there is somewhere to push
	$(TERRAFORM_GITHUB) init -input=false
	$(TERRAFORM_GITHUB) apply -input=false -target=github_repository.repo

github-remote: ## Point origin at the repository this root created
	@[ -n "$(FULL_NAME)" ] || { \
		echo "FULL_NAME is unset: no applied state in terraform/github." >&2; \
		echo "run 'make github-create' once, or pass FULL_NAME=owner/name" >&2; \
		exit 1; }
	@git remote get-url origin >/dev/null 2>&1 \
		&& git remote set-url origin "$$($(TERRAFORM_GITHUB) output -raw ssh_clone_url)" \
		|| git remote add origin "$$($(TERRAFORM_GITHUB) output -raw ssh_clone_url)"

github-apply: ## Apply the rest, so branch protection lands once the branch exists
	$(TERRAFORM_GITHUB) init -input=false
	$(TERRAFORM_GITHUB) apply -input=false

github-verify: ## Show the repository settings and the protection that exist
	@[ -n "$(FULL_NAME)" ] || { \
		echo "FULL_NAME is unset: no applied state in terraform/github." >&2; \
		echo "run 'make github-create' once, or pass FULL_NAME=owner/name" >&2; \
		exit 1; }
	@gh api /repos/$(FULL_NAME) >/dev/null && echo "repository $(FULL_NAME) exists"
	@gh api /repos/$(FULL_NAME) \
		--jq '{visibility, security: (.security_and_analysis | map_values(.status))}'
	@gh api /repos/$(FULL_NAME)/branches/$(BRANCH)/protection \
		--jq '{admins: .enforce_admins.enabled, force_push: .allow_force_pushes.enabled, deletion: .allow_deletions.enabled, reviews: .required_pull_request_reviews, checks: .required_status_checks}'

registry-plan: ## Show what the registry root creates, without creating it
	$(TERRAFORM) init -input=false
	$(TERRAFORM) plan -input=false

registry-apply: ## Create the registry. Once, by the human, before the first push
	$(TERRAFORM) init -input=false
	$(TERRAFORM) apply -input=false

login: ## Authorise docker for the registry host, as your own gcloud identity
	@gcloud auth configure-docker $(REGION)-docker.pkg.dev --quiet

push: ## Tag and push the image for this commit
	@[ -n "$(PROJECT)" ] || { \
		echo "PROJECT is unset: there is no applied state in terraform/registry." >&2; \
		echo "run 'make registry-apply' once, or pass PROJECT=<id>" >&2; \
		exit 1; }
	docker tag $(LOCAL) $(REMOTE)
	docker push $(REMOTE)

registry-verify: ## Show the registry that exists, and the policy that guards it
	@[ -n "$(PROJECT)" ] || { \
		echo "PROJECT is unset: there is no applied state in terraform/registry." >&2; \
		echo "run 'make registry-apply' once, or pass PROJECT=<id>" >&2; \
		exit 1; }
	@gcloud artifacts repositories describe $(REPOSITORY) \
		--location=$(REGION) --project=$(PROJECT) \
		--format='table(name.basename(),format,dockerConfig.immutableTags)'
	@echo "== iam policy: no bindings is the intended answer"
	@gcloud artifacts repositories get-iam-policy $(REPOSITORY) \
		--location=$(REGION) --project=$(PROJECT) \
		--format='value(bindings.members)'
