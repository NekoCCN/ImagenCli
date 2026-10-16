"""Generate docs/coverage-report.md from the reviewed coverage matrix and records."""

from collections import Counter
from collections.abc import Iterable

from imagen_cli.domain.models import CapabilityRecord

from .catalog_data import CANONICAL_RECORDS
from .coverage import DIMENSIONS, matrix

__all__ = ["render"]

_LABELS = {
    "T": "tested (live evidence)",
    "I": "implemented (offline evidence)",
    "D": "documented",
    "N": "not supported",
    "U": "unknown",
}


def render(records: Iterable[CapabilityRecord] | None = None) -> str:
    rows = tuple(records or CANONICAL_RECORDS)
    routes = matrix()
    status_counts = Counter(
        cell.status for route in routes for cell in route.cells.values()
    )
    record_counts = Counter(record.support_status.value for record in rows)
    live = sum(len(record.live_evidence) for record in rows)
    lines = [
        "# Capability Coverage Report: ImagenCli",
        "",
        "生成日期：2026-10-24。此文件由 `imagen_cli.capabilities.report` 从覆盖矩阵与能力记录生成；"
        "不手工维护第二份支持清单。",
        "",
        "## 证据声明",
        "",
        "- 本轮没有 live 证据：所有 `T` 计数必须为 0；离线契约通过最多标记 `I`。",
        "- `U` 不是支持，也不是服务不支持的证明；`N` 必须附有定位来源或实现说明。",
        "- 运行时能力仍需细化到 provider/family/model/revision/operation；"
        "泛化路线行不计为已审核模型操作。",
        "",
        "## 计数",
        "",
        f"- 规划路线：{len(routes)}；能力维度：{len(DIMENSIONS)}；"
        f"单元格：{len(routes) * len(DIMENSIONS)}。",
        "- 单元格状态：" + "，".join(
            f"{key}={status_counts.get(key, 0)}" for key in ("T", "I", "D", "N", "U")
        ) + "。",
        "- 能力记录状态：" + "，".join(
            f"{key}={record_counts.get(key, 0)}" for key in ("T", "I", "D", "N", "U")
        ) + "。",
        f"- live 证据条目：{live}。",
        "",
        "## 路线 × 维度",
        "",
        "状态图例：" + "；".join(f"{key}={_LABELS[key]}" for key in ("T", "I", "D", "N", "U")),
        "",
        "| 路线 | 来源 | " + " | ".join(key for key, _ in DIMENSIONS) + " |",
        "| --- | --- | " + " | ".join("---" for _ in DIMENSIONS) + " |",
    ]
    for route in routes:
        cells = " | ".join(route.cells[key].status for key, _ in DIMENSIONS)
        lines.append(f"| {route.name} | {route.source} ({route.source_date}) | {cells} |")
    lines += [
        "",
        "## 维度证据",
        "",
        "| 路线 | 维度 | 状态 | 证据 |",
        "| --- | --- | --- | --- |",
    ]
    for route in routes:
        for key, label in DIMENSIONS:
            cell = route.cells[key]
            lines.append(f"| {route.name} | {label} | {cell.status} | {cell.evidence} |")
    lines += [
        "",
        "## 能力记录",
        "",
        "| provider | family | model | revision | operation | 状态 | 传输 | "
        "离线证据 | live 证据 | 来源 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in sorted(
        rows, key=lambda item: (
            item.provider, item.family, item.model, item.model_revision, item.operation.value,
        ),
    ):
        sources = "; ".join(
            f"{source.url} ({source.date}, {source.locator})" for source in record.sources
        )
        lines.append(
            f"| {record.provider} | {record.family} | {record.model} | "
            f"{record.model_revision} | {record.operation.value} | "
            f"{record.support_status.value} | {record.transport_kind} | "
            f"{len(record.offline_evidence)} | {len(record.live_evidence)} | {sources} |"
        )
    lines += [
        "",
        "## 具名自托管路线（T119）",
        "",
        "`AUTOMATIC1111 Stable Diffusion WebUI API` 取代了泛化的“其他公开自托管 API”行。"
        "其来源为官方仓库 wiki（2026-10-24），已实现 `/sdapi/v1/txt2img`、"
        "`/sdapi/v1/img2img` 与 `/sdapi/v1/interrupt` 的离线契约；"
        "仍为 `I`，因为本轮没有真实自托管实例的 live 证据。",
        "",
        "任何其他自托管服务必须单独具名、复核来源并满足同一门禁，不能由该路线代表。",
        "",
    ]
    return "\n".join(lines)
