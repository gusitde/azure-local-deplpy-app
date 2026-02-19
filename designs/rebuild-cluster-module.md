# Design Document: Rebuild Cluster Module

> **Module name:** `rebuild_cluster.py`
> **Status:** Draft — Pending Approval
> **Author:** Azure Local Deploy Team
> **Date:** 2026-02-18
> **Version:** 1.0

---

## Table of Contents

- [1. Executive Summary](#1-executive-summary)
- [2. Problem Statement](#2-problem-statement)
- [3. Goals & Non-Goals](#3-goals--non-goals)
- [4. Architecture Overview](#4-architecture-overview)
- [4b. VM Backup (Pre-Migration Safety Net)](#4b-vm-backup-pre-migration-safety-net)
- [5. Migration Strategy Deep Dive](#5-migration-strategy-deep-dive)
  - [5.1 Azure Local → Azure Local (Live Migration)](#51-azure-local--azure-local-live-migration)
  - [5.2 Azure Local → Standalone Hyper-V Host / Cluster (Quick Migration + Export/Import)](#52-azure-local--standalone-hyper-v-host--cluster-quick-migration--exportimport)
  - [5.3 Shared-Nothing Live Migration](#53-shared-nothing-live-migration)
  - [5.4 Storage Replica for Data Volumes](#54-storage-replica-for-data-volumes)
- [6. Hydration Process (Cluster Rebuild)](#6-hydration-process-cluster-rebuild)
- [7. Workload Move-Back](#7-workload-move-back)
- [8. Dependency Mapping](#8-dependency-mapping)
- [9. AI-Assisted Migration Planning](#9-ai-assisted-migration-planning)
- [10. Pipeline Stages](#10-pipeline-stages)
- [11. YAML Configuration Schema](#11-yaml-configuration-schema)
- [12. CLI Commands](#12-cli-commands)
- [13. Web Wizard Design](#13-web-wizard-design)
- [14. Module API Design](#14-module-api-design)
- [15. Error Handling & Rollback](#15-error-handling--rollback)
- [16. Security Considerations](#16-security-considerations)
- [17. Testing Strategy](#17-testing-strategy)
- [18. Open Questions](#18-open-questions)
- [19. References](#19-references)
- [20. REST API Integration](#20-rest-api-integration)
  - [20.1 API Architecture](#201-api-architecture)
  - [20.2 Endpoint Reference](#202-endpoint-reference)
  - [20.3 Webhook & Event Streaming](#203-webhook--event-streaming)
  - [20.4 SDK / Client Examples](#204-sdk--client-examples)
- [21. Authentication & User Management](#21-authentication--user-management)
  - [21.1 Default Admin User](#211-default-admin-user)
  - [21.2 Authentication Methods](#212-authentication-methods)
  - [21.3 Role-Based Access Control](#213-role-based-access-control)
  - [21.4 Token Management](#214-token-management)
  - [21.5 Secure Authentication Recommendations](#215-secure-authentication-recommendations)
- [22. AI Provider Selection Requirements](#22-ai-provider-selection-requirements)
  - [22.1 Mandatory AI Provider Configuration](#221-mandatory-ai-provider-configuration)
  - [22.2 Provider Role Assignment](#222-provider-role-assignment)
  - [22.3 Configuration Schema](#223-configuration-schema)

---

## 1. Executive Summary

The **Rebuild Cluster** module extends Azure Local Deploy with the ability to safely destroy an existing Azure Local cluster and rebuild it from scratch — while preserving workloads by migrating them to a temporary destination before the tear-down, and migrating them back after the rebuild completes.

This is a complex, multi-phase operation that combines:

1. **Workload discovery & dependency mapping** — Inventory every VM, logical network, image, and Arc resource on the source cluster.
2. **VM backup (optional but recommended)** — Back up all VMs to a UNC share, Azure Blob, or local path before any migration. Users may skip this step, but a prominent warning is displayed: *"Skipping backup means data loss is unrecoverable if migration fails."*
3. **Batch migration** — Move VMs to a temporary target (another Azure Local cluster, a standalone Hyper-V host, or a Hyper-V failover cluster) using Live Migration, Quick Migration, or Export/Import depending on the scenario.
4. **Cluster tear-down** — Unregister the cluster from Azure, destroy the Windows Failover Cluster, clean up Azure AD app registrations, and optionally wipe the OS on every node.
5. **Cluster rebuild (hydration)** — Re-deploy the cluster using the existing `orchestrator.py` 17-stage pipeline, including OS installation, Arc registration, and cloud-orchestrated deployment.
6. **Workload move-back** — Migrate VMs from the temporary target back to the freshly rebuilt cluster and restore logical networks, images, and Arc VM management.
7. **AI-assisted planning** — Optional integration with OpenAI, Azure OpenAI, or Anthropic Claude to analyze dependency maps, generate migration runbooks, estimate downtime, and produce PowerShell scripts on the fly.

```mermaid
flowchart TD
    START["🔄 Rebuild Cluster<br/>Initiated"] --> DISC["Phase 1<br/>Discovery &<br/>Dependency Mapping"]
    DISC --> DECIDE{"Workloads<br/>present?"}
    DECIDE -->|No| TEARDOWN["Phase 4<br/>Cluster Tear-Down"]
    DECIDE -->|Yes| BACKUP{"Backup VMs?"}
    BACKUP -->|Yes| DOBACKUP["Phase 2<br/>Backup VMs<br/>(UNC / Azure Blob)"]
    BACKUP -->|"Skip (⚠️ warning)"| MIGRATE["Phase 3<br/>Migrate Workloads<br/>to Temp Target"]
    DOBACKUP --> MIGRATE
    MIGRATE --> TEARDOWN
    TEARDOWN --> HYDRATE["Phase 5<br/>Cluster Rebuild<br/>(Hydration)"]
    HYDRATE --> DAY2["Phase 6<br/>Restore Day 2<br/>(Networks, Images)"]
    DAY2 --> MOVEBACK{"Move workloads<br/>back?"}
    MOVEBACK -->|Yes| RETURN["Phase 7<br/>Move-Back<br/>Workloads"]
    MOVEBACK -->|No| DONE["✅ Complete"]
    RETURN --> VERIFY["Phase 8<br/>Validation &<br/>Health Check"]
    VERIFY --> DONE

    style START fill:#264653,color:#fff
    style DISC fill:#2a9d8f,color:#fff
    style DOBACKUP fill:#f4a261,color:#000
    style MIGRATE fill:#e9c46a,color:#000
    style TEARDOWN fill:#e76f51,color:#fff
    style HYDRATE fill:#264653,color:#fff
    style DAY2 fill:#2a9d8f,color:#fff
    style RETURN fill:#e9c46a,color:#000
    style VERIFY fill:#52b788,color:#000
    style DONE fill:#52b788,color:#000
```

---

## 2. Problem Statement

Azure Local clusters may need to be destroyed and rebuilt for several reasons:

| Scenario | Description |
|---|---|
| **Major version upgrade** | Moving from Azure Stack HCI 22H2 to Azure Local 2411+ requires a clean deployment (in-place upgrade is not always supported). |
| **Configuration drift** | Over time, manual changes to networking, security, or storage may diverge from the original design. A rebuild restores the golden baseline. |
| **Hardware refresh** | Same chassis, new motherboards/NICs/drives — the cluster identity must be recreated. |
| **Compliance reset** | Regulatory requirements may demand periodic re-provisioning with verified firmware and security baselines. |
| **Disaster recovery testing** | Validate that the organization can actually rebuild from scratch and restore workloads within the RTO. |
| **Cluster corruption** | Failed updates, storage pool corruption, or broken Arc registration — sometimes the fastest fix is a clean rebuild. |

Today, operators must perform these steps manually using dozens of PowerShell commands, Azure Portal clicks, and custom scripts. This module automates the entire lifecycle.

---

## 3. Goals & Non-Goals

### Goals

- Automate end-to-end cluster rebuild with workload preservation
- Support three migration targets: Azure Local ↔ Azure Local, Azure Local → Hyper-V host, Azure Local → Hyper-V cluster
- Support three migration methods: Live Migration (zero downtime), Quick Migration (brief pause), Export/Import (offline)
- Integrate with existing 17-stage pipeline for the rebuild phase
- Provide AI-assisted dependency analysis and migration planning
- Include a web wizard for guided execution
- Support batch operations for multi-VM migrations
- Provide workload move-back after rebuild
- Track and report on every step with rollback capability

### Non-Goals

- Cross-hypervisor migration (e.g., VMware → Azure Local) — separate project
- Azure Migrate integration — separate project scope
- Physical-to-virtual (P2V) conversions
- Azure cloud VM migration (IaaS lift-and-shift)

---

## 4. Architecture Overview

```mermaid
graph TD
    subgraph RebuildModule["rebuild_cluster.py"]
        direction TB
        DISC["WorkloadDiscovery"]
        DEP["DependencyMapper"]
        BATCH["BatchMigrationEngine"]
        TEAR["ClusterTearDown"]
        HYDRATE["ClusterHydration"]
        MOVEBACK["WorkloadMoveBack"]
        AI["AIPlanner (optional)"]
    end

    subgraph Existing["Existing Modules"]
        ORCH["orchestrator.py"]
        DEPLOY_CLUSTER["deploy_cluster.py"]
        DAY2["day2_services.py"]
        REMOTE["remote.py"]
        POST["post_deploy.py"]
        ADD_NODE["add_node.py"]
    end

    subgraph Targets["Migration Targets"]
        AL2["Azure Local Cluster B"]
        HV1["Standalone Hyper-V Host"]
        HVC["Hyper-V Failover Cluster"]
    end

    subgraph AIProviders["AI Providers"]
        OAI["OpenAI GPT-5"]
        AZURE_AI["Azure OpenAI"]
        CLAUDE["Anthropic Claude"]
    end

    RebuildModule --> Existing
    BATCH --> Targets
    AI --> AIProviders
    HYDRATE --> ORCH
    MOVEBACK --> BATCH

    style RebuildModule fill:#264653,color:#fff
    style Existing fill:#2a9d8f,color:#fff
    style Targets fill:#e9c46a,color:#000
    style AIProviders fill:#e76f51,color:#fff
```

### Key Design Decisions

1. **Reuse the existing pipeline** — The rebuild (hydration) phase calls `orchestrator.run_pipeline()` directly. No duplication.
2. **Migration engine is bidirectional** — The same `BatchMigrationEngine` handles both evacuate-to-target and move-back-to-source.
3. **AI is optional** — The module works without AI. AI adds planning intelligence but is never required.
4. **Checkpoint-based execution** — Every phase writes a checkpoint file so the operation can be resumed after failure.

---

## 4b. VM Backup (Pre-Migration Safety Net)

Before any workload migration begins, the rebuild pipeline offers a **VM backup phase**. This step is **enabled by default** and **strongly recommended**. Users may choose to skip it, but the tool will display a prominent, unmissable warning.

### Why Backup Before Rebuild?

- **Migration is destructive in aggregate** — Even though individual Live Migrations are safe, the combination of evacuating *all* VMs, tearing down the cluster, rebuilding from scratch, and migrating back introduces multiple failure points.
- **Cluster tear-down is irreversible** — Once the Windows Failover Cluster is destroyed, Storage Spaces Direct volumes may be wiped. Without a backup, any VM data on those volumes is permanently lost.
- **Export provides a cold-copy safety net** — A Hyper-V Export creates a self-contained copy of each VM (VHDX + configuration) that can be imported to *any* Hyper-V host, independent of the original cluster.

### Backup Methods

| Method | Command | Speed | Portability | Notes |
|---|---|---|---|---|
| **Hyper-V Export** | `Export-VM` | Medium | High — standalone copy | Default. Creates full copy at `backup_path`. VM continues running during export. |
| **Hyper-V Checkpoint** | `Checkpoint-VM` | Fast | Low — requires original storage | Creates a differencing disk. Not portable — useful only as a quick rollback on the same cluster before tear-down. |
| **Azure Blob Upload** | `azcopy` / Azure SDK | Slow (network) | High — off-site | Exports VM then uploads to Azure Blob Storage container. Best for disaster recovery. |

### Backup Flow

```mermaid
flowchart LR
    START["Backup Phase<br/>Initiated"] --> CHECK{"backup.enabled<br/>in config?"}
    CHECK -->|true| ENUM["Enumerate VMs<br/>(exclude list applied)"]
    CHECK -->|false| WARN["⚠️ RED WARNING<br/>Data loss is<br/>unrecoverable if<br/>migration fails"]
    WARN --> CONFIRM{"User confirms<br/>skip?"}
    CONFIRM -->|No| ENUM
    CONFIRM -->|Yes, skip| NEXT["→ Proceed to<br/>Migration"]
    ENUM --> EXPORT["Export VMs<br/>in parallel<br/>(parallel_backups)"]
    EXPORT --> VERIFY{"verify_backup<br/>= true?"}
    VERIFY -->|Yes| VALIDATE["Validate backup<br/>integrity<br/>(file size, VHDX)"]
    VERIFY -->|No| NEXT
    VALIDATE --> NEXT

    style WARN fill:#dc3545,color:#fff
    style CONFIRM fill:#dc3545,color:#fff
    style EXPORT fill:#f4a261,color:#000
    style VALIDATE fill:#52b788,color:#000
```

### Skip Backup Warning

When a user sets `backup.enabled: false` in the YAML config or passes `--skip-backup` on the CLI, the following warning is displayed:

```
┌──────────────────────────────────────────────────────────────────────┐
│  ⚠️  WARNING: VM BACKUP SKIPPED                                     │
│                                                                      │
│  You have chosen to skip the VM backup step.                         │
│                                                                      │
│  If the migration, tear-down, or rebuild process fails for any       │
│  reason, your VM data will be PERMANENTLY LOST with NO way to        │
│  recover it.                                                         │
│                                                                      │
│  This includes:                                                      │
│    • VM virtual hard disks (VHDX)                                    │
│    • VM configuration and snapshots                                  │
│    • Any data stored on cluster Storage Spaces Direct volumes        │
│                                                                      │
│  Are you sure you want to continue without backup? [y/N]             │
└──────────────────────────────────────────────────────────────────────┘
```

In the web wizard, this appears as a **red alert banner** with a mandatory confirmation checkbox.

### PowerShell Commands Used

```powershell
# Export a single VM to a UNC share
Export-VM -Name "sql-server-01" -Path "\\fileserver\backups\rebuild"

# Export all cluster VMs in parallel (2 at a time)
$vms = Get-ClusterGroup -Cluster "source-cluster" | Where-Object GroupType -eq VirtualMachine
$vms | ForEach-Object -Parallel {
    Export-VM -Name $_.Name -Path "\\fileserver\backups\rebuild"
} -ThrottleLimit 2

# Verify backup integrity
$backup = Get-ChildItem "\\fileserver\backups\rebuild\sql-server-01" -Recurse -Filter "*.vhdx"
Test-VHD -Path $backup.FullName

# Upload to Azure Blob (optional)
azcopy copy "\\fileserver\backups\rebuild\*" "https://storageacct.blob.core.windows.net/vm-backups" --recursive
```

### YAML Configuration Reference

```yaml
rebuild:
  backup:
    enabled: true                                    # Default: true
    backup_path: "\\\\fileserver\\backups\\rebuild"  # UNC or local path
    backup_type: "export"                            # "export" | "checkpoint" | "azure_blob"
    azure_blob_container: ""                         # Required if backup_type = "azure_blob"
    parallel_backups: 2                              # Concurrent export operations
    verify_backup: true                              # Validate VHDX integrity post-export
    exclude_vms: []                                  # VMs to skip (e.g. ephemeral/dev VMs)
```

---

## 5. Migration Strategy Deep Dive

### 5.1 Azure Local → Azure Local (Live Migration)

**Best for:** Zero-downtime migrations when a second Azure Local cluster is available.

Live Migration moves running VMs between Hyper-V hosts without any perceived downtime. On Azure Local (which uses Windows Failover Clustering), live migration is the native mechanism for moving VMs between cluster nodes.

**Cross-cluster Live Migration** works when:
- Both clusters are in the same Active Directory domain (or have constrained delegation configured)
- Both clusters have compatible Hyper-V VM configuration versions
- Network connectivity exists between the clusters on the live migration network
- The destination has sufficient CPU, memory, and storage resources
- Processor compatibility mode is enabled on the VMs (if CPU models differ)

```mermaid
sequenceDiagram
    participant SRC as Source Cluster A
    participant VM as Running VM
    participant DST as Target Cluster B

    SRC->>DST: Negotiate live migration
    DST-->>SRC: Accept (resources available)
    SRC->>DST: Transfer VM memory pages (iterative)
    Note over SRC,DST: VM continues running
    SRC->>DST: Transfer final memory delta
    SRC->>DST: Transfer VM state (CPU registers)
    DST->>VM: Resume VM on Cluster B
    Note over DST: Downtime: <1 second
    SRC->>SRC: Clean up source VM resources
```

**PowerShell commands used:**

```powershell
# Enable live migration on both clusters
Enable-VMMigration
Set-VMMigrationNetwork <ManagementNetworkSubnet>

# Move a single VM (with storage)
Move-VM -Name "vm-name" -DestinationHost "targetnode.domain.com" `
    -IncludeStorage `
    -DestinationStoragePath "C:\ClusterStorage\Volume1\vm-name"

# Move a clustered VM (Failover Clustering)
Move-ClusterVirtualMachineRole -Name "vm-name" `
    -MigrationType Live `
    -Node "targetnode"
```

**Constraints:**
- Requires constrained delegation (Kerberos) or CredSSP between clusters
- Storage must be accessible on both ends (or use shared-nothing with `-IncludeStorage`)
- VMs with pass-through disks or physical GPU assignments cannot live migrate

### 5.2 Azure Local → Standalone Hyper-V Host / Cluster (Quick Migration + Export/Import)

**Best for:** When no second Azure Local cluster exists. A standalone Hyper-V Server or Windows Server with Hyper-V role serves as temporary holding.

**Quick Migration** saves the VM state (pauses), moves the VM configuration and storage, then resumes:

```powershell
# Quick Migration — brief downtime (seconds to minutes depending on memory)
Move-ClusterVirtualMachineRole -Name "vm-name" `
    -MigrationType Quick `
    -Node "hvhost01"
```

**Export/Import** — For VMs that cannot be live or quick migrated (e.g., incompatible processor):

```powershell
# Export VM to a file share
Export-VM -Name "vm-name" -Path "\\fileserver\exports\vm-name"

# On the destination Hyper-V host, import the VM
Import-VM -Path "\\fileserver\exports\vm-name\vm-name\Virtual Machines\<guid>.vmcx" `
    -Copy -GenerateNewId
```

**Decision matrix:**

| Criterion | Live Migration | Quick Migration | Export/Import |
|---|---|---|---|
| **Downtime** | < 1 second | Seconds–minutes | Minutes–hours |
| **Requires same domain** | Yes (or delegation) | Yes (if clustered) | No |
| **Requires shared storage** | No (shared-nothing) | No | No (uses file share) |
| **Compatible processors** | Required (or compat mode) | Required (if resuming) | Not required |
| **Works cross-Azure Local to Hyper-V** | Yes (if domain joined) | Yes (if clustered) | Always works |
| **VM stays running** | Yes | No (paused) | No (stopped) |
| **Best use case** | Production with zero-downtime | Acceptable brief pause | Dissimilar hardware |

### 5.3 Shared-Nothing Live Migration

This is the recommended approach when the source and target do **not** share storage (the normal case for Azure Local → external Hyper-V). The VM's memory AND storage are migrated simultaneously over the network.

```powershell
# Shared-nothing live migration — no shared storage needed
Move-VM -Name "vm-name" `
    -DestinationHost "hvhost01.domain.com" `
    -IncludeStorage `
    -DestinationStoragePath "D:\Hyper-V\vm-name" `
    -DestinationNetworkPreference Performance
```

**Network requirements:**
- Minimum 1 Gbps dedicated link (10 Gbps recommended)
- SMB 3.0 with RDMA preferred for storage transfer
- Compression option available if bandwidth is limited (`Set-VMHost -VirtualMachineMigrationPerformanceOption Compression`)

### 5.4 Storage Replica for Data Volumes

For large data volumes that would take too long to migrate with VM live migration, **Storage Replica** can pre-seed the data to the target before the VM migration:

```powershell
# Set up Storage Replica partnership (block-level replication)
New-SRPartnership -SourceComputerName "srcnode01" `
    -SourceRGName "SourceRG" `
    -SourceVolumeName "C:\ClusterStorage\Volume1" `
    -SourceLogVolumeName "C:\ClusterStorage\LogVol1" `
    -DestinationComputerName "dstnode01" `
    -DestinationRGName "DestRG" `
    -DestinationVolumeName "D:\ReplicaVol" `
    -DestinationLogVolumeName "D:\ReplicaLog" `
    -ReplicationMode Asynchronous

# Monitor replication progress
(Get-SRGroup).Replicas | Select-Object NumOfBytesRemaining
```

This is useful for **data-heavy VMs** (SQL Server, file servers) where live migrating terabytes of storage would take too long.

---

## 6. Hydration Process (Cluster Rebuild)

"Hydration" is the process of taking bare-metal or freshly reimaged servers and bringing them all the way up to a fully operational Azure Local cluster. Our existing 17-stage pipeline (`orchestrator.py`) already implements this.

### Tear-Down Procedure (Pre-Hydration)

Before rebuilding, the existing cluster must be cleanly destroyed:

```mermaid
flowchart TD
    subgraph TearDown["Cluster Tear-Down Procedure"]
        T1["1. Drain & pause all nodes<br/>(Suspend-ClusterNode -Drain)"]
        T2["2. Evict nodes from cluster<br/>(Remove-ClusterNode -Force)"]
        T3["3. Destroy the failover cluster<br/>(Remove-Cluster -Force -CleanUpAD)"]
        T4["4. Unregister from Azure<br/>(Unregister-AzStackHCI)"]
        T5["5. Delete Azure resources<br/>(Remove-AzResource cluster + Arc machines)"]
        T6["6. Clean up Azure AD apps<br/>(Remove-AzADApplication)"]
        T7["7. Remove Arc agent<br/>(azcmagent disconnect)"]
        T8["8. (Optional) Wipe OS<br/>(Reimage via iDRAC)"]
    end

    T1 --> T2 --> T3 --> T4 --> T5 --> T6 --> T7 --> T8

    style TearDown fill:#e76f51,color:#fff
    style T1 fill:#264653,color:#fff
    style T2 fill:#264653,color:#fff
    style T3 fill:#264653,color:#fff
    style T4 fill:#264653,color:#fff
    style T5 fill:#264653,color:#fff
    style T6 fill:#264653,color:#fff
    style T7 fill:#264653,color:#fff
    style T8 fill:#264653,color:#fff
```

**PowerShell: Full cluster tear-down sequence**

```powershell
# Step 1: Drain each node
foreach ($node in (Get-ClusterNode)) {
    Suspend-ClusterNode -Name $node.Name -Drain -Wait
}

# Step 2: Evict all nodes except one
$nodes = Get-ClusterNode | Where-Object { $_.State -eq 'Paused' }
foreach ($node in $nodes) {
    Remove-ClusterNode -Name $node.Name -Force
}

# Step 3: Destroy the Failover Cluster
$clusterName = (Get-Cluster).Name
Remove-Cluster -Cluster $clusterName -Force -CleanupAD

# Step 4: Unregister from Azure (on each node)
Unregister-AzStackHCI -SubscriptionId $subId -Force

# Step 5: Delete Azure resources
Remove-AzResource -ResourceId "/subscriptions/$subId/resourceGroups/$rg/providers/Microsoft.AzureStackHCI/clusters/$clusterName" -Force

# Step 6: Clean up Azure AD app registrations
$apps = Get-AzADApplication -DisplayNameStartWith $clusterName
foreach ($app in $apps) { Remove-AzADApplication -ObjectId $app.Id -Force }

# Step 7: Disconnect Arc agent on each node
foreach ($node in $allNodes) {
    Invoke-Command -ComputerName $node -ScriptBlock {
        & "$env:ProgramFiles\AzureConnectedMachineAgent\azcmagent.exe" disconnect --force-local-only
    }
}
```

### Hydration (Rebuild)

After tear-down, the rebuild calls the existing pipeline:

```python
from azure_local_deploy.orchestrator import run_pipeline, load_config

# The same config file, possibly updated
config = load_config("deploy-config.yaml")
run_pipeline(config)  # All 17 stages
```

The rebuild includes:
1. Register Azure resource providers
2. Validate RBAC permissions
3. Prepare Active Directory (recreate OU if destroyed)
4. Validate nodes (hardware, BIOS, network)
5. Environment checker
6. Firmware update (if needed)
7. BIOS configuration
8. OS deployment (reimage via iDRAC virtual media)
9. Network configuration
10. Proxy configuration (if applicable)
11. Time synchronization
12. Security baseline
13. Arc agent deployment
14. Key Vault provisioning
15. Cloud witness
16. Cluster creation
17. Post-deployment tasks

After the pipeline completes, Day 2 services restore logical networks, images, and VM infrastructure.

---

## 7. Workload Move-Back

After the cluster is rebuilt and Day 2 infrastructure is restored, workloads can be migrated back:

```mermaid
flowchart LR
    subgraph Temp["Temporary Target"]
        VM1["VM: sql-server-01"]
        VM2["VM: web-frontend"]
        VM3["VM: dc-02"]
    end

    subgraph Rebuilt["Rebuilt Cluster"]
        N1["Node 1 ✅"]
        N2["Node 2 ✅"]
        LN["Logical Networks ✅"]
    end

    VM1 -->|"Live Migration<br/>(zero downtime)"| N1
    VM2 -->|"Live Migration"| N2
    VM3 -->|"Quick Migration<br/>(brief pause)"| N1

    style Temp fill:#e9c46a,color:#000
    style Rebuilt fill:#2a9d8f,color:#fff
```

**Move-back is the reverse of the evacuate operation:**

1. Verify rebuilt cluster health (Test-Cluster, storage ready, networks up)
2. Restore logical networks on the rebuilt cluster (using Day 2 services)
3. For each VM on the temp target, migrate back using the same method that was used to evacuate
4. Re-register Arc VMs if needed (Arc Resource Bridge re-deployment is part of the hydration)
5. Validate all VMs are running and accessible
6. Optionally decommission the temporary target

**Batch priority for move-back:**

| Priority | VM Category | Method | Reason |
|---|---|---|---|
| 1 | Domain controllers | Quick Migration | Must be back first for AD services |
| 2 | Infrastructure VMs (DNS, DHCP) | Live Migration | Network services needed before app VMs |
| 3 | Database servers | Live Migration + Storage Replica | Minimize data transfer time |
| 4 | Application servers | Live Migration | Largest batch, parallelizable |
| 5 | Dev/test VMs | Export/Import | Lowest priority, can tolerate downtime |

---

## 8. Dependency Mapping

Before migrating any VM, we must understand the dependency graph. A VM running SQL Server may depend on a domain controller VM on the same cluster. Migrating in the wrong order breaks the application.

### Discovery Process

```mermaid
flowchart TD
    subgraph Discovery["Dependency Discovery"]
        D1["1. Enumerate all cluster VMs<br/>Get-VM on each node"]
        D2["2. Collect VM metadata<br/>(CPU, RAM, disk, NIC, VLAN)"]
        D3["3. Query network connections<br/>(Get-NetTCPConnection inside VMs)"]
        D4["4. Map VM-to-VM traffic flows"]
        D5["5. Identify service dependencies<br/>(AD, DNS, SQL, apps)"]
        D6["6. Detect clustered roles<br/>(SQL Always On, DFS)"]
        D7["7. Build dependency graph"]
        D8["8. Generate migration waves<br/>(batch ordering)"]
    end

    D1 --> D2 --> D3 --> D4 --> D5 --> D6 --> D7 --> D8

    style Discovery fill:#2a9d8f,color:#fff
```

### Data Collected Per VM

| Property | Source | Command |
|---|---|---|
| VM Name, ID, State | Hyper-V | `Get-VM` |
| CPU, Memory, Generation | Hyper-V | `Get-VM \| Select-Object *` |
| Virtual disks (VHD/VHDX) | Hyper-V | `Get-VMHardDiskDrive` |
| Disk sizes | Storage | `Get-VHD` |
| Network adapters, VLANs | Hyper-V | `Get-VMNetworkAdapter` |
| IP addresses | Guest OS (via IC) | `Get-VMNetworkAdapter -VMName X \| Select IPAddresses` |
| Open ports / connections | Guest OS | `Get-NetTCPConnection` (via PowerShell Direct) |
| Installed roles/features | Guest OS | `Get-WindowsFeature` (via PowerShell Direct) |
| Cluster roles | Failover Cluster | `Get-ClusterGroup` |
| Storage Spaces Direct volumes | S2D | `Get-VirtualDisk`, `Get-ClusterSharedVolume` |
| Arc registration | Azure | Azure SDK — list Arc machines |

### Dependency Graph Output

The dependency mapper produces a JSON structure:

```json
{
  "cluster": "azlocal-cl-01",
  "discovery_time": "2026-02-18T14:30:00Z",
  "vms": [
    {
      "name": "dc-01",
      "node": "node-01",
      "category": "infrastructure",
      "resources": { "cpu": 4, "memory_gb": 8, "disk_gb": 120 },
      "networks": ["Management-VLAN10"],
      "depends_on": [],
      "depended_by": ["sql-server-01", "web-frontend", "app-backend"]
    },
    {
      "name": "sql-server-01",
      "node": "node-02",
      "category": "database",
      "resources": { "cpu": 8, "memory_gb": 32, "disk_gb": 500 },
      "networks": ["Management-VLAN10", "Data-VLAN20"],
      "depends_on": ["dc-01"],
      "depended_by": ["web-frontend", "app-backend"]
    }
  ],
  "migration_waves": [
    { "wave": 1, "vms": ["dc-01"], "method": "quick_migration" },
    { "wave": 2, "vms": ["sql-server-01"], "method": "live_migration" },
    { "wave": 3, "vms": ["web-frontend", "app-backend"], "method": "live_migration" },
    { "wave": 4, "vms": ["test-vm-01"], "method": "export_import" }
  ]
}
```

---

## 9. AI-Assisted Migration Planning

The AI module provides intelligent assistance at several points in the rebuild workflow. **Users must select either OpenAI or Azure OpenAI** as their primary AI provider for dependency analysis, runbook generation, and interactive planning. **Anthropic Claude Opus 4** is available as a secondary provider specialized for complex infrastructure-as-code (IaC) generation and PowerShell/terminal scripting tasks.

> **Requirement:** At least one AI provider must be configured. The user chooses between OpenAI or Azure OpenAI as their primary provider. Claude Opus 4 can be enabled alongside as the IaC/code engine. See [Section 22](#22-ai-provider-selection-requirements) for full details.

### AI Provider Support

| Provider | Model | Role | Use Case | Configuration |
|---|---|---|---|---|
| **OpenAI** | GPT-5, GPT-5-mini | **Primary** (option A) | Dependency analysis, runbook generation, downtime estimation, interactive chat | `OPENAI_API_KEY` |
| **Azure OpenAI** | GPT-5 (deployment) | **Primary** (option B) | Same as OpenAI but with enterprise data sovereignty; data stays in Azure tenant | `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_KEY` |
| **Anthropic** | Claude Opus 4 | **Secondary** (IaC/code) | Complex PowerShell script generation, infrastructure-as-code, terminal automation, advanced debugging | `ANTHROPIC_API_KEY` |

### AI Capabilities

```mermaid
flowchart TD
    subgraph AI["AI-Assisted Planning"]
        A1["📊 Dependency Analysis<br/>Analyze VM dependency graph,<br/>identify hidden dependencies,<br/>suggest migration order"]
        A2["📋 Runbook Generation<br/>Generate step-by-step<br/>PowerShell runbook for<br/>the entire migration"]
        A3["⏱️ Downtime Estimation<br/>Estimate migration time<br/>per VM based on disk size,<br/>memory, network bandwidth"]
        A4["🔧 Script Generation<br/>Generate PowerShell scripts<br/>on-the-fly for edge cases<br/>or custom configurations"]
        A5["🛡️ Risk Assessment<br/>Identify potential failure<br/>points, suggest mitigations,<br/>flag incompatibilities"]
        A6["💬 Interactive Chat<br/>Answer operator questions<br/>about the migration in<br/>the web wizard"]
    end

    style AI fill:#264653,color:#fff
    style A1 fill:#2a9d8f,color:#fff
    style A2 fill:#2a9d8f,color:#fff
    style A3 fill:#e9c46a,color:#000
    style A4 fill:#e9c46a,color:#000
    style A5 fill:#e76f51,color:#fff
    style A6 fill:#e76f51,color:#fff
```

### AI Prompt Architecture

The AI module sends structured prompts with the dependency map as context:

```python
@dataclass
class AIContext:
    """Context object passed to AI providers."""
    dependency_map: dict          # Full VM dependency graph
    source_cluster: ClusterInfo   # Source cluster specs
    target_info: TargetInfo       # Target host/cluster specs
    migration_config: dict        # User preferences
    constraints: list[str]        # "max_downtime: 30m", "no_weekend_work", etc.

class AIPlanner:
    """AI-assisted migration planning."""

    def __init__(self, provider: str = "azure_openai"):
        self.provider = self._init_provider(provider)

    def analyze_dependencies(self, ctx: AIContext) -> dict:
        """Analyze dependency graph and suggest migration waves."""
        ...

    def generate_runbook(self, ctx: AIContext) -> str:
        """Generate a step-by-step migration runbook (Markdown)."""
        ...

    def estimate_downtime(self, ctx: AIContext) -> dict:
        """Estimate per-VM and total migration downtime."""
        ...

    def generate_script(self, task_description: str, ctx: AIContext) -> str:
        """Generate a PowerShell script for a specific task."""
        ...

    def assess_risk(self, ctx: AIContext) -> dict:
        """Identify risks and suggest mitigations."""
        ...

    def chat(self, question: str, ctx: AIContext) -> str:
        """Interactive Q&A about the migration."""
        ...
```

### Example AI Interaction (Downtime Estimation)

```
User: "Estimate total downtime for migrating all 12 VMs to the Hyper-V host."

AI Response:
Based on your dependency map:
- 3 VMs (dc-01, dns-01, dhcp-01): Quick Migration → ~45 seconds each = ~2.5 min
- 5 VMs (sql-01, sql-02, app-01, app-02, web-01): Live Migration with 10GB NIC
  → 200GB avg storage per VM, ~3 min transfer each = ~15 min
- 4 VMs (dev-01 through dev-04): Export/Import → ~500GB total, ~25 min

Estimated total wall-clock time: ~45 minutes (waves run sequentially)
Estimated application downtime: <5 minutes (only Quick Migration VMs experience downtime)

Risks:
- sql-01 has 500GB data disk; if network drops below 5Gbps, migration may exceed
  maintenance window. Recommend pre-seeding with Storage Replica.
```

---

## 10. Pipeline Stages

The rebuild cluster operation is a 14-stage pipeline:

```mermaid
flowchart TD
    S1["Stage 1<br/>Discovery<br/>Enumerate all VMs,<br/>networks, storage"]
    S2["Stage 2<br/>Dependency Mapping<br/>Build VM dependency graph"]
    S3["Stage 3<br/>AI Planning<br/>(optional)<br/>Generate migration plan"]
    S4["Stage 4<br/>Backup VMs<br/>(optional — skip shows ⚠️ warning)<br/>Export to UNC / Azure Blob"]
    S5["Stage 5<br/>Pre-Migration Validation<br/>Check target capacity,<br/>network, permissions"]
    S6["Stage 6<br/>Evacuate Workloads<br/>Batch-migrate VMs to target"]
    S7["Stage 7<br/>Verify Evacuation<br/>Confirm all VMs running<br/>on target"]
    S8["Stage 8<br/>Cluster Tear-Down<br/>Unregister, destroy cluster,<br/>clean up Azure"]
    S9["Stage 9<br/>Cluster Rebuild<br/>(Hydration)<br/>Run 17-stage pipeline"]
    S10["Stage 10<br/>Day 2 Restore<br/>Recreate logical networks,<br/>upload images"]
    S11["Stage 11<br/>Move-Back Workloads<br/>Migrate VMs from target<br/>back to rebuilt cluster"]
    S12["Stage 12<br/>Post-Move Validation<br/>Health check, Arc re-registration,<br/>connectivity tests"]
    S13["Stage 13<br/>Verify Backups<br/>Confirm backup integrity,<br/>clean up if move-back OK"]
    S14["Stage 14<br/>Cleanup<br/>Remove temp resources,<br/>generate final report"]

    S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7
    S7 --> S8 --> S9 --> S10 --> S11 --> S12 --> S13 --> S14

    style S1 fill:#2a9d8f,color:#fff
    style S2 fill:#2a9d8f,color:#fff
    style S3 fill:#e76f51,color:#fff
    style S4 fill:#f4a261,color:#000
    style S5 fill:#e9c46a,color:#000
    style S6 fill:#264653,color:#fff
    style S7 fill:#264653,color:#fff
    style S8 fill:#e76f51,color:#fff
    style S9 fill:#264653,color:#fff
    style S10 fill:#2a9d8f,color:#fff
    style S11 fill:#264653,color:#fff
    style S12 fill:#52b788,color:#000
    style S13 fill:#f4a261,color:#000
    style S14 fill:#52b788,color:#000
```

---

## 11. YAML Configuration Schema

```yaml
rebuild_cluster:
  # Source cluster (the one being rebuilt)
  source_cluster:
    name: "azlocal-cl-01"
    resource_group: "rg-azlocal-prod"
    subscription_id: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

  # VM backup — recommended before any migration
  backup:
    enabled: true                # Set to false to skip (⚠️ WARNING displayed)
    backup_path: "\\\\fileserver\\backups\\rebuild"  # UNC path for VM exports
    backup_type: "export"        # "export" (Hyper-V Export-VM) | "checkpoint" | "azure_blob"
    azure_blob_container: ""     # For azure_blob type: storage account container URL
    parallel_backups: 2          # How many VMs to back up simultaneously
    verify_backup: true          # Verify backup integrity after export
    exclude_vms: []              # VM names to skip backup (e.g. ephemeral dev VMs)

  # Migration target — where workloads go during rebuild
  migration_target:
    type: "azure_local"          # "azure_local" | "hyperv_host" | "hyperv_cluster"
    host: "10.0.1.50"            # Target host IP (for hyperv_host) or cluster IP
    hosts:                       # For hyperv_cluster, list of hosts
      - host: "10.0.1.50"
        user: "administrator"
        password: "P@ssw0rd!"
      - host: "10.0.1.51"
        user: "administrator"
        password: "P@ssw0rd!"
    user: "administrator"
    password: "P@ssw0rd!"
    storage_path: "C:\\ClusterStorage\\TempMigration"
    network_name: "Management"    # Network for migrated VMs

  # Migration settings
  migration:
    method: "auto"               # "auto" | "live" | "quick" | "export_import"
    parallel_migrations: 2       # How many VMs to migrate simultaneously
    bandwidth_limit_gbps: 10     # Limit migration bandwidth
    use_compression: false       # Use SMB compression for transfers
    use_storage_replica: false   # Pre-seed large volumes with Storage Replica
    max_downtime_minutes: 30     # Max acceptable downtime per VM

  # Batch / Wave configuration
  batches:
    auto_order: true             # Let dependency mapper determine order
    custom_waves:                # Override if auto_order is false
      - wave: 1
        vms: ["dc-01", "dns-01"]
        method: "quick"
      - wave: 2
        vms: ["sql-server-01"]
        method: "live"
      - wave: 3
        vms: ["web-01", "app-01", "app-02"]
        method: "live"

  # Move-back settings
  move_back:
    enabled: true                # Move workloads back after rebuild
    auto_start: false            # Start move-back automatically or wait for approval
    restore_day2: true           # Restore logical networks and images before move-back
    verify_before_move: true     # Run health checks before moving VMs back

  # Tear-down settings
  teardown:
    clean_azure_resources: true  # Delete Azure cluster resource + Arc machines
    clean_ad_objects: true       # Remove AD computer objects and OU
    wipe_os: true                # Reimage servers (required for full rebuild)
    preserve_idrac_config: true  # Don't reset iDRAC settings

  # AI planning (optional)
  ai:
    enabled: false
    provider: "azure_openai"     # "openai" | "azure_openai" | "anthropic"
    model: "gpt-5"               # Model to use
    azure_endpoint: ""           # For azure_openai provider
    generate_runbook: true
    estimate_downtime: true
    risk_assessment: true
    interactive_chat: false      # Enable chat in web wizard
```

---

## 12. CLI Commands

### `rebuild` — Full rebuild with migration

```bash
# Full rebuild with workload migration (backup runs by default)
azure-local-deploy rebuild deploy-config.yaml

# Skip backup (⚠️ WARNING: data loss is unrecoverable if migration fails)
azure-local-deploy rebuild deploy-config.yaml --skip-backup

# Skip AI planning
azure-local-deploy rebuild deploy-config.yaml --no-ai

# Discovery only (don't execute)
azure-local-deploy rebuild deploy-config.yaml --discover-only

# Skip move-back (leave workloads on target)
azure-local-deploy rebuild deploy-config.yaml --skip-move-back

# Resume from a checkpoint
azure-local-deploy rebuild deploy-config.yaml --resume
```

### `backup-vms` — Standalone VM backup

```bash
# Back up all VMs on the cluster to a UNC share
azure-local-deploy backup-vms deploy-config.yaml

# Back up specific VMs only
azure-local-deploy backup-vms deploy-config.yaml --vm sql-server-01 --vm dc-01

# Back up to Azure Blob instead of UNC
azure-local-deploy backup-vms deploy-config.yaml --type azure_blob

# List existing backups
azure-local-deploy backup-vms deploy-config.yaml --list
```

### `discover` — Workload discovery and dependency mapping

```bash
# Discover and map all workloads
azure-local-deploy discover deploy-config.yaml

# Output as JSON
azure-local-deploy discover deploy-config.yaml --format json --output discovery.json

# Include AI analysis
azure-local-deploy discover deploy-config.yaml --ai-analyze
```

### `evacuate` — Migrate workloads off the cluster

```bash
# Evacuate all workloads to the configured target
azure-local-deploy evacuate deploy-config.yaml

# Evacuate specific VMs only
azure-local-deploy evacuate deploy-config.yaml --vm dc-01 --vm sql-server-01

# Dry run — show what would be migrated
azure-local-deploy evacuate deploy-config.yaml --dry-run
```

### `move-back` — Migrate workloads back after rebuild

```bash
# Move all workloads back from the target
azure-local-deploy move-back deploy-config.yaml

# Move specific VMs only
azure-local-deploy move-back deploy-config.yaml --vm dc-01
```

---

## 13. Web Wizard Design

The rebuild wizard is a multi-step guided workflow:

```mermaid
flowchart LR
    S1["Step 1<br/>Source Cluster<br/>Connection"]
    S2["Step 2<br/>Discovery &<br/>Dependency Map"]
    S3["Step 3<br/>Backup VMs<br/>(or Skip ⚠️)"]
    S4["Step 4<br/>Migration Target<br/>Configuration"]
    S5["Step 5<br/>Migration Plan<br/>Review / AI Planning"]
    S6["Step 6<br/>Execute<br/>(Evacuate → Rebuild → Move-Back)"]
    S7["Step 7<br/>Results &<br/>Validation Report"]

    S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7

    style S1 fill:#264653,color:#fff
    style S2 fill:#2a9d8f,color:#fff
    style S3 fill:#f4a261,color:#000
    style S4 fill:#e9c46a,color:#000
    style S5 fill:#e76f51,color:#fff
    style S6 fill:#264653,color:#fff
    style S7 fill:#52b788,color:#000
```

| Step | Template | Description |
|---|---|---|
| 1 | `wizard_rebuild_step1.html` | Enter source cluster credentials, SSH to first node, verify cluster exists |
| 2 | `wizard_rebuild_step2.html` | Display discovered VMs in a table with resource usage, show dependency graph (Mermaid rendered client-side), allow manual overrides |
| 3 | `wizard_rebuild_step3.html` | **Backup VMs** — Choose backup target (UNC share / Azure Blob), select which VMs to back up, or click **Skip Backup** (displays a red warning banner: *"⚠️ Skipping backup — if migration or rebuild fails, data loss is permanent and unrecoverable. Proceed only if you have an independent backup."*). User must check a confirmation checkbox to skip. |
| 4 | `wizard_rebuild_step4.html` | Configure migration target (Azure Local / Hyper-V host / Hyper-V cluster), enter credentials, run capacity check |
| 5 | `wizard_rebuild_step5.html` | Show migration plan with wave ordering, estimated downtime, AI recommendations (if enabled). Allow drag-and-drop reordering of waves. AI chat sidebar (if enabled) |
| 6 | `wizard_rebuild_step6.html` | Real-time progress via Socket.IO. Shows each stage with status bars. Pause/resume/abort controls |
| 7 | `wizard_rebuild_step7.html` | Final validation results, VM health status, backup verification, before/after comparison, generate PDF report |

### AI Chat in Wizard (Optional)

If `ai.interactive_chat` is enabled, Step 4 includes a chat sidebar:

```
┌──────────────────────────────────┬────────────────────┐
│  Migration Plan                  │  AI Assistant       │
│                                  │                     │
│  Wave 1: dc-01 (Quick)          │  "I notice sql-01  │
│  Wave 2: sql-01 (Live)          │   has 500GB of data.│
│  Wave 3: web-01, app-01 (Live)  │   With your 10Gbps │
│  Wave 4: test-01 (Export)       │   link, expect ~3   │
│                                  │   min transfer.    │
│  Est. downtime: 5 min           │   Want me to pre-  │
│  Est. total time: 45 min        │   configure Storage │
│                                  │   Replica instead?" │
│  [▶ Start] [✏ Edit] [🤖 Plan]  │                     │
│                                  │  [Send message...]  │
└──────────────────────────────────┴────────────────────┘
```

---

## 14. Module API Design

### Data Classes

```python
@dataclass
class VMInventoryItem:
    """Discovered VM with metadata."""
    name: str
    node: str                     # Current host node
    state: str                    # Running, Off, Saved, Paused
    generation: int               # 1 or 2
    cpu_count: int
    memory_gb: float
    disk_paths: list[str]         # VHD/VHDX paths
    total_disk_gb: float
    network_adapters: list[dict]  # {name, switch, vlan_id, ip_addresses}
    cluster_role: str | None      # Failover cluster role name
    category: str                 # "infrastructure", "database", "application", "dev_test"
    depends_on: list[str]         # VM names this VM depends on
    depended_by: list[str]        # VM names that depend on this VM
    arc_resource_id: str | None   # Azure Arc resource ID if registered

@dataclass
class MigrationWave:
    """A batch of VMs to migrate together."""
    wave_number: int
    vms: list[str]                # VM names
    method: str                   # "live", "quick", "export_import"
    estimated_downtime_seconds: int
    estimated_transfer_minutes: int

@dataclass
class MigrationPlan:
    """Complete migration plan."""
    source_cluster: str
    target_host: str
    target_type: str              # "azure_local", "hyperv_host", "hyperv_cluster"
    waves: list[MigrationWave]
    total_vms: int
    total_disk_gb: float
    estimated_total_minutes: int
    ai_recommendations: list[str]
    risks: list[str]

@dataclass
class RebuildTask:
    """A single task in the rebuild pipeline."""
    stage: str
    name: str
    success: bool
    message: str
    duration_seconds: float

@dataclass
class RebuildReport:
    """Final rebuild report."""
    tasks: list[RebuildTask]
    vms_evacuated: int
    vms_moved_back: int
    cluster_rebuilt: bool
    total_duration_minutes: float
    ai_report: str | None         # AI-generated summary if enabled
```

### Public API

```python
def backup_vms(
    host: str, user: str, password: str,
    vms: list[VMInventoryItem],
    *,
    backup_path: str = "",
    backup_type: str = "export",            # "export" | "checkpoint" | "azure_blob"
    azure_blob_container: str = "",
    parallel_backups: int = 2,
    verify: bool = True,
    exclude_vms: list[str] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Back up VMs before migration. Uses Hyper-V Export-VM or Azure Blob upload."""

def discover_workloads(
    host: str, user: str, password: str,
    *, ssh_port: int = 22,
    progress_callback: Callable[[str], None] | None = None,
) -> list[VMInventoryItem]:
    """Discover all VMs and their metadata on the cluster."""

def map_dependencies(
    vms: list[VMInventoryItem],
    host: str, user: str, password: str,
    *, use_ai: bool = False,
    ai_provider: str = "azure_openai",
) -> dict:
    """Build dependency graph and generate migration waves."""

def create_migration_plan(
    vms: list[VMInventoryItem],
    dependency_map: dict,
    target_config: dict,
    migration_config: dict,
) -> MigrationPlan:
    """Create an ordered migration plan with wave assignments."""

def evacuate_workloads(
    plan: MigrationPlan,
    source_host: str, source_user: str, source_password: str,
    target_host: str, target_user: str, target_password: str,
    *, progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Execute the migration plan — move all VMs to the target."""

def teardown_cluster(
    host: str, user: str, password: str,
    subscription_id: str, resource_group: str, cluster_name: str,
    *, clean_azure: bool = True, clean_ad: bool = True,
    wipe_os: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Destroy the existing cluster and clean up all resources."""

def rebuild_cluster(
    config_path: str,
    *, progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Run the full 17-stage pipeline to rebuild the cluster."""

def move_back_workloads(
    plan: MigrationPlan,
    target_host: str, target_user: str, target_password: str,
    rebuilt_host: str, rebuilt_user: str, rebuilt_password: str,
    *, progress_callback: Callable[[str], None] | None = None,
) -> list[RebuildTask]:
    """Migrate VMs from the temp target back to the rebuilt cluster."""

def run_rebuild_pipeline(
    config: dict,
    *,
    skip_backup: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> RebuildReport:
    """Orchestrate the complete rebuild: discover → backup → evacuate → teardown → rebuild → move-back."""
```

---

## 15. Error Handling & Rollback

### Checkpoint System

Each stage writes a checkpoint to `~/.azure-local-deploy/rebuild_checkpoint.json`:

```json
{
  "rebuild_id": "rb-20260218-143000",
  "config_hash": "sha256:abc123...",
  "current_stage": "evacuate_workloads",
  "completed_stages": ["discovery", "dependency_mapping", "pre_migration_validation"],
  "failed_stage": null,
  "evacuation_progress": {
    "wave_1": { "status": "completed", "vms": ["dc-01"] },
    "wave_2": { "status": "in_progress", "vms": ["sql-server-01"], "progress_pct": 65 },
    "wave_3": { "status": "pending", "vms": ["web-01", "app-01"] }
  },
  "timestamp": "2026-02-18T14:45:00Z"
}
```

### Rollback Scenarios

| Failed Stage | Rollback Action |
|---|---|
| Evacuation fails mid-wave | VMs already migrated stay on target. Retry the failed VM. User can abort and move everything back. |
| Tear-down fails | Cluster may be in partial state. Log the exact step, provide manual remediation commands. |
| Rebuild (hydration) fails | Standard pipeline retry (`--stage` flag). Cluster is bare metal at this point — nothing to lose. |
| Move-back fails | VMs stay on the temp target (still operational). Retry individual VMs. |

### Safety Guardrails

1. **Mandatory confirmation** — The `rebuild` command requires `--yes` or interactive confirmation before tear-down.
2. **Discovery snapshot** — A full discovery JSON is saved before any destructive action.
3. **Dry-run mode** — `--dry-run` shows the complete plan without executing.
4. **Evacuation verification** — The pipeline verifies ALL VMs are running on the target before proceeding to tear-down. If any VM failed to migrate, the pipeline stops.
5. **Health checks** — Post-rebuild validation runs automatically before move-back.

---

## 16. Security Considerations

| Concern | Mitigation |
|---|---|
| **API authentication** | All API endpoints require authentication (JWT, API Key, Entra ID, or mTLS). See [Section 21](#21-authentication--user-management). |
| **Default admin credentials** | Initial `admin` / `admin123` — forced password change on first login. See [Section 21.1](#211-default-admin-user). |
| **Credentials in config** | Support Azure Key Vault references for passwords. Support environment variables. Never store plaintext secrets. |
| **AI data privacy** | Dependency maps sent to AI contain only VM names, resource sizes, and network topology. No passwords, no data content. Azure OpenAI stays within tenant boundary. |
| **AI API keys** | Stored as environment variables, never in YAML config files. Referenced via `_env` suffix keys. |
| **Live migration authentication** | Use Kerberos constrained delegation (recommended) or CredSSP. CredSSP is disabled after migration. |
| **Tear-down is destructive** | Require `--yes` flag or interactive prompt. Write discovery snapshot before any destructive operation. API requires explicit `confirm_teardown: true` in request body. |
| **Move-back validation** | VMs are verified (ping, RDP, Arc status) before declaring move-back complete. |
| **HTTPS** | Production deployments must use HTTPS. JWT tokens transmitted over HTTP are vulnerable to interception. |
| **Rate limiting** | API endpoints rate-limited to 60 requests/min per user. Auth endpoints limited to 10 requests/min. |

---

## 17. Testing Strategy

| Test Type | What | How |
|---|---|---|
| **Unit tests** | Data classes, dependency mapper logic, wave ordering | `pytest` with mock data |
| **Integration tests** | Discovery against a real 2-node cluster | Test lab with 2 Dell PowerEdge nodes |
| **Migration tests** | Live/Quick/Export migration of test VMs | Test lab with source + target |
| **AI tests** | Prompt formatting, response parsing | Mock AI provider with recorded responses |
| **Wizard tests** | Web wizard step navigation, form validation | Flask test client |
| **End-to-end** | Full rebuild cycle (discover → evacuate → teardown → rebuild → move-back) | Full test lab with 2 clusters |

### Test VMs

| VM | Purpose | Size |
|---|---|---|
| `test-dc-01` | Domain controller | 2 vCPU, 4 GB RAM, 60 GB disk |
| `test-sql-01` | SQL Server (dependency test) | 4 vCPU, 8 GB RAM, 100 GB disk |
| `test-web-01` | Web server (depends on SQL) | 2 vCPU, 4 GB RAM, 40 GB disk |
| `test-dev-01` | Dev/test (no dependencies) | 2 vCPU, 4 GB RAM, 40 GB disk |

---

## 18. Open Questions

| # | Question | Options | Decision |
|---|---|---|---|
| 1 | Should we support VMware targets for interim holding? | Yes / No (scope creep) | **Pending** |
| 2 | Should AI chat in the wizard use streaming (SSE) or polling? | SSE / WebSocket / Polling | **Pending** — SSE recommended for simplicity |
| 3 | Should the move-back automatically start after rebuild, or require manual approval? | Auto / Manual / Configurable | **Pending** — Configurable recommended |
| 4 | Should we support partial rebuilds (e.g., reimage 1 of 3 nodes)? | Yes / No (use existing repair-server) | **Pending** — Microsoft's `Repair-Server` handles single-node; our module handles full cluster rebuild |
| 5 | Should the discovery persist to a database or just JSON files? | JSON / SQLite / Both | **Pending** — JSON recommended for simplicity, SQLite for larger environments |
| 6 | Which AI provider should be the default? | OpenAI / Azure OpenAI / Anthropic | **Pending** — Azure OpenAI recommended for enterprise compliance |
| 7 | Should we generate PDF reports or just Markdown? | PDF / Markdown / Both | **Pending** |
| 8 | Maximum number of VMs we support in a single rebuild operation? | 50 / 100 / unlimited | **Pending** — Recommend 100 with batch throttling |

---

## 19. References

### Microsoft Documentation

| Document | URL |
|---|---|
| Live Migration Overview | https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/manage/live-migration-overview |
| Use Live Migration without Failover Clustering | https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/manage/use-live-migration-without-failover-clustering-to-move-a-virtual-machine |
| Repair a Node on Azure Local | https://learn.microsoft.com/en-us/azure/azure-local/manage/repair-server |
| Manage Azure Stack HCI Cluster Registration | https://learn.microsoft.com/en-us/previous-versions/azure/azure-local/manage/manage-cluster-registration |
| Deploy Azure Local via ARM Template | https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-azure-resource-manager-template |
| Validate an Azure Stack HCI Cluster | https://learn.microsoft.com/en-us/previous-versions/azure/azure-local/deploy/validate |
| Deploy a Quorum Witness | https://learn.microsoft.com/en-us/windows-server/failover-clustering/manage-cluster-quorum |
| Manage Clusters with Windows Admin Center | https://learn.microsoft.com/en-us/previous-versions/azure/azure-local/manage/cluster |
| Move-VM Cmdlet | https://learn.microsoft.com/en-us/powershell/module/hyper-v/move-vm |
| Move-ClusterVirtualMachineRole Cmdlet | https://learn.microsoft.com/en-us/powershell/module/failoverclusters/move-clustervirtualmachinerole |
| Storage Replica Overview | https://learn.microsoft.com/en-us/windows-server/storage/storage-replica/storage-replica-overview |
| Export-VM / Import-VM | https://learn.microsoft.com/en-us/powershell/module/hyper-v/export-vm |

### PowerShell Cmdlets Used

| Cmdlet | Module | Purpose |
|---|---|---|
| `Get-VM` | Hyper-V | Enumerate virtual machines |
| `Move-VM` | Hyper-V | Live / shared-nothing migration |
| `Export-VM` / `Import-VM` | Hyper-V | Offline VM export & import |
| `Move-ClusterVirtualMachineRole` | FailoverClusters | Cluster-aware VM migration |
| `Suspend-ClusterNode` | FailoverClusters | Drain and pause a cluster node |
| `Remove-ClusterNode` | FailoverClusters | Evict a node from the cluster |
| `Remove-Cluster` | FailoverClusters | Destroy the failover cluster |
| `Get-VMHardDiskDrive` | Hyper-V | List VM disk attachments |
| `Get-VHD` | Hyper-V | Get VHD/VHDX size info |
| `Get-VMNetworkAdapter` | Hyper-V | List VM network adapters + IPs |
| `Enable-VMMigration` | Hyper-V | Enable live migration on a host |
| `Set-VMMigrationNetwork` | Hyper-V | Configure migration network |
| `New-SRPartnership` | StorageReplica | Set up block-level replication |
| `Get-SRGroup` | StorageReplica | Monitor replication progress |
| `Repair-Server` | Azure Local | Repair single node (Microsoft's tool) |
| `Test-Cluster` | FailoverClusters | Validate cluster health |
| `Unregister-AzStackHCI` | Az.StackHCI | Unregister cluster from Azure |

### Existing Module Integration Points

| Existing Module | How Rebuild Uses It |
|---|---|
| `orchestrator.py` | Calls `run_pipeline()` for the 17-stage hydration |
| `remote.py` | SSH/PowerShell execution for all discovery and migration commands |
| `day2_services.py` | Restores logical networks, images, and test VMs after rebuild |
| `deploy_cluster.py` | Called by orchestrator during rebuild |
| `post_deploy.py` | Runs post-deployment health checks after rebuild |
| `idrac_client.py` | Used for OS reimage via iDRAC virtual media during tear-down |
| `validate_nodes.py` | Pre-flight checks before rebuild |
| `environment_checker.py` | Microsoft environment validation after rebuild |

---

## 20. REST API Integration

Every step of the rebuild pipeline is exposed as a **REST API endpoint**, allowing full programmatic control via scripts, CI/CD pipelines, external orchestrators, or third-party tools. The API runs on the same Flask server as the web wizard.

### 20.1 API Architecture

```mermaid
flowchart TD
    subgraph Clients["API Clients"]
        CLI["CLI<br/>azure-local-deploy"]
        WEB["Web Wizard<br/>Browser"]
        EXT["External Automation<br/>PowerShell / Python / curl"]
        CICD["CI/CD Pipeline<br/>GitHub Actions / ADO"]
    end

    subgraph API["REST API Layer (Flask)"]
        AUTH["🔐 Auth Middleware<br/>JWT / API Key / Entra ID"]
        ROUTES["API Routes<br/>/api/v1/*"]
        SSE["Event Stream<br/>Server-Sent Events"]
    end

    subgraph Engine["Rebuild Engine"]
        DISC["Discovery"]
        BACKUP["Backup"]
        MIGRATE["Migration"]
        TEARDOWN["Tear-Down"]
        HYDRATE["Hydration"]
        MOVEBACK["Move-Back"]
    end

    CLI --> AUTH
    WEB --> AUTH
    EXT --> AUTH
    CICD --> AUTH
    AUTH --> ROUTES
    ROUTES --> DISC & BACKUP & MIGRATE & TEARDOWN & HYDRATE & MOVEBACK
    ROUTES --> SSE

    style AUTH fill:#e76f51,color:#fff
    style ROUTES fill:#264653,color:#fff
    style SSE fill:#2a9d8f,color:#fff
```

**Base URL:** `http://<host>:5000/api/v1`

All API responses follow a standard envelope:

```json
{
  "status": "success | error",
  "data": { ... },
  "message": "Human-readable description",
  "timestamp": "2026-02-18T14:30:00Z",
  "request_id": "req-abc123"
}
```

### 20.2 Endpoint Reference

#### Authentication

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Authenticate and receive JWT token |
| `POST` | `/api/v1/auth/refresh` | Refresh an expiring JWT token |
| `POST` | `/api/v1/auth/logout` | Invalidate the current token |
| `GET` | `/api/v1/auth/me` | Get current user info |
| `POST` | `/api/v1/auth/api-keys` | Generate a long-lived API key |
| `DELETE` | `/api/v1/auth/api-keys/{key_id}` | Revoke an API key |

#### User Management

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/users` | List all users (admin only) |
| `POST` | `/api/v1/users` | Create a new user (admin only) |
| `PUT` | `/api/v1/users/{user_id}` | Update user (role, password) |
| `DELETE` | `/api/v1/users/{user_id}` | Delete a user (admin only) |
| `POST` | `/api/v1/users/{user_id}/change-password` | Change password |

#### Discovery

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rebuild/discover` | Start workload discovery on source cluster |
| `GET` | `/api/v1/rebuild/discover/{job_id}` | Get discovery job status and results |
| `GET` | `/api/v1/rebuild/discover/{job_id}/vms` | List all discovered VMs |
| `GET` | `/api/v1/rebuild/discover/{job_id}/dependencies` | Get dependency map |

#### Backup

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rebuild/backup` | Start VM backup (or skip with `skip: true`) |
| `GET` | `/api/v1/rebuild/backup/{job_id}` | Get backup job status |
| `GET` | `/api/v1/rebuild/backup/{job_id}/vms` | List backup status per VM |
| `POST` | `/api/v1/rebuild/backup/{job_id}/verify` | Verify backup integrity |
| `GET` | `/api/v1/rebuild/backups` | List all available backups |
| `DELETE` | `/api/v1/rebuild/backups/{backup_id}` | Delete a backup set |

#### AI Planning

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rebuild/ai/plan` | Generate AI migration plan |
| `POST` | `/api/v1/rebuild/ai/analyze` | Analyze dependencies with AI |
| `POST` | `/api/v1/rebuild/ai/estimate` | AI downtime estimation |
| `POST` | `/api/v1/rebuild/ai/script` | Generate a PowerShell script |
| `POST` | `/api/v1/rebuild/ai/chat` | Interactive AI chat (stateful session) |
| `GET` | `/api/v1/rebuild/ai/chat/{session_id}/history` | Get chat history |

#### Evacuation (Migrate Out)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rebuild/evacuate` | Start workload evacuation to target |
| `GET` | `/api/v1/rebuild/evacuate/{job_id}` | Get evacuation status (per wave, per VM) |
| `POST` | `/api/v1/rebuild/evacuate/{job_id}/pause` | Pause evacuation after current wave |
| `POST` | `/api/v1/rebuild/evacuate/{job_id}/resume` | Resume paused evacuation |
| `POST` | `/api/v1/rebuild/evacuate/{job_id}/abort` | Abort evacuation and roll back |
| `POST` | `/api/v1/rebuild/evacuate/verify` | Verify all VMs running on target |

#### Cluster Tear-Down

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rebuild/teardown` | Initiate cluster tear-down |
| `GET` | `/api/v1/rebuild/teardown/{job_id}` | Get tear-down progress |
| `POST` | `/api/v1/rebuild/teardown/{job_id}/confirm` | Confirm destructive action (required) |

#### Cluster Rebuild (Hydration)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rebuild/hydrate` | Start 17-stage cluster rebuild |
| `GET` | `/api/v1/rebuild/hydrate/{job_id}` | Get hydration progress (stage-by-stage) |
| `POST` | `/api/v1/rebuild/hydrate/{job_id}/retry-stage` | Retry a failed stage |

#### Move-Back (Migrate In)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rebuild/move-back` | Start workload move-back to rebuilt cluster |
| `GET` | `/api/v1/rebuild/move-back/{job_id}` | Get move-back status (per wave, per VM) |
| `POST` | `/api/v1/rebuild/move-back/{job_id}/pause` | Pause move-back |
| `POST` | `/api/v1/rebuild/move-back/{job_id}/resume` | Resume move-back |

#### Validation & Reporting

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rebuild/validate` | Run post-rebuild validation |
| `GET` | `/api/v1/rebuild/validate/{job_id}` | Get validation results |
| `GET` | `/api/v1/rebuild/reports` | List all rebuild reports |
| `GET` | `/api/v1/rebuild/reports/{report_id}` | Get detailed report |
| `GET` | `/api/v1/rebuild/reports/{report_id}/pdf` | Download PDF report |

#### Full Pipeline

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/rebuild/pipeline` | Start complete end-to-end rebuild pipeline |
| `GET` | `/api/v1/rebuild/pipeline/{job_id}` | Get pipeline status (all stages) |
| `POST` | `/api/v1/rebuild/pipeline/{job_id}/pause` | Pause pipeline after current stage |
| `POST` | `/api/v1/rebuild/pipeline/{job_id}/resume` | Resume pipeline |
| `POST` | `/api/v1/rebuild/pipeline/{job_id}/abort` | Abort pipeline with rollback |
| `GET` | `/api/v1/rebuild/pipeline/{job_id}/checkpoints` | List checkpoint data |
| `POST` | `/api/v1/rebuild/pipeline/resume-from-checkpoint` | Resume a previous pipeline from checkpoint |

#### Health & Config

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/health` | Server health check |
| `GET` | `/api/v1/config/ai-providers` | List configured AI providers |
| `PUT` | `/api/v1/config/ai-providers` | Update AI provider configuration |
| `GET` | `/api/v1/config/rebuild` | Get current rebuild YAML config |
| `PUT` | `/api/v1/config/rebuild` | Update rebuild configuration |

### Request / Response Examples

#### Login

```bash
curl -X POST http://localhost:5000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}'
```

```json
{
  "status": "success",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 3600,
    "user": {
      "id": 1,
      "username": "admin",
      "role": "admin"
    }
  }
}
```

#### Start Discovery (with Bearer token)

```bash
curl -X POST http://localhost:5000/api/v1/rebuild/discover \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{
    "source_cluster": {
      "host": "node-01.contoso.local",
      "username": "administrator",
      "password": "ClusterP@ss!"
    }
  }'
```

```json
{
  "status": "success",
  "data": {
    "job_id": "disc-20260218-143500",
    "state": "running",
    "started_at": "2026-02-18T14:35:00Z"
  }
}
```

#### Start Full Pipeline

```bash
curl -X POST http://localhost:5000/api/v1/rebuild/pipeline \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "config_file": "deploy-config.yaml",
    "skip_backup": false,
    "use_ai": true,
    "skip_move_back": false,
    "confirm_teardown": true
  }'
```

```json
{
  "status": "success",
  "data": {
    "job_id": "rb-20260218-144000",
    "pipeline_state": "running",
    "current_stage": "discovery",
    "stages": [
      { "name": "discovery", "status": "running" },
      { "name": "dependency_mapping", "status": "pending" },
      { "name": "ai_planning", "status": "pending" },
      { "name": "backup_vms", "status": "pending" },
      { "name": "pre_migration_validation", "status": "pending" },
      { "name": "evacuate_workloads", "status": "pending" },
      { "name": "verify_evacuation", "status": "pending" },
      { "name": "cluster_teardown", "status": "pending" },
      { "name": "cluster_rebuild", "status": "pending" },
      { "name": "day2_restore", "status": "pending" },
      { "name": "move_back", "status": "pending" },
      { "name": "post_move_validation", "status": "pending" },
      { "name": "verify_backups", "status": "pending" },
      { "name": "cleanup", "status": "pending" }
    ],
    "event_stream": "/api/v1/rebuild/pipeline/rb-20260218-144000/events"
  }
}
```

#### Start Backup (skip with warning)

```bash
# Skip backup — server returns warning in response
curl -X POST http://localhost:5000/api/v1/rebuild/backup \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "skip": true, "confirm_skip": true }'
```

```json
{
  "status": "success",
  "data": { "skipped": true },
  "message": "⚠️ WARNING: VM backup skipped. Data loss is unrecoverable if migration or rebuild fails."
}
```

### 20.3 Webhook & Event Streaming

#### Server-Sent Events (SSE)

For real-time progress monitoring, every long-running job exposes an SSE endpoint:

```bash
curl -N -H "Authorization: Bearer $TOKEN" \
  http://localhost:5000/api/v1/rebuild/pipeline/rb-20260218-144000/events
```

```
event: stage_change
data: {"stage": "discovery", "status": "completed", "duration_seconds": 45}

event: stage_change
data: {"stage": "dependency_mapping", "status": "running"}

event: progress
data: {"stage": "evacuate_workloads", "wave": 2, "vm": "sql-01", "progress_pct": 65}

event: warning
data: {"message": "VM dev-03 export taking longer than expected", "vm": "dev-03"}

event: error
data: {"stage": "evacuate_workloads", "vm": "app-02", "error": "Live migration failed: insufficient memory on target"}

event: complete
data: {"job_id": "rb-20260218-144000", "status": "success", "total_duration_seconds": 10800}
```

#### Webhooks (Optional)

Register webhook URLs to receive HTTP POST callbacks:

```bash
curl -X POST http://localhost:5000/api/v1/config/webhooks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://hooks.contoso.com/azure-deploy",
    "events": ["stage_change", "error", "complete"],
    "secret": "webhook-signing-secret-123"
  }'
```

Webhook payloads are signed with HMAC-SHA256 in the `X-Signature-256` header.

### 20.4 SDK / Client Examples

#### Python SDK

```python
from azure_local_deploy.api_client import RebuildAPIClient

client = RebuildAPIClient("http://localhost:5000")
client.login("admin", "admin123")

# Discover workloads
job = client.discover(host="node-01.contoso.local", user="administrator", password="P@ss!")
job.wait()  # Block until done
vms = job.vms()

# Start full pipeline
pipeline = client.start_pipeline(
    config_file="deploy-config.yaml",
    skip_backup=False,
    use_ai=True,
)

# Stream events
for event in pipeline.events():
    print(f"[{event.stage}] {event.message}")
    if event.type == "complete":
        print(f"Rebuild complete in {event.duration_seconds}s")
        break

# Get final report
report = pipeline.report()
report.download_pdf("rebuild-report.pdf")
```

#### PowerShell Client

```powershell
# Login
$token = (Invoke-RestMethod -Uri "http://localhost:5000/api/v1/auth/login" `
    -Method POST -ContentType "application/json" `
    -Body '{"username":"admin","password":"admin123"}').data.access_token

$headers = @{ Authorization = "Bearer $token" }

# Start discovery
$disc = Invoke-RestMethod -Uri "http://localhost:5000/api/v1/rebuild/discover" `
    -Method POST -Headers $headers -ContentType "application/json" `
    -Body (@{
        source_cluster = @{
            host = "node-01.contoso.local"
            username = "administrator"
            password = "ClusterP@ss!"
        }
    } | ConvertTo-Json)

Write-Host "Discovery job started: $($disc.data.job_id)"

# Poll until complete
do {
    Start-Sleep -Seconds 5
    $status = Invoke-RestMethod -Uri "http://localhost:5000/api/v1/rebuild/discover/$($disc.data.job_id)" `
        -Headers $headers
} while ($status.data.state -eq "running")

# Get VMs
$vms = Invoke-RestMethod -Uri "http://localhost:5000/api/v1/rebuild/discover/$($disc.data.job_id)/vms" `
    -Headers $headers
$vms.data | Format-Table Name, State, MemoryGB, TotalDiskGB
```

#### curl — Trigger Each Stage Individually

```bash
TOKEN=$(curl -s -X POST http://localhost:5000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | jq -r '.data.access_token')

# 1. Discover
DISC=$(curl -s -X POST http://localhost:5000/api/v1/rebuild/discover \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source_cluster":{"host":"node-01","username":"admin","password":"pass"}}' \
  | jq -r '.data.job_id')

# 2. Backup
curl -X POST http://localhost:5000/api/v1/rebuild/backup \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"discovery_job_id":"'$DISC'","backup_path":"\\\\server\\backups"}'

# 3. Evacuate
curl -X POST http://localhost:5000/api/v1/rebuild/evacuate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"discovery_job_id":"'$DISC'","target":{"host":"hyper-v-01","type":"hyperv_host"}}'

# 4. Confirm & tear down
curl -X POST http://localhost:5000/api/v1/rebuild/teardown \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"confirm":true}'

# 5. Hydrate (rebuild)
curl -X POST http://localhost:5000/api/v1/rebuild/hydrate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"config_file":"deploy-config.yaml"}'

# 6. Move-back
curl -X POST http://localhost:5000/api/v1/rebuild/move-back \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"discovery_job_id":"'$DISC'"}'

# 7. Validate
curl -X POST http://localhost:5000/api/v1/rebuild/validate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}'
```

---

## 21. Authentication & User Management

All API endpoints (except `GET /api/v1/health`) are protected by authentication. The web wizard and CLI also use the same auth layer when operating against the API.

### 21.1 Default Admin User

On first startup, the application creates an initial admin user:

| Field | Value |
|---|---|
| **Username** | `admin` |
| **Password** | `admin123` |
| **Role** | `admin` |

> **⚠️ IMPORTANT:** The user **must** change the default password on first login. The API will return HTTP `403` with `"password_change_required": true` until the password is changed.

```bash
# First login with default credentials
curl -X POST http://localhost:5000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}'

# Response forces password change
{
  "status": "error",
  "message": "Password change required on first login",
  "data": { "password_change_required": true, "temp_token": "eyJ..." }
}

# Change password
curl -X POST http://localhost:5000/api/v1/users/1/change-password \
  -H "Authorization: Bearer <temp_token>" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "admin123", "new_password": "S3cur3P@ssw0rd!"}'
```

### 21.2 Authentication Methods

The application supports multiple authentication methods. Users choose based on their security requirements:

```mermaid
flowchart TD
    REQ["API Request"] --> AUTH{"Auth Method?"}
    AUTH -->|"Header: Bearer <JWT>"| JWT["JWT Token<br/>Validation"]
    AUTH -->|"Header: X-API-Key"| APIKEY["API Key<br/>Lookup"]
    AUTH -->|"Header: Bearer <Entra>"| ENTRA["Microsoft Entra ID<br/>Token Validation"]
    AUTH -->|"mTLS Client Cert"| MTLS["Certificate<br/>Validation"]
    AUTH -->|"No auth"| REJECT["❌ 401 Unauthorized"]

    JWT --> RBAC["RBAC Check"]
    APIKEY --> RBAC
    ENTRA --> RBAC
    MTLS --> RBAC
    RBAC -->|Authorized| ALLOW["✅ Process Request"]
    RBAC -->|Forbidden| DENY["❌ 403 Forbidden"]

    style JWT fill:#2a9d8f,color:#fff
    style APIKEY fill:#e9c46a,color:#000
    style ENTRA fill:#264653,color:#fff
    style MTLS fill:#e76f51,color:#fff
    style REJECT fill:#d62828,color:#fff
    style DENY fill:#d62828,color:#fff
    style ALLOW fill:#52b788,color:#000
```

| Method | Description | Token Lifetime | Best For |
|---|---|---|---|
| **JWT (default)** | Username/password login → JWT access + refresh tokens | Access: 1 hour, Refresh: 7 days | Interactive users, web wizard |
| **API Key** | Long-lived key generated via API | Until revoked | CI/CD pipelines, scripts, automation |
| **Microsoft Entra ID** | OAuth 2.0 with Azure AD / Entra ID | Per Entra policy | Enterprise environments with SSO |
| **mTLS** | Mutual TLS with client certificates | Certificate validity period | Zero-trust environments, service-to-service |

### 21.3 Role-Based Access Control

| Role | Permissions | Description |
|---|---|---|
| **admin** | Full access | Create/delete users, all API endpoints, configure AI providers, trigger tear-down |
| **operator** | Execute + read | Start/pause/resume pipelines, trigger discovery, evacuate, move-back. Cannot manage users or change config. |
| **viewer** | Read-only | View discovery results, reports, pipeline status. Cannot trigger any actions. |
| **api-service** | Scoped by API key | Custom permissions set per API key at creation time |

```python
# User data model
@dataclass
class User:
    id: int
    username: str
    password_hash: str            # bcrypt hashed
    role: str                     # "admin" | "operator" | "viewer"
    must_change_password: bool    # True on first creation
    created_at: datetime
    last_login: datetime | None
    is_active: bool

@dataclass
class APIKey:
    id: str                       # "ak-" prefix + random
    user_id: int
    name: str                     # Human-readable label
    key_hash: str                 # SHA-256 of the full key
    permissions: list[str]        # ["rebuild:read", "rebuild:execute", "users:manage"]
    expires_at: datetime | None
    created_at: datetime
    last_used: datetime | None
    is_active: bool
```

### 21.4 Token Management

#### JWT Token Structure

```json
{
  "sub": "1",
  "username": "admin",
  "role": "admin",
  "iat": 1739886600,
  "exp": 1739890200,
  "jti": "jwt-abc123"
}
```

- **Access tokens** expire in 1 hour (configurable).
- **Refresh tokens** expire in 7 days and can only be used once.
- JWT secret is generated on first startup and stored in `~/.azure-local-deploy/jwt_secret.key`.
- Token blacklist is maintained for revoked tokens.

#### API Key Format

```
ald_ak_<base64-random-32-bytes>
```

Example: `ald_ak_R2F1dGhpZXIgZGVwbG95IG1hbmFnZXI=`

The full key is shown once at creation. Only the SHA-256 hash is stored server-side.

### 21.5 Secure Authentication Recommendations

The following table presents authentication methods from **least to most secure**. Users should implement the highest level practical for their environment:

| Level | Method | Implementation | Security | Recommendation |
|---|---|---|---|---|
| 🟡 **Basic** | JWT with username/password | Built-in, works out of the box | Passwords can be brute-forced if weak; JWT can be stolen if HTTPS not used | **Development and lab use only. Always use HTTPS in production.** |
| 🟠 **Standard** | API Keys over HTTPS | Generate via API, store securely | Keys don't expire unless set; revocable | **Good for automation. Set expiry. Rotate keys regularly.** |
| 🟢 **Recommended** | Microsoft Entra ID (Azure AD) | OAuth 2.0 / OIDC integration | MFA, conditional access, centralized identity | **Recommended for production.** Integrates with existing Azure AD. Users authenticate via Entra, app validates tokens. |
| 🔵 **Enterprise** | Entra ID + mTLS + short-lived tokens | Mutual TLS + Entra ID token validation | Zero-trust; both client and server authenticated | **Highest security.** For regulated environments. Requires PKI infrastructure. |

#### Microsoft Entra ID Integration (Recommended)

```yaml
# config.yaml — Entra ID authentication
auth:
  method: "entra_id"             # "jwt" | "entra_id" | "mtls"
  entra:
    tenant_id: "your-tenant-id"
    client_id: "your-app-registration-client-id"
    client_secret_env: "ENTRA_CLIENT_SECRET"  # environment variable
    authority: "https://login.microsoftonline.com/your-tenant-id"
    scopes:
      - "api://azure-local-deploy/.default"
    require_mfa: true
    allowed_groups:              # Only users in these groups can access
      - "sg-azure-local-admins"
      - "sg-azure-local-operators"
    group_role_mapping:          # Map Azure AD groups to app roles
      "sg-azure-local-admins": "admin"
      "sg-azure-local-operators": "operator"
      "sg-azure-local-viewers": "viewer"
```

#### Password Policy (for JWT mode)

| Rule | Requirement |
|---|---|
| Minimum length | 12 characters |
| Complexity | At least 1 uppercase, 1 lowercase, 1 digit, 1 special character |
| History | Cannot reuse last 5 passwords |
| Expiry | 90 days (configurable, can be disabled) |
| Lockout | 5 failed attempts → 15-minute lockout |
| Hashing | bcrypt with cost factor 12 |

### YAML Configuration — Authentication

```yaml
# Full auth config in deploy-config.yaml
auth:
  method: "jwt"                  # "jwt" | "entra_id" | "mtls"
  jwt:
    access_token_ttl: 3600       # seconds (1 hour)
    refresh_token_ttl: 604800    # seconds (7 days)
    secret_key_file: "~/.azure-local-deploy/jwt_secret.key"  # auto-generated
  password_policy:
    min_length: 12
    require_uppercase: true
    require_lowercase: true
    require_digit: true
    require_special: true
    max_failed_attempts: 5
    lockout_duration: 900        # seconds (15 min)
    password_expiry_days: 90
  api_keys:
    enabled: true
    max_per_user: 5
    default_expiry_days: 90
  default_admin:
    username: "admin"
    password: "admin123"         # ⚠️ MUST be changed on first login
    force_password_change: true
```

---

## 22. AI Provider Selection Requirements

The rebuild module **requires** users to select an AI provider for intelligent migration planning. The user must choose **OpenAI** or **Azure OpenAI** as their primary provider. **Anthropic Claude Opus 4** is available as a secondary provider optimized for complex coding, infrastructure-as-code (IaC), and terminal/PowerShell script generation.

### 22.1 Mandatory AI Provider Configuration

During first-time setup (via CLI or web wizard), the user is prompted to select their AI provider:

```
┌──────────────────────────────────────────────────────────────────────┐
│  🤖 AI Provider Configuration                                       │
│                                                                      │
│  Select your PRIMARY AI provider for migration planning:             │
│                                                                      │
│    [1] OpenAI          — GPT-5 / GPT-5-mini via OpenAI API          │
│    [2] Azure OpenAI    — GPT-5 via Azure deployment                 │
│                          (data stays in your Azure tenant)           │
│                                                                      │
│  Select: _                                                           │
│                                                                      │
│  ─────────────────────────────────────────────────────────────────   │
│  Optional: Enable Claude Opus 4 for IaC & code generation?          │
│                                                                      │
│    [Y] Yes  — Use Claude for PowerShell scripts, IaC templates,     │
│               and complex terminal automation                        │
│    [N] No   — Use primary provider for all tasks                    │
│                                                                      │
│  Select: _                                                           │
└──────────────────────────────────────────────────────────────────────┘
```

### 22.2 Provider Role Assignment

```mermaid
flowchart TD
    subgraph Primary["Primary Provider (REQUIRED)"]
        direction TB
        P_SELECT{"User selects one"}
        OAI["OpenAI<br/>GPT-5 / GPT-5-mini"]
        AOAI["Azure OpenAI<br/>GPT-5 deployment"]
        P_SELECT -->|Option 1| OAI
        P_SELECT -->|Option 2| AOAI
    end

    subgraph Tasks_Primary["Primary Provider Tasks"]
        T1["📊 Dependency Analysis"]
        T2["📋 Runbook Generation"]
        T3["⏱️ Downtime Estimation"]
        T4["🛡️ Risk Assessment"]
        T5["💬 Interactive Chat"]
    end

    subgraph Secondary["Secondary Provider (OPTIONAL)"]
        CLAUDE["Anthropic<br/>Claude Opus 4"]
    end

    subgraph Tasks_Secondary["Claude Opus 4 Tasks"]
        C1["🔧 PowerShell Script<br/>Generation"]
        C2["🏗️ Infrastructure-as-Code<br/>(ARM, Bicep, Terraform)"]
        C3["💻 Terminal Automation<br/>Complex multi-step commands"]
        C4["🐛 Advanced Debugging<br/>Log analysis, root cause"]
        C5["📝 Code Review<br/>Migration script validation"]
    end

    OAI --> Tasks_Primary
    AOAI --> Tasks_Primary
    CLAUDE --> Tasks_Secondary

    style Primary fill:#264653,color:#fff
    style Secondary fill:#e76f51,color:#fff
    style Tasks_Primary fill:#2a9d8f,color:#fff
    style Tasks_Secondary fill:#f4a261,color:#000
```

| Provider | Role | Tasks | When Used |
|---|---|---|---|
| **OpenAI GPT-5** (or Azure OpenAI GPT-5) | **Primary** | Dependency analysis, runbook generation, downtime estimation, risk assessment, interactive chat | All planning and interactive operations |
| **Anthropic Claude Opus 4** | **Secondary (IaC & Code)** | PowerShell script generation, ARM/Bicep template creation, complex terminal command sequences, log analysis, code review | When generating scripts, IaC, or debugging complex issues. Falls back to primary if Claude not configured. |

### Why This Split?

1. **GPT-5 excels at structured planning** — Fast, excellent at analyzing dependency graphs, generating ordered runbooks, estimating timelines, and holding interactive conversations.
2. **Claude Opus 4 excels at complex code** — Superior at generating long, correct PowerShell scripts, infrastructure-as-code templates, and multi-step terminal automation. Claude's large context window (200K) allows it to process entire cluster configurations and generate comprehensive scripts.
3. **Azure OpenAI provides data sovereignty** — For enterprise customers, Azure OpenAI keeps all data within the Azure tenant, meeting compliance requirements for regulated industries.

### 22.3 Configuration Schema

```yaml
# AI provider configuration in deploy-config.yaml
ai:
  # Primary provider — REQUIRED, user picks one
  primary_provider: "openai"     # "openai" | "azure_openai"

  openai:
    api_key_env: "OPENAI_API_KEY"
    model: "gpt-5"               # For planning tasks
    mini_model: "gpt-5-mini"     # For quick/simple tasks (faster, cheaper)
    max_tokens: 4096
    temperature: 0.3             # Low temp for deterministic planning

  azure_openai:
    endpoint_env: "AZURE_OPENAI_ENDPOINT"
    api_key_env: "AZURE_OPENAI_KEY"
    deployment_name: "gpt-5"     # Your Azure OpenAI deployment name
    api_version: "2025-12-01"
    max_tokens: 4096
    temperature: 0.3

  # Secondary provider — OPTIONAL, for code/IaC generation
  secondary_provider: "anthropic"  # "anthropic" | null (use primary for all)

  anthropic:
    api_key_env: "ANTHROPIC_API_KEY"
    model: "claude-opus-4-20250918"
    max_tokens: 8192             # Larger for code generation
    temperature: 0.2             # Very low for correct code

  # Task routing — controls which provider handles what
  task_routing:
    dependency_analysis: "primary"
    runbook_generation: "primary"
    downtime_estimation: "primary"
    risk_assessment: "primary"
    interactive_chat: "primary"
    script_generation: "secondary"       # PowerShell scripts → Claude
    iac_generation: "secondary"          # ARM/Bicep/Terraform → Claude
    terminal_automation: "secondary"     # Complex shell commands → Claude
    code_review: "secondary"             # Migration script review → Claude
    log_analysis: "secondary"            # Debug log analysis → Claude
    fallback: "primary"                  # If secondary unavailable → use primary
```

### API Endpoints for AI Provider Management

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/config/ai-providers` | Get current AI provider configuration (keys redacted) |
| `PUT` | `/api/v1/config/ai-providers` | Update AI provider settings |
| `POST` | `/api/v1/config/ai-providers/test` | Test connectivity to configured providers |
| `GET` | `/api/v1/config/ai-providers/models` | List available models per provider |

```bash
# Test AI provider connectivity
curl -X POST http://localhost:5000/api/v1/config/ai-providers/test \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "status": "success",
  "data": {
    "primary": {
      "provider": "azure_openai",
      "model": "gpt-5",
      "status": "connected",
      "latency_ms": 245
    },
    "secondary": {
      "provider": "anthropic",
      "model": "claude-opus-4-20250918",
      "status": "connected",
      "latency_ms": 312
    }
  }
}
```

### Web Wizard — AI Provider Setup (Step 0)

Before entering the rebuild wizard, the user configures AI providers if not already done:

```
┌─────────────────────────────────────────────────────────────────────┐
│  🤖 AI Provider Setup                                              │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Primary Provider (required)                                │   │
│  │                                                             │   │
│  │  ○ OpenAI (GPT-5)           ● Azure OpenAI (GPT-5)        │   │
│  │                                                             │   │
│  │  Endpoint: https://mycompany.openai.azure.com              │   │
│  │  API Key:  ●●●●●●●●●●●●●●●●●●●● [Test Connection ✅]      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Secondary Provider (optional — for code & IaC)             │   │
│  │                                                             │   │
│  │  ☑ Enable Claude Opus 4 for script generation               │   │
│  │                                                             │   │
│  │  API Key:  ●●●●●●●●●●●●●●●●●●●● [Test Connection ✅]      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  [Continue to Rebuild Wizard →]                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Appendix A: Migration Method Selection Flowchart

```mermaid
flowchart TD
    START["Select Migration<br/>Method"] --> Q1{"Second Azure Local<br/>cluster available?"}
    Q1 -->|Yes| Q2{"Same AD domain?"}
    Q1 -->|No| Q3{"Hyper-V cluster<br/>available?"}

    Q2 -->|Yes| LIVE["✅ Live Migration<br/>(zero downtime)"]
    Q2 -->|No| Q4{"Can configure<br/>delegation?"}
    Q4 -->|Yes| LIVE
    Q4 -->|No| QUICK["⚡ Quick Migration<br/>(brief pause)"]

    Q3 -->|Yes| Q5{"Same domain?"}
    Q3 -->|No| Q6{"Standalone Hyper-V<br/>host available?"}

    Q5 -->|Yes| LIVE_HVC["✅ Live Migration<br/>to Hyper-V Cluster"]
    Q5 -->|No| QUICK_HVC["⚡ Quick Migration<br/>to Hyper-V Cluster"]

    Q6 -->|Yes| Q7{"VMs < 200GB each?"}
    Q6 -->|No| FAIL["❌ No target available<br/>Cannot proceed"]

    Q7 -->|Yes| SHARED_NOTHING["🔄 Shared-Nothing<br/>Live Migration"]
    Q7 -->|No| Q8{"Network > 10Gbps?"}

    Q8 -->|Yes| SHARED_NOTHING
    Q8 -->|No| EXPORT["📦 Export/Import<br/>(offline, but reliable)"]

    style LIVE fill:#2a9d8f,color:#fff
    style LIVE_HVC fill:#2a9d8f,color:#fff
    style SHARED_NOTHING fill:#52b788,color:#000
    style QUICK fill:#e9c46a,color:#000
    style QUICK_HVC fill:#e9c46a,color:#000
    style EXPORT fill:#e76f51,color:#fff
    style FAIL fill:#d62828,color:#fff
```

---

## Appendix B: Full Rebuild Timeline (Example)

A typical 2-node cluster with 8 VMs, rebuilding with a Hyper-V host as temp target:

```mermaid
gantt
    title Rebuild Cluster Timeline (Estimated)
    dateFormat HH:mm
    axisFormat %H:%M

    section Discovery
    Enumerate VMs & Networks       :d1, 00:00, 5m
    Build Dependency Map           :d2, after d1, 3m

    section Evacuation
    Wave 1 - DC (Quick)            :e1, after d2, 2m
    Wave 2 - SQL (Live)            :e2, after e1, 8m
    Wave 3 - App VMs (Live)        :e3, after e2, 6m
    Wave 4 - Dev VMs (Export)      :e4, after e3, 10m
    Verify All VMs on Target       :e5, after e4, 2m

    section Tear-Down
    Drain & Evict Nodes            :t1, after e5, 3m
    Destroy Cluster                :t2, after t1, 2m
    Unregister Azure + Cleanup     :t3, after t2, 5m

    section Rebuild (Hydration)
    OS Deployment (iDRAC)          :r1, after t3, 30m
    Network + Time + BIOS          :r2, after r1, 15m
    Arc Agent + Azure Deploy       :r3, after r2, 45m
    Post-Deploy + Day 2            :r4, after r3, 15m

    section Move-Back
    Wave 1 - DC (Quick)            :m1, after r4, 2m
    Wave 2 - SQL (Live)            :m2, after m1, 8m
    Wave 3 - App VMs (Live)        :m3, after m2, 6m
    Wave 4 - Dev VMs (Import)      :m4, after m3, 10m
    Final Validation               :m5, after m4, 5m
```

**Estimated total time: ~3 hours**
- Discovery & planning: ~10 min
- Evacuation: ~30 min
- Tear-down: ~10 min
- Rebuild (hydration): ~105 min (OS deploy is the longest)
- Move-back: ~30 min
- Validation: ~5 min

---

*This document is a design proposal. Implementation should begin only after team review and approval of the approach, open questions resolution, and test lab availability confirmation.*
