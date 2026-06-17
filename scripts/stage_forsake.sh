#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/forsake-or-forsake-source-dir" >&2
  exit 2
fi

source_path="$1"
if [[ ! -e "$source_path" ]]; then
  echo "Forsake source path does not exist: $source_path" >&2
  exit 1
fi

mkdir -p tools

if [[ -d "$source_path" ]]; then
  rm -rf tools/Forsake
  cp -R "$source_path" tools/Forsake
  if [[ -f tools/Forsake/forsake ]]; then
    chmod 0755 tools/Forsake/forsake
  fi
  if [[ -f tools/Forsake/forsake.py ]]; then
    chmod 0755 tools/Forsake/forsake.py
  fi
  if [[ -f tools/Forsake/forsake.sh ]]; then
    chmod 0755 tools/Forsake/forsake.sh
  fi
  echo "Staged Forsake directory at tools/Forsake"
else
  install -m 0755 "$source_path" tools/forsake
  echo "Staged Forsake executable at tools/forsake"
fi
