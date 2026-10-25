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
    is_advanced: bool = False


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
        return [{"op": self.default_op, "params": params}]


CATEGORIES: dict[str, str] = {
    "preset": "精选管线预设 (Presets)",
    "anti_ai": "去AI感 (Generative Artifact Suppression)",
    "acg": "二次元动漫 (Non-Photorealistic Rendering & Anime Optics)",
    "photo": "色彩光影 (Color Space Transformation & Tone Mapping)",
    "art": "风格滤波 (Spatial & Frequency Domain Filtering)",
    "superres": "超分辨率 (Deep Super-Resolution)",
}

# Rich presets mapping
PRESET_PIPELINES: dict[str, list[dict[str, Any]]] = {
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
        description="基于分段三次 Hermite 曲线的动态范围压缩与色阶映射算子。",
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
                "本算子基于径向距离权重的色通道横向几何色散模拟，重构真"
                "实光学透镜的边缘色散特性。"
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
        name="次表面散射 (Subsurface Scatter)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description=(
                "本算子基于波长依赖性光子扩散模型与非线性肤色色度掩模，"
                "消除生成模型表面的均一塑料反光质感。"
            ),
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
                description="红色长波长通道高斯漫射核像素半径",
            ),
            Parameter(
                "scatter_strength",
                "漫射混合权重",
                "float",
                0.35,
                min_value=0.0,
                max_value=1.0,
                description="真皮层红润光子漫射线性混合强度",
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
            Parameter("color", "边框颜色", "str", "#ffffff",
                description=(
                    "外轮廓十六进制颜色代码"
                )),
            Parameter("shadow", "立体投影", "bool", True,
                description=(
                    "是否合成低频偏移阴影层"
                )),
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
        description="基于欧氏距离平滑衰减函数的镜头渐晕遮角算子。",
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
                description="余弦平滑过渡过渡带宽度",
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
        description="基于普朗克黑体轨迹与正交色度平移的双轴白平衡校正算子。",
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
        name="AI 超分辨率 (Super-Resolution)",
        category="superres",
        category_label=CATEGORIES["superres"],
        description="基于深度残差卷积与自注意力网络的单图像超分辨率重构算子。",
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
                description="用于推理重构的预训练权重结构",
            ),
            Parameter(
                "tile",
                "瓦片分块",
                "choice",
                "256",
                choices=["128", "256", "512"],
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
        description="基于四象限局部方差最小化判决的各向异性边缘保持平滑算子。",
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
        description="基于流向差分高斯与形态学定向闭合的二次元线稿边缘强化与连贯描墨算子。",
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
                description="形态学闭运算半径，用于缝合AI断线微隙",
                is_advanced=True,
            ),
        ],
    ),
]

OPERATORS_BY_OP: dict[str, OperatorItem] = {item.default_op: item for item in OPERATORS}


def get_operator_item(op: str) -> OperatorItem | None:
    return OPERATORS_BY_OP.get(op)


# -------------------------------------------------------------------------
# Comprehensive Introductions for Presets & Operators
# -------------------------------------------------------------------------

