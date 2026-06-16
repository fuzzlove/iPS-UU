"""Allow `python3 -m ips_uu` execution."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
