"""Operator catalog and metadata for interactive parameter tuning in CLI and TUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Parameter:
    name: str
    label: str
    param_type: str  # "int", "float", "bool", "str", "choice"
    default: Any
    min_value: float | int | None = None
    max_value: float | int | None = None
    choices: list[str] | None = None
    description: str = ""


@dataclass(frozen=True)
class OperatorItem:
    key: str
    name: str
    category: str
    category_label: str
    description: str
    default_op: str
    default_params: dict[str, Any]
    parameters: list[Parameter] = field(default_factory=list)

    def build_pipeline(self, overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(self.default_params)
        if overrides:
            params.update(overrides)
        return [{"op": self.default_op, "params": params}]


CATEGORIES: dict[str, str] = {
    "preset": "🌟 精选预设",
    "anti_ai": "🎨 去AI感与胶片",
    "acg": "⚡ ACG漫画特化",
    "photo": "📸 摄影与光影",
    "art": "🎭 风格与像素",
    "superres": "🚀 AI 超分辨率",
}

# Rich presets mapping
PRESET_PIPELINES: dict[str, list[dict[str, Any]]] = {
    "去AI塑料感 (Anti-AI Organic)": [
        {
            "op": "filmic_tone",
            "params": {"lift_black": 6, "compress_white": 245, "saturation": 0.95},
        },
        {
            "op": "de_ai_bloom",
            "params": {"radius": 6, "intensity": 0.25, "threshold": 190, "preserve_lines": True},
        },
        {"op": "film_grain", "params": {"amount": 0.05, "monochrome": True, "shadow_bias": True}},
        {"op": "chromatic_aberration", "params": {"shift": 2, "radial": True}},
    ],
    "日系通透空气感 (Shinkai Airy)": [
        {"op": "color_temperature", "params": {"temperature": 0.15, "tint": -0.05}},
        {
            "op": "de_ai_bloom",
            "params": {"radius": 10, "intensity": 0.35, "threshold": 170, "preserve_lines": True},
        },
        {"op": "vignette", "params": {"radius": 0.8, "feather": 0.4, "darkness": 0.3}},
    ],
    "热血集中线 (Manga Action Focus)": [
        {"op": "focus_lines", "params": {"lines": 140, "inner_radius": 0.35, "max_width": 3}},
    ],
    "黑白漫画网点 (Manga Screentone)": [
        {"op": "screentone", "params": {"frequency": 35, "angle": 45.0, "contrast": 1.2}},
    ],
    "立绘贴纸白边 (Die-cut Sticker)": [
        {"op": "sticker_outline", "params": {"width": 8, "color": "#ffffff", "shadow": True}},
    ],
    "XDoG动漫手绘线稿 (Line Art)": [
        {
            "op": "lineart_extract",
            "params": {"radius": 1.0, "k": 1.6, "gamma": 0.98, "threshold": 0.02},
        },
    ],
    "复古90年代胶片 (Retro 90s Film)": [
        {"op": "filmic_tone", "params": {"lift_black": 10, "compress_white": 238, "warmth": 0.2}},
        {"op": "vignette", "params": {"radius": 0.7, "feather": 0.5, "darkness": 0.45}},
        {"op": "film_grain", "params": {"amount": 0.08, "monochrome": False}},
    ],
    "赛璐璐手绘 (Cel Shade)": [
        {"op": "cel_shade", "params": {"levels": 3}},
    ],
    "像素复古风 (Pixel Art)": [
        {"op": "pixelate", "params": {"scale": 4}},
    ],
    "动漫超分辨率 4x (Anime SR)": [
        {
            "op": "superres",
            "params": {"model": "realesrgan-x4plus-anime", "tile": 256, "overlap": 16},
        },
    ],
    "通用超分辨率 4x (General SR)": [
        {"op": "superres", "params": {"model": "realesrgan-x4plus", "tile": 256, "overlap": 16}},
    ],
}

OPERATORS: list[OperatorItem] = [
    # --- Anti-AI ---
    OperatorItem(
        key="film_grain",
        name="胶片微噪点 (Film Grain)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="打破AI平滑塑料感，注入有机模拟银盐颗粒与纸质微纹理",
        default_op="film_grain",
        default_params={"amount": 0.06, "monochrome": True, "shadow_bias": True},
        parameters=[
            Parameter(
                "amount",
                "噪点强度",
                "float",
                0.06,
                min_value=0.01,
                max_value=0.4,
                description="颗粒明显程度 (0.01~0.4)",
            ),
            Parameter(
                "monochrome",
                "单色颗粒",
                "bool",
                True,
                description="开启为黑白银盐颗粒，关闭为彩色杂色",
            ),
            Parameter(
                "shadow_bias",
                "暗部加权",
                "bool",
                True,
                description="模拟真实物理规律：高光颗粒少，中暗部颗粒明显",
            ),
        ],
    ),
    OperatorItem(
        key="de_ai_bloom",
        name="柔光光晕 (Mist Bloom)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="软化刺眼的数码计算边缘，高光产生漫射通透感同时保全线稿",
        default_op="de_ai_bloom",
        default_params={"radius": 8, "intensity": 0.3, "threshold": 180, "preserve_lines": True},
        parameters=[
            Parameter(
                "radius",
                "光晕半径",
                "int",
                8,
                min_value=1,
                max_value=40,
                description="柔光漫射扩散范围 (像素)",
            ),
            Parameter(
                "intensity",
                "发光强度",
                "float",
                0.3,
                min_value=0.05,
                max_value=1.0,
                description="高光光晕混合透明度",
            ),
            Parameter(
                "threshold",
                "高光阈值",
                "int",
                180,
                min_value=100,
                max_value=240,
                description="起光发亮点的最低明度 (0~255)",
            ),
            Parameter(
                "preserve_lines", "保护线稿", "bool", True, description="防止漫画描线被柔光冲淡变虚"
            ),
        ],
    ),
    OperatorItem(
        key="filmic_tone",
        name="胶片色调与高光压制 (Filmic Tone)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="压制死白高光，抬升死黑阴影，消除AI过度饱和数码感",
        default_op="filmic_tone",
        default_params={"lift_black": 6, "compress_white": 245, "saturation": 0.95, "warmth": 0.0},
        parameters=[
            Parameter(
                "lift_black",
                "抬升暗部",
                "int",
                6,
                min_value=0,
                max_value=30,
                description="将纯黑压向深炭灰/深蓝 (0~30)",
            ),
            Parameter(
                "compress_white",
                "高光收敛",
                "int",
                245,
                min_value=180,
                max_value=255,
                description="收敛刺眼过曝死白 (180~255)",
            ),
            Parameter(
                "saturation",
                "饱和度",
                "float",
                0.95,
                min_value=0.5,
                max_value=1.5,
                description="平抑AI过艳的糖精色彩",
            ),
            Parameter(
                "warmth",
                "冷暖色偏",
                "float",
                0.0,
                min_value=-0.8,
                max_value=0.8,
                description="正数为日落暖金，负数为清冷青蓝",
            ),
        ],
    ),
    OperatorItem(
        key="chromatic_aberration",
        name="光学镜头色差 (Chromatic)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="模拟真实物理玻璃镜头的红蓝边缘色散，打破数学绝对纯净感",
        default_op="chromatic_aberration",
        default_params={"shift": 2, "radial": True},
        parameters=[
            Parameter(
                "shift",
                "色散偏移",
                "int",
                2,
                min_value=1,
                max_value=15,
                description="红蓝边缘分离错位量 (像素)",
            ),
            Parameter(
                "radial", "径向衰减", "bool", True, description="中心清晰无色散，四周边缘产生色散"
            ),
        ],
    ),
    # --- ACG ---
    OperatorItem(
        key="focus_lines",
        name="漫画集中线 (Speed Lines)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="经典二次元漫画热血特写背景与视觉冲击放射线",
        default_op="focus_lines",
        default_params={
            "lines": 120,
            "inner_radius": 0.35,
            "max_width": 3,
            "line_color": "#000000",
        },
        parameters=[
            Parameter(
                "lines",
                "射线数量",
                "int",
                120,
                min_value=20,
                max_value=400,
                description="集中线密集程度 (20~400)",
            ),
            Parameter(
                "inner_radius",
                "主体留白",
                "float",
                0.35,
                min_value=0.1,
                max_value=0.8,
                description="中心主体不被线条遮挡的椭圆半径比例",
            ),
            Parameter(
                "max_width",
                "线条粗细",
                "int",
                3,
                min_value=1,
                max_value=10,
                description="外围最粗处的线宽 (像素)",
            ),
            Parameter(
                "line_color",
                "线条颜色",
                "str",
                "#000000",
                description="十六进制颜色，如 #000000 或 #ffffff",
            ),
        ],
    ),
    OperatorItem(
        key="screentone",
        name="黑白漫画网点 (Screentone)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="日式黑白漫画单行本经典 45° 印刷点阵排线与灰度表现",
        default_op="screentone",
        default_params={"frequency": 40, "angle": 45.0, "contrast": 1.2},
        parameters=[
            Parameter(
                "frequency",
                "网点密度",
                "int",
                40,
                min_value=10,
                max_value=100,
                description="点阵密度线数 (10~100)",
            ),
            Parameter(
                "angle",
                "网点倾角",
                "float",
                45.0,
                min_value=0.0,
                max_value=90.0,
                description="网点排列倾斜度 (标准日漫为 45 度)",
            ),
            Parameter(
                "contrast",
                "黑白反差",
                "float",
                1.2,
                min_value=0.5,
                max_value=2.5,
                description="网点过渡前的对比度增强",
            ),
        ],
    ),
    OperatorItem(
        key="sticker_outline",
        name="立绘贴纸描边 (Sticker Die-cut)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="角色立绘周边神器：沿透明边缘生成纯白轮廓与柔和立体投影",
        default_op="sticker_outline",
        default_params={
            "width": 6,
            "color": "#ffffff",
            "shadow": True,
            "shadow_offset": 4,
            "shadow_blur": 4,
        },
        parameters=[
            Parameter(
                "width",
                "白边宽度",
                "int",
                6,
                min_value=1,
                max_value=25,
                description="贴纸外扩轮廓宽度 (像素)",
            ),
            Parameter(
                "color", "边框颜色", "str", "#ffffff", description="边框颜色，默认纯白 #ffffff"
            ),
            Parameter("shadow", "立体投影", "bool", True, description="是否在贴纸下方生成柔和阴影"),
            Parameter(
                "shadow_offset",
                "阴影偏移",
                "int",
                4,
                min_value=1,
                max_value=20,
                description="阴影右下方偏移量",
            ),
        ],
    ),
    OperatorItem(
        key="lineart_extract",
        name="XDoG 漫画线稿提取 (Line Art)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于扩展高斯差分提取具有手绘笔触感的黑白线条轮廓",
        default_op="lineart_extract",
        default_params={"radius": 1.0, "k": 1.6, "gamma": 0.98, "threshold": 0.02},
        parameters=[
            Parameter(
                "radius",
                "线条精细度",
                "float",
                1.0,
                min_value=0.3,
                max_value=4.0,
                description="基础高斯核半径，越小线条越细",
            ),
            Parameter(
                "threshold",
                "线条阈值",
                "float",
                0.02,
                min_value=0.005,
                max_value=0.1,
                description="线条提取门槛，越小杂线越少",
            ),
        ],
    ),
    # --- Photo & Light ---
    OperatorItem(
        key="vignette",
        name="镜头暗角 (Vignette)",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="四周柔和光学暗角，聚焦画面视觉核心",
        default_op="vignette",
        default_params={"radius": 0.75, "feather": 0.5, "darkness": 0.5},
        parameters=[
            Parameter(
                "radius",
                "清晰范围",
                "float",
                0.75,
                min_value=0.2,
                max_value=1.5,
                description="中心免受暗角影响的半径比例",
            ),
            Parameter(
                "feather",
                "边缘羽化",
                "float",
                0.5,
                min_value=0.1,
                max_value=1.0,
                description="暗角平滑渐变的过渡宽度",
            ),
            Parameter(
                "darkness",
                "暗角深度",
                "float",
                0.5,
                min_value=0.1,
                max_value=0.9,
                description="边缘最大压暗比例 (0.1~0.9)",
            ),
        ],
    ),
    OperatorItem(
        key="color_temperature",
        name="色温与色调 (White Balance)",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="快速调整冷暖色温与洋红/绿色调偏向",
        default_op="color_temperature",
        default_params={"temperature": 0.0, "tint": 0.0},
        parameters=[
            Parameter(
                "temperature",
                "色温调节",
                "float",
                0.0,
                min_value=-0.8,
                max_value=0.8,
                description="负数偏冰蓝，正数偏暖阳",
            ),
            Parameter(
                "tint",
                "色调调节",
                "float",
                0.0,
                min_value=-0.8,
                max_value=0.8,
                description="负数偏草绿，正数偏洋红",
            ),
        ],
    ),
    # --- Art & Style ---
    OperatorItem(
        key="cel_shade",
        name="赛璐璐分阶 (Cel Shade)",
        category="art",
        category_label=CATEGORIES["art"],
        description="传统动画手绘分阶着色风格化",
        default_op="cel_shade",
        default_params={"levels": 3},
        parameters=[
            Parameter(
                "levels",
                "色彩阶数",
                "int",
                3,
                min_value=2,
                max_value=8,
                description="每个通道保留的明暗分阶数 (2~8)",
            ),
        ],
    ),
    OperatorItem(
        key="pixelate",
        name="像素马赛克 (Pixelate)",
        category="art",
        category_label=CATEGORIES["art"],
        description="经典复古游戏像素点阵颗粒化",
        default_op="pixelate",
        default_params={"scale": 4},
        parameters=[
            Parameter(
                "scale",
                "像素块大小",
                "int",
                4,
                min_value=2,
                max_value=16,
                description="像素化缩放马赛克尺寸",
            ),
        ],
    ),
    # --- Super-Resolution ---
    OperatorItem(
        key="superres",
        name="AI 超分辨率 (Super-Resolution)",
        category="superres",
        category_label=CATEGORIES["superres"],
        description="基于本地 ONNX 神经模型无损放大重建细节",
        default_op="superres",
        default_params={"model": "realesrgan-x4plus-anime", "tile": 256, "overlap": 16},
        parameters=[
            Parameter(
                "model",
                "超分模型",
                "choice",
                "realesrgan-x4plus-anime",
                choices=[
                    "realesrgan-x4plus-anime",
                    "realesrgan-x4plus",
                    "realesrgan-x2plus",
                    "swinir-s-x2",
                    "swinir-s-x4",
                ],
                description="可选动漫专化 (Anime) 或通用照片 (General)",
            ),
            Parameter(
                "tile",
                "瓦片分块",
                "choice",
                "256",
                choices=["128", "256", "512"],
                description="分块推理尺寸，显存较小时选 128",
            ),
            Parameter(
                "overlap",
                "分块重叠",
                "int",
                16,
                min_value=8,
                max_value=32,
                description="消除瓦片接缝的重叠边缘像素",
            ),
        ],
    ),
]

OPERATORS_BY_OP: dict[str, OperatorItem] = {item.default_op: item for item in OPERATORS}


def get_operator_item(op: str) -> OperatorItem | None:
    return OPERATORS_BY_OP.get(op)

