"""CLI entry point – ``azure-local-deploy`` command."""

from __future__ import annotations

import sys

import click
from rich.console import Console

from azure_local_deploy.orchestrator import STAGES, load_config, run_pipeline

console = Console()


@click.group()
@click.version_option(package_name="azure-local-deploy")
def main() -> None:
    """Azure Local Deploy – automated bare-metal to cluster pipeline."""


@main.command()
@click.argument("config_file", type=click.Path(exists=True))
@click.option(
    "--stage", "-s",
    multiple=True,
    type=click.Choice(STAGES, case_sensitive=False),
    help="Run only specific stage(s). Repeat for multiple. Default: all.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing.")
def deploy(config_file: str, stage: tuple[str, ...], dry_run: bool) -> None:
    """Run the full deployment pipeline (or selected stages).

    CONFIG_FILE is the path to a YAML deployment configuration file.
    """
    try:
        cfg = load_config(config_file)
        stages = list(stage) if stage else None
        run_pipeline(cfg, stages=stages, dry_run=dry_run)
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="add-node")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing.")
def add_node_cmd(config_file: str, dry_run: bool) -> None:
    """Add node(s) to an existing Azure Local cluster.

    CONFIG_FILE must include an ``add_node`` section with the existing cluster name.
    """
    from azure_local_deploy.add_node import run_add_node_pipeline

    try:
        cfg = load_config(config_file)
        if dry_run:
            console.print("[yellow]DRY RUN – would add node(s) to cluster[/]")
            console.print(f"  Cluster: {cfg.get('add_node', {}).get('existing_cluster_name', '?')}")
            console.print(f"  New nodes: {len(cfg.get('servers', []))}")
            return
        run_add_node_pipeline(cfg)
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command()
@click.argument("config_file", type=click.Path(exists=True))
def validate(config_file: str) -> None:
    """Validate a deployment config file without executing anything."""
    try:
        cfg = load_config(config_file)
        console.print(f"[green]✔ Config is valid.[/]  Servers: {len(cfg['servers'])}")
    except Exception as exc:
        console.print(f"[bold red]✘ Config validation failed:[/] {exc}")
        sys.exit(1)


@main.command(name="preflight")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--no-abort", is_flag=True, help="Don't abort on failures, just report.")
def preflight(config_file: str, no_abort: bool) -> None:
    """Run pre-flight hardware & BIOS validation on all nodes."""
    from azure_local_deploy.validate_nodes import validate_all_nodes

    try:
        cfg = load_config(config_file)
        reports = validate_all_nodes(
            cfg["servers"],
            abort_on_failure=not no_abort,
        )
        total_fail = sum(r.failures for r in reports)
        if total_fail == 0:
            console.print("[green]✔ All nodes passed pre-flight validation.[/]")
        else:
            console.print(f"[yellow]⚠ {total_fail} failure(s) found across {len(reports)} node(s).[/]")
            if not no_abort:
                sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="check-docs")
def check_docs_cmd() -> None:
    """Fetch Azure Local docs and display current requirements."""
    from azure_local_deploy.docs_checker import check_docs, print_docs_report

    try:
        report = check_docs()
        print_docs_report(report)
        console.print(f"\n[green]✔[/] {len(report.required_items)} required, "
                       f"{len(report.recommended_items)} recommended items found.")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)

@main.command(name="env-check")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--no-abort", is_flag=True, help="Don't abort on critical failures, just report.")
@click.option("--validators", "-v", multiple=True,
              help="Run only specific validators (Connectivity, Hardware, Active Directory, Network, Arc Integration).")
