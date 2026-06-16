"""Shared terminal intro for iPS-UU commands."""

ASCII_INTRO = r"""
 _ ____  ____        _   _ _   _
(_)  _ \/ ___|      | | | | | | |
| | |_) \___ \ _____| | | | | | |
| |  __/ ___) |_____| |_| | |_| |
|_|_|   |____/       \___/ \___/
              iPS-UU
"""


def print_intro() -> None:
    print(ASCII_INTRO.strip("\n"))
