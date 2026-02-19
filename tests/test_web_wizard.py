"""Unit tests for the web wizard config builder."""

import pytest

from azure_local_deploy.web_app import _build_config_from_wizard, _max_steps


def test_max_steps_new_cluster():
    assert _max_steps("new_cluster") == 12


def test_max_steps_add_node():
    assert _max_steps("add_node") == 9


def test_build_config_new_cluster_basic():
    data = {
        "tenant_id": "t-123",
        "subscription_id": "s-456",
        "resource_group": "rg-test",
        "region": "eastus",
        "iso_url": "https://server/image.iso",
        "ntp_servers": "time.windows.com, pool.ntp.org",
        "timezone": "UTC",
        "server_count": "1",
        "cluster_name": "my-cluster",
        "cluster_ip": "10.0.1.100",
        "domain_fqdn": "",
        "ou_path": "",
        "deployment_timeout": "7200",
        "server_1_idrac_host": "10.0.0.11",
        "server_1_idrac_user": "root",
        "server_1_idrac_password": "pass",
        "server_1_host_ip": "10.0.1.11",
        "server_1_host_user": "Administrator",
        "server_1_host_password": "pass2",
        "server_1_ssh_port": "22",
        "server_1_arc_resource_id": "",
        "server_1_nic_count": "1",
        "server_1_nic_1_name": "Mgmt",
        "server_1_nic_1_mac": "AA:BB:CC:DD:EE:01",
        "server_1_nic_1_ip": "10.0.1.11",
        "server_1_nic_1_prefix": "24",
        "server_1_nic_1_gateway": "10.0.1.1",
        "server_1_nic_1_dns": "10.0.0.5, 10.0.0.6",
    }
    config = _build_config_from_wizard("new_cluster", data)

    assert config["azure"]["tenant_id"] == "t-123"
    assert config["azure"]["region"] == "eastus"
    assert config["cluster"]["name"] == "my-cluster"
    assert len(config["servers"]) == 1
    assert config["servers"][0]["idrac_host"] == "10.0.0.11"
    assert len(config["servers"][0]["nics"]) == 1
    assert config["servers"][0]["nics"][0]["adapter_name"] == "Mgmt"
    assert config["servers"][0]["nics"][0]["dns_servers"] == ["10.0.0.5", "10.0.0.6"]
    assert config["global"]["ntp_servers"] == ["time.windows.com", "pool.ntp.org"]


def test_build_config_add_node():
    data = {
        "tenant_id": "t-123",
        "subscription_id": "s-456",
        "resource_group": "rg-test",
        "region": "westus2",
        "iso_url": "https://server/image.iso",
        "ntp_servers": "time.windows.com",
        "timezone": "UTC",
        "server_count": "1",
        "existing_cluster_name": "existing-cluster",
        "existing_cluster_rg": "rg-existing",
        "server_1_idrac_host": "10.0.0.20",
        "server_1_idrac_user": "root",
        "server_1_idrac_password": "pass",
        "server_1_host_ip": "10.0.1.20",
        "server_1_host_user": "Admin",
        "server_1_host_password": "pass2",
        "server_1_ssh_port": "22",
        "server_1_nic_count": "1",
        "server_1_nic_1_name": "Mgmt",
        "server_1_nic_1_mac": "AA:BB:CC:00:11:22",
        "server_1_nic_1_ip": "10.0.1.20",
        "server_1_nic_1_prefix": "24",
    }
    config = _build_config_from_wizard("add_node", data)

    assert "cluster" not in config
    assert config["add_node"]["existing_cluster_name"] == "existing-cluster"
    assert config["add_node"]["existing_cluster_resource_group"] == "rg-existing"
    assert len(config["servers"]) == 1
    assert config["servers"][0]["host_ip"] == "10.0.1.20"


def test_build_config_multiple_servers():
    data = {
        "tenant_id": "t",
        "subscription_id": "s",
        "resource_group": "rg",
        "region": "eastus",
        "iso_url": "https://x/y.iso",
        "ntp_servers": "time.windows.com",
        "timezone": "UTC",
        "server_count": "2",
        "cluster_name": "cl",
        "cluster_ip": "10.0.1.100",
        "deployment_timeout": "3600",
    }
    # Add minimal server data
    for i in range(1, 3):
        p = f"server_{i}_"
        data[f"{p}idrac_host"] = f"10.0.0.{10+i}"
        data[f"{p}idrac_user"] = "root"
        data[f"{p}idrac_password"] = "pass"
        data[f"{p}host_ip"] = f"10.0.1.{10+i}"
        data[f"{p}host_user"] = "Admin"
        data[f"{p}host_password"] = "pw"
        data[f"{p}ssh_port"] = "22"
        data[f"{p}nic_count"] = "0"

    config = _build_config_from_wizard("new_cluster", data)
    assert len(config["servers"]) == 2
    assert config["servers"][0]["idrac_host"] == "10.0.0.11"
    assert config["servers"][1]["idrac_host"] == "10.0.0.12"
