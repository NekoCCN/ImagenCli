"""Operator catalog and metadata for interactive parameter tuning in CLI and TUI."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from imagen_cli import i18n

from . import operator_catalog_en


@dataclass(frozen=True)
class Parameter:
    name: str
    label: str
    param_type: str  # "int", "float", "bool", "str", "choice"
    default: Any
    min_value: float | int | None = None
    max_value: float | int | None = None
    choices: list[str | int] | None = None
    description: str = ""
    is_advanced: bool = False
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        """What a caller needs to type `name=value` without reading source."""
        return {
            "name": self.name,
            "label": self.label,
            "type": self.param_type,
            "default": self.default,
            "min": self.min_value,
            "max": self.max_value,
            "choices": list(self.choices) if self.choices else None,
            "description": self.description,
            "advanced": self.is_advanced,
            "required": self.required,
        }


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

    @property
    def basic_parameters(self) -> list[Parameter]:
        """返回基础浓缩参数列表。若未特别指定高级参数，则返回全部参数。"""
        basics = [p for p in self.parameters if not p.is_advanced]
        return basics if basics else list(self.parameters)

    @property
    def advanced_parameters(self) -> list[Parameter]:
        """返回全部微观高级参数列表。"""
        return list(self.parameters)

    def build_pipeline(self, overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(self.default_params)
        if overrides:
            params.update(overrides)
        for parameter in self.parameters:
            if parameter.required:
                value = params.get(parameter.name)
                if value is None or (isinstance(value, str) and not value.strip()):
                    raise ValueError(f"{parameter.name} is required")
        return [{"op": self.default_op, "params": params}]


CATEGORIES: dict[str, str] = {
    "preset": "处理配方",
    "anti_ai": "平滑与缺陷",
    "acg": "线条与色块",
    "photo": "颜色明暗",
    "art": "风格光学",
    "superres": "模型恢复",
    "geometry": "尺寸几何",
}
_CATEGORY_LABELS_EN = {
    label: operator_catalog_en.CATEGORY_LABELS[key] for key, label in CATEGORIES.items()
}

# Rich presets mapping
PRESET_PIPELINES: dict[str, list[dict[str, Any]]] = {
    "像素化": [{"op": "pixelate", "params": {"scale": 4}}],
    "调色板": [{"op": "quantize", "params": {"colors": 16, "dither": True}}],
    "线稿": [
        {"op": "grayscale"},
        {"op": "autocontrast"},
        {"op": "highpass", "params": {"radius": 2, "boost": 2}},
    ],
    "抖动": [{"op": "dither", "params": {"mode": "atkinson"}}],
    "锐化": [{"op": "sharpen", "params": {"radius": 2, "percent": 160}}],
    "赛璐璐": [{"op": "cel_shade", "params": {"levels": 3}}],
    "去AI塑料感 (Anti-AI Organic)": [
        {
            "op": "surface_blur",
            "params": {"radius": 3, "threshold": 18.0, "blend": 0.8},
        },
        {
            "op": "filmic_tone",
            "params": {"lift_black": 2, "compress_white": 250, "saturation": 0.98},
        },
        {
            "op": "de_ai_bloom",
            "params": {"radius": 4, "intensity": 0.08, "threshold": 230, "preserve_lines": True},
        },
    ],
    "抗AI生成伪影综合治理 (Anti-AI Artifacts Suppression)": [
        {"op": "anti_checkerboard", "params": {"strength": 0.3, "cutoff": 0.85}},
        {"op": "de_haloing", "params": {"radius": 2, "strength": 0.5, "threshold": 25}},
        {
            "op": "surface_blur",
            "params": {"radius": 4, "threshold": 20.0, "blend": 0.85},
        },
        {
            "op": "subsurface_scattering",
            "params": {"radius": 3, "scatter_strength": 0.12, "skin_only": True},
        },
        {
            "op": "filmic_tone",
            "params": {"lift_black": 2, "compress_white": 250, "saturation": 0.98},
        },
    ],
    "二次元平涂去杂 (Anime Cel Surface Smoothing)": [
        {
            "op": "surface_blur",
            "params": {"radius": 4, "threshold": 22.0, "blend": 0.9},
        },
        {
            "op": "color_purity",
            "params": {"shadow_desat": 0.3, "shadow_threshold": 65.0, "vibrance_boost": 0.12},
        },
    ],
    "动漫色彩提纯 (Anime Color Purity)": [
        {
            "op": "color_purity",
            "params": {"shadow_desat": 0.4, "shadow_threshold": 70.0, "vibrance_boost": 0.2},
        },
    ],
    "二次元线条强化与描墨 (Anime Line Inking)": [
        {
            "op": "anime_line_enhance",
            "params": {
                "strength": 1.0,
                "thickness": 1.0,
                "sigma": 1.0,
                "k_ratio": 1.6,
                "tau": 0.98,
                "gamma": 1.5,
                "closing_radius": 1,
            },
        },
    ],
    "二次元赛璐珞顶级精修 (Anime Cel Master)": [
        {
            "op": "surface_blur",
            "params": {"radius": 4, "threshold": 22.0, "blend": 0.85},
        },
        {
            "op": "anime_line_enhance",
            "params": {"strength": 0.9, "thickness": 1.0},
        },
        {
            "op": "color_purity",
            "params": {"shadow_desat": 0.35, "shadow_threshold": 65.0, "vibrance_boost": 0.15},
        },
    ],
    "ACG 赛璐璐轻修复 (ACG Cel Cleanup)": [
        {"op": "chroma_denoise", "params": {"radius": 2, "strength": 0.7}},
        {
            "op": "surface_blur",
            "params": {"radius": 3, "threshold": 20.0, "blend": 0.75},
        },
        {"op": "cel_banding_reduce", "params": {"strength": 0.25, "threshold": 10}},
    ],
    "ACG 压缩截图修复 (ACG Compressed Still)": [
        {"op": "chroma_denoise", "params": {"radius": 2, "strength": 0.8}},
        {"op": "de_haloing", "params": {"radius": 2, "strength": 0.4, "threshold": 24}},
        {"op": "edge_antialias", "params": {"radius": 1.0, "strength": 0.45, "threshold": 14}},
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
    "日系治愈空气柔光 (Anime Soft Glow)": [
        {"op": "diffuse_glow", "params": {"intensity": 0.4, "radius": 20, "threshold": 175}},
        {"op": "vibrance", "params": {"amount": 0.25}},
        {"op": "line_color", "params": {"strength": 0.7, "darkness": 0.4}},
    ],
    "大魄力动感鱼眼 (Anime Fisheye)": [
        {"op": "fisheye", "params": {"strength": 0.35, "zoom": 1.05}},
        {"op": "focus_lines", "params": {"lines": 60, "inner_radius": 0.35, "max_width": 2}},
    ],
    "橙青影视色彩平衡 (Teal & Orange)": [
        {
            "op": "color_balance",
            "params": {
                "shadows": [-0.15, -0.05, 0.2],
                "highlights": [0.2, 0.05, -0.15],
            },
        },
        {"op": "filmic_tone", "params": {"lift_black": 8, "compress_white": 242}},
        {"op": "film_grain", "params": {"amount": 0.05, "monochrome": True}},
    ],
}


PRESETS = PRESET_PIPELINES

OPERATORS: list[OperatorItem] = [
    # --- Anti-AI ---
    OperatorItem(
        key="film_grain",
        name="胶片微噪点 (Film Grain)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="基于照度加权的伪随机高斯银盐微粒噪声合成算子。",
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
                description="颗粒强度系数 (0.01~0.4)",
            ),
            Parameter(
                "monochrome",
                "单色颗粒",
                "bool",
                True,
                description="启用单通道灰阶噪点，关闭则为多通道彩色噪点",
            ),
            Parameter(
                "shadow_bias",
                "暗部加权",
                "bool",
                True,
                description="根据反照度加权暗部噪点强度",
            ),
        ],
    ),
    OperatorItem(
        key="de_ai_bloom",
        name="柔光光晕 (Mist Bloom)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="基于亮度阈值分割与多尺度高斯核的高光漫射保边算子。",
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
                description="高斯核模糊半径",
            ),
            Parameter(
                "intensity",
                "发光强度",
                "float",
                0.3,
                min_value=0.05,
                max_value=1.0,
                description="滤色通道线性叠加权重",
            ),
            Parameter(
                "threshold",
                "高光阈值",
                "int",
                180,
                min_value=100,
                max_value=240,
                description="提取漫射区域的起始照度阈值",
            ),
            Parameter(
                "preserve_lines",
                "保护线稿",
                "bool",
                True,
                description="启用暗部梯度掩模保全线稿轮廓",
            ),
        ],
    ),
    OperatorItem(
        key="filmic_tone",
        name="胶片色调与高光压制 (Filmic Tone)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="使用幂函数 LUT 调整黑白端点，并调节饱和度与冷暖色偏。",
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
                description="暗部基底电平提升值",
            ),
            Parameter(
                "compress_white",
                "高光收敛",
                "int",
                245,
                min_value=180,
                max_value=255,
                description="亮部饱和截断压缩阈值",
            ),
            Parameter(
                "saturation",
                "饱和度",
                "float",
                0.95,
                min_value=0.5,
                max_value=1.5,
                description="色彩空间全局饱和度缩放倍率",
            ),
            Parameter(
                "warmth",
                "冷暖色偏",
                "float",
                0.0,
                min_value=-0.8,
                max_value=0.8,
                description="红蓝通道互补色温平移系数",
            ),
        ],
    ),
    OperatorItem(
        key="chromatic_aberration",
        name="光学镜头横向色差 (Lateral Chromatic Aberration)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description=(
            "本算子基于径向距离权重的色通道横向几何色散模拟，重构真实光学透镜的边缘色散特性。"
        ),
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
                description="红蓝色通道空间位移像素量",
            ),
            Parameter(
                "radial",
                "径向衰减",
                "bool",
                True,
                description="根据距视口中心欧氏距离调节色散位移量",
            ),
        ],
    ),
    OperatorItem(
        key="anti_checkerboard",
        name="网格去混叠 (Anti-Checkerboard)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description=(
            "本算子基于二维离散傅里叶变换与高频陷波掩模，衰减潜空间"
            "转置卷积引入的周期性棋盘格网格尖峰。"
        ),
        default_op="anti_checkerboard",
        default_params={"strength": 0.5, "cutoff": 0.85},
        parameters=[
            Parameter(
                "strength",
                "抑制强度",
                "float",
                0.5,
                min_value=0.0,
                max_value=1.0,
                description="频域陷波衰减与空间重构混合权重",
            ),
            Parameter(
                "cutoff",
                "高频截止半径",
                "float",
                0.85,
                min_value=0.5,
                max_value=1.0,
                description="触发陷波衰减的归一化奈奎斯特频率半径阈值",
            ),
        ],
    ),
    OperatorItem(
        key="subsurface_scattering",
        name="肤色区域柔化 (Skin Softening)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="分别模糊 RGB 通道并混合原图，可用肤色掩模限制作用区域。",
        default_op="subsurface_scattering",
        default_params={"radius": 5, "scatter_strength": 0.35, "skin_only": True},
        parameters=[
            Parameter(
                "radius",
                "散射基准半径",
                "int",
                5,
                min_value=1,
                max_value=30,
                description="红通道高斯模糊半径，绿蓝通道使用较小半径",
            ),
            Parameter(
                "scatter_strength",
                "漫射混合权重",
                "float",
                0.35,
                min_value=0.0,
                max_value=1.0,
                description="RGB 柔化结果与原图的混合比例",
            ),
            Parameter(
                "skin_only",
                "限制肤色区域",
                "bool",
                True,
                description="启用 YCbCr 空间生物肤色色度检测掩模与羽化过渡",
            ),
        ],
    ),
    OperatorItem(
        key="de_haloing",
        name="去边缘白边光晕 (De-Haloing)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description=(
            "本算子基于局部中值滤波与极值形态学夹紧约束，抑制高反差"
            "边缘处由采样器过度锐化引入的振铃白边与暗光晕伪影。"
        ),
        default_op="de_haloing",
        default_params={"radius": 2, "strength": 0.6, "threshold": 25},
        parameters=[
            Parameter(
                "radius",
                "滤波窗口半径",
                "int",
                2,
                min_value=1,
                max_value=8,
                description="局部中值统计窗口的像素空间半径",
            ),
            Parameter(
                "strength",
                "抑制强度",
                "float",
                0.6,
                min_value=0.0,
                max_value=1.0,
                description="偏离局部中值过冲像素的收敛截断比例",
            ),
            Parameter(
                "threshold",
                "过冲跳变阈值",
                "int",
                25,
                min_value=5,
                max_value=100,
                description="允许保留的边缘自然反差容差梯度值",
            ),
        ],
    ),
    # --- ACG ---
    OperatorItem(
        key="focus_lines",
        name="漫画集中线 (Speed Lines)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于极坐标角向离散射线投影的径向集中线生成算子。",
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
                description="角向离散射线生成条数",
            ),
            Parameter(
                "inner_radius",
                "主体留白",
                "float",
                0.35,
                min_value=0.1,
                max_value=0.8,
                description="中心保留区域的椭圆长短轴比例",
            ),
            Parameter(
                "max_width",
                "线条粗细",
                "int",
                3,
                min_value=1,
                max_value=10,
                description="射线外边缘最大线宽像素",
            ),
            Parameter(
                "line_color",
                "线条颜色",
                "str",
                "#000000",
                description="十六进制颜色代码",
            ),
        ],
    ),
    OperatorItem(
        key="screentone",
        name="黑白漫画网点 (Screentone)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于二维空间旋转网格的调幅半色调网点光栅化算子。",
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
                description="空间采样网格频率线数",
            ),
            Parameter(
                "angle",
                "网点倾角",
                "float",
                45.0,
                min_value=0.0,
                max_value=90.0,
                description="二维网格旋转角度",
            ),
            Parameter(
                "contrast",
                "黑白反差",
                "float",
                1.2,
                min_value=0.5,
                max_value=2.5,
                description="色阶量化前对比度缩放系数",
            ),
        ],
    ),
    OperatorItem(
        key="sticker_outline",
        name="立绘贴纸描边 (Sticker Die-cut)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于 Alpha 通道距离变换形态学膨胀的轮廓描边与投影算子。",
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
                description="距离场膨胀像素宽度",
            ),
            Parameter(
                "color", "边框颜色", "str", "#ffffff", description=("外轮廓十六进制颜色代码")
            ),
            Parameter("shadow", "立体投影", "bool", True, description=("是否合成低频偏移阴影层")),
            Parameter(
                "shadow_offset",
                "阴影偏移",
                "int",
                4,
                min_value=1,
                max_value=20,
                description="阴影层平移向量模长",
            ),
        ],
    ),
    OperatorItem(
        key="lineart_extract",
        name="XDoG 漫画线稿提取 (Line Art)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于扩展高斯差分 (XDoG) 与非线性双阈值化的线稿提取算子。",
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
                description="基础高斯核标准差半径",
            ),
            Parameter(
                "threshold",
                "线条阈值",
                "float",
                0.02,
                min_value=0.005,
                max_value=0.1,
                description="二值化截断判定阈值",
            ),
        ],
    ),
    # --- Photo & Light ---
    OperatorItem(
        key="vignette",
        name="镜头暗角 (Vignette)",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="按径向距离生成渐变掩模，压暗图像边缘。",
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
                description="未衰减中心椭圆半径系数",
            ),
            Parameter(
                "feather",
                "边缘羽化",
                "float",
                0.5,
                min_value=0.1,
                max_value=1.0,
                description="径向掩模过渡带宽度",
            ),
            Parameter(
                "darkness",
                "暗角深度",
                "float",
                0.5,
                min_value=0.1,
                max_value=0.9,
                description="边界最大照度衰减率",
            ),
        ],
    ),
    OperatorItem(
        key="color_temperature",
        name="色温与色调 (White Balance)",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="使用 RGB 通道 LUT 调节冷暖与绿品红色偏，不估计光源色温。",
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
                description="黄蓝轴色温偏移系数",
            ),
            Parameter(
                "tint",
                "色调调节",
                "float",
                0.0,
                min_value=-0.8,
                max_value=0.8,
                description="绿品红轴色调偏移系数",
            ),
        ],
    ),
    # --- Art & Style ---
    OperatorItem(
        key="cel_shade",
        name="赛璐璐分阶 (Cel Shade)",
        category="art",
        category_label=CATEGORIES["art"],
        description="基于亮度空间均匀量化截断的非真实感分阶着色算子。",
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
                description="各色彩通道离散阶梯量化级别",
            ),
        ],
    ),
    OperatorItem(
        key="pixelate",
        name="像素马赛克 (Pixelate)",
        category="art",
        category_label=CATEGORIES["art"],
        description="基于空间块平均降采样与邻近插值的马赛克点阵重采样算子。",
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
                description="空间采样网格块线度像素",
            ),
        ],
    ),
    # --- Super-Resolution ---
    OperatorItem(
        key="superres",
        name="AI 模型恢复 (Model Restoration)",
        category="superres",
        category_label=CATEGORIES["superres"],
        description="本地模型推理：超分辨率、原尺寸降噪或 JPEG 伪影清理；任务取决于所选权重。",
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
                    "realesr-animevideov3",
                    "realesrgan-x4plus",
                    "realesrgan-x2plus",
                    "swinir-s-x2",
                    "swinir-s-x4",
                    "film-degrainer",
                    "dejpg-srformer-light",
                ],
                description="用于推理重构的预训练权重结构",
            ),
            Parameter(
                "tile",
                "瓦片分块",
                "choice",
                256,
                choices=[128, 256, 512],
                description="分块推理空间尺寸",
            ),
            Parameter(
                "overlap",
                "分块重叠",
                "int",
                16,
                min_value=8,
                max_value=32,
                description="瓦片拼接余量像素以消除边缘伪影",
            ),
        ],
    ),
    # --- Advanced Paper-based Ops ---
    OperatorItem(
        key="aces_tonemap",
        name="ACES 电影级色调映射 (ACES Filmic Tone)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="基于 ACES 拟合有理函数的宽动态范围 S 型曲线色调映射算子。",
        default_op="aces_tonemap",
        default_params={"exposure": 1.0, "contrast": 1.0},
        parameters=[
            Parameter(
                "exposure",
                "曝光系数",
                "float",
                1.0,
                min_value=0.2,
                max_value=3.0,
                description="输入线性辐射度缩放系数",
            ),
            Parameter(
                "contrast",
                "中灰对比度",
                "float",
                1.0,
                min_value=0.5,
                max_value=2.0,
                description="中间调斜率调整因子",
            ),
        ],
    ),
    OperatorItem(
        key="guided_filter",
        name="引导滤波保边纹理 (Guided Filter)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="基于局部线性模型的保边引导滤波与高频残差细节增强算子。",
        default_op="guided_filter",
        default_params={"radius": 4, "eps": 0.04, "boost": 1.5},
        parameters=[
            Parameter(
                "radius",
                "局部半径",
                "int",
                4,
                min_value=1,
                max_value=16,
                description="局部线性回归核空间窗口半径",
            ),
            Parameter(
                "eps",
                "平滑正则化",
                "float",
                0.04,
                min_value=0.001,
                max_value=0.2,
                description="方差估计岭回归正则化常数",
            ),
            Parameter(
                "boost",
                "细节增益",
                "float",
                1.5,
                min_value=0.5,
                max_value=3.0,
                description="提取高频残差层乘法放大系数",
            ),
        ],
    ),
    OperatorItem(
        key="kuwahara",
        name="桑原动漫平涂滤波 (Kuwahara Filter)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="比较四象限局部方差，选取较均匀区域的均值以平滑色块。",
        default_op="kuwahara",
        default_params={"size": 5},
        parameters=[
            Parameter(
                "size",
                "窗口尺寸",
                "int",
                5,
                min_value=3,
                max_value=15,
                description="四象限滤波窗口像素边长",
            ),
        ],
    ),
    OperatorItem(
        key="anamorphic_flare",
        name="剧场版宽银幕眩光 (Anamorphic Flare)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于各向异性横向一维卷积核的变形镜头高光光斑模拟算子。",
        default_op="anamorphic_flare",
        default_params={"threshold": 215, "length": 36, "tint": "#4a90e2", "intensity": 0.65},
        parameters=[
            Parameter(
                "threshold",
                "高光阈值",
                "int",
                215,
                min_value=120,
                max_value=250,
                description="产生条纹的起始像素照度截断阈值",
            ),
            Parameter(
                "length",
                "眩光横向长度",
                "int",
                36,
                min_value=10,
                max_value=120,
                description="水平轴向模糊核卷积长度",
            ),
            Parameter(
                "tint",
                "眩光色调",
                "str",
                "#4a90e2",
                description="光斑着色十六进制色彩代码",
            ),
            Parameter(
                "intensity",
                "混合强度",
                "float",
                0.65,
                min_value=0.1,
                max_value=1.5,
                description="光斑线性加色混合权重",
            ),
        ],
    ),
    OperatorItem(
        key="color_transfer",
        name="色彩空间风格迁移 (Color Transfer)",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="基于 Lab 色彩空间一阶与二阶统计矩对齐的全局色彩迁移算子。",
        default_op="color_transfer",
        default_params={"style": "anime_sky", "strength": 1.0},
        parameters=[
            Parameter(
                "style",
                "调色风格",
                "choice",
                "anime_sky",
                choices=["anime_sky", "cyberpunk", "vintage_warm", "morandi"],
                description="参考图像色彩空间一阶与二阶统计量分布",
            ),
            Parameter(
                "strength",
                "迁移浓度",
                "float",
                1.0,
                min_value=0.1,
                max_value=1.0,
                description="均值方差对齐线性插值权重",
            ),
        ],
    ),
    OperatorItem(
        key="line_color",
        name="线稿自适应染色 (Line Color)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于低照度边缘加权与邻域低频基底色彩融合的线稿自适应染色算子。",
        default_op="line_color",
        default_params={"strength": 0.8, "darkness": 0.5},
        parameters=[
            Parameter(
                "strength",
                "染色强度",
                "float",
                0.8,
                min_value=0.0,
                max_value=1.0,
                description="边缘融合权重系数",
            ),
            Parameter(
                "darkness",
                "暗度保留",
                "float",
                0.5,
                min_value=0.0,
                max_value=1.0,
                description="染色基底亮度缩放衰减率",
            ),
        ],
    ),
    OperatorItem(
        key="color_balance",
        name="影调色彩平衡 (Color Balance)",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="基于亮度区间平滑权重分割的三段式色彩平衡调节算子。",
        default_op="color_balance",
        default_params={
            "shadows": [0.0, 0.0, 0.0],
            "midtones": [0.0, 0.0, 0.0],
            "highlights": [0.0, 0.0, 0.0],
        },
        parameters=[
            Parameter(
                "shadows",
                "阴影偏移",
                "str",
                "0.0,0.0,0.0",
                description="暗部通道加性偏移向量 (-1.0~1.0)",
            ),
            Parameter(
                "midtones",
                "中间调偏移",
                "str",
                "0.0,0.0,0.0",
                description="中间调通道加性偏移向量 (-1.0~1.0)",
            ),
            Parameter(
                "highlights",
                "高光偏移",
                "str",
                "0.0,0.0,0.0",
                description="高光通道加性偏移向量 (-1.0~1.0)",
            ),
        ],
    ),
    OperatorItem(
        key="fisheye",
        name="鱼眼广角透视 (Fisheye Lens)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于 Brown-Conrady 径向多项式模型的反向映射透镜畸变算子。",
        default_op="fisheye",
        default_params={"strength": 0.35, "zoom": 1.0},
        parameters=[
            Parameter(
                "strength",
                "畸变强度",
                "float",
                0.35,
                min_value=-0.8,
                max_value=1.0,
                description="径向多项式二次项畸变系数",
            ),
            Parameter(
                "zoom",
                "视场缩放",
                "float",
                1.0,
                min_value=0.5,
                max_value=2.0,
                description="空间采样坐标线性缩放倍数",
            ),
        ],
    ),
    OperatorItem(
        key="vibrance",
        name="智能自然饱和度 (Vibrance)",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="基于反饱和度加权与暖色调掩模衰减的非线性自然饱和度调节算子。",
        default_op="vibrance",
        default_params={"amount": 0.3},
        parameters=[
            Parameter(
                "amount",
                "自然饱和度",
                "float",
                0.3,
                min_value=-1.0,
                max_value=1.0,
                description="未饱和区域增益系数",
            ),
        ],
    ),
    OperatorItem(
        key="diffuse_glow",
        name="日漫扩散柔光 (Diffuse Glow)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于亮度阈值分割与滤色混合模式的高光多尺度漫反射光晕算子。",
        default_op="diffuse_glow",
        default_params={"threshold": 180, "radius": 20, "intensity": 0.4},
        parameters=[
            Parameter(
                "threshold",
                "高光阈值",
                "int",
                180,
                min_value=0,
                max_value=255,
                description="提取发光能量分布的照度阈值",
            ),
            Parameter(
                "radius",
                "光晕半径",
                "int",
                20,
                min_value=1,
                max_value=100,
                description="漫射高斯核空间滤波半径",
            ),
            Parameter(
                "intensity",
                "柔光浓度",
                "float",
                0.4,
                min_value=0.0,
                max_value=1.0,
                description="滤色通道线性叠加权重",
            ),
        ],
    ),
    OperatorItem(
        key="surface_blur",
        name="表面去杂平滑 (Surface Blur)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="基于色差引导高斯权重与中值滤波结合的二次元色块去杂色保边算子。",
        default_op="surface_blur",
        default_params={"radius": 4, "threshold": 20.0, "blend": 0.85},
        parameters=[
            Parameter(
                "blend",
                "混合强度",
                "float",
                0.85,
                min_value=0.0,
                max_value=1.0,
                description="平滑效果与原图的融合权重系数",
                is_advanced=False,
            ),
            Parameter(
                "radius",
                "平滑半径",
                "int",
                4,
                min_value=1,
                max_value=30,
                description="平滑滤波空间邻域核半径",
                is_advanced=True,
            ),
            Parameter(
                "threshold",
                "色差阈值",
                "float",
                20.0,
                min_value=0.0,
                max_value=100.0,
                description="色差容差阈值，低于该差值被判定为同色块并熨平均匀",
                is_advanced=True,
            ),
        ],
    ),
    OperatorItem(
        key="color_purity",
        name="动漫色彩提纯 (Color Purity)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="基于暗部照度掩模的阴影去脏与色彩纯度自适应增强算子。",
        default_op="color_purity",
        default_params={"shadow_desat": 0.35, "shadow_threshold": 70.0, "vibrance_boost": 0.15},
        parameters=[
            Parameter(
                "vibrance_boost",
                "色彩提纯",
                "float",
                0.15,
                min_value=-1.0,
                max_value=1.0,
                description="主体未饱和色彩的自然通透度增益系数",
                is_advanced=False,
            ),
            Parameter(
                "shadow_desat",
                "暗部去杂色",
                "float",
                0.35,
                min_value=0.0,
                max_value=1.0,
                description="阴影区域杂乱反光的饱和度衰减比例",
                is_advanced=True,
            ),
            Parameter(
                "shadow_threshold",
                "暗部范围",
                "float",
                70.0,
                min_value=0.0,
                max_value=255.0,
                description="定义阴影暗部的照度阈值界限",
                is_advanced=True,
            ),
        ],
    ),
    OperatorItem(
        key="anime_line_enhance",
        name="动漫线条增强 (Line Inking)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="使用各向同性 DoG 与方形形态学闭运算提取暗线，再乘法压暗原图。",
        default_op="anime_line_enhance",
        default_params={
            "strength": 1.0,
            "thickness": 1.0,
            "sigma": 1.0,
            "k_ratio": 1.6,
            "tau": 0.98,
            "gamma": 1.5,
            "closing_radius": 1,
        },
        parameters=[
            Parameter(
                "strength",
                "强化强度",
                "float",
                1.0,
                min_value=0.0,
                max_value=3.0,
                description="线条加深压墨的全局融合权重系数",
                is_advanced=False,
            ),
            Parameter(
                "thickness",
                "线条粗细",
                "float",
                1.0,
                min_value=0.2,
                max_value=5.0,
                description="描线宽度缩放系数",
                is_advanced=False,
            ),
            Parameter(
                "sigma",
                "高斯尺度",
                "float",
                1.0,
                min_value=0.1,
                max_value=10.0,
                description="中心尺度滤波核标准差",
                is_advanced=True,
            ),
            Parameter(
                "k_ratio",
                "核宽比例",
                "float",
                1.6,
                min_value=1.0,
                max_value=5.0,
                description="差分高斯外核与内核宽度比率",
                is_advanced=True,
            ),
            Parameter(
                "tau",
                "敏感度阈值",
                "float",
                0.98,
                min_value=0.5,
                max_value=1.2,
                description="边缘检测响应敏感度阈值",
                is_advanced=True,
            ),
            Parameter(
                "gamma",
                "压墨对比度",
                "float",
                1.5,
                min_value=0.2,
                max_value=5.0,
                description="非线性曲线伽马对比度增强系数",
                is_advanced=True,
            ),
            Parameter(
                "closing_radius",
                "闭合半径",
                "int",
                1,
                min_value=0,
                max_value=5,
                description="方形闭运算半径，可能连接邻近线条",
                is_advanced=True,
            ),
        ],
    ),
    OperatorItem(
        key="chroma_denoise",
        name="色彩降噪 (Chroma Denoise)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="只对 Cb/Cr 色度做中值滤波，压低压缩色噪，尽量保留亮度线条。",
        default_op="chroma_denoise",
        default_params={"radius": 2, "strength": 0.8},
        parameters=[
            Parameter(
                "radius",
                "色噪半径",
                "int",
                2,
                min_value=1,
                max_value=3,
                description="中值滤波窗口半径；越大越平但越慢",
                is_advanced=False,
            ),
            Parameter(
                "strength",
                "降噪强度",
                "float",
                0.8,
                min_value=0.0,
                max_value=1.0,
                description="与原色度的混合比例",
                is_advanced=False,
            ),
        ],
    ),
    OperatorItem(
        key="edge_antialias",
        name="边缘抗锯齿 (Edge Antialias)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="在检测到的边缘附近做轻度柔化，减轻放大后的阶梯锯齿，平坦色块保持不变。",
        default_op="edge_antialias",
        default_params={"radius": 1.0, "strength": 0.6, "threshold": 16},
        parameters=[
            Parameter(
                "radius",
                "柔化半径",
                "float",
                1.0,
                min_value=0.3,
                max_value=3.0,
                description="边缘附近的高斯模糊半径",
                is_advanced=False,
            ),
            Parameter(
                "strength",
                "作用强度",
                "float",
                0.6,
                min_value=0.0,
                max_value=1.0,
                description="边缘柔化权重",
                is_advanced=False,
            ),
            Parameter(
                "threshold",
                "边缘阈值",
                "int",
                16,
                min_value=1,
                max_value=128,
                description="FIND_EDGES 响应阈值，越高只处理越明显的边缘",
                is_advanced=True,
            ),
        ],
    ),
    OperatorItem(
        key="cel_banding_reduce",
        name="色带抑制 (Cel Banding Reduce)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="只在低对比渐变区域加入固定有序抖动，打散赛璐璐渐变中的色带。",
        default_op="cel_banding_reduce",
        default_params={"strength": 0.4, "threshold": 12},
        parameters=[
            Parameter(
                "strength",
                "抖动强度",
                "float",
                0.4,
                min_value=0.0,
                max_value=1.0,
                description="有序抖动幅度",
                is_advanced=False,
            ),
            Parameter(
                "threshold",
                "平坦阈值",
                "int",
                12,
                min_value=4,
                max_value=64,
                description="局部亮度跨度低于该值才视为渐变区域",
                is_advanced=True,
            ),
        ],
    ),
]

OPERATORS.extend(
    [
        OperatorItem(
            key="guided_smooth",
            name="引导保边平滑",
            category="anti_ai",
            category_label=CATEGORIES["anti_ai"],
            description="使用局部线性引导模型平滑编码 sRGB 色彩，再与原图混合；细线也可能减弱。",
            default_op="guided_smooth",
            default_params={
                "radius": 4,
                "epsilon": 0.001,
                "strength": 0.5,
                "guidance": "luma",
                "border": "reflect",
            },
            parameters=[
                Parameter("radius", "邻域半径（输入像素）", "int", 4, 1, 128),
                Parameter(
                    "epsilon",
                    "引导方差正则项",
                    "float",
                    0.001,
                    1e-6,
                    1,
                    description="归一化引导值的平方单位",
                ),
                Parameter("strength", "混合强度", "float", 0.5, 0, 1),
                Parameter(
                    "guidance",
                    "引导通道",
                    "choice",
                    "luma",
                    choices=["luma", "rgb"],
                    is_advanced=True,
                ),
                Parameter(
                    "border", "边界策略", "choice", "reflect", choices=["reflect"], is_advanced=True
                ),
            ],
        ),
        OperatorItem(
            key="rolling_guidance",
            name="迭代引导纹理平滑",
            category="anti_ai",
            category_label=CATEGORIES["anti_ai"],
            description="初始尺度平滑后迭代联合双边引导，减少细碎纹理；可能抹去小结构。",
            default_op="rolling_guidance",
            default_params={
                "sigma_space": 3,
                "sigma_range": 0.1,
                "iterations": 4,
                "strength": 0.5,
                "inner": "joint_bilateral",
                "border": "reflect",
            },
            parameters=[
                Parameter("sigma_space", "空间尺度（输入像素）", "float", 3, 0.5, 32),
                Parameter(
                    "sigma_range",
                    "范围尺度",
                    "float",
                    0.1,
                    0.001,
                    1,
                    description="归一化编码 sRGB 强度单位",
                ),
                Parameter("iterations", "迭代次数", "int", 4, 1, 10),
                Parameter("strength", "混合强度", "float", 0.5, 0, 1),
                Parameter(
                    "inner",
                    "内部滤波",
                    "choice",
                    "joint_bilateral",
                    choices=["joint_bilateral"],
                    is_advanced=True,
                ),
                Parameter(
                    "border", "边界策略", "choice", "reflect", choices=["reflect"], is_advanced=True
                ),
            ],
        ),
        OperatorItem(
            key="flow_line",
            name="沿轮廓方向提取线条",
            category="acg",
            category_label=CATEGORIES["acg"],
            description="构建 ETF 切线场并执行法向 DoG 与切向积分，输出线条或回混原图。",
            default_op="flow_line",
            default_params={
                "derivative_sigma": 1,
                "etf_radius": 3,
                "etf_iterations": 3,
                "sigma_normal": 1,
                "gaussian_ratio": 1.6,
                "sigma_tangent": 3,
                "tau": 0.99,
                "threshold": 0,
                "strength": 0.3,
                "output": "overlay",
                "border": "reflect",
            },
            parameters=[
                Parameter("strength", "混合强度", "float", 0.3, 0, 1),
                Parameter("output", "输出方式", "choice", "overlay", choices=["overlay", "lines"]),
                Parameter(
                    "derivative_sigma", "梯度尺度（像素）", "float", 1, 0.3, 4, is_advanced=True
                ),
                Parameter("etf_radius", "方向邻域（像素）", "int", 3, 1, 8, is_advanced=True),
                Parameter("etf_iterations", "方向迭代次数", "int", 3, 0, 8, is_advanced=True),
                Parameter("sigma_normal", "法向尺度（像素）", "float", 1, 0.3, 4, is_advanced=True),
                Parameter("gaussian_ratio", "高斯核宽比", "float", 1.6, 1.01, 3, is_advanced=True),
                Parameter(
                    "sigma_tangent", "切向尺度（像素）", "float", 3, 0.5, 12, is_advanced=True
                ),
                Parameter("tau", "差分权重", "float", 0.99, 0.8, 1.1, is_advanced=True),
                Parameter("threshold", "线条阈值", "float", 0, -1, 1, is_advanced=True),
                Parameter(
                    "border", "边界策略", "choice", "reflect", choices=["reflect"], is_advanced=True
                ),
            ],
        ),
        OperatorItem(
            key="kuwahara_anisotropic",
            name="方向性色块平滑",
            category="acg",
            category_label=CATEGORIES["acg"],
            description="用结构张量确定方向，对方向性扇区统计加权以平滑色块；可能改变细节形状。",
            default_op="kuwahara_anisotropic",
            default_params={"radius": 3, "sectors": 8, "strength": 1.0},
            parameters=[
                Parameter("radius", "邻域半径（输入像素）", "int", 3, 1, 16),
                Parameter("strength", "混合强度", "float", 1.0, 0, 1),
                Parameter(
                    "sectors", "扇区数量", "choice", 8, choices=[4, 8, 12, 16], is_advanced=True
                ),
            ],
        ),
        OperatorItem(
            key="bounded_sharpen",
            name="限制过冲的亮度锐化",
            category="anti_ai",
            category_label=CATEGORIES["anti_ai"],
            description="增强亮度代理残差并限制局部过冲，可跳过低幅残差；可能削弱真实高光。",
            default_op="bounded_sharpen",
            default_params={"amount": 1.0, "radius": 1.0, "noise_gate": 0.01, "overshoot": 0.0},
            parameters=[
                Parameter("amount", "锐化增益", "float", 1.0, 0, 4),
                Parameter("radius", "半径（输入像素）", "float", 1.0, 0.3, 32),
                Parameter(
                    "noise_gate",
                    "残差门限",
                    "float",
                    0.01,
                    0,
                    1,
                    description="归一化编码 sRGB 亮度代理单位",
                ),
                Parameter(
                    "overshoot",
                    "允许过冲",
                    "float",
                    0.0,
                    0,
                    1,
                    description="归一化编码 sRGB 亮度代理单位",
                ),
            ],
        ),
        OperatorItem(
            key="multiscale_detail",
            name="按尺度调整细节",
            category="photo",
            category_label=CATEGORIES["photo"],
            description="调整细、中、粗尺度亮度残差增益；1 为不变，增强也可能放大噪声。",
            default_op="multiscale_detail",
            default_params={"fine": 1.0, "medium": 1.0, "coarse": 1.0, "radius": 1.0},
            parameters=[
                Parameter("fine", "细尺度增益", "float", 1.0, 0, 4),
                Parameter("medium", "中尺度增益", "float", 1.0, 0, 4),
                Parameter("coarse", "粗尺度增益", "float", 1.0, 0, 4),
                Parameter("radius", "基础半径（输入像素）", "float", 1.0, 0.3, 32),
            ],
        ),
        OperatorItem(
            key="linear_resize",
            name="线性光透明边缘缩放",
            category="geometry",
            category_label=CATEGORIES["geometry"],
            description="在线性 sRGB 中预乘 Alpha 后缩放。倍率与尺寸互斥，目录默认倍率为 1。",
            default_op="linear_resize",
            default_params={"scale": 1.0, "filter": "lanczos"},
            parameters=[
                Parameter(
                    "scale", "缩放倍率", "float", 1.0, description="正有限数；指定尺寸时须移除倍率"
                ),
                Parameter(
                    "filter",
                    "重采样滤波",
                    "choice",
                    "lanczos",
                    choices=["nearest", "box", "bilinear", "bicubic", "lanczos"],
                ),
            ],
        ),
    ]
)

OPERATORS.extend(
    [
        OperatorItem(
            key="l0_smooth",
            name="L0 梯度平滑",
            category="anti_ai",
            category_label=CATEGORIES["anti_ai"],
            description="以半二次分裂减少非零梯度，使用 FFT 周期边界；可能合并细线与纹理。",
            default_op="l0_smooth",
            default_params={"smoothing": 0.02, "beta_growth": 2, "strength": 1},
            parameters=[
                Parameter(
                    "smoothing",
                    "平滑系数",
                    "float",
                    0.02,
                    max_value=1,
                    description="大于 0，不超过 1",
                ),
                Parameter("beta_growth", "惩罚增长倍率", "float", 2, 1.1, 4, is_advanced=True),
                Parameter("strength", "混合强度", "float", 1, 0, 1),
            ],
        ),
        OperatorItem(
            key="domain_transform",
            name="变换域递归平滑",
            category="anti_ai",
            category_label=CATEGORIES["anti_ai"],
            description="按固定原图色差构建变换域，执行全图双向递归扫描；可能削弱小结构。",
            default_op="domain_transform",
            default_params={"sigma_space": 8, "sigma_range": 0.15, "iterations": 3, "strength": 1},
            parameters=[
                Parameter(
                    "sigma_space",
                    "空间尺度（输入像素）",
                    "float",
                    8,
                    max_value=256,
                    description="大于 0，不超过 256",
                ),
                Parameter(
                    "sigma_range",
                    "色差尺度",
                    "float",
                    0.15,
                    max_value=1,
                    description="归一化编码 RGB 单位；大于 0，不超过 1",
                ),
                Parameter("iterations", "扫描轮数", "int", 3, 1, 8),
                Parameter("strength", "混合强度", "float", 1, 0, 1),
            ],
        ),
        OperatorItem(
            key="wls_smooth",
            name="加权最小二乘平滑",
            category="anti_ai",
            category_label=CATEGORIES["anti_ai"],
            description="用对数亮度代理梯度加权，求解 RGB 最小二乘平滑；未收敛时报错。",
            default_op="wls_smooth",
            default_params={"smoothing": 0.1, "alpha": 1.2, "strength": 1},
            parameters=[
                Parameter(
                    "smoothing",
                    "平滑系数",
                    "float",
                    0.1,
                    max_value=10,
                    description="大于 0，不超过 10",
                ),
                Parameter("alpha", "梯度权重指数", "float", 1.2, 0.1, 2),
                Parameter("strength", "混合强度", "float", 1, 0, 1),
            ],
        ),
        OperatorItem(
            key="rtv_smooth",
            name="相对全变分启发式平滑（近似）",
            category="anti_ai",
            category_label=CATEGORIES["anti_ai"],
            description="以 box 窗口变化权重执行 IRLS 近似平滑，不等同原论文完整 RTV 求解。",
            default_op="rtv_smooth",
            default_params={"smoothing": 0.01, "iterations": 4, "strength": 1},
            parameters=[
                Parameter(
                    "smoothing",
                    "平滑系数",
                    "float",
                    0.01,
                    max_value=1,
                    description="大于 0，不超过 1",
                ),
                Parameter("iterations", "IRLS 轮数", "int", 4, 1, 10),
                Parameter("strength", "混合强度", "float", 1, 0, 1),
            ],
        ),
        OperatorItem(
            key="periodic_notch",
            name="手动周期频率抑制",
            category="anti_ai",
            category_label=CATEGORIES["anti_ai"],
            description="手动指定周期频率并施加共轭 Gaussian 陷波，非自动检测；也会移除有意图案。",
            default_op="periodic_notch",
            default_params={"frequencies": "", "width": 0.01, "strength": 1.0},
            parameters=[
                Parameter(
                    "frequencies",
                    "频率坐标",
                    "str",
                    "",
                    description="fx,fy;fx,fy，cycles/pixel，各轴 [-0.5,0.5]，排除 0,0；空值不处理",
                ),
                Parameter("width", "频带宽度（cycles/pixel）", "float", 0.01, 0.0001, 0.25),
                Parameter("strength", "抑制强度", "float", 1.0, 0, 1),
            ],
        ),
        OperatorItem(
            key="chroma_transfer",
            name="参考图色度统计迁移",
            category="photo",
            category_label=CATEGORIES["photo"],
            description="在线性 RGB 对立色通道中迁移参考图统计；保留相对亮度非 Lab 感知明度。",
            default_op="chroma_transfer",
            default_params={"strength": 1.0, "luminance": "preserve"},
            parameters=[
                Parameter(
                    "reference",
                    "参考图片路径（必填）",
                    "str",
                    None,
                    description="必须提供可读取的参考图片路径",
                    required=True,
                ),
                Parameter("strength", "迁移强度", "float", 1.0, 0, 1),
                Parameter(
                    "luminance", "相对亮度处理", "choice", "preserve", choices=["preserve", "match"]
                ),
            ],
        ),
        OperatorItem(
            key="illumination_correct",
            name="平滑照明近似校正",
            category="photo",
            category_label=CATEGORIES["photo"],
            description="用 Gaussian 照明估计做线性 sRGB 除法校正，非 LIME；可能放大噪声。",
            default_op="illumination_correct",
            default_params={"radius": 16.0, "strength": 0.5, "floor": 0.05},
            parameters=[
                Parameter("radius", "照明平滑半径（像素）", "float", 16.0, 0.3, 128),
                Parameter("strength", "校正强度", "float", 0.5, 0, 1),
                Parameter(
                    "floor", "照明下限", "float", 0.05, 0.001, 1, description="线性 sRGB 归一化值"
                ),
            ],
        ),
    ]
)

OPERATORS.append(
    OperatorItem(
        key="weighted_median",
        name="按颜色相似度计算加权中值",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="以 RGB 相似度和覆盖率加权逐通道中值，减少脉冲和局部杂点；可能移除细线。",
        default_op="weighted_median",
        default_params={"radius": 2, "sigma_range": 0.15, "strength": 1.0},
        parameters=[
            Parameter("radius", "邻域半径（像素）", "int", 2, 1, 8),
            Parameter("sigma_range", "RGB 范围尺度", "float", 0.15, 0.001, 1),
            Parameter("strength", "混合强度", "float", 1.0, 0, 1),
        ],
    )
)

OPERATORS.append(
    OperatorItem(
        key="fast_guided_smooth",
        name="降采样估计引导平滑系数",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="在低分辨率估计线性系数，再由原尺寸亮度引导重建；可能遗漏细小结构。",
        default_op="fast_guided_smooth",
        default_params={"radius": 4, "epsilon": 0.001, "strength": 0.5, "subsample": 4},
        parameters=[
            Parameter("radius", "输入像素半径", "int", 4, 1, 128),
            Parameter("epsilon", "归一化引导值平方正则项", "float", 0.001, 0.000001, 1),
            Parameter("strength", "混合强度", "float", 0.5, 0, 1),
            Parameter("subsample", "系数降采样倍率", "int", 4, 1, 8),
        ],
    )
)

OPERATORS.append(
    OperatorItem(
        key="periodic_notch_auto",
        name="检测窄带周期峰并抑制",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="对去趋势加窗频谱检测窄峰并施加共轭陷波；无法区分有意网点与噪声。",
        default_op="periodic_notch_auto",
        default_params={
            "threshold": 8,
            "min_frequency": 0.06,
            "max_pairs": 3,
            "width": 0.01,
            "strength": 0.5,
        },
        parameters=[
            Parameter("threshold", "局部峰对比阈值", "float", 8, 2, 100),
            Parameter("min_frequency", "最低径向频率", "float", 0.06, 0.01, 0.45),
            Parameter("max_pairs", "最多频率对数", "int", 3, 1, 16),
            Parameter("width", "频带宽度", "float", 0.01, 0.0001, 0.25),
            Parameter("strength", "抑制强度", "float", 0.5, 0, 1),
        ],
    )
)

OPERATORS.append(
    OperatorItem(
        key="wls_detail",
        name="按保边尺度调整亮度细节",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="以固定引导 WLS 分解三个尺度的亮度残差；增强可能产生光晕，非 Local Laplacian。",
        default_op="wls_detail",
        default_params={
            "fine": 1,
            "medium": 1,
            "coarse": 1,
            "smoothing": 0.05,
            "scale_ratio": 4,
            "alpha": 1.2,
        },
        parameters=[
            Parameter("fine", "细尺度增益", "float", 1, 0, 4),
            Parameter("medium", "中尺度增益", "float", 1, 0, 4),
            Parameter("coarse", "粗尺度增益", "float", 1, 0, 4),
            Parameter("smoothing", "起始平滑权重", "float", 0.05, 0.0001, 1),
            Parameter("scale_ratio", "层间权重倍率", "float", 4, 1.1, 8),
            Parameter("alpha", "边缘权重指数", "float", 1.2, 0.1, 2),
        ],
    )
)

OPERATORS.append(
    OperatorItem(
        key="weighted_guided_smooth",
        name="按局部方差调节引导平滑",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="按三像素窗口方差自适应正则强度，平滑色块并减少跨边混合；仍可能损失细纹。",
        default_op="weighted_guided_smooth",
        default_params={"radius": 4, "epsilon": 0.001, "strength": 0.5},
        parameters=[
            Parameter("radius", "输入像素半径", "int", 4, 1, 128),
            Parameter("epsilon", "归一化引导值平方正则项", "float", 0.001, 0.000001, 1),
            Parameter("strength", "混合强度", "float", 0.5, 0, 1),
        ],
    )
)

OPERATORS.append(
    OperatorItem(
        key="local_laplacian_sampled",
        name="采样局部拉普拉斯细节重映射",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="在多个参考亮度上重映射并插值金字塔系数；采样近似可能产生光晕或色域裁切。",
        default_op="local_laplacian_sampled",
        default_params={"sigma": 0.2, "detail": 2, "samples": 8, "levels": 5, "strength": 0.5},
        parameters=[
            Parameter("sigma", "细节幅值阈值", "float", 0.2, 0.01, 1),
            Parameter("detail", "细节幂指数（大于 1 平滑）", "float", 2, 0.25, 4),
            Parameter("samples", "参考亮度采样数", "int", 8, 4, 32),
            Parameter("levels", "最多处理层数", "int", 5, 1, 8),
            Parameter("strength", "混合强度", "float", 0.5, 0, 1),
        ],
    )
)

# New recipes share the displayed defaults; historical recipes above stay unchanged.
_NEW_OPERATORS = (
    "local_laplacian_sampled",
    "weighted_guided_smooth",
    "wls_detail",
    "periodic_notch_auto",
    "fast_guided_smooth",
    "weighted_median",
    "guided_smooth",
    "rolling_guidance",
    "flow_line",
    "kuwahara_anisotropic",
    "bounded_sharpen",
    "multiscale_detail",
    "linear_resize",
    "l0_smooth",
    "domain_transform",
    "wls_smooth",
    "rtv_smooth",
    "periodic_notch",
    "chroma_transfer",
    "illumination_correct",
    "chroma_denoise",
    "edge_antialias",
    "cel_banding_reduce",
)
for _op in _NEW_OPERATORS:
    _item = next(item for item in OPERATORS if item.default_op == _op)
    if not any(parameter.required for parameter in _item.parameters):
        PRESET_PIPELINES[_op] = _item.build_pipeline()

OPERATORS_BY_OP: dict[str, OperatorItem] = {item.default_op: item for item in OPERATORS}


def get_operator_item(op: str) -> OperatorItem | None:
    return OPERATORS_BY_OP.get(op)


def category_label(category: str) -> str:
    """Display label; keys and raw CATEGORIES stay stable."""
    if not i18n.is_chinese():
        return operator_catalog_en.CATEGORY_LABELS.get(category, CATEGORIES.get(category, category))
    return CATEGORIES.get(category, category)


def localized_operator(item: OperatorItem) -> OperatorItem:
    """A copy with display text in the active language; identifiers are unchanged."""
    if i18n.is_chinese():
        return item
    name = operator_catalog_en.OPERATORS.get(item.default_op, {}).get("name", item.name)
    description = operator_catalog_en.OPERATORS.get(item.default_op, {}).get(
        "description", item.description
    )
    translated = operator_catalog_en.PARAMETERS.get(item.default_op, {})
    parameters = [
        replace(
            parameter,
            label=translated.get(parameter.name, {}).get("label", parameter.label),
            description=translated.get(parameter.name, {}).get(
                "description", parameter.description
            ),
        )
        for parameter in item.parameters
    ]
    return replace(
        item,
        name=name,
        description=description,
        category_label=category_label(item.category),
        parameters=parameters,
    )


def localized_intro(key: str) -> dict[str, str] | None:
    """Intro text for a catalog key in the active language."""
    base = INTROS.get(key)
    if base is None or i18n.is_chinese():
        return base
    english = operator_catalog_en.intro_en(key)
    return {**base, **english} if english else base


# -------------------------------------------------------------------------
# Comprehensive Introductions for Presets & Operators
# -------------------------------------------------------------------------

INTROS: dict[str, dict[str, str]] = {
    "local_laplacian_sampled": {
        "title": "采样局部拉普拉斯细节重映射",
        "principle": "按 Local Laplacian Filters（2011）式 1 的幂函数重映射细节，"
        "大幅边缘保持单位斜率；均匀采样参考亮度，对重映射金字塔系数线性插值。"
        "Gaussian sigma=1 降采样与双线性展开，不是原逐系数精确算法或作者加速实现。",
        "scenarios": "适用于跨尺度压低或增强小幅细节；编码亮度加性回混、隐藏 RGB 均值延拓，"
        "最终裁切 RGB，不能承诺无光晕或保色相。",
    },
    "weighted_guided_smooth": {
        "title": "按局部方差调节引导平滑",
        "principle": "按 Weighted Guided Image Filtering（2015）式 5–10，以 3×3 方差和"
        "全图逆方差均值计算边缘权重，正则项为 epsilon/weight。"
        "本项目以亮度引导 RGB，覆盖率加权、reflect 边界、sigma=1 Gaussian 权重平滑。",
        "scenarios": "适用于平滑色块与边界保护对照；不是加权中值，不能保证消除所有光晕。",
    },
    "wls_detail": {
        "title": "按保边尺度调整亮度细节",
        "principle": "使用原图 log-luma 固定权重进行三个不同正则强度的 WLS 求解，"
        "调整相邻层残差后重建。CG 容差 1e-6、最多 500 次，不收敛拒绝输出。",
        "scenarios": "适用于分尺度控制明暗细节；增益全 1 精确旁路，强增益仍可能损伤细线。",
    },
    "periodic_notch_auto": {
        "title": "检测窄带周期峰并抑制",
        "principle": "拟合亮度平面去趋势，Hann 加窗后按局部环带背景寻找共轭窄峰；"
        "无峰则不处理，检测频率写入执行记录。不是语义识别或论文同名算法。",
        "scenarios": "适用于检查已知周期污染；漫画网点、织物、建筑栅格也可能被删除，须对照原图。",
    },
    "fast_guided_smooth": {
        "title": "降采样估计引导平滑系数",
        "principle": "按 Fast Guided Filter（2015）Algorithm 2 在低分辨率计算系数，"
        "双线性上采样后应用于原尺寸引导。半径除以倍率取整且至少为 1，"
        "覆盖率加权为本项目变体；倍率 1 直接调用完整引导滤波。",
        "scenarios": "适用于大尺度平滑的速度对照；不保证与完整滤波相同，不自动替换旧配方。",
    },
    "weighted_median": {
        "title": "按颜色相似度计算加权中值",
        "principle": "按 CVPR 2014 WMF 论文式 2 累计排序权重达到一半后取值；"
        "本实现为 RGB Gaussian 权重的逐行排序参考变体，不含论文加速结构。",
        "scenarios": "适用于局部杂点与脉冲；窄范围会保留强离群点，逐通道取值也可能改变颜色组合。",
    },
    "rtv_smooth": {
        "title": "相对全变分启发式平滑",
        "summary": "以 box 窗口变化权重执行 IRLS 近似平滑。",
        "principle": "这是工程近似，不等同原论文完整 RTV 求解。",
        "scenarios": "适用于减少纹理；迭代不足或参数较强时可能损失边缘。",
    },
    "periodic_notch": {
        "title": "手动周期频率抑制",
        "summary": "手动指定周期频率并施加共轭 Gaussian 陷波，非自动检测。",
        "principle": "按 cycles/pixel 坐标构造频域共轭陷波，保留 DC。",
        "scenarios": "适用于已知周期伪影；也会移除所选频率上的有意图案。",
    },
    "illumination_correct": {
        "title": "平滑照明近似校正",
        "summary": "用 Gaussian 最大通道照明估计做线性 sRGB 除法校正，非 LIME。",
        "principle": "以覆盖率加权平滑照明并归一化，再按强度混合校正结果。",
        "scenarios": "适用于缓慢照明不均；可能抹平材质明暗并放大噪声。",
    },
    "l0_smooth": {
        "title": "L0 梯度平滑",
        "summary": "以半二次分裂减少非零梯度，使用 FFT 周期边界。",
        "principle": "在频域求解周期边界下的梯度稀疏化迭代。",
        "scenarios": "适用于减少细碎纹理；可能合并细线与纹理。",
    },
    "domain_transform": {
        "title": "变换域递归平滑",
        "summary": "按固定原图色差构建变换域，执行全图双向递归扫描。",
        "principle": "按空间与范围尺度控制变换域递归权重。",
        "scenarios": "适用于保边平滑；可能削弱小结构。",
    },
    "wls_smooth": {
        "title": "加权最小二乘平滑",
        "summary": "用对数亮度代理梯度加权，求解 RGB 最小二乘平滑。",
        "principle": "构造边缘权重并用共轭梯度求解；未收敛时报错。",
        "scenarios": "适用于分离缓慢结构与细节；大图求解可能耗时。",
    },
    "chroma_transfer": {
        "title": "参考图色度统计迁移",
        "summary": "在线性 RGB 对立色通道中迁移参考图统计，参考图路径必填。",
        "principle": "按 Alpha 加权均值和标准差匹配色度统计，可保留相对亮度。",
        "scenarios": "适用于参考图配色迁移；相对亮度不是 Lab 感知明度。",
    },
    "像素化": {
        "title": "像素化 (Pixelate)",
        "summary": ("本算子通过邻域块均值采样与重构，将连续图像离散化为规则的像素网格点阵。"),
        "principle": "基于最近邻下采样与块级像素重构算法，实现空间分辨率的阶跃式离散化。",
        "scenarios": "适用于复古像素风格艺术创作与局部敏感信息脱敏处理流程。",
    },
    "调色板": {
        "title": "调色板减色 (Color Palette)",
        "summary": ("本算子通过中值切分聚类提取代表性色彩空间，并利用误差扩散完成低位深着色。"),
        "principle": "基于中值切分色彩量化算法与 Floyd-Steinberg 空间邻域误差扩散卷积。",
        "scenarios": "适用于有限调色板限制的插画设计与复古显示介质效果模拟。",
    },
    "线稿": {
        "title": "手绘线稿提取 (Lineart)",
        "summary": ("本算子通过高通滤波与动态阈值反转，提取图像的高频轮廓结构并生成黑白线稿。"),
        "principle": ("基于高通空间卷积算子提取高频梯度，经由直方图自动色阶拉伸与二值反色映射。"),
        "scenarios": "适用于手绘线稿底图提取、结构线临摹与着色填色练习流程。",
    },
    "抖动": {
        "title": "有序抖动 (Ordered Dither)",
        "summary": "本算子通过预设误差矩阵离散化连续色阶，在有限色阶下重建视觉连续灰度。",
        "principle": (
            "基于 Atkinson 与 Floyd-Steinberg 空间误差扩散转移矩阵，将量化残差分配至邻域像元。"
        ),
        "scenarios": "适用于单色点阵印刷模拟、赛博终端视觉重构与复古介质渲染。",
    },
    "锐化": {
        "title": "反虚光锐化 (Unsharp Mask)",
        "summary": ("本算子通过分离并强化微观高频边缘分量，提升图像整体视觉反差与边缘聚焦度。"),
        "principle": (
            "基于经典高斯差分反虚光掩模（Unsharp Masking）数学模型，按比例叠加高频边缘残差。"
        ),
        "scenarios": "适用于补偿光学镜头软化、提高小尺寸图像的视觉微观辨识度。",
    },
    "赛璐璐": {
        "title": "赛璐璐分色 (Cel Shading)",
        "summary": ("本算子将连续光影照度离散化为分段阶梯分布，结合索贝尔算子生成硬边赛璐璐风格。"),
        "principle": "基于照度直方图区间阶梯量化与索贝尔空间一阶导数梯度轮廓提取。",
        "scenarios": "适用于写实影像向非真实感动漫插画的阶梯化风格转换。",
    },
    # --- 抗AI生成伪影专项 ---
    "去AI塑料感": {
        "title": "去AI塑料感 (Anti-AI Organic)",
        "summary": (
            "本预设管线复合表面保边去杂、非线性软肩色阶与微量光晕，"
            "抑制生成模型的油腻塑料质感与微观杂色。"
        ),
        "principle": (
            "基于色差引导高斯保边表面平滑消除浮动色斑，结合高光软肩滚降与极高光微漫射恢复自然光影。"
        ),
        "scenarios": ("适用于消除扩散模型生成画面的蜡质油光与不自然高光，呈现干净通透质感。"),
    },
    "抗AI生成伪影综合治理": {
        "title": "抗AI伪影综合 (Anti-AI Artifacts)",
        "summary": (
            "本预设管线结合频域陷波滤波、反边缘振铃、表面去杂平滑与胶片色阶，多维度消除生成伪影。"
        ),
        "principle": (
            "复合 2D-FFT 频域陷波滤除转置卷积棋盘格，结合形态学反振铃与表面保边平滑消除结构伪影。"
        ),
        "scenarios": ("适用于高画质 AI 生成作品的全流程伪影净化，还原平滑纯净的画面层次。"),
    },
    "二次元平涂去杂": {
        "title": "二次元平涂去杂 (Anime Cel Smoothing)",
        "summary": (
            "本预设管线专为二次元插画平涂设计，消除AI在色块内部生成的细碎杂色斑驳与暗部脏色。"
        ),
        "principle": (
            "基于色差引导表面模糊平滑色块内部微小杂斑，结合暗部照度掩模"
            "衰减阴影杂乱饱和度以保全线条锐度。"
        ),
        "scenarios": ("适用于二次元动漫角色、日系赛璐珞立绘及平涂背景消除AI杂色噪点。"),
    },
    "动漫色彩提纯": {
        "title": "动漫色彩提纯 (Anime Color Purity)",
        "summary": (
            "本预设管线衰减暗部多余杂乱反光饱和度，提纯明亮主体未饱和色彩，"
            "重构二次元画作的清爽通透感。"
        ),
        "principle": (
            "基于非线性照度掩模对阴影暗部实施选择性去饱和，并结合色差权重"
            "对明部主体进行自然饱和度微调增益。"
        ),
        "scenarios": ("适用于解决AI生图在阴影深处泛紫泛红等脏色问题，恢复二次元纯净冷暖对比。"),
    },
    "anti_checkerboard": {
        "title": "网格去混叠 (Anti-Checkerboard)",
        "summary": (
            "本算子通过二维离散傅里叶变换与高频陷波掩模，衰减潜空间转置卷"
            "积引入的周期性棋盘格网格尖峰。"
        ),
        "principle": (
            "基于 Frank et al. (ICML 2020) 与 Odena et al. (2016) 理论，"
            "在频域对齐奈奎斯特轴向高频尖峰并平滑衰减。"
        ),
        "scenarios": (
            "适用于消除扩散模型与 GAN 在反卷积上采样阶段引入的微观高频栅格与周期性棋盘格伪影。"
        ),
    },
    "subsurface_scattering": {
        "title": "肤色区域柔化 (Skin Softening)",
        "summary": "分别模糊 RGB 通道并混合原图，可用肤色掩模限制作用区域。",
        "principle": "采用不同通道半径的高斯模糊与肤色权重混合，属于图像柔化效果。",
        "scenarios": "适用于人像或插画的局部柔化；可能抹去细节，肤色掩模也可能误选。",
    },
    "de_haloing": {
        "title": "去边缘白边光晕 (De-Haloing)",
        "summary": (
            "本算子通过局部中值滤波与极值形态学夹紧约束，抑制高反差边缘处"
            "由采样器过度锐化引入的振铃白边与暗光晕伪影。"
        ),
        "principle": (
            "基于 Trentacoste et al. (2011) 局部中值与极值形态学包络，"
            "对边缘梯度过冲像素执行非对称软截断收敛。"
        ),
        "scenarios": (
            "适用于消除高 Guidance 采样与强锐化滤波器在角色轮廓、发丝及高"
            "对比边缘处产生的白色震荡光晕。"
        ),
    },
    "film_grain": {
        "title": "胶片微粒 (Film Grain)",
        "summary": (
            "本算子通过照度加权的微观噪点合成，模拟传统感光胶片与数字传感器在弱光下的光子散粒噪声。"
        ),
        "principle": (
            "基于泊松-高斯（Poisson-Gaussian）噪声分布模型，依反照度非线性加权高斯随机扰动。"
        ),
        "scenarios": "适用于打破 AI 图像中过于平滑均一的合成表面，建立微观物理纹理基底。",
    },
    "de_ai_bloom": {
        "title": "柔和漫射光晕 (Diffuse Bloom)",
        "summary": (
            "本算子通过照度阈值分割与多尺度高斯漫射核，在保全暗部轮廓的同时模拟镜头微光晕溢出。"
        ),
        "principle": ("基于高光照度非线性加权截断提取，并应用暗部梯度掩模保全线稿边缘的完整性。"),
        "scenarios": ("适用于软化刺眼生硬的 AI 高光边缘，营造大光比场景下的自然光学漫射环境。"),
    },
    "filmic_tone": {
        "title": "胶片色调 (Filmic Tone)",
        "summary": "使用幂函数 LUT 调整黑白端点，并调节饱和度与冷暖色偏。",
        "principle": "在编码 RGB 上应用查找表映射，不恢复已经截断的高光或暗部。",
        "scenarios": "适用于复古调色；抬黑与压白会改变对比度和可用色阶。",
    },
    "chromatic_aberration": {
        "title": "镜头边缘色差 (Chromatic Aberration)",
        "summary": (
            "本算子通过红蓝通道径向空间离散错位，模拟透镜材料折射率随光波长变化引起的横向色散现象。"
        ),
        "principle": (
            "基于光学透镜色散物理模型，以视口几何中心为极点对色通道执行双线性径向重采样缩放。"
        ),
        "scenarios": (
            "适用于打破完美计算机成像的几何死板感，赋予画面真实广角光学镜头的边缘成像物理特征。"
        ),
    },
    # --- 电影级与色彩空间变换 ---
    "ACES电影级色调": {
        "title": "ACES电影色调 (ACES Filmic)",
        "summary": (
            "本算子采用影视工业标准学院色彩编码系统，将高动态场景辐射度精确映射至标准显示色彩空间。"
        ),
        "principle": (
            "基于 Krzysztof Narkowicz 与 Stephen Hill (SIGGRAPH 2015) 的 ACES S曲线有理多项式拟合。"
        ),
        "scenarios": (
            "适用于大光比场景、复杂逆光与发光体渲染，实现平滑的高光肩部滚降与渐进暗部过渡。"
        ),
    },
    "aces_tonemap": {
        "title": "ACES电影色调 (ACES Filmic)",
        "summary": (
            "本算子采用影视工业标准学院色彩编码系统，将高动态场景辐射度精确映射至标准显示色彩空间。"
        ),
        "principle": (
            "基于 Krzysztof Narkowicz 与 Stephen Hill (SIGGRAPH 2015) 的 ACES S曲线有理多项式拟合。"
        ),
        "scenarios": (
            "适用于大光比场景、复杂逆光与发光体渲染，实现平滑的高光肩部滚降与渐进暗部过渡。"
        ),
    },
    "引导滤波细节增强": {
        "title": "细节增强 (Guided Filter)",
        "summary": (
            "本算子利用引导图像的结构先验实施保边平滑与高频残差倍增，实现无振铃效应的细节增强。"
        ),
        "principle": (
            "基于 He et al. (ECCV 2010 / IEEE TPAMI 2013) 局部线性平移不"
            "变模型，精准分离与强化微观高频纹理。"
        ),
        "scenarios": ("适用于发丝细节、皮肤微孔、衣物布料纹理的真实感恢复，消除过度涂抹平滑感。"),
    },
    "guided_filter": {
        "title": "细节增强 (Guided Filter)",
        "summary": (
            "本算子利用引导图像的结构先验实施保边平滑与高频残差倍增，实现无振铃效应的细节增强。"
        ),
        "principle": (
            "基于 He et al. (ECCV 2010 / IEEE TPAMI 2013) 局部线性平移不"
            "变模型，精准分离与强化微观高频纹理。"
        ),
        "scenarios": ("适用于发丝细节、皮肤微孔、衣物布料纹理的真实感恢复，消除过度涂抹平滑感。"),
    },
    "桑原二次元平涂": {
        "title": "动漫平涂滤波 (Kuwahara Filter)",
        "summary": (
            "本算子在保全主要结构轮廓锐利度的同时平滑色块内部细节，生成纯净的平面手绘插画质感。"
        ),
        "principle": (
            "基于 Papari et al. (IEEE TIP 2007) 四象限局部方差最小化判决"
            "准则，执行保边各向异性平滑。"
        ),
        "scenarios": "适用于二次元插画背景平涂、厚涂笔触离散化以及去除高频噪点伪影。",
    },
    "kuwahara": {
        "title": "动漫平涂滤波 (Kuwahara Filter)",
        "summary": (
            "本算子在保全主要结构轮廓锐利度的同时平滑色块内部细节，生成纯净的平面手绘插画质感。"
        ),
        "principle": (
            "基于 Papari et al. (IEEE TIP 2007) 四象限局部方差最小化判决"
            "准则，执行保边各向异性平滑。"
        ),
        "scenarios": "适用于二次元插画背景平涂、厚涂笔触离散化以及去除高频噪点伪影。",
    },
    "剧场版宽银幕眩光": {
        "title": "宽银幕眩光 (Anamorphic Flare)",
        "summary": (
            "本算子通过高光阈值提取与横向一维卷积核扩展，模拟好莱坞变形宽银幕镜头的水平光斑条纹。"
        ),
        "principle": (
            "基于横向非对称长条高斯卷积核与阈值能量提取，沿水平轴向生成具有特定波长着色的衍射光斑。"
        ),
        "scenarios": ("适用于科幻场景、强逆光人像、夜景光源与舞台灯光下的电影宽银幕视觉重构。"),
    },
    "anamorphic_flare": {
        "title": "宽银幕眩光 (Anamorphic Flare)",
        "summary": (
            "本算子通过高光阈值提取与横向一维卷积核扩展，模拟好莱坞变形宽银幕镜头的水平光斑条纹。"
        ),
        "principle": (
            "基于横向非对称长条高斯卷积核与阈值能量提取，沿水平轴向生成具有特定波长着色的衍射光斑。"
        ),
        "scenarios": ("适用于科幻场景、强逆光人像、夜景光源与舞台灯光下的电影宽银幕视觉重构。"),
    },
    "日系晴空色彩迁移": {
        "title": "色彩风格迁移 (Color Transfer)",
        "summary": (
            "本算子通过在正交去相关色彩空间中对齐均值与方差统计量，实现参考图像色彩风格的全局迁移。"
        ),
        "principle": (
            "基于 Reinhard et al. (IEEE CG&A 2001) 算法，在 CIE-Lab 色彩"
            "空间执行一阶矩与二阶矩的线性映射转换。"
        ),
        "scenarios": ("适用于调整阴郁灰暗图像的整体色调，快速赋予其晴朗通透的明快色调氛围。"),
    },
    "color_transfer": {
        "title": "色彩风格迁移 (Color Transfer)",
        "summary": (
            "本算子通过在正交去相关色彩空间中对齐均值与方差统计量，实现参考图像色彩风格的全局迁移。"
        ),
        "principle": (
            "基于 Reinhard et al. (IEEE CG&A 2001) 算法，在 CIE-Lab 色彩"
            "空间执行一阶矩与二阶矩的线性映射转换。"
        ),
        "scenarios": ("适用于调整阴郁灰暗图像的整体色调，快速赋予其晴朗通透的明快色调氛围。"),
    },
    "日系通透空气感": {
        "title": "暖色柔光与暗角",
        "summary": "依次调整 RGB 冷暖色偏、叠加高光柔光并压暗边缘。",
        "principle": "组合通道 LUT、高光模糊混合与径向暗角掩模。",
        "scenarios": "适用于插画和风景的氛围调色；可能降低局部对比度。",
    },
    "热血集中线": {
        "title": "漫画集中线 (Focus Rays)",
        "summary": (
            "本算子在极坐标系下沿角向生成离散高反差射线，建立指向视觉兴趣中心的动态放射构图。"
        ),
        "principle": (
            "基于极坐标逆映射与离散角向随机游走算法，留出中心视场椭球保留区并向外发散射线。"
        ),
        "scenarios": "适用于强调动作瞬间、视觉中心引导与夸张冲击力构图表达。",
    },
    "focus_lines": {
        "title": "漫画集中线 (Focus Rays)",
        "summary": (
            "本算子在极坐标系下沿角向生成离散高反差射线，建立指向视觉兴趣中心的动态放射构图。"
        ),
        "principle": (
            "基于极坐标逆映射与离散角向随机游走算法，留出中心视场椭球保留区并向外发散射线。"
        ),
        "scenarios": "适用于强调动作瞬间、视觉中心引导与夸张冲击力构图表达。",
    },
    "黑白漫画网点": {
        "title": "漫画网点 (Manga Screentone)",
        "summary": (
            "本算子通过局部照度调制固定角度与密度的网格圆点面积，模拟实体黑白漫画与印刷光栅工艺。"
        ),
        "principle": (
            "基于 45 度正交网点旋转矩阵与空间亮度能量卷积量化，精确再现传"
            "统调频/调幅印刷网点纸特性。"
        ),
        "scenarios": "适用于黑白漫画出版、同人印刷品预览与报纸版画非真实感风格化。",
    },
    "screentone": {
        "title": "漫画网点 (Manga Screentone)",
        "summary": (
            "本算子通过局部照度调制固定角度与密度的网格圆点面积，模拟实体黑白漫画与印刷光栅工艺。"
        ),
        "principle": (
            "基于 45 度正交网点旋转矩阵与空间亮度能量卷积量化，精确再现传"
            "统调频/调幅印刷网点纸特性。"
        ),
        "scenarios": "适用于黑白漫画出版、同人印刷品预览与报纸版画非真实感风格化。",
    },
    "立绘贴纸白边": {
        "title": "贴纸白边 (Sticker Outline)",
        "summary": (
            "本算子通过 Alpha 透明通道的形态学欧氏距离场膨胀与阴影卷积，生成立绘裁切轮廓边框。"
        ),
        "principle": (
            "基于二值形态学膨胀算子与高斯环境遮蔽投影合成，构建高精度各向同性轮廓包络层。"
        ),
        "scenarios": "适用于透明背景立绘衍生品预览、贴纸裁切设计与图形界面标识突出。",
    },
    "sticker_outline": {
        "title": "贴纸白边 (Sticker Outline)",
        "summary": (
            "本算子通过 Alpha 透明通道的形态学欧氏距离场膨胀与阴影卷积，生成立绘裁切轮廓边框。"
        ),
        "principle": (
            "基于二值形态学膨胀算子与高斯环境遮蔽投影合成，构建高精度各向同性轮廓包络层。"
        ),
        "scenarios": "适用于透明背景立绘衍生品预览、贴纸裁切设计与图形界面标识突出。",
    },
    "XDoG动漫线稿": {
        "title": "XDoG动漫线稿 (XDoG Lineart)",
        "summary": (
            "本算子通过不同方差高斯核相减与非线性阈值映射，提取具有手绘铅笔质感的连续边缘线稿。"
        ),
        "principle": (
            "基于 Winnemöller et al. (2012) XDoG 算子，结合双尺度高斯差分"
            "与非线性阶跃响应函数提取暗部线段。"
        ),
        "scenarios": "适用于从复杂色彩画面中提取纯净手绘级线稿结构与填色底稿制作。",
    },
    "lineart_extract": {
        "title": "XDoG动漫线稿 (XDoG Lineart)",
        "summary": (
            "本算子通过不同方差高斯核相减与非线性阈值映射，提取具有手绘铅笔质感的连续边缘线稿。"
        ),
        "principle": (
            "基于 Winnemöller et al. (2012) XDoG 算子，结合双尺度高斯差分"
            "与非线性阶跃响应函数提取暗部线段。"
        ),
        "scenarios": "适用于从复杂色彩画面中提取纯净手绘级线稿结构与填色底稿制作。",
    },
    "复古90年代胶片": {
        "title": "90年代复古胶片 (90s Film)",
        "summary": "组合 RGB LUT 色调、径向暗角和随机颗粒。",
        "principle": "先做通道查找表映射，再应用边缘掩模与噪点合成。",
        "scenarios": "适用于复古风格；会改变色阶并增加颗粒。",
    },
    "动漫超分辨率 4x": {
        "title": "动漫超分 4x (Anime SR)",
        "summary": (
            "本算子采用专精动漫图像的深度神经网络，以 4 倍放大比例无损重构清晰边缘与纯净色块。"
        ),
        "principle": (
            "基于 Real-ESRGAN 残差密集特征网络推理，在潜空间抑制高频振铃并平滑各向异性边缘。"
        ),
        "scenarios": "适用于动漫截图高清化、二次元插画大尺寸输出与印刷级高分辨率重构。",
    },
    "通用超分辨率 4x": {
        "title": "通用超分 4x (General SR)",
        "summary": (
            "本算子采用高保真深度生成网络，以 4 倍比例重构自然图像的高频微观纹理与结构细节。"
        ),
        "principle": (
            "基于 Real-ESRGAN 通用模型推理，利用深层残差连接与感知损失重建真实感自然图像高频分量。"
        ),
        "scenarios": "适用于真实摄影照片、复杂写实人像与宏大风景图像的高清分辨率重建。",
    },
    "日系治愈空气柔光": {
        "title": "扩散柔光 (Diffuse Glow)",
        "summary": (
            "本预设管线结合高光多尺度滤色扩散、自然饱和度保护与线稿自适应"
            "染色，营造柔美清澈的视觉调性。"
        ),
        "principle": ("复合亮度阈值高斯扩散、HSV 肤色饱和度保护增益以及邻域低频基底线稿融合计算。"),
        "scenarios": "适用于插画人物特写、顺光/逆光风景原画以及需要柔和光感渲染的场景。",
    },
    "大魄力动感鱼眼": {
        "title": "鱼眼广角透视 (Fisheye Lens)",
        "summary": (
            "本预设管线结合 Brown-Conrady 径向透镜畸变与极坐标聚焦射线，"
            "构建强烈的广角动感透视构图。"
        ),
        "principle": (
            "基于径向几何反向映射与极坐标多项式变形模型，协同生成视场向外凸起的球面透视冲击力。"
        ),
        "scenarios": "适用于动作特写镜头、运动俯冲视角以及具有极度张力的视觉构图表现。",
    },
    "橙青影视色彩平衡": {
        "title": "橙青影视平衡 (Teal & Orange)",
        "summary": (
            "本预设管线采用经典影视冷暖互补调色理论，建立暗部偏青、高光偏橙金的电影级色彩对冲关系。"
        ),
        "principle": ("复合三段式影调亮度权重矩阵、胶片高光滚降映射曲线与微观感光颗粒噪声。"),
        "scenarios": "适用于城市夜景、霓虹街景、赛博朋克风格渲染与电影感人像调色。",
    },
    "line_color": {
        "title": "线稿自适应染色 (Line Color)",
        "summary": (
            "本算子通过提取暗部边缘轮廓并依据邻域低频色彩进行平滑加权染色，消除生硬的死黑线条。"
        ),
        "principle": (
            "基于照度掩模非线性加权与大核邻域基底色彩空间插值，将暗部边缘向局部环境色彩平滑过渡。"
        ),
        "scenarios": ("适用于二次元线稿融入背景、软化 AI 容易产生的死硬黑边线条并统一画面色调。"),
    },
    "color_balance": {
        "title": "三段色彩平衡 (Color Balance)",
        "summary": (
            "本算子通过亮度区间平滑权重分割，独立调节阴影、中间调与高光区域的三通道色彩偏移量。"
        ),
        "principle": (
            "基于照度区间高斯平滑基函数分离影调，实施红绿蓝三通道加性色彩向量的精准受控位移。"
        ),
        "scenarios": "适用于精细校正画面色偏、构建特定影视调色风格与冷暖色调对冲布局。",
    },
    "fisheye": {
        "title": "鱼眼广角透视 (Fisheye Lens)",
        "summary": (
            "本算子通过高阶多项式坐标逆映射，模拟真实超广角与鱼眼镜头产生的桶形或枕形几何畸变。"
        ),
        "principle": (
            "基于 Brown-Conrady 径向几何畸变多项式方程，执行亚像素双线性插值坐标重采样。"
        ),
        "scenarios": "适用于模拟超广角与鱼眼镜头透视、夸张画面纵深与边缘视场拉伸效果。",
    },
    "vibrance": {
        "title": "自然饱和度 (Vibrance)",
        "summary": (
            "本算子依据像素现有饱和度反向加权增益并保护暖色皮肤，非线性提"
            "升画面灰暗平淡区域的色彩鲜活度。"
        ),
        "principle": (
            "基于感知反饱和度权重衰减模型与 HSV 色彩空间肤色保护遮罩，避"
            "免高饱和度与肤色区域色彩溢出。"
        ),
        "scenarios": (
            "适用于风景画面生动化、人像气色提亮以及需要提升鲜艳度且防止色彩断阶的调色场景。"
        ),
    },
    "diffuse_glow": {
        "title": "扩散柔光 (Diffuse Glow)",
        "summary": (
            "本算子通过亮度阈值分割与多尺度高斯核滤色混合，模拟强光源在光学介质中的漫反射光晕现象。"
        ),
        "principle": ("基于高光照度截断阈值提取、大核空间滤波与滤色（Screen）通道线性叠加算法。"),
        "scenarios": ("适用于增强发光体漫射、营造朦胧梦幻氛围与消除边缘生硬的数字化锯齿反差。"),
    },
    "vignette": {
        "title": "边缘暗角 (Vignette)",
        "summary": "按径向距离生成渐变掩模，压暗图像边缘。",
        "principle": "中心范围、羽化宽度和暗度共同控制径向亮度权重。",
        "scenarios": "适用于突出画面中心；边缘主体也会随掩模变暗。",
    },
    "color_temperature": {
        "title": "冷暖与色偏 (Color Temperature)",
        "summary": "使用 RGB 通道 LUT 调节冷暖与绿品红色偏。",
        "principle": "分别映射通道增益，不估计光源光谱或开尔文色温。",
        "scenarios": "适用于主观调色；强调整可能造成通道裁切。",
    },
    "superres": {
        "title": "神经超分辨率 (Super-Resolution)",
        "summary": (
            "本算子采用轻量化 ONNX 深度残差卷积神经网络，实现 4 倍分辨率下的结构保持超分重建。"
        ),
        "principle": (
            "基于 Real-ESRGAN 残差密集块（Residual-in-Residual Dense Bloc"
            "k）前向推理，在切片瓦片上执行拼接无缝重构。"
        ),
        "scenarios": "适用于低清晰度图像高清放大、微观纹理恢复与印刷级输出预处理。",
    },
    "二次元线条强化与描墨": {
        "title": "二次元线条强化与描墨 (Anime Line Inking)",
        "summary": "使用各向同性 DoG 与方形闭运算提取暗线，再压暗原图。",
        "principle": "依次计算高斯差分、闭运算、百分位归一化和乘法混合。",
        "scenarios": "适用于加深暗部轮廓；可能连接相邻线条，需检查文字与交叉点。",
    },
    "二次元赛璐珞顶级精修": {
        "title": "色块、线条与阴影调整",
        "summary": "依次平滑色块、加深暗线并调整暗部饱和度。",
        "principle": "组合 surface_blur、各向同性 DoG 暗线增强与 color_purity。",
        "scenarios": "适用于平涂插画风格调整；可能丢失细线与改变阴影配色。",
    },
    "anime_line_enhance": {
        "title": "动漫线条增强 (Line Inking)",
        "summary": "使用各向同性 DoG 与方形形态学闭运算提取暗线，再乘法压暗原图。",
        "principle": "对 RGB 图像执行方形邻域闭运算和百分位归一化，得到压暗权重。",
        "scenarios": "适用于加深暗部轮廓；可能连接邻近线条并压暗细节。",
    },
    "ACG 赛璐璐轻修复 (ACG Cel Cleanup)": {
        "title": "色噪、色带与平涂轻修复",
        "summary": "依次清理色度噪点、轻平滑平涂区域并打散渐变色带。",
        "principle": "只对 Cb/Cr 做中值滤波，再按色差平滑并加入固定有序抖动。",
        "scenarios": "适用于轻量整理赛璐璐画面；可能平均掉细小彩色细节。",
        "use_case": "轻量清理色噪、色带和细碎杂色",
        "benefit": "平涂更干净，渐变色带减轻",
        "loss": "细小彩色细节和低对比线条可能被平均",
    },
    "ACG 压缩截图修复 (ACG Compressed Still)": {
        "title": "压缩截图彩噪与锯齿修复",
        "summary": "依次清理色度噪点、压低白边光晕并在边缘做轻度抗锯齿。",
        "principle": "色度中值滤波、局部中值钳制与边缘掩模高斯柔化组合。",
        "scenarios": "适用于压缩动漫截图；边缘可能变软，文字需放大检查。",
        "use_case": "修复压缩动漫截图的彩噪、白边和锯齿",
        "benefit": "减轻彩噪、白边和阶梯锯齿",
        "loss": "边缘和文字可能变软，细节可能被抹平",
    },
}


for _op, (_principle, _scenarios) in {
    "guided_smooth": (
        "在编码 sRGB 中做局部线性回归；epsilon 为归一化引导值的平方单位。",
        "适用于平涂色块平滑；低对比细线可能被减弱。",
    ),
    "rolling_guidance": (
        "初始尺度抑制后迭代联合双边引导；不是普通双边滤波的重复。",
        "适用于减少细碎纹理；小文字与排线也可能被抹去。",
    ),
    "flow_line": (
        "ETF 平滑切线场，在法向计算 DoG 并沿切向积分；lines 输出独立黑线白底图。",
        "适用于提取或加深轮廓；需检查交叉点、细线与线条末端。",
    ),
    "kuwahara_anisotropic": (
        "由结构张量确定椭圆方向与伸长率，按扇区均值和方差加权。",
        "适用于方向性色块抽象；可能改变小结构的形状。",
    ),
    "bounded_sharpen": (
        "对编码 sRGB 亮度代理高频残差加噪声门限，并约束局部范围过冲。",
        "适用于受控锐化；可能削弱真实高光或放大纹理。",
    ),
    "multiscale_detail": (
        "分解亮度代理的多尺度高斯残差，各尺度增益为 1 时不改变该层。",
        "适用于分尺度对比度调整；增强高频也可能放大噪声。",
    ),
    "linear_resize": (
        "解码到线性 sRGB，预乘 Alpha 后浮点重采样，再解预乘并编码。",
        "适用于透明边缘缩放；倍率与尺寸互斥，非学习型细节恢复。",
    ),
    "chroma_denoise": (
        "分离 YCbCr 后只对 Cb/Cr 做中值滤波，再合并回 RGB。",
        "适用于压缩动漫截图的彩色噪点；细小彩色细节也会被平均。",
    ),
    "edge_antialias": (
        "FIND_EDGES 掩模膨胀模糊后，只在边缘附近混合高斯模糊结果。",
        "适用于减轻阶梯锯齿；边缘可能略软，文字与极细线条需检查。",
    ),
    "cel_banding_reduce": (
        "按局部亮度跨度识别渐变，用固定 Bayer 矩阵加入有序抖动。",
        "适用于赛璐璐渐变色带；平坦区域会出现轻微颗粒。",
    ),
}.items():
    INTROS[_op] = {
        "title": OPERATORS_BY_OP[_op].name,
        "principle": _principle,
        "scenarios": _scenarios,
    }
for _op in _NEW_OPERATORS:
    INTROS[_op]["summary"] = OPERATORS_BY_OP[_op].description
    INTROS[_op]["principle"] += " 实验性实现，尚未完成真实图像质量验收。"


PRESET_CATEGORIES: dict[str, str] = {
    "像素化": "风格光学",
    "调色板": "风格光学",
    "线稿": "线条与色块",
    "抖动": "风格光学",
    "锐化": "风格光学",
    "赛璐璐": "线条与色块",
    "去AI塑料感": "平滑与缺陷",
    "抗AI生成伪影综合治理": "平滑与缺陷",
    "二次元平涂去杂": "平滑与缺陷",
    "动漫色彩提纯": "线条与色块",
    "二次元线条强化与描墨": "线条与色块",
    "二次元赛璐珞顶级精修": "线条与色块",
    "ACG 赛璐璐轻修复": "线条与色块",
    "ACG 压缩截图修复": "线条与色块",
    "动漫线条增强": "线条与色块",
    "anime_line_enhance": "线条与色块",
    "日系通透空气感": "线条与色块",
    "热血集中线": "线条与色块",
    "黑白漫画网点": "线条与色块",
    "立绘贴纸白边": "风格光学",
    "XDoG动漫线稿": "线条与色块",
    "XDoG动漫手绘线稿": "线条与色块",
    "像素复古风": "风格光学",
    "赛璐璐手绘": "线条与色块",
    "ACES电影级色调": "颜色明暗",
    "引导滤波细节增强": "风格光学",
    "桑原二次元平涂": "线条与色块",
    "剧场版宽银幕眩光": "线条与色块",
    "日系晴空色彩迁移": "颜色明暗",
    "复古90年代胶片": "颜色明暗",
    "动漫超分辨率 4x": "模型恢复",
    "通用超分辨率 4x": "模型恢复",
    "日系治愈空气柔光": "线条与色块",
    "大魄力动感鱼眼": "线条与色块",
    "橙青影视色彩平衡": "颜色明暗",
}

PRESET_CATEGORIES.update({op: OPERATORS_BY_OP[op].category_label for op in _NEW_OPERATORS})
PRESET_CATEGORIES.update(
    {
        name: PRESET_CATEGORIES[name.split(" (", 1)[0]]
        for name in PRESET_PIPELINES
        if name not in PRESET_CATEGORIES
    }
)

CATEGORIES_LIST: list[str] = [
    "全部",
    "平滑与缺陷",
    "线条与色块",
    "颜色明暗",
    "风格光学",
    "模型恢复",
    "尺寸几何",
]


_BROWSE_NAMES = {
    "XDoG动漫手绘线稿 (Line Art)": "高斯差分线稿提取",
    "去AI塑料感 (Anti-AI Organic)": "局部平滑与高光扩散",
    "抗AI生成伪影综合治理 (Anti-AI Artifacts Suppression)": "高频衰减与局部平滑",
    "二次元平涂去杂 (Anime Cel Surface Smoothing)": "局部平滑与暗部降饱和",
    "ACG 赛璐璐轻修复 (ACG Cel Cleanup)": "色噪、色带与平涂轻修复",
    "ACG 压缩截图修复 (ACG Compressed Still)": "压缩截图彩噪与锯齿修复",
    "动漫色彩提纯 (Anime Color Purity)": "暗部降饱和与色差增益",
    "二次元线条强化与描墨 (Anime Line Inking)": "暗线提取与加深",
    "二次元赛璐珞顶级精修 (Anime Cel Master)": "局部平滑、暗线加深与降饱和",
    "日系通透空气感 (Shinkai Airy)": "通道调色、高光扩散与边缘压暗",
    "热血集中线 (Manga Action Focus)": "叠加径向线条",
    "黑白漫画网点 (Manga Screentone)": "亮度转网点",
    "立绘贴纸白边 (Die-cut Sticker)": "透明轮廓扩边与投影",
    "复古90年代胶片 (Retro 90s Film)": "色阶压缩、边缘压暗与噪声叠加",
    "动漫超分辨率 4x (Anime SR)": "插画模型四倍放大",
    "通用超分辨率 4x (General SR)": "通用模型四倍放大",
    "日系治愈空气柔光 (Anime Soft Glow)": "高光扩散、饱和度与暗线颜色调整",
    "大魄力动感鱼眼 (Anime Fisheye)": "径向畸变与线条叠加",
    "橙青影视色彩平衡 (Teal & Orange)": "分区调色、色阶压缩与噪声叠加",
    "赛璐璐": "色阶量化与轮廓叠加",
    "l0_smooth": "稀疏梯度平滑",
}


def browse_entries(*, include_aliases: bool = False) -> list[dict[str, str]]:
    """Human-facing recipes; every preset key stays valid for execution.

    A single-operator preset is not a recipe: it is that operator with tuned
    parameters, so it is documented on the operator page instead of listed here.
    """
    entries = []
    pipelines: list[list[dict[str, Any]]] = []
    for key, pipeline in PRESET_PIPELINES.items():
        if pipeline in pipelines:
            continue
        pipelines.append(pipeline)
        if not include_aliases and len(pipeline) == 1:
            continue
        item = get_operator_item(key)
        if not i18n.is_chinese():
            name = operator_catalog_en.PRESET_NAMES.get(
                key
            ) or operator_catalog_en.PRESET_NAMES.get(key.split(" (", 1)[0])
            if name is None and item is not None:
                name = localized_operator(item).name
            name = name or key
            group = (
                operator_catalog_en.GROUPS["综合配方"]
                if len(pipeline) > 1
                else _CATEGORY_LABELS_EN.get(PRESET_CATEGORIES[key], PRESET_CATEGORIES[key])
            )
        else:
            name = _BROWSE_NAMES.get(key, (item.name if item else key).split(" (", 1)[0]).replace(
                "4x", "4倍"
            )
            group = "综合配方" if len(pipeline) > 1 else PRESET_CATEGORIES[key]
        entries.append({"key": key, "name": name, "group": group})
    return entries


def display_preset_name(key: str) -> str:
    """English display name for a preset key; the key itself stays the identifier."""
    if i18n.is_chinese():
        return key
    base = key.split(" (", 1)[0]
    for entry in browse_entries(include_aliases=True):
        if entry["key"] in {key, base}:
            return entry["name"]
    item = get_operator_item(key)
    return localized_operator(item).name if item is not None else key


# Shared descriptions for individual operations and composed recipes.
_OPERATION_EFFECTS = {
    "quantize": (
        "调色板量化",
        "将 RGB 映射到有限调色板，可启用误差扩散。",
        "减少使用的颜色数量。",
        "原始颜色与渐变层次会丢失，可能出现色带或颗粒。",
    ),
    "dither": (
        "量化误差扩散",
        "按所选规则将量化误差分配到邻近像素。",
        "以空间点阵近似连续色阶。",
        "增加点状纹理，微小结构可能与点阵混叠。",
    ),
    "sharpen": (
        "高频残差增强",
        "通过模糊差分增强高频分量。",
        "增加边缘局部对比度。",
        "噪声、光晕和过冲可能增加。",
    ),
    "grayscale": (
        "转换灰度",
        "将颜色通道转换为单通道亮度代理。",
        "去除颜色差异以处理明暗结构。",
        "颜色信息会丢失。",
    ),
    "autocontrast": (
        "直方图端点拉伸",
        "按直方图端点重新映射像素范围。",
        "扩大使用的明暗范围。",
        "噪声与微小亮度差可能放大。",
    ),
    "highpass": (
        "高频残差提取",
        "从原图减去低通分量并按增益映射。",
        "突出局部亮度变化。",
        "缓慢明暗结构会减弱，噪声也可能增强。",
    ),
    "surface_blur": (
        "局部色差加权平滑",
        "按原图与 Gaussian 模糊图的 RGB 距离加权，混合 Gaussian 与中值结果。",
        "减小局部小幅颜色变化。",
        "低对比细线、纹理与小色块可能被平滑。",
    ),
    "anti_checkerboard": (
        "指定高频区域衰减",
        "对 RGB 分通道做 FFT，在指定半径与轴向条件的频区衰减后混回原图；不检测伪影来源。",
        "减小所选频区的周期变化。",
        "同频率的网点、排线和真实纹理也会衰减。",
    ),
    "de_haloing": (
        "局部中值偏差压缩",
        "计算各通道相对局部中值的差值，压缩超过阈值的正负偏差。",
        "减小孤立明暗过冲。",
        "真实高光、小字与细线也可能被压弱。",
    ),
    "filmic_tone": (
        "黑白端点与色阶调整",
        "在编码 RGB 上以幂函数 LUT 抬黑、压白，并调整饱和度与通道色偏。",
        "改变亮暗端点及色阶分布。",
        "原有对比度和颜色关系会改变，不能恢复已截断信息。",
    ),
    "de_ai_bloom": (
        "高光提取与模糊混合",
        "按亮度阈值提取高光，模糊后混合，并可用暗线权重限制作用范围。",
        "扩大高光周围的亮区过渡。",
        "局部对比度可能下降，细线可能变淡。",
    ),
    "subsurface_scattering": (
        "分通道模糊混合",
        "以不同半径模糊 RGB 通道后回混，可用肤色权重限制区域；不求解次表面光传输。",
        "减弱局部明暗与颜色变化。",
        "细节可能模糊，颜色掩模可能误选。",
    ),
    "color_purity": (
        "暗部降饱和与色差增益",
        "按编码亮度代理生成暗部掩模，向灰度混合；按低饱和度权重调整非暗部色差。",
        "降低暗部饱和度并改变其他区域的颜色强度。",
        "有意的阴影配色可能丢失，强增益可能裁切通道。",
    ),
    "anime_line_enhance": (
        "暗线提取与加深",
        "使用各向同性 DoG、方形闭运算与百分位归一化生成权重，再乘法压暗原图。",
        "提高暗部轮廓的对比度。",
        "相邻线条可能连接，细小间隙与灰阶细节可能丢失。",
    ),
    "color_temperature": (
        "通道冷暖与色偏调整",
        "使用 RGB 通道 LUT 调整冷暖及绿品红色偏，不估计光源光谱。",
        "改变整体通道平衡。",
        "原有配色会偏移，强调整可能裁切通道。",
    ),
    "vignette": (
        "径向边缘压暗",
        "按距中心的径向距离生成渐变亮度掩模。",
        "降低画面边缘的亮度。",
        "边缘主体与暗部细节也会变暗。",
    ),
    "focus_lines": (
        "径向线条叠加",
        "按固定随机种子生成角度与宽度扰动，沿中心外侧叠加三角形线条。",
        "增加指向中心的线条结构。",
        "原有背景可能被遮挡，画面结构会改变。",
    ),
    "screentone": (
        "亮度转网点",
        "按亮度与设定的网点频率、角度生成离散点阵。",
        "将连续亮度转为网点密度或大小变化。",
        "颜色与连续色阶可能丢失，缩放可能产生摩尔纹。",
    ),
    "sticker_outline": (
        "透明轮廓扩边与投影",
        "扩张 Alpha 轮廓并叠加指定颜色的边框，可添加投影。",
        "增加可见轮廓与背景的分隔。",
        "原有透明边界会改变，细小间隙可能填满。",
    ),
    "lineart_extract": (
        "高斯差分线稿提取",
        "以不同尺度 Gaussian 差分及非线性阈值映射提取明暗边缘。",
        "将部分亮度变化转为线条。",
        "颜色和连续明暗会丢失，噪声也可能成为线条。",
    ),
    "film_grain": (
        "亮度加权噪声叠加",
        "按亮度权重叠加固定随机种子的噪声；不是对真实胶片或传感器的标定。",
        "增加像素级随机变化。",
        "平坦区域不再均匀，信噪比可能下降。",
    ),
    "cel_shade": (
        "色阶量化与轮廓叠加",
        "先模糊并量化色阶，再以 FIND_EDGES 阈值与最小值滤波生成深色轮廓。",
        "减少颜色层次并强化边缘。",
        "渐变、小色差和细节可能丢失，可能出现断阶。",
    ),
    "pixelate": (
        "块均值降采样与最近邻展开",
        "以 BOX 降采样后用最近邻恢复原尺寸。",
        "以大色块替代局部变化。",
        "空间细节不可恢复；不保证敏感信息已安全脱敏。",
    ),
    "superres": (
        "模型推理放大",
        "以所选模型执行切片推理并合成放大结果，模型与后端身份记录在产物来源中。",
        "增加像素尺寸，并可能生成更鲜明的边缘或纹理。",
        "可能生成原图不存在的细节、改变线条或产生接缝，不是真实细节恢复保证。",
    ),
    "diffuse_glow": (
        "高光模糊与滤色混合",
        "提取超过阈值的亮度区域，Gaussian 模糊后以 Screen 混合并回混原图。",
        "扩大亮区并抬升邻近亮度。",
        "局部对比度和高光层次可能降低。",
    ),
    "vibrance": (
        "饱和度加权颜色调整",
        "按像素颜色与饱和度权重调整通道相对差异。",
        "改变不同饱和度区域的颜色强度。",
        "原配色可能偏移，强调整可能裁切色域。",
    ),
    "line_color": (
        "暗线颜色混合",
        "按暗部权重将轮廓颜色与局部颜色混合。",
        "减小暗线与邻近色块之间的颜色反差。",
        "暗线辨识度可能下降，文字与交叉点可能变淡。",
    ),
    "fisheye": (
        "径向坐标重采样",
        "按径向映射重采样像素，并用缩放参数调整可见范围。",
        "改变画面中心与边缘的相对尺度。",
        "直线与比例会变形，重采样及裁切可能损失边缘内容。",
    ),
    "color_balance": (
        "分亮度区间通道偏移",
        "按阴影、中间调与高光权重调整 RGB 通道。",
        "分别改变不同明暗区域的色偏。",
        "原有颜色关系会改变，过强偏移可能裁切通道。",
    ),
}
for _op, (_title, _method, _benefit, _loss) in _OPERATION_EFFECTS.items():
    INTROS[_op] = {
        "title": _title,
        "summary": _method,
        "principle": _method,
        "scenarios": f"可能带来：{_benefit} 可能失去：{_loss}",
        "benefit": _benefit,
        "loss": _loss,
    }
for _key, _pipeline in PRESET_PIPELINES.items():
    if _key in _NEW_OPERATORS:
        continue
    _steps = [INTROS[step["op"]] for step in _pipeline if step["op"] in _OPERATION_EFFECTS]
    if len(_steps) != len(_pipeline):
        continue
    _preset_item = get_operator_item(_key)
    _title = _BROWSE_NAMES.get(
        _key, (_preset_item.name if _preset_item else _key).split(" (", 1)[0]
    ).replace("4x", "4倍")
    _intro = {
        "title": _title,
        "summary": "依次执行：" + "；".join(step["title"] for step in _steps) + "。",
        "principle": " ".join(step["principle"] for step in _steps),
        "benefit": " ".join(step["benefit"] for step in _steps),
        "loss": " ".join(step["loss"] for step in _steps),
        "scenarios": "各步骤作用叠加，结果取决于输入与参数，不代表画质提升保证。",
    }
    INTROS[_key] = _intro
    INTROS[_key.split(" (", 1)[0]] = _intro


# Practical "may be useful for" notes, kept separate from the technical intros.
_USE_CASES = {
    "local_laplacian_sampled": "整理跨尺度的明暗细节，压低或增强小幅纹理",
    "weighted_guided_smooth": "平滑色块同时保住轮廓，适合处理噪点和压缩痕迹",
    "wls_detail": "分尺度调整明暗细节，减轻灰雾或补回纹理",
    "periodic_notch_auto": "去除网纹、织物和建筑栅格等周期性干扰",
    "fast_guided_smooth": "快速做大面积柔化，适合先看效果再细调",
    "weighted_median": "清理局部杂点和脉冲噪声，同时保留主要边缘",
    "rtv_smooth": "减少纹理和皮肤噪点，尽量留住主要边缘",
    "periodic_notch": "手动压制已知频率的周期性伪影",
    "illumination_correct": "校正不均匀照明和暗角，让亮度更均匀",
    "l0_smooth": "强平滑同时保留强边缘，适合去噪和简化色块",
    "domain_transform": "做保边递归平滑，压低细节噪点",
    "wls_smooth": "保边平滑，减少细小纹理和噪点",
    "chroma_transfer": "把参考图的整体色调迁移到当前图",
    "像素化": "做像素风、马赛克或低分辨率效果",
    "调色板": "限制颜色数量，做复古或海报化配色",
    "线稿": "提取轮廓，做线稿或漫画底稿",
    "抖动": "用点阵近似色阶，做复古印刷质感",
    "锐化": "增强边缘和细节，让画面更利落",
    "赛璐璐": "压平色块并叠加轮廓，做动画赛璐璐风格",
    "去AI塑料感": "降低生成模型感，柔化皮肤和高光",
    "抗AI生成伪影综合治理": "降低生成模型感，抑制高频伪影和塑料质感",
    "二次元平涂去杂": "让平涂更干净，去掉杂色和细碎噪点",
    "动漫色彩提纯": "让暗部更干净、颜色更鲜明",
    "anti_checkerboard": "消除棋盘格和网格状的高频伪影",
    "subsurface_scattering": "做皮肤或半透明材质的柔和透光感",
    "de_haloing": "减轻锐化或 HDR 产生的白边光晕",
    "film_grain": "增加胶片颗粒，削弱数码感和生成感",
    "de_ai_bloom": "扩散高光，做柔和发光的空气感",
    "filmic_tone": "压出电影感的黑白端和层次",
    "chromatic_aberration": "模拟镜头边缘色散，增加摄影感",
    "ACES电影级色调": "做电影级的高光压缩和暗部过渡",
    "引导滤波细节增强": "增强局部细节和纹理，减少过度涂抹感",
    "桑原二次元平涂": "做动漫平涂和色块化，压低照片细节",
    "剧场版宽银幕眩光": "加横向眩光，做剧场版镜头感",
    "日系晴空色彩迁移": "迁移日系晴空的明快色彩",
    "日系通透空气感": "做通透、明亮的日系空气感",
    "热血集中线": "叠加径向集中线，做漫画冲击力",
    "focus_lines": "叠加径向集中线，做漫画冲击力",
    "黑白漫画网点": "把明暗转成漫画网点",
    "screentone": "把明暗转成漫画网点",
    "立绘贴纸白边": "给透明立绘加白边和投影，做贴纸效果",
    "sticker_outline": "给透明立绘加白边和投影，做贴纸效果",
    "XDoG动漫线稿": "提取动漫线稿，强调轮廓和手绘感",
    "lineart_extract": "用高斯差分提取线稿，做干净的手绘底稿",
    "复古90年代胶片": "做 90 年代胶片色偏、暗角和颗粒",
    "动漫超分辨率 4x": "把插画放大四倍，补回线条和色块",
    "通用超分辨率 4x": "把照片或混合图像放大四倍",
    "日系治愈空气柔光": "做柔和高光、通透色彩和暖线稿",
    "大魄力动感鱼眼": "做鱼眼畸变和放射线，增强动感",
    "橙青影视色彩平衡": "做橙青电影调色和胶片颗粒",
    "line_color": "调整暗线颜色，让线稿融入画面",
    "color_balance": "按亮度区间调整色彩偏移",
    "fisheye": "做径向畸变的鱼眼效果",
    "vibrance": "提升饱和度，同时尽量保护肤色",
    "diffuse_glow": "给高光加柔光扩散",
    "vignette": "压暗四周，把视线集中到主体",
    "color_temperature": "调整冷暖色温和色调偏移",
    "superres": "用模型推理放大图像",
    "二次元线条强化与描墨": "强化并加深动漫线稿",
    "二次元赛璐珞顶级精修": "做平滑、描墨和降饱和的赛璐璐精修",
    "anime_line_enhance": "强化动漫线条和描墨",
    "ACG 赛璐璐轻修复": "轻量清理色噪、色带和细碎杂色",
    "ACG 压缩截图修复": "修复压缩动漫截图的彩噪、白边和锯齿",
    "chroma_denoise": "只清理彩色噪点，尽量保住线稿",
    "edge_antialias": "减轻放大后的阶梯锯齿",
    "cel_banding_reduce": "打散平涂渐变里的色带",
    "guided_smooth": "保边平滑，压低噪点和生成纹理",
    "rolling_guidance": "迭代平滑纹理，减少细碎噪点",
    "flow_line": "沿轮廓方向提线，做流动感线稿",
    "kuwahara_anisotropic": "做方向性色块平滑，保留边缘",
    "bounded_sharpen": "锐化但避免明显白边和过冲",
    "multiscale_detail": "按尺度增减细节，控制纹理强度",
    "linear_resize": "在透明边缘下做线性光缩放",
    "quantize": "减少颜色数量，做色调分离",
    "dither": "用误差扩散补足量化后的色阶",
    "sharpen": "增强高频，让边缘更清楚",
    "grayscale": "转成灰度图",
    "autocontrast": "拉伸直方图，增加对比度",
    "highpass": "提取高频细节，做锐化或纹理层",
    "surface_blur": "按色差平滑局部颜色，减少杂色",
    "color_purity": "让暗部降饱和，颜色更纯",
    "cel_shade": "做色阶量化加轮廓的赛璐璐效果",
    "pixelate": "做块状像素化",
    "赛璐璐手绘": "做手绘赛璐璐风格",
    "像素复古风": "做复古像素风",
}
_NOTES = {
    "local_laplacian_sampled": ["跨尺度整理细节，幅度可控", "不能承诺无光晕或保色相"],
    "weighted_guided_smooth": ["平滑色块同时保护轮廓", "不保证消除所有光晕，可能削弱细线"],
    "wls_detail": ["分尺度控制明暗细节", "强增益可能损伤细线"],
    "periodic_notch_auto": ["自动找到并压低窄带周期污染", "网纹、织物、栅格也可能被误删"],
    "fast_guided_smooth": ["大尺度平滑更快", "结果不保证与完整滤波一致"],
    "weighted_median": ["清理局部杂点和脉冲", "窄范围会保留强离群点，可能改变颜色组合"],
    "rtv_smooth": ["减少纹理并保留主要边缘", "迭代不足或参数过强会损失边缘"],
    "periodic_notch": ["精确压制已知频率伪影", "也会移除该频率上的有意图案"],
    "illumination_correct": ["改善不均匀照明和暗角", "可能抹平材质明暗并放大噪声"],
    "l0_smooth": ["强平滑且保留强边缘", "可能合并细线与纹理"],
    "domain_transform": ["保边平滑，减少细节噪点", "可能削弱小结构"],
    "wls_smooth": ["分离缓慢结构与细节", "大图求解可能较慢"],
    "chroma_transfer": ["快速迁移参考图配色", "相对亮度不是 Lab 感知明度，迁移结果可能偏差"],
    "chromatic_aberration": ["增加真实广角镜头的边缘色散", "可能让边缘发虚或出现彩边"],
    "ACES电影级色调": ["平滑高光肩部和暗部过渡", "会改变整体对比和色彩，需要重新调色"],
    "引导滤波细节增强": ["恢复发丝、皮肤和布料纹理", "增强过度会放大噪点和锐化痕迹"],
    "桑原二次元平涂": ["平涂背景、离散厚涂笔触", "可能抹掉小结构和高频细节"],
    "剧场版宽银幕眩光": ["增加宽银幕横向眩光和镜头感", "强光附近可能过曝或出现条纹"],
    "日系晴空色彩迁移": ["快速把灰暗画面调得明快", "整体偏色可能影响肤色和品牌色"],
    "XDoG动漫线稿": ["从彩色画面提取干净线稿", "复杂纹理可能被当成线条"],
    "guided_smooth": ["平滑平涂色块并保护边缘", "低对比细线可能被减弱"],
    "rolling_guidance": ["减少细碎纹理", "小文字和排线可能被抹去"],
    "flow_line": ["沿轮廓提取流动感线条", "交叉点、细线和末端容易断裂"],
    "kuwahara_anisotropic": ["方向性色块抽象并保留边缘", "可能改变小结构形状"],
    "bounded_sharpen": ["受控锐化，抑制过冲", "可能削弱真实高光或放大纹理"],
    "multiscale_detail": ["按尺度调整对比和细节", "增强高频会放大噪声"],
    "linear_resize": ["透明边缘缩放更干净", "非学习型细节恢复，倍率与尺寸互斥"],
    "chroma_denoise": ["压低彩色噪点并保留亮度线条", "细小彩色细节会被平均"],
    "edge_antialias": ["减轻阶梯锯齿", "边缘可能略软，文字需检查"],
    "cel_banding_reduce": ["打散渐变色带且结果可复现", "平坦区域会出现轻微颗粒"],
}
for _name in INTROS:
    _base = operator_catalog_en.ALIASES.get(_name, _name).split(" (", 1)[0]
    if _base not in _USE_CASES:
        _base = _name.split(" (", 1)[0]
    if _base in _USE_CASES:
        INTROS[_name]["use_case"] = _USE_CASES[_base]
    _note = _NOTES.get(_base)
    if _note:
        INTROS[_name]["benefit"], INTROS[_name]["loss"] = _note


def get_intro_text(key: str) -> str:
    """Return formatted introductory text for TUI transform screen."""
    item = localized_intro(key)
    category_badge = ""
    for k, cat in PRESET_CATEGORIES.items():
        if k in key:
            shown = cat if i18n.is_chinese() else _CATEGORY_LABELS_EN.get(cat, cat)
            category_badge = f" [bold cyan][{shown}][/]"
            break
    if item:
        return (
            f"[bold cyan]{item['title']}[/]{category_badge}\n"
            + i18n.t("Operation: ", "操作：")
            + f"[white]{item['summary']}[/]\n"
            + i18n.t("Possible advantages: ", "可能的优势：")
            + f"[dim green]{item['benefit']}[/]\n"
            + i18n.t("Possible drawbacks: ", "可能的缺点：")
            + f"[dim yellow]{item['loss']}[/]\n"
            + i18n.t("May be useful for: ", "可能用来：")
            + f"[green]{item['use_case']}[/]"
        )
    # Fallback to operator item if key matches op
    op_item = get_operator_item(key)
    if op_item:
        op_shown = localized_operator(op_item)
        return (
            f"[bold cyan]{op_shown.name}[/] [bold cyan][{op_shown.category_label}][/]\n"
            + i18n.t("Operation: ", "操作：")
            + f"[white]{op_shown.description}[/]\n"
            + i18n.t("Category: ", "分类：")
            + f"{op_shown.category_label}[/]"
        )
    return i18n.t(
        "[dim]Select a transform preset to see its description and effects[/]",
        "[dim]选择变换预设查看介绍与效果[/]",
    )
