"""Local-only signing simulator for GUI and workflow tests.

The simulator never contacts Apple, never generates valid SHSH/APTicket data,
and marks every response as invalid for real restore.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SIMULATION_BANNER = "Simulation mode only. This does not contact Apple, does not generate valid SHSH/APTickets, and cannot authorize a real restore."
RESULT_STATUSES = ("approved", "rejected", "tethered_only", "expired", "network_error")
BLOCKED_EXPORT_SUFFIXES = (".shsh", ".shsh2", ".apticket", ".plist", ".bshsh2")
RESTORE_TOOL_NAMES = {"futurerestore", "idevicerestore", "palera1n", "turdus_merula", "turdusra1n", "cfgutil"}


class SimulationRequiredError(RuntimeError):
    pass


class MockTicketGuardrailError(RuntimeError):
    pass


@dataclass
class MockTSSStore:
    requests: dict[str, dict[str, Any]] = field(default_factory=dict)


STORE = MockTSSStore()


def require_simulation(enabled: bool) -> None:
    if not enabled:
        raise SimulationRequiredError("mock signing simulator requires --simulation or the GUI simulation toggle")


def normalize_status(status: str) -> str:
    key = status.strip().lower().replace(" ", "_").replace("-", "_")
    if key not in RESULT_STATUSES:
        raise ValueError(f"unsupported mock signing status: {status}")
    return key


def prepare_request(device: str, ecid: str, build: str, simulation: bool = False) -> dict[str, Any]:
    require_simulation(simulation)
    request_id = str(uuid.uuid4())
    payload = {
        "simulation": True,
        "request_id": request_id,
        "status": "prepared",
        "device": device or "Unknown",
        "ecid": ecid or "mock-ecid",
        "build": build or "mock-build",
        "ticket_type": "mock",
        "valid_for_real_restore": False,
        "notes": "UI test ticket only",
        "banner": SIMULATION_BANNER,
    }
    STORE.requests[request_id] = payload
    return payload


def decide_request(request_id: str, status: str, simulation: bool = False) -> dict[str, Any]:
    require_simulation(simulation)
    normalized = normalize_status(status)
    if request_id not in STORE.requests:
        raise KeyError(f"unknown mock request id: {request_id}")
    payload = dict(STORE.requests[request_id])
    payload["status"] = normalized
    payload["restore_allowed"] = normalized == "approved"
    payload["restore_allowed_in_simulation_only"] = normalized == "approved"
    payload["real_restore_allowed"] = False
    payload["simulated_flash_state"] = "allowed" if normalized == "approved" else "blocked"
    payload["valid_for_real_restore"] = False
    if normalized == "tethered_only":
        payload["warnings"] = ["Tethered-only warning for UI testing."]
    elif normalized == "network_error":
        payload["warnings"] = ["Simulated network error. No network request was made."]
    elif normalized == "rejected":
        payload["warnings"] = ["Simulated signing rejection."]
    elif normalized == "expired":
        payload["warnings"] = ["Simulated ticket expiration."]
    else:
        payload["warnings"] = []
    STORE.requests[request_id] = payload
    return payload


def simulated_restore_flash_plan(response: dict[str, Any], simulation: bool = False) -> dict[str, Any]:
    require_simulation(simulation)
    if not response.get("simulation") or response.get("ticket_type") != "mock":
        raise MockTicketGuardrailError("simulation restore requires a mock signing response")
    allowed = bool(response.get("restore_allowed_in_simulation_only")) and response.get("status") == "approved"
    return {
        "simulation": True,
        "valid_for_real_restore": False,
        "real_restore_allowed": False,
        "restore_allowed_in_simulation_only": allowed,
        "status": "restore_allowed_in_simulation_mode_only" if allowed else "restore_blocked",
        "device": response.get("device"),
        "ecid": response.get("ecid"),
        "build": response.get("build"),
        "ticket_type": "mock",
        "command": [],
        "command_preview": "No restore binary is executed in signing simulation mode.",
        "phases": [
            "device detected",
            "firmware selected",
            "mock signing request prepared",
            f"mock signing {response.get('status')}",
            "restore allowed in simulation mode only" if allowed else "restore blocked",
        ],
        "banner": SIMULATION_BANNER,
        "notes": "UI workflow simulation only. This cannot authorize or perform a real restore/flash.",
    }


def generate_mock_response(device: str, ecid: str, build: str, status: str, simulation: bool = False) -> dict[str, Any]:
    request = prepare_request(device, ecid, build, simulation=simulation)
    return decide_request(str(request["request_id"]), status, simulation=simulation)


def safe_mock_ticket_name(device: str, build: str) -> str:
    safe_device = re.sub(r"[^A-Za-z0-9_]+", "_", (device or "device").replace(",", "_")).strip("_") or "device"
    safe_build = re.sub(r"[^A-Za-z0-9_]+", "_", build or "build").strip("_") or "build"
    return f"mock_ticket_{safe_device}_{safe_build}.mock.json"


def ensure_mock_export_path(path: str | Path, device: str = "device", build: str = "build") -> Path:
    target = Path(path).expanduser()
    lower_name = target.name.lower()
    if any(lower_name.endswith(suffix) for suffix in BLOCKED_EXPORT_SUFFIXES):
        raise MockTicketGuardrailError("mock tickets cannot be exported with real restore artifact extensions")
    if not lower_name.endswith(".mock.json"):
        if target.suffix:
            target = target.with_name(target.name + ".mock.json")
        else:
            target = target / safe_mock_ticket_name(device, build) if target.exists() and target.is_dir() else target.with_suffix(".mock.json")
    if not target.name.lower().endswith(".mock.json"):
        raise MockTicketGuardrailError("mock ticket exports must end in .mock.json")
    return target


def save_mock_ticket(response: dict[str, Any], path: str | Path) -> Path:
    target = ensure_mock_export_path(path, str(response.get("device") or "device"), str(response.get("build") or "build"))
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **response,
        "simulation": True,
        "ticket_type": "mock",
        "valid_for_real_restore": False,
        "banner": SIMULATION_BANNER,
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def command_contains_mock_ticket(command: list[str]) -> bool:
    return any(str(part).lower().endswith(".mock.json") for part in command)


def validate_command_has_no_mock_ticket(command: list[str]) -> None:
    if command_contains_mock_ticket(command):
        tool = Path(str(command[0])).name if command else "backend"
        if tool in RESTORE_TOOL_NAMES or command:
            raise MockTicketGuardrailError("mock signing tickets cannot be passed to restore or jailbreak tooling")


class MockTSSHandler(BaseHTTPRequestHandler):
    server_version = "MockTSS/0.1"

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            body = self._read_json()
            simulation = bool(getattr(self.server, "simulation", False))
            if self.path == "/mock-tss/request":
                payload = prepare_request(str(body.get("device") or ""), str(body.get("ecid") or ""), str(body.get("build") or ""), simulation=simulation)
                self._write_json(200, payload)
                return
            if self.path == "/mock-tss/decision":
                payload = decide_request(str(body.get("request_id") or ""), str(body.get("status") or ""), simulation=simulation)
                self._write_json(200, payload)
                return
            self._write_json(404, {"simulation": True, "error": "not found", "valid_for_real_restore": False})
        except Exception as exc:
            self._write_json(400, {"simulation": True, "error": str(exc), "valid_for_real_restore": False, "banner": SIMULATION_BANNER})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        prefix = "/mock-tss/status/"
        if not self.path.startswith(prefix):
            self._write_json(404, {"simulation": True, "error": "not found", "valid_for_real_restore": False})
            return
        request_id = self.path[len(prefix) :]
        payload = STORE.requests.get(request_id)
        if not payload:
            self._write_json(404, {"simulation": True, "error": "unknown request id", "valid_for_real_restore": False, "banner": SIMULATION_BANNER})
            return
        self._write_json(200, payload)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def create_server(host: str = "127.0.0.1", port: int = 0, simulation: bool = False) -> ThreadingHTTPServer:
    require_simulation(simulation)
    server = ThreadingHTTPServer((host, port), MockTSSHandler)
    setattr(server, "simulation", True)
    return server


def start_server_in_thread(host: str = "127.0.0.1", port: int = 0, simulation: bool = False) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = create_server(host, port, simulation=simulation)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local-only mock signing simulator API")
    parser.add_argument("--simulation", action="store_true", required=True, help="Required. Enables local simulation mode.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port, simulation=args.simulation)
    host, port = server.server_address
    print(json.dumps({"simulation": True, "base_url": f"http://{host}:{port}", "banner": SIMULATION_BANNER}, sort_keys=True))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
