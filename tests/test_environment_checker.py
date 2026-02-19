"""Unit tests for environment_checker module."""

import json
import pytest
from unittest.mock import patch, MagicMock

from azure_local_deploy.environment_checker import (
    ValidatorResult,
    EnvironmentCheckReport,
    VALIDATOR_CMDLETS,
    _parse_validator_output,
    _build_validator_script,
    install_environment_checker,
    uninstall_environment_checker,
    run_validator,
    run_environment_checker,
    run_environment_checker_all_nodes,
)


# ---- Data types -----------------------------------------------------------

def test_validator_result_defaults():
    r = ValidatorResult(name="Connectivity")
    assert r.status == "Unknown"
    assert r.critical == 0
    assert r.passed == 0
    assert r.details == []
    assert r.error == ""


def test_environment_check_report_ok_passed():
    report = EnvironmentCheckReport(host="10.0.0.1", overall_status="Passed")
    assert report.ok is True


def test_environment_check_report_ok_failed():
    report = EnvironmentCheckReport(host="10.0.0.1", overall_status="Failed")
    assert report.ok is False


def test_environment_check_report_ok_partial():
    report = EnvironmentCheckReport(host="10.0.0.1", overall_status="Partial")
    assert report.ok is True


def test_environment_check_report_critical_count():
    report = EnvironmentCheckReport(host="10.0.0.1")
    report.validators = [
        ValidatorResult(name="A", critical=2),
        ValidatorResult(name="B", critical=1),
    ]
    assert report.critical_count == 3


def test_environment_check_report_warning_count():
    report = EnvironmentCheckReport(host="10.0.0.1")
    report.validators = [
        ValidatorResult(name="A", warning=5),
        ValidatorResult(name="B", warning=3),
    ]
    assert report.warning_count == 8


# ---- VALIDATOR_CMDLETS ----------------------------------------------------

def test_all_five_validators_present():
    expected = ["Connectivity", "Hardware", "Active Directory", "Network", "Arc Integration"]
    for name in expected:
        assert name in VALIDATOR_CMDLETS, f"Missing validator: {name}"


def test_cmdlets_are_invoke_commands():
    for name, cmdlet in VALIDATOR_CMDLETS.items():
        assert cmdlet.startswith("Invoke-AzStackHci"), \
            f"{name} cmdlet should start with Invoke-AzStackHci"


# ---- _build_validator_script ---------------------------------------------

def test_build_validator_script_contains_cmdlet():
    script = _build_validator_script("Invoke-AzStackHciConnectivityValidation")
    assert "Invoke-AzStackHciConnectivityValidation" in script
    assert "-PassThru" in script
    assert "ConvertTo-Json" in script


def test_build_validator_script_has_error_handling():
    script = _build_validator_script("Invoke-AzStackHciHardwareValidation")
    assert "catch" in script
    assert "Error" in script


# ---- _parse_validator_output ----------------------------------------------

def test_parse_empty_output():
    r = _parse_validator_output("Test", "")
    assert r.status == "Skipped"
    assert r.error == "No output returned"


def test_parse_invalid_json():
    r = _parse_validator_output("Test", "not json at all {{{")
    assert r.status == "Error"
    assert r.error != ""


def test_parse_error_object():
    data = json.dumps({"Error": "Module not found"})
    r = _parse_validator_output("Test", data)
    assert r.status == "Error"
    assert "Module not found" in r.error


def test_parse_all_passed():
    items = [
        {"Status": "Succeeded", "Severity": "Informational", "Name": "DNS"},
        {"Status": "Succeeded", "Severity": "Informational", "Name": "Firewall"},
    ]
    r = _parse_validator_output("Connectivity", json.dumps(items))
    assert r.status == "Succeeded"
    assert r.informational == 2
    assert r.critical == 0


def test_parse_with_critical():
    items = [
        {"Status": "Succeeded", "Severity": "Informational", "Name": "DNS"},
        {"Status": "Failed", "Severity": "Critical", "Name": "Firewall"},
    ]
    r = _parse_validator_output("Connectivity", json.dumps(items))
    assert r.status == "Failed"
    assert r.critical == 1


