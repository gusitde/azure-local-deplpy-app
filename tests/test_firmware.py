"""Unit tests for update_firmware module."""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from azure_local_deploy.update_firmware import (
    FirmwareTarget,
    get_firmware_inventory,
    log_firmware_inventory,
    update_firmware,
)
from azure_local_deploy.idrac_client import IdracClient


@pytest.fixture
def idrac():
    return IdracClient("10.0.0.1", "root", "password", verify_ssl=False)


# ---- FirmwareTarget dataclass -----------------------------------------

def test_firmware_target_defaults():
    target = FirmwareTarget(component="BIOS", dup_url="https://dl.dell.com/bios.exe")
    assert target.component == "BIOS"
    assert target.dup_url == "https://dl.dell.com/bios.exe"
    assert target.target_version == ""
    assert target.install_option == "Now"


def test_firmware_target_custom():
    target = FirmwareTarget(
        component="iDRAC",
        dup_url="https://dl.dell.com/idrac.exe",
        target_version="7.00.00.00",
        install_option="NowAndReboot",
    )
    assert target.install_option == "NowAndReboot"
    assert target.target_version == "7.00.00.00"


# ---- get_firmware_inventory -------------------------------------------

@patch.object(IdracClient, "get")
def test_get_firmware_inventory(mock_get, idrac):
    mock_get.side_effect = [
        # First call: /UpdateService/FirmwareInventory
        {
            "Members": [
                {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/Installed-1"},
                {"@odata.id": "/redfish/v1/UpdateService/FirmwareInventory/Installed-2"},
            ]
        },
        # Second & third calls: individual members
        {"Name": "BIOS", "Version": "2.20.1", "Id": "Installed-1"},
        {"Name": "iDRAC", "Version": "7.00.00.00", "Id": "Installed-2"},
    ]
    inventory = get_firmware_inventory(idrac)
    assert len(inventory) == 2
    assert inventory[0]["Name"] == "BIOS"
    assert inventory[1]["Version"] == "7.00.00.00"


@patch.object(IdracClient, "get")
def test_get_firmware_inventory_empty(mock_get, idrac):
    mock_get.return_value = {"Members": []}
    inventory = get_firmware_inventory(idrac)
    assert inventory == []


# ---- log_firmware_inventory -------------------------------------------

@patch("azure_local_deploy.update_firmware.get_firmware_inventory")
def test_log_firmware_inventory(mock_inv, idrac):
    items = [{"Name": "BIOS", "Version": "2.20.1"}]
    mock_inv.return_value = items
    result = log_firmware_inventory(idrac)
    assert result == items


# ---- update_firmware --------------------------------------------------

@patch("azure_local_deploy.update_firmware._trigger_simple_update")
@patch("azure_local_deploy.update_firmware.log_firmware_inventory")
def test_update_firmware_with_targets(mock_log, mock_simple, idrac):
    """Firmware update with individual DUP targets uses SimpleUpdate."""
    targets = [
        FirmwareTarget(component="BIOS", dup_url="https://dl.dell.com/bios.exe"),
    ]
    mock_simple.return_value = "JID_123"
    mock_log.return_value = [{"Name": "BIOS", "Version": "2.20.1"}]

    # Patch wait_for_task to return immediately
    with patch.object(idrac, "wait_for_task", return_value={"TaskState": "Completed"}):
        update_firmware(idrac, targets=targets, apply_reboot=False)

    mock_simple.assert_called_once()


@patch("azure_local_deploy.update_firmware._trigger_repository_update")
@patch("azure_local_deploy.update_firmware.log_firmware_inventory")
def test_update_firmware_with_catalog(mock_log, mock_repo, idrac):
    """Firmware update with catalog_url uses repository update."""
    mock_repo.return_value = "JID_456"
    mock_log.return_value = []

    with patch.object(idrac, "wait_for_task", return_value={"TaskState": "Completed"}):
        update_firmware(idrac, catalog_url="https://dl.dell.com/catalog.xml")

    mock_repo.assert_called_once()
