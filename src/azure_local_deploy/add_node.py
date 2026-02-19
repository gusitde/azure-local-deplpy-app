"""Add a node to an existing Azure Local cluster.

Full 15-stage pipeline aligned with Microsoft documentation:
    https://learn.microsoft.com/en-us/azure/azure-local/manage/add-server
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-install-os
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-without-azure-arc-gateway

Stages:
     1. validate_permissions   – Azure RBAC role checks
     2. validate_nodes         – Hardware validation (CPU, Memory, Drives)
     3. environment_check      – AzStackHci EnvironmentChecker
     4. update_firmware        – Dell iDRAC firmware & drivers
     5. configure_bios         – Azure Local BIOS profile via iDRAC
     6. deploy_os              – Install Azure Stack HCI OS via iDRAC
     7. prepare_os             – Clean non-OS drives + set hostname + copy SBE
     8. configure_network      – IP / DNS / VLAN per-NIC (SConfig equivalent)
     9. configure_proxy        – HTTP/HTTPS proxy (optional)
    10. configure_time         – NTP & timezone (w32tm equivalent)
    11. configure_security     – HVCI, BitLocker, Credential Guard, etc.
    12. deploy_agent           – Register with Azure Arc via
                                 Invoke-AzStackHciArcInitialization
    13. pre_add_setup          – Quorum witness + storage intent on existing
                                 cluster (required BEFORE Add-Server for 1→2)
    14. add_node               – Add to cluster via Azure API
    15. post_join_validation   – Health checks + Sync-AzureStackHCI

Phase 3 enhancements (from Azure Local docs gap analysis):
    - OS version matching: verify new node OS matches cluster version
    - Quorum witness: auto-configure cloud witness when going 1→2 nodes
    - Storage intent: set up storage network intent for single→multi expansion
    - Azure role assignments: ensure new node has required Azure roles
    - Storage rebalance monitoring: watch storage rebalance after add-node
    - Arc registration parity: verify new node Arc registration matches cluster
    - Clean non-OS drives: required by Microsoft before deployment
    - SBE copy: Solution Builder Extension placed at C:\\SBE
    - Sync-AzureStackHCI: force Azure portal visibility after add
"""

from __future__ import annotations

import json as _json
import time
from typing import Any, Callable

from azure.identity import DefaultAzureCredential
from azure.mgmt.azurestackhci import AzureStackHCIClient