def test_parse_with_warnings():
    items = [
        {"Status": "Succeeded", "Severity": "Warning", "Name": "SSL"},
    ]
    r = _parse_validator_output("Connectivity", json.dumps(items))
    assert r.status == "Succeeded"
    assert r.warning == 1


def test_parse_single_object():
    """Single object (not array) should also work."""
    item = {"Status": "Succeeded", "Severity": "Informational", "Name": "Check1"}
    r = _parse_validator_output("Test", json.dumps(item))
    assert r.status == "Succeeded"
    assert len(r.details) == 1


# ---- install_environment_checker ------------------------------------------

@patch("azure_local_deploy.environment_checker.run_powershell")
def test_install_success(mock_ps):
    mock_ps.return_value = "INSTALLED:1.2.3.4"
    version = install_environment_checker("10.0.0.1", "admin", "pass")
    assert version == "1.2.3.4"
    mock_ps.assert_called_once()


@patch("azure_local_deploy.environment_checker.run_powershell")
def test_install_failure(mock_ps):
    mock_ps.return_value = "Some error, no INSTALLED line"
    with pytest.raises(RuntimeError, match="Failed to install"):
        install_environment_checker("10.0.0.1", "admin", "pass")


# ---- uninstall_environment_checker ----------------------------------------

@patch("azure_local_deploy.environment_checker.run_powershell")
def test_uninstall_success(mock_ps):
    mock_ps.return_value = "UNINSTALLED"
    # Should not raise
    uninstall_environment_checker("10.0.0.1", "admin", "pass")


@patch("azure_local_deploy.environment_checker.run_powershell")
def test_uninstall_failure_is_nonfatal(mock_ps):
    mock_ps.side_effect = RuntimeError("SSH failed")
    # Should not raise — uninstall failure is non-fatal
    uninstall_environment_checker("10.0.0.1", "admin", "pass")


# ---- run_validator --------------------------------------------------------

@patch("azure_local_deploy.environment_checker.run_powershell")
def test_run_validator_success(mock_ps):
    items = [{"Status": "Succeeded", "Severity": "Informational", "Name": "DNS"}]
    mock_ps.return_value = json.dumps(items)
    result = run_validator("10.0.0.1", "admin", "pass", "Connectivity")
    assert result.status == "Succeeded"
    assert result.name == "Connectivity"


@patch("azure_local_deploy.environment_checker.run_powershell")
def test_run_validator_unknown_name(mock_ps):
    result = run_validator("10.0.0.1", "admin", "pass", "FakeValidator")
    assert result.status == "Error"
    assert "Unknown validator" in result.error


@patch("azure_local_deploy.environment_checker.run_powershell")
def test_run_validator_ssh_error(mock_ps):
    mock_ps.side_effect = RuntimeError("Connection refused")
    result = run_validator("10.0.0.1", "admin", "pass", "Connectivity")
    assert result.status == "Error"
    assert "Connection refused" in result.error


# ---- run_environment_checker (full per-node flow) -------------------------

@patch("azure_local_deploy.environment_checker.uninstall_environment_checker")
@patch("azure_local_deploy.environment_checker.run_validator")
@patch("azure_local_deploy.environment_checker.install_environment_checker")
def test_run_environment_checker_all_pass(mock_install, mock_run, mock_uninstall):
    mock_install.return_value = "1.0.0"
    mock_run.return_value = ValidatorResult(name="Test", status="Succeeded", passed=5)

    report = run_environment_checker("10.0.0.1", "admin", "pass")
    assert report.overall_status == "Passed"
    assert mock_install.call_count == 1
    assert mock_run.call_count == len(VALIDATOR_CMDLETS)
    assert mock_uninstall.call_count == 1


