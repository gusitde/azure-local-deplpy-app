"""Centralized Azure credential factory.

Provides a single ``get_credential()`` function used throughout the project.
The default strategy tries **InteractiveBrowserCredential** first (works on
developer workstations without Azure CLI or Az PowerShell module), then
falls back to ``DefaultAzureCredential`` for non-interactive environments
(CI, Azure-hosted, Managed Identity).

The strategy can be overridden via the ``ALD_AZURE_AUTH`` environment variable:
  - ``interactive``  – always use InteractiveBrowserCredential
  - ``default``      – always use DefaultAzureCredential
  - ``auto`` (or unset) – try interactive first, fall back to default
"""

from __future__ import annotations

import os
from functools import lru_cache

from azure_local_deploy.utils import get_logger

log = get_logger(__name__)

_AUTH_STRATEGY = os.environ.get("ALD_AZURE_AUTH", "auto").lower().strip()


@lru_cache(maxsize=1)
def get_credential():
    """Return an Azure ``TokenCredential`` suitable for the current environment.

    The result is cached (singleton) so that all modules share one credential
    and the user is only prompted once for interactive login.
    """
    if _AUTH_STRATEGY == "default":
        return _default_credential()
    if _AUTH_STRATEGY == "interactive":
        return _interactive_credential()

    # auto: try interactive first, fall back to default
    try:
        cred = _interactive_credential()
        # Quick sanity check – attempt a token request
        cred.get_token("https://management.azure.com/.default")
        log.debug("Using InteractiveBrowserCredential for Azure auth")
        return cred
    except Exception:
        log.debug("InteractiveBrowserCredential unavailable, falling back to DefaultAzureCredential")
        return _default_credential()


def _interactive_credential():
    from azure.identity import InteractiveBrowserCredential
    return InteractiveBrowserCredential()


def _default_credential():
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential()
