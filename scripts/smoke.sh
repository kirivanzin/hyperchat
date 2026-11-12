#!/bin/sh
# Assert the deployed service in both directions.
#
# A positive-only smoke test passes when auth is a no-op, so `/api/me` is checked without a token
# (401) and with a freshly minted one (200). The token is captured, never printed.
set -eu

url=${1:-}
if [ -z "$url" ]; then
	echo "usage: scripts/smoke.sh URL" >&2
	exit 2
fi

check() {
	expected=$1
	path=$2
	shift 2
	actual=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "$@" "$url$path") || {
		echo "  $path: curl failed" >&2
		exit 1
	}
	if [ "$actual" != "$expected" ]; then
		echo "  $path: expected $expected, got $actual" >&2
		exit 1
	fi
	printf '  %-12s %s\n' "$path" "$actual"
}

check 200 /
check 200 /livez
check 200 /readyz
check 401 /api/me

# `make smoke` sources .env first, so token.sh uses the same audience the service expects.
token=$("$(dirname "$0")/token.sh")
check 200 /api/me -H "Authorization: Bearer $token"