def env_check_cmd(config_file: str, no_abort: bool, validators: tuple[str, ...]) -> None:
    """Run Microsoft AzStackHci.EnvironmentChecker on all nodes.

    Installs the checker via SSH, runs all 5 validators (Connectivity,
    Hardware, Active Directory, Network, Arc Integration), reports results,
    then uninstalls the module before deployment.
    """
    from azure_local_deploy.environment_checker import run_environment_checker_all_nodes

    try:
        cfg = load_config(config_file)
        env_cfg = cfg.get("environment_checker", {})
        val_list = list(validators) if validators else env_cfg.get("validators", None)

        reports = run_environment_checker_all_nodes(
            cfg["servers"],
            validators=val_list,
            install_timeout=int(env_cfg.get("install_timeout", 300)),
            validator_timeout=int(env_cfg.get("validator_timeout", 600)),
            auto_uninstall=env_cfg.get("auto_uninstall", True),
            abort_on_failure=not no_abort,
        )
        total_crit = sum(r.critical_count for r in reports)
        if total_crit == 0:
            console.print("[green]\u2714 All nodes passed Environment Checker.[/]")
        else:
            console.print(f"[yellow]\u26A0 {total_crit} critical issue(s) found.[/]")
            if not no_abort:
                sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)

@main.command(name="list-stages")
def list_stages() -> None:
    """List available pipeline stages."""
    for s in STAGES:
        console.print(f"  • {s}")


@main.command(name="check-providers")
@click.argument("config_file", type=click.Path(exists=True))
def check_providers_cmd(config_file: str) -> None:
    """Check Azure resource provider registration status."""
    from azure_local_deploy.register_providers import check_resource_providers

    try:
        cfg = load_config(config_file)
        results = check_resource_providers(cfg["azure"]["subscription_id"])
        all_ok = True
        for provider, state in results.items():
            icon = "✔" if state == "Registered" else "✘"
            colour = "green" if state == "Registered" else "yellow"
            console.print(f"  [{colour}]{icon}[/{colour}] {provider}: {state}")
            if state != "Registered":
                all_ok = False
        if all_ok:
            console.print("[green]All required providers are registered.[/]")
        else:
            console.print("[yellow]Some providers are not registered. Run deploy with register_providers stage.[/]")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="check-permissions")
@click.argument("config_file", type=click.Path(exists=True))
def check_permissions_cmd(config_file: str) -> None:
    """Validate Azure RBAC role assignments for deployment."""
    from azure_local_deploy.validate_permissions import validate_permissions

    try:
        cfg = load_config(config_file)
        report = validate_permissions(
            subscription_id=cfg["azure"]["subscription_id"],
            resource_group=cfg["azure"]["resource_group"],
        )
        for check in report.checks:
            icon = "✔" if check.assigned else "✘"
            colour = "green" if check.assigned else ("red" if check.critical else "yellow")
            scope = check.scope.split("/")[-1] if "/" in check.scope else check.scope
            console.print(f"  [{colour}]{icon}[/{colour}] {check.role_name} ({scope})")
        if report.all_ok:
            console.print("[green]All required permissions are assigned.[/]")
        else:
            missing = [c.role_name for c in report.checks if not c.assigned]
            console.print(f"[yellow]Missing roles: {', '.join(missing)}[/]")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="prepare-ad")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--verify-only", is_flag=True, help="Only verify AD readiness, don't make changes.")
def prepare_ad_cmd(config_file: str, verify_only: bool) -> None:
    """Prepare Active Directory for Azure Local deployment."""
    from azure_local_deploy.prepare_ad import prepare_active_directory, verify_ad_readiness, ADPrepConfig

    try:
        cfg = load_config(config_file)
        ad_cfg = cfg.get("active_directory", {})
        cluster_cfg = cfg.get("cluster", {})

        if verify_only:
            dc_host = ad_cfg.get("dc_host", "")
            if not dc_host:
                console.print("[yellow]No dc_host in config – cannot verify AD remotely.[/]")
                return
            result = verify_ad_readiness(
                host=dc_host,
                user=ad_cfg.get("dc_user", "Administrator"),
                password=ad_cfg.get("dc_password", ""),
                domain_fqdn=cluster_cfg.get("domain_fqdn", ""),
                ou_name=ad_cfg.get("ou_name", "AzureLocal"),
            )
            for k, v in vars(result).items():
                icon = "✔" if v else "✘"
                console.print(f"  {icon} {k}: {v}")
            return

        ad_config = ADPrepConfig(
            ou_name=ad_cfg.get("ou_name", "AzureLocal"),
            deployment_user=ad_cfg.get("deployment_user", ""),
            deployment_password=ad_cfg.get("deployment_password", ""),
            domain_fqdn=cluster_cfg.get("domain_fqdn", ""),
        )
        dc_host = ad_cfg.get("dc_host", "")
        if not dc_host:
            console.print("[yellow]No dc_host in config. Set active_directory.dc_host in your YAML.[/]")
            return
        prepare_active_directory(
            host=dc_host,
            user=ad_cfg.get("dc_user", "Administrator"),
            password=ad_cfg.get("dc_password", ""),
            config=ad_config,
        )
        console.print("[green]AD preparation complete ✔[/]")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="configure-security")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--profile", type=click.Choice(["recommended", "customized"]), default="recommended",
              help="Security profile to apply.")
