# Azure Local Deploy

Automated bare-metal-to-cluster deployment of **Azure Local** (formerly Azure Stack HCI) on Dell servers via iDRAC Redfish.

## What it does

| Stage | Description |
|---|---|
| **1. Deploy OS** | Connects to Dell iDRAC via Redfish, mounts the Azure Local ISO as virtual media, sets one-time boot, and powers on the server. Waits for SSH to come up. |
| **2. Configure Network** | Renames NICs by MAC, assigns static IPs, sets DNS, configures VLANs, and verifies gateway connectivity. |
| **3. Configure Time** | Sets NTP peers and (optionally) timezone via `w32tm` on each node. |
| **4. Deploy Agent** | Installs the Azure Connected Machine agent and registers each node with Azure Arc. |
| **5. Deploy Cluster** | Creates the Azure Local cluster resource in Azure and triggers the cloud-orchestrated deployment. Polls until complete. |

## Prerequisites

| Requirement | Details |
|---|---|
| Python | 3.10+ |
| Network | Workstation must reach every iDRAC (HTTPS/443) and every host (SSH/22) |
| ISO | Azure Local OS ISO hosted on HTTP/NFS/CIFS reachable from each iDRAC |
| Azure | Service principal or interactive credentials with Contributor on the target resource group |
| Dell iDRAC | iDRAC 9+ with Redfish enabled and virtual-media license |

## Quick start

```bash
# 1. Clone & install
git clone https://github.com/your-org/azure-local-deploy-app.git
cd azure-local-deploy-app
python -m pip install -e ".[dev]"

# 2. Copy & edit the sample config
cp deploy-config.sample.yaml deploy-config.yaml
# → fill in iDRAC IPs, credentials, MAC addresses, Azure details

# 3. Validate config
azure-local-deploy validate deploy-config.yaml

# 4. Run the full pipeline
azure-local-deploy deploy deploy-config.yaml

# Or run a single stage
azure-local-deploy deploy deploy-config.yaml --stage deploy_os
azure-local-deploy deploy deploy-config.yaml --stage configure_network --stage configure_time

# Dry run (shows plan without executing)
azure-local-deploy deploy deploy-config.yaml --dry-run
```

## Configuration

See [`deploy-config.sample.yaml`](deploy-config.sample.yaml) for a fully commented template.

### Structure

```yaml
global:            # Defaults inherited by all servers
  iso_url: "..."
  ntp_servers: [...]
  timezone: "UTC"

azure:             # Azure subscription & tenant details
  tenant_id: "..."
  subscription_id: "..."
  resource_group: "..."
  region: "eastus"

cluster:           # Cluster-level settings
  name: "..."
  cluster_ip: "..."
  domain_fqdn: "..."

servers:           # Per-server definitions
  - idrac_host: "..."
    nics:
      - adapter_name: Mgmt
        mac_address: "AA:BB:CC:DD:EE:01"
        ip_address: "10.0.1.11"
```

## Pipeline stages

You can run all stages or pick individual ones:

```
deploy_os            Mount ISO & install Azure Local OS
configure_network    Set static IPs, DNS, VLANs
configure_time       Configure NTP
deploy_agent         Install & register Azure Arc agent
deploy_cluster       Create cluster in Azure & deploy
```

## Project layout

```
src/azure_local_deploy/
├── cli.py                 # Click CLI entry point
├── orchestrator.py        # Pipeline controller
├── idrac_client.py        # Dell iDRAC Redfish client
├── deploy_os.py           # OS image deployment
├── configure_network.py   # Network adapter configuration
├── configure_time.py      # NTP / time server setup
├── deploy_agent.py        # Azure Arc agent installation
├── deploy_cluster.py      # Azure Local cluster creation
├── remote.py              # SSH / PowerShell helpers
└── utils.py               # Logging, retry, validation
```

## Authentication

The cluster-deployment stage uses `DefaultAzureCredential` from the Azure SDK.  
In order of priority it tries:

1. Environment variables (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET`)
2. Azure CLI (`az login`)
3. Managed Identity (when running on an Azure VM)

For iDRAC and host SSH credentials, specify them in the YAML config or set environment variables.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run linter
ruff check src/

# Run type checker
mypy src/

# Run tests
pytest tests/ -v
```

## License

MIT
