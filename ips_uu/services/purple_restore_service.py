"""Local-only Purple Restore / Tatsu workflow emulator.

This module models internal restore UI states without impersonating Apple
services, generating valid restore tickets, changing device trust, or invoking
restore tools.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ips_uu.services.ipsw_service import compatibility_summary, parse_ipsw
from ips_uu.services.mock_tss_service import MockTicketGuardrailError, SimulationRequiredError, validate_command_has_no_mock_ticket


PURPLE_WARNING = "Warning. This is to only be used by apple employees for internal use."
PURPLE_SIMULATION_BANNER = (
    f"{PURPLE_WARNING} Simulation mode only. This mock Tatsu workflow does not contact Apple, "
    "does not generate valid SHSH/APTicket data, does not alter device trust, and cannot authorize a real restore."
)
PURPLE_STATES = (
    "Normal Mode",
    "Recovery Mode",
    "DFU Mode",
    "Purple Restore Prepared",
    "Ticket Requested",
    "Ticket Approved",
    "Restore Proceeding",
    "Restore Failed",
    "Restore Complete",
)
INTERNAL_CLIENT_TOKEN = "apple-connect-purple-restore-simulator"


class PurpleRestoreGuardrailError(RuntimeError):
    pass


@dataclass
class MockTatsuStore:
    requests: dict[str, dict[str, Any]] = field(default_factory=dict)


TATSU_STORE = MockTatsuStore()


def require_internal_simulation(enabled: bool) -> None:
    if not enabled:
        raise SimulationRequiredError("Purple Restore emulator requires explicit simulation mode")


def normalize_mode(mode: str | None) -> str:
    value = (mode or "normal").strip().lower()
    if value == "recovery":
        return "Recovery Mode"
    if value == "dfu":
        return "DFU Mode"
    return "Normal Mode"


def _device_product_type(device: dict[str, Any] | None, override: str | None = None) -> str:
    return str(override or (device or {}).get("product_type") or (device or {}).get("ProductType") or "").strip()


def build_purple_restore_session(
    device: dict[str, Any] | None,
    ipsw_path: str | None,
    simulation: bool = False,
    product_type_override: str | None = None,
    mode_override: str | None = None,
) -> dict[str, Any]:
    require_internal_simulation(simulation)
    product_type = _device_product_type(device, product_type_override)
    mode = normalize_mode(mode_override or str((device or {}).get("current_mode") or (device or {}).get("mode") or "normal"))
    ipsw: dict[str, Any] | None = None
    compatibility = {"status": "missing_ipsw", "message": "Select an IPSW to check device compatibility."}
    guardrails: list[str] = [
        "No real restore command is built or executed.",
        "Mock artifacts are not valid SHSH/APTicket data.",
        "Device trust decisions are not modified.",
        "Cross-device IPSW use is blocked before simulation can proceed.",
    ]
    blocked = False
    if ipsw_path:
        ipsw = parse_ipsw(ipsw_path, product_type or None)
        compatibility = compatibility_summary({"product_type": product_type} if product_type else device, ipsw)
        blocked = compatibility.get("status") == "incompatible"
    elif not product_type:
        blocked = True
        compatibility = {"status": "unknown_device", "message": "Enter or detect a ProductType before preparing a Purple Restore simulation."}

    state = mode if mode in {"Normal Mode", "Recovery Mode", "DFU Mode"} else "Normal Mode"
    if not blocked and ipsw_path:
        state = "Purple Restore Prepared"

    session = {
        "simulation": True,
        "internal_testing_only": True,
        "warning": PURPLE_WARNING,
        "banner": PURPLE_SIMULATION_BANNER,
        "session_id": str(uuid.uuid4()),
        "state": "Restore Failed" if blocked else state,
        "state_machine": list(PURPLE_STATES),
        "device": {
            "product_type": product_type or "Unknown",
            "mode": mode,
            "ecid": (device or {}).get("ecid") or (device or {}).get("ECID") or "mock-ecid",
        },
        "ipsw": ipsw,
        "compatibility": compatibility,
        "blocked": blocked,
        "restore_allowed": False,
        "restore_allowed_in_simulation_only": False,
        "valid_for_real_restore": False,
        "command": [],
        "command_preview": "No restore binary is executed by the Purple Restore emulator.",
        "guardrails": guardrails,
        "events": [mode],
    }
    if blocked:
        session["events"].append("Restore Failed")
        session["failure_reason"] = compatibility.get("message") or "IPSW/device compatibility check failed."
    elif ipsw_path:
        session["events"].append("Purple Restore Prepared")
    return session


def request_mock_tatsu_ticket(session: dict[str, Any], simulation: bool = False) -> dict[str, Any]:
    require_internal_simulation(simulation)
    if session.get("blocked"):
        raise PurpleRestoreGuardrailError(str(session.get("failure_reason") or "Purple Restore simulation is blocked"))
    if session.get("state") not in {"Purple Restore Prepared", "Ticket Requested", "Ticket Approved"}:
        raise PurpleRestoreGuardrailError("prepare Purple Restore before requesting a mock Tatsu ticket")
    request_id = str(uuid.uuid4())
    ticket = {
        "simulation": True,
        "internal_testing_only": True,
        "request_id": request_id,
        "status": "approved",
        "ticket_type": "purple_mock",
        "valid_for_real_restore": False,
        "restore_allowed": False,
        "restore_allowed_in_simulation_only": True,
        "device": session.get("device", {}).get("product_type"),
        "ecid": session.get("device", {}).get("ecid"),
        "build": (session.get("ipsw") or {}).get("product_build_version") or "mock-build",
        "artifact_name": safe_purple_mock_artifact_name(session),
        "warning": PURPLE_WARNING,
        "banner": PURPLE_SIMULATION_BANNER,
        "notes": "Mock Tatsu response for internal UI testing only.",
    }
    TATSU_STORE.requests[request_id] = ticket
    updated = dict(session)
    updated["state"] = "Ticket Approved"
    updated["mock_tatsu_ticket"] = ticket
    updated["restore_allowed_in_simulation_only"] = True
    updated["events"] = [*(session.get("events") or []), "Ticket Requested", "Ticket Approved"]
    return updated


def safe_purple_mock_artifact_name(session: dict[str, Any]) -> str:
    device = str(session.get("device", {}).get("product_type") or "device").replace(",", "_")
    build = str((session.get("ipsw") or {}).get("product_build_version") or "build")
    clean_device = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in device).strip("_") or "device"
    clean_build = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in build).strip("_") or "build"
    return f"purple_restore_{clean_device}_{clean_build}.purple.mock.json"


def run_purple_restore_simulation(session: dict[str, Any], simulation: bool = False, succeed: bool = True) -> dict[str, Any]:
    require_internal_simulation(simulation)
    if session.get("blocked"):
        raise PurpleRestoreGuardrailError(str(session.get("failure_reason") or "Purple Restore simulation is blocked"))
    if not session.get("mock_tatsu_ticket"):
        raise PurpleRestoreGuardrailError("request a mock Tatsu ticket before running the restore simulation")
    updated = dict(session)
    updated["state"] = "Restore Complete" if succeed else "Restore Failed"
    updated["restore_allowed"] = False
    updated["restore_allowed_in_simulation_only"] = True
    updated["valid_for_real_restore"] = False
    updated["command"] = []
    updated["command_preview"] = "No restore binary is executed by the Purple Restore emulator."
    updated["events"] = [*(session.get("events") or []), "Restore Proceeding", updated["state"]]
    updated["summary"] = "Purple Restore UI simulation completed." if succeed else "Purple Restore UI simulation failed by selected test outcome."
    return updated


def validate_no_purple_mock_artifact_in_command(command: list[str]) -> None:
    if any(str(part).lower().endswith(".purple.mock.json") for part in command):
        raise MockTicketGuardrailError("Purple Restore mock artifacts cannot be passed to real restore commands")
    validate_command_has_no_mock_ticket(command)


class MockTatsuHandler(BaseHTTPRequestHandler):
    server_version = "MockTatsu/0.1"

    def _authorized(self) -> bool:
        return self.headers.get("X-Apple-Connect-Simulation") == INTERNAL_CLIENT_TOKEN

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

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write_json(403, {"simulation": True, "error": "mock Tatsu API is restricted to the in-app simulator client"})
            return
        try:
            body = self._read_json()
            if self.path != "/mock-tatsu/request":
                self._write_json(404, {"simulation": True, "error": "not found"})
                return
            session = build_purple_restore_session(
                body.get("device") if isinstance(body.get("device"), dict) else {},
                str(body.get("ipsw_path") or "") or None,
                simulation=bool(getattr(self.server, "simulation", False)),
                product_type_override=str(body.get("product_type") or "") or None,
                mode_override=str(body.get("mode") or "") or None,
            )
            payload = request_mock_tatsu_ticket(session, simulation=True)
            self._write_json(200, payload["mock_tatsu_ticket"])
        except Exception as exc:
            self._write_json(400, {"simulation": True, "error": str(exc), "valid_for_real_restore": False, "banner": PURPLE_SIMULATION_BANNER})

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write_json(403, {"simulation": True, "error": "mock Tatsu API is restricted to the in-app simulator client"})
            return
        prefix = "/mock-tatsu/status/"
        if not self.path.startswith(prefix):
            self._write_json(404, {"simulation": True, "error": "not found"})
            return
        payload = TATSU_STORE.requests.get(self.path[len(prefix) :])
        if not payload:
            self._write_json(404, {"simulation": True, "error": "unknown request id", "valid_for_real_restore": False})
            return
        self._write_json(200, payload)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def create_mock_tatsu_server(host: str = "127.0.0.1", port: int = 0, simulation: bool = False) -> ThreadingHTTPServer:
    require_internal_simulation(simulation)
    server = ThreadingHTTPServer((host, port), MockTatsuHandler)
    setattr(server, "simulation", True)
    return server


def start_mock_tatsu_server_in_thread(host: str = "127.0.0.1", port: int = 0, simulation: bool = False) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = create_mock_tatsu_server(host, port, simulation=simulation)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
