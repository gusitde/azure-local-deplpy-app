"""Unit tests for config loading and validation."""

import pytest
import yaml
import tempfile
from pathlib import Path

from azure_local_deploy.orchestrator import load_config


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    f = tmp_path / "config.yaml"
    f.write_text(yaml.dump(data), encoding="utf-8")
    return f


def test_load_valid_config(tmp_path):
    cfg = {
        "azure": {
            "tenant_id": "t",
            "subscription_id": "s",
            "resource_group": "rg",
            "region": "eastus",
        },
        "servers": [
            {
                "idrac_host": "10.0.0.1",
                "idrac_user": "root",
                "idrac_password": "pass",
                "host_ip": "10.0.1.1",
            }
        ],
    }
    p = _write_yaml(tmp_path, cfg)
    result = load_config(p)
    assert len(result["servers"]) == 1
    assert result["azure"]["region"] == "eastus"


def test_load_missing_azure_keys(tmp_path):
    cfg = {
        "azure": {"tenant_id": "t"},
        "servers": [{"idrac_host": "x", "idrac_user": "u", "idrac_password": "p", "host_ip": "h"}],
    }
    p = _write_yaml(tmp_path, cfg)
    with pytest.raises(ValueError, match="Missing required config keys"):
        load_config(p)


def test_load_missing_servers(tmp_path):
    cfg = {
        "azure": {
            "tenant_id": "t",
            "subscription_id": "s",
            "resource_group": "rg",
            "region": "eastus",
        },
    }
    p = _write_yaml(tmp_path, cfg)
    with pytest.raises(ValueError, match="Missing required config keys"):
        load_config(p)


def test_load_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path.yaml")
