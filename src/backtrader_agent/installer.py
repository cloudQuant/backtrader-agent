"""Create-only native host adapter installer."""

import json
from contextlib import contextmanager
from pathlib import Path
import re
import shlex
from typing import Any, Dict, Iterator, List, Mapping

from .canonical import create_or_verify_bytes, create_or_verify_json, read_json, sha256_bytes
from .errors import AgentError
from .locking import exclusive_file_lock

RESOURCE_ROOT = Path(__file__).resolve().parent / "resources" / "adapters"
INSTALL_MANIFEST_SCHEMA = "adapter-install-manifest-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ADAPTER_RESOURCE_FILES: Mapping[str, Mapping[str, str]] = {
    "claude": {
        ".claude/agents/backtrader-agent.md": "claude-code/backtrader-agent.md",
    },
    "codex": {
        ".codex/agents/backtrader-agent.toml": "codex/backtrader-agent.toml",
    },
    "opencode": {
        ".opencode/agents/backtrader-agent.md": "opencode/backtrader-agent.md",
    },
    "openclaw": {
        ".openclaw/workspaces/backtrader-agent/README.md": "openclaw/workspace/README.md",
        ".openclaw/workspaces/backtrader-agent/AGENTS.md": "openclaw/workspace/AGENTS.md",
        ".openclaw/workspaces/backtrader-agent/IDENTITY.md": "openclaw/workspace/IDENTITY.md",
    },
}