@click.option("--check-only", is_flag=True, help="Only check current security status.")
def configure_security_cmd(config_file: str, profile: str, check_only: bool) -> None:
    """Configure or check security settings on all nodes."""
    from azure_local_deploy.configure_security import (
        configure_security, check_security_status,
        RECOMMENDED_SECURITY, CUSTOMIZED_SECURITY,
    )

    try:
        cfg = load_config(config_file)
        sec_profile = RECOMMENDED_SECURITY if profile == "recommended" else CUSTOMIZED_SECURITY

        for idx, srv in enumerate(cfg["servers"], 1):
            host = srv.get("host_ip", srv["idrac_host"])
            user = srv.get("host_user", "Administrator")
            password = srv.get("host_password", srv["idrac_password"])
            console.print(f"\n[bold]Node {idx}: {host}[/]")

            if check_only:
                status = check_security_status(host, user, password)
                for k, v in status.items():
                    console.print(f"  {k}: {v}")
            else:
                configure_security(host, user, password, profile=sec_profile)
                console.print(f"  [green]Security profile '{profile}' applied ✔[/]")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="provision-keyvault")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--vault-name", help="Key Vault name (overrides config).")
def provision_keyvault_cmd(config_file: str, vault_name: str | None) -> None:
    """Provision Azure Key Vault for cluster secrets."""
    from azure_local_deploy.provision_keyvault import provision_keyvault

    try:
        cfg = load_config(config_file)
        kv_cfg = cfg.get("keyvault", {})
        name = vault_name or kv_cfg.get("name", "")
        if not name:
            console.print("[yellow]Provide --vault-name or set keyvault.name in config.[/]")
            return
        provision_keyvault(
            subscription_id=cfg["azure"]["subscription_id"],
            resource_group=cfg["azure"]["resource_group"],
            vault_name=name,
            region=cfg["azure"]["region"],
            tenant_id=cfg["azure"]["tenant_id"],
        )
        console.print(f"[green]Key Vault '{name}' provisioned ✔[/]")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="cloud-witness")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--storage-account", help="Storage account name for cloud witness.")
def cloud_witness_cmd(config_file: str, storage_account: str | None) -> None:
    """Provision cloud witness storage account and configure cluster quorum."""
    from azure_local_deploy.cloud_witness import provision_cloud_witness, configure_cluster_witness

    try:
        cfg = load_config(config_file)
        cw_cfg = cfg.get("cloud_witness", {})
        sa_name = storage_account or cw_cfg.get("storage_account_name", "")
        if not sa_name:
            console.print("[yellow]Provide --storage-account or set cloud_witness.storage_account_name.[/]")
            return

        account_name, key = provision_cloud_witness(
            subscription_id=cfg["azure"]["subscription_id"],
            resource_group=cfg["azure"]["resource_group"],
            storage_account_name=sa_name,
            region=cfg["azure"]["region"],
        )
        console.print(f"[green]Storage account '{account_name}' provisioned ✔[/]")

        # Configure on first server if available
        servers = cfg.get("servers", [])
        if servers:
            srv = servers[0]
            configure_cluster_witness(
                host=srv.get("host_ip", ""),
                user=srv.get("host_user", "Administrator"),
                password=srv.get("host_password", srv.get("idrac_password", "")),
                storage_account_name=account_name,
                storage_account_key=key,
            )
            console.print("[green]Cloud witness configured on cluster ✔[/]")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="post-deploy")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--enable-rdp", is_flag=True, help="Enable RDP on all nodes (disabled by default).")
