"""Validate Azure RBAC permissions required for Azure Local deployment.

Checks that the deploying user/service principal has the required roles
at both subscription and resource group levels.

Required roles per Microsoft documentation:
    Subscription level:
        - Azure Stack HCI Administrator
        - Reader
    Resource group level:
        - Key Vault Data Access Administrator
        - Key Vault Secrets Officer
        - Key Vault Contributor
        - Storage Account Contributor

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-arc-register-server-permissions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from azure_local_deploy.azure_auth import get_credential
from azure.mgmt.authorization import AuthorizationManagementClient

from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Required roles
# ---------------------------------------------------------------------------

class RoleScope(str, Enum):
    SUBSCRIPTION = "subscription"
    RESOURCE_GROUP = "resource_group"


@dataclass
class RequiredRole:
    """A role required for Azure Local deployment."""
    display_name: str
    scope: RoleScope
    role_definition_name: str  # Azure built-in role name
    critical: bool = True      # If True, deployment will fail without it


# Subscription-level roles
SUBSCRIPTION_ROLES: list[RequiredRole] = [
    RequiredRole("Azure Stack HCI Administrator", RoleScope.SUBSCRIPTION, "Azure Stack HCI Administrator"),
    RequiredRole("Reader", RoleScope.SUBSCRIPTION, "Reader"),
]

# Resource-group-level roles
RESOURCE_GROUP_ROLES: list[RequiredRole] = [
    RequiredRole("Key Vault Data Access Administrator", RoleScope.RESOURCE_GROUP, "Key Vault Data Access Administrator"),
    RequiredRole("Key Vault Secrets Officer", RoleScope.RESOURCE_GROUP, "Key Vault Secrets Officer"),
    RequiredRole("Key Vault Contributor", RoleScope.RESOURCE_GROUP, "Key Vault Contributor"),
    RequiredRole("Storage Account Contributor", RoleScope.RESOURCE_GROUP, "Storage Account Contributor"),
]

# Arc-registration roles (on the resource group)
ARC_REGISTRATION_ROLES: list[RequiredRole] = [
    RequiredRole("Azure Connected Machine Onboarding", RoleScope.RESOURCE_GROUP, "Azure Connected Machine Onboarding"),
    RequiredRole("Azure Connected Machine Resource Administrator", RoleScope.RESOURCE_GROUP, "Azure Connected Machine Resource Administrator"),
]

# Add-node specific roles (per node)
ADD_NODE_ROLES: list[RequiredRole] = [
    RequiredRole("Azure Stack HCI Device Management Role", RoleScope.RESOURCE_GROUP,
                 "Azure Stack HCI Device Management Role", critical=True),
    RequiredRole("Key Vault Secrets User", RoleScope.RESOURCE_GROUP,
                 "Key Vault Secrets User", critical=True),
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PermissionCheck:
    """Result of checking a single role."""
    role_name: str
    scope: str
    found: bool
    message: str = ""


@dataclass
class PermissionReport:
    """Aggregated permission validation report."""
    checks: list[PermissionCheck] = field(default_factory=list)
    passed: int = 0
    missing_critical: int = 0
    missing_optional: int = 0

    @property
    def ok(self) -> bool:
        return self.missing_critical == 0

    def add(self, check: PermissionCheck, critical: bool = True) -> None:
        self.checks.append(check)
        if check.found:
            self.passed += 1
        elif critical:
            self.missing_critical += 1
        else:
            self.missing_optional += 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_permissions(
    subscription_id: str,
    resource_group: str,
    *,
    include_arc_roles: bool = True,
    include_add_node_roles: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> PermissionReport:
    """Validate that the current identity has required Azure RBAC roles.

    Parameters
    ----------
    subscription_id:
        Azure subscription ID.
    resource_group:
        Target resource group name.
    include_arc_roles:
        Also check Arc registration roles (default: True).
    include_add_node_roles:
        Also check add-node specific roles (default: False).
    progress_callback:
        Optional callable for progress messages.

    Returns
    -------
    PermissionReport
    """
    _cb = progress_callback or (lambda msg: None)
    report = PermissionReport()

    log.info("[bold]== Validate Azure RBAC Permissions ==[/]")
    _cb("Validating Azure RBAC permissions …")

    credential = get_credential()
    auth_client = AuthorizationManagementClient(credential, subscription_id)

    # Build scope strings
    sub_scope = f"/subscriptions/{subscription_id}"
    rg_scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"

    # Get all role assignments for the subscription + resource group
    _cb("Fetching role assignments …")
    sub_assignments = _get_role_assignments(auth_client, sub_scope)
    rg_assignments = _get_role_assignments(auth_client, rg_scope)

    # Also get role definitions to map IDs to names
    _cb("Resolving role definitions …")
    role_defs = _get_role_definitions(auth_client, sub_scope)

    # Check subscription-level roles
    _cb("Checking subscription-level roles …")
    for role in SUBSCRIPTION_ROLES:
        found = _check_role(role.role_definition_name, sub_scope, sub_assignments, role_defs)
        check = PermissionCheck(
            role_name=role.display_name,
            scope="subscription",
            found=found,
            message="Found ✔" if found else "MISSING – required for deployment",
        )
        report.add(check, critical=role.critical)
        status = "✔" if found else "✘ MISSING"
        log.info("  [%s] %s (subscription) – %s",
                 "green" if found else "red", role.display_name, status)

    # Check resource-group-level roles
    _cb("Checking resource-group-level roles …")
    for role in RESOURCE_GROUP_ROLES:
        found = _check_role(role.role_definition_name, rg_scope, rg_assignments, role_defs)
        # Also check if granted at subscription level (inherited)
        if not found:
            found = _check_role(role.role_definition_name, sub_scope, sub_assignments, role_defs)
        check = PermissionCheck(
            role_name=role.display_name,
            scope="resource_group",
            found=found,
            message="Found ✔" if found else "MISSING – required for deployment",
        )
        report.add(check, critical=role.critical)
        status = "✔" if found else "✘ MISSING"
        log.info("  [%s] %s (resource group) – %s",
                 "green" if found else "red", role.display_name, status)

    # Check Arc registration roles
    if include_arc_roles:
        _cb("Checking Arc registration roles …")
        for role in ARC_REGISTRATION_ROLES:
            found = _check_role(role.role_definition_name, rg_scope, rg_assignments, role_defs)
            if not found:
                found = _check_role(role.role_definition_name, sub_scope, sub_assignments, role_defs)
            check = PermissionCheck(
                role_name=role.display_name,
                scope="resource_group",
                found=found,
                message="Found ✔" if found else "MISSING – needed for Arc registration",
            )
            report.add(check, critical=role.critical)

    # Check add-node roles
    if include_add_node_roles:
        _cb("Checking add-node roles …")
        for role in ADD_NODE_ROLES:
            found = _check_role(role.role_definition_name, rg_scope, rg_assignments, role_defs)
            if not found:
                found = _check_role(role.role_definition_name, sub_scope, sub_assignments, role_defs)
            check = PermissionCheck(
                role_name=role.display_name,
                scope="resource_group",
                found=found,
                message="Found ✔" if found else "MISSING – needed for add-node",
            )
            report.add(check, critical=role.critical)

    # Summary
    summary = (
        f"Permission check: {report.passed} found, "
        f"{report.missing_critical} critical missing, "
        f"{report.missing_optional} optional missing"
    )
    log.info("[bold]%s[/]", summary)
    _cb(summary)

    if not report.ok:
        log.warning(
            "[bold red]Missing critical permissions – deployment will likely fail.[/]\n"
            "Assign the missing roles in the Azure portal under Access Control (IAM)."
        )

    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_role_assignments(
    client: AuthorizationManagementClient,
    scope: str,
) -> list[Any]:
    """List role assignments at the given scope."""
    try:
        return list(client.role_assignments.list_for_scope(scope))
    except Exception as exc:
        log.warning("Could not list role assignments for %s: %s", scope, exc)
        return []


def _get_role_definitions(
    client: AuthorizationManagementClient,
    scope: str,
) -> dict[str, str]:
    """Build a map of role definition ID → role name."""
    role_map: dict[str, str] = {}
    try:
        for rd in client.role_definitions.list(scope):
            if rd.id and rd.role_name:
                role_map[rd.id] = rd.role_name
    except Exception as exc:
        log.warning("Could not list role definitions: %s", exc)
    return role_map


def _check_role(
    role_name: str,
    scope: str,
    assignments: list[Any],
    role_defs: dict[str, str],
) -> bool:
    """Check if *role_name* is assigned at *scope*."""
    for assignment in assignments:
        rd_id = getattr(assignment, "role_definition_id", "")
        if rd_id in role_defs:
            if role_defs[rd_id] == role_name:
                return True
        # Fallback: check if the role definition id contains the role name
        if role_name.lower().replace(" ", "") in rd_id.lower().replace(" ", ""):
            return True
    return False
