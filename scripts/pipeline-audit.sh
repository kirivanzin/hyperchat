#!/bin/sh
# Read the live CI/CD pipeline and check it against what this repository says about itself.
#
# Every line is a claim taken from README.md or docs/repository-as-code.md, checked against the
# repository, the workflows, or the Terraform. It is not a dump of settings: a dump tells you what is
# there, and the question is whether what is there is what was promised.
#
# A claim that cannot be read is `unreadable`, never `ok`. Branch protection answers 404 to anyone
# without admin, so a read-only audit needs an owner's token, and a run without one must fail rather
# than pass quietly.
#
#   REPO=owner/name scripts/pipeline-audit.sh
#
# Exit 1 if any claim fails or cannot be read.
set -u

repo="${REPO:-}"
if [ -z "$repo" ]; then
	echo "usage: REPO=owner/name $0" >&2
	exit 2
fi

failed=0
pass() { printf '  %-30s ok    %s\n' "$1" "$2"; }
fail() { printf '  %-30s FAIL  %s\n' "$1" "$2"; failed=1; }
blind() { printf '  %-30s unreadable: %s\n' "$1" "$2"; failed=1; }

# claim <label> <api path> <jq filter> <what the repository says it is>
claim() {
	if got=$(gh api "$2" --jq "$3" 2>/dev/null); then
		if [ "$got" = "$4" ]; then pass "$1" "$got"; else fail "$1" "is '$got', the repository says '$4'"; fi
	else
		blind "$1" "$(gh api "$2" 2>&1 >/dev/null | tail -1)"
	fi
}

echo "== branch protection (docs/repository-as-code.md)"
claim "admins cannot bypass" "/repos/$repo/branches/main/protection" '.enforce_admins.enabled' 'true'
claim "no force-push" "/repos/$repo/branches/main/protection" '.allow_force_pushes.enabled' 'false'
claim "no deletion" "/repos/$repo/branches/main/protection" '.allow_deletions.enabled' 'false'
claim "required check is ci" "/repos/$repo/branches/main/protection" \
	'(.required_status_checks.contexts // []) | join(",")' 'ci'

echo "== repository settings (README.md)"
claim "secret scanning" "/repos/$repo" '.security_and_analysis.secret_scanning.status' 'enabled'
claim "push protection" "/repos/$repo" '.security_and_analysis.secret_scanning_push_protection.status' 'enabled'
claim "dependabot updates" "/repos/$repo" '.security_and_analysis.dependabot_security_updates.status' 'enabled'

echo "== workflows (README.md)"
# A required check that no run reports blocks every merge without saying why.
if reported=$(gh api "/repos/$repo/commits/main/check-runs" --jq '[.check_runs[].name] | unique | join(",")' 2>/dev/null); then
	case ",$reported," in
	*,ci,*) pass "ci reports on main" "$reported" ;;
	*) fail "ci reports on main" "it does not, and main requires it: $reported" ;;
	esac
else
	blind "ci reports on main" "could not read the check runs"
fi

# Every action pinned by commit SHA, not by tag.
unpinned=$(awk '
	/uses:/ {
		line = $0
		sub(/^.*uses:[[:space:]]*/, "", line)
		sub(/[[:space:]].*$/, "", line)
		if (line ~ /^\.\//) next
		sub(/.*@/, "", line)
		if (length(line) != 40 || line !~ /^[0-9a-f]+$/) print FILENAME ": " $0
	}' .github/workflows/*.yml)
if [ -z "$unpinned" ]; then pass "actions pinned by sha" "all of them"; else fail "actions pinned by sha" "$unpinned"; fi

# A workflow whose token can write is a workflow whose next modification can write.
missing_perms=""
for f in .github/workflows/*.yml; do
	grep -q '^permissions:' "$f" || missing_perms="$missing_perms $f"
done
if [ -z "$missing_perms" ]; then
	pass "permissions declared" "every workflow"
else
	fail "permissions declared" "no top-level permissions: in$missing_perms"
fi

# The guard must not be able to skip itself by reading an empty variable — that is how the
# negative-auth job was invisible on every pull request (F1 in the review, #58).
if grep -q 'head\.repo\.fork' .github/workflows/deploy.yml; then
	pass "fork skip is explicit" "deploy.yml names it"
else
	fail "fork skip is explicit" "deploy.yml does not name the fork condition"
fi
if git grep -q 'vars\.PROJECT != ' -- .github/workflows/deploy.yml 2>/dev/null; then
	fail "no silent skip by empty var" "the condition still reads a repository variable"
else
	pass "no silent skip by empty var" "the condition does not test vars"
fi

echo "== prose (README.md, docs/)"
# The sentences that were true until the change that made them false.
stale=$(git grep -n -E 'not required|there is no CI|no CI in this repository' -- README.md docs .github terraform 2>/dev/null)
if [ -z "$stale" ]; then pass "no stale pipeline claims" "none"; else fail "no stale pipeline claims" "$stale"; fi

echo "== drift (terraform/github)"
if [ -n "${GITHUB_TOKEN:-}" ] && [ -f terraform/github/terraform.tfstate ]; then
	GITHUB_TOKEN="$GITHUB_TOKEN" terraform -chdir=terraform/github plan -input=false \
		-detailed-exitcode -no-color >/tmp/pipeline-audit.tf 2>&1
	rc=$?
	case $rc in
	0) pass "terraform drift" "none" ;;
	2) fail "terraform drift" "the plan is not empty — /tmp/pipeline-audit.tf" ;;
	*) blind "terraform drift" "$(tail -1 /tmp/pipeline-audit.tf)" ;;
	esac
else
	blind "terraform drift" "needs GITHUB_TOKEN and terraform/github/terraform.tfstate in this checkout"
fi

exit "$failed"
