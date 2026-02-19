"""Unit tests for Phase 1–4 enhancement modules.

Tests cover the new modules added from the Azure Local documentation
gap analysis: register_providers, prepare_ad, validate_permissions,
provision_keyvault, cloud_witness, configure_security, configure_proxy,
post_deploy, and enhancements to validate_nodes, configure_network,
and add_node.
"""

import pytest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# register_providers
# ---------------------------------------------------------------------------

from azure_local_deploy.register_providers import REQUIRED_PROVIDERS


def test_required_providers_not_empty():
    assert len(REQUIRED_PROVIDERS) >= 10


def test_required_providers_contains_hci():
    assert "Microsoft.AzureStackHCI" in REQUIRED_PROVIDERS


def test_required_providers_contains_keyvault():
    assert "Microsoft.KeyVault" in REQUIRED_PROVIDERS


# ---------------------------------------------------------------------------
# validate_permissions
# ---------------------------------------------------------------------------

from azure_local_deploy.validate_permissions import (
    RequiredRole,
    PermissionCheck,
    PermissionReport,
    SUBSCRIPTION_ROLES,
    RESOURCE_GROUP_ROLES,
)


def test_subscription_roles_not_empty():
    assert len(SUBSCRIPTION_ROLES) > 0


def test_resource_group_roles_not_empty():
    assert len(RESOURCE_GROUP_ROLES) > 0


def test_permission_report_all_ok_true():
    report = PermissionReport()
    report.checks = [
        PermissionCheck(role_name="A", scope="sub", assigned=True, critical=True),
        PermissionCheck(role_name="B", scope="rg", assigned=True, critical=False),
    ]
    assert report.all_ok is True


def test_permission_report_all_ok_false():
    report = PermissionReport()
    report.checks = [
        PermissionCheck(role_name="A", scope="sub", assigned=True, critical=True),
        PermissionCheck(role_name="B", scope="rg", assigned=False, critical=True),
    ]
    assert report.all_ok is False


# ---------------------------------------------------------------------------
# prepare_ad
# ---------------------------------------------------------------------------

from azure_local_deploy.prepare_ad import ADPrepConfig, ADPrepResult


def test_ad_prep_config_defaults():
    cfg = ADPrepConfig(
        ou_name="Test",
        deployment_user="u",
        deployment_password="p",
        domain_fqdn="corp.local",
    )
    assert cfg.ou_name == "Test"
    assert cfg.skip_if_exists is True
    assert cfg.block_inheritance is True


def test_ad_prep_result():
    result = ADPrepResult(
        ou_created=True,
        user_created=True,
        gpo_blocked=True,
    )
    assert result.ou_created is True


# ---------------------------------------------------------------------------
# configure_security
# ---------------------------------------------------------------------------

from azure_local_deploy.configure_security import (
    SecurityProfile,
    RECOMMENDED_SECURITY,
    CUSTOMIZED_SECURITY,
)


def test_recommended_security_all_true():
    for field_name in SecurityProfile.__dataclass_fields__:
        assert getattr(RECOMMENDED_SECURITY, field_name) is True, f"{field_name} should be True"


def test_customized_security_has_some_false():
    vals = [getattr(CUSTOMIZED_SECURITY, f) for f in SecurityProfile.__dataclass_fields__]
    assert False in vals, "Customized profile should have at least one False value"


def test_security_profile_to_deployment_dict():
    d = RECOMMENDED_SECURITY.to_deployment_dict()
    assert d["hvciProtection"] is True
    assert d["drtmProtection"] is True
    assert d["wdacEnforced"] is True
    assert len(d) == 10


# ---------------------------------------------------------------------------
# configure_proxy
# ---------------------------------------------------------------------------

from azure_local_deploy.configure_proxy import ProxyConfig


def test_proxy_config_defaults():
    cfg = ProxyConfig(
        http_proxy="http://proxy:8080",
        https_proxy="http://proxy:8080",
    )
    assert cfg.no_proxy == ""
    assert cfg.auto_bypass is True


# ---------------------------------------------------------------------------
# provision_keyvault
# ---------------------------------------------------------------------------
# All public functions require Azure SDK, so only import-level tests

def test_provision_keyvault_importable():
    from azure_local_deploy.provision_keyvault import provision_keyvault, check_keyvault_exists
    assert callable(provision_keyvault)
    assert callable(check_keyvault_exists)


