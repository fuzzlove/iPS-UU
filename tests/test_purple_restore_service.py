import plistlib
import zipfile
from pathlib import Path

import pytest

from ips_uu.services.backend_runner import BackendRunner
from ips_uu.services.mock_tss_service import MockTicketGuardrailError, SimulationRequiredError
from ips_uu.services.purple_restore_service import (
    PURPLE_WARNING,
    PurpleRestoreGuardrailError,
    build_purple_restore_session,
    request_mock_tatsu_ticket,
    run_purple_restore_simulation,
    validate_no_purple_mock_artifact_in_command,
)


def make_ipsw(path: Path, product_type: str = "iPhone10,5", build: str = "20H240") -> Path:
    manifest = {
        "ProductVersion": "16.7.10",
        "ProductBuildVersion": build,
        "SupportedProductTypes": [product_type],
        "BuildIdentities": [
            {
                "Info": {"Variant": "Customer Erase Install (IPSW)", "DeviceClass": "d22ap", "BuildNumber": build},
                "Manifest": {},
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BuildManifest.plist", plistlib.dumps(manifest))
    return path


def test_purple_restore_requires_simulation(tmp_path: Path) -> None:
    ipsw = make_ipsw(tmp_path / "firmware.ipsw")

    with pytest.raises(SimulationRequiredError):
        build_purple_restore_session({"product_type": "iPhone10,5"}, str(ipsw), simulation=False)


def test_purple_restore_state_machine_completes_in_simulation_only(tmp_path: Path) -> None:
    ipsw = make_ipsw(tmp_path / "firmware.ipsw")
    session = build_purple_restore_session(
        {"product_type": "iPhone10,5", "ecid": "mock-ecid", "current_mode": "recovery"},
        str(ipsw),
        simulation=True,
    )
    session = request_mock_tatsu_ticket(session, simulation=True)
    completed = run_purple_restore_simulation(session, simulation=True)

    assert completed["state"] == "Restore Complete"
    assert completed["restore_allowed"] is False
    assert completed["restore_allowed_in_simulation_only"] is True
    assert completed["valid_for_real_restore"] is False
    assert completed["command"] == []
    assert PURPLE_WARNING in completed["warning"]


def test_purple_restore_blocks_cross_device_ipsw(tmp_path: Path) -> None:
    ipsw = make_ipsw(tmp_path / "wrong-device.ipsw", product_type="iPhone9,1")
    session = build_purple_restore_session({"product_type": "iPhone10,5"}, str(ipsw), simulation=True)

    assert session["blocked"] is True
    assert session["state"] == "Restore Failed"
    assert session["compatibility"]["status"] == "incompatible"
    with pytest.raises(PurpleRestoreGuardrailError):
        request_mock_tatsu_ticket(session, simulation=True)


def test_purple_mock_artifacts_cannot_be_passed_to_restore_commands(tmp_path: Path) -> None:
    artifact = tmp_path / "purple_restore_iPhone10_5_20H240.purple.mock.json"
    artifact.write_text("{}", encoding="utf-8")

    with pytest.raises(MockTicketGuardrailError):
        validate_no_purple_mock_artifact_in_command(["idevicerestore", "--erase", str(artifact)])
    with pytest.raises(MockTicketGuardrailError):
        BackendRunner("test").build_plan(["futurerestore", "-t", str(artifact)], "test")

