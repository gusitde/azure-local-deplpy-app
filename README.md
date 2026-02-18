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

### Additional features

| Feature | Description |
|---|---|
| **Web Wizard** | Browser-based step-by-step wizard that collects all info (iDRAC IPs, credentials, Azure subscription, NICs, NTP, cluster settings) and launches the deployment with real-time progress via Socket.IO. |
| **Add Node** | Add one or more new Dell servers to an existing Azure Local cluster (including single-node → multi-node conversion). Available via CLI (`add-node`) and the web wizard. |

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

# 5. Add a node to an existing cluster
azure-local-deploy add-node add-node-config.yaml

# 6. Launch the web wizard (no config file needed)
azure-local-deploy web
# → opens http://localhost:5000 with the step-by-step wizard
azure-local-deploy web --port 8080 --debug
```

## Web Wizard

The web wizard provides a browser-based UI that walks you through every setting:

| Wizard | Steps |
|---|---|
| **New Cluster** | 1. Azure Account → 2. Global Settings (ISO, server count) → 3. Server iDRAC creds → 4. NIC config per server → 5. NTP/Time → 6. Cluster settings → Review → Deploy |
| **Add Node** | 1. Azure Account → 2. Existing cluster details → 3. New server iDRAC → 4. NIC config → 5. NTP → Review → Deploy |

Launch with:

```bash
azure-local-deploy web
```

Real-time deployment progress is streamed to the browser via Socket.IO.

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
├── web_app.py             # Flask web wizard + Socket.IO
├── add_node.py            # Add-node-to-cluster pipeline
├── idrac_client.py        # Dell iDRAC Redfish client
├── deploy_os.py           # OS image deployment
├── configure_network.py   # Network adapter configuration
├── configure_time.py      # NTP / time server setup
├── deploy_agent.py        # Azure Arc agent installation
├── deploy_cluster.py      # Azure Local cluster creation
├── remote.py              # SSH / PowerShell helpers
├── utils.py               # Logging, retry, validation
└── templates/             # Jinja2 HTML templates for web wizard
    ├── base.html
    ├── index.html
    ├── wizard_sidebar.html
    ├── wizard_new_cluster_step[1-6].html
    ├── wizard_add_node_step[1-5].html
    ├── wizard_review.html
    └── wizard_progress.html
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
