"""Labelled forms with explicit choices and ordinary text fallbacks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from imagen_cli.i18n import t


class ChoiceForm(ModalScreen[dict[str, str] | None]):
    DEFAULT_CSS = """
    ChoiceForm { align: center middle; }
    ChoiceForm #choice-form { width: 90%; max-width: 100; height: 90%; padding: 1 2;
                              background: $surface; border: round $primary; }
    ChoiceForm Static { height: auto; margin-bottom: 1; }
    ChoiceForm Input, ChoiceForm Select { margin-bottom: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        fields: list[tuple[str, str, str]],
        *,
        choices: dict[str, list[tuple[str, str]]] | None = None,
        optional: set[str] | None = None,
        secrets: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.heading = title
        self.fields = fields
        self.choices = choices or {}
        self.optional = optional or set()
        self.secrets = secrets or set()

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="choice-form"):
            yield Static(self.heading, id="form-status")
            for name, placeholder, default in self.fields:
                yield Static(name)
                if name in self.choices:
                    options = list(self.choices[name])
                    if default and default not in {value for _, value in options}:
                        options.append((default, default))
                    yield Select[str](options, value=default if default else Select.NULL, id=name)
                else:
                    yield Input(
                        value=default,
                        placeholder=placeholder,
                        id=name,
                        password=name in self.secrets,
                    )
            yield from self.extra_fields()
            yield Button(t("Save", "保存"), id="submit", variant="primary")

    def extra_fields(self) -> ComposeResult:
        yield from ()

    def on_mount(self) -> None:
        if self.fields:
            self.query_one("#" + self.fields[0][0]).focus()

    def values(self) -> dict[str, str]:
        result = {}
        for name, _, _ in self.fields:
            widget = self.query_one("#" + name)
            value = widget.value if isinstance(widget, (Input, Select)) else ""
            result[name] = value.strip() if isinstance(value, str) else ""
        return result

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.submit()

    def submit(self) -> None:
        values = self.values()
        missing = [
            name for name, value in values.items() if not value and name not in self.optional
        ]
        if missing:
            self.query_one("#form-status", Static).update(
                t("Required: ", "必填：") + ", ".join(missing)
            )
            return
        self.dismiss({name: value for name, value in values.items() if value})

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProviderForm(ChoiceForm):
    ENDPOINTS = {
        "openai": ("images", "https://api.openai.com/v1"),
        "gemini": ("generate_content", "https://generativelanguage.googleapis.com/v1beta"),
    }

    def __init__(
        self,
        title: str,
        fields: list[tuple[str, str, str]],
        *,
        fetch_models: Callable[[dict[str, str]], list[str]],
    ) -> None:
        defaults = {name: value for name, _, value in fields}
        self.fetch_models = fetch_models
        self.previous_provider = defaults.get("provider", "openai")
        self.original_reference = defaults.get("credential_ref", "")
        family, endpoint = self.ENDPOINTS.get(self.previous_provider, self.ENDPOINTS["openai"])
        modes = [
            (t("Direct API key (private storage)", "直接填写 key（私有存储）"), "key"),
            (t("Environment variable name", "环境变量名称"), "env"),
            (t("Existing private/keyring reference", "已有 private/keyring 引用"), "reference"),
        ]
        if self.original_reference:
            modes.insert(0, (t("Keep existing credential", "保留现有凭据"), "keep"))
        entries = [
            ("profile", "profile id", defaults.get("profile", "")),
            ("provider", "", self.previous_provider),
            ("family", "", defaults.get("family", family)),
            ("base_url", "https://…", defaults.get("base_url") or endpoint),
            ("credential_mode", "", "keep" if self.original_reference else "key"),
            ("api_key", t("API key", "API key"), ""),
            ("credential_ref", t("Variable name or reference", "变量名或引用"), ""),
            (
                "model_default",
                t("Choose below or type model ID", "下方选择或手输模型 ID"),
                defaults.get("model_default", ""),
            ),
            ("revision", "1", defaults.get("revision", "1")),
        ]
        super().__init__(
            title,
            entries,
            optional={"api_key", "credential_ref"},
            secrets={"api_key"},
            choices={
                "provider": [(p, p) for p in self.ENDPOINTS],
                "family": [(family, family)],
                "credential_mode": modes,
            },
        )
        self.request_version = 0

    def extra_fields(self) -> ComposeResult:
        yield Static(
            t(
                "Direct key: stored privately; config contains only its reference. Environment: "
                "enter the variable NAME, not its value. Existing reference: private:NAME or "
                "keyring:SERVICE. Fetch sends this credential to the displayed endpoint.",
                "直接 key：写入私有存储，配置仅保存引用。环境变量：填变量名称，不是 key。"
                "已有引用：private:NAME 或 keyring:SERVICE。获取列表会向上方地址发送所选凭据。",
            )
        )
        yield Button(t("Fetch models", "获取模型列表"), id="fetch-models")
        yield Select[str]([], id="model-choice", prompt=t("Choose model", "选择模型"))

    def on_mount(self) -> None:
        super().on_mount()
        self._credential_visibility()
        self.query_one("#family", Select).disabled = True

    def _credential_visibility(self) -> None:
        mode = self.query_one("#credential_mode", Select).value
        self.query_one("#api_key", Input).display = mode == "key"
        self.query_one("#credential_ref", Input).display = mode in {"env", "reference"}

    def values(self) -> dict[str, str]:
        values = super().values()
        mode = values.pop("credential_mode", "")
        if mode != "key":
            values.pop("api_key", None)
        if mode == "keep":
            values["credential_ref"] = self.original_reference
        elif mode == "env":
            values["credential_ref"] = "env:" + values["credential_ref"].removeprefix("env:")
        elif mode == "key":
            values.pop("credential_ref", None)
        return values

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "provider" and isinstance(event.value, str):
            if event.value != self.previous_provider:
                old_endpoint = self.ENDPOINTS.get(self.previous_provider, ("", ""))[1]
                family, endpoint = self.ENDPOINTS[event.value]
                self.previous_provider = event.value
                selector = self.query_one("#family", Select)
                selector.set_options([(family, family)])
                selector.value = family
                url = self.query_one("#base_url", Input)
                if not url.value or url.value == old_endpoint:
                    url.value = endpoint
                self.query_one("#model_default", Input).value = ""
                self.query_one("#model-choice", Select).set_options([])
        elif event.select.id == "credential_mode":
            self._credential_visibility()
        elif event.select.id == "model-choice" and isinstance(event.value, str):
            self.query_one("#model_default", Input).value = event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "fetch-models":
            self.request_version += 1
            self._fetch(self.values(), self.request_version)
        else:
            super().on_button_pressed(event)

    @work(thread=True, exclusive=True)
    def _fetch(self, values: dict[str, str], version: int) -> None:
        try:
            models = self.fetch_models(values)
            message = t(
                f"{len(models)} models; manual input remains available",
                f"{len(models)} 个模型；仍可手动输入",
            )
        except Exception:
            models = []
            message = t(
                "Could not fetch models; check endpoint/credential or enter an ID",
                "无法获取模型；请检查地址/凭据，或手动填写模型 ID",
            )
        self.app.call_from_thread(self._fetched, values, version, models, message)

    def _fetched(
        self, values: dict[str, str], version: int, models: list[str], message: str
    ) -> None:
        if not self.is_mounted or version != self.request_version or values != self.values():
            return
        self.query_one("#model-choice", Select).set_options([(m, m) for m in models])
        self.query_one("#form-status", Static).update(message)

    def submit(self) -> None:
        values = self.values()
        try:
            from imagen_cli.application.profile_service import ProfileService

            ProfileService().build(
                identity=values["profile"],
                provider=values["provider"],
                family=values["family"],
                base_url=values["base_url"],
                model_default=values["model_default"],
                credential_ref=values.get("credential_ref") or "private:draft",
                revision=int(values["revision"]),
            )
            if not values.get("api_key") and not values.get("credential_ref"):
                raise ValueError("credential required")
        except (ValueError, KeyError):
            self.query_one("#form-status", Static).update(
                t(
                    "Check required fields, credential and positive revision",
                    "请检查必填字段、凭据与正整数 revision",
                )
            )
            return
        self.dismiss({k: v for k, v in values.items() if v})


