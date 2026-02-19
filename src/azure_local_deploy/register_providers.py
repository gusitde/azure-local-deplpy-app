"""Register required Azure resource providers for Azure Local deployment.

Microsoft requires 11+ resource providers to be registered on the subscription
before any Azure Local deployment or add-node operation can succeed.

Reference:
    https://learn.microsoft.com/en-us/azure/azure-local/deploy/deployment-arc-register-server-permissions
"""

from __future__ import annotations

import time
from typing import Any, Callable

from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import ResourceManagementClient

from azure_local_deploy.utils import get_logger, retry

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Required resource providers for Azure Local
# ---------------------------------------------------------------------------

REQUIRED_PROVIDERS: list[str] = [
    "Microsoft.HybridCompute",
    "Microsoft.GuestConfiguration",
    "Microsoft.HybridConnectivity",
    "Microsoft.AzureStackHCI",
    "Microsoft.Kubernetes",
    "Microsoft.KubernetesConfiguration",
    "Microsoft.ExtendedLocation",
    "Microsoft.ResourceConnector",
    "Microsoft.HybridContainerService",
    "Microsoft.Attestation",
    "Microsoft.Storage",
    "Microsoft.Insights",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_resource_providers(
    subscription_id: str,
    *,
    providers: list[str] | None = None,
    wait: bool = True,
    poll_interval: int = 10,
    timeout: int = 300,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict[str, str]]:
    """Register all required Azure resource providers on *subscription_id*.

    Parameters
    ----------
    subscription_id:
        The Azure subscription to register providers on.
    providers:
        Override the default list of providers. ``None`` = all required.
    wait:
        If ``True``, block until every provider reaches ``Registered`` state.
    poll_interval:
        Seconds between polling checks when *wait* is ``True``.
    timeout:
        Max seconds to wait for all providers to register.
    progress_callback:
        Optional callable that receives progress messages.

    Returns
    -------
    list of dicts with keys ``namespace`` and ``status`` for each provider.
    """
    _cb = progress_callback or (lambda msg: None)
    target = providers or REQUIRED_PROVIDERS

    log.info("[bold]== Register Azure Resource Providers ==[/]")
    _cb(f"Registering {len(target)} resource providers on subscription {subscription_id}")

    credential = DefaultAzureCredential()
    rm_client = ResourceManagementClient(credential, subscription_id)

    results: list[dict[str, str]] = []

    for ns in target:
        _register_single(rm_client, ns, _cb)
        results.append({"namespace": ns, "status": "Registering"})

    if wait:
        results = _wait_for_registration(
            rm_client, target,
            poll_interval=poll_interval,
            timeout=timeout,
            progress_callback=_cb,
        )

    all_ok = all(r["status"] == "Registered" for r in results)
    if all_ok:
        log.info("[bold green]All resource providers registered successfully.[/]")
        _cb("All resource providers registered ✔")
    else:
        not_ready = [r for r in results if r["status"] != "Registered"]
        log.warning("Some providers are not registered: %s", not_ready)
        _cb(f"Warning: {len(not_ready)} provider(s) not yet Registered")

    return results


def check_resource_providers(
    subscription_id: str,
    *,
    providers: list[str] | None = None,
) -> list[dict[str, str]]:
    """Check registration status of resource providers without registering.

    Returns
    -------
    list of dicts with ``namespace`` and ``status``.
    """
    target = providers or REQUIRED_PROVIDERS
    credential = DefaultAzureCredential()
    rm_client = ResourceManagementClient(credential, subscription_id)

    results: list[dict[str, str]] = []
    for ns in target:
        try:
            provider = rm_client.providers.get(ns)
            results.append({
                "namespace": ns,
                "status": provider.registration_state or "Unknown",
            })
        except Exception as exc:
            results.append({"namespace": ns, "status": f"Error: {exc}"})

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _register_single(
    client: ResourceManagementClient,
    namespace: str,
    cb: Callable[[str], None],
) -> None:
    """Fire-and-forget registration call for a single provider."""
    try:
        provider = client.providers.get(namespace)
        if provider.registration_state == "Registered":
            log.info("  %s – already Registered", namespace)
            cb(f"  {namespace} – already Registered")
            return
    except Exception:
        pass

    log.info("  Registering %s …", namespace)
    cb(f"  Registering {namespace} …")
    try:
        client.providers.register(namespace)
    except Exception as exc:
        log.warning("  Failed to register %s: %s", namespace, exc)
        cb(f"  Warning: failed to register {namespace}: {exc}")


def _wait_for_registration(
    client: ResourceManagementClient,
    namespaces: list[str],
    *,
    poll_interval: int = 10,
    timeout: int = 300,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict[str, str]]:
    """Poll until all providers are Registered or timeout."""
    _cb = progress_callback or (lambda msg: None)
    deadline = time.time() + timeout
    pending = set(namespaces)

    while pending and time.time() < deadline:
        time.sleep(poll_interval)
        still_pending: set[str] = set()
        for ns in pending:
            try:
                provider = client.providers.get(ns)
                state = provider.registration_state or "Unknown"
                if state != "Registered":
                    still_pending.add(ns)
            except Exception:
                still_pending.add(ns)
        pending = still_pending
        if pending:
            _cb(f"  Waiting for {len(pending)} provider(s) to register …")

    # Build final results
    results: list[dict[str, str]] = []
    for ns in namespaces:
        try:
            provider = client.providers.get(ns)
            results.append({
                "namespace": ns,
                "status": provider.registration_state or "Unknown",
            })
        except Exception as exc:
            results.append({"namespace": ns, "status": f"Error: {exc}"})

    return results
