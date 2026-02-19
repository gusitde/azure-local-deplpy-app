"""Python SDK client for the Azure Local Deploy REST API.

Provides a high-level ``RebuildAPIClient`` class that wraps all API v1
endpoints with automatic authentication, retry, and response parsing.

Usage::

    from azure_local_deploy.api_client import RebuildAPIClient

    client = RebuildAPIClient("http://localhost:5000")
    client.login("admin", "admin123")

    # Discover VMs
    vms = client.discover(host="10.0.1.11", password="P@ssw0rd!")

    # Start full pipeline
    job_id = client.start_pipeline(skip_backup=False)

    # Stream events
    for event in client.stream_events(job_id):
        print(event)

    # Get report
    report = client.get_report(job_id)
"""

from __future__ import annotations

import json
import time
from typing import Any, Generator

import requests


class APIError(Exception):
    """Raised when the API returns a non-success status."""

    def __init__(self, message: str, status_code: int = 0, data: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data


class RebuildAPIClient:
    """High-level Python SDK for the Azure Local Deploy API v1."""

    def __init__(self, base_url: str = "http://localhost:5000", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> dict[str, Any]:
        """Authenticate and store tokens."""
        data = self._post("/api/v1/auth/login", json={"username": username, "password": password})
        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]
        self._session.headers["Authorization"] = f"Bearer {self._access_token}"
        return data

    def set_api_key(self, api_key: str) -> None:
        """Use an API key instead of JWT."""
        self._session.headers["X-API-Key"] = api_key
        self._session.headers.pop("Authorization", None)

    def refresh_token(self) -> str:
        """Exchange refresh token for a new access token."""
        if not self._refresh_token:
            raise APIError("No refresh token available. Call login() first.")
        data = self._post("/api/v1/auth/refresh", json={"refresh_token": self._refresh_token})
        self._access_token = data["access_token"]
        self._session.headers["Authorization"] = f"Bearer {self._access_token}"
        return self._access_token

    def change_password(self, old_password: str, new_password: str) -> None:
        """Change the current user's password."""
        self._post("/api/v1/auth/change-password", json={
            "old_password": old_password,
            "new_password": new_password,
        })

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def list_users(self) -> list[dict]:
        return self._get("/api/v1/users")

    def create_user(self, username: str, password: str, role: str = "operator") -> dict:
        return self._post("/api/v1/users", json={"username": username, "password": password, "role": role})

    def delete_user(self, user_id: int) -> None:
        self._delete(f"/api/v1/users/{user_id}")

    # ------------------------------------------------------------------
    # API keys
    # ------------------------------------------------------------------

    def list_api_keys(self) -> list[dict]:
        return self._get("/api/v1/api-keys")

    def create_api_key(self, user_id: int, name: str = "", permissions: list[str] | None = None) -> dict:
        body: dict[str, Any] = {"user_id": user_id, "name": name}
        if permissions:
            body["permissions"] = permissions
        return self._post("/api/v1/api-keys", json=body)

    def revoke_api_key(self, key_id: str) -> None:
        self._delete(f"/api/v1/api-keys/{key_id}")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, host: str, password: str, username: str = "Administrator",
                 ssh_port: int = 22) -> list[dict]:
        """Discover VMs on the source cluster."""
        return self._post("/api/v1/discover", json={
            "host": host, "username": username, "password": password, "ssh_port": ssh_port,
        })

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    def backup(self, host: str, password: str, backup_path: str,
               username: str = "Administrator", **kwargs) -> list[dict]:
        """Trigger VM backup."""
        body = {"host": host, "username": username, "password": password, "backup_path": backup_path}
        body.update(kwargs)
        return self._post("/api/v1/backup", json=body)

    # ------------------------------------------------------------------
    # AI endpoints
    # ------------------------------------------------------------------

    def ai_plan(self, host: str, password: str, username: str = "Administrator") -> dict:
        return self._post("/api/v1/ai/plan", json={"host": host, "username": username, "password": password})

    def ai_runbook(self, host: str, password: str, target_host: str = "",
                   username: str = "Administrator") -> str:
        data = self._post("/api/v1/ai/runbook", json={
            "host": host, "username": username, "password": password, "target_host": target_host,
        })
        return data.get("runbook", "")

    def ai_estimate(self, host: str, password: str, username: str = "Administrator") -> dict:
        return self._post("/api/v1/ai/estimate", json={"host": host, "username": username, "password": password})

    def ai_risk(self, host: str, password: str, target_host: str = "",
                username: str = "Administrator") -> dict:
        return self._post("/api/v1/ai/risk", json={
            "host": host, "username": username, "password": password, "target_host": target_host,
        })

    def ai_chat(self, message: str, context: str = "") -> str:
        data = self._post("/api/v1/ai/chat", json={"message": message, "context": context})
        return data.get("response", "")

    def ai_script(self, task_description: str, target_platform: str = "PowerShell",
                  constraints: list[str] | None = None) -> str:
        data = self._post("/api/v1/ai/script", json={
            "task_description": task_description,
            "target_platform": target_platform,
            "constraints": constraints or [],
        })
        return data.get("script", "")

    def ai_iac(self, infrastructure_description: str, fmt: str = "bicep") -> str:
        data = self._post("/api/v1/ai/iac", json={
            "infrastructure_description": infrastructure_description, "format": fmt,
        })
        return data.get("template", "")

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def start_pipeline(self, *, skip_backup: bool = False, skip_move_back: bool = False,
                       use_ai: bool = True, resume: bool = False,
                       config: dict | None = None) -> str:
        """Start the rebuild pipeline and return the job ID."""
        body: dict[str, Any] = {
            "skip_backup": skip_backup,
            "skip_move_back": skip_move_back,
            "use_ai": use_ai,
            "resume": resume,
        }
        if config:
            body["config"] = config
        data = self._post("/api/v1/pipeline/start", json=body)
        return data["job_id"]

    def get_pipeline_status(self, job_id: str) -> dict:
        return self._get(f"/api/v1/pipeline/{job_id}")

    def get_pipeline_logs(self, job_id: str, offset: int = 0) -> list[dict]:
        return self._get(f"/api/v1/pipeline/{job_id}/logs", params={"offset": offset})

    def get_report(self, job_id: str) -> dict:
        return self._get(f"/api/v1/pipeline/{job_id}/report")

    def abort_pipeline(self, job_id: str) -> None:
        self._post(f"/api/v1/pipeline/{job_id}/abort")

    def list_pipelines(self) -> list[dict]:
        return self._get("/api/v1/pipeline")

    def stream_events(self, job_id: str) -> Generator[dict, None, None]:
        """Stream SSE events from a running pipeline. Yields dicts."""
        url = f"{self.base_url}/api/v1/pipeline/{job_id}/events"
        with self._session.get(url, stream=True, timeout=None) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    payload = line[6:]
                    try:
                        event = json.loads(payload)
                        yield event
                        if event.get("event") == "done":
                            return
                    except json.JSONDecodeError:
                        continue

    def wait_for_completion(self, job_id: str, poll_interval: float = 5.0,
                            timeout: float = 0) -> dict:
        """Poll pipeline status until done. Returns final status dict."""
        start = time.time()
        while True:
            status = self.get_pipeline_status(job_id)
            state = status.get("state", "")
            if state in ("completed", "failed", "aborted"):
                return status
            if timeout > 0 and (time.time() - start) > timeout:
                raise APIError(f"Pipeline {job_id} did not complete within {timeout}s")
            time.sleep(poll_interval)

    # ------------------------------------------------------------------
    # Individual stage triggers
    # ------------------------------------------------------------------

    def evacuate(self, config: dict | None = None) -> list[dict]:
        return self._post("/api/v1/evacuate", json={"config": config})

    def move_back(self, config: dict | None = None) -> list[dict]:
        return self._post("/api/v1/move-back", json={"config": config})

    def teardown(self, config: dict | None = None, confirm: bool = True) -> list[dict]:
        return self._post("/api/v1/teardown", json={"config": config, "confirm_teardown": confirm})

    def validate(self, host: str, password: str, expected_vms: list[str],
                 username: str = "Administrator") -> list[dict]:
        return self._post("/api/v1/validate", json={
            "host": host, "username": username, "password": password, "expected_vms": expected_vms,
        })

    # ------------------------------------------------------------------
    # Config & health
    # ------------------------------------------------------------------

    def get_config(self) -> dict:
        return self._get("/api/v1/config")

    def update_config(self, config: dict) -> None:
        self._put("/api/v1/config", json=config)

    def health(self) -> dict:
        return self._get("/api/v1/health")

    def list_stages(self) -> list[str]:
        return self._get("/api/v1/stages")

    def ai_providers(self) -> dict:
        return self._get("/api/v1/ai/providers")

    def ai_test(self) -> dict:
        return self._post("/api/v1/ai/test")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        resp = self._session.request(method, url, **kwargs)

        # Auto-refresh on 401
        if resp.status_code == 401 and self._refresh_token:
            try:
                self.refresh_token()
                resp = self._session.request(method, url, **kwargs)
            except Exception:
                pass

        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            if not resp.ok:
                raise APIError(f"HTTP {resp.status_code}: {resp.text}", resp.status_code)
            return resp.text

        if resp.ok and body.get("status") == "success":
            return body.get("data")

        raise APIError(
            body.get("message", f"HTTP {resp.status_code}"),
            resp.status_code,
            body.get("data"),
        )

    def _get(self, path: str, **kwargs) -> Any:
        return self._request("GET", path, **kwargs)

    def _post(self, path: str, **kwargs) -> Any:
        return self._request("POST", path, **kwargs)

    def _put(self, path: str, **kwargs) -> Any:
        return self._request("PUT", path, **kwargs)

    def _delete(self, path: str, **kwargs) -> Any:
        return self._request("DELETE", path, **kwargs)