@click.option("--skip-volumes", is_flag=True, help="Skip workload volume creation.")
def post_deploy_cmd(config_file: str, enable_rdp: bool, skip_volumes: bool) -> None:
    """Run post-deployment tasks (health monitoring, volumes, RDP)."""
    from azure_local_deploy.post_deploy import run_post_deployment

    try:
        cfg = load_config(config_file)
        cluster_cfg = cfg.get("cluster", {})
        servers = cfg.get("servers", [])
        node_hosts = [
            {
                "host": s.get("host_ip", ""),
                "user": s.get("host_user", "Administrator"),
                "password": s.get("host_password", s.get("idrac_password", "")),
            }
            for s in servers
        ]
        report = run_post_deployment(
            subscription_id=cfg["azure"]["subscription_id"],
            resource_group=cfg["azure"]["resource_group"],
            cluster_name=cluster_cfg.get("name", "azlocal-cluster"),
            node_hosts=node_hosts,
            enable_rdp=enable_rdp,
            create_workload_volumes=not skip_volumes,
        )
        for task in report.tasks:
            icon = "✔" if task.success else "✘"
            colour = "green" if task.success else "red"
            console.print(f"  [{colour}]{icon}[/{colour}] {task.name}: {task.message}")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="day2")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--skip-networks", is_flag=True, help="Skip logical network creation.")
@click.option("--skip-images", is_flag=True, help="Skip VM image upload.")
@click.option("--skip-vms", is_flag=True, help="Skip test VM creation.")
def day2_cmd(config_file: str, skip_networks: bool, skip_images: bool, skip_vms: bool) -> None:
    """Run Day 2 services: logical networks, VM images, and test VMs.

    Creates two logical networks (DHCP + Static IP), uploads Windows Server 2025
    and Windows 11 images, and provisions two test VMs with login credentials.
    """
    from azure_local_deploy.day2_services import (
        Day2Report,
        LogicalNetworkConfig,
        TestVMConfig,
        VMImageConfig,
        run_day2_services,
    )

    try:
        cfg = load_config(config_file)
        servers = cfg.get("servers", [])
        if not servers:
            console.print("[bold red]ERROR:[/] No servers defined in config.")
            sys.exit(1)

        node = servers[0]
        host = node.get("host_ip", "")
        user = node.get("host_user", "Administrator")
        password = node.get("host_password", node.get("idrac_password", ""))

        day2_cfg = cfg.get("day2_services", {})

        # Logical networks
        networks = None
        if not skip_networks:
            net_list = day2_cfg.get("logical_networks", [])
            if net_list:
                networks = [
                    LogicalNetworkConfig(
                        name=n.get("name", f"network-{i}"),
                        address_type=n.get("address_type", "DHCP"),
                        address_prefix=n.get("address_prefix", ""),
                        gateway=n.get("gateway", ""),
                        dns_servers=n.get("dns_servers", []),
                        ip_pool_start=n.get("ip_pool_start", ""),
                        ip_pool_end=n.get("ip_pool_end", ""),
                        vm_switch_name=n.get("vm_switch_name", "ConvergedSwitch(compute_management)"),
                        vlan_id=n.get("vlan_id"),
                    )
                    for i, n in enumerate(net_list, 1)
                ]

        # VM images
        images = None
        if not skip_images:
            img_list = day2_cfg.get("vm_images", [])
            if img_list:
                images = [
                    VMImageConfig(
                        name=im.get("name", f"image-{i}"),
                        image_path=im.get("image_path", ""),
                        os_type=im.get("os_type", "Windows"),
                    )
                    for i, im in enumerate(img_list, 1)
                ]

        # Test VMs
        vms = None
        if not skip_vms:
            vm_list = day2_cfg.get("test_vms", [])
            if vm_list:
                vms = [
                    TestVMConfig(
                        name=v.get("name", f"test-vm-{i}"),
                        logical_network=v.get("logical_network", ""),
                        image_name=v.get("image_name", ""),
                        cpu_count=int(v.get("cpu_count", 4)),
                        memory_gb=int(v.get("memory_gb", 8)),
                        storage_gb=int(v.get("storage_gb", 128)),
                        admin_username=v.get("admin_username", "azurelocaladmin"),
                        admin_password=v.get("admin_password", ""),
                    )
                    for i, v in enumerate(vm_list, 1)
                ]

        report = run_day2_services(
            host=host,
            user=user,
            password=password,
            subscription_id=cfg["azure"]["subscription_id"],
            resource_group=cfg["azure"]["resource_group"],
            custom_location_name=day2_cfg.get("custom_location_name", ""),
            logical_networks=networks if not skip_networks else [],
            vm_images=images if not skip_images else [],
            test_vms=vms if not skip_vms else [],
        )

        console.print()
        console.print("[bold]Day 2 Services Report[/]")
        for task in report.tasks:
            icon = "✔" if task.success else "✘"
            colour = "green" if task.success else "red"
            console.print(f"  [{colour}]{icon}[/{colour}] {task.name}: {task.message}")

        if not report.all_ok:
            console.print("\n[bold yellow]Some tasks failed — review the output above.[/]")
            sys.exit(1)
        else:
            console.print("\n[bold green]All Day 2 tasks completed successfully ✔[/]")

    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="list-day2")
