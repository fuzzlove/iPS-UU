#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 /path/to/arm64-tools /path/to/x86_64-tools" >&2
  exit 64
fi

arm_dir="$1"
x86_dir="$2"
required_tools=(
  idevicerestore
  ideviceinstaller
  idevice_id
  ideviceinfo
  idevicepair
  idevicediagnostics
  ideviceenterrecovery
  irecovery
  idevicescreenshot
)

mkdir -p tools

for name in "${required_tools[@]}"; do
  arm_tool="$arm_dir/$name"
  x86_tool="$x86_dir/$name"
  target="tools/$name"
  if [[ ! -f "$arm_tool" ]]; then
    echo "missing arm64 input: $arm_tool" >&2
    exit 1
  fi
  if [[ ! -f "$x86_tool" ]]; then
    echo "missing x86_64 input: $x86_tool" >&2
    exit 1
  fi
  lipo -create "$arm_tool" "$x86_tool" -output "$target"
  chmod 0755 "$target"
  echo "staged universal2 $target"
done

python3 scripts/audit_tool_architectures.py
