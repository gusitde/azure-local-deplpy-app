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


@main.command(name="list-stages")
def list_stages() -> None:
    """List available pipeline stages."""
    for s in STAGES:
        console.print(f"  • {s}")


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
