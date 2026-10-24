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
    # --- Advanced Paper-based Ops ---
    OperatorItem(
        key="aces_tonemap",
        name="ACES 电影级色调映射 (ACES Filmic Tone)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="好莱坞电影工业级色调映射，完美高光滚降与胶片暗部，消除高光死白",
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
                description="整体亮度曝光缩放倍数",
            ),
            Parameter(
                "contrast",
                "中灰对比度",
                "float",
                1.0,
                min_value=0.5,
                max_value=2.0,
                description="胶片中间调对比度微调",
            ),
        ],
    ),
    OperatorItem(
        key="guided_filter",
        name="引导滤波保边纹理 (Guided Filter)",
        category="anti_ai",
        category_label=CATEGORIES["anti_ai"],
        description="何恺明经典引导滤波，微观纹理与发丝细节恢复，无边缘光晕伪影",
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
                description="局部均值方差引导半径",
            ),
            Parameter(
                "eps",
                "平滑正则化",
                "float",
                0.04,
                min_value=0.001,
                max_value=0.2,
                description="平滑阈值，较小值能保留更硬的边缘",
            ),
            Parameter(
                "boost",
                "细节增益",
                "float",
                1.5,
                min_value=0.5,
                max_value=3.0,
                description="高频微观纹理恢复放大倍率",
            ),
        ],
    ),
    OperatorItem(
        key="kuwahara",
        name="桑原动漫平涂滤波 (Kuwahara Filter)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="四象限各向异性滤波，保留锋利线稿轮廓的同时平滑色块，呈现赛璐璐手绘插画风",
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
                description="象限滤波窗口大小（奇数）",
            ),
        ],
    ),
    OperatorItem(
        key="anamorphic_flare",
        name="剧场版宽银幕眩光 (Anamorphic Flare)",
        category="acg",
        category_label=CATEGORIES["acg"],
        description="模拟好莱坞变形宽银幕镜头的标志性横向高光条纹与光学星芒，增强画面张力",
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
                description="产生眩光的高光亮度起征点",
            ),
            Parameter(
                "length",
                "眩光横向长度",
                "int",
                36,
                min_value=10,
                max_value=120,
                description="变形镜头横向条纹拉伸长度",
            ),
            Parameter(
                "tint",
                "眩光色调",
                "str",
                "#4a90e2",
                description="十六进制色彩，经典蓝青色或香槟金",
            ),
            Parameter(
                "intensity",
                "混合强度",
                "float",
                0.65,
                min_value=0.1,
                max_value=1.5,
                description="眩光叠加不透明度",
            ),
        ],
    ),
    OperatorItem(
        key="color_transfer",
        name="色彩空间风格迁移 (Color Transfer)",
        category="photo",
        category_label=CATEGORIES["photo"],
        description="基于 Reinhard 统计色彩迁移，赋予画面日系晴空、赛博霓虹或复古暖调氛围",
        default_op="color_transfer",
        default_params={"style": "anime_sky", "strength": 1.0},
        parameters=[
            Parameter(
                "style",
                "调色风格",
                "choice",
                "anime_sky",
                choices=["anime_sky", "cyberpunk", "vintage_warm", "morandi"],
                description="预设色彩统计分布",
            ),
            Parameter(
                "strength",
                "迁移浓度",
                "float",
                1.0,
                min_value=0.1,
                max_value=1.0,
                description="风格迁移融合权重",
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
    "像素化": {
        "title": "像素化 (Pixelate)",
        "summary": "经典复古 8-bit / 16-bit 游戏像素点阵颗粒化",
        "principle": "块状降采样与马赛克网格重采样",
        "scenarios": "复古像素艺术、像素游戏头像、局部马赛克",
    },
    "调色板": {
        "title": "调色板减色 (Color Palette)",
        "summary": "降低色彩深度，提取有限色板与抖动过渡",
        "principle": "中值切分色彩量化与 Floyd-Steinberg 扩散",
        "scenarios": "像素复古风、复古印章、限色插画",
    },
    "线稿": {
        "title": "经典高反差线稿 (Classic Lineart)",
        "summary": "提取图像骨架边缘并反转为黑白手绘线稿",
        "principle": "高通滤波边缘检测 + 自动色阶拉伸 + 反色处理",
        "scenarios": "快速提取线稿、填色练习、着色线稿底图",
    },
    "抖动": {
        "title": "有序抖动 (Ordered Dither)",
        "summary": "早期单色或限色显示屏经典噪点抖动艺术",
        "principle": "Atkinson / Floyd 误差扩散矩阵算法",
        "scenarios": "点阵印刷风、赛博朋克终端感、复古海报",
    },
    "锐化": {
        "title": "反虚光锐化 (Unsharp Sharpen)",
        "summary": "增强画面微观边缘对比，提升视觉聚焦清晰度",
        "principle": "高斯差分钝化掩模 (Unsharp Masking)",
        "scenarios": "弥补镜头软化、提高小图清晰度",
    },
    "赛璐璐": {
        "title": "赛璐璐分色 (Cel Shading)",
        "summary": "将真实光影离散化为阶梯状动漫阴影与硬边",
        "principle": "色彩阶梯量化 + 索贝尔边缘勾线",
        "scenarios": "写实转动漫、扁平二次元动画风格化",
    },
    "去AI塑料感": {
        "title": "去AI塑料感 (Anti-AI Film Look)",
        "summary": "消除扩散模型常见的平滑塑料皮、高光死白与糖精色彩",
        "principle": "模拟银盐微颗粒打乱平滑 + S曲线压制死白溢出 + 高光漫射保边 + 光学镜头色散",
        "scenarios": "AI 二次元特写立绘、写实人像，赋予画面沉稳厚重的胶片物理呼吸感",
    },
    "ACES电影级色调": {
        "title": "ACES电影级色调 (ACES Filmic Tone)",
        "summary": "好莱坞与顶级现代渲染引擎标准的电影工业级色调映射",
        "principle": (
            "Stephen Hill & Krzysztof Narkowicz (SIGGRAPH 2015)，完美的胶片高光滚降与渐进暗部"
        ),
        "scenarios": "大光比场景、夕阳夜景、消除高光刺眼与死白",
    },
    "引导滤波细节增强": {
        "title": "引导滤波细节增强 (Guided Filter)",
        "summary": "无光晕伪影的高频保边微纹理恢复与细节强化",
        "principle": "何恺明等 (ECCV 2010)，基于局部线性模型精准恢复微观高频细节",
        "scenarios": "发丝微光、眼眸纹理、服装布料质感，消除AI过度平滑抹脸感",
    },
    "桑原二次元平涂": {
        "title": "桑原二次元平涂 (Kuwahara Filter)",
        "summary": "保持角色轮廓边缘锐利的同时平滑内部色块，呈现纯净的手绘插画质感",
        "principle": "Papari et al. (IEEE TIP 2007)，四象限最小局部方差滤波",
        "scenarios": "二次元插画背景平涂、厚涂转赛璐璐风、消除AI碎噪点",
    },
    "剧场版宽银幕眩光": {
        "title": "剧场版宽银幕眩光 (Anamorphic Flare)",
        "summary": "模拟好莱坞变形宽银幕镜头的标志性横向高光条纹与光学星芒",
        "principle": "Keshmirian (2008) 镜头衍射模拟，沿横轴产生冷蓝或香槟金横向光斑",
        "scenarios": "动漫战斗高潮、舞台灯光、反光点星芒、大片电影海报感",
    },
    "日系晴空色彩迁移": {
        "title": "日系晴空色彩迁移 (Color Transfer)",
        "summary": "新海诚风清爽明快的通透蓝天与暖阳金光氛围",
        "principle": "Reinhard et al. (IEEE CGA 2001)，去相关色彩空间统计分布迁移",
        "scenarios": "暗沉或灰蒙画面快速转为通透日系动漫风",
    },
    "日系通透空气感": {
        "title": "日系通透空气感 (Anime Air Glow)",
        "summary": "京都动画风格的清新明快、清透阳光与柔和光晕",
        "principle": "微暖色温偏移 + 柔光高光漫射 + 柔和暗角",
        "scenarios": "日常治愈系插画、风景原画、唯美立绘",
    },
    "热血集中线": {
        "title": "热血集中线 (Manga Focus Lines)",
        "summary": "少年热血漫画经典的视觉焦点放射速度线",
        "principle": "极坐标空间射线发射算法，留出中心主体安全区",
        "scenarios": "动作特写、出招画面、情绪爆发、夸张冲击力表达",
    },
    "黑白漫画网点": {
        "title": "黑白漫画网点 (Manga Screentone)",
        "summary": "实体黑白漫画与印刷杂志的 45 度半色调网点纸效果",
        "principle": "XDoG 线稿提取 + 45° 调频半色调光栅算法",
        "scenarios": "单行本插页、同人本印刷风、黑白报纸风",
    },
    "立绘贴纸白边": {
        "title": "立绘贴纸白边 (Die-cut Sticker)",
        "summary": "游戏立绘切边、周边贴纸或亚克力徽章常用的白边描边与柔和投影",
        "principle": "Alpha 距离场二值形态学膨胀 + 环境遮蔽柔和阴影",
        "scenarios": "角色表情包、透明底立绘导出、徽章与周边印制预览",
    },
    "XDoG动漫线稿": {
        "title": "XDoG动漫手绘线稿 (XDoG Lineart)",
        "summary": "模拟铅笔在纸上绘制的动漫草稿与线稿提取",
        "principle": "扩展高斯差分 (Extended Difference of Gaussians)，对暗部轮廓高灵敏",
        "scenarios": "草稿线稿提取、填色底稿、线稿临摹学习",
    },
    "复古90年代胶片": {
        "title": "复古90年代胶片 (Retro 90s Film)",
        "summary": "胶片单反相机的暖调怀旧质感与微弱边缘暗角",
        "principle": "胶片暖调色温偏移 + 颗粒模拟 + 镜头暗角",
        "scenarios": "拍立得、复古街拍、千禧年怀旧插画",
    },
    "动漫超分辨率 4x": {
        "title": "动漫超分辨率 4x (Anime Super-Resolution)",
        "summary": "基于 RealESRGAN-anime ONNX 模型的 4 倍无损超分重建",
        "principle": "残差密集神经网络推理，专精二次元平滑边缘与纯净色块重建",
        "scenarios": "低分辨率动漫截图放大、壁纸高清化、印刷级分辨率重建",
    },
    "通用超分辨率 4x": {
        "title": "通用超分辨率 4x (General Super-Resolution)",
        "summary": "基于 RealESRGAN 通用写实模型的 4 倍细节超分重建",
        "principle": "深度神经网络纹理超分辨率重构，兼顾真实细节与清晰度",
        "scenarios": "真实摄影照片、写实人像与宏大场景 4K 放大",
    },
}


def get_intro_text(key: str) -> str:
    """Return formatted introductory text for TUI transform screen."""
    item = INTROS.get(key)
    if item:
        return (
            f"[bold cyan]✦ {item['title']}[/]\n"
            f"[white]{item['summary']}[/]\n"
            f"[dim]原理：{item['principle']}[/]\n"
            f"[dim green]适用：{item['scenarios']}[/]"
        )
    # Fallback to operator item if key matches op
    op_item = get_operator_item(key)
    if op_item:
        return (
            f"[bold cyan]✦ {op_item.name}[/]\n"
            f"[white]{op_item.description}[/]\n"
            f"[dim]分类：{op_item.category_label}[/]"
        )
    return "[dim]选择变换预设查看介绍与效果[/]"
