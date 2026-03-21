#!/usr/bin/env bash
set -euo pipefail

python -m PyInstaller \
  --noconfirm \
  --clean \
  --name VPSDASH \
  --windowed \
  --add-data "templates:templates" \
  --add-data "data:data" \
  --add-data "static:static" \
  run.py