@click.argument("config_file", type=click.Path(exists=True))
def list_day2_cmd(config_file: str) -> None:
    """List existing Day 2 resources (networks, images, VMs)."""
    from azure_local_deploy.day2_services import (
        list_logical_networks,
        list_vm_images,
        list_vms,
    )

    try:
        cfg = load_config(config_file)
        servers = cfg.get("servers", [])
        if not servers:
            console.print("[bold red]ERROR:[/] No servers defined in config.")
            sys.exit(1)

        node = servers[0]
        host = node.get("host_ip", "")
        user = node.get("host_user", "Administrator")
        password = node.get("host_password", node.get("idrac_password", ""))

        console.print("[bold cyan]Logical Networks[/]")
        console.print(list_logical_networks(host, user, password))
        console.print()
        console.print("[bold cyan]VM Images[/]")
        console.print(list_vm_images(host, user, password))
        console.print()
        console.print("[bold cyan]Virtual Machines[/]")
        console.print(list_vms(host, user, password))

    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command()
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--skip-backup", is_flag=True, help="Skip VM backup (DANGEROUS — data loss risk).")
@click.option("--skip-move-back", is_flag=True, help="Leave VMs on migration target after rebuild.")
@click.option("--no-ai", is_flag=True, help="Skip AI-assisted planning.")
@click.option("--resume", is_flag=True, help="Resume from last checkpoint.")
@click.option("--discover-only", is_flag=True, help="Only run discovery and exit.")
def rebuild(config_file: str, skip_backup: bool, skip_move_back: bool,
            no_ai: bool, resume: bool, discover_only: bool) -> None:
    """Rebuild an existing Azure Local cluster.

    Evacuates workloads, tears down and rebuilds the cluster, then
    optionally moves workloads back. CONFIG_FILE must include a
    ``rebuild`` section with source/target cluster details.
    """
    from azure_local_deploy.rebuild import (
        discover_workloads, map_dependencies, run_rebuild_pipeline,
    )

    try:
        cfg = load_config(config_file)
        rebuild_cfg = cfg.get("rebuild", {})
        src = rebuild_cfg.get("source_cluster", {})

        if discover_only:
            console.print("[bold cyan]Running discovery only...[/]")
            vms = discover_workloads(
                src.get("host", ""),
                src.get("username", "Administrator"),
                src.get("password", ""),
                ssh_port=int(src.get("ssh_port", 22)),
                progress_callback=lambda m: console.print(f"  {m}"),
            )
            vms = map_dependencies(vms, progress_callback=lambda m: console.print(f"  {m}"))
            console.print(f"\n[bold]Discovered {len(vms)} VM(s):[/]")
            for vm in vms:
                deps = f" → depends on: {', '.join(vm.depends_on)}" if vm.depends_on else ""
                console.print(f"  • {vm.name} ({vm.category}) — {vm.state}, "
                              f"{vm.cpu_count} vCPU, {vm.memory_gb} GB RAM, "
                              f"{vm.total_disk_gb} GB disk{deps}")
            return

        if skip_backup:
            console.print("[bold red]⚠ WARNING: VM backup is DISABLED. "
                          "Data loss is unrecoverable if migration fails![/]")
            if not click.confirm("Continue without backup?"):
                return

        report = run_rebuild_pipeline(
            cfg,
            skip_backup=skip_backup,
            skip_move_back=skip_move_back,
            use_ai=not no_ai,
            resume=resume,
            progress_callback=lambda m: console.print(f"  {m}"),
        )

        console.print()
        console.print("[bold]Rebuild Report[/]")
        console.print(f"  ID:       {report.rebuild_id}")
        console.print(f"  Status:   {report.status}")
        console.print(f"  VMs:      {report.total_vms_migrated}")
        console.print(f"  Duration: {report.total_duration_seconds:.0f}s")
        for task in report.tasks:
            icon = "✔" if task.success else "✘"
            colour = "green" if task.success else "red"
            console.print(f"  [{colour}]{icon}[/{colour}] [{task.stage}] {task.name}: {task.message}")

        if not report.all_ok:
            console.print("\n[bold red]Rebuild completed with failures.[/]")
            sys.exit(1)
        else:
            console.print("\n[bold green]Rebuild completed successfully ✔[/]")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command(name="backup-vms")
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--backup-path", help="Override backup path from config.")
@click.option("--verify/--no-verify", default=True, help="Verify backup integrity.")
def backup_vms_cmd(config_file: str, backup_path: str | None, verify: bool) -> None:
    """Back up VMs on the source cluster before rebuild."""
    from azure_local_deploy.rebuild import backup_vms, discover_workloads

    try:
        cfg = load_config(config_file)
        rc = cfg.get("rebuild", {})
        src = rc.get("source_cluster", {})
        bk = rc.get("backup", {})

        vms = discover_workloads(
            src["host"], src.get("username", "Administrator"), src["password"],
            progress_callback=lambda m: console.print(f"  {m}"),
        )
        tasks = backup_vms(
            src["host"], src.get("username", "Administrator"), src["password"],
            vms,
            backup_path=backup_path or bk.get("backup_path", ""),
            verify=verify,
            progress_callback=lambda m: console.print(f"  {m}"),
        )
        ok = sum(1 for t in tasks if t.success)
        console.print(f"\n[bold]Backup complete: {ok}/{len(tasks)} succeeded[/]")
        for t in tasks:
            icon = "✔" if t.success else "✘"
            colour = "green" if t.success else "red"
            console.print(f"  [{colour}]{icon}[/{colour}] {t.name}: {t.message}")
    except Exception as exc:
        console.print(f"[bold red]ERROR:[/] {exc}")
        sys.exit(1)


@main.command()
@click.option("--host", "-h", default="0.0.0.0", help="Listen address.")
@click.option("--port", "-p", default=5000, type=int, help="Listen port.")
@click.option("--debug", is_flag=True, help="Enable Flask debug mode.")
def web(host: str, port: int, debug: bool) -> None:
    """Launch the web-based deployment wizard."""
    from azure_local_deploy.web_app import create_app

    console.print(f"[bold cyan]Starting web wizard[/] at http://{host}:{port}")
    app, socketio = create_app()
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
