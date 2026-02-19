"""Provision an Azure Key Vault for Azure Local deployment.

Azure Local requires a Key Vault to securely store:
    - BitLocker recovery keys
    - Local administrator credentials
    - Cryptographic keys

The Key Vault must NOT have Private Endpoints enabled.

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deploy-via-portal
"""

from __future__ import annotations

import time
from typing import Any, Callable

from azure.identity import DefaultAzureCredential
from azure.mgmt.keyvault import KeyVaultManagementClient
from azure.mgmt.keyvault.models import (
    VaultCreateOrUpdateParameters,
    VaultProperties,
    Sku,
    SkuFamily,
    SkuName,
    AccessPolicyEntry,
    Permissions,
    SecretPermissions,
    KeyPermissions,
)

from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def provision_keyvault(
    *,
    subscription_id: str,
    resource_group: str,
    vault_name: str,
    region: str,
    tenant_id: str,
    deployer_object_id: str = "",
    soft_delete_retention_days: int = 90,
    enable_public_network_access: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create or update an Azure Key Vault for Azure Local deployment.

    Parameters
    ----------
    subscription_id / resource_group / vault_name / region:
        Azure resource coordinates.
    tenant_id:
        Azure AD tenant ID.
    deployer_object_id:
        Object ID of the deploying user/SP for access policies.
        If empty, access policies are not set (use RBAC instead).
    soft_delete_retention_days:
        Days to retain deleted vaults (7–90, default 90).
    enable_public_network_access:
        Must be True for Azure Local (Private Endpoints not supported).
    progress_callback:
        Optional callable for progress messages.

    Returns
    -------
    dict with ``vault_name``, ``vault_uri``, ``resource_id``.
    """
    _cb = progress_callback or (lambda msg: None)

    log.info("[bold]== Provision Azure Key Vault ==[/]")
    _cb(f"Creating Key Vault '{vault_name}' in {resource_group}/{region}")

    credential = DefaultAzureCredential()
    kv_client = KeyVaultManagementClient(credential, subscription_id)

    # Build access policies if deployer object ID is provided
    access_policies: list[AccessPolicyEntry] = []
    if deployer_object_id:
        access_policies.append(
            AccessPolicyEntry(
                tenant_id=tenant_id,
                object_id=deployer_object_id,
                permissions=Permissions(
                    keys=[KeyPermissions.ALL],
                    secrets=[SecretPermissions.ALL],
                ),
            )
        )

    # Build vault parameters
    properties = VaultProperties(
        tenant_id=tenant_id,
        sku=Sku(family=SkuFamily.A, name=SkuName.STANDARD),
        access_policies=access_policies if access_policies else None,
        enabled_for_deployment=True,
        enabled_for_disk_encryption=True,
        enabled_for_template_deployment=True,
        soft_delete_retention_in_days=soft_delete_retention_days,
        enable_soft_delete=True,
        public_network_access="Enabled" if enable_public_network_access else "Disabled",
    )

    params = VaultCreateOrUpdateParameters(
        location=region,
        properties=properties,
    )

    _cb("Creating Key Vault resource …")
    log.info("Creating Key Vault [cyan]%s[/] …", vault_name)

    vault = kv_client.vaults.begin_create_or_update(
        resource_group_name=resource_group,
        vault_name=vault_name,
        parameters=params,
    ).result()

    vault_uri = vault.properties.vault_uri
    resource_id = vault.id

    log.info("[bold green]Key Vault created:[/] %s", vault_uri)
    _cb(f"Key Vault created: {vault_uri} ✔")

    return {
        "vault_name": vault_name,
        "vault_uri": vault_uri,
        "resource_id": resource_id,
    }


def check_keyvault_exists(
    subscription_id: str,
    resource_group: str,
    vault_name: str,
) -> dict[str, Any] | None:
    """Check if a Key Vault already exists. Returns info dict or None."""
    credential = DefaultAzureCredential()
    kv_client = KeyVaultManagementClient(credential, subscription_id)

    try:
        vault = kv_client.vaults.get(resource_group, vault_name)
        return {
            "vault_name": vault_name,
            "vault_uri": vault.properties.vault_uri,
            "resource_id": vault.id,
            "provisioning_state": getattr(vault.properties, "provisioning_state", "Unknown"),
        }
    except Exception:
        return None
