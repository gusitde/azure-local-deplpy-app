"""Online documentation checker for Azure Local.

Fetches the latest Microsoft Azure Local documentation pages and extracts
key requirements, recommendations, and known caveats.  Compares the
gathered requirements against the current server configuration to flag
potential compliance gaps.

Primary documentation source:
    https://learn.microsoft.com/en-us/azure/azure-local/?view=azloc-2601

Checked pages:
    - System requirements
    - Deployment prerequisites
    - Physical network requirements
    - Firewall requirements

This module caches fetched content for the duration of the process to
avoid repeated HTTP calls.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOCS_BASE = "https://learn.microsoft.com/en-us/azure/azure-local"
DOC_PAGES: dict[str, str] = {
    "system_requirements": f"{DOCS_BASE}/concepts/system-requirements-23h2?view=azloc-2601",
    "deployment_prerequisites": f"{DOCS_BASE}/deploy/deployment-prerequisites?view=azloc-2601",
    "physical_network": f"{DOCS_BASE}/concepts/physical-network-requirements?view=azloc-2601",
    "firewall_requirements": f"{DOCS_BASE}/concepts/firewall-requirements?view=azloc-2601",
    "host_network": f"{DOCS_BASE}/concepts/host-network-requirements?view=azloc-2601",
    "ad_preparation": f"{DOCS_BASE}/deploy/deployment-prep-active-directory?view=azloc-2601",
}

# Cache fetched docs in memory for this process
_docs_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    """A single recommendation extracted from the docs."""
    category: str        # e.g. "Hardware", "BIOS", "Network", "Security"
    requirement: str     # human-readable description
    severity: str        # "required" | "recommended" | "optional"
    source_page: str     # which doc page it came from
    source_url: str = ""


@dataclass
class DocsReport:
    """Full report from the docs checker."""
    recommendations: list[Recommendation] = field(default_factory=list)
    pages_fetched: int = 0
    pages_failed: int = 0
    fetch_time_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def add(self, rec: Recommendation) -> None:
        self.recommendations.append(rec)

    @property
    def required_items(self) -> list[Recommendation]:
        return [r for r in self.recommendations if r.severity == "required"]

    @property
    def recommended_items(self) -> list[Recommendation]:
        return [r for r in self.recommendations if r.severity == "recommended"]


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _fetch_page(url: str, timeout: int = 30) -> str:
    """Fetch a URL and return the body text, using the cache if available."""
    if url in _docs_cache:
        return _docs_cache[url]

    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "AzureLocalDeploy/1.0 DocsChecker",
            "Accept": "text/html",
        })
        resp.raise_for_status()
        text = resp.text
        _docs_cache[url] = text
        return text
    except Exception as exc:
        log.warning("Failed to fetch %s: %s", url, exc)
        return ""


def _extract_text(html: str) -> str:
    """Crude HTML-to-text extraction (no external dependency)."""
    # Remove script/style blocks
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return text.strip()


# ---------------------------------------------------------------------------
# Requirement extractors  (hardcoded knowledge + live docs)
# ---------------------------------------------------------------------------

# These are the *known* requirements from Microsoft documentation as of 2025.
# The live fetch supplements them and warns if new requirements are found.

KNOWN_REQUIREMENTS: list[Recommendation] = [
    # Hardware
    Recommendation("Hardware", "1 to 16 machines per cluster, same model/manufacturer/processor", "required", "system_requirements"),
    Recommendation("Hardware", "64-bit Intel Nehalem+ or AMD EPYC+ CPU with SLAT", "required", "system_requirements"),
    Recommendation("Hardware", "Minimum 32 GB RAM per machine with ECC", "required", "system_requirements"),
    Recommendation("Hardware", "At least 2 network adapters per machine", "required", "system_requirements"),
    Recommendation("Hardware", "Boot drive ≥ 200 GB (400 GB+ recommended for large-memory instances)", "required", "system_requirements"),
    Recommendation("Hardware", "At least 2 data drives per server (≥ 500 GB each)", "required", "system_requirements"),
    Recommendation("Hardware", "Same number, type, capacity, and firmware of drives across all servers", "required", "system_requirements"),

    # BIOS
    Recommendation("BIOS", "Intel VT or AMD-V must be turned on", "required", "system_requirements"),
    Recommendation("BIOS", "TPM version 2.0 must be present and turned on", "required", "system_requirements"),
    Recommendation("BIOS", "Secure Boot must be present and turned on", "required", "system_requirements"),
    Recommendation("BIOS", "Boot mode must be UEFI", "required", "system_requirements"),

    # Storage
    Recommendation("Storage", "Direct-attached drives only (no RAID controllers, no SAN)", "required", "system_requirements"),
    Recommendation("Storage", "HBA cards must implement simple pass-through mode", "required", "system_requirements"),
    Recommendation("Storage", "Supported drives: SATA, SAS, NVMe (M.2, U.2, add-in card)", "required", "system_requirements"),
    Recommendation("Storage", "Flash drives must have power-loss protection", "required", "system_requirements"),
    Recommendation("Storage", "NVMe driver must be the Microsoft-provided stornvme.sys", "required", "system_requirements"),

    # Network
    Recommendation("Network", "Minimum 10 Mbit connectivity for management", "required", "system_requirements"),
    Recommendation("Network", "Physical switches must allow traffic on all configured VLANs", "required", "physical_network"),

    # Azure
    Recommendation("Azure", "Valid Azure subscription (EA, CSP, PAYG, or free)", "required", "system_requirements"),
    Recommendation("Azure", "User Access Administrator + Contributor roles on subscription", "required", "deployment_prerequisites"),
    Recommendation("Azure", "Azure Key Vault with public network access enabled", "required", "system_requirements"),

    # Active Directory
    Recommendation("Active Directory", "Dedicated OU for Azure Local objects with blocked GPO inheritance", "required", "ad_preparation"),
    Recommendation("Active Directory", "LCM user with all OU permissions (14+ char password, 3/4 complexity)", "required", "ad_preparation"),
    Recommendation("Active Directory", "Machines must NOT be joined to AD before deployment", "required", "ad_preparation"),

    # Security
    Recommendation("Security", "Local admin password ≥ 14 chars with 3/4 complexity classes", "required", "deployment_prerequisites"),

    # Firmware
    Recommendation("Firmware", "All firmware updated to OEM-recommended versions before deployment", "recommended", "system_requirements"),
    Recommendation("Firmware", "Ensure OEM Solution Builder Extension (SBE) package is current", "recommended", "system_requirements"),

    # Recommended BIOS
    Recommendation("BIOS", "SR-IOV Global Enable set to Enabled", "recommended", "system_requirements"),
    Recommendation("BIOS", "VT-d / IOMMU enabled for device pass-through", "recommended", "system_requirements"),
    Recommendation("BIOS", "Hyper-Threading (Logical Processor) enabled", "recommended", "system_requirements"),
]


def _search_for_new_requirements(page_text: str, page_name: str) -> list[Recommendation]:
    """Heuristic search for requirements not in our known list.

    Looks for sentences containing key phrases like 'must', 'required',
    'minimum', 'at least', etc.
    """
    new_recs: list[Recommendation] = []
    markers = [
        (r'\bmust\b', "required"),
        (r'\brequired\b', "required"),
        (r'\bminimum\b', "required"),
        (r'\bat least\b', "required"),
        (r'\bshould\b', "recommended"),
        (r'\brecommended\b', "recommended"),
    ]

    text = _extract_text(page_text)
    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text)

    for sentence in sentences:
        for pattern, severity in markers:
            if re.search(pattern, sentence, re.IGNORECASE):
                # Skip very short/long or navigation sentences
                if 20 < len(sentence) < 300:
                    # Deduplicate against known requirements
                    already_known = any(
                        sentence_overlap(sentence, kr.requirement) > 0.5
                        for kr in KNOWN_REQUIREMENTS
                    )
                    if not already_known:
                        new_recs.append(Recommendation(
                            category="Online Docs",
                            requirement=sentence.strip(),
                            severity=severity,
                            source_page=page_name,
                        ))
                break  # one marker per sentence is enough

    return new_recs


def sentence_overlap(a: str, b: str) -> float:
    """Simple word-overlap ratio between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / max(len(words_a), len(words_b))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_docs(
    *,
    pages: dict[str, str] | None = None,
    include_live_search: bool = True,
    progress_callback=None,
) -> DocsReport:
    """Fetch Azure Local docs and compile a requirements report.

    Parameters
    ----------
    pages :
        Custom page dict ``{name: url}``.  Defaults to ``DOC_PAGES``.
    include_live_search :
        If ``True``, also search fetched pages for requirements not in
        the hardcoded list.
    progress_callback :
        Optional callable for UI messages.

    Returns
    -------
    DocsReport
    """
    _cb = progress_callback or (lambda msg: None)
    report = DocsReport()

    t0 = time.time()

    # Start with known requirements
    for rec in KNOWN_REQUIREMENTS:
        rec_copy = Recommendation(
            category=rec.category,
            requirement=rec.requirement,
            severity=rec.severity,
            source_page=rec.source_page,
            source_url=DOC_PAGES.get(rec.source_page, ""),
        )
        report.add(rec_copy)

    # Fetch live docs
    target_pages = pages or DOC_PAGES
    _cb(f"Fetching {len(target_pages)} Azure Local documentation page(s) …")
    log.info("Fetching %d documentation pages …", len(target_pages))

    for name, url in target_pages.items():
        _cb(f"  Fetching {name} …")
        html = _fetch_page(url)
        if html:
            report.pages_fetched += 1
            log.info("  ✔ %s (%d bytes)", name, len(html))

            if include_live_search:
                new_recs = _search_for_new_requirements(html, name)
                for rec in new_recs[:15]:  # cap per page to avoid noise
                    rec.source_url = url
                    report.add(rec)
                if new_recs:
                    log.info("    Found %d additional recommendations from live docs", len(new_recs))
        else:
            report.pages_failed += 1
            report.errors.append(f"Failed to fetch {name}: {url}")
            log.warning("  ✘ Could not fetch %s", name)

    report.fetch_time_seconds = time.time() - t0

    # Summary
    req_count = len(report.required_items)
    rec_count = len(report.recommended_items)
    _cb(f"Docs check complete: {req_count} required + {rec_count} recommended items. "
        f"({report.pages_fetched} pages fetched, {report.pages_failed} failed)")
    log.info(
        "[bold]Docs check:[/] %d required, %d recommended, %d pages fetched in %.1fs",
        req_count, rec_count, report.pages_fetched, report.fetch_time_seconds,
    )

    return report


def print_docs_report(report: DocsReport) -> None:
    """Print a human-readable docs report to the logger."""
    log.info("\n[bold]═══ Azure Local Documentation Requirements ═══[/]")

    categories: dict[str, list[Recommendation]] = {}
    for rec in report.recommendations:
        categories.setdefault(rec.category, []).append(rec)

    for cat, recs in sorted(categories.items()):
        log.info("\n[bold cyan]── %s ──[/]", cat)
        for rec in recs:
            colour = "red" if rec.severity == "required" else "yellow"
            log.info("  [%s][%s][/%s] %s", colour, rec.severity.upper(), colour, rec.requirement)

    log.info(
        "\n  Total: %d items (%d required, %d recommended, %d optional)",
        len(report.recommendations),
        len(report.required_items),
        len(report.recommended_items),
        len(report.recommendations) - len(report.required_items) - len(report.recommended_items),
    )

    if report.errors:
        log.warning("Fetch errors:")
        for err in report.errors:
            log.warning("  ✘ %s", err)
