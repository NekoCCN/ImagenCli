#!/usr/bin/env bash
# Build wheel and sdist, then pin their hashes for release review.
set -euo pipefail
cd "$(dirname "$0")/../.."
rm -rf dist
uv build
( cd dist && sha256sum ./* > SHA256SUMS )
cat dist/SHA256SUMS
