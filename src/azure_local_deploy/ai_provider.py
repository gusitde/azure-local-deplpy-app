"""AI provider integration module.

Routes tasks to the appropriate AI provider:
  - Primary: OpenAI or Azure OpenAI (GPT-5) — planning, chat, analysis
  - Secondary: Anthropic Claude Opus 4 — code generation, IaC, terminal scripts

Users must configure at least a primary provider (OpenAI or Azure OpenAI).
Claude is optional and recommended for complex scripting tasks.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from azure_local_deploy.models import (
    AIConfig,
    AIProvider,
    AIProviderConfig,
    AITaskType,
    VMInventoryItem,
)
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Base provider interface
# ---------------------------------------------------------------------------

class BaseAIProvider:
    """Abstract provider that wraps a model API."""

    def __init__(self, config: AIProviderConfig):
        self.config = config

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """Send chat-completion request and return the assistant's reply."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIProvider(BaseAIProvider):
    """OpenAI GPT-5 via the openai Python SDK."""

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai package not installed — pip install openai")

        api_key = self.config.api_key or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")

        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=kwargs.get("model", self.config.model),
            messages=messages,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
        )
        return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Azure OpenAI
# ---------------------------------------------------------------------------

class AzureOpenAIProvider(BaseAIProvider):
    """Azure OpenAI GPT-5 via Azure deployments."""

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai package not installed — pip install openai")

        api_key = self.config.api_key or os.environ.get("AZURE_OPENAI_KEY", "")
        endpoint = self.config.endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        if not api_key or not endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY not set")

        client = openai.AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=self.config.api_version,
        )
        resp = client.chat.completions.create(
            model=self.config.deployment_name or self.config.model,
            messages=messages,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature", self.config.temperature),
        )
        return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Anthropic Claude
# ---------------------------------------------------------------------------

class AnthropicProvider(BaseAIProvider):
    """Anthropic Claude Opus 4 — optimised for code & IaC generation."""

    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed — pip install anthropic")

        api_key = self.config.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)

        # Anthropic expects system as a separate param
        system_msg = ""
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                chat_messages.append(m)

        resp = client.messages.create(
            model=kwargs.get("model", self.config.model),
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            system=system_msg or "You are an expert infrastructure and PowerShell engineer.",
            messages=chat_messages,
            temperature=kwargs.get("temperature", self.config.temperature),
        )
        return resp.content[0].text


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _create_provider(cfg: AIProviderConfig) -> BaseAIProvider:
    """Instantiate the correct provider from config."""
    if cfg.provider == AIProvider.OPENAI:
        return OpenAIProvider(cfg)
    elif cfg.provider == AIProvider.AZURE_OPENAI:
        return AzureOpenAIProvider(cfg)
    elif cfg.provider == AIProvider.ANTHROPIC:
        return AnthropicProvider(cfg)
    raise ValueError(f"Unknown AI provider: {cfg.provider}")


# ---------------------------------------------------------------------------
# AI Planner — high-level task interface
# ---------------------------------------------------------------------------