INTROS: dict[str, dict[str, str]] = {
    # --- 基础与风格化滤波 ---
    "像素化": {
        "title": "像素化 (Pixelate)",
        "summary": (
            "本算子通过邻域块均值采样与重构，将连续图像离散化为规则的像素"
            "网格点阵。"
        ),
        "principle": "基于最近邻下采样与块级像素重构算法，实现空间分辨率的阶跃式离散化。",
        "scenarios": "适用于复古像素风格艺术创作与局部敏感信息脱敏处理流程。",
    },
    "调色板": {
        "title": "调色板减色 (Color Palette)",
        "summary": (
            "本算子通过中值切分聚类提取代表性色彩空间，并利用误差扩散完成"
            "低位深着色。"
        ),
        "principle": "基于中值切分色彩量化算法与 Floyd-Steinberg 空间邻域误差扩散卷积。",
        "scenarios": "适用于有限调色板限制的插画设计与复古显示介质效果模拟。",
    },
    "线稿": {
        "title": "手绘线稿提取 (Lineart)",
        "summary": (
            "本算子通过高通滤波与动态阈值反转，提取图像的高频轮廓结构并生"
            "成黑白线稿。"
        ),
        "principle": (
            "基于高通空间卷积算子提取高频梯度，经由直方图自动色阶拉伸与二"
            "值反色映射。"
        ),
        "scenarios": "适用于手绘线稿底图提取、结构线临摹与着色填色练习流程。",
    },
    "抖动": {
        "title": "有序抖动 (Ordered Dither)",
        "summary": "本算子通过预设误差矩阵离散化连续色阶，在有限色阶下重建视觉连续灰度。",
        "principle": (
            "基于 Atkinson 与 Floyd-Steinberg 空间误差扩散转移矩阵，将量"
            "化残差分配至邻域像元。"
        ),
        "scenarios": "适用于单色点阵印刷模拟、赛博终端视觉重构与复古介质渲染。",
    },
    "锐化": {
        "title": "反虚光锐化 (Unsharp Mask)",
        "summary": (
            "本算子通过分离并强化微观高频边缘分量，提升图像整体视觉反差与"
            "边缘聚焦度。"
        ),
        "principle": (
            "基于经典高斯差分反虚光掩模（Unsharp Masking）数学模型，按比"
            "例叠加高频边缘残差。"
        ),
        "scenarios": "适用于补偿光学镜头软化、提高小尺寸图像的视觉微观辨识度。",
    },
    "赛璐璐": {
        "title": "赛璐璐分色 (Cel Shading)",
        "summary": (
            "本算子将连续光影照度离散化为分段阶梯分布，结合索贝尔算子生成"
            "硬边赛璐璐风格。"
        ),
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
            "基于色差引导高斯保边表面平滑消除浮动色斑，结合高光软肩滚降"
            "与极高光微漫射恢复自然光影。"
        ),
        "scenarios": (
            "适用于消除扩散模型生成画面的蜡质油光与不自然高光，呈现干净通透质感。"
        ),
    },
    "抗AI生成伪影综合治理": {
        "title": "抗AI伪影综合 (Anti-AI Artifacts)",
        "summary": (
            "本预设管线结合频域陷波滤波、反边缘振铃、表面去杂平滑与"
            "胶片色阶，多维度消除生成伪影。"
        ),
        "principle": (
            "复合 2D-FFT 频域陷波滤除转置卷积棋盘格，结合形态学反振铃"
            "与表面保边平滑消除结构伪影。"
        ),
        "scenarios": (
            "适用于高画质 AI 生成作品的全流程伪影净化，还原平滑纯净的画面层次。"
        ),
    },
    "二次元平涂去杂": {
        "title": "二次元平涂去杂 (Anime Cel Smoothing)",
        "summary": (
            "本预设管线专为二次元插画平涂设计，消除AI在色块内部生成的"
            "细碎杂色斑驳与暗部脏色。"
        ),
        "principle": (
            "基于色差引导表面模糊平滑色块内部微小杂斑，结合暗部照度掩模"
            "衰减阴影杂乱饱和度以保全线条锐度。"
        ),
        "scenarios": (
            "适用于二次元动漫角色、日系赛璐珞立绘及平涂背景消除AI杂色噪点。"
        ),
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
        "scenarios": (
            "适用于解决AI生图在阴影深处泛紫泛红等脏色问题，恢复二次元纯净冷暖对比。"
        ),
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
            "适用于消除扩散模型与 GAN 在反卷积上采样阶段引入的微观高频栅"
            "格与周期性棋盘格伪影。"
        ),
    },
    "subsurface_scattering": {
        "title": "次表面散射 (Subsurface Scatter)",
        "summary": (
            "本算子通过波长依赖性光子扩散模型与非线性肤色色度掩模，消除生"
            "成模型表面的均一塑料反光质感。"
        ),
        "principle": (
            "基于 Jensen et al. (SIGGRAPH 2001) 次表面散射物理模型，"
            "按红绿蓝波长差分扩散半径模拟半透明组织透光。"
        ),
        "scenarios": (
            "适用于人像皮肤、有机材质与半透明物体渲染，消除蜡质死白并增强"
            "生物组织的温润光泽。"
        ),
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
            "本算子通过照度加权的微观噪点合成，模拟传统感光胶片与数字传感"
            "器在弱光下的光子散粒噪声。"
        ),
        "principle": (
            "基于泊松-高斯（Poisson-Gaussian）噪声分布模型，依反照度非线"
            "性加权高斯随机扰动。"
        ),
        "scenarios": "适用于打破 AI 图像中过于平滑均一的合成表面，建立微观物理纹理基底。",
    },
    "de_ai_bloom": {
        "title": "柔和漫射光晕 (Diffuse Bloom)",
        "summary": (
            "本算子通过照度阈值分割与多尺度高斯漫射核，在保全暗部轮廓的同"
            "时模拟镜头微光晕溢出。"
        ),
        "principle": (
            "基于高光照度非线性加权截断提取，并应用暗部梯度掩模保全线稿边"
            "缘的完整性。"
        ),
        "scenarios": (
            "适用于软化刺眼生硬的 AI 高光边缘，营造大光比场景下的自然光学"
            "漫射环境。"
        ),
    },
    "filmic_tone": {
        "title": "胶片高光压制 (Filmic Tone)",
        "summary": (
            "本算子通过分段平滑 S 曲线抬升基底黑电平并平缓压缩高光，建立"
            "胶片级动态范围滚降响应。"
        ),
        "principle": (
            "基于三次 Hermite 多项式拟合胶片感光特性曲线（Hurter-Driffiel"
            "d Curve），压制纯白过曝溢出。"
        ),
        "scenarios": "适用于修复高动态范围反差失衡、解决画面高光死白与暗部死黑问题。",
    },
    "chromatic_aberration": {
        "title": "镜头边缘色差 (Chromatic Aberration)",
        "summary": (
            "本算子通过红蓝通道径向空间离散错位，模拟透镜材料折射率随光波"
            "长变化引起的横向色散现象。"
        ),
        "principle": (
            "基于光学透镜色散物理模型，以视口几何中心为极点对色通道执行双"
            "线性径向重采样缩放。"
        ),
        "scenarios": (
            "适用于打破完美计算机成像的几何死板感，赋予画面真实广角光学镜"
            "头的边缘成像物理特征。"
        ),
    },
    # --- 电影级与色彩空间变换 ---
    "ACES电影级色调": {
        "title": "ACES电影色调 (ACES Filmic)",
        "summary": (
            "本算子采用影视工业标准学院色彩编码系统，将高动态场景辐射度精"
            "确映射至标准显示色彩空间。"
        ),
        "principle": (
            "基于 Krzysztof Narkowicz 与 Stephen Hill (SIGGRAPH 2015) 的 "
            "ACES S曲线有理多项式拟合。"
        ),
        "scenarios": (
            "适用于大光比场景、复杂逆光与发光体渲染，实现平滑的高光肩部滚"
            "降与渐进暗部过渡。"
        ),
    },
    "aces_tonemap": {
        "title": "ACES电影色调 (ACES Filmic)",
        "summary": (
            "本算子采用影视工业标准学院色彩编码系统，将高动态场景辐射度精"
            "确映射至标准显示色彩空间。"
        ),
        "principle": (
            "基于 Krzysztof Narkowicz 与 Stephen Hill (SIGGRAPH 2015) 的 "
            "ACES S曲线有理多项式拟合。"
        ),
        "scenarios": (
            "适用于大光比场景、复杂逆光与发光体渲染，实现平滑的高光肩部滚"
            "降与渐进暗部过渡。"
        ),
    },
    "引导滤波细节增强": {
        "title": "细节增强 (Guided Filter)",
        "summary": (
            "本算子利用引导图像的结构先验实施保边平滑与高频残差倍增，实现"
            "无振铃效应的细节增强。"
        ),
        "principle": (
            "基于 He et al. (ECCV 2010 / IEEE TPAMI 2013) 局部线性平移不"
            "变模型，精准分离与强化微观高频纹理。"
        ),
        "scenarios": (
            "适用于发丝细节、皮肤微孔、衣物布料纹理的真实感恢复，消除过度"
            "涂抹平滑感。"
        ),
    },
    "guided_filter": {
        "title": "细节增强 (Guided Filter)",
        "summary": (
            "本算子利用引导图像的结构先验实施保边平滑与高频残差倍增，实现"
            "无振铃效应的细节增强。"
        ),
        "principle": (
            "基于 He et al. (ECCV 2010 / IEEE TPAMI 2013) 局部线性平移不"
            "变模型，精准分离与强化微观高频纹理。"
        ),
        "scenarios": (
            "适用于发丝细节、皮肤微孔、衣物布料纹理的真实感恢复，消除过度"
            "涂抹平滑感。"
        ),
    },
    "桑原二次元平涂": {
        "title": "动漫平涂滤波 (Kuwahara Filter)",
        "summary": (
            "本算子在保全主要结构轮廓锐利度的同时平滑色块内部细节，生成纯"
            "净的平面手绘插画质感。"
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
            "本算子在保全主要结构轮廓锐利度的同时平滑色块内部细节，生成纯"
            "净的平面手绘插画质感。"
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
            "本算子通过高光阈值提取与横向一维卷积核扩展，模拟好莱坞变形宽"
            "银幕镜头的水平光斑条纹。"
        ),
        "principle": (
            "基于横向非对称长条高斯卷积核与阈值能量提取，沿水平轴向生成具"
            "有特定波长着色的衍射光斑。"
        ),
        "scenarios": (
            "适用于科幻场景、强逆光人像、夜景光源与舞台灯光下的电影宽银幕"
            "视觉重构。"
        ),
    },
    "anamorphic_flare": {
        "title": "宽银幕眩光 (Anamorphic Flare)",
        "summary": (
            "本算子通过高光阈值提取与横向一维卷积核扩展，模拟好莱坞变形宽"
            "银幕镜头的水平光斑条纹。"
        ),
        "principle": (
            "基于横向非对称长条高斯卷积核与阈值能量提取，沿水平轴向生成具"
            "有特定波长着色的衍射光斑。"
        ),
        "scenarios": (
            "适用于科幻场景、强逆光人像、夜景光源与舞台灯光下的电影宽银幕"
            "视觉重构。"
        ),
    },
    "日系晴空色彩迁移": {
        "title": "色彩风格迁移 (Color Transfer)",
        "summary": (
            "本算子通过在正交去相关色彩空间中对齐均值与方差统计量，实现参"
            "考图像色彩风格的全局迁移。"
        ),
        "principle": (
            "基于 Reinhard et al. (IEEE CG&A 2001) 算法，在 CIE-Lab 色彩"
            "空间执行一阶矩与二阶矩的线性映射转换。"
        ),
        "scenarios": (
            "适用于调整阴郁灰暗图像的整体色调，快速赋予其晴朗通透的明快色"
            "调氛围。"
        ),
    },
    "color_transfer": {
        "title": "色彩风格迁移 (Color Transfer)",
        "summary": (
            "本算子通过在正交去相关色彩空间中对齐均值与方差统计量，实现参"
            "考图像色彩风格的全局迁移。"
        ),
        "principle": (
            "基于 Reinhard et al. (IEEE CG&A 2001) 算法，在 CIE-Lab 色彩"
            "空间执行一阶矩与二阶矩的线性映射转换。"
        ),
        "scenarios": (
            "适用于调整阴郁灰暗图像的整体色调，快速赋予其晴朗通透的明快色"
            "调氛围。"
        ),
    },
    "日系通透空气感": {
        "title": "通透空气感 (Airy Glow)",
        "summary": (
            "本预设管线复合微暖色温偏移、高光滤色漫射与四次余弦暗角，模拟"
            "通透清澈的环境空气透视感。"
        ),
        "principle": (
            "复合微调黑体辐射普朗克色温曲线、多尺度高斯漫射滤色混合以及光"
            "学镜头边缘照度平缓衰减。"
        ),
        "scenarios": (
            "适用于户外自然光照、日常插画与风景原画，提升图像的空气透视深"
            "度与柔和感。"
        ),
    },
    "热血集中线": {
        "title": "漫画集中线 (Focus Rays)",
        "summary": (
            "本算子在极坐标系下沿角向生成离散高反差射线，建立指向视觉兴趣"
            "中心的动态放射构图。"
        ),
        "principle": (
            "基于极坐标逆映射与离散角向随机游走算法，留出中心视场椭球保留"
            "区并向外发散射线。"
        ),
        "scenarios": "适用于强调动作瞬间、视觉中心引导与夸张冲击力构图表达。",
    },
    "focus_lines": {
        "title": "漫画集中线 (Focus Rays)",
        "summary": (
            "本算子在极坐标系下沿角向生成离散高反差射线，建立指向视觉兴趣"
            "中心的动态放射构图。"
        ),
        "principle": (
            "基于极坐标逆映射与离散角向随机游走算法，留出中心视场椭球保留"
            "区并向外发散射线。"
        ),
        "scenarios": "适用于强调动作瞬间、视觉中心引导与夸张冲击力构图表达。",
    },
    "黑白漫画网点": {
        "title": "漫画网点 (Manga Screentone)",
        "summary": (
            "本算子通过局部照度调制固定角度与密度的网格圆点面积，模拟实体"
            "黑白漫画与印刷光栅工艺。"
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
            "本算子通过局部照度调制固定角度与密度的网格圆点面积，模拟实体"
            "黑白漫画与印刷光栅工艺。"
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
            "本算子通过 Alpha 透明通道的形态学欧氏距离场膨胀与阴影卷积，"
            "生成立绘裁切轮廓边框。"
        ),
        "principle": (
            "基于二值形态学膨胀算子与高斯环境遮蔽投影合成，构建高精度各向"
            "同性轮廓包络层。"
        ),
        "scenarios": "适用于透明背景立绘衍生品预览、贴纸裁切设计与图形界面标识突出。",
    },
    "sticker_outline": {
        "title": "贴纸白边 (Sticker Outline)",
        "summary": (
            "本算子通过 Alpha 透明通道的形态学欧氏距离场膨胀与阴影卷积，"
            "生成立绘裁切轮廓边框。"
        ),
        "principle": (
            "基于二值形态学膨胀算子与高斯环境遮蔽投影合成，构建高精度各向"
            "同性轮廓包络层。"
        ),
        "scenarios": "适用于透明背景立绘衍生品预览、贴纸裁切设计与图形界面标识突出。",
    },
    "XDoG动漫线稿": {
        "title": "XDoG动漫线稿 (XDoG Lineart)",
        "summary": (
            "本算子通过不同方差高斯核相减与非线性阈值映射，提取具有手绘铅"
            "笔质感的连续边缘线稿。"
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
            "本算子通过不同方差高斯核相减与非线性阈值映射，提取具有手绘铅"
            "笔质感的连续边缘线稿。"
        ),
        "principle": (
            "基于 Winnemöller et al. (2012) XDoG 算子，结合双尺度高斯差分"
            "与非线性阶跃响应函数提取暗部线段。"
        ),
        "scenarios": "适用于从复杂色彩画面中提取纯净手绘级线稿结构与填色底稿制作。",
    },
    "复古90年代胶片": {
        "title": "90年代复古胶片 (90s Film)",
        "summary": (
            "本预设管线复合暖色色温偏移、暗部电平抬升、四次余弦暗角与银盐"
            "颗粒，还原 90 年代胶片成像特性。"
        ),
        "principle": (
            "复合胶片感光曲线特性、透镜边缘四次余弦光照衰减公式与多通道彩"
            "色随机微粒噪声。"
        ),
        "scenarios": "适用于人像写真与怀旧摄影，重构传统胶片相机沉稳、温暖的物理质感。",
    },
    "动漫超分辨率 4x": {
        "title": "动漫超分 4x (Anime SR)",
        "summary": (
            "本算子采用专精动漫图像的深度神经网络，以 4 倍放大比例无损重"
            "构清晰边缘与纯净色块。"
        ),
        "principle": (
            "基于 Real-ESRGAN 残差密集特征网络推理，在潜空间抑制高频振铃"
            "并平滑各向异性边缘。"
        ),
        "scenarios": "适用于动漫截图高清化、二次元插画大尺寸输出与印刷级高分辨率重构。",
    },
    "通用超分辨率 4x": {
        "title": "通用超分 4x (General SR)",
        "summary": (
            "本算子采用高保真深度生成网络，以 4 倍比例重构自然图像的高频"
            "微观纹理与结构细节。"
        ),
        "principle": (
            "基于 Real-ESRGAN 通用模型推理，利用深层残差连接与感知损失重"
            "建真实感自然图像高频分量。"
        ),
        "scenarios": "适用于真实摄影照片、复杂写实人像与宏大风景图像的高清分辨率重建。",
    },
    "日系治愈空气柔光": {
        "title": "扩散柔光 (Diffuse Glow)",
        "summary": (
            "本预设管线结合高光多尺度滤色扩散、自然饱和度保护与线稿自适应"
            "染色，营造柔美清澈的视觉调性。"
        ),
        "principle": (
            "复合亮度阈值高斯扩散、HSV 肤色饱和度保护增益以及邻域低频基底"
            "线稿融合计算。"
        ),
        "scenarios": "适用于插画人物特写、顺光/逆光风景原画以及需要柔和光感渲染的场景。",
    },
    "大魄力动感鱼眼": {
        "title": "鱼眼广角透视 (Fisheye Lens)",
        "summary": (
            "本预设管线结合 Brown-Conrady 径向透镜畸变与极坐标聚焦射线，"
            "构建强烈的广角动感透视构图。"
        ),
        "principle": (
            "基于径向几何反向映射与极坐标多项式变形模型，协同生成视场向外"
            "凸起的球面透视冲击力。"
        ),
        "scenarios": "适用于动作特写镜头、运动俯冲视角以及具有极度张力的视觉构图表现。",
    },
    "橙青影视色彩平衡": {
        "title": "橙青影视平衡 (Teal & Orange)",
        "summary": (
            "本预设管线采用经典影视冷暖互补调色理论，建立暗部偏青、高光偏"
            "橙金的电影级色彩对冲关系。"
        ),
        "principle": (
            "复合三段式影调亮度权重矩阵、胶片高光滚降映射曲线与微观感光颗"
            "粒噪声。"
        ),
        "scenarios": "适用于城市夜景、霓虹街景、赛博朋克风格渲染与电影感人像调色。",
    },
    "line_color": {
        "title": "线稿自适应染色 (Line Color)",
        "summary": (
            "本算子通过提取暗部边缘轮廓并依据邻域低频色彩进行平滑加权染色"
            "，消除生硬的死黑线条。"
        ),
        "principle": (
            "基于照度掩模非线性加权与大核邻域基底色彩空间插值，将暗部边缘"
            "向局部环境色彩平滑过渡。"
        ),
        "scenarios": (
            "适用于二次元线稿融入背景、软化 AI 容易产生的死硬黑边线条并统"
            "一画面色调。"
        ),
    },
    "color_balance": {
        "title": "三段色彩平衡 (Color Balance)",
        "summary": (
            "本算子通过亮度区间平滑权重分割，独立调节阴影、中间调与高光区"
            "域的三通道色彩偏移量。"
        ),
        "principle": (
            "基于照度区间高斯平滑基函数分离影调，实施红绿蓝三通道加性色彩"
            "向量的精准受控位移。"
        ),
        "scenarios": "适用于精细校正画面色偏、构建特定影视调色风格与冷暖色调对冲布局。",
    },
    "fisheye": {
        "title": "鱼眼广角透视 (Fisheye Lens)",
        "summary": (
            "本算子通过高阶多项式坐标逆映射，模拟真实超广角与鱼眼镜头产生"
            "的桶形或枕形几何畸变。"
        ),
        "principle": (
            "基于 Brown-Conrady 径向几何畸变多项式方程，执行亚像素双线性"
            "插值坐标重采样。"
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
            "适用于风景画面生动化、人像气色提亮以及需要提升鲜艳度且防止色"
            "彩断阶的调色场景。"
        ),
    },
    "diffuse_glow": {
        "title": "扩散柔光 (Diffuse Glow)",
        "summary": (
            "本算子通过亮度阈值分割与多尺度高斯核滤色混合，模拟强光源在光"
            "学介质中的漫反射光晕现象。"
        ),
        "principle": (
            "基于高光照度截断阈值提取、大核空间滤波与滤色（Screen）通道线"
            "性叠加算法。"
        ),
        "scenarios": (
            "适用于增强发光体漫射、营造朦胧梦幻氛围与消除边缘生硬的数字化"
            "锯齿反差。"
        ),
    },
    "vignette": {
        "title": "镜头暗角 (Vignette)",
        "summary": (
            "本算子通过距视口中心的径向距离多项式衰减，模拟真实光学镜头孔"
            "径引起的自然边缘照度衰减。"
        ),
        "principle": (
            "基于光学透镜余弦四次方照度定律（Cosine-Fourth Law），执行向"
            "边缘平滑过渡的亮度衰减。"
        ),
        "scenarios": "适用于聚焦画面主体、模拟大光圈镜头光学特征与增强画面构图沉浸感。",
    },
    "color_temperature": {
        "title": "色温微调 (Color Temperature)",
        "summary": (
            "本算子通过沿普朗克黑体辐射轨迹调整红蓝通道比例，实现物理准确"
            "的色温与色彩微调。"
        ),
        "principle": (
            "基于普朗克黑体辐射定律与 CIE 1931 色品坐标近似模型，对三通道"
            "增益执行非线性对冲调节。"
        ),
        "scenarios": "适用于校正拍摄白平衡失衡、营造温暖黄昏或清冷蓝调光照环境。",
    },
    "superres": {
        "title": "神经超分辨率 (Super-Resolution)",
        "summary": (
            "本算子采用轻量化 ONNX 深度残差卷积神经网络，实现 4 倍分辨率"
            "下的结构保持超分重建。"
        ),
        "principle": (
            "基于 Real-ESRGAN 残差密集块（Residual-in-Residual Dense Bloc"
            "k）前向推理，在切片瓦片上执行拼接无缝重构。"
        ),
        "scenarios": "适用于低清晰度图像高清放大、微观纹理恢复与印刷级输出预处理。",
    },
    "二次元线条强化与描墨": {
        "title": "二次元线条强化与描墨 (Anime Line Inking)",
        "summary": (
            "本算子通过流向差分高斯与定向形态学闭合理论，提取动漫插画暗部"
            "轮廓骨架并加深描墨。"
        ),
        "principle": (
            "基于差分高斯（FDoG）多尺度空间边缘响应提取暗部轮廓，经由形态学"
            "定向闭运算与非线性伽马压墨融合。"
        ),
        "scenarios": "适用于加深强化二次元手绘线稿、缝合AI断线微隙以及消除线条边缘虚灰毛刺。",
    },
    "二次元赛璐珞顶级精修": {
        "title": "二次元赛璐珞顶级精修 (Anime Cel Master)",
        "summary": (
            "整合保边色块平滑、连贯线稿描墨与暗部阴影提纯，提供全套顶级"
            "二次元赛璐珞风格后处理重构。"
        ),
        "principle": (
            "三阶段协同管线：首先表面保边去AI杂斑，其次流向差分高斯描墨增强"
            "轮廓，最后暗部掩模色彩提纯去脏。"
        ),
        "scenarios": (
            "适用于AI二次元生成图的一键顶级动漫化精修，重塑纯净平涂赛璐珞与"
            "紧致利落的手绘质感。"
        ),
    },
    "anime_line_enhance": {
        "title": "动漫线条增强 (Line Inking)",
        "summary": "基于流向差分高斯与形态学定向闭合的二次元线稿边缘强化与连贯描墨算子。",
        "principle": (
            "基于差分高斯（DoG）多尺度响应与形态学闭运算，增强暗部线条黑度并"
            "保全平涂色块。"
        ),
        "scenarios": "适用于消除AI二次元图像线条虚浮断线缺陷并增强日系动漫描墨质感。",
    },
}


