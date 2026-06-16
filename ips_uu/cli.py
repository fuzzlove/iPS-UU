"""Top-level iPS-UU command dispatcher."""

from __future__ import annotations

import argparse
import sys

from . import PROJECT_NAME
from . import airswitch
from . import atlascore
from . import dcsd
from . import faketunes
from . import frameworks
from . import installcoordination
from . import planner
from . import purplefat
from . import purple_rabbit
from . import restorectl
from . import tss_replay
from .banner import print_intro


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{PROJECT_NAME} IPSW utility framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  Online full restore dry-run:
    python3 -m ips_uu planner restore ./firmware.ipsw --product-type iPhone10,6 --install-mode erase

  Online full restore execution, Apple-signed only:
    python3 -m ips_uu planner restore ./firmware.ipsw --product-type iPhone10,6 --install-mode erase --execute --confirm-erase

  Offline full restore feasibility check, no signing bypass:
    python3 -m ips_uu planner offline-check ./firmware.ipsw --product-type iPhone10,6 --install-mode erase --offline-mode
""",
    )
    parser.add_argument(
        "tool",
        choices=(
            "planner",
            "listener",
            "rabbit",
            "airswitch",
            "dcsd",
            "frameworks",
            "installcoordination",
            "sniff",
            "atlascore",
            "purplefat",
            "faketunes",
            "restorectl",
            "restore-research",
            "gui",
        ),
        help="Tool to run",
    )
    parser.add_argument("tool_args", nargs=argparse.REMAINDER, help="Arguments passed to the selected tool")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in {
        "planner",
        "listener",
        "rabbit",
        "airswitch",
        "dcsd",
        "frameworks",
        "installcoordination",
        "sniff",
        "atlascore",
        "purplefat",
        "faketunes",
        "restorectl",
        "restore-research",
        "gui",
    }:
        tool = argv[0]
        tool_args = argv[1:]
    else:
        args = build_parser().parse_args(argv)
        tool = args.tool
        tool_args = args.tool_args
    print_intro()
    if tool == "planner":
        return planner.main(tool_args)
    if tool == "listener":
        return tss_replay.main(tool_args)
    if tool == "rabbit":
        return purple_rabbit.main(tool_args)
    if tool == "airswitch":
        return airswitch.main(tool_args)
    if tool == "dcsd":
        return dcsd.main(tool_args)
    if tool == "frameworks":
        return frameworks.main(tool_args)
    if tool == "installcoordination":
        return installcoordination.main(tool_args)
    if tool == "sniff":
        from . import sniff

        return sniff.main(tool_args)
    if tool == "atlascore":
        return atlascore.main(tool_args)
    if tool == "purplefat":
        return purplefat.main(tool_args)
    if tool == "faketunes":
        return faketunes.main(tool_args)
    if tool == "restorectl":
        return restorectl.main(tool_args)
    if tool == "restore-research":
        from . import restore_research

        return restore_research.main(tool_args)
    if tool == "gui":
        from .gui import app

        return app.main(tool_args)
    raise RuntimeError(f"Unhandled tool: {tool}")


if __name__ == "__main__":
    raise SystemExit(main())