class AdapterInstaller:
    @staticmethod
    def _resource_text(relative_path: str) -> str:
        path = RESOURCE_ROOT.joinpath(*Path(relative_path).parts)
        if not path.is_file() or path.is_symlink():
            raise AgentError(
                "BTAG-INSTALL-RESOURCE",
                "packaged host adapter resource is missing or unsafe",
            )
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _openclaw_registration(workspace: Path) -> Dict[str, Any]:
        template = json.loads(
            AdapterInstaller._resource_text(
                "openclaw/workspace/registration-manifest.template.json"
            )
        )
        quoted_workspace = shlex.quote(str(workspace))
        first_request = str(template["first_request"])
        quoted_request = shlex.quote(first_request)
        template.update(
            {
                "workspace": str(workspace),
                "registration_command": (
                    "openclaw agents add backtrader-agent "
                    f"--workspace {quoted_workspace} --non-interactive"
                ),
                "invocation_command": (
                    "openclaw agent --agent backtrader-agent " f"--message {quoted_request}"
                ),
            }
        )
        return template

    @staticmethod
    def _files(root: Path, host: str) -> Dict[str, str]:
        files = {
            destination: AdapterInstaller._resource_text(resource)
            for destination, resource in ADAPTER_RESOURCE_FILES[host].items()
        }
        if host == "openclaw":
            workspace = (root / ".openclaw/workspaces/backtrader-agent").resolve()
            registration = AdapterInstaller._openclaw_registration(workspace)
            files[".openclaw/workspaces/backtrader-agent/registration-manifest.json"] = (
                json.dumps(registration, indent=2, sort_keys=True) + "\n"
            )
        return files

    @staticmethod
    def _validate_host(host: str) -> None:
        if host not in ADAPTER_RESOURCE_FILES:
            raise AgentError("BTAG-INSTALL-HOST", "host adapter is not supported")

    @staticmethod
    def _manifest_path(root: Path, host: str) -> Path:
        return root / ".backtrader-agent" / "installer" / f"{host}.json"

    @classmethod
    def _lock_path(cls, root: Path, host: str) -> Path:
        return cls._manifest_path(root, host).with_suffix(".lock")

    @contextmanager
    def _locked_apply(self, root: Path, host: str) -> Iterator[None]:
        with exclusive_file_lock(
            self._lock_path(root, host),
            error_code="BTAG-INSTALL-LOCK",
            subject=f"adapter {host} lifecycle",
        ):
            yield

    def install(self, target: Path, host: str, *, apply: bool) -> Dict[str, Any]:
        self._validate_host(host)
        root = Path(target).resolve(strict=True)
        if not root.is_dir():
            raise AgentError("BTAG-INSTALL-TARGET", "install target must be a directory")
        if apply:
            with self._locked_apply(root, host):
                return self._install_unlocked(root, host, apply=True)
        return self._install_unlocked(root, host, apply=False)

    def _install_unlocked(self, root: Path, host: str, *, apply: bool) -> Dict[str, Any]:
        adapter_files = self._files(root, host)
        changes: List[Dict[str, Any]] = []
        for relative, content in adapter_files.items():
            destination = root.joinpath(*Path(relative).parts)
            encoded = content.encode("utf-8")
            if destination.exists() or destination.is_symlink():
                try:
                    matches = (
                        not destination.is_symlink()
                        and destination.is_file()
                        and destination.read_bytes() == encoded
                    )
                except OSError as exc:
                    raise AgentError(
                        "BTAG-INSTALL-CONFLICT",
                        "existing adapter could not be safely inspected",
                        details={"relative_path": relative},
                    ) from exc
                if not matches:
                    raise AgentError(
                        "BTAG-INSTALL-CONFLICT",
                        "existing adapter differs; create-only install refused",
                        details={"relative_path": relative},
                    )
                action = "unchanged"
            else:
                action = "create"
            changes.append(
                {
                    "relative_path": relative,
                    "action": action,
                    "sha256": sha256_bytes(encoded),
                    "size_bytes": len(encoded),
                }
            )
        if not apply:
            result: Dict[str, Any] = {
                "status": "preview",
                "host": host,
                "changes": changes,
            }
            if host == "openclaw":
                workspace = (root / ".openclaw/workspaces/backtrader-agent").resolve()
                registration = self._openclaw_registration(workspace)
                result["manual_registration"] = {
                    "required": True,
                    "command": registration["registration_command"],
                    "invoke": registration["invocation_command"],
                    "verify": registration["verification_command"],
                }
            return result
        change_by_relative = {item["relative_path"]: item for item in changes}
        for relative, content in adapter_files.items():
            destination = root.joinpath(*Path(relative).parts)
            created = create_or_verify_bytes(
                destination,
                content.encode("utf-8"),
                conflict_code="BTAG-INSTALL-CONFLICT",
                conflict_message="existing adapter differs; create-only install refused",
            )
            change_by_relative[relative]["action"] = "create" if created else "unchanged"
        install_manifest = {
            "schema_version": INSTALL_MANIFEST_SCHEMA,
            "host": host,
            "files": [
                {
                    "relative_path": relative,
                    "sha256": sha256_bytes(content.encode("utf-8")),
                }
                for relative, content in sorted(adapter_files.items())
            ],
        }
        manifest_path = self._manifest_path(root, host)
        create_or_verify_json(
            manifest_path,
            install_manifest,
            conflict_code="BTAG-INSTALL-MANIFEST",
            conflict_message="install manifest conflicts",
        )
        status = (
            "unchanged" if all(item["action"] == "unchanged" for item in changes) else "installed"
        )
        result = {"status": status, "host": host, "changes": changes}
        if host == "openclaw":
            workspace = (root / ".openclaw/workspaces/backtrader-agent").resolve()
            registration = self._openclaw_registration(workspace)
            result["manual_registration"] = {
                "required": True,
                "executed": False,
                "command": registration["registration_command"],
                "invoke": registration["invocation_command"],
                "verify": registration["verification_command"],
            }
        return result

    def uninstall(self, target: Path, host: str, *, apply: bool) -> Dict[str, Any]:
        self._validate_host(host)
        root = Path(target).resolve(strict=True)
        if apply:
            with self._locked_apply(root, host):
                return self._uninstall_unlocked(root, host, apply=True)
        return self._uninstall_unlocked(root, host, apply=False)

    def _load_uninstall_manifest(self, root: Path, host: str) -> List[Dict[str, str]]:
        manifest_path = self._manifest_path(root, host)
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise AgentError("BTAG-UNINSTALL-MANIFEST", "no safe install manifest exists for host")
        try:
            manifest = read_json(manifest_path)
        except AgentError as exc:
            raise AgentError(
                "BTAG-UNINSTALL-MANIFEST",
                "install manifest could not be parsed",
            ) from exc
        entries = manifest.get("files")
        if (
            manifest.get("schema_version") != INSTALL_MANIFEST_SCHEMA
            or manifest.get("host") != host
            or not isinstance(entries, list)
        ):
            raise AgentError("BTAG-UNINSTALL-MANIFEST", "install manifest is malformed")
        expected_paths = set(self._files(root, host))
        normalized: List[Dict[str, str]] = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"relative_path", "sha256"}:
                raise AgentError("BTAG-UNINSTALL-MANIFEST", "install manifest is malformed")
            relative = entry.get("relative_path")
            digest = entry.get("sha256")
            if (
                not isinstance(relative, str)
                or relative not in expected_paths
                or relative in seen
                or not isinstance(digest, str)
                or not SHA256_RE.fullmatch(digest)
            ):
                raise AgentError("BTAG-UNINSTALL-MANIFEST", "install manifest is malformed")
            seen.add(relative)
            normalized.append({"relative_path": relative, "sha256": digest})
        if seen != expected_paths:
            raise AgentError("BTAG-UNINSTALL-MANIFEST", "install manifest is incomplete")
        return normalized

    def _uninstall_unlocked(self, root: Path, host: str, *, apply: bool) -> Dict[str, Any]:
        manifest_path = self._manifest_path(root, host)
        entries = self._load_uninstall_manifest(root, host)
        removals = []
        for item in entries:
            destination = root.joinpath(*Path(item["relative_path"]).parts)
            if destination.exists() or destination.is_symlink():
                try:
                    matches = (
                        not destination.is_symlink()
                        and destination.is_file()
                        and sha256_bytes(destination.read_bytes()) == item["sha256"]
                    )
                except OSError as exc:
                    raise AgentError(
                        "BTAG-UNINSTALL-MODIFIED",
                        "installed adapter could not be safely inspected",
                        details={"relative_path": item["relative_path"]},
                    ) from exc
                if not matches:
                    raise AgentError(
                        "BTAG-UNINSTALL-MODIFIED",
                        "modified adapter will not be removed",
                        details={"relative_path": item["relative_path"]},
                    )
                removals.append(item["relative_path"])
        if apply:
            for relative in removals:
                root.joinpath(*Path(relative).parts).unlink()
            manifest_path.unlink()
        return {
            "status": "uninstalled" if apply else "preview",
            "host": host,
            "remove": removals,
        }
