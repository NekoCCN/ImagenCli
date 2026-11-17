"""Interactive Skills setup; explicit CLI arguments remain the automation interface."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, TextIO

from imagen_cli.application import skill_targets, skills_service
from imagen_cli.i18n import t

from . import selection


def answer(
    prompt: str, choices: set[str], *, stdin: TextIO, stderr: TextIO, default: str = ""
) -> str:
    while True:
        stderr.write(prompt)
        stderr.flush()
        raw = stdin.readline()
        if not raw or raw.strip().lower() == "q":
            raise KeyboardInterrupt
        value = raw.strip().lower() or default
        if value == "b":
            raise selection.Back
        if value in choices:
            return value
        stderr.write(t("Choose one of the shown options.\n", "请选择列出的选项。\n"))


def launcher(*, stdin: TextIO, stderr: TextIO) -> list[str]:
    workspace = ""
    while True:
        try:
            selected = answer(
                t(
                    "Imagen\n1. Image workbench (TUI)\n2. Agent skills\n3. CLI help\n"
                    "4. Choose image workspace\nChoose [3], q cancel: ",
                    "Imagen\n1. 图片工作台（TUI）\n2. Agent 技能\n3. CLI 帮助\n"
                    "4. 选择图片工作区\n选择 [3]，q 取消：",
                ),
                {"1", "2", "3", "4"},
                stdin=stdin,
                stderr=stderr,
                default="3",
            )
            if selected == "4":
                stderr.write(t(
                    "Workspace path (blank = default, b back, q cancel): ",
                    "工作区路径（留空为默认，b 返回，q 取消）：",
                ))
                stderr.flush()
                raw = stdin.readline()
                if not raw or raw.strip().lower() == "q":
                    raise KeyboardInterrupt
                if raw.strip().lower() == "b":
                    continue
                workspace = str(Path(raw.strip()).expanduser()) if raw.strip() else ""
                stderr.write(t(
                    f"Image workspace: {workspace or '(default)'}\n"
                    "Project Skills still use the current directory.\n",
                    f"图片工作区：{workspace or '（默认）'}\n项目技能仍使用当前目录。\n",
                ))
                continue
        except selection.Back:
            continue
        command = {"1": ["tui"], "2": ["skills"], "3": ["help"]}[selected]
        return command + (["--workspace", workspace] if workspace else [])


def configure(command: str, *, stdin: TextIO, stderr: TextIO) -> tuple[str, dict[str, Any]]:
    """Select action, scope, agents, skills, then review before any filesystem changes."""
    action = command.removeprefix("skills ") if command != "skills" else "list"
    scope = "project"
    agents: list[str] | None = None
    names = [row["name"] for row in skills_service.available()]
    selected_skills = list(names)
    step = 0 if command == "skills" else 1
    stderr.write(
        t(
            "Agent skills · b back · q cancel · no changes until review\n",
            "Agent 技能 · b 返回 · q 取消 · 确认前不修改文件\n",
        )
    )
    while True:
        try:
            if step == 0:
                selected = answer(
                    t(
                        "1. Status / list\n2. Install\n3. Update\n4. Uninstall\nAction [1]: ",
                        "1. 状态 / 列表\n2. 安装\n3. 更新\n4. 卸载\n操作 [1]：",
                    ),
                    {"1", "2", "3", "4"},
                    stdin=stdin,
                    stderr=stderr,
                    default="1",
                )
                action = {"1": "list", "2": "install", "3": "update", "4": "uninstall"}[selected]
            elif step == 1:
                selected = answer(
                    t(
                        "1. Project (current directory)\n2. Global (user directories)\n"
                        f"Scope [{1 if scope == 'project' else 2}]: ",
                        "1. 项目（当前目录）\n2. 全局（用户目录）\n"
                        f"范围 [{1 if scope == 'project' else 2}]：",
                    ),
                    {"1", "2"},
                    stdin=stdin,
                    stderr=stderr,
                    default="1" if scope == "project" else "2",
                )
                scope = "project" if selected == "1" else "global"
            elif step == 2:
                labels, defaults = skill_targets.prompt_order(scope)
                stderr.write(t(f"Agents · {scope}\n", f"Agent · {scope}\n"))
                agents = selection.choose(
                    labels,
                    stdin=stdin,
                    stderr=stderr,
                    allow_back=True,
                    selected=defaults if agents is None else [a for a in agents if a in labels],
                )
                if not agents:
                    raise KeyboardInterrupt
            elif step == 3:
                stderr.write(t("Skills (choose a subset or all)\n", "技能（可选择部分或全部）\n"))
                selected_skills = selection.choose(
                    names,
                    stdin=stdin,
                    stderr=stderr,
                    selected=selected_skills,
                    allow_back=True,
                )
                if not selected_skills:
                    raise KeyboardInterrupt
            else:
                assert agents
                args: dict[str, Any] = {"scope": scope, "target": agents, "skill": selected_skills}
                stderr.write(t("Review\n", "确认\n"))
                stderr.write(f"skills {action} · {scope} · {', '.join(selected_skills)}\n")
                for agent in agents:
                    stderr.write(f"{agent} → {skill_targets.root_for(agent, scope)}\n")
                if action == "list":
                    prompt = t(
                        "1. Show status\nChoose [1], b back, q cancel: ",
                        "1. 查看状态\n选择 [1]，b 返回，q 取消：",
                    )
                    choices = {"1"}
                else:
                    stderr.write(
                        t(
                            "Safe mode preserves local edits. Unmanaged and symlinked paths are "
                            "never replaced.\n",
                            "安全模式保留本地修改。不会覆盖非受管目录或符号链接路径。\n",
                        )
                    )
                    prompt = t(
                        "1. Apply safely\n2. Override managed copies (discard local edits)\n"
                        "Choose [1], b back, q cancel: ",
                        "1. 安全执行\n2. 强制处理受管副本（丢弃本地修改）\n"
                        "选择 [1]，b 返回，q 取消：",
                    )
                    choices = {"1", "2"}
                selected = answer(prompt, choices, stdin=stdin, stderr=stderr, default="1")
                if selected == "2":
                    token = "remove" if action == "uninstall" else "overwrite"
                    try:
                        answer(
                            t(
                                f"Discard local edits in these managed copies? Type {token} "
                                "to confirm, b back, q cancel: ",
                                f"输入 {token} 确认丢弃本地修改，b 返回，q 取消：",
                            ),
                            {token},
                            stdin=stdin,
                            stderr=stderr,
                        )
                    except selection.Back:
                        continue
                    args["force"] = True
                    if action == "uninstall":
                        args["yes"] = True
                words = ["imagen", "skills", action, *selected_skills, "--scope", scope]
                for agent in agents:
                    words.extend(["--target", agent])
                if args.get("force"):
                    words.append("--force")
                if args.get("yes"):
                    words.append("--yes")
                stderr.write(t("Equivalent command: ", "等价命令：") + shlex.join(words) + "\n")
                return "skills " + action, args
            step += 1
        except selection.Back:
            if step == 0:
                raise KeyboardInterrupt from None
            step -= 1
