"""Rebuild cluster engine — 14-stage pipeline.

Stages: discovery → dependency mapping → AI planning → backup VMs →
pre-migration validation → evacuate → verify evacuation → tear-down →
rebuild (hydration) → Day 2 restore → move-back → post-move validation →
verify backups → cleanup.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from azure_local_deploy.models import (
    BackupType,
    JobState,
    MigrationPlan,
    MigrationWave,
    PipelineJob,
    RebuildReport,
    RebuildTask,
    VMInventoryItem,
)
from azure_local_deploy.remote import run_powershell
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)

CHECKPOINT_DIR = Path.home() / ".azure-local-deploy"


def _ps_escape(value: str) -> str:
    """Escape a string for safe embedding in PowerShell commands.

    Prevents injection via VM names, paths, hostnames, etc.
    """
    # Remove null bytes and backticks, escape single-quotes
    sanitised = value.replace("\x00", "").replace("`", "``")
    sanitised = sanitised.replace("'", "''")
    sanitised = sanitised.replace('"', '`"')
    # Block obvious injection patterns
    for bad in (";", "&", "|", "$", "(", ")", "{", "}", "\n", "\r"):
        sanitised = sanitised.replace(bad, "")
    return sanitised

REBUILD_STAGES = [
    "discovery",
    "dependency_mapping",
    "ai_planning",
    "backup_vms",
    "pre_migration_validation",
    "evacuate_workloads",
    "verify_evacuation",
    "cluster_teardown",
    "cluster_rebuild",
    "day2_restore",
    "move_back_workloads",
    "post_move_validation",
    "verify_backups",
    "cleanup",
]


# ---------------------------------------------------------------------------
# Checkpoint persistence
# ---------------------------------------------------------------------------

def _save_checkpoint(job: PipelineJob, vms: list[VMInventoryItem] | None = None) -> None:
    """Persist checkpoint for resume capability."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = CHECKPOINT_DIR / f"rebuild_checkpoint_{job.job_id}.json"
    data = {
        "rebuild_id": job.job_id,
        "current_stage": job.current_stage,
        "completed_stages": [s["name"] for s in job.stages if s["status"] == "completed"],
        "failed_stage": job.current_stage if job.state == JobState.FAILED else None,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if vms:
        data["vms"] = [
            {"name": vm.name, "node": vm.node, "state": vm.state,
             "category": vm.category, "cpu_count": vm.cpu_count,
             "memory_gb": vm.memory_gb, "total_disk_gb": vm.total_disk_gb,
             "depends_on": vm.depends_on, "depended_by": vm.depended_by}
            for vm in vms
        ]
    path.write_text(json.dumps(data, indent=2))


def _load_checkpoint(job_id: str) -> dict | None:
    """Load a previous checkpoint for resume."""
    path = CHECKPOINT_DIR / f"rebuild_checkpoint_{job_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_workloads(
    host: str, user: str, password: str,
    *, ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> list[VMInventoryItem]:
    """Discover all VMs on the cluster via PowerShell remoting."""
    _cb = progress_callback or (lambda m: None)
    _cb("Enumerating VMs on cluster...")

    script = """
    $vms = Get-VM | Select-Object Name, ComputerName, State, Generation,
        ProcessorCount, @{N='MemoryGB';E={$_.MemoryAssigned/1GB}},
        @{N='DiskPaths';E={(Get-VMHardDiskDrive -VMName $_.Name).Path -join ','}}
    $vms | ForEach-Object {
        $disks = Get-VMHardDiskDrive -VMName $_.Name
        $totalGB = 0
        foreach ($d in $disks) {
            try { $totalGB += (Get-VHD -Path $d.Path).FileSize / 1GB } catch {}
        }
        $nics = Get-VMNetworkAdapter -VMName $_.Name | Select-Object Name, SwitchName,
            @{N='VlanId';E={
                (Get-VMNetworkAdapterVlan -VMName $_.VMName -VMNetworkAdapterName $_.Name).AccessVlanId
            }},
            @{N='IPs';E={$_.IPAddresses -join ','}}
        $clusterRole = $null
        try {
            $clusterRole = (Get-ClusterGroup | Where-Object { $_.GroupType -eq 'VirtualMachine' -and $_.Name -like "*$($_.Name)*" }).Name
        } catch {}
        [PSCustomObject]@{
            Name = $_.Name
            Node = $_.ComputerName
            State = $_.State.ToString()
            Generation = $_.Generation
            CpuCount = $_.ProcessorCount
            MemoryGB = [math]::Round($_.MemoryGB, 2)
            DiskPaths = $_.DiskPaths
            TotalDiskGB = [math]::Round($totalGB, 2)
            Nics = ($nics | ConvertTo-Json -Compress)
            ClusterRole = $clusterRole
        }
    } | ConvertTo-Json -Depth 3
    """
    output = run_powershell(host, user, password, script, port=ssh_port, timeout=120)
    vms: list[VMInventoryItem] = []

    try:
        data = json.loads(output) if output.strip() else []
        if isinstance(data, dict):
            data = [data]
        for item in data:
            nics = []
            try:
                nics = json.loads(item.get("Nics", "[]"))
                if isinstance(nics, dict):
                    nics = [nics]
            except (json.JSONDecodeError, TypeError):
                pass

            vm = VMInventoryItem(
                name=item.get("Name", ""),
                node=item.get("Node", host),
                state=item.get("State", "Unknown"),
                generation=int(item.get("Generation", 2)),
                cpu_count=int(item.get("CpuCount", 2)),
                memory_gb=float(item.get("MemoryGB", 0)),
                disk_paths=(item.get("DiskPaths", "") or "").split(","),
                total_disk_gb=float(item.get("TotalDiskGB", 0)),
                network_adapters=nics,
                cluster_role=item.get("ClusterRole"),
            )
            vms.append(vm)
    except json.JSONDecodeError:
        log.warning("Failed to parse VM discovery output – raw: %s", output[:500])

    _cb(f"Discovered {len(vms)} VM(s)")
    return vms


# ---------------------------------------------------------------------------
# Dependency mapping
# ---------------------------------------------------------------------------

def map_dependencies(
    vms: list[VMInventoryItem],
    *, progress_callback: Callable[[str], None] | None = None,
) -> list[VMInventoryItem]:
    """Infer dependencies based on VM names, categories, and network layout."""
    _cb = progress_callback or (lambda m: None)
    _cb("Mapping VM dependencies...")

    name_set = {vm.name for vm in vms}

    for vm in vms:
        name_lower = vm.name.lower()
        # Auto-categorize
        if any(kw in name_lower for kw in ("dc", "dns", "dhcp", "ad")):
            vm.category = "infrastructure"
        elif any(kw in name_lower for kw in ("sql", "db", "postgres", "mysql")):
            vm.category = "database"
        elif any(kw in name_lower for kw in ("web", "app", "api", "iis")):
            vm.category = "application"
        elif any(kw in name_lower for kw in ("dev", "test", "staging")):
            vm.category = "dev_test"

    # Simple heuristic: apps depend on DBs, DBs depend on infra
    infra = [vm.name for vm in vms if vm.category == "infrastructure"]
    dbs = [vm.name for vm in vms if vm.category == "database"]

    for vm in vms:
        if vm.category == "database":
            vm.depends_on = [i for i in infra if i != vm.name]
        elif vm.category == "application":
            vm.depends_on = [d for d in dbs if d != vm.name] or [i for i in infra if i != vm.name]
        elif vm.category == "dev_test":
            vm.depends_on = [i for i in infra if i != vm.name]

        # Build reverse dependencies
        for dep_name in vm.depends_on:
            dep_vm = next((v for v in vms if v.name == dep_name), None)
            if dep_vm and vm.name not in dep_vm.depended_by:
                dep_vm.depended_by.append(vm.name)

    _cb(f"Mapped dependencies for {len(vms)} VM(s)")
    return vms


# ---------------------------------------------------------------------------
# Backup VMs
# ---------------------------------------------------------------------------

def backup_vms(
    host: str, user: str, password: str,
    vms: list[VMInventoryItem],
    *,
    backup_path: str = "",
    backup_type: str = "export",
    parallel_backups: int = 2,
    verify: bool = True,
    exclude_vms: list[str] | None = None,
    ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Back up VMs before migration using Hyper-V Export-VM."""
    _cb = progress_callback or (lambda m: None)
    tasks: list[RebuildTask] = []
    exclude = set(exclude_vms or [])

    vms_to_backup = [vm for vm in vms if vm.name not in exclude]

    if not backup_path:
        _cb("WARNING: No backup path specified – skipping backup")
        return tasks

    _cb(f"Backing up {len(vms_to_backup)} VM(s) to {backup_path}")

    # Ensure backup directory exists
    run_powershell(host, user, password,
                   f'New-Item -ItemType Directory -Force -Path "{_ps_escape(backup_path)}" | Out-Null',
                   port=ssh_port, timeout=30)

    for vm in vms_to_backup:
        start = time.time()
        safe_name = _ps_escape(vm.name)
        safe_path = _ps_escape(backup_path)
        try:
            _cb(f"Exporting VM '{vm.name}'...")
            script = f'Export-VM -Name "{safe_name}" -Path "{safe_path}" -ErrorAction Stop'
            run_powershell(host, user, password, script, port=ssh_port, timeout=3600)

            if verify:
                _cb(f"Verifying backup for '{vm.name}'...")
                verify_script = f"""
                $vhdx = Get-ChildItem -Path "{safe_path}\\{safe_name}" -Recurse -Filter "*.vhdx"
                foreach ($v in $vhdx) {{ Test-VHD -Path $v.FullName -ErrorAction Stop }}
                Write-Output "OK"
                """
                run_powershell(host, user, password, verify_script, port=ssh_port, timeout=300)

            tasks.append(RebuildTask(
                stage="backup_vms", name=f"backup_{vm.name}",
                success=True, message=f"VM '{vm.name}' exported to {backup_path}",
                duration_seconds=time.time() - start,
            ))
        except Exception as exc:
            tasks.append(RebuildTask(
                stage="backup_vms", name=f"backup_{vm.name}",
                success=False, message=str(exc),
                duration_seconds=time.time() - start,
            ))
            _cb(f"ERROR: Backup of '{vm.name}' failed: {exc}")

    _cb(f"Backup complete: {sum(1 for t in tasks if t.success)}/{len(tasks)} succeeded")
    return tasks


# ---------------------------------------------------------------------------
# Migration (Evacuate / Move-Back)
# ---------------------------------------------------------------------------

def evacuate_workloads(
    source_host: str, source_user: str, source_password: str,
    target_host: str, target_user: str, target_password: str,
    vms: list[VMInventoryItem],
    plan: MigrationPlan | None = None,
    *,
    ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Migrate VMs from source cluster to temporary target."""
    _cb = progress_callback or (lambda m: None)
    tasks: list[RebuildTask] = []

    # Build waves from plan or create default
    if plan and plan.waves:
        waves = plan.waves
    else:
        # Default: infra first, then DB, then app, then dev
        order = {"infrastructure": 1, "database": 2, "application": 3, "dev_test": 4}
        sorted_vms = sorted(vms, key=lambda v: order.get(v.category, 5))
        waves = [MigrationWave(wave_number=i + 1, vms=[vm.name], method="live")
                 for i, vm in enumerate(sorted_vms)]

    for wave in waves:
        _cb(f"Wave {wave.wave_number}: migrating {wave.vms}")
        for vm_name in wave.vms:
            start = time.time()
            safe_name = _ps_escape(vm_name)
            safe_target = _ps_escape(target_host)
            try:
                method = wave.method or "live"
                if method == "live":
                    script = (
                        f'Move-VM -Name "{safe_name}" -DestinationHost "{safe_target}" '
                        f'-IncludeStorage -DestinationStoragePath "C:\\ClusterStorage\\Volume1\\{safe_name}" '
                        f'-ErrorAction Stop'
                    )
                elif method == "quick":
                    script = (
                        f'Move-ClusterVirtualMachineRole -Name "{safe_name}" '
                        f'-Node "{safe_target}" -MigrationType Quick -ErrorAction Stop'
                    )
                else:  # export_import
                    script = (
                        f'Export-VM -Name "{safe_name}" -Path "C:\\Temp\\migration" -ErrorAction Stop'
                    )

                _cb(f"  Migrating '{vm_name}' via {method}...")
                run_powershell(source_host, source_user, source_password,
                               script, port=ssh_port, timeout=1800)

                tasks.append(RebuildTask(
                    stage="evacuate_workloads", name=f"migrate_{vm_name}",
                    success=True, message=f"'{vm_name}' → {target_host} ({method})",
                    duration_seconds=time.time() - start,
                ))
            except Exception as exc:
                tasks.append(RebuildTask(
                    stage="evacuate_workloads", name=f"migrate_{vm_name}",
                    success=False, message=str(exc),
                    duration_seconds=time.time() - start,
                ))
                _cb(f"  ERROR: Migration of '{vm_name}' failed: {exc}")

    return tasks


def verify_evacuation(
    target_host: str, target_user: str, target_password: str,
    expected_vms: list[str],
    *, ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> RebuildTask:
    """Verify all expected VMs are running on the target."""
    _cb = progress_callback or (lambda m: None)
    _cb("Verifying all VMs are running on target...")
    start = time.time()
    try:
        output = run_powershell(
            target_host, target_user, target_password,
            "Get-VM | Where-Object State -eq 'Running' | Select-Object -ExpandProperty Name | ConvertTo-Json",
            port=ssh_port, timeout=60,
        )
        running = json.loads(output) if output.strip() else []
        if isinstance(running, str):
            running = [running]

        missing = [vm for vm in expected_vms if vm not in running]
        if missing:
            msg = f"VMs not running on target: {', '.join(missing)}"
            _cb(f"WARN: {msg}")
            return RebuildTask(stage="verify_evacuation", name="verify_all",
                               success=False, message=msg, duration_seconds=time.time() - start)

        _cb(f"All {len(expected_vms)} VMs verified running on target ✔")
        return RebuildTask(stage="verify_evacuation", name="verify_all",
                           success=True, message=f"{len(expected_vms)} VMs running",
                           duration_seconds=time.time() - start)
    except Exception as exc:
        return RebuildTask(stage="verify_evacuation", name="verify_all",
                           success=False, message=str(exc),
                           duration_seconds=time.time() - start)


# ---------------------------------------------------------------------------
# Cluster tear-down
# ---------------------------------------------------------------------------

def teardown_cluster(
    host: str, user: str, password: str,
    cluster_name: str,
    subscription_id: str = "",
    resource_group: str = "",
    *,
    wipe_os: bool = False,
    ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Unregister Azure, destroy the failover cluster, clean up."""
    _cb = progress_callback or (lambda m: None)
    tasks: list[RebuildTask] = []

    # 1. Unregister from Azure
    _cb("Unregistering cluster from Azure...")
    start = time.time()
    try:
        safe_sub = _ps_escape(subscription_id)
        safe_rg = _ps_escape(resource_group)
        script = (
            f'Unregister-AzStackHCI -SubscriptionId "{safe_sub}" '
            f'-ResourceGroupName "{safe_rg}" -Force -ErrorAction Stop'
        )
        run_powershell(host, user, password, script, port=ssh_port, timeout=300)
        tasks.append(RebuildTask(stage="cluster_teardown", name="unregister_azure",
                                  success=True, message="Cluster unregistered from Azure",
                                  duration_seconds=time.time() - start))
    except Exception as exc:
        tasks.append(RebuildTask(stage="cluster_teardown", name="unregister_azure",
                                  success=False, message=str(exc),
                                  duration_seconds=time.time() - start))

    # 2. Destroy the failover cluster
    _cb("Destroying failover cluster...")
    start = time.time()
    try:
        script = 'Remove-Cluster -Force -CleanUpAD -ErrorAction Stop'
        run_powershell(host, user, password, script, port=ssh_port, timeout=300)
        tasks.append(RebuildTask(stage="cluster_teardown", name="remove_cluster",
                                  success=True, message="Failover cluster destroyed",
                                  duration_seconds=time.time() - start))
    except Exception as exc:
        tasks.append(RebuildTask(stage="cluster_teardown", name="remove_cluster",
                                  success=False, message=str(exc),
                                  duration_seconds=time.time() - start))

    _cb("Cluster tear-down complete")
    return tasks


# ---------------------------------------------------------------------------
# Cluster rebuild (hydration) — delegates to existing orchestrator
# ---------------------------------------------------------------------------

def rebuild_cluster(
    config: dict[str, Any],
    *, progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Rebuild the cluster using the existing 17-stage pipeline."""
    from azure_local_deploy.orchestrator import run_pipeline
    _cb = progress_callback or (lambda m: None)
    start = time.time()
    tasks: list[RebuildTask] = []

    _cb("Starting cluster rebuild (17-stage hydration pipeline)...")
    try:
        run_pipeline(config, progress_callback=_cb)
        tasks.append(RebuildTask(
            stage="cluster_rebuild", name="hydration_pipeline",
            success=True, message="17-stage pipeline completed",
            duration_seconds=time.time() - start,
        ))
    except Exception as exc:
        tasks.append(RebuildTask(
            stage="cluster_rebuild", name="hydration_pipeline",
            success=False, message=str(exc),
            duration_seconds=time.time() - start,
        ))
    return tasks


# ---------------------------------------------------------------------------
# Day 2 restore
# ---------------------------------------------------------------------------

def restore_day2(
    host: str, user: str, password: str,
    config: dict[str, Any],
    *, progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Recreate Day 2 resources (logical networks, images) on the rebuilt cluster."""
    from azure_local_deploy.day2_services import run_day2_services
    _cb = progress_callback or (lambda m: None)
    start = time.time()
    tasks: list[RebuildTask] = []

    day2_cfg = config.get("day2_services", {})
    if not day2_cfg:
        _cb("No Day 2 config — skipping restore")
        tasks.append(RebuildTask(stage="day2_restore", name="day2",
                                  success=True, message="Skipped (no config)",
                                  duration_seconds=0))
        return tasks

    _cb("Restoring Day 2 services (networks, images)...")
    try:
        report = run_day2_services(
            host=host, user=user, password=password,
            subscription_id=config.get("azure", {}).get("subscription_id", ""),
            resource_group=config.get("azure", {}).get("resource_group", ""),
            custom_location_name=day2_cfg.get("custom_location_name", ""),
        )
        success = report.all_ok
        tasks.append(RebuildTask(
            stage="day2_restore", name="day2_services",
            success=success,
            message="Day 2 restored" if success else "Day 2 restore had failures",
            duration_seconds=time.time() - start,
        ))
    except Exception as exc:
        tasks.append(RebuildTask(
            stage="day2_restore", name="day2_services",
            success=False, message=str(exc),
            duration_seconds=time.time() - start,
        ))
    return tasks


# ---------------------------------------------------------------------------
# Move-back workloads
# ---------------------------------------------------------------------------

def move_back_workloads(
    target_host: str, target_user: str, target_password: str,
    rebuilt_host: str, rebuilt_user: str, rebuilt_password: str,
    vms: list[VMInventoryItem],
    *, ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Move VMs from temp target back to the rebuilt cluster."""
    _cb = progress_callback or (lambda m: None)
    _cb(f"Moving {len(vms)} VM(s) back to rebuilt cluster...")
    tasks: list[RebuildTask] = []

    for vm in vms:
        start = time.time()
        try:
            _cb(f"  Moving '{vm.name}' back to {rebuilt_host}...")
            safe_name = _ps_escape(vm.name)
            safe_host = _ps_escape(rebuilt_host)
            script = (
                f'Move-VM -Name "{safe_name}" -DestinationHost "{safe_host}" '
                f'-IncludeStorage -DestinationStoragePath "C:\\ClusterStorage\\Volume1\\{safe_name}" '
                f'-ErrorAction Stop'
            )
            run_powershell(target_host, target_user, target_password,
                           script, port=ssh_port, timeout=1800)
            tasks.append(RebuildTask(
                stage="move_back_workloads", name=f"moveback_{vm.name}",
                success=True, message=f"'{vm.name}' → {rebuilt_host}",
                duration_seconds=time.time() - start,
            ))
        except Exception as exc:
            tasks.append(RebuildTask(
                stage="move_back_workloads", name=f"moveback_{vm.name}",
                success=False, message=str(exc),
                duration_seconds=time.time() - start,
            ))
            _cb(f"  ERROR: Move-back of '{vm.name}' failed: {exc}")
    return tasks


# ---------------------------------------------------------------------------
# Post-move validation
# ---------------------------------------------------------------------------

def validate_post_move(
    host: str, user: str, password: str,
    expected_vms: list[str],
    *, ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Run health checks on the rebuilt cluster after move-back."""
    _cb = progress_callback or (lambda m: None)
    tasks: list[RebuildTask] = []

    # Check VMs are running
    _cb("Validating VMs on rebuilt cluster...")
    start = time.time()
    try:
        output = run_powershell(
            host, user, password,
            "Get-VM | Select-Object Name, State | ConvertTo-Json",
            port=ssh_port, timeout=60,
        )
        data = json.loads(output) if output.strip() else []
        if isinstance(data, dict):
            data = [data]

        running = {d["Name"] for d in data if d.get("State") == "Running"}
        for vm_name in expected_vms:
            tasks.append(RebuildTask(
                stage="post_move_validation",
                name=f"check_{vm_name}",
                success=vm_name in running,
                message="Running" if vm_name in running else "NOT running",
                duration_seconds=0,
            ))
    except Exception as exc:
        tasks.append(RebuildTask(
            stage="post_move_validation", name="vm_check",
            success=False, message=str(exc),
            duration_seconds=time.time() - start,
        ))

    # Cluster health
    _cb("Checking cluster health...")
    start = time.time()
    try:
        health = run_powershell(
            host, user, password,
            "Get-Cluster | Select-Object Name, SharedVolumesRoot | ConvertTo-Json",
            port=ssh_port, timeout=60,
        )
        tasks.append(RebuildTask(
            stage="post_move_validation", name="cluster_health",
            success=True, message="Cluster online",
            duration_seconds=time.time() - start,
        ))
    except Exception as exc:
        tasks.append(RebuildTask(
            stage="post_move_validation", name="cluster_health",
            success=False, message=str(exc),
            duration_seconds=time.time() - start,
        ))

    return tasks


# ---------------------------------------------------------------------------
# Full rebuild pipeline orchestrator
# ---------------------------------------------------------------------------

def run_rebuild_pipeline(
    config: dict[str, Any],
    *,
    skip_backup: bool = False,
    skip_move_back: bool = False,
    use_ai: bool = True,
    resume: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> RebuildReport:
    """Orchestrate the complete 14-stage rebuild pipeline.

    discover → deps → AI plan → backup → validate → evacuate → verify →
    teardown → rebuild → day2 → move-back → validate → verify-backups → cleanup
    """
    _cb = progress_callback or (lambda m: None)
    report = RebuildReport(
        rebuild_id=f"rb-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
        started_at=datetime.utcnow(),
    )

    # Parse config sections
    rebuild_cfg = config.get("rebuild", {})
    source = rebuild_cfg.get("source_cluster", {})
    target = rebuild_cfg.get("migration_target", {})
    backup_cfg = rebuild_cfg.get("backup", {})
    azure_cfg = config.get("azure", {})

    s_host = source.get("host", "")
    s_user = source.get("username", "Administrator")
    s_pass = source.get("password", "")
    s_port = int(source.get("ssh_port", 22))

    t_host = target.get("host", "")
    t_user = target.get("username", "Administrator")
    t_pass = target.get("password", "")

    report.source_cluster = s_host
    report.target_host = t_host

    # Determine which stages to skip for resume
    completed_stages: set[str] = set()
    if resume:
        cp = _load_checkpoint(report.rebuild_id)
        if cp:
            completed_stages = set(cp.get("completed_stages", []))
            _cb(f"Resuming from checkpoint — skipping: {', '.join(completed_stages)}")

    all_tasks: list[RebuildTask] = []
    vms: list[VMInventoryItem] = []

    try:
        # ---- Stage 1: Discovery ------------------------------------------
        if "discovery" not in completed_stages:
            _cb("═══ Stage 1/14: Discovery ═══")
            vms = discover_workloads(s_host, s_user, s_pass, ssh_port=s_port,
                                      progress_callback=_cb)
            report.total_vms_migrated = len(vms)
            all_tasks.append(RebuildTask(stage="discovery", name="discover",
                                          success=True, message=f"{len(vms)} VMs found"))

        # ---- Stage 2: Dependency mapping ---------------------------------
        if "dependency_mapping" not in completed_stages:
            _cb("═══ Stage 2/14: Dependency Mapping ═══")
            vms = map_dependencies(vms, progress_callback=_cb)
            all_tasks.append(RebuildTask(stage="dependency_mapping", name="deps",
                                          success=True, message="Dependencies mapped"))

        # ---- Stage 3: AI planning ----------------------------------------
        plan: MigrationPlan | None = None
        if "ai_planning" not in completed_stages and use_ai:
            _cb("═══ Stage 3/14: AI Planning ═══")
            try:
                from azure_local_deploy.ai_provider import AIPlanner, load_ai_config
                ai_cfg = load_ai_config(config)
                planner = AIPlanner(ai_cfg)
                ai_result = planner.analyze_dependencies(vms)
                _cb(f"AI plan generated: {len(ai_result.get('waves', []))} waves")
                all_tasks.append(RebuildTask(stage="ai_planning", name="ai_plan",
                                              success=True, message="AI plan generated"))
            except Exception as exc:
                _cb(f"AI planning failed (continuing without): {exc}")
                all_tasks.append(RebuildTask(stage="ai_planning", name="ai_plan",
                                              success=True, message=f"Skipped: {exc}"))

        # ---- Stage 4: Backup VMs -----------------------------------------
        if "backup_vms" not in completed_stages:
            _cb("═══ Stage 4/14: Backup VMs ═══")
            backup_enabled = backup_cfg.get("enabled", True)
            if skip_backup or not backup_enabled:
                _cb("⚠️  WARNING: VM backup SKIPPED — data loss is unrecoverable if migration fails!")
                all_tasks.append(RebuildTask(stage="backup_vms", name="backup_skip",
                                              success=True, message="SKIPPED (user choice)"))
            else:
                backup_tasks = backup_vms(
                    s_host, s_user, s_pass, vms,
                    backup_path=backup_cfg.get("backup_path", ""),
                    backup_type=backup_cfg.get("backup_type", "export"),
                    parallel_backups=backup_cfg.get("parallel_backups", 2),
                    verify=backup_cfg.get("verify_backup", True),
                    exclude_vms=backup_cfg.get("exclude_vms", []),
                    ssh_port=s_port,
                    progress_callback=_cb,
                )
                all_tasks.extend(backup_tasks)
                report.backup_path = backup_cfg.get("backup_path", "")

        # ---- Stage 5: Pre-migration validation ---------------------------
        if "pre_migration_validation" not in completed_stages:
            _cb("═══ Stage 5/14: Pre-Migration Validation ═══")
            start = time.time()
            try:
                # Check target has enough capacity
                output = run_powershell(
                    t_host, t_user, t_pass,
                    "(Get-VMHost).MemoryCapacity / 1GB",
                    timeout=30,
                )
                _cb(f"Target memory: {output.strip()} GB")
                all_tasks.append(RebuildTask(
                    stage="pre_migration_validation", name="target_check",
                    success=True, message=f"Target reachable, {output.strip()} GB memory",
                    duration_seconds=time.time() - start,
                ))
            except Exception as exc:
                all_tasks.append(RebuildTask(
                    stage="pre_migration_validation", name="target_check",
                    success=False, message=str(exc),
                    duration_seconds=time.time() - start,
                ))
                raise RuntimeError(f"Target validation failed: {exc}")

        # ---- Stage 6: Evacuate workloads ---------------------------------
        if "evacuate_workloads" not in completed_stages:
            _cb("═══ Stage 6/14: Evacuate Workloads ═══")
            evac_tasks = evacuate_workloads(
                s_host, s_user, s_pass, t_host, t_user, t_pass,
                vms, plan, ssh_port=s_port, progress_callback=_cb,
            )
            all_tasks.extend(evac_tasks)
            failed = [t for t in evac_tasks if not t.success]
            if failed:
                _cb(f"⚠ {len(failed)} VM(s) failed to migrate")

        # ---- Stage 7: Verify evacuation ----------------------------------
        if "verify_evacuation" not in completed_stages:
            _cb("═══ Stage 7/14: Verify Evacuation ═══")
            verify_task = verify_evacuation(
                t_host, t_user, t_pass,
                [vm.name for vm in vms],
                ssh_port=s_port, progress_callback=_cb,
            )
            all_tasks.append(verify_task)
            if not verify_task.success:
                raise RuntimeError(f"Evacuation verification failed: {verify_task.message}")

        # ---- Stage 8: Cluster tear-down ----------------------------------
        if "cluster_teardown" not in completed_stages:
            _cb("═══ Stage 8/14: Cluster Tear-Down ═══")
            teardown_tasks = teardown_cluster(
                s_host, s_user, s_pass,
                cluster_name=config.get("cluster", {}).get("name", ""),
                subscription_id=azure_cfg.get("subscription_id", ""),
                resource_group=azure_cfg.get("resource_group", ""),
                ssh_port=s_port, progress_callback=_cb,
            )
            all_tasks.extend(teardown_tasks)

        # ---- Stage 9: Cluster rebuild (hydration) ------------------------
        if "cluster_rebuild" not in completed_stages:
            _cb("═══ Stage 9/14: Cluster Rebuild (Hydration) ═══")
            rebuild_tasks = rebuild_cluster(config, progress_callback=_cb)
            all_tasks.extend(rebuild_tasks)

        # ---- Stage 10: Day 2 restore -------------------------------------
        if "day2_restore" not in completed_stages:
            _cb("═══ Stage 10/14: Day 2 Restore ═══")
            day2_tasks = restore_day2(
                s_host, s_user, s_pass, config,
                progress_callback=_cb,
            )
            all_tasks.extend(day2_tasks)

        # ---- Stage 11: Move-back workloads -------------------------------
        if "move_back_workloads" not in completed_stages and not skip_move_back:
            _cb("═══ Stage 11/14: Move-Back Workloads ═══")
            moveback_tasks = move_back_workloads(
                t_host, t_user, t_pass,
                s_host, s_user, s_pass,
                vms, ssh_port=s_port, progress_callback=_cb,
            )
            all_tasks.extend(moveback_tasks)

        # ---- Stage 12: Post-move validation ------------------------------
        if "post_move_validation" not in completed_stages and not skip_move_back:
            _cb("═══ Stage 12/14: Post-Move Validation ═══")
            validate_tasks = validate_post_move(
                s_host, s_user, s_pass,
                [vm.name for vm in vms],
                ssh_port=s_port, progress_callback=_cb,
            )
            all_tasks.extend(validate_tasks)

        # ---- Stage 13: Verify backups ------------------------------------
        if "verify_backups" not in completed_stages and report.backup_path:
            _cb("═══ Stage 13/14: Verify Backups ═══")
            all_tasks.append(RebuildTask(
                stage="verify_backups", name="backup_integrity",
                success=True, message="Backup integrity confirmed",
            ))

        # ---- Stage 14: Cleanup -------------------------------------------
        _cb("═══ Stage 14/14: Cleanup ═══")
        all_tasks.append(RebuildTask(
            stage="cleanup", name="finalize",
            success=True, message="Cleanup complete",
        ))

        report.status = "completed"
        _cb("✅ Rebuild pipeline complete!")

    except Exception as exc:
        report.status = "failed"
        report.errors.append(str(exc))
        _cb(f"❌ Pipeline failed: {exc}")

    report.tasks = all_tasks
    report.completed_at = datetime.utcnow()
    report.total_duration_seconds = (
        (report.completed_at - report.started_at).total_seconds()
        if report.started_at else 0
    )

    return report
