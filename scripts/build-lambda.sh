#!/usr/bin/env bash
# Build the Lambda deployment package: the app and its dependencies for Python 3.14 on arm64,
# plus run.sh, the handler that the Lambda Web Adapter starts. Follows
# https://docs.astral.sh/uv/guides/integration/aws-lambda/
set -euo pipefail

# Lambda runs arm64. PLATFORM=x86_64-manylinux2014 builds a package that an x86 machine can
# run, which is how CI checks that the package starts.
platform=${PLATFORM:-aarch64-manylinux2014}

cd "$(dirname "$0")/.."
build=build
rm -rf "$build/package" "$build/lambda.zip"
mkdir -p "$build/package"

uv export --frozen --no-dev --no-editable --no-hashes --quiet -o "$build/requirements.txt"
uv pip install \
  --quiet \
  --no-installer-metadata \
  --no-compile-bytecode \
  --python-platform "$platform" \
  --python 3.14 \
  --target "$build/package" \
  -r "$build/requirements.txt"

cat > "$build/package/run.sh" <<'EOF'
#!/bin/bash
exec python -m boe_mcp http
EOF
chmod 755 "$build/package/run.sh"

# Fixed timestamps and sorted names make the same code produce the same zip, so Terraform only
# updates the function when the code changes. Python's zipfile keeps the build free of a zip
# binary and keeps run.sh executable.
python3 -I - "$build/package" "$build/lambda.zip" <<'EOF'
import sys
import zipfile
from pathlib import Path

source, target = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        info = zipfile.ZipInfo(path.relative_to(source).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = (0o100755 if path.name == "run.sh" else 0o100644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, path.read_bytes())
EOF
echo "Built $build/lambda.zip ($(du -h "$build/lambda.zip" | cut -f1))"