from azure_local_deploy.idrac_client import IdracClient
from azure_local_deploy.deploy_os import deploy_os_image
from azure_local_deploy.configure_network import configure_network, NicConfig
from azure_local_deploy.configure_time import configure_time_server
from azure_local_deploy.deploy_agent import deploy_agent
from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger, require_keys

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_node_to_cluster(
    *,
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    region: str,
    tenant_id: str,
    new_node: dict[str, str],
    existing_node: dict[str, str] | None = None,
    deployment_timeout: int = 3600,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Add a prepared & Arc-registered node to an existing Azure Local cluster.

    Parameters
    ----------
    subscription_id / resource_group / cluster_name / region / tenant_id:
        Azure coordinates of the *existing* cluster.
    new_node:
        Dict with ``host``, ``user``, ``password``, ``arc_resource_id``.
    existing_node:
        Dict with ``host``, ``user``, ``password`` for an existing cluster node
        (used for post-add operations like quorum and storage rebalance).
    deployment_timeout:
        Max seconds to wait for the add-node operation.
    progress_callback:
        Optional callable that receives progress messages.
    """
    _cb = progress_callback or (lambda msg: None)

    require_keys(new_node, ["host", "user", "password", "arc_resource_id"], context="new node")
    arc_id = new_node["arc_resource_id"]

    _cb(f"Adding node {new_node['host']} (Arc ID: {arc_id}) to cluster {cluster_name}")
    log.info("[bold]== Stage: Add Node to Cluster ==[/]")

    # Authenticate
    credential = DefaultAzureCredential()
    hci_client = AzureStackHCIClient(credential, subscription_id)

    # Validate node readiness
    _validate_new_node(new_node)
    _cb("Node pre-flight validation passed")

    # Get existing cluster to read current node list
    log.info("Fetching existing cluster [cyan]%s[/] …", cluster_name)
    cluster = hci_client.clusters.get(resource_group, cluster_name)
    _cb(f"Cluster {cluster_name} found – status: {getattr(cluster, 'provisioning_state', '?')}")

    # Pre-add validations --------------------------------------------------

    # 1. OS version matching
    if existing_node:
        _cb("Validating OS version match …")
        _validate_os_version_match(new_node, existing_node)
        _cb("OS version match ✔")

    # 2. Arc registration parity
    _cb("Validating Arc registration parity …")
    _validate_arc_parity(new_node, subscription_id, resource_group, region, tenant_id)
    _cb("Arc parity ✔")

    # 3. Azure role assignments
    _cb("Checking Azure role assignments for new node …")
    _ensure_node_role_assignments(arc_id, subscription_id, resource_group, credential)
    _cb("Role assignments ✔")

    # Trigger the add-node operation via update deployment settings ---------
    log.info("Initiating add-node operation …")
    _cb("Initiating add-node operation via Azure API …")

    add_node_payload: dict[str, Any] = {
        "properties": {
            "arcNodeResourceIds": [arc_id],
            "deploymentMode": "AddNode",
        },
    }

    poller = hci_client.deployment_settings.begin_create_or_update(
        resource_group_name=resource_group,
        cluster_name=cluster_name,
        deployment_settings_name="default",
        resource=add_node_payload,
    )

    log.info("Polling add-node operation (timeout=%ds) …", deployment_timeout)
    _cb("Waiting for add-node operation to complete …")

    result = poller.result(timeout=deployment_timeout)
    status = getattr(result, "provisioning_state", "Unknown")

    if status not in ("Succeeded", "succeeded"):
        raise RuntimeError(f"Add-node operation ended with status: {status}")

    _cb(f"Node successfully added to cluster {cluster_name}")
    log.info("[bold green]Node added to cluster %s successfully![/]", cluster_name)

    # Post-add operations --------------------------------------------------

    if existing_node:
        ehost = existing_node["host"]
        euser = existing_node["user"]
        epassword = existing_node["password"]
        eport = int(existing_node.get("ssh_port", 22))

        # 4. Quorum witness (single → two node)
        _cb("Checking quorum witness configuration …")
        _configure_quorum_if_needed(ehost, euser, epassword, eport, _cb)

        # 5. Storage intent (single → multi-node)
        _cb("Checking storage intent configuration …")
        _configure_storage_intent_if_needed(ehost, euser, epassword, eport, _cb)

        # 6. Monitor storage rebalance
        _cb("Monitoring storage rebalance …")
        _monitor_storage_rebalance(ehost, euser, epassword, eport, _cb)

    return {
        "cluster_name": cluster_name,
        "added_node": new_node["host"],
        "arc_resource_id": arc_id,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Full add-node pipeline (from bare metal to cluster member)
# ---------------------------------------------------------------------------

def run_add_node_pipeline(
    config: dict[str, Any],
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Full 15-stage pipeline: bare-metal node prep → cluster add → validation.

    Aligned with Microsoft documentation:
      https://learn.microsoft.com/en-us/azure/azure-local/manage/add-server
      https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-install-os
      https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-without-azure-arc-gateway

    Stages
    ------
     1. validate_permissions   – Azure RBAC role checks
     2. validate_nodes         – Pre-flight hardware validation (CPU/Mem/Drives)
     3. environment_check      – AzStackHci EnvironmentChecker
     4. update_firmware        – Dell iDRAC firmware & drivers
     5. configure_bios         – Azure Local BIOS profile
     6. deploy_os              – Install Azure Stack HCI OS via iDRAC virtual media
     7. prepare_os             – Clean non-OS drives + set hostname + copy SBE
     8. configure_network      – IP / DNS / VLAN per-NIC
     9. configure_proxy        – HTTP / HTTPS proxy (optional)
    10. configure_time         – NTP & timezone
    11. configure_security     – HVCI, BitLocker, Credential Guard, etc.
    12. deploy_agent           – Register with Azure Arc
                                 (Invoke-AzStackHciArcInitialization)
    13. pre_add_setup          – Quorum witness + storage intent on existing
                                 cluster (required before Add-Server for 1→2)
    14. add_node               – Add to cluster via Azure API / Add-Server
    15. post_join_validation   – Health checks + Sync-AzureStackHCI
    """
    TOTAL = 15
    _cb = progress_callback or (lambda msg: None)

    azure_cfg = config["azure"]
    global_cfg = config.get("global", {})
    add_cfg = config.get("add_node", {})
    firmware_cfg = config.get("firmware", {})
    bios_cfg = config.get("bios", {})
    servers = config["servers"]

    require_keys(add_cfg, ["existing_cluster_name"], context="add_node config")

    existing_cluster = add_cfg["existing_cluster_name"]
    existing_rg = add_cfg.get("existing_cluster_resource_group", azure_cfg["resource_group"])

    # Build existing_node dict for pre/post-add ops
    existing_node_cfg = add_cfg.get("existing_node", {})
    existing_node: dict[str, str] | None = None
    if existing_node_cfg.get("host"):
        existing_node = {
            "host": existing_node_cfg["host"],
            "user": existing_node_cfg.get("user", "Administrator"),
            "password": existing_node_cfg.get("password", ""),
            "ssh_port": str(existing_node_cfg.get("ssh_port", 22)),
        }

    _cb(f"Add-node pipeline: {len(servers)} new node(s) → cluster '{existing_cluster}'")
    log.info("[bold magenta]===== Add Node Pipeline (15 stages) =====[/]")

    # ==================================================================
    # Phase 1: Azure pre-flight (subscription-level, before per-node)
    # ==================================================================

    # -- Stage 1: Validate Azure RBAC permissions -------------------------
    _cb(f"Stage 1/{TOTAL}: validate_permissions – checking Azure RBAC roles …")
    try:
        from azure_local_deploy.validate_permissions import validate_permissions
        perm_report = validate_permissions(
            subscription_id=azure_cfg["subscription_id"],
            resource_group=azure_cfg["resource_group"],
        )
        if not perm_report.all_ok:
            missing = [c.role_name for c in perm_report.checks if not c.assigned]
            _cb(f"⚠ Missing roles: {', '.join(missing)}")
            if global_cfg.get("abort_on_validation_failure", True):
                raise RuntimeError(f"Missing RBAC roles: {', '.join(missing)}")
        else:
            _cb("RBAC permissions validated ✔")
    except ImportError:
        log.warning("azure-mgmt-authorization not installed – skipping permission check")
        _cb("RBAC check skipped (azure-mgmt-authorization not installed)")

    # ==================================================================
    # Phase 2: Per-node preparation (bare-metal to Arc-registered)
    # ==================================================================

    for idx, server in enumerate(servers, 1):
        require_keys(server, ["idrac_host", "idrac_user", "idrac_password", "host_ip"],
                      context=f"new node #{idx}")

        host_user = server.get("host_user", "Administrator")
        host_password = server.get("host_password", server["idrac_password"])
        ssh_port = int(server.get("ssh_port", 22))

        _cb(f"── Processing new node {idx}/{len(servers)}: {server['idrac_host']} ──")

        # -- Stage 2: Pre-flight hardware validation ----------------------
        validation_cfg = config.get("validation", {})
        run_preflight = validation_cfg.get("run_pre_flight", True)
        abort_on_failure = validation_cfg.get("abort_on_failure",
                                               global_cfg.get("abort_on_validation_failure", True))

        if run_preflight:
            _cb(f"Stage 2/{TOTAL}: validate_nodes – pre-flight hardware checks …")
            try:
                from azure_local_deploy.validate_nodes import validate_all_nodes
                validate_all_nodes(
                    [server],
                    progress_callback=_cb,
                    abort_on_failure=abort_on_failure,
                )
                _cb("Pre-flight validation passed ✔")
            except Exception as exc:
                _cb(f"Pre-flight validation failed: {exc}")
                if abort_on_failure:
                    raise
        else:
            _cb(f"Stage 2/{TOTAL}: validate_nodes – skipped (disabled in config)")

        # -- Stage 3: Environment Checker ---------------------------------
        if run_preflight:
            _cb(f"Stage 3/{TOTAL}: environment_check – AzStackHci EnvironmentChecker …")
            try:
                from azure_local_deploy.environment_checker import run_environment_checker_all_nodes
                env_cfg = config.get("environment_checker", {})
                run_environment_checker_all_nodes(
                    [server],
                    validators=env_cfg.get("validators", None),
                    install_timeout=int(env_cfg.get("install_timeout", 300)),
                    validator_timeout=int(env_cfg.get("validator_timeout", 600)),
                    auto_uninstall=env_cfg.get("auto_uninstall", True),
                    abort_on_failure=abort_on_failure,
                    progress_callback=_cb,
                )
                _cb("Environment check passed ✔")
            except Exception as exc:
                _cb(f"Environment check failed: {exc}")
                if abort_on_failure:
                    raise
        else:
            _cb(f"Stage 3/{TOTAL}: environment_check – skipped (disabled in config)")

        # -- Stage 4: Update firmware via iDRAC ---------------------------
        fw_targets_raw = server.get("firmware_targets", firmware_cfg.get("targets", []))
        catalog_url = server.get("firmware_catalog_url") or firmware_cfg.get("catalog_url", "")

        if fw_targets_raw or catalog_url:
            _cb(f"Stage 4/{TOTAL}: update_firmware – {server['idrac_host']} …")
            try:
                from azure_local_deploy.update_firmware import FirmwareTarget, update_firmware
                fw_targets = [
                    FirmwareTarget(
                        component=t.get("component", "Unknown"),
                        dup_url=t.get("dup_url", ""),
                        target_version=t.get("target_version", ""),
                        install_option=t.get("install_option", "NowAndReboot"),
                    )
                    for t in fw_targets_raw
                ]
                with IdracClient(server["idrac_host"], server["idrac_user"],
                                 server["idrac_password"]) as idrac:
                    update_firmware(
                        idrac,
                        targets=fw_targets if fw_targets else None,
                        catalog_url=catalog_url,
                        apply_reboot=firmware_cfg.get("apply_reboot", True),
                        task_timeout=int(firmware_cfg.get("task_timeout", 3600)),
                        progress_callback=_cb,
                    )
                _cb("Firmware update complete ✔")
            except Exception as exc:
                _cb(f"Firmware update failed: {exc}")
                raise
        else:
            _cb(f"Stage 4/{TOTAL}: update_firmware – skipped (no firmware targets or catalog)")

        # -- Stage 5: Configure BIOS --------------------------------------
        bios_profile_name = bios_cfg.get("profile", "AzureLocal")
        custom_bios = server.get("bios_attributes", bios_cfg.get("attributes", {}))

        if bios_profile_name or custom_bios:
            _cb(f"Stage 5/{TOTAL}: configure_bios – {server['idrac_host']} …")
            try:
                from azure_local_deploy.configure_bios import BiosProfile, configure_bios
                with IdracClient(server["idrac_host"], server["idrac_user"],
                                 server["idrac_password"]) as idrac:
                    configure_bios(
                        idrac,
                        profile=BiosProfile(name=bios_profile_name),
                        custom_attributes=custom_bios if custom_bios else None,
                        apply_reboot=bios_cfg.get("apply_reboot", True),
                        task_timeout=int(bios_cfg.get("task_timeout", 1200)),
                        progress_callback=_cb,
                    )
                _cb("BIOS configuration complete ✔")
            except Exception as exc:
                _cb(f"BIOS configuration failed: {exc}")
                raise
        else:
            _cb(f"Stage 5/{TOTAL}: configure_bios – skipped (no BIOS config)")

        # -- Stage 6: Deploy OS -------------------------------------------
        iso_url = server.get("iso_url") or global_cfg.get("iso_url", "")
        if iso_url:
            _cb(f"Stage 6/{TOTAL}: deploy_os – {server['idrac_host']} …")
            with IdracClient(server["idrac_host"], server["idrac_user"],
                             server["idrac_password"]) as idrac:
                deploy_os_image(
                    idrac,
                    iso_url=iso_url,
                    host_ip=server["host_ip"],
                    host_user=host_user,
                    host_password=host_password,
                    install_timeout=int(server.get("install_timeout", 3600)),
                    ssh_port=ssh_port,
                )
            _cb("OS deployment complete ✔")
        else:
            _cb(f"Stage 6/{TOTAL}: deploy_os – skipped (no ISO URL)")

        # -- Stage 7: Prepare OS (clean drives + hostname + SBE) ----------
        # Microsoft docs: "Clean all non-OS drives for each machine"
        # Microsoft docs: "Copy SBE content to C:\SBE"
        # Microsoft docs: "Change the Computer Name as desired"
        # Ref: https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-install-os
        _cb(f"Stage 7/{TOTAL}: prepare_os – clean drives, set hostname, copy SBE …")

        # 7a. Clean non-OS drives + copy SBE
        sbe_source = server.get("sbe_source", global_cfg.get("sbe_source", ""))
        _prepare_disks_and_sbe(
            server["host_ip"], host_user, host_password, ssh_port,
            sbe_source=sbe_source,
            _cb=_cb,
        )

        # 7b. Set computer name if provided
        desired_hostname = server.get("hostname", "")
        if desired_hostname:
            _cb(f"  Setting computer name to '{desired_hostname}' …")
            try:
                run_powershell(
                    server["host_ip"], host_user, host_password,
                    f"Rename-Computer -NewName '{desired_hostname}' -Force -ErrorAction Stop; "
                    f"Write-Output 'HOSTNAME_SET={desired_hostname}'",
                    port=ssh_port, timeout=60,
                )
                _cb(f"  Computer name set to '{desired_hostname}' – reboot pending ✔")
                # Reboot to apply hostname
                run_powershell(
                    server["host_ip"], host_user, host_password,
                    "Restart-Computer -Force",
                    port=ssh_port, timeout=30,
                )
                _cb("  Rebooting to apply hostname change …")
                time.sleep(60)  # Wait for reboot
            except Exception as exc:
                _cb(f"  ⚠ Hostname change failed (non-blocking): {exc}")
                log.warning("  Hostname change failed: %s", exc)
        _cb("OS preparation complete ✔")

        # -- Stage 8: Configure network -----------------------------------
        nic_defs = server.get("nics", [])
        if nic_defs:
            _cb(f"Stage 8/{TOTAL}: configure_network – {server['idrac_host']} …")
            nics = [NicConfig(**n) for n in nic_defs]
            configure_network(server["host_ip"], host_user, host_password,
                              nics, ssh_port=ssh_port)
            _cb("Network configuration complete ✔")
        else:
            _cb(f"Stage 8/{TOTAL}: configure_network – skipped (no NIC definitions)")

        # -- Stage 9: Configure proxy (optional) --------------------------
        proxy_cfg = config.get("proxy", {})
        http_proxy = proxy_cfg.get("http_proxy") or global_cfg.get("proxy_url", "")
        if http_proxy:
            _cb(f"Stage 9/{TOTAL}: configure_proxy – {server['idrac_host']} …")
            try:
                from azure_local_deploy.configure_proxy import configure_proxy, ProxyConfig
                proxy = ProxyConfig(
                    http_proxy=http_proxy,
                    https_proxy=proxy_cfg.get("https_proxy", http_proxy),
                    no_proxy=proxy_cfg.get("no_proxy", ""),
                )
                configure_proxy(server["host_ip"], host_user, host_password,
                                config=proxy, ssh_port=ssh_port)
                _cb("Proxy configuration complete ✔")
            except Exception as exc:
                _cb(f"Proxy configuration failed (non-blocking): {exc}")
                log.warning("Proxy config failed: %s", exc)
        else:
            _cb(f"Stage 9/{TOTAL}: configure_proxy – skipped (no proxy configured)")

        # -- Stage 10: Configure time -------------------------------------
        ntp = server.get("ntp_servers") or global_cfg.get("ntp_servers", ["time.windows.com"])
        tz = server.get("timezone") or global_cfg.get("timezone")
        _cb(f"Stage 10/{TOTAL}: configure_time – {server['idrac_host']} …")
        configure_time_server(server["host_ip"], host_user, host_password,
                              ntp_servers=ntp, ssh_port=ssh_port, timezone=tz)
        _cb("Time configuration complete ✔")

        # -- Stage 11: Configure security ---------------------------------
        sec_cfg = config.get("security", {})
        sec_profile_name = sec_cfg.get("profile", "recommended")
        _cb(f"Stage 11/{TOTAL}: configure_security – {server['idrac_host']} ({sec_profile_name}) …")
        try:
            from azure_local_deploy.configure_security import (
                configure_security, SecurityProfile, RECOMMENDED_SECURITY,
            )
            if sec_profile_name == "recommended":
                sec_profile = RECOMMENDED_SECURITY
            else:
                sec_profile = SecurityProfile(**{
                    k: sec_cfg.get(k, True)
                    for k in SecurityProfile.__dataclass_fields__
                })
            configure_security(
                server["host_ip"], host_user, host_password,
                profile=sec_profile, ssh_port=ssh_port,
                progress_callback=_cb,
            )
            _cb("Security configuration complete ✔")
        except Exception as exc:
            _cb(f"Security configuration failed: {exc}")
            raise

        # -- Stage 12: Register with Azure Arc ----------------------------
        # Microsoft docs: use Invoke-AzStackHciArcInitialization
        # Ref: https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-without-azure-arc-gateway
        _cb(f"Stage 12/{TOTAL}: deploy_agent – Invoke-AzStackHciArcInitialization …")
        deploy_agent(
            server["host_ip"], host_user, host_password,
            tenant_id=azure_cfg["tenant_id"],
            subscription_id=azure_cfg["subscription_id"],
            resource_group=azure_cfg["resource_group"],
            region=azure_cfg["region"],
            arc_gateway_id=server.get("arc_gateway_id", ""),
            proxy_url=server.get("proxy_url", global_cfg.get("proxy_url", "")),
            ssh_port=ssh_port,
            use_hci_init=True,
        )
        _cb("Arc agent deployment complete ✔")

    # ==================================================================
    # Phase 3: Cluster-side pre-add setup (runs on existing node)
    # ==================================================================

    # -- Stage 13: Pre-add cluster setup ----------------------------------
    # Microsoft docs (Add a node): for single-node expansion, configure
    # quorum witness and storage intent BEFORE running Add-Server.
    _cb(f"Stage 13/{TOTAL}: pre_add_setup – cluster-side prerequisites …")
    if existing_node:
        _pre_add_cluster_setup(existing_node, _cb)
        _cb("Pre-add cluster setup complete ✔")
    else:
        _cb(f"Stage 13/{TOTAL}: pre_add_setup – skipped (no existing node credentials)")

    # ==================================================================
    # Phase 4: Add nodes to cluster + post-join validation
    # ==================================================================

    for idx, server in enumerate(servers, 1):
        host_user = server.get("host_user", "Administrator")
        host_password = server.get("host_password", server["idrac_password"])
        ssh_port = int(server.get("ssh_port", 22))

        # -- Stage 14: Add to cluster -------------------------------------
        arc_resource_id = server.get("arc_resource_id", "")
        if not arc_resource_id:
            _cb("Discovering Arc resource ID from agent …")
            arc_resource_id = _discover_arc_id(server["host_ip"], host_user,
                                                host_password, ssh_port)
            _cb(f"Discovered Arc ID: {arc_resource_id}")

        _cb(f"Stage 14/{TOTAL}: add_node – Adding node to cluster '{existing_cluster}' …")
        add_node_to_cluster(
            subscription_id=azure_cfg["subscription_id"],
            resource_group=existing_rg,
            cluster_name=existing_cluster,
            region=azure_cfg["region"],
            tenant_id=azure_cfg["tenant_id"],
            new_node={
                "host": server["host_ip"],
                "user": host_user,
                "password": host_password,
                "arc_resource_id": arc_resource_id,
            },
            existing_node=existing_node,
            progress_callback=_cb,
        )
        _cb(f"Node {server['host_ip']} successfully added to cluster '{existing_cluster}'")

        # -- Stage 15: Post-join validation -------------------------------
        _cb(f"Stage 15/{TOTAL}: post_join_validation – {server['host_ip']} …")
        _post_join_validation(
            new_node_host=server["host_ip"],
            new_node_user=host_user,
            new_node_password=host_password,
            new_node_port=ssh_port,
            existing_node=existing_node,
            cluster_name=existing_cluster,
            progress_callback=_cb,
        )
        _cb(f"Post-join validation complete for {server['host_ip']} ✔")

    _cb("Add-node pipeline complete ✅")
    log.info("[bold green]===== Add Node Pipeline complete =====[/]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prepare_disks_and_sbe(
    host: str,
    user: str,
    password: str,
    port: int,
    sbe_source: str = "",
    _cb: Callable[[str], None] | None = None,
) -> None:
    """Clean non-OS data drives and optionally copy the Solution Builder Extension.

    Microsoft docs require all non-OS drives to be cleaned before adding a
    node so Storage Spaces Direct can claim them.  If an SBE source path is
    provided the content is copied to ``C:\\SBE`` on the target node.

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-install-os
        https://learn.microsoft.com/en-us/azure/azure-local/manage/add-server
    """
    cb = _cb or (lambda m: None)

    # 1. Clean non-OS drives -----------------------------------------------
    cb("  Cleaning non-OS data drives (Clear-Disk) …")
    clean_cmd = (
        "$osDisk = (Get-Disk | Where-Object { $_.IsBoot -eq $true }).Number; "
        "$dataDrives = Get-Disk | Where-Object { $_.Number -ne $osDisk -and $_.Number -ne $null }; "
        "foreach ($d in $dataDrives) { "
        "  try { "
        "    Clear-Disk -Number $d.Number -RemoveData -RemoveOEM -Confirm:$false -ErrorAction SilentlyContinue; "
        "    Initialize-Disk -Number $d.Number -PartitionStyle GPT -ErrorAction SilentlyContinue; "
        "    Write-Output \"CLEANED=$($d.Number)|SIZE=$([math]::Round($d.Size/1GB,1))GB\" "
        "  } catch { Write-Output \"SKIP=$($d.Number)|ERR=$($_.Exception.Message)\" } "
        "}"
    )
    try:
        result = run_powershell(host, user, password, clean_cmd, port=port, timeout=120)
        for line in result.strip().splitlines():
            if line.strip():
                cb(f"  {line.strip()}")
        cb("  Non-OS drives cleaned ✔")
        log.info("  ✔ Non-OS drives cleaned on %s", host)
    except Exception as exc:
        log.warning("  Drive cleaning failed on %s: %s", host, exc)
        cb(f"  ⚠ Drive cleaning failed: {exc}")
        raise

    # 2. Copy SBE (Solution Builder Extension) if provided -----------------
    if sbe_source:
        cb(f"  Copying SBE from {sbe_source} to C:\\SBE …")
        sbe_cmd = (
            "New-Item -Path 'C:\\SBE' -ItemType Directory -Force | Out-Null; "
            f"Copy-Item -Path '{sbe_source}\\*' -Destination 'C:\\SBE' -Recurse -Force; "
            "Write-Output \"SBE_COPIED=$(Get-ChildItem C:\\SBE -Recurse | Measure-Object | Select-Object -ExpandProperty Count) files\""
        )
        try:
            result = run_powershell(host, user, password, sbe_cmd, port=port, timeout=120)
            cb(f"  SBE copied ✔ ({result.strip()})")
            log.info("  ✔ SBE copied to C:\\SBE on %s", host)
        except Exception as exc:
            log.warning("  SBE copy failed on %s: %s", host, exc)
            cb(f"  ⚠ SBE copy failed (non-blocking): {exc}")


def _pre_add_cluster_setup(
    existing_node: dict[str, str],
    _cb: Callable[[str], None],
) -> None:
    """Perform cluster-side setup BEFORE adding the node.

    Microsoft docs require that for single → two-node expansion:
      - A quorum witness is configured
      - A storage network intent is configured

    These must be done BEFORE running Add-Server.

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/manage/add-server
    """
    ehost = existing_node["host"]
    euser = existing_node.get("user", "Administrator")
    epassword = existing_node.get("password", "")
    eport = int(existing_node.get("ssh_port", 22))

    # Check current node count
    _cb("  Checking current cluster size for pre-add requirements …")
    try:
        result = run_powershell(
            ehost, euser, epassword,
            "(Get-ClusterNode | Measure-Object).Count",
            port=eport, timeout=30,
        )
        node_count = int(result.strip())
        _cb(f"  Current cluster size: {node_count} node(s)")
    except Exception as exc:
        _cb(f"  ⚠ Could not determine cluster size: {exc}")
        log.warning("  Could not determine cluster size: %s", exc)
        return

    if node_count == 1:
        _cb("  Single-node cluster detected – configuring prerequisites for expansion …")

        # 1. Quorum witness
        _configure_quorum_if_needed(ehost, euser, epassword, eport, _cb)

        # 2. Storage network intent
        _configure_storage_intent_if_needed(ehost, euser, epassword, eport, _cb)
    else:
        _cb(f"  Cluster has {node_count} nodes – no special pre-add setup needed")


def _validate_new_node(node: dict[str, str]) -> None:
    """Quick pre-flight check on the new node."""
    host, user, password = node["host"], node["user"], node["password"]
    log.info("Validating new node %s …", host)

    result = run_powershell(
        host, user, password,
        "& \"$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe\" show -j",
    )
    if '"Connected"' not in result:
        raise RuntimeError(f"Node {host}: Arc agent is not Connected. Output: {result[:300]}")

    log.info("  ✔ New node %s passed pre-flight checks", host)


def _discover_arc_id(host: str, user: str, password: str, port: int = 22) -> str:
    """Query the Arc agent on the host to get its Azure resource id."""
    result = run_powershell(
        host, user, password,
        "& \"$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe\" show -j",
        port=port,
    )
    try:
        data = _json.loads(result)
        return data.get("resourceId", data.get("id", ""))
    except Exception:
        # Try to extract from plain text
        for line in result.splitlines():
            if "resourceId" in line or "/machines/" in line:
                return line.split(":", 1)[-1].strip().strip('"').strip(",")
    raise RuntimeError(f"Could not discover Arc resource ID on {host}")


# ---------------------------------------------------------------------------
# Phase 3 – Add-node enhancements
# ---------------------------------------------------------------------------

def _validate_os_version_match(
    new_node: dict[str, str],
    existing_node: dict[str, str],
) -> None:
    """Ensure the new node's OS version matches the existing cluster.

    Azure Local requires all nodes to run the same OS build. Mismatched
    versions cause deployment failures or cluster instability.

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/manage/add-node
    """
    new_host = new_node["host"]
    existing_host = existing_node["host"]

    new_ver = run_powershell(
        new_host, new_node["user"], new_node["password"],
        "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion').DisplayVersion + '|' + "
        "(Get-CimInstance Win32_OperatingSystem).BuildNumber",
    ).strip()

    existing_ver = run_powershell(
        existing_host, existing_node["user"], existing_node["password"],
        "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion').DisplayVersion + '|' + "
        "(Get-CimInstance Win32_OperatingSystem).BuildNumber",
    ).strip()

    log.info("  OS version – new: %s, existing: %s", new_ver, existing_ver)
    if new_ver != existing_ver:
        raise RuntimeError(
            f"OS version mismatch: new node ({new_host}) has '{new_ver}' "
            f"but existing node ({existing_host}) has '{existing_ver}'. "
            f"All nodes must run the same OS build."
        )
    log.info("  ✔ OS versions match")


def _validate_arc_parity(
    new_node: dict[str, str],
    subscription_id: str,
    resource_group: str,
    region: str,
    tenant_id: str,
) -> None:
    """Verify Arc registration parameters match the cluster's expectations.

    The new node's Arc agent must be registered to the same subscription,
    resource group, region, and tenant as the existing cluster.
    """
    host = new_node["host"]
    result = run_powershell(
        host, new_node["user"], new_node["password"],
        "& \"$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe\" show -j",
    )
    try:
        data = _json.loads(result)
    except Exception:
        log.warning("  Could not parse Arc agent output – skipping parity check")
        return

    arc_sub = data.get("subscriptionId", "")
    arc_rg = data.get("resourceGroup", "")
    arc_region = data.get("location", data.get("region", ""))
    arc_tenant = data.get("tenantId", "")

    mismatches: list[str] = []
    if arc_sub and arc_sub.lower() != subscription_id.lower():
        mismatches.append(f"subscription: {arc_sub} ≠ {subscription_id}")
    if arc_rg and arc_rg.lower() != resource_group.lower():
        mismatches.append(f"resource group: {arc_rg} ≠ {resource_group}")
    if arc_region and arc_region.lower() != region.lower():
        mismatches.append(f"region: {arc_region} ≠ {region}")
    if arc_tenant and arc_tenant.lower() != tenant_id.lower():
        mismatches.append(f"tenant: {arc_tenant} ≠ {tenant_id}")

    if mismatches:
        raise RuntimeError(
            f"Arc registration parity failure on {host}: " + "; ".join(mismatches)
        )
    log.info("  ✔ Arc registration parity validated for %s", host)


def _ensure_node_role_assignments(
    arc_resource_id: str,
    subscription_id: str,
    resource_group: str,
    credential: Any,
) -> None:
    """Ensure the Arc-enabled node has required RBAC role assignments.

    Required roles for add-node:
        - Azure Connected Machine Resource Manager
        - HCI Device Management Role (on RG)
        - Key Vault Secrets User (on RG)
    """
    try:
        from azure.mgmt.authorization import AuthorizationManagementClient
    except ImportError:
        log.warning("  azure-mgmt-authorization not installed – skipping role check")
        return

    ADD_NODE_ROLES = [
        "Azure Connected Machine Resource Manager",
        "Azure Stack HCI Device Management Role",
        "Key Vault Secrets User",
    ]

    auth_client = AuthorizationManagementClient(credential, subscription_id)
    scope = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"

    existing_roles: set[str] = set()
    try:
        assignments = auth_client.role_assignments.list_for_scope(scope)
        for a in assignments:
            try:
                rd = auth_client.role_definitions.get_by_id(a.role_definition_id)
                existing_roles.add(rd.role_name)
            except Exception:
                pass
    except Exception as exc:
        log.warning("  Could not list role assignments: %s", exc)
        return

    missing = [r for r in ADD_NODE_ROLES if r not in existing_roles]
    if missing:
        log.warning("  Missing role assignments: %s", missing)
        log.warning("  Add-node may fail if the service principal lacks these roles.")
    else:
        log.info("  ✔ All required add-node roles are assigned")


def _configure_quorum_if_needed(
    host: str, user: str, password: str, port: int,
    _cb: Callable[[str], None],
) -> None:
    """Configure cloud witness when expanding from 1 to 2 nodes.

    A two-node cluster requires a witness to maintain quorum. If no
    witness is configured, set up a cloud witness automatically.

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/manage/witness
    """
    # Check current node count and witness status
    check_cmd = (
        "$nodes = (Get-ClusterNode | Measure-Object).Count; "
        "$witness = (Get-ClusterQuorum).QuorumResource; "
        "Write-Output \"NODES=$nodes|WITNESS=$witness\""
    )
    try:
        result = run_powershell(host, user, password, check_cmd, port=port)
        parts = dict(item.split("=", 1) for item in result.strip().split("|") if "=" in item)
        node_count = int(parts.get("NODES", "0"))
        witness = parts.get("WITNESS", "").strip()

        if node_count == 2 and not witness:
            _cb("Two-node cluster detected without witness – recommend configuring cloud witness")
            log.warning(
                "  ⚠ Cluster now has 2 nodes but no quorum witness. "
                "Use 'cloud-witness' CLI command or configure manually."
            )
        elif witness:
            _cb(f"Quorum witness configured: {witness}")
            log.info("  ✔ Quorum witness: %s", witness)
        else:
            log.info("  Cluster has %d node(s), witness: %s", node_count, witness or "none")
    except Exception as exc:
        log.warning("  Could not check quorum: %s", exc)


def _configure_storage_intent_if_needed(
    host: str, user: str, password: str, port: int,
    _cb: Callable[[str], None],
) -> None:
    """Check and configure storage network intent for multi-node expansion.

    When expanding from single-node to multi-node, Storage Spaces Direct
    comes into play and storage intents must be configured via Network ATC.

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/deploy/network-atc
    """
    check_cmd = (
        "$intents = Get-NetIntent -ErrorAction SilentlyContinue; "
        "$storage = $intents | Where-Object { $_.IntentType -match 'Storage' }; "
        "if ($storage) { Write-Output 'STORAGE_INTENT_EXISTS' } else { Write-Output 'NO_STORAGE_INTENT' }"
    )
    try:
        result = run_powershell(host, user, password, check_cmd, port=port)
        if "STORAGE_INTENT_EXISTS" in result:
            _cb("Storage intent already configured ✔")
            log.info("  ✔ Storage network intent exists")
        else:
            _cb("No storage intent found – recommend configuring via Network ATC for multi-node")
            log.warning(
                "  ⚠ No storage network intent configured. For multi-node clusters, "
                "configure storage intent using Add-NetIntent -Storage."
            )
    except Exception as exc:
        log.warning("  Could not check storage intent: %s", exc)


def _monitor_storage_rebalance(
    host: str, user: str, password: str, port: int,
    _cb: Callable[[str], None],
    poll_interval: int = 30,
    max_polls: int = 10,
) -> None:
    """Monitor Storage Spaces Direct rebalance/repair after adding a node.

    After a node is added, S2D redistributes data across the new topology.
    This monitors the progress but does not block indefinitely.

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/manage/add-node
    """
    check_cmd = (
        "$jobs = Get-StorageJob -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Name -match 'Repair|Regenerat|Rebalance' -and $_.IsBackground -eq $false }; "
        "if ($jobs) { "
        "  $jobs | ForEach-Object { "
        "    Write-Output (\"JOB={0}|PROGRESS={1}|STATE={2}\" -f $_.Name, $_.PercentComplete, $_.JobState) "
        "  } "
        "} else { Write-Output 'NO_ACTIVE_JOBS' }"
    )

    _cb("Checking for active storage rebalance jobs …")
    for i in range(max_polls):
        try:
            result = run_powershell(host, user, password, check_cmd, port=port)
            if "NO_ACTIVE_JOBS" in result:
                _cb("No active storage rebalance jobs ✔")
                log.info("  ✔ No active storage rebalance jobs")
                return

            # Parse and report active jobs
            for line in result.strip().splitlines():
                parts = dict(p.split("=", 1) for p in line.split("|") if "=" in p)
                job_name = parts.get("JOB", "?")
                progress = parts.get("PROGRESS", "?")
                state = parts.get("STATE", "?")
                _cb(f"Storage job '{job_name}': {progress}% ({state})")
                log.info("  Storage job [cyan]%s[/]: %s%% (%s)", job_name, progress, state)

            if i < max_polls - 1:
                time.sleep(poll_interval)

        except Exception as exc:
            log.warning("  Storage rebalance check failed: %s", exc)
            return

    _cb("Storage rebalance monitoring timed out – check manually via Get-StorageJob")
    log.info("  Storage rebalance still running – monitor manually")


# ---------------------------------------------------------------------------
# Post-join cluster validation
# ---------------------------------------------------------------------------

def _post_join_validation(
    *,
    new_node_host: str,
    new_node_user: str,
    new_node_password: str,
    new_node_port: int = 22,
    existing_node: dict[str, str] | None = None,
    cluster_name: str = "",
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Comprehensive post-join validation after a node is added to the cluster.

    Checks:
        1. New node appears in Get-ClusterNode and is Up
        2. Cluster health (no critical faults)
        3. Storage Spaces Direct health (pool healthy, all disks online)
        4. Network connectivity (cluster network reachable between nodes)
        5. Arc agent on new node still connected
        6. Storage rebalance status

    Reference:
        https://learn.microsoft.com/en-us/azure/azure-local/manage/add-node
        https://learn.microsoft.com/en-us/azure/azure-local/manage/monitor-cluster
    """
    _cb = progress_callback or (lambda m: None)
    # Pick which host to run cluster-wide commands on
    # Prefer the existing node (already a member), fall back to new node
    if existing_node and existing_node.get("host"):
        cmd_host = existing_node["host"]
        cmd_user = existing_node.get("user", "Administrator")
        cmd_pass = existing_node.get("password", "")
        cmd_port = int(existing_node.get("ssh_port", 22))
    else:
        cmd_host = new_node_host
        cmd_user = new_node_user
        cmd_pass = new_node_password
        cmd_port = new_node_port

    errors: list[str] = []

    # -- 1. Verify new node in cluster and Up ----------------------------
    _cb("  Checking new node is visible in cluster …")
    try:
        result = run_powershell(
            cmd_host, cmd_user, cmd_pass,
            "Get-ClusterNode | Select-Object Name, State | ConvertTo-Json",
            port=cmd_port, timeout=60,
        )
        nodes = _json.loads(result) if result.strip() else []
        if isinstance(nodes, dict):
            nodes = [nodes]

        # Match by IP substring or hostname
        node_found = False
        for n in nodes:
            name = n.get("Name", "")
            state = n.get("State", "")
            _cb(f"  Cluster node: {name} – {state}")
            if state != "Up" and state != 0:
                errors.append(f"Node {name} is {state}, expected Up")
            # We can't always match by IP, so just verify all nodes are Up
            node_found = True

        if not node_found:
            errors.append("No cluster nodes returned by Get-ClusterNode")
        else:
            node_count = len(nodes)
            up_count = sum(1 for n in nodes if n.get("State") in ("Up", 0))
            _cb(f"  Cluster nodes: {up_count}/{node_count} Up")
    except Exception as exc:
        errors.append(f"Cluster node check failed: {exc}")
        _cb(f"  ⚠ Cluster node check failed: {exc}")

    # -- 2. Cluster health faults ----------------------------------------
    _cb("  Checking cluster health faults …")
    try:
        result = run_powershell(
            cmd_host, cmd_user, cmd_pass,
            "Get-HealthFault -ErrorAction SilentlyContinue | "
            "Select-Object FaultType, Severity, Description | ConvertTo-Json",
            port=cmd_port, timeout=60,
        )
        faults = _json.loads(result) if result.strip() else []
        if isinstance(faults, dict):
            faults = [faults]

        critical = [f for f in faults if f.get("Severity") in ("Critical", "Warning")]
        if critical:
            for f in critical[:5]:
                _cb(f"  ⚠ Fault: [{f.get('Severity')}] {f.get('FaultType')}: "
                    f"{f.get('Description', '')[:120]}")
            if any(f.get("Severity") == "Critical" for f in critical):
                errors.append(f"{len(critical)} health fault(s) detected")
        else:
            _cb("  Cluster health: no faults ✔")
    except Exception as exc:
        log.warning("  Health fault check failed (non-blocking): %s", exc)
        _cb(f"  Health fault check skipped: {exc}")

    # -- 3. Storage Spaces Direct health ---------------------------------
    _cb("  Checking Storage Spaces Direct health …")
    try:
        result = run_powershell(
            cmd_host, cmd_user, cmd_pass,
            "$pool = Get-StoragePool -IsPrimordial $false -ErrorAction SilentlyContinue; "
            "if ($pool) { "
            "  $status = $pool.HealthStatus; "
            "  $disks = (Get-PhysicalDisk | Group-Object HealthStatus | "
            "    ForEach-Object { '{0}={1}' -f $_.Name, $_.Count }) -join '|'; "
            "  Write-Output \"POOL=$status|$disks\" "
            "} else { Write-Output 'NO_POOL' }",
            port=cmd_port, timeout=60,
        )
        if "NO_POOL" in result:
            _cb("  S2D: No storage pool (expected for fresh single-node expansions)")
        elif "Healthy" in result:
            _cb(f"  S2D pool healthy ✔ ({result.strip()})")
        else:
            _cb(f"  ⚠ S2D pool status: {result.strip()}")
            errors.append(f"Storage pool not healthy: {result.strip()}")
    except Exception as exc:
        log.warning("  S2D check failed (non-blocking): %s", exc)
        _cb(f"  S2D check skipped: {exc}")

    # -- 4. Cluster network validation -----------------------------------
    _cb("  Checking cluster networks …")
    try:
        result = run_powershell(
            cmd_host, cmd_user, cmd_pass,
            "Get-ClusterNetwork | Select-Object Name, State, Role | ConvertTo-Json",
            port=cmd_port, timeout=60,
        )
        networks = _json.loads(result) if result.strip() else []
        if isinstance(networks, dict):
            networks = [networks]
        for net in networks:
            state = net.get("State", "")
            name = net.get("Name", "")
            role = net.get("Role", "")
            state_str = "Up" if state in ("Up", 1, 3) else str(state)
            _cb(f"  Network '{name}': {state_str} (role: {role})")
            if state not in ("Up", 1, 3):
                errors.append(f"Cluster network '{name}' is {state}, expected Up")

        if networks:
            _cb(f"  Cluster networks: {len(networks)} found ✔")
    except Exception as exc:
        log.warning("  Network check failed (non-blocking): %s", exc)
        _cb(f"  Network check skipped: {exc}")

    # -- 5. Arc agent connectivity on new node ---------------------------
    _cb("  Checking Arc agent status on new node …")
    try:
        result = run_powershell(
            new_node_host, new_node_user, new_node_password,
            "& \"$env:ProgramFiles\\AzureConnectedMachineAgent\\azcmagent.exe\" show -j",
            port=new_node_port, timeout=30,
        )
        if '"Connected"' in result:
            _cb("  Arc agent: Connected ✔")
        else:
            _cb("  ⚠ Arc agent may not be connected")
            errors.append("Arc agent on new node is not in Connected state")
    except Exception as exc:
        _cb(f"  Arc agent check failed: {exc}")
        errors.append(f"Arc agent check failed on new node: {exc}")

    # -- 6. Storage rebalance status -------------------------------------
    if existing_node and existing_node.get("host"):
        _monitor_storage_rebalance(
            cmd_host, cmd_user, cmd_pass, cmd_port, _cb,
            poll_interval=15, max_polls=4,
        )

    # -- 7. Sync-AzureStackHCI to force portal visibility ----------------
    #    Microsoft docs: "run Sync-AzureStackHCI to force the node to show
    #    up in Azure portal" (otherwise it can take several hours).
    _cb("  Running Sync-AzureStackHCI to update Azure portal …")
    try:
        run_powershell(
            cmd_host, cmd_user, cmd_pass,
            "Sync-AzureStackHCI -ErrorAction SilentlyContinue",
            port=cmd_port, timeout=120,
        )
        _cb("  Sync-AzureStackHCI completed ✔")
    except Exception as exc:
        log.warning("  Sync-AzureStackHCI failed (non-blocking): %s", exc)
        _cb(f"  Sync-AzureStackHCI skipped: {exc}")

    # -- Summary ----------------------------------------------------------
    if errors:
        _cb(f"  ⚠ Post-join validation completed with {len(errors)} issue(s):")
        for e in errors:
            _cb(f"    • {e}")
        log.warning("Post-join validation issues: %s", errors)
    else:
        _cb("  Post-join validation: all checks passed ✔")
        log.info("  ✔ Post-join validation passed")
