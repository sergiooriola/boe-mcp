#!/usr/bin/env bash
# Start the built package the way Lambda does (run.sh from the package root) and check that it
# answers: a health check, a 401 that points to the OAuth metadata, and that metadata.
set -euo pipefail

cd "$(dirname "$0")/.."
python=$(uv python find 3.14)
dir=$(mktemp -d)
trap 'kill "${pid:-}" 2>/dev/null || true; rm -rf "$dir"' EXIT
"$python" -I -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' \
  build/lambda.zip "$dir"

port=18080
url="http://127.0.0.1:$port"
(
  cd "$dir"
  PATH="$(dirname "$python"):$PATH" PORT=$port \
    BOE_MCP_RESOURCE_URL="$url/mcp" \
    BOE_MCP_ISSUER_URL="https://cognito-idp.eu-south-2.amazonaws.com/eu-south-2_SMOKE" \
    BOE_MCP_CLIENT_IDS=smoke \
    BOE_MCP_REQUIRED_SCOPE="$url/mcp/read" \
    exec bash run.sh
) &
pid=$!

for _ in $(seq 1 40); do
  curl -fsS "$url/healthz" >/dev/null 2>&1 && break
  sleep 0.25
done

test "$(curl -fsS "$url/healthz")" = ok
status=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$url/mcp" -H 'content-type: application/json' -d '{}')
test "$status" = 401
curl -sS -D - -o /dev/null -X POST "$url/mcp" -H 'content-type: application/json' -d '{}' \
  | grep -qi "resource_metadata=\"$url/.well-known/oauth-protected-resource/mcp\""
curl -fsS "$url/.well-known/oauth-protected-resource/mcp" | grep -q '"authorization_servers"'
echo "Package starts and protects /mcp"
