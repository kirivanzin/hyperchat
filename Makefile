# Every operation this repository supports, in the order it happens:
#   registry -> login -> build -> push.  `make help` lists them.
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
# The project is read from the applied registry root rather than repeated here. Two places holding
# the same identifier is how an image ends up in a registry that nothing reads.
PROJECT ?= $(shell $(TERRAFORM) output -raw project 2>/dev/null)

REGISTRY = $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPOSITORY)
LOCAL    = $(IMAGE):$(TAG)
REMOTE   = $(REGISTRY)/$(IMAGE):$(TAG)

.DEFAULT_GOAL := help

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
		echo "PROJECT is unset: there is no applied state in terraform/registry."; \
		echo "run 'make registry-apply' once, or pass PROJECT=<id>"; \
		exit 1; }
	docker tag $(LOCAL) $(REMOTE)
	docker push $(REMOTE)

registry-verify: ## Show the registry that exists, and the policy that guards it
	@[ -n "$(PROJECT)" ] || { \
		echo "PROJECT is unset: there is no applied state in terraform/registry."; \
		echo "run 'make registry-apply' once, or pass PROJECT=<id>"; \
		exit 1; }
	@gcloud artifacts repositories describe $(REPOSITORY) \
		--location=$(REGION) --project=$(PROJECT) \
		--format='table(name.basename(),format,dockerConfig.immutableTags)'
	@echo "== iam policy: no bindings is the intended answer"
	@gcloud artifacts repositories get-iam-policy $(REPOSITORY) \
		--location=$(REGION) --project=$(PROJECT) \
		--format='value(bindings.members)'
