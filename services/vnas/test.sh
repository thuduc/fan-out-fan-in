#!/bin/sh
set -euo pipefail

LC_ALL=C awk 'BEGIN { srand(); printf "%.2f\n", 50 + rand() * 50 }'
