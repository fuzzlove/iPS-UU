#!/usr/bin/env python3
"""
Replay-only local AuthInstall/TSS listener.

This server does not generate signatures, APTickets, SHSH blobs, or any other
cryptographic material. It is only useful for capturing AuthInstall requests and
replaying a response fixture you already have for the exact same device, nonce,
build, and restore identity.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import plistlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .banner import print_intro


class ReplayConfig:
    def __init__(self, capture_dir: Path, response_file: Path | None, status: int) -> None:
        self.capture_dir = capture_dir
        self.response_file = response_file
        self.status = status


def parse_plist_payload(body: bytes) -> Any | None:
    try:
        return plistlib.loads(body)
    except Exception:
        return None


def error_response(message: str) -> bytes:
    return plistlib.dumps(
        {
            "MESSAGE": message,
            "STATUS": 94,
            "TSS-Error": message,
        },
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )


def load_response(path: Path | None) -> tuple[bytes, str]:
    if path is None:
        return error_response("No replay fixture configured"), "application/x-apple-plist"
    data = path.read_bytes()
    if path.suffix.lower() in {".plist", ".xml"}:
        return data, "application/x-apple-plist"
    return data, "application/octet-stream"


class TSSReplayHandler(BaseHTTPRequestHandler):
    server_version = "TSSReplay/1.0"

    @property
    def config(self) -> ReplayConfig:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        if urlparse(self.path).path in {"/", "/health"}:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "mode": "replay-only"}).encode("utf-8"))
            return
        self.send_error(404, "not found")

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(content_length)
        request_id = self.capture_request(body)
        response, content_type = load_response(self.config.response_file)
        self.send_response(self.config.status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response)))
        self.send_header("X-Replay-Request-ID", request_id)
        self.end_headers()
        self.wfile.write(response)

    def capture_request(self, body: bytes) -> str:
        self.config.capture_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(body).hexdigest()
        timestamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        request_id = f"{timestamp}-{digest[:16]}"
        base = self.config.capture_dir / request_id

        (base.with_suffix(".body")).write_bytes(body)
        metadata = {
            "request_id": request_id,
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers.items()),
            "sha256": digest,
            "body_length": len(body),
        }
        parsed = parse_plist_payload(body)
        if parsed is not None:
            metadata["plist_summary"] = summarize_plist(parsed)
            with base.with_suffix(".plist").open("wb") as f:
                plistlib.dump(parsed, f, sort_keys=True)
        base.with_suffix(".json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        return request_id


def summarize_plist(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    interesting_keys = [
        "ECID",
        "ApECID",
        "ApNonce",
        "ApBoardID",
        "ApChipID",
        "ApSecurityDomain",
        "UniqueBuildID",
        "@ApImg4Ticket",
        "BbSNUM",
        "BbChipID",
        "BbGoldCertId",
    ]
    summary: dict[str, Any] = {"key_count": len(value), "keys": sorted(map(str, value.keys()))}
    for key in interesting_keys:
        if key in value:
            item = value[key]
            if isinstance(item, bytes):
                summary[key] = item.hex()
            else:
                summary[key] = item
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay a provided AuthInstall/TSS response fixture and capture requests.")
    parser.add_argument("--host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    parser.add_argument("--capture-dir", default="tss_captures", help="Directory for captured requests")
    parser.add_argument("--response", help="Response fixture to replay for every POST")
    parser.add_argument("--status", type=int, default=200, help="HTTP status for replay responses")
    return parser


def main(argv: list[str] | None = None, show_intro: bool = False) -> int:
    if show_intro:
        print_intro()
    args = build_parser().parse_args(argv)
    config = ReplayConfig(
        capture_dir=Path(args.capture_dir),
        response_file=Path(args.response) if args.response else None,
        status=args.status,
    )
    server = ThreadingHTTPServer((args.host, args.port), TSSReplayHandler)
    server.config = config  # type: ignore[attr-defined]
    print(f"Listening on http://{args.host}:{args.port} (replay-only)")
    if config.response_file:
        print(f"Replaying response fixture: {config.response_file}")
    else:
        print("No response fixture configured; POSTs will receive an error plist.")
    print(f"Capturing requests in: {config.capture_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping listener")
        return 0


if __name__ == "__main__":
    raise SystemExit(main(show_intro=True))