class CreationForm(ChoiceForm):
    """Choose a saved endpoint and its model without changing saved defaults."""

    def __init__(
        self,
        title: str,
        fields: list[tuple[str, str, str]],
        *,
        profiles: list[dict[str, Any]],
        models: dict[str, list[str]],
        optional: set[str] | None = None,
        fetch_models: Callable[[str], list[str]] | None = None,
        add_profile: Callable[[], None] | None = None,
    ) -> None:
        self.fetch_models = fetch_models
        self.add_profile = add_profile
        self.request_version = 0
        self.profiles = {str(row["id"]): row for row in profiles}
        self.models = models
        super().__init__(
            title,
            fields,
            optional=optional,
            choices={
                "profile": [(identity, identity) for identity in self.profiles],
                "async": [(t("Yes", "是"), "true"), (t("No", "否"), "false")],
            },
        )
        self.initial_profile = dict((name, value) for name, _, value in fields).get("profile", "")

    def extra_fields(self) -> ComposeResult:
        if not self.profiles and self.add_profile:
            yield Button(t("Add a provider first", "先添加 provider"), id="add-profile")
        if self.fetch_models:
            yield Button(
                t("Fetch models for selected profile", "获取所选 profile 的模型"), id="fetch-models"
            )
        yield Select[str]([], id="model-choice", prompt=t("Choose model", "选择模型"))
        yield Static(
            t(
                "Model selection fills the editable model ID. Changes apply only to this form.",
                "模型下拉会填入可编辑的模型 ID；本表单不会修改 profile 默认值。",
            )
        )

    def on_mount(self) -> None:
        super().on_mount()
        self._models(self.initial_profile, reset=False)

    def _models(self, identity: str, *, reset: bool) -> None:
        default = str(self.profiles.get(identity, {}).get("model_default") or "")
        choices = list(dict.fromkeys([default, *self.models.get(identity, [])]))
        self.query_one("#model-choice", Select).set_options([(m, m) for m in choices if m])
        if reset:
            self.query_one("#model", Input).value = default

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "profile":
            identity = event.value if isinstance(event.value, str) else ""
            if identity != self.initial_profile:
                self.initial_profile = identity
                self._models(identity, reset=True)
        elif event.select.id == "model-choice" and isinstance(event.value, str):
            self.query_one("#model", Input).value = event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-profile" and self.add_profile:
            self.action_cancel()
            self.add_profile()
        elif event.button.id == "fetch-models" and self.fetch_models:
            identity = self.values().get("profile", "")
            if identity:
                self.request_version += 1
                self._fetch(identity, self.request_version)
        else:
            super().on_button_pressed(event)

    @work(thread=True, exclusive=True)
    def _fetch(self, identity: str, version: int) -> None:
        try:
            models = self.fetch_models(identity) if self.fetch_models else []
            message = t(f"{len(models)} models", f"{len(models)} 个模型")
        except Exception:
            models = []
            message = t("Fetch failed; manual model ID is available", "获取失败；仍可手输模型 ID")
        self.app.call_from_thread(self._fetched, identity, version, models, message)

    def _fetched(self, identity: str, version: int, models: list[str], message: str) -> None:
        if not self.is_mounted or version != self.request_version:
            return
        if self.values().get("profile") != identity:
            return
        self.models[identity] = models
        self._models(identity, reset=False)
        self.query_one("#form-status", Static).update(message)

    def submit(self) -> None:
        values = self.values()
        try:
            if "count" in values and int(values["count"]) < 1:
                raise ValueError("count must be positive")
        except ValueError:
            self.query_one("#form-status", Static).update(
                t(
                    "Count must be a positive integer",
                    "数量必须是正整数",
                )
            )
            return
        super().submit()
