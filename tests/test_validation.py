"""Unit tests for validate_nodes module."""

import pytest
from unittest.mock import patch, MagicMock

from azure_local_deploy.validate_nodes import (
    Severity,
    CheckResult,
    ValidationReport,
    _check_cpu,
    _check_memory,
    _check_boot_mode,
    _check_secure_boot,
    _check_virtualisation,
    _check_tpm,
    _check_sriov,
    _check_bios_compliance,
    _check_power_state,
)


# ---- CheckResult / ValidationReport data types ---------------------------

def test_check_result_creation():
    r = CheckResult(name="Test", severity=Severity.PASS, message="OK")
    assert r.name == "Test"
    assert r.severity == Severity.PASS
    assert r.detail == ""


def test_validation_report_add_pass():
    report = ValidationReport(host="10.0.0.1")
    report.add(CheckResult("A", Severity.PASS, "Good"))
    assert report.passed == 1
    assert report.warnings == 0
    assert report.failures == 0
    assert report.ok is True


def test_validation_report_add_fail():
    report = ValidationReport(host="10.0.0.1")
    report.add(CheckResult("A", Severity.FAIL, "Bad"))
    assert report.failures == 1
    assert report.ok is False


def test_validation_report_add_warn():
    report = ValidationReport(host="10.0.0.1")
    report.add(CheckResult("A", Severity.WARN, "Maybe"))
    assert report.warnings == 1
    assert report.ok is True  # warnings don't fail


def test_validation_report_mixed():
    report = ValidationReport(host="10.0.0.1")
    report.add(CheckResult("A", Severity.PASS, "OK"))
    report.add(CheckResult("B", Severity.WARN, "Hmm"))
    report.add(CheckResult("C", Severity.FAIL, "Bad"))
    assert report.passed == 1
    assert report.warnings == 1
    assert report.failures == 1
    assert report.ok is False
    assert len(report.checks) == 3


# ---- _check_cpu -----------------------------------------------------------

def test_check_cpu_intel_xeon():
    system = {"ProcessorSummary": {"Count": 2, "Model": "Intel(R) Xeon(R) Gold 6338"}}
    results = _check_cpu(system)
    assert any(r.severity == Severity.PASS and "2 processor" in r.message for r in results)
    assert any(r.severity == Severity.PASS and "64-bit" in r.message for r in results)


def test_check_cpu_amd_epyc():
    system = {"ProcessorSummary": {"Count": 1, "Model": "AMD EPYC 7763"}}
    results = _check_cpu(system)
    assert all(r.severity == Severity.PASS for r in results)


def test_check_cpu_none_detected():
    system = {"ProcessorSummary": {"Count": 0, "Model": "Unknown"}}
    results = _check_cpu(system)
    assert any(r.severity == Severity.FAIL for r in results)


def test_check_cpu_unknown_model():
    system = {"ProcessorSummary": {"Count": 1, "Model": "AcmeCPU-3000"}}
    results = _check_cpu(system)
    assert any(r.severity == Severity.WARN for r in results)


# ---- _check_memory --------------------------------------------------------

def test_check_memory_sufficient():
    system = {"MemorySummary": {"TotalSystemMemoryGiB": 256, "Status": {"Health": "OK"}}}
    r = _check_memory(system)
    assert r.severity == Severity.PASS
    assert "256 GB" in r.message


def test_check_memory_minimum():
    system = {"MemorySummary": {"TotalSystemMemoryGiB": 32, "Status": {"Health": "OK"}}}
    r = _check_memory(system)
    assert r.severity == Severity.PASS


def test_check_memory_insufficient():
    system = {"MemorySummary": {"TotalSystemMemoryGiB": 16, "Status": {"Health": "OK"}}}
    r = _check_memory(system)
    assert r.severity == Severity.FAIL


# ---- _check_boot_mode ----------------------------------------------------

def test_check_boot_mode_uefi():
    r = _check_boot_mode({"BootMode": "Uefi"})
    assert r.severity == Severity.PASS


def test_check_boot_mode_bios():
    r = _check_boot_mode({"BootMode": "Bios"})
    assert r.severity == Severity.FAIL


def test_check_boot_mode_missing():
    r = _check_boot_mode({})
    assert r.severity == Severity.FAIL


# ---- _check_secure_boot --------------------------------------------------

def test_check_secure_boot_enabled():
    r = _check_secure_boot({"SecureBoot": "Enabled"})
    assert r.severity == Severity.PASS


def test_check_secure_boot_disabled():
    r = _check_secure_boot({"SecureBoot": "Disabled"})
    assert r.severity == Severity.FAIL


# ---- _check_virtualisation -----------------------------------------------

def test_check_virtualisation_enabled():
    r = _check_virtualisation({"ProcVirtualization": "Enabled"})
    assert r.severity == Severity.PASS


def test_check_virtualisation_disabled():
    r = _check_virtualisation({"ProcVirtualization": "Disabled"})
    assert r.severity == Severity.FAIL


# ---- _check_tpm ----------------------------------------------------------

def test_check_tpm_enabled():
    r = _check_tpm({"TpmSecurity": "OnPbm"})
    assert r.severity == Severity.PASS


def test_check_tpm_on():
    r = _check_tpm({"TpmSecurity": "On"})
    assert r.severity == Severity.PASS


def test_check_tpm_disabled():
    r = _check_tpm({"TpmSecurity": "Off"})
    assert r.severity == Severity.FAIL


# ---- _check_sriov --------------------------------------------------------

def test_check_sriov_enabled():
    r = _check_sriov({"SriovGlobalEnable": "Enabled"})
    assert r.severity == Severity.PASS


def test_check_sriov_disabled():
    r = _check_sriov({"SriovGlobalEnable": "Disabled"})
    assert r.severity == Severity.WARN  # SR-IOV is recommended, not required


# ---- _check_bios_compliance ----------------------------------------------

def test_check_bios_compliance_all_ok():
    """BIOS attributes that all match Azure Local defaults."""
    bios = {
        "ProcVirtualization": "Enabled",
        "ProcVtd": "Enabled",
        "SriovGlobalEnable": "Enabled",
        "SecureBoot": "Enabled",
        "BootMode": "Uefi",
        "TpmSecurity": "OnPbm",
        "MemOpMode": "OptimizerMode",
        "LogicalProc": "Enabled",
    }
    results = _check_bios_compliance(bios)
    assert all(r.severity == Severity.PASS for r in results)


def test_check_bios_compliance_some_mismatch():
    bios = {
        "ProcVirtualization": "Disabled",
        "BootMode": "Bios",
    }
    results = _check_bios_compliance(bios)
    # Should have at least some warns for mismatched settings
    failures_or_warns = [r for r in results if r.severity in (Severity.FAIL, Severity.WARN)]
    assert len(failures_or_warns) > 0


# ---- _check_power_state --------------------------------------------------

def test_check_power_state_on():
    system = {"PowerState": "On"}
    r = _check_power_state(system, "10.0.0.1")
    assert r.severity == Severity.PASS


def test_check_power_state_off():
    system = {"PowerState": "Off"}
    r = _check_power_state(system, "10.0.0.1")
    assert r.severity == Severity.WARN
