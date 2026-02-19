"""Data models for the Azure Local Deploy application.

Covers: users, API keys, rebuild pipeline, VM inventory, migration plans,
AI provider configuration, and authentication tokens.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    API_SERVICE = "api-service"


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class MigrationMethod(str, Enum):
    LIVE = "live"
    QUICK = "quick"
    EXPORT_IMPORT = "export_import"
    SHARED_NOTHING = "shared_nothing"
    STORAGE_REPLICA = "storage_replica"


class BackupType(str, Enum):
    EXPORT = "export"
    CHECKPOINT = "checkpoint"
    AZURE_BLOB = "azure_blob"


class AIProvider(str, Enum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"


class AITaskType(str, Enum):
    DEPENDENCY_ANALYSIS = "dependency_analysis"
    RUNBOOK_GENERATION = "runbook_generation"
    DOWNTIME_ESTIMATION = "downtime_estimation"
    RISK_ASSESSMENT = "risk_assessment"
    INTERACTIVE_CHAT = "interactive_chat"
    SCRIPT_GENERATION = "script_generation"
    IAC_GENERATION = "iac_generation"
    TERMINAL_AUTOMATION = "terminal_automation"
    CODE_REVIEW = "code_review"
    LOG_ANALYSIS = "log_analysis"


# ---------------------------------------------------------------------------
# User & Authentication
# ---------------------------------------------------------------------------

@dataclass
class User:
    """Application user."""
    id: int
    username: str
    password_hash: str
    role: UserRole = UserRole.OPERATOR
    must_change_password: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_login: datetime | None = None
    is_active: bool = True
    failed_attempts: int = 0
    locked_until: datetime | None = None
    password_history: list[str] = field(default_factory=list)


@dataclass
class APIKey:
    """Long-lived API key for automation."""
    id: str
    user_id: int
    name: str
    key_hash: str
    permissions: list[str] = field(default_factory=lambda: ["rebuild:read", "rebuild:execute"])
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used: datetime | None = None
    is_active: bool = True

    @staticmethod
    def generate_key() -> tuple[str, str]:
        """Generate a new API key and return (full_key, key_hash)."""
        raw = secrets.token_urlsafe(32)
        full_key = f"ald_ak_{raw}"
        key_hash = hashlib.sha256(full_key.encode()).hexdigest()
        return full_key, key_hash


# ---------------------------------------------------------------------------
# VM & Migration
# ---------------------------------------------------------------------------

@dataclass
class VMInventoryItem:
    """Discovered VM with metadata."""
    name: str
    node: str
    state: str = "Running"
    generation: int = 2
    cpu_count: int = 2
    memory_gb: float = 4.0
    disk_paths: list[str] = field(default_factory=list)
    total_disk_gb: float = 0.0
    network_adapters: list[dict[str, Any]] = field(default_factory=list)
    cluster_role: str | None = None
    category: str = "application"
    depends_on: list[str] = field(default_factory=list)
    depended_by: list[str] = field(default_factory=list)
    arc_resource_id: str | None = None


@dataclass
class MigrationWave:
    """A batch of VMs to migrate together."""
    wave_number: int
    vms: list[str] = field(default_factory=list)
    method: str = "live"
    estimated_downtime_seconds: int = 0
    estimated_transfer_minutes: int = 0


@dataclass
class MigrationPlan:
    """Complete migration plan."""
    source_cluster: str = ""
    target_host: str = ""
    target_type: str = "hyperv_host"
    waves: list[MigrationWave] = field(default_factory=list)
    total_vms: int = 0
    total_disk_gb: float = 0.0
    estimated_total_minutes: int = 0
    ai_recommendations: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


@dataclass
class RebuildTask:
    """A single task in the rebuild pipeline."""
    stage: str = ""
    name: str = ""
    success: bool = False
    message: str = ""
    duration_seconds: float = 0.0


@dataclass
class RebuildReport:
    """Final rebuild report."""
    rebuild_id: str = ""
    source_cluster: str = ""
    target_host: str = ""
    status: str = "pending"
    tasks: list[RebuildTask] = field(default_factory=list)
    total_vms_migrated: int = 0
    total_duration_seconds: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    backup_path: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(t.success for t in self.tasks) and self.status == "completed"


# ---------------------------------------------------------------------------
# Pipeline Job
# ---------------------------------------------------------------------------

@dataclass
class PipelineJob:
    """Tracks a running rebuild pipeline job."""
    job_id: str
    state: JobState = JobState.PENDING
    mode: str = "rebuild"
    current_stage: str = ""
    stages: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    logs: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    report: RebuildReport | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for API responses."""
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "mode": self.mode,
            "current_stage": self.current_stage,
            "stages": self.stages,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# AI Configuration
# ---------------------------------------------------------------------------

@dataclass
class AIProviderConfig:
    """Configuration for an AI provider."""
    provider: AIProvider = AIProvider.OPENAI
    api_key: str = ""
    model: str = "gpt-5"
    endpoint: str = ""
    deployment_name: str = ""
    api_version: str = "2025-12-01"
    max_tokens: int = 4096
    temperature: float = 0.3


@dataclass
class AIConfig:
    """Full AI configuration with primary + secondary providers."""
    primary_provider: AIProvider = AIProvider.OPENAI
    primary: AIProviderConfig = field(default_factory=AIProviderConfig)
    secondary_provider: AIProvider | None = None
    secondary: AIProviderConfig | None = None
    task_routing: dict[str, str] = field(default_factory=lambda: {
        "dependency_analysis": "primary",
        "runbook_generation": "primary",
        "downtime_estimation": "primary",
        "risk_assessment": "primary",
        "interactive_chat": "primary",
        "script_generation": "secondary",
        "iac_generation": "secondary",
        "terminal_automation": "secondary",
        "code_review": "secondary",
        "log_analysis": "secondary",
        "fallback": "primary",
    })

    def get_provider_for_task(self, task: str) -> AIProviderConfig:
        """Return the appropriate provider config for a given task."""
        route = self.task_routing.get(task, "primary")
        if route == "secondary" and self.secondary is not None:
            return self.secondary
        return self.primary


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@dataclass
class WebhookConfig:
    """Webhook registration."""
    id: str = ""
    url: str = ""
    events: list[str] = field(default_factory=lambda: ["stage_change", "error", "complete"])
    secret: str = ""
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
