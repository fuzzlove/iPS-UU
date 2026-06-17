from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ips_uu.services.backend_runner import BackendRunner
from ips_uu.services.mock_tss_service import (
    MockTicketGuardrailError,
    SIMULATION_BANNER,
    SimulationRequiredError,
    create_server,
    ensure_mock_export_path,
    generate_mock_response,
    save_mock_ticket,
    simulated_restore_flash_plan,
    validate_command_has_no_mock_ticket,
)


def test_mock_mode_requires_simulation_enabled() -> None:
    with pytest.raises(SimulationRequiredError):
        generate_mock_response("iPhone10,5", "mock-ecid", "20H240", "Approved", simulation=False)


def test_exported_mock_ticket_always_ends_in_mock_json(tmp_path: Path) -> None:
    response = generate_mock_response("iPhone10,5", "mock-ecid", "20H240", "Approved", simulation=True)
    saved = save_mock_ticket(response, tmp_path / "ticket")

    assert saved.name.endswith(".mock.json")
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["simulation"] is True
    assert payload["valid_for_real_restore"] is False


def test_block_real_restore_artifact_export_names(tmp_path: Path) -> None:
    with pytest.raises(MockTicketGuardrailError):
        ensure_mock_export_path(tmp_path / "real.shsh2")
    with pytest.raises(MockTicketGuardrailError):
        ensure_mock_export_path(tmp_path / "ticket.apticket")


def test_mock_tickets_cannot_be_passed_to_restore_commands(tmp_path: Path) -> None:
    mock_ticket = tmp_path / "mock_ticket_iPhone10_5_20H240.mock.json"
    mock_ticket.write_text("{}", encoding="utf-8")

    with pytest.raises(MockTicketGuardrailError):
        validate_command_has_no_mock_ticket(["idevicerestore", "-e", str(mock_ticket)])
    with pytest.raises(MockTicketGuardrailError):
        BackendRunner("test").build_plan(["futurerestore", "-t", str(mock_ticket)], "test")


def test_approved_mock_ticket_advances_restore_flash_simulation_only() -> None:
    response = generate_mock_response("iPhone10,5", "mock-ecid", "20H240", "Approved", simulation=True)
    plan = simulated_restore_flash_plan(response, simulation=True)

    assert plan["status"] == "restore_allowed_in_simulation_mode_only"
    assert plan["restore_allowed_in_simulation_only"] is True
    assert plan["real_restore_allowed"] is False
    assert plan["valid_for_real_restore"] is False
    assert plan["command"] == []
    assert "No restore binary is executed" in plan["command_preview"]


def test_rejected_mock_ticket_blocks_restore_flash_simulation() -> None:
    response = generate_mock_response("iPhone10,5", "mock-ecid", "20H240", "Rejected", simulation=True)
    plan = simulated_restore_flash_plan(response, simulation=True)

    assert plan["status"] == "restore_blocked"
    assert plan["restore_allowed_in_simulation_only"] is False
    assert plan["real_restore_allowed"] is False
    assert plan["command"] == []


def test_local_mock_api_endpoints_require_simulation_and_return_json() -> None:
    with pytest.raises(SimulationRequiredError):
        create_server(simulation=False)

    try:
        server = create_server(simulation=True)
    except (PermissionError, socket.error) as exc:
        pytest.skip(f"local socket binding is unavailable in this sandbox: {exc}")
    try:
        host, port = server.server_address
        base = f"http://{host}:{port}"
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        request = urllib.request.Request(
            base + "/mock-tss/request",
            data=json.dumps({"device": "iPhone10,5", "ecid": "mock-ecid", "build": "20H240"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["simulation"] is True
        assert payload["valid_for_real_restore"] is False
        decision = urllib.request.Request(
            base + "/mock-tss/decision",
            data=json.dumps({"request_id": payload["request_id"], "status": "Approved"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(decision, timeout=5) as response:
            decided = json.loads(response.read().decode("utf-8"))
        assert decided["status"] == "approved"
        assert decided["ticket_type"] == "mock"
    finally:
        server.shutdown()
        server.server_close()


def test_mock_response_contains_clear_simulation_banner() -> None:
    response = generate_mock_response("iPhone10,5", "mock-ecid", "20H240", "Rejected", simulation=True)

    assert response["banner"] == SIMULATION_BANNER
    assert response["valid_for_real_restore"] is False