class AIPlanner:
    """AI-assisted migration planning.

    Routes tasks to the correct provider based on the AIConfig task_routing map.
    """

    def __init__(self, config: AIConfig):
        self.config = config
        self._primary = _create_provider(config.primary)
        self._secondary: BaseAIProvider | None = None
        if config.secondary is not None:
            try:
                self._secondary = _create_provider(config.secondary)
            except Exception as exc:
                log.warning("Secondary AI provider init failed (using primary fallback): %s", exc)

    def _get_provider(self, task: str) -> BaseAIProvider:
        """Get the right provider for a task, falling back to primary."""
        route = self.config.task_routing.get(task, "primary")
        if route == "secondary" and self._secondary is not None:
            return self._secondary
        return self._primary

    # -- High-level task methods -------------------------------------------

    def analyze_dependencies(self, vms: list[VMInventoryItem]) -> dict[str, Any]:
        """Analyze VM dependency graph and suggest migration waves."""
        vm_data = [
            {
                "name": vm.name, "node": vm.node, "state": vm.state,
                "cpu": vm.cpu_count, "memory_gb": vm.memory_gb,
                "disk_gb": vm.total_disk_gb, "category": vm.category,
                "depends_on": vm.depends_on, "depended_by": vm.depended_by,
            }
            for vm in vms
        ]
        messages = [
            {"role": "system", "content": (
                "You are an expert Azure Local / Hyper-V migration engineer. "
                "Analyze the VM dependency graph and produce a JSON migration plan with "
                "ordered waves. Infrastructure VMs (DCs, DNS) should migrate first."
            )},
            {"role": "user", "content": (
                f"VMs to migrate:\n```json\n{json.dumps(vm_data, indent=2)}\n```\n\n"
                "Return a JSON object with: waves (list of {wave_number, vms, method, "
                "estimated_downtime_seconds}), risks (list of strings), recommendations (list)."
            )},
        ]
        provider = self._get_provider(AITaskType.DEPENDENCY_ANALYSIS.value)
        result = provider.complete(messages)
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"raw": result}

    def generate_runbook(self, vms: list[VMInventoryItem], target_info: dict) -> str:
        """Generate a step-by-step migration runbook in Markdown."""
        vm_summary = ", ".join(f"{vm.name} ({vm.total_disk_gb}GB)" for vm in vms)
        messages = [
            {"role": "system", "content": (
                "You are an expert Azure Local migration engineer. Generate a detailed "
                "step-by-step runbook in Markdown for migrating VMs from one cluster to a "
                "temporary target, tearing down, rebuilding, and migrating back."
            )},
            {"role": "user", "content": (
                f"VMs: {vm_summary}\n"
                f"Target: {json.dumps(target_info)}\n\n"
                "Generate a runbook with numbered steps, PowerShell commands, estimated times, "
                "and rollback procedures for each major phase."
            )},
        ]
        return self._get_provider(AITaskType.RUNBOOK_GENERATION.value).complete(messages)

    def estimate_downtime(self, vms: list[VMInventoryItem], network_speed_gbps: float = 10.0) -> dict:
        """Estimate per-VM and total migration downtime."""
        vm_data = [{"name": vm.name, "disk_gb": vm.total_disk_gb, "memory_gb": vm.memory_gb}
                    for vm in vms]
        messages = [
            {"role": "system", "content": "You are a Hyper-V migration expert. Estimate migration times."},
            {"role": "user", "content": (
                f"Network speed: {network_speed_gbps} Gbps\n"
                f"VMs:\n{json.dumps(vm_data, indent=2)}\n\n"
                "Return JSON: per_vm (list of {name, method, downtime_seconds, transfer_minutes}), "
                "total_minutes, total_downtime_seconds, notes."
            )},
        ]
        result = self._get_provider(AITaskType.DOWNTIME_ESTIMATION.value).complete(messages)
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"raw": result}

    def generate_script(self, task_description: str, context: str = "") -> str:
        """Generate a PowerShell script for a specific task.  Uses Claude if available."""
        messages = [
            {"role": "system", "content": (
                "You are an expert PowerShell and infrastructure-as-code engineer. "
                "Generate production-ready PowerShell scripts with error handling, "
                "logging, and comments. Only output the script, no explanations."
            )},
            {"role": "user", "content": (
                f"Task: {task_description}\n"
                + (f"Context:\n{context}\n" if context else "")
                + "Generate a complete PowerShell script."
            )},
        ]
        return self._get_provider(AITaskType.SCRIPT_GENERATION.value).complete(
            messages, max_tokens=8192
        )

    def assess_risk(self, vms: list[VMInventoryItem], plan: dict) -> dict:
        """Identify risks and suggest mitigations."""
        messages = [
            {"role": "system", "content": "You are a migration risk analyst for Azure Local environments."},
            {"role": "user", "content": (
                f"Migration plan:\n{json.dumps(plan, indent=2)}\n\n"
                f"VM count: {len(vms)}\n"
                "Identify risks, assign severity (high/medium/low), suggest mitigations. "
                "Return JSON: risks (list of {risk, severity, mitigation})."
            )},
        ]
        result = self._get_provider(AITaskType.RISK_ASSESSMENT.value).complete(messages)
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"raw": result}

    def chat(self, question: str, context: str = "") -> str:
        """Interactive Q&A about the migration."""
        messages = [
            {"role": "system", "content": (
                "You are a helpful Azure Local / Hyper-V migration assistant. "
                "Answer questions clearly with PowerShell examples where relevant."
            )},
        ]
        if context:
            messages.append({"role": "user", "content": f"Context:\n{context}"})
            messages.append({"role": "assistant", "content": "I have the context. What's your question?"})
        messages.append({"role": "user", "content": question})
        return self._get_provider(AITaskType.INTERACTIVE_CHAT.value).complete(messages)

    def generate_iac(self, description: str, iac_type: str = "bicep") -> str:
        """Generate Infrastructure-as-Code template.  Uses Claude if available."""
        messages = [
            {"role": "system", "content": (
                f"You are an expert {iac_type.upper()} / infrastructure-as-code engineer. "
                f"Generate production-ready {iac_type} templates with comments. "
                "Only output the template code."
            )},
            {"role": "user", "content": f"Generate a {iac_type} template for: {description}"},
        ]
        return self._get_provider(AITaskType.IAC_GENERATION.value).complete(
            messages, max_tokens=8192
        )