@patch("azure_local_deploy.environment_checker.uninstall_environment_checker")
@patch("azure_local_deploy.environment_checker.run_validator")
@patch("azure_local_deploy.environment_checker.install_environment_checker")
def test_run_environment_checker_with_critical(mock_install, mock_run, mock_uninstall):
    mock_install.return_value = "1.0.0"
    mock_run.return_value = ValidatorResult(name="Test", status="Failed", critical=2)

    report = run_environment_checker("10.0.0.1", "admin", "pass")
    assert report.overall_status == "Failed"
    assert report.critical_count > 0


@patch("azure_local_deploy.environment_checker.install_environment_checker")
def test_run_environment_checker_install_fails(mock_install):
    mock_install.side_effect = RuntimeError("Install failed")

    report = run_environment_checker("10.0.0.1", "admin", "pass")
    assert report.overall_status == "Error"


@patch("azure_local_deploy.environment_checker.uninstall_environment_checker")
@patch("azure_local_deploy.environment_checker.run_validator")
@patch("azure_local_deploy.environment_checker.install_environment_checker")
def test_run_environment_checker_subset_validators(mock_install, mock_run, mock_uninstall):
    mock_install.return_value = "1.0.0"
    mock_run.return_value = ValidatorResult(name="Test", status="Succeeded", passed=3)

    report = run_environment_checker(
        "10.0.0.1", "admin", "pass",
        validators=["Connectivity", "Hardware"],
    )
    assert mock_run.call_count == 2  # only 2 validators


@patch("azure_local_deploy.environment_checker.uninstall_environment_checker")
@patch("azure_local_deploy.environment_checker.run_validator")
@patch("azure_local_deploy.environment_checker.install_environment_checker")
def test_run_environment_checker_no_uninstall(mock_install, mock_run, mock_uninstall):
    mock_install.return_value = "1.0.0"
    mock_run.return_value = ValidatorResult(name="Test", status="Succeeded", passed=1)

    run_environment_checker("10.0.0.1", "admin", "pass", auto_uninstall=False)
    mock_uninstall.assert_not_called()


# ---- run_environment_checker_all_nodes ------------------------------------

@patch("azure_local_deploy.environment_checker.run_environment_checker")
def test_all_nodes_success(mock_checker):
    mock_checker.return_value = EnvironmentCheckReport(
        host="10.0.0.1", overall_status="Passed"
    )
    servers = [
        {"host_ip": "10.0.0.1", "host_user": "admin", "host_password": "pw", "idrac_host": "10.0.0.1", "idrac_user": "root", "idrac_password": "pw"},
        {"host_ip": "10.0.0.2", "host_user": "admin", "host_password": "pw", "idrac_host": "10.0.0.2", "idrac_user": "root", "idrac_password": "pw"},
    ]
    reports = run_environment_checker_all_nodes(servers, abort_on_failure=False)
    assert len(reports) == 2
    assert all(r.ok for r in reports)


@patch("azure_local_deploy.environment_checker.run_environment_checker")
def test_all_nodes_abort_on_failure(mock_checker):
    mock_checker.return_value = EnvironmentCheckReport(
        host="10.0.0.1", overall_status="Failed",
        validators=[ValidatorResult(name="HW", status="Failed", critical=1)],
    )
    servers = [
        {"host_ip": "10.0.0.1", "host_user": "admin", "host_password": "pw", "idrac_host": "10.0.0.1", "idrac_user": "root", "idrac_password": "pw"},
    ]
    with pytest.raises(RuntimeError, match="critical issues"):
        run_environment_checker_all_nodes(servers, abort_on_failure=True)


@patch("azure_local_deploy.environment_checker.run_environment_checker")
def test_all_nodes_skip_no_host_ip(mock_checker):
    servers = [
        {"idrac_host": "10.0.0.1", "idrac_user": "root", "idrac_password": "pw"},  # no host_ip
    ]
    reports = run_environment_checker_all_nodes(servers, abort_on_failure=False)
    assert len(reports) == 0
    mock_checker.assert_not_called()
