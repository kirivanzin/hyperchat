#!/bin/sh
# Assert the per-caller rate limit in both directions.
#
#   scripts/limits.sh URL LIMIT
#
# `make limits LIMIT=3` sends LIMIT+2 requests to `/api/chat` as one caller. The first LIMIT must be
# 200 and the last two must be 429 carrying a `Retry-After` in whole seconds.
#
# The second direction is the one that matters: run against a service configured to allow more, the
# fourth request is a 200 and this exits 1 saying so. A limiter that never fires passes a check
# written only the first way round, which is how this kind of bound ends up tested and absent.
#
# Each allowed request is a provider call, so LIMIT is small on purpose. The token comes from
# scripts/token.sh unless TOKEN is set, which is what lets the test run it against a fake server.
set -eu

url=${1:-}
limit=${2:-}
if [ -z "$url" ] || [ -z "$limit" ]; then
	echo "usage: scripts/limits.sh URL LIMIT" >&2
	exit 2
fi
case "$limit" in
'' | *[!0-9]*)
	echo "LIMIT must be a whole number of requests, not '$limit'" >&2
	exit 2
	;;
esac

token=${TOKEN:-$("$(dirname "$0")/token.sh")}
body='{"message":"Reply with the single word OK."}'

allowed=0
refused=0
i=1
while [ "$i" -le $((limit + 2)) ]; do
	headers=$(mktemp)
	trap 'rm -f "$headers"' EXIT
	code=$(curl -sS --max-time 300 -o /dev/null -D "$headers" -w '%{http_code}' \
		-H "Authorization: Bearer $token" \
		-H 'Content-Type: application/json' \
		-d "$body" \
		"$url/api/chat") || {
		echo "request $i: curl failed" >&2
		exit 1
	}
	retry_after=$(sed -n 's/^[Rr]etry-[Aa]fter:[[:space:]]*//p' "$headers" | tr -d '\r')
	rm -f "$headers"

	if [ "$i" -le "$limit" ]; then
		if [ "$code" != 200 ]; then
			echo "request $i: expected 200, got $code — the limit fired before LIMIT=$limit" >&2
			exit 1
		fi
		allowed=$((allowed + 1))
	else
		if [ "$code" != 429 ]; then
			echo "request $i: expected 429, got $code — the service allowed more than LIMIT=$limit." >&2
			echo "Is its RATE_LIMIT_PER_MINUTE higher than $limit? That is this check failing, not passing." >&2
			exit 1
		fi
		case "$retry_after" in
		'' | *[!0-9]*)
			echo "request $i: 429 without a Retry-After in whole seconds (got '$retry_after')" >&2
			exit 1
			;;
		esac
		refused=$((refused + 1))
	fi
	i=$((i + 1))
done

printf '  %s allowed, %s refused with Retry-After: %ss\n' "$allowed" "$refused" "$retry_after"
