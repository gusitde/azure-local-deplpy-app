"""Provision an Azure Storage Account for the cluster cloud witness.

Multi-node Azure Local clusters require a cloud witness (quorum witness)
backed by an Azure Storage Account. Each cluster should use its own
storage account — reusing accounts across clusters is not supported.

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deploy-via-portal
"""

from __future__ import annotations

import time
from typing import Any, Callable

from azure.identity import DefaultAzureCredential
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.storage.models import (
    StorageAccountCreateParameters,
    Sku as StorageSku,
    Kind,
)

from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def provision_cloud_witness(
    *,
    subscription_id: str,
    resource_group: str,
    account_name: str,
    region: str,
    sku_name: str = "Standard_LRS",
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create an Azure Storage Account for the cluster cloud witness.

    Parameters
    ----------
    subscription_id / resource_group / account_name / region:
        Azure resource coordinates.
    sku_name:
        Storage SKU (default: Standard_LRS for locally redundant).
    progress_callback:
        Optional callable for progress messages.

    Returns
    -------
    dict with ``account_name``, ``resource_id``, ``primary_endpoints``.
    """
    _cb = progress_callback or (lambda msg: None)

    log.info("[bold]== Provision Cloud Witness Storage Account ==[/]")
    _cb(f"Creating storage account '{account_name}' for cloud witness")

    credential = DefaultAzureCredential()
    storage_client = StorageManagementClient(credential, subscription_id)

    # Check if account already exists
    existing = check_cloud_witness_exists(subscription_id, resource_group, account_name)
    if existing:
        log.info("Storage account [cyan]%s[/] already exists", account_name)
        _cb(f"Storage account '{account_name}' already exists – reusing ✔")
        return existing

    params = StorageAccountCreateParameters(
        sku=StorageSku(name=sku_name),
        kind=Kind.STORAGE_V2,
        location=region,
        tags={
            "purpose": "azure-local-cloud-witness",
            "created-by": "azure-local-deploy",
        },
    )

    _cb("Creating storage account …")
    log.info("Creating storage account [cyan]%s[/] …", account_name)

    poller = storage_client.storage_accounts.begin_create(
        resource_group_name=resource_group,
        account_name=account_name,
        parameters=params,
    )
    account = poller.result()

    # Retrieve access key for witness configuration
    keys = storage_client.storage_accounts.list_keys(resource_group, account_name)
    primary_key = keys.keys[0].value if keys.keys else ""

    result = {
        "account_name": account_name,
        "resource_id": account.id,
        "primary_endpoints": {
            "blob": account.primary_endpoints.blob,
        },
        "access_key": primary_key,
    }

    log.info("[bold green]Cloud witness storage account created:[/] %s", account_name)
    _cb(f"Cloud witness storage account created: {account_name} ✔")

    return result


def configure_cluster_witness(
    host: str,
    user: str,
    password: str,
    *,
    storage_account_name: str,
    storage_account_key: str,
    port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Configure the cloud witness on an existing cluster node.

    Runs ``Set-ClusterQuorum`` on one of the cluster nodes.
    """
    from azure_local_deploy.remote import run_powershell

    _cb = progress_callback or (lambda msg: None)

    log.info("Configuring cloud witness on %s …", host)
    _cb(f"Setting cluster quorum witness (storage: {storage_account_name})")

    cmd = (
        f"Set-ClusterQuorum -CloudWitness "
        f"-AccountName '{storage_account_name}' "
        f"-AccessKey '{storage_account_key}' "
        f"-ErrorAction Stop"
    )

    run_powershell(host, user, password, cmd, port=port)

    log.info("[bold green]Cloud witness configured.[/]")
    _cb("Cloud witness configured ✔")


def check_cloud_witness_exists(
    subscription_id: str,
    resource_group: str,
    account_name: str,
) -> dict[str, Any] | None:
    """Check if the storage account already exists. Returns info dict or None."""
    credential = DefaultAzureCredential()
    storage_client = StorageManagementClient(credential, subscription_id)

    try:
        account = storage_client.storage_accounts.get_properties(resource_group, account_name)
        return {
            "account_name": account_name,
            "resource_id": account.id,
            "primary_endpoints": {
                "blob": account.primary_endpoints.blob,
            },
        }
    except Exception:
        return None