# ---------------------------------------------------------------------------
# cloud_witness
# ---------------------------------------------------------------------------

def test_cloud_witness_importable():
    from azure_local_deploy.cloud_witness import provision_cloud_witness, configure_cluster_witness
    assert callable(provision_cloud_witness)
    assert callable(configure_cluster_witness)


# ---------------------------------------------------------------------------
# post_deploy
# ---------------------------------------------------------------------------

from azure_local_deploy.post_deploy import PostDeployTask, PostDeployReport


def test_post_deploy_task():
    task = PostDeployTask(name="Test", success=True, message="OK")
    assert task.success is True


def test_post_deploy_report_all_ok():
    report = PostDeployReport()
    report.add(PostDeployTask("A", True, "Good"))
    report.add(PostDeployTask("B", True, "Good"))
    assert report.all_ok is True


def test_post_deploy_report_not_ok():
    report = PostDeployReport()
    report.add(PostDeployTask("A", True, "Good"))
    report.add(PostDeployTask("B", False, "Bad"))
    assert report.all_ok is False


# ---------------------------------------------------------------------------
# validate_nodes – reserved IP range checks
# ---------------------------------------------------------------------------

from azure_local_deploy.validate_nodes import (
    Severity,
    _check_reserved_ip_ranges,
    RESERVED_IP_RANGES,
)


def test_reserved_ip_ranges_defined():
    assert len(RESERVED_IP_RANGES) == 2


def test_reserved_ip_safe_address():
    results = _check_reserved_ip_ranges(["192.168.1.100"])
    assert len(results) == 1
    assert results[0].severity == Severity.PASS


def test_reserved_ip_kubernetes_service_cidr():
    # 10.96.0.0/12 – Kubernetes service CIDR
    results = _check_reserved_ip_ranges(["10.96.0.1"])
    assert len(results) == 1
    assert results[0].severity == Severity.FAIL


def test_reserved_ip_kubernetes_pod_cidr():
    # 10.244.0.0/16 – Kubernetes pod CIDR
    results = _check_reserved_ip_ranges(["10.244.1.1"])
    assert len(results) == 1
    assert results[0].severity == Severity.FAIL


def test_reserved_ip_invalid_address():
    results = _check_reserved_ip_ranges(["not-an-ip"])
    assert len(results) == 1
    assert results[0].severity == Severity.WARN


def test_reserved_ip_multiple_addresses():
    results = _check_reserved_ip_ranges(["192.168.1.1", "10.96.0.5", "10.0.0.1"])
    assert len(results) == 3
    pass_count = sum(1 for r in results if r.severity == Severity.PASS)
    fail_count = sum(1 for r in results if r.severity == Severity.FAIL)
    assert pass_count == 2
    assert fail_count == 1


# ---------------------------------------------------------------------------
# configure_network – NetworkIntent data class
# ---------------------------------------------------------------------------

from azure_local_deploy.configure_network import NicConfig, NetworkIntent


def test_network_intent_creation():
    intent = NetworkIntent(
        name="Mgmt_Compute",
        traffic_types=["Management", "Compute"],
        adapter_names=["NIC1", "NIC2"],
    )
    assert intent.name == "Mgmt_Compute"
    assert len(intent.traffic_types) == 2
    assert intent.override_qos_policy is False


def test_network_intent_with_storage_vlans():
    intent = NetworkIntent(
        name="Storage",
        traffic_types=["Storage"],
        adapter_names=["Storage1", "Storage2"],
        storage_vlan_ids=[711, 712],
    )
    assert intent.storage_vlan_ids == [711, 712]


# ---------------------------------------------------------------------------
# orchestrator – STAGES list updated
# ---------------------------------------------------------------------------

from azure_local_deploy.orchestrator import STAGES


def test_stages_count():
    assert len(STAGES) == 17


def test_stages_includes_new_stages():
    assert "register_providers" in STAGES
    assert "validate_permissions" in STAGES
    assert "prepare_ad" in STAGES
    assert "configure_proxy" in STAGES
    assert "configure_security" in STAGES
    assert "provision_keyvault" in STAGES
    assert "cloud_witness" in STAGES
    assert "post_deploy" in STAGES


def test_stages_order_register_before_validate():
    assert STAGES.index("register_providers") < STAGES.index("validate_nodes")


def test_stages_order_deploy_before_post():
    assert STAGES.index("deploy_cluster") < STAGES.index("post_deploy")