# ---------------------------------------------------------------------------
# Config loader helper
# ---------------------------------------------------------------------------

def load_ai_config(cfg: dict) -> AIConfig:
    """Build an AIConfig from the 'ai' section of a YAML config dict."""
    ai_cfg = cfg.get("ai", {})
    primary_name = ai_cfg.get("primary_provider", "openai")
    primary_provider = AIProvider(primary_name)

    # Primary
    if primary_provider == AIProvider.OPENAI:
        p_section = ai_cfg.get("openai", {})
        primary = AIProviderConfig(
            provider=AIProvider.OPENAI,
            api_key=os.environ.get(p_section.get("api_key_env", "OPENAI_API_KEY"), ""),
            model=p_section.get("model", "gpt-5"),
            max_tokens=p_section.get("max_tokens", 4096),
            temperature=p_section.get("temperature", 0.3),
        )
    else:
        p_section = ai_cfg.get("azure_openai", {})
        primary = AIProviderConfig(
            provider=AIProvider.AZURE_OPENAI,
            api_key=os.environ.get(p_section.get("api_key_env", "AZURE_OPENAI_KEY"), ""),
            endpoint=os.environ.get(p_section.get("endpoint_env", "AZURE_OPENAI_ENDPOINT"), ""),
            deployment_name=p_section.get("deployment_name", "gpt-5"),
            api_version=p_section.get("api_version", "2025-12-01"),
            model=p_section.get("deployment_name", "gpt-5"),
            max_tokens=p_section.get("max_tokens", 4096),
            temperature=p_section.get("temperature", 0.3),
        )

    # Secondary (optional)
    secondary: AIProviderConfig | None = None
    secondary_name = ai_cfg.get("secondary_provider")
    secondary_provider: AIProvider | None = None
    if secondary_name:
        secondary_provider = AIProvider(secondary_name)
        s_section = ai_cfg.get("anthropic", {})
        secondary = AIProviderConfig(
            provider=AIProvider.ANTHROPIC,
            api_key=os.environ.get(s_section.get("api_key_env", "ANTHROPIC_API_KEY"), ""),
            model=s_section.get("model", "claude-opus-4-20250918"),
            max_tokens=s_section.get("max_tokens", 8192),
            temperature=s_section.get("temperature", 0.2),
        )

    # Task routing
    routing = ai_cfg.get("task_routing", {})

    return AIConfig(
        primary_provider=primary_provider,
        primary=primary,
        secondary_provider=secondary_provider,
        secondary=secondary,
        task_routing={**AIConfig().task_routing, **routing},
    )


def test_provider_connectivity(config: AIProviderConfig) -> dict[str, Any]:
    """Quick connectivity test — send a minimal prompt and measure latency."""
    import time as _time
    provider = _create_provider(config)
    start = _time.time()
    try:
        result = provider.complete([
            {"role": "user", "content": "Reply with exactly: OK"}
        ], max_tokens=10)
        latency = int((_time.time() - start) * 1000)
        return {
            "provider": config.provider.value,
            "model": config.model,
            "status": "connected",
            "latency_ms": latency,
            "response": result.strip()[:50],
        }
    except Exception as exc:
        return {
            "provider": config.provider.value,
            "model": config.model,
            "status": "error",
            "error": str(exc),
        }
