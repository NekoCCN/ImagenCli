"""Open a local artifact with the platform's default handler."""

import os
import subprocess
import sys
from pathlib import Path


def open_path(path: Path, *, folder: bool = False) -> None:
    target = path.parent if folder else path
    if not target.exists():
        raise ValueError("path is unavailable")
    if sys.platform == "darwin":
        command = ["open"]
    elif os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined,unused-ignore]
        return
    else:
        command = ["xdg-open"]
    subprocess.Popen(
        [*command, str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
