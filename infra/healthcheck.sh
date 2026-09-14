#!/usr/bin/env bash
set -euo pipefail
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health
