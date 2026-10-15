"""Workspace paths are independent of the current working directory."""

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

from imagen_cli.storage.safety import secure_directory


@dataclass(frozen=True)
class Workspace:
    root: Path
    database: Path
    artifacts: Path
    private: Path


def resolve_workspace(path: str | Path | None = None) -> Workspace:
    root = secure_directory(Path(path) if path is not None else user_data_path("imagen-cli"))
    return Workspace(
        root,
        root / "state.sqlite3",
        secure_directory(root / "artifacts"),
        secure_directory(root / "private"),
    )
