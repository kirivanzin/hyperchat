#!/bin/sh
# Print a Google ID token for the audience this service expects.
#
# Two facts this rests on, both checkable rather than assumed:
#
#   - `gcloud auth print-identity-token` with *user* credentials mints a token whose audience is the
#     gcloud SDK client id, 32555940559.apps.googleusercontent.com. It needs no project and no
#     billing: a Google account is free, and a GCP project is what wants a card.
#   - The token is a credential valid for about an hour. It is printed and never written to disk, so
#     it ends up in a shell or in `$(...)`, which is the smallest surface this can have.
#
# The audience is read back out of the token before it is handed over. That is not paranoia: if the
# login is a service account, or the client id has been changed in .env, the service answers 401 and
# the reason is a claim, not a key. Failing here names which.
set -eu

expected="${EXPECTED_AUDIENCE:-32555940559.apps.googleusercontent.com}"

if ! command -v gcloud >/dev/null 2>&1; then
	echo "gcloud is not installed. This target needs the CLI and a user login:" >&2
	echo "  gcloud auth login" >&2
	exit 1
fi

token=$(gcloud auth print-identity-token --quiet)

actual=$(
	printf '%s' "$token" | cut -d. -f2 | python3 -c '
import base64, json, sys
segment = sys.stdin.read().strip()
segment += "=" * (-len(segment) % 4)
print(json.loads(base64.urlsafe_b64decode(segment)).get("aud", ""))
'
)

if [ "$actual" != "$expected" ]; then
	echo "the token is for '$actual', not '$expected'." >&2
	echo "Log in as a user rather than a service account, or set EXPECTED_AUDIENCE in .env." >&2
	exit 1
fi

printf '%s\n' "$token"
