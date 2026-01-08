from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class VenvRecord:
    name: str
    path: Path
    requirements_hash: str | None


class VirtualEnvManager:
    """Manage Windows virtual environments based on a base interpreter."""

    DEFAULT_BASE_DIR = Path("C:/venvs")
    DEFAULT_BASE_INTERPRETER = Path("C:/python-embed/python.exe")

    def __init__(
        self,
        base_interpreter: Path | str | None = None,
        *,
        base_dir: Path | str | None = None,
        registry_path: Path | str | None = None,
    ) -> None:
        base_interpreter_value = base_interpreter or self.DEFAULT_BASE_INTERPRETER
        self.base_interpreter = Path(base_interpreter_value)
        if not self.base_interpreter.is_file():
            raise FileNotFoundError(f"Base interpreter not found: {self.base_interpreter}")

        self.base_dir = Path(base_dir) if base_dir else self.DEFAULT_BASE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

        if registry_path:
            self.registry_path = Path(registry_path)
        else:
            self.registry_path = self.base_dir / ".venv_mgr" / "registry.json"

        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._save_registry([])

    def create_venv(
        self,
        name: str,
        *,
        path: Path | str | None = None,
        requirements: Sequence[str] | Path | str | None = None,
    ) -> Path:
        venv_path = Path(path) if path else self.base_dir / name
        venv_path = venv_path.resolve()

        if venv_path.exists():
            raise FileExistsError(f"Venv already exists at: {venv_path}")

        self._run([str(self.base_interpreter), "-m", "venv", str(venv_path)])

        if requirements:
            self.install_requirements(venv_path, requirements)

        requirements_hash = None
        if requirements:
            requirements_hash = self._hash_requirements(requirements)

        records = self._load_registry()
        records.append(
            {
                "name": name,
                "path": str(venv_path),
                "requirements_hash": requirements_hash,
            }
        )
        self._save_registry(records)
        return venv_path

    def get_or_create_venv(
        self,
        name: str,
        *,
        path: Path | str | None = None,
        requirements: Sequence[str] | Path | str | None = None,
    ) -> Path:
        if requirements:
            existing = self.find_venvs_by_requirements(requirements)
            if existing:
                return existing[0]
        return self.create_venv(name, path=path, requirements=requirements)

    def list_venvs(self) -> list[VenvRecord]:
        records = []
        for record in self._load_registry():
            records.append(
                VenvRecord(
                    name=record["name"],
                    path=Path(record["path"]),
                    requirements_hash=record.get("requirements_hash"),
                )
            )
        return records

    def delete_venv(self, name_or_path: str | Path, *, remove_dir: bool = True) -> bool:
        target_path = Path(name_or_path)
        if not target_path.is_absolute():
            target_path = (self.base_dir / target_path).resolve()

        records = self._load_registry()
        updated = [
            record
            for record in records
            if Path(record["path"]).resolve() != target_path
        ]
        removed_from_registry = len(records) != len(updated)
        if removed_from_registry:
            self._save_registry(updated)

        if remove_dir and target_path.exists():
            shutil.rmtree(target_path)

        return removed_from_registry

    def clear_all_venvs(self, *, remove_dirs: bool = True) -> list[Path]:
        records = self._load_registry()
        removed_paths = [Path(record["path"]) for record in records]
        self._save_registry([])

        if remove_dirs:
            for venv_path in removed_paths:
                if venv_path.exists():
                    shutil.rmtree(venv_path)

        return removed_paths

    def install_requirements(
        self, venv_path: Path | str, requirements: Sequence[str] | Path | str
    ) -> None:
        venv_root = Path(venv_path)
        venv_python = self._venv_python(venv_root)
        log_path = venv_root / "pip-install.log"

        if isinstance(requirements, (str, Path)):
            req_path = Path(requirements)
            if req_path.exists():
                self._run_pip_install(
                    log_path,
                    [str(venv_python), "-m", "pip", "install", "-r", str(req_path)],
                )
                return
            raise FileNotFoundError(f"Requirements file not found: {req_path}")

        if not requirements:
            return

        self._run_pip_install(
            log_path,
            [str(venv_python), "-m", "pip", "install", *requirements],
        )

    def install_wheels_from_dir(
        self, wheels_dir: Path | str, name_or_path: Path | str | None = None
    ) -> list[Path]:
        wheels_path = Path(wheels_dir)
        if not wheels_path.exists():
            raise FileNotFoundError(f"Wheels directory not found: {wheels_path}")

        wheel_files = sorted(wheels_path.rglob("*.whl"))
        if not wheel_files:
            return []

        if name_or_path is None:
            venv_python = self.base_interpreter
            log_path = self.base_dir / "base-pip-install.log"
        else:
            venv_path = self._resolve_venv_path(name_or_path)
            venv_python = self._venv_python(venv_path)
            log_path = venv_path / "pip-install.log"
        self._run_pip_install(
            log_path,
            [str(venv_python), "-m", "pip", "install", *[str(p) for p in wheel_files]],
        )
        return wheel_files

    def list_installed_packages(self, name_or_path: Path | str) -> list[str]:
        venv_python = self.get_python_path(name_or_path)
        output = self._run(
            [str(venv_python), "-m", "pip", "freeze"], capture_output=True
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    def get_python_path(self, name_or_path: Path | str) -> Path:
        venv_path = self._resolve_venv_path(name_or_path)
        return self._venv_python(venv_path)

    def copy_pip_install_log(
        self, name_or_path: Path | str, output_dir: Path | str
    ) -> Path | None:
        venv_path = self._resolve_venv_path(name_or_path)
        log_path = venv_path / "pip-install.log"
        if not log_path.exists():
            return None

        destination_dir = Path(output_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_path = destination_dir / log_path.name
        shutil.copy2(log_path, destination_path)
        return destination_path

    def find_venvs_by_requirements(
        self, requirements: Sequence[str] | Path | str
    ) -> list[Path]:
        target_hash = self._hash_requirements(requirements)
        matches: list[Path] = []

        records = self._load_registry()
        for record in records:
            record_hash = record.get("requirements_hash")
            record_path = Path(record["path"])
            if record_hash is None and record_path.exists():
                record_hash = self._hash_installed_packages(record_path)
                record["requirements_hash"] = record_hash
            if record_hash == target_hash:
                matches.append(record_path)

        self._save_registry(records)
        return matches

    def _hash_requirements(self, requirements: Sequence[str] | Path | str) -> str:
        if isinstance(requirements, (str, Path)):
            req_path = Path(requirements)
            if req_path.exists():
                lines = req_path.read_text(encoding="utf-8").splitlines()
            else:
                lines = [str(requirements)]
        else:
            lines = list(requirements)

        normalized = [self._normalize_requirement(line) for line in lines]
        normalized = [line for line in normalized if line]
        normalized.sort()
        joined = "\n".join(normalized)
        return sha256(joined.encode("utf-8")).hexdigest()

    def _hash_installed_packages(self, venv_path: Path) -> str:
        packages = self.list_installed_packages(venv_path)
        normalized = [self._normalize_requirement(line) for line in packages]
        normalized = [line for line in normalized if line]
        normalized.sort()
        joined = "\n".join(normalized)
        return sha256(joined.encode("utf-8")).hexdigest()

    def _normalize_requirement(self, line: str) -> str:
        line = line.strip()
        if not line or line.startswith("#"):
            return ""
        if " #" in line:
            line = line.split(" #", 1)[0]
        return line.lower()

    def _venv_python(self, venv_path: Path) -> Path:
        return venv_path / "Scripts" / "python.exe"

    def _resolve_venv_path(self, name_or_path: Path | str) -> Path:
        venv_path = Path(name_or_path)
        if venv_path.is_absolute() or venv_path.exists():
            return venv_path

        name = str(name_or_path)
        for record in self._load_registry():
            if record["name"] == name:
                return Path(record["path"])

        return (self.base_dir / name).resolve()

    def _load_registry(self) -> list[dict[str, str | None]]:
        content = self.registry_path.read_text(encoding="utf-8")
        if not content.strip():
            return []
        return json.loads(content)

    def _save_registry(self, records: Iterable[dict[str, str | None]]) -> None:
        payload = json.dumps(list(records), indent=2)
        self.registry_path.write_text(payload + "\n", encoding="utf-8")

    def _run(self, command: Sequence[str], *, capture_output: bool = False) -> str:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=capture_output,
        )
        return result.stdout if capture_output else ""

    def _run_pip_install(self, log_path: Path, command: Sequence[str]) -> None:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(result.stdout or "")
            log_file.write(result.stderr or "")
        result.check_returncode()
