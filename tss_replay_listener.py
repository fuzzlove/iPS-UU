#!/usr/bin/env python3
"""Compatibility launcher for the iPS-UU replay-only TSS listener."""

from ips_uu.tss_replay import main


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
