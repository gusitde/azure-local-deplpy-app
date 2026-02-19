"""Unit tests for configure_bios module."""

import pytest
from unittest.mock import patch, MagicMock

from azure_local_deploy.configure_bios import (
    AZURE_LOCAL_BIOS_DEFAULTS,
    BiosProfile,
    get_current_bios,
    compare_bios,
    configure_bios,
)
from azure_local_deploy.idrac_client import IdracClient


@pytest.fixture
def idrac():
    return IdracClient("10.0.0.1", "root", "password", verify_ssl=False)


# ---- BIOS defaults -------------------------------------------------------

def test_azure_local_bios_defaults_keys():
    """Critical Azure Local attributes must be in defaults."""
    required_keys = [
        "ProcVirtualization",
        "ProcVtd",
        "SriovGlobalEnable",
        "SecureBoot",
        "BootMode",
        "TpmSecurity",
        "MemOpMode",
        "LogicalProc",
        "ProcCStates",
    ]
    for key in required_keys:
        assert key in AZURE_LOCAL_BIOS_DEFAULTS, f"Missing required key: {key}"


def test_bios_virtualisation_enabled():
    assert AZURE_LOCAL_BIOS_DEFAULTS["ProcVirtualization"] == "Enabled"
    assert AZURE_LOCAL_BIOS_DEFAULTS["ProcVtd"] == "Enabled"
    assert AZURE_LOCAL_BIOS_DEFAULTS["SriovGlobalEnable"] == "Enabled"


def test_bios_secure_boot_enabled():
    assert AZURE_LOCAL_BIOS_DEFAULTS["SecureBoot"] == "Enabled"
    assert AZURE_LOCAL_BIOS_DEFAULTS["BootMode"] == "Uefi"


# ---- BiosProfile ----------------------------------------------------------

def test_bios_profile_defaults():
    profile = BiosProfile()
    assert profile.name == "AzureLocal"
    assert "ProcVirtualization" in profile.attributes
    # Default profile should match AZURE_LOCAL_BIOS_DEFAULTS
    assert profile.attributes["BootMode"] == "Uefi"


def test_bios_profile_custom():
    custom = {"BootMode": "Uefi", "SecureBoot": "Disabled"}
    profile = BiosProfile(name="Custom", attributes=custom)
    assert profile.name == "Custom"
    assert profile.attributes["SecureBoot"] == "Disabled"


# ---- compare_bios --------------------------------------------------------

def test_compare_bios_all_match():
    current = {"ProcVirtualization": "Enabled", "BootMode": "Uefi"}
    desired = {"ProcVirtualization": "Enabled", "BootMode": "Uefi"}
    mismatched, already_ok = compare_bios(current, desired)
    assert len(mismatched) == 0
    assert len(already_ok) == 2


def test_compare_bios_mismatch():
    current = {"ProcVirtualization": "Disabled", "BootMode": "Bios"}
    desired = {"ProcVirtualization": "Enabled", "BootMode": "Uefi"}
    mismatched, already_ok = compare_bios(current, desired)
    assert len(mismatched) == 2
    assert mismatched["ProcVirtualization"] == ("Disabled", "Enabled")
    assert mismatched["BootMode"] == ("Bios", "Uefi")


def test_compare_bios_missing_attribute():
    """Attributes not present on server should be silently skipped."""
    current = {"BootMode": "Uefi"}
    desired = {"BootMode": "Uefi", "NonExistentAttr": "Value"}
    mismatched, already_ok = compare_bios(current, desired)
    assert "NonExistentAttr" not in mismatched
    assert "NonExistentAttr" not in already_ok
    assert already_ok["BootMode"] == "Uefi"


def test_compare_bios_partial_match():
    current = {
        "ProcVirtualization": "Enabled",
        "BootMode": "Bios",
        "SecureBoot": "Enabled",
    }
    desired = {
        "ProcVirtualization": "Enabled",
        "BootMode": "Uefi",
        "SecureBoot": "Enabled",
    }
    mismatched, already_ok = compare_bios(current, desired)
    assert len(mismatched) == 1
    assert len(already_ok) == 2


# ---- get_current_bios ----------------------------------------------------

@patch.object(IdracClient, "get_bios_attributes")
def test_get_current_bios(mock_bios, idrac):
    expected = {"BootMode": "Uefi", "ProcVirtualization": "Enabled"}
    mock_bios.return_value = expected
    result = get_current_bios(idrac)
    assert result == expected


# ---- configure_bios -------------------------------------------------------

@patch("azure_local_deploy.configure_bios._create_bios_config_job")
@patch("azure_local_deploy.configure_bios._patch_bios_pending")
@patch("azure_local_deploy.configure_bios.compare_bios")
@patch("azure_local_deploy.configure_bios.get_current_bios")
def test_configure_bios_no_changes_needed(
    mock_current, mock_compare, mock_patch, mock_job, idrac
):
    """When all settings already match, skip patching."""
    mock_current.return_value = {"BootMode": "Uefi"}
    mock_compare.return_value = ({}, {"BootMode": "Uefi"})  # no mismatches

    configure_bios(idrac)

    mock_patch.assert_not_called()
    mock_job.assert_not_called()


@patch("azure_local_deploy.configure_bios._wait_for_host_ready")
@patch("azure_local_deploy.configure_bios._create_bios_config_job")
@patch("azure_local_deploy.configure_bios._patch_bios_pending")
@patch("azure_local_deploy.configure_bios.compare_bios")
@patch("azure_local_deploy.configure_bios.get_current_bios")
def test_configure_bios_applies_changes(
    mock_current, mock_compare, mock_patch, mock_job, mock_wait, idrac
):
    """When mismatches exist, patch and create a job."""
    mock_current.return_value = {"BootMode": "Bios"}
    mock_compare.return_value = (
        {"BootMode": ("Bios", "Uefi")},  # mismatched
        {},                                # already_ok
    )
    mock_patch.return_value = None
    mock_job.return_value = "JID_789"

    with patch.object(idrac, "wait_for_task", return_value={"TaskState": "Completed"}):
        with patch.object(idrac, "set_power_state"):
            configure_bios(idrac, apply_reboot=True)

    mock_patch.assert_called_once()
    mock_job.assert_called_once()