PRESET_CATEGORIES: dict[str, str] = {
    "像素化": "风格滤波",
    "调色板": "风格滤波",
    "线稿": "二次元动漫",
    "抖动": "风格滤波",
    "锐化": "风格滤波",
    "赛璐璐": "二次元动漫",
    "去AI塑料感": "去AI感",
    "抗AI生成伪影综合治理": "去AI感",
    "二次元平涂去杂": "去AI感",
    "动漫色彩提纯": "二次元动漫",
    "二次元线条强化与描墨": "二次元动漫",
    "二次元赛璐珞顶级精修": "二次元动漫",
    "动漫线条增强": "二次元动漫",
    "anime_line_enhance": "二次元动漫",
    "日系通透空气感": "二次元动漫",
    "热血集中线": "二次元动漫",
    "黑白漫画网点": "二次元动漫",
    "立绘贴纸白边": "风格滤波",
    "XDoG动漫线稿": "二次元动漫",
    "ACES电影级色调": "色彩光影",
    "引导滤波细节增强": "风格滤波",
    "桑原二次元平涂": "二次元动漫",
    "剧场版宽银幕眩光": "二次元动漫",
    "日系晴空色彩迁移": "色彩光影",
    "复古90年代胶片": "色彩光影",
    "动漫超分辨率 4x": "超分辨率",
    "通用超分辨率 4x": "超分辨率",
    "日系治愈空气柔光": "二次元动漫",
    "大魄力动感鱼眼": "二次元动漫",
    "橙青影视色彩平衡": "色彩光影",
}

CATEGORIES_LIST: list[str] = [
    "全部",
    "去AI感",
    "二次元动漫",
    "色彩光影",
    "风格滤波",
    "超分辨率",
]


def get_intro_text(key: str) -> str:
    """Return formatted introductory text for TUI transform screen."""
    item = INTROS.get(key)
    category_badge = ""
    for k, cat in PRESET_CATEGORIES.items():
        if k in key:
            category_badge = f" [bold cyan][{cat}][/]"
            break
    if item:
        return (
            f"[bold cyan]{item['title']}[/]{category_badge}\n"
            f"[white]{item['summary']}[/]\n"
            f"[dim]原理：{item['principle']}[/]\n"
            f"[dim green]适用：{item['scenarios']}[/]"
        )
    # Fallback to operator item if key matches op
    op_item = get_operator_item(key)
    if op_item:
        return (
            f"[bold cyan]{op_item.name}[/] [bold cyan][{op_item.category_label}][/]\n"
            f"[white]{op_item.description}[/]\n"
            f"[dim]分类：{op_item.category_label}[/]"
        )
    return "[dim]选择变换预设查看介绍与效果[/]"
