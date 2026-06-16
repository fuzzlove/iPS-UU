#!/usr/bin/env python3
"""Compatibility launcher for the iPS-UU planner."""

from ips_uu.planner import main


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
