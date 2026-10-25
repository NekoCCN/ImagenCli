"""English display text for the postprocessing catalog.

Call identifiers (preset keys, operation names, parameter names) stay
unchanged; only human-readable names and descriptions live here.
"""

from __future__ import annotations

CATEGORY_LABELS = {
    "preset": "Recipes",
    "anti_ai": "Smoothing and artifacts",
    "acg": "Lines and color areas",
    "photo": "Color and tone",
    "art": "Style and optics",
    "superres": "Model restoration",
    "geometry": "Size and geometry",
}

OPERATORS: dict[str, dict[str, str]] = {
    "film_grain": {
        "name": "Film Grain",
        "description": "Synthesizes pseudo-random Gaussian silver-halide grain weighted by "
        "illumination.",
    },
    "de_ai_bloom": {
        "name": "Mist Bloom",
        "description": "Edge-preserving highlight diffusion using a luminance threshold and "
        "multi-scale Gaussian kernels.",
    },
    "filmic_tone": {
        "name": "Filmic Tone",
        "description": "Adjusts black and white endpoints with a power-function LUT, plus "
        "saturation and warm/cool shift.",
    },
    "chromatic_aberration": {
        "name": "Lateral Chromatic Aberration",
        "description": "Simulates lateral color dispersion in real lens edges using radial "
        "distance weights per color channel.",
    },
    "anti_checkerboard": {
        "name": "Anti-Checkerboard",
        "description": "Attenuates periodic checkerboard spikes introduced by transposed "
        "convolutions, using a 2D DFT and high-frequency notch mask.",
    },
    "subsurface_scattering": {
        "name": "Skin Softening",
        "description": "Blurs the RGB channels separately and blends with the original; a skin "
        "mask can restrict the area.",
    },
    "de_haloing": {
        "name": "De-Haloing",
        "description": "Suppresses ringing white edges and dark halos at high-contrast edges "
        "using local medians and morphological clamping.",
    },
    "focus_lines": {
        "name": "Speed Lines",
        "description": "Draws radial manga action lines by projecting discrete angular rays in "
        "polar coordinates.",
    },
    "screentone": {
        "name": "Screentone",
        "description": "Rasterizes amplitude-modulated halftone dots on a rotated 2D grid.",
    },
    "sticker_outline": {
        "name": "Sticker Die-cut",
        "description": "Adds an outline and drop shadow by morphologically dilating the alpha "
        "channel distance field.",
    },
    "lineart_extract": {
        "name": "Line Art (XDoG)",
        "description": "Extracts line art using extended difference-of-Gaussians (XDoG) and "
        "non-linear double thresholding.",
    },
    "vignette": {
        "name": "Vignette",
        "description": "Darkens image edges with a gradient mask based on radial distance.",
    },
    "color_temperature": {
        "name": "White Balance",
        "description": "Adjusts warm/cool and green/magenta shift with RGB channel LUTs; does "
        "not estimate the illuminant temperature.",
    },
    "cel_shade": {
        "name": "Cel Shade",
        "description": "Non-photorealistic banded shading by uniformly quantizing luminance.",
    },
    "pixelate": {
        "name": "Pixelate",
        "description": "Resamples into a mosaic by block-average downsampling and nearest-"
        "neighbor expansion.",
    },
    "superres": {
        "name": "Super-Resolution",
        "description": "Single-image super-resolution based on deep residual convolution and "
        "self-attention networks.",
    },
    "aces_tonemap": {
        "name": "ACES Filmic Tone",
        "description": "S-curve tone mapping for wide dynamic range using a rational function "
        "fitted to ACES.",
    },
    "guided_filter": {
        "name": "Guided Filter",
        "description": "Edge-preserving guided filtering with local linear models plus "
        "high-frequency residual detail enhancement.",
    },
    "kuwahara": {
        "name": "Kuwahara Filter",
        "description": "Compares local variance in four quadrants and uses the mean of the most "
        "uniform region to smooth color areas.",
    },
    "anamorphic_flare": {
        "name": "Anamorphic Flare",
        "description": "Simulates anamorphic lens highlights with an anisotropic horizontal 1D "
        "convolution kernel.",
    },
    "color_transfer": {
        "name": "Color Transfer",
        "description": "Global color transfer by matching first- and second-order statistics in "
        "Lab color space.",
    },
    "line_color": {
        "name": "Line Color",
        "description": "Blends an adaptive tint into dark line work using low-luminance edge "
        "weights and the surrounding low-frequency color.",
    },
    "color_balance": {
        "name": "Color Balance",
        "description": "Three-way color balance across shadows, midtones and highlights with "
        "smooth luminance weights.",
    },
    "fisheye": {
        "name": "Fisheye Lens",
        "description": "Lens distortion by inverse mapping with the Brown-Conrady radial "
        "polynomial model.",
    },
    "vibrance": {
        "name": "Vibrance",
        "description": "Non-linear vibrance that boosts unsaturated colors while damping warm "
        "hues.",
    },
    "diffuse_glow": {
        "name": "Diffuse Glow",
        "description": "Multi-scale highlight glow using luminance thresholding and a screen "
        "blend mode.",
    },
    "surface_blur": {
        "name": "Surface Blur",
        "description": "Removes speckle from flat anime color areas while keeping edges, using "
        "color-difference-weighted Gaussians and a median pass.",
    },
    "color_purity": {
        "name": "Color Purity",
        "description": "Cleans dirty shadows and adaptively enhances color purity using a "
        "shadow-luminance mask.",
    },
    "anime_line_enhance": {
        "name": "Line Inking",
        "description": "Extracts dark lines with isotropic DoG and a square morphological "
        "closing, then multiplies them into the image.",
    },
    "guided_smooth": {
        "name": "Guided edge-preserving smoothing",
        "description": "Smooths encoded sRGB colors with a local linear guided model, then "
        "blends with the original; fine lines may weaken.",
    },
    "rolling_guidance": {
        "name": "Iterative guided texture smoothing",
        "description": "Iteratively applies joint bilateral guidance after an initial smooth to "
        "reduce fine texture; small structures may disappear.",
    },
    "flow_line": {
        "name": "Flow-guided line extraction",
        "description": "Builds an ETF tangent field, applies normal-direction DoG and tangent "
        "integration, and outputs lines or blends them back.",
    },
    "kuwahara_anisotropic": {
        "name": "Directional color-area smoothing",
        "description": "Uses a structure tensor to choose a direction, then weights directional "
        "sector statistics; detail shapes may change.",
    },
    "bounded_sharpen": {
        "name": "Bounded luminance sharpening",
        "description": "Enhances a luminance proxy residual with a local overshoot limit; may "
        "clip genuine highlights.",
    },
    "multiscale_detail": {
        "name": "Scale-adjusted detail",
        "description": "Adjusts fine, medium and coarse luminance residual gains; 1 leaves them "
        "unchanged and boosts may amplify noise.",
    },
    "linear_resize": {
        "name": "Linear-light alpha resize",
        "description": "Premultiplies alpha in linear sRGB before resizing. Scale and size are "
        "mutually exclusive; the catalog default scale is 1.",
    },
    "l0_smooth": {
        "name": "L0 gradient smoothing",
        "description": "Reduces non-zero gradients with half-quadratic splitting and FFT "
        "periodic boundaries; fine lines and texture may merge.",
    },
    "domain_transform": {
        "name": "Domain-transform recursive smoothing",
        "description": "Builds a transform domain from the original color differences and "
        "performs bidirectional recursive scans; small structures may weaken.",
    },
    "wls_smooth": {
        "name": "Weighted least-squares smoothing",
        "description": "Solves an RGB least-squares smooth weighted by log-luminance proxy "
        "gradients; errors out when unconverged.",
    },
    "rtv_smooth": {
        "name": "Relative total variation heuristic (approximate)",
        "description": "Approximate IRLS smoothing with box-window variation weights; not the "
        "paper's full RTV solver.",
    },
    "periodic_notch": {
        "name": "Manual periodic-frequency suppression",
        "description": "Applies conjugate Gaussian notches at user-specified periodic "
        "frequencies without detection; intentional patterns are also removed.",
    },
    "chroma_transfer": {
        "name": "Reference chroma statistics transfer",
        "description": "Transfers reference-image statistics in linear-RGB opponent channels "
        "while keeping relative luminance, not Lab perceptual lightness.",
    },
    "illumination_correct": {
        "name": "Smooth illumination correction (approximate)",
        "description": "Divides linear sRGB by a Gaussian illumination estimate; not LIME, and "
        "may amplify noise.",
    },
    "weighted_median": {
        "name": "Color-similarity weighted median",
        "description": "Takes a per-channel median weighted by RGB similarity and coverage to "
        "reduce impulses and speckles; fine lines may be removed.",
    },
    "fast_guided_smooth": {
        "name": "Downsampled guided smoothing coefficients",
        "description": "Estimates linear coefficients at low resolution and reconstructs with "
        "full-size luminance guidance; small structures may be missed.",
    },
    "periodic_notch_auto": {
        "name": "Detect and suppress narrowband periodic peaks",
        "description": "Detects narrow peaks in a detrended, windowed spectrum and applies "
        "conjugate notches; cannot distinguish intentional dots from noise.",
    },
    "wls_detail": {
        "name": "Edge-preserving scale detail",
        "description": "Decomposes luminance residuals at three scales with fixed guidance WLS; "
        "boosts may halo, and this is not Local Laplacian.",
    },
    "weighted_guided_smooth": {
        "name": "Variance-weighted guided smoothing",
        "description": "Adapts regularization from 3-pixel window variance to smooth color "
        "areas with less cross-edge blending; fine texture may still be lost.",
    },
    "local_laplacian_sampled": {
        "name": "Sampled local Laplacian detail remapping",
        "description": "Remaps and interpolates pyramid coefficients at several reference "
        "luminances; the sampled approximation may halo or clip gamut.",
    },
}

PARAMETERS: dict[str, dict[str, dict[str, str]]] = {
    "film_grain": {
        "amount": {"label": "Grain strength", "description": "Grain intensity factor (0.01–0.4)"},
        "monochrome": {
            "label": "Monochrome grain",
            "description": "Single-channel gray grain; off uses color grain",
        },
        "shadow_bias": {
            "label": "Shadow weighting",
            "description": "Weights grain intensity toward dark areas",
        },
    },
    "de_ai_bloom": {
        "radius": {"label": "Glow radius", "description": "Gaussian blur radius"},
        "intensity": {"label": "Glow intensity", "description": "Linear screen-blend weight"},
        "threshold": {
            "label": "Highlight threshold",
            "description": "Starting luminance threshold for the diffused region",
        },
        "preserve_lines": {
            "label": "Protect line art",
            "description": "Uses a dark-gradient mask to preserve line outlines",
        },
    },
    "filmic_tone": {
        "lift_black": {"label": "Lift shadows", "description": "Black-level lift amount"},
        "compress_white": {
            "label": "Compress highlights",
            "description": "Highlight saturation cutoff threshold",
        },
        "saturation": {
            "label": "Saturation",
            "description": "Global saturation scale in the color space",
        },
        "warmth": {
            "label": "Warm/cool shift",
            "description": "Complementary red/blue channel temperature shift",
        },
    },
    "chromatic_aberration": {
        "shift": {
            "label": "Dispersion shift",
            "description": "Spatial red/blue channel displacement in pixels",
        },
        "radial": {
            "label": "Radial falloff",
            "description": "Scales shift by Euclidean distance from the viewport center",
        },
    },
    "anti_checkerboard": {
        "strength": {
            "label": "Suppression strength",
            "description": "Blend weight between frequency notching and spatial reconstruction",
        },
        "cutoff": {
            "label": "High-frequency cutoff radius",
            "description": "Normalized Nyquist radius that triggers notching",
        },
    },
    "subsurface_scattering": {
        "radius": {
            "label": "Base scattering radius",
            "description": "Red-channel Gaussian radius; green and blue use smaller radii",
        },
        "scatter_strength": {
            "label": "Diffusion blend",
            "description": "Blend ratio of the softened RGB result with the original",
        },
        "skin_only": {
            "label": "Restrict to skin",
            "description": "Enables a feathered YCbCr skin-chroma mask",
        },
    },
    "de_haloing": {
        "radius": {
            "label": "Filter window radius",
            "description": "Pixel radius of the local median window",
        },
        "strength": {
            "label": "Suppression strength",
            "description": "Convergence ratio for pixels overshooting the local median",
        },
        "threshold": {
            "label": "Overshoot threshold",
            "description": "Gradient tolerance that keeps natural edge contrast",
        },
    },
    "focus_lines": {
        "lines": {"label": "Ray count", "description": "Number of discrete angular rays"},
        "inner_radius": {
            "label": "Subject clearance",
            "description": "Ellipse axis ratio of the clear center region",
        },
        "max_width": {
            "label": "Line width",
            "description": "Maximum ray width in pixels at the outer edge",
        },
        "line_color": {"label": "Line color", "description": "Hexadecimal color code"},
    },
    "screentone": {
        "frequency": {"label": "Dot density", "description": "Spatial sampling grid frequency"},
        "angle": {"label": "Dot angle", "description": "2D grid rotation angle"},
        "contrast": {
            "label": "Black/white contrast",
            "description": "Contrast scale before level quantization",
        },
    },
    "sticker_outline": {
        "width": {
            "label": "Outline width",
            "description": "Distance-field dilation width in pixels",
        },
        "color": {"label": "Outline color", "description": "Outer outline hex color code"},
        "shadow": {
            "label": "Drop shadow",
            "description": "Whether to composite a low-frequency offset shadow",
        },
        "shadow_offset": {
            "label": "Shadow offset",
            "description": "Magnitude of the shadow translation vector",
        },
    },
    "lineart_extract": {
        "radius": {
            "label": "Line fineness",
            "description": "Base Gaussian kernel standard deviation",
        },
        "threshold": {"label": "Line threshold", "description": "Binarization cutoff threshold"},
    },
    "vignette": {
        "radius": {
            "label": "Clear range",
            "description": "Ellipse radius factor that stays undarkened",
        },
        "feather": {"label": "Edge falloff", "description": "Width of the radial mask transition"},
        "darkness": {
            "label": "Vignette depth",
            "description": "Maximum luminance attenuation at the border",
        },
    },
    "color_temperature": {
        "temperature": {
            "label": "Temperature",
            "description": "Yellow/blue axis temperature shift factor",
        },
        "tint": {"label": "Tint", "description": "Green/magenta axis tint shift factor"},
    },
    "cel_shade": {
        "levels": {
            "label": "Levels",
            "description": "Discrete quantization steps per color channel",
        },
    },
    "pixelate": {
        "scale": {"label": "Block size", "description": "Spatial sampling block size in pixels"},
    },
    "superres": {
        "model": {"label": "Model", "description": "Pretrained weight architecture for inference"},
        "tile": {"label": "Tile size", "description": "Spatial tile size for tiled inference"},
        "overlap": {
            "label": "Tile overlap",
            "description": "Overlap margin in pixels to hide tile seams",
        },
    },
    "aces_tonemap": {
        "exposure": {"label": "Exposure", "description": "Input linear radiance scale"},
        "contrast": {"label": "Midtone contrast", "description": "Midtone slope adjustment factor"},
    },
    "guided_filter": {
        "radius": {
            "label": "Local radius",
            "description": "Spatial window radius of the local linear regression",
        },
        "eps": {
            "label": "Smoothing regularization",
            "description": "Ridge regularization constant for variance estimation",
        },
        "boost": {
            "label": "Detail boost",
            "description": "Multiplicative gain for the high-frequency residual",
        },
    },
    "kuwahara": {
        "size": {
            "label": "Window size",
            "description": "Pixel side length of the four-quadrant window",
        },
    },
    "anamorphic_flare": {
        "threshold": {
            "label": "Highlight threshold",
            "description": "Luminance cutoff where streaks begin",
        },
        "length": {
            "label": "Horizontal flare length",
            "description": "Horizontal 1D blur kernel length",
        },
        "tint": {"label": "Flare tint", "description": "Glow tint hex color code"},
        "intensity": {
            "label": "Blend intensity",
            "description": "Linear additive blend weight for the flare",
        },
    },
    "color_transfer": {
        "style": {
            "label": "Color style",
            "description": "First- and second-order statistics of the reference image",
        },
        "strength": {
            "label": "Transfer strength",
            "description": "Linear interpolation weight for mean/variance matching",
        },
    },
    "line_color": {
        "strength": {"label": "Tint strength", "description": "Edge blend weight"},
        "darkness": {
            "label": "Keep darkness",
            "description": "Luminance attenuation of the tint base",
        },
    },
    "color_balance": {
        "shadows": {
            "label": "Shadow offset",
            "description": "Additive channel offset vector for shadows (−1.0–1.0)",
        },
        "midtones": {
            "label": "Midtone offset",
            "description": "Additive channel offset vector for midtones (−1.0–1.0)",
        },
        "highlights": {
            "label": "Highlight offset",
            "description": "Additive channel offset vector for highlights (−1.0–1.0)",
        },
    },
    "fisheye": {
        "strength": {
            "label": "Distortion strength",
            "description": "Quadratic radial distortion coefficient",
        },
        "zoom": {
            "label": "Field-of-view zoom",
            "description": "Linear scale of the sampling coordinates",
        },
    },
    "vibrance": {
        "amount": {"label": "Vibrance", "description": "Gain for unsaturated regions"},
    },
    "diffuse_glow": {
        "threshold": {
            "label": "Highlight threshold",
            "description": "Luminance threshold for extracting glow energy",
        },
        "radius": {"label": "Glow radius", "description": "Gaussian diffusion radius"},
        "intensity": {"label": "Glow strength", "description": "Linear screen-blend weight"},
    },
    "surface_blur": {
        "blend": {
            "label": "Blend strength",
            "description": "Weight of the smoothed result against the original",
        },
        "radius": {
            "label": "Smoothing radius",
            "description": "Spatial neighborhood radius of the smoothing filter",
        },
        "threshold": {
            "label": "Color-difference threshold",
            "description": "Tolerance below which pixels count as the same color "
            "area and are flattened",
        },
    },
    "color_purity": {
        "vibrance_boost": {
            "label": "Color purity",
            "description": "Natural vibrance gain for unsaturated colors",
        },
        "shadow_desat": {
            "label": "Shadow cleanup",
            "description": "Saturation reduction for stray color in shadows",
        },
        "shadow_threshold": {
            "label": "Shadow range",
            "description": "Luminance threshold defining shadow areas",
        },
    },
    "anime_line_enhance": {
        "strength": {
            "label": "Ink strength",
            "description": "Global blend weight for darkening line work",
        },
        "thickness": {"label": "Line thickness", "description": "Ink width scale factor"},
        "sigma": {
            "label": "Gaussian scale",
            "description": "Standard deviation of the center-scale kernel",
        },
        "k_ratio": {
            "label": "Kernel width ratio",
            "description": "Width ratio of the outer to inner DoG kernel",
        },
        "tau": {
            "label": "Sensitivity threshold",
            "description": "Edge-response sensitivity threshold",
        },
        "gamma": {"label": "Ink contrast", "description": "Gamma contrast for the ink curve"},
        "closing_radius": {
            "label": "Closing radius",
            "description": "Square closing radius; may join nearby lines",
        },
    },
    "guided_smooth": {
        "radius": {"label": "Neighborhood radius (input pixels)", "description": ""},
        "epsilon": {
            "label": "Guided variance regularization",
            "description": "Squared units of normalized guidance",
        },
        "strength": {"label": "Blend strength", "description": ""},
        "guidance": {"label": "Guidance channel", "description": ""},
        "border": {"label": "Border policy", "description": ""},
    },
    "rolling_guidance": {
        "sigma_space": {"label": "Spatial scale (input pixels)", "description": ""},
        "sigma_range": {
            "label": "Range scale",
            "description": "Units of normalized encoded sRGB intensity",
        },
        "iterations": {"label": "Iterations", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
        "inner": {"label": "Inner filter", "description": ""},
        "border": {"label": "Border policy", "description": ""},
    },
    "flow_line": {
        "strength": {"label": "Blend strength", "description": ""},
        "output": {"label": "Output mode", "description": ""},
        "derivative_sigma": {"label": "Gradient scale (pixels)", "description": ""},
        "etf_radius": {"label": "Orientation neighborhood (pixels)", "description": ""},
        "etf_iterations": {"label": "Orientation iterations", "description": ""},
        "sigma_normal": {"label": "Normal scale (pixels)", "description": ""},
        "gaussian_ratio": {"label": "Gaussian width ratio", "description": ""},
        "sigma_tangent": {"label": "Tangent scale (pixels)", "description": ""},
        "tau": {"label": "Difference weight", "description": ""},
        "threshold": {"label": "Line threshold", "description": ""},
        "border": {"label": "Border policy", "description": ""},
    },
    "kuwahara_anisotropic": {
        "radius": {"label": "Neighborhood radius (input pixels)", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
        "sectors": {"label": "Sector count", "description": ""},
    },
    "bounded_sharpen": {
        "amount": {"label": "Sharpen gain", "description": ""},
        "radius": {"label": "Radius (input pixels)", "description": ""},
        "noise_gate": {
            "label": "Residual gate",
            "description": "Units of normalized encoded sRGB luminance proxy",
        },
        "overshoot": {
            "label": "Allowed overshoot",
            "description": "Units of normalized encoded sRGB luminance proxy",
        },
    },
    "multiscale_detail": {
        "fine": {"label": "Fine scale gain", "description": ""},
        "medium": {"label": "Medium scale gain", "description": ""},
        "coarse": {"label": "Coarse scale gain", "description": ""},
        "radius": {"label": "Base radius (input pixels)", "description": ""},
    },
    "linear_resize": {
        "scale": {
            "label": "Scale factor",
            "description": "Positive finite number; remove it when a size is given",
        },
        "filter": {"label": "Resampling filter", "description": ""},
    },
    "l0_smooth": {
        "smoothing": {"label": "Smoothing factor", "description": "Greater than 0, at most 1"},
        "beta_growth": {"label": "Penalty growth", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
    },
    "domain_transform": {
        "sigma_space": {
            "label": "Spatial scale (input pixels)",
            "description": "Greater than 0, at most 256",
        },
        "sigma_range": {
            "label": "Color-difference scale",
            "description": "Normalized encoded RGB units; greater than 0, at most 1",
        },
        "iterations": {"label": "Scan passes", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
    },
    "wls_smooth": {
        "smoothing": {"label": "Smoothing factor", "description": "Greater than 0, at most 10"},
        "alpha": {"label": "Gradient weight exponent", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
    },
    "rtv_smooth": {
        "smoothing": {"label": "Smoothing factor", "description": "Greater than 0, at most 1"},
        "iterations": {"label": "IRLS passes", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
    },
    "periodic_notch": {
        "frequencies": {
            "label": "Frequency coordinates",
            "description": "fx,fy;fx,fy in cycles/pixel, each axis [−0.5, 0.5], "
            "excluding 0,0; empty skips processing",
        },
        "width": {"label": "Notch width (cycles/pixel)", "description": ""},
        "strength": {"label": "Suppression strength", "description": ""},
    },
    "chroma_transfer": {
        "reference": {
            "label": "Reference image path (required)",
            "description": "A readable reference image path is required",
        },
        "strength": {"label": "Transfer strength", "description": ""},
        "luminance": {"label": "Relative luminance handling", "description": ""},
    },
    "illumination_correct": {
        "radius": {"label": "Illumination smoothing radius (pixels)", "description": ""},
        "strength": {"label": "Correction strength", "description": ""},
        "floor": {"label": "Illumination floor", "description": "Normalized linear sRGB value"},
    },
    "weighted_median": {
        "radius": {"label": "Neighborhood radius (pixels)", "description": ""},
        "sigma_range": {"label": "RGB range scale", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
    },
    "fast_guided_smooth": {
        "radius": {"label": "Input pixel radius", "description": ""},
        "epsilon": {"label": "Normalized guidance squared regularization", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
        "subsample": {"label": "Coefficient downsample factor", "description": ""},
    },
    "periodic_notch_auto": {
        "threshold": {"label": "Local peak contrast threshold", "description": ""},
        "min_frequency": {"label": "Minimum radial frequency", "description": ""},
        "max_pairs": {"label": "Maximum frequency pairs", "description": ""},
        "width": {"label": "Notch width", "description": ""},
        "strength": {"label": "Suppression strength", "description": ""},
    },
    "wls_detail": {
        "fine": {"label": "Fine scale gain", "description": ""},
        "medium": {"label": "Medium scale gain", "description": ""},
        "coarse": {"label": "Coarse scale gain", "description": ""},
        "smoothing": {"label": "Initial smoothing weight", "description": ""},
        "scale_ratio": {"label": "Inter-layer weight ratio", "description": ""},
        "alpha": {"label": "Edge weight exponent", "description": ""},
    },
    "weighted_guided_smooth": {
        "radius": {"label": "Input pixel radius", "description": ""},
        "epsilon": {"label": "Normalized guidance squared regularization", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
    },
    "local_laplacian_sampled": {
        "sigma": {"label": "Detail amplitude threshold", "description": ""},
        "detail": {"label": "Detail exponent (above 1 smooths)", "description": ""},
        "samples": {"label": "Reference luminance samples", "description": ""},
        "levels": {"label": "Maximum levels", "description": ""},
        "strength": {"label": "Blend strength", "description": ""},
    },
}

GROUPS = {"综合配方": "Combined recipes"}

PRESET_NAMES = {
    "像素化": "Pixelate",
    "调色板": "Palette",
    "线稿": "Line art",
    "抖动": "Dither",
    "锐化": "Sharpen",
    "赛璐璐": "Cel shade",
    "去AI塑料感": "Anti-AI Organic",
    "抗AI生成伪影综合治理": "Anti-AI Artifacts Suppression",
    "二次元平涂去杂": "Anime Cel Surface Smoothing",
    "动漫色彩提纯": "Anime Color Purity",
    "二次元线条强化与描墨": "Anime Line Inking",
    "二次元赛璐珞顶级精修": "Anime Cel Master",
    "日系通透空气感": "Shinkai Airy",
    "热血集中线": "Manga Action Focus",
    "黑白漫画网点": "Manga Screentone",
    "立绘贴纸白边": "Die-cut Sticker",
    "XDoG动漫线稿": "XDoG Lineart",
    "XDoG动漫手绘线稿": "Line Art",
    "复古90年代胶片": "Retro 90s Film",
    "赛璐璐手绘": "Cel Shade",
    "像素复古风": "Pixel Art",
    "动漫超分辨率 4x": "Anime SR",
    "通用超分辨率 4x": "General SR",
    "日系治愈空气柔光": "Anime Soft Glow",
    "大魄力动感鱼眼": "Anime Fisheye",
    "橙青影视色彩平衡": "Teal & Orange",
    "ACES电影级色调": "ACES Filmic Tone",
    "引导滤波细节增强": "Guided Filter Detail",
    "桑原二次元平涂": "Kuwahara Filter",
    "剧场版宽银幕眩光": "Anamorphic Flare",
    "日系晴空色彩迁移": "Color Transfer",
}

INTROS: dict[str, dict[str, str]] = {
    "local_laplacian_sampled": {
        "title": "Sampled local Laplacian detail remapping",
        "summary": "Remaps and interpolates pyramid coefficients at several reference luminances; "
        "the sampled approximation may halo or clip gamut.",
        "principle": "Remaps detail with the power function from Local Laplacian Filters (2011), "
        "equation 1, keeping unit slope at large edges; reference luminances are uniformly "
        "sampled and remapped pyramid coefficients are linearly interpolated. Downsampling with "
        "Gaussian sigma=1 and bilinear expansion are not the original per-coefficient algorithm "
        "or the authors' accelerated implementation. Experimental; real-image acceptance is not "
        "complete.",
        "scenarios": "For suppressing or boosting small detail across scales; encoded-luminance "
        "additive re-mixing, hidden RGB mean extension and final RGB clipping cannot promise "
        "halo-free or hue-preserving output.",
    },
    "weighted_guided_smooth": {
        "title": "Variance-weighted guided smoothing",
        "summary": "Adapts regularization from 3-pixel window variance to smooth color areas with "
        "less cross-edge blending; fine texture may still be lost.",
        "principle": "Follows Weighted Guided Image Filtering (2015), equations 5–10: edge weights "
        "come from 3×3 variance and the global mean inverse variance, with epsilon/weight "
        "regularization. This project guides RGB with luminance, uses coverage weighting, reflect "
        "borders and sigma=1 Gaussian weights. Experimental; real-image acceptance is not "
        "complete.",
        "scenarios": "For smoothing color areas while comparing boundary protection; this is not "
        "a weighted median and cannot guarantee that every halo is removed.",
    },
    "wls_detail": {
        "title": "Edge-preserving scale detail",
        "summary": "Decomposes luminance residuals at three scales with fixed guidance WLS; "
        "boosts may halo, and this is not Local Laplacian.",
        "principle": "Solves three WLS systems with different regularization strengths using fixed "
        "log-luma weights from the original, adjusts adjacent-layer residuals, then reconstructs. "
        "CG tolerance is 1e-6 with at most 500 iterations; non-convergence refuses output. "
        "Experimental; real-image acceptance is not complete.",
        "scenarios": "For controlling tonal detail per scale; all gains at 1 bypass exactly, "
        "while strong gains can still damage fine lines.",
    },
    "periodic_notch_auto": {
        "title": "Detect and suppress narrowband periodic peaks",
        "summary": "Detects narrow peaks in a detrended, windowed spectrum and applies conjugate "
        "notches; cannot distinguish intentional dots from noise.",
        "principle": "Fits and removes a luminance plane, applies a Hann window, then looks for "
        "conjugate narrow peaks against a local annulus background; no peak means no processing, "
        "and detected frequencies are written to the execution record. This is not semantic "
        "recognition or the paper's same-named algorithm. Experimental; real-image acceptance is "
        "not complete.",
        "scenarios": "For checking known periodic contamination; manga screentone, fabric and "
        "architectural grids may also be removed, so compare against the original.",
    },
    "fast_guided_smooth": {
        "title": "Downsampled guided smoothing coefficients",
        "summary": "Estimates linear coefficients at low resolution and reconstructs with "
        "full-size luminance guidance; small structures may be missed.",
        "principle": "Computes coefficients at low resolution following Fast Guided Filter (2015) "
        "Algorithm 2, bilinearly upsamples them and applies them to full-size guidance. Radius is "
        "divided by the factor with a floor of 1; coverage weighting is this project's variant, "
        "and factor 1 calls the full guided filter. Experimental; real-image acceptance is not "
        "complete.",
        "scenarios": "For comparing speed at large-scale smoothing; output is not guaranteed to "
        "match the full filter and does not replace old recipes automatically.",
    },
    "weighted_median": {
        "title": "Color-similarity weighted median",
        "summary": "Takes a per-channel median weighted by RGB similarity and coverage to reduce "
        "impulses and speckles; fine lines may be removed.",
        "principle": "Takes the value once cumulative sorted weight reaches half, following WMF "
        "(CVPR 2014), equation 2; this implementation is a row-wise sorted reference variant with "
        "RGB Gaussian weights and none of the paper's acceleration structures. Experimental; "
        "real-image acceptance is not complete.",
        "scenarios": "For local speckles and impulses; a narrow range keeps strong outliers, and "
        "per-channel values may also change color combinations.",
    },
    "rtv_smooth": {
        "title": "Relative total variation heuristic",
        "summary": "Approximate IRLS smoothing with box-window variation weights; not the paper's "
        "full RTV solver.",
        "principle": "This is an engineering approximation, not the paper's full RTV solution. "
        "Experimental; real-image acceptance is not complete.",
        "scenarios": "For reducing texture; too few iterations or strong parameters may lose "
        "edges.",
    },
    "periodic_notch": {
        "title": "Manual periodic-frequency suppression",
        "summary": "Applies conjugate Gaussian notches at user-specified periodic frequencies "
        "without detection; intentional patterns are also removed.",
        "principle": "Builds conjugate frequency-domain notches from cycles/pixel coordinates "
        "while keeping DC. Experimental; real-image acceptance is not complete.",
        "scenarios": "For known periodic interference; the notch also removes intentional "
        "screentone or line patterns at the same frequencies.",
    },
    "illumination_correct": {
        "title": "Smooth illumination correction",
        "summary": "Divides linear sRGB by a Gaussian illumination estimate; not LIME, and may "
        "amplify noise.",
        "principle": "Divides linear sRGB by a smoothed illumination estimate. Experimental; "
        "real-image acceptance is not complete.",
        "scenarios": "For uneven lighting; the approximation may amplify noise and does not "
        "recover clipped highlights.",
    },
    "l0_smooth": {
        "title": "L0 gradient smoothing",
        "summary": "Reduces non-zero gradients with half-quadratic splitting and FFT periodic "
        "boundaries; fine lines and texture may merge.",
        "principle": "Uses half-quadratic splitting and FFT with periodic boundaries. "
        "Experimental; real-image acceptance is not complete.",
        "scenarios": "For flat color areas; thin lines and fine texture may be merged.",
    },
    "domain_transform": {
        "title": "Domain-transform recursive smoothing",
        "summary": "Builds a transform domain from the original color differences and performs "
        "bidirectional recursive scans; small structures may weaken.",
        "principle": "Controls recursive transform-domain weights through spatial and range "
        "scales. Experimental; real-image acceptance is not complete.",
        "scenarios": "For edge-preserving smoothing; small structures may weaken.",
    },
    "wls_smooth": {
        "title": "Weighted least-squares smoothing",
        "summary": "Solves an RGB least-squares smooth weighted by log-luminance proxy gradients; "
        "errors out when unconverged.",
        "principle": "Builds edge weights and solves with conjugate gradients; raises when "
        "unconverged. Experimental; real-image acceptance is not complete.",
        "scenarios": "For separating slow structure from detail; large images may be slow to "
        "solve.",
    },
    "chroma_transfer": {
        "title": "Reference chroma statistics transfer",
        "summary": "Transfers reference-image statistics in linear-RGB opponent channels while "
        "keeping relative luminance, not Lab perceptual lightness.",
        "principle": "Matches chroma statistics with alpha-weighted means and standard "
        "deviations, optionally keeping relative luminance. Experimental; real-image acceptance "
        "is not complete.",
        "scenarios": "For transferring a reference palette; relative luminance is not Lab "
        "perceptual lightness.",
    },
    "像素化": {
        "title": "Pixelate",
        "summary": "Sequence: block-average downsampling and nearest-neighbor expansion.",
        "principle": "Downsamples with BOX and restores the original size with nearest-neighbor.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Replaces local variation with large color blocks.",
        "loss": "Spatial detail cannot be recovered; this is not a guarantee that sensitive "
        "information has been safely anonymized.",
    },
    "调色板": {
        "title": "Palette",
        "summary": "Sequence: palette quantization.",
        "principle": "Maps RGB to a limited palette, optionally with error diffusion.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Reduces the number of colors used.",
        "loss": "Original colors and gradient levels are lost; banding or grain may appear.",
    },
    "线稿": {
        "title": "Line art",
        "summary": "Sequence: grayscale conversion, histogram endpoint stretch, high-pass "
        "residual extraction.",
        "principle": "Converts color channels to a single luminance proxy. Remaps the pixel range "
        "by histogram endpoints. Subtracts a low-pass component and maps the gain.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Removes color differences to work on tonal structure. Expands the tonal "
        "range. Emphasizes local luminance change.",
        "loss": "Color information is lost. Noise and tiny luminance differences may be "
        "amplified. Slow tonal structure weakens and noise may increase.",
    },
    "抖动": {
        "title": "Dither",
        "summary": "Sequence: quantization error diffusion.",
        "principle": "Distributes quantization error to neighboring pixels using the chosen rule.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Approximates continuous tones with a spatial dot pattern.",
        "loss": "Adds dotted texture; tiny structures may alias with the pattern.",
    },
    "锐化": {
        "title": "Sharpen",
        "summary": "Sequence: high-frequency residual enhancement.",
        "principle": "Enhances high-frequency components through a blur difference.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Increases local edge contrast.",
        "loss": "Noise, halos and overshoot may increase.",
    },
    "赛璐璐": {
        "title": "Level quantization and contour overlay",
        "summary": "Sequence: level quantization and contour overlay.",
        "principle": "Blurs and quantizes color levels, then creates dark contours with a "
        "FIND_EDGES threshold and minimum filter.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Reduces tonal levels and strengthens edges.",
        "loss": "Gradients, small color differences and detail may be lost; posterization may "
        "appear.",
    },
    "去AI塑料感": {
        "title": "Local smoothing and highlight diffusion",
        "summary": "Sequence: local color-difference smoothing, endpoint and level adjustment, "
        "highlight extraction and blur blend.",
        "principle": "Weights by RGB distance between the original and a Gaussian blur, blending "
        "Gaussian and median results. Lifts blacks and compresses whites with a power LUT on "
        "encoded RGB, adjusting saturation and channel shift. Extracts highlights by luminance "
        "threshold, blurs and blends them, optionally limited by dark-line weights.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Reduces small local color variation. Changes tonal endpoints and level "
        "distribution. Widens the bright transition around highlights.",
        "loss": "Low-contrast fine lines, texture and small color areas may be smoothed. Original "
        "contrast and color relationships change, and clipped information cannot be recovered. "
        "Local contrast may drop and fine lines may fade.",
    },
    "抗AI生成伪影综合治理": {
        "title": "High-frequency attenuation and local smoothing",
        "summary": "Sequence: high-frequency region attenuation, local median deviation "
        "compression, local color-difference smoothing, per-channel blur blend, endpoint and "
        "level adjustment.",
        "principle": "Applies FFT per RGB channel and attenuates selected radial/axial frequency "
        "regions before blending back; the artifact source is not detected. Compresses positive "
        "and negative deviations from the local median per channel. Weights by RGB distance "
        "between the original and a Gaussian blur, blending Gaussian and median results. Blurs "
        "RGB channels at different radii and blends back, optionally weighted by skin tone; it "
        "does not solve subsurface light transport. Lifts blacks and compresses whites with a "
        "power LUT on encoded RGB, adjusting saturation and channel shift.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Reduces periodic variation in the chosen band. Reduces isolated tonal "
        "overshoot. Reduces small local color variation. Weakens local tonal and color change. "
        "Changes tonal endpoints and level distribution.",
        "loss": "Screentone, line patterns and real texture at the same frequencies also "
        "attenuate. Real highlights, small text and fine lines may weaken. Low-contrast fine "
        "lines, texture and small color areas may be smoothed. Detail may blur and the color mask "
        "may select the wrong areas. Original contrast and color relationships change, and "
        "clipped information cannot be recovered.",
    },
    "二次元平涂去杂": {
        "title": "Local smoothing and shadow desaturation",
        "summary": "Sequence: local color-difference smoothing, shadow desaturation and chroma "
        "gain.",
        "principle": "Weights by RGB distance between the original and a Gaussian blur, blending "
        "Gaussian and median results. Builds a shadow mask from encoded luminance and blends "
        "toward gray; adjusts non-shadow chroma by low-saturation weight.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Reduces small local color variation. Lowers shadow saturation and changes "
        "color strength elsewhere.",
        "loss": "Low-contrast fine lines, texture and small color areas may be smoothed. "
        "Intentional shadow color may be lost and strong gain may clip channels.",
    },
    "动漫色彩提纯": {
        "title": "Shadow desaturation and chroma gain",
        "summary": "Sequence: shadow desaturation and chroma gain.",
        "principle": "Builds a shadow mask from encoded luminance and blends toward gray; adjusts "
        "non-shadow chroma by low-saturation weight.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Lowers shadow saturation and changes color strength elsewhere.",
        "loss": "Intentional shadow color may be lost and strong gain may clip channels.",
    },
    "anti_checkerboard": {
        "title": "Selected high-frequency attenuation",
        "summary": "Applies FFT per RGB channel, attenuates selected radial/axial frequency "
        "regions and blends back; the artifact source is not detected.",
        "principle": "Applies FFT per RGB channel, attenuates selected radial/axial frequency "
        "regions and blends back; the artifact source is not detected.",
        "scenarios": "May provide: reduces periodic variation in the chosen band. May lose: "
        "screentone, line patterns and real texture at the same frequencies also attenuate.",
        "benefit": "Reduces periodic variation in the chosen band.",
        "loss": "Screentone, line patterns and real texture at the same frequencies also "
        "attenuate.",
    },
    "subsurface_scattering": {
        "title": "Per-channel blur blend",
        "summary": "Blurs RGB channels at different radii and blends back, optionally weighted by "
        "skin tone; it does not solve subsurface light transport.",
        "principle": "Blurs RGB channels at different radii and blends back, optionally weighted "
        "by skin tone; it does not solve subsurface light transport.",
        "scenarios": "May provide: weakens local tonal and color change. May lose: detail may "
        "blur and the color mask may select the wrong areas.",
        "benefit": "Weakens local tonal and color change.",
        "loss": "Detail may blur and the color mask may select the wrong areas.",
    },
    "de_haloing": {
        "title": "Local median deviation compression",
        "summary": "Compresses positive and negative deviations from the local median per channel.",
        "principle": "Compresses positive and negative deviations from the local median per "
        "channel.",
        "scenarios": "May provide: reduces isolated tonal overshoot. May lose: real highlights, "
        "small text and fine lines may weaken.",
        "benefit": "Reduces isolated tonal overshoot.",
        "loss": "Real highlights, small text and fine lines may weaken.",
    },
    "film_grain": {
        "title": "Luminance-weighted noise overlay",
        "summary": "Adds fixed-seed noise weighted by luminance; not calibrated to real film or "
        "sensors.",
        "principle": "Adds fixed-seed noise weighted by luminance; not calibrated to real film or "
        "sensors.",
        "scenarios": "May provide: adds pixel-level random variation. May lose: flat areas are no "
        "longer uniform and signal-to-noise ratio may drop.",
        "benefit": "Adds pixel-level random variation.",
        "loss": "Flat areas are no longer uniform and signal-to-noise ratio may drop.",
    },
    "de_ai_bloom": {
        "title": "Highlight extraction and blur blend",
        "summary": "Extracts highlights by luminance threshold, blurs and blends them, optionally "
        "limited by dark-line weights.",
        "principle": "Extracts highlights by luminance threshold, blurs and blends them, "
        "optionally limited by dark-line weights.",
        "scenarios": "May provide: widens the bright transition around highlights. May lose: "
        "local contrast may drop and fine lines may fade.",
        "benefit": "Widens the bright transition around highlights.",
        "loss": "Local contrast may drop and fine lines may fade.",
    },
    "filmic_tone": {
        "title": "Endpoint and level adjustment",
        "summary": "Lifts blacks and compresses whites with a power LUT on encoded RGB, adjusting "
        "saturation and channel shift.",
        "principle": "Lifts blacks and compresses whites with a power LUT on encoded RGB, "
        "adjusting saturation and channel shift.",
        "scenarios": "May provide: changes tonal endpoints and level distribution. May lose: "
        "original contrast and color relationships change, and clipped information cannot be "
        "recovered.",
        "benefit": "Changes tonal endpoints and level distribution.",
        "loss": "Original contrast and color relationships change, and clipped information cannot "
        "be recovered.",
    },
    "chromatic_aberration": {
        "title": "Lateral chromatic aberration",
        "summary": "Displaces the red and blue channels radially to simulate lateral dispersion "
        "from lens refractive index varying with wavelength.",
        "principle": "Uses a physical lens-dispersion model and bilinearly resamples color "
        "channels radially around the viewport center.",
        "scenarios": "Breaks the rigid geometry of perfect computer imagery and gives the frame "
        "the edge behavior of a real wide-angle lens.",
    },
    "ACES电影级色调": {
        "title": "ACES Filmic Tone",
        "summary": "Maps high-dynamic-range radiance to a standard display color space using the "
        "Academy color encoding system.",
        "principle": "Uses the ACES S-curve rational polynomial fit from Krzysztof Narkowicz and "
        "Stephen Hill (SIGGRAPH 2015).",
        "scenarios": "For high-contrast scenes, complex backlight and emissive objects, with a "
        "smooth highlight shoulder and gradual shadow transition.",
    },
    "引导滤波细节增强": {
        "title": "Guided Filter Detail",
        "summary": "Uses the guidance image's structural prior for edge-preserving smoothing and "
        "high-frequency residual boost without ringing.",
        "principle": "Based on the local linear translation-invariant model of He et al. (ECCV "
        "2010 / IEEE TPAMI 2013) to separate and strengthen micro-texture.",
        "scenarios": "For restoring hair detail, skin pores and fabric texture without an "
        "over-smoothed look.",
    },
    "桑原二次元平涂": {
        "title": "Kuwahara Filter",
        "summary": "Smooths interior color detail while keeping major contours sharp, producing a "
        "clean flat-shaded illustration look.",
        "principle": "Uses the four-quadrant minimum-variance criterion from Papari et al. (IEEE "
        "TIP 2007) for edge-preserving anisotropic smoothing.",
        "scenarios": "For flat anime backgrounds, discretizing painterly strokes and removing "
        "high-frequency speckle.",
    },
    "剧场版宽银幕眩光": {
        "title": "Anamorphic Flare",
        "summary": "Extracts highlights by threshold and stretches them horizontally with a 1D "
        "kernel to simulate anamorphic widescreen streaks.",
        "principle": "Uses an asymmetric long horizontal Gaussian kernel and thresholded energy "
        "extraction to form tinted diffraction streaks.",
        "scenarios": "For science-fiction scenes, strong backlight portraits, night light sources "
        "and stage lighting.",
    },
    "日系晴空色彩迁移": {
        "title": "Color Transfer",
        "summary": "Globally transfers a reference image's color style by matching means and "
        "variances in a decorrelated color space.",
        "principle": "Follows Reinhard et al. (IEEE CG&A 2001), applying first- and second-moment "
        "linear mapping in CIE-Lab.",
        "scenarios": "For brightening gloomy images and quickly giving them a clear, airy palette.",
    },
    "日系通透空气感": {
        "title": "Channel grading, highlight diffusion and edge darkening",
        "summary": "Sequence: channel temperature and tint, highlight extraction and blur blend, "
        "radial edge darkening.",
        "principle": "Adjusts warm/cool and green/magenta with RGB channel LUTs without "
        "estimating the illuminant spectrum. Extracts highlights by luminance threshold, blurs "
        "and blends them, optionally limited by dark-line weights. Builds a gradient luminance "
        "mask from radial distance to the center.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Changes overall channel balance. Widens the bright transition around "
        "highlights. Lowers edge luminance.",
        "loss": "The original palette shifts and strong adjustments may clip channels. Local "
        "contrast may drop and fine lines may fade. Edge subjects and shadow detail also "
        "darken.",
    },
    "热血集中线": {
        "title": "Radial line overlay",
        "summary": "Sequence: radial line overlay.",
        "principle": "Generates angle and width jitter from a fixed seed and overlays triangular "
        "lines outside the center.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Adds lines that point toward the center.",
        "loss": "The original background may be covered and the composition changes.",
    },
    "focus_lines": {
        "title": "Radial line overlay",
        "summary": "Generates angle and width jitter from a fixed seed and overlays triangular "
        "lines outside the center.",
        "principle": "Generates angle and width jitter from a fixed seed and overlays triangular "
        "lines outside the center.",
        "scenarios": "May provide: adds lines that point toward the center. May lose: the "
        "original background may be covered and the composition changes.",
        "benefit": "Adds lines that point toward the center.",
        "loss": "The original background may be covered and the composition changes.",
    },
    "黑白漫画网点": {
        "title": "Luminance to screentone",
        "summary": "Sequence: luminance to screentone.",
        "principle": "Generates a discrete dot grid from luminance and the configured dot "
        "frequency and angle.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Turns continuous luminance into dot density or size variation.",
        "loss": "Color and continuous tone may be lost; scaling may cause moiré.",
    },
    "screentone": {
        "title": "Luminance to screentone",
        "summary": "Generates a discrete dot grid from luminance and the configured dot frequency "
        "and angle.",
        "principle": "Generates a discrete dot grid from luminance and the configured dot "
        "frequency and angle.",
        "scenarios": "May provide: turns continuous luminance into dot density or size variation. "
        "May lose: color and continuous tone may be lost and scaling may cause moiré.",
        "benefit": "Turns continuous luminance into dot density or size variation.",
        "loss": "Color and continuous tone may be lost and scaling may cause moiré.",
    },
    "立绘贴纸白边": {
        "title": "Transparent outline and shadow",
        "summary": "Sequence: transparent outline expansion and shadow.",
        "principle": "Expands the alpha contour, overlays an outline in the chosen color, and can "
        "add a drop shadow.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Separates the visible contour from the background.",
        "loss": "The original transparent boundary changes and small gaps may fill in.",
    },
    "sticker_outline": {
        "title": "Transparent outline and shadow",
        "summary": "Expands the alpha contour, overlays an outline in the chosen color, and can "
        "add a drop shadow.",
        "principle": "Expands the alpha contour, overlays an outline in the chosen color, and can "
        "add a drop shadow.",
        "scenarios": "May provide: separates the visible contour from the background. May lose: "
        "the original transparent boundary changes and small gaps may fill in.",
        "benefit": "Separates the visible contour from the background.",
        "loss": "The original transparent boundary changes and small gaps may fill in.",
    },
    "XDoG动漫线稿": {
        "title": "XDoG Lineart",
        "summary": "Subtracts Gaussian kernels of different variance and applies a non-linear "
        "threshold to extract continuous pencil-like line art.",
        "principle": "Based on the XDoG operator of Winnemöller et al. (2012), combining "
        "dual-scale difference-of-Gaussians with a non-linear step response.",
        "scenarios": "For extracting clean hand-drawn line structure from complex color images "
        "and preparing flat color bases.",
    },
    "lineart_extract": {
        "title": "Difference-of-Gaussians line extraction",
        "summary": "Extracts tonal edges with Gaussian differences at different scales and a "
        "non-linear threshold.",
        "principle": "Extracts tonal edges with Gaussian differences at different scales and a "
        "non-linear threshold.",
        "scenarios": "May provide: turns some luminance variation into lines. May lose: color and "
        "continuous tone are lost, and noise may become lines.",
        "benefit": "Turns some luminance variation into lines.",
        "loss": "Color and continuous tone are lost, and noise may become lines.",
    },
    "复古90年代胶片": {
        "title": "Level compression, edge darkening and grain",
        "summary": "Sequence: endpoint and level adjustment, radial edge darkening, "
        "luminance-weighted grain.",
        "principle": "Lifts blacks and compresses whites with a power LUT on encoded RGB, "
        "adjusting saturation and channel shift. Builds a gradient luminance mask from radial "
        "distance to the center. Adds fixed-seed noise weighted by luminance; not calibrated to "
        "real film or sensors.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Changes tonal endpoints and level distribution. Lowers edge luminance. Adds "
        "pixel-level random variation.",
        "loss": "Original contrast and color relationships change, and clipped information cannot "
        "be recovered. Edge subjects and shadow detail also darken. Flat areas are no longer "
        "uniform and signal-to-noise ratio may drop.",
    },
    "动漫超分辨率 4x": {
        "title": "4× illustration model upscaling",
        "summary": "Sequence: model inference upscaling.",
        "principle": "Runs tiled inference with the selected model and composes the result; model "
        "and backend identities are recorded in artifact provenance.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Increases pixel size and may produce crisper edges or texture.",
        "loss": "May generate detail absent from the original, alter lines or create seams; this "
        "is not guaranteed recovery of real detail.",
    },
    "通用超分辨率 4x": {
        "title": "4× general model upscaling",
        "summary": "Sequence: model inference upscaling.",
        "principle": "Runs tiled inference with the selected model and composes the result; model "
        "and backend identities are recorded in artifact provenance.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Increases pixel size and may produce crisper edges or texture.",
        "loss": "May generate detail absent from the original, alter lines or create seams; this "
        "is not guaranteed recovery of real detail.",
    },
    "日系治愈空气柔光": {
        "title": "Highlight diffusion, vibrance and line tint",
        "summary": "Sequence: highlight blur and screen blend, saturation-weighted color "
        "adjustment, dark-line color blend.",
        "principle": "Extracts luminance above the threshold, blurs with a Gaussian, blends with "
        "Screen and mixes back. Adjusts relative channel differences by pixel color and "
        "saturation weight. Blends contour color with local color by shadow weight.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Expands bright areas and lifts nearby luminance. Changes color strength by "
        "saturation. Reduces color contrast between dark lines and nearby color areas.",
        "loss": "Local contrast and highlight separation may drop. The palette may shift and "
        "strong adjustments may clip gamut. Dark-line legibility may drop, and text and "
        "intersections may fade.",
    },
    "大魄力动感鱼眼": {
        "title": "Radial distortion and line overlay",
        "summary": "Sequence: radial coordinate resampling, radial line overlay.",
        "principle": "Resamples pixels by radial mapping and adjusts the visible range with a zoom "
        "parameter. Generates angle and width jitter from a fixed seed and overlays triangular "
        "lines outside the center.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Changes the relative scale of center and edges. Adds lines that point toward "
        "the center.",
        "loss": "Straight lines and proportions distort, and resampling or cropping may lose edge "
        "content. The original background may be covered and the composition changes.",
    },
    "橙青影视色彩平衡": {
        "title": "Split toning, level compression and grain",
        "summary": "Sequence: per-tone channel offset, endpoint and level adjustment, "
        "luminance-weighted grain.",
        "principle": "Adjusts RGB channels by shadow, midtone and highlight weights. Lifts blacks "
        "and compresses whites with a power LUT on encoded RGB, adjusting saturation and channel "
        "shift. Adds fixed-seed noise weighted by luminance; not calibrated to real film or "
        "sensors.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Changes color shift separately by tonal region. Changes tonal endpoints and "
        "level distribution. Adds pixel-level random variation.",
        "loss": "Original color relationships change and strong offsets may clip channels. "
        "Original contrast and color relationships change, and clipped information cannot be "
        "recovered. Flat areas are no longer uniform and signal-to-noise ratio may drop.",
    },
    "line_color": {
        "title": "Dark-line color blend",
        "summary": "Blends contour color with local color by shadow weight.",
        "principle": "Blends contour color with local color by shadow weight.",
        "scenarios": "May provide: reduces color contrast between dark lines and nearby color "
        "areas. May lose: dark-line legibility may drop, and text and intersections may fade.",
        "benefit": "Reduces color contrast between dark lines and nearby color areas.",
        "loss": "Dark-line legibility may drop, and text and intersections may fade.",
    },
    "color_balance": {
        "title": "Per-tone channel offset",
        "summary": "Adjusts RGB channels by shadow, midtone and highlight weights.",
        "principle": "Adjusts RGB channels by shadow, midtone and highlight weights.",
        "scenarios": "May provide: changes color shift separately by tonal region. May lose: "
        "original color relationships change and strong offsets may clip channels.",
        "benefit": "Changes color shift separately by tonal region.",
        "loss": "Original color relationships change and strong offsets may clip channels.",
    },
    "fisheye": {
        "title": "Radial coordinate resampling",
        "summary": "Resamples pixels by radial mapping and adjusts the visible range with a zoom "
        "parameter.",
        "principle": "Resamples pixels by radial mapping and adjusts the visible range with a zoom "
        "parameter.",
        "scenarios": "May provide: changes the relative scale of center and edges. May lose: "
        "straight lines and proportions distort, and resampling or cropping may lose edge "
        "content.",
        "benefit": "Changes the relative scale of center and edges.",
        "loss": "Straight lines and proportions distort, and resampling or cropping may lose edge "
        "content.",
    },
    "vibrance": {
        "title": "Saturation-weighted color adjustment",
        "summary": "Adjusts relative channel differences by pixel color and saturation weight.",
        "principle": "Adjusts relative channel differences by pixel color and saturation weight.",
        "scenarios": "May provide: changes color strength by saturation. May lose: the palette may "
        "shift and strong adjustments may clip gamut.",
        "benefit": "Changes color strength by saturation.",
        "loss": "The palette may shift and strong adjustments may clip gamut.",
    },
    "diffuse_glow": {
        "title": "Highlight blur and screen blend",
        "summary": "Extracts luminance above the threshold, blurs with a Gaussian, blends with "
        "Screen and mixes back.",
        "principle": "Extracts luminance above the threshold, blurs with a Gaussian, blends with "
        "Screen and mixes back.",
        "scenarios": "May provide: expands bright areas and lifts nearby luminance. May lose: "
        "local contrast and highlight separation may drop.",
        "benefit": "Expands bright areas and lifts nearby luminance.",
        "loss": "Local contrast and highlight separation may drop.",
    },
    "vignette": {
        "title": "Radial edge darkening",
        "summary": "Builds a gradient luminance mask from radial distance to the center.",
        "principle": "Builds a gradient luminance mask from radial distance to the center.",
        "scenarios": "May provide: lowers edge luminance. May lose: edge subjects and shadow "
        "detail also darken.",
        "benefit": "Lowers edge luminance.",
        "loss": "Edge subjects and shadow detail also darken.",
    },
    "color_temperature": {
        "title": "Channel temperature and tint",
        "summary": "Adjusts warm/cool and green/magenta with RGB channel LUTs without estimating "
        "the illuminant spectrum.",
        "principle": "Adjusts warm/cool and green/magenta with RGB channel LUTs without "
        "estimating the illuminant spectrum.",
        "scenarios": "May provide: changes overall channel balance. May lose: the palette shifts "
        "and strong adjustments may clip channels.",
        "benefit": "Changes overall channel balance.",
        "loss": "The palette shifts and strong adjustments may clip channels.",
    },
    "superres": {
        "title": "Model inference upscaling",
        "summary": "Runs tiled inference with the selected model and composes the result; model "
        "and backend identities are recorded in artifact provenance.",
        "principle": "Runs tiled inference with the selected model and composes the result; model "
        "and backend identities are recorded in artifact provenance.",
        "scenarios": "May provide: increases pixel size and may produce crisper edges or texture. "
        "May lose: may generate detail absent from the original, alter lines or create seams; "
        "not guaranteed recovery of real detail.",
        "benefit": "Increases pixel size and may produce crisper edges or texture.",
        "loss": "May generate detail absent from the original, alter lines or create seams; not "
        "guaranteed recovery of real detail.",
    },
    "二次元线条强化与描墨": {
        "title": "Dark-line extraction and deepening",
        "summary": "Sequence: dark-line extraction and deepening.",
        "principle": "Builds weights with isotropic DoG, square closing and percentile "
        "normalization, then darkens the original multiplicatively.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Increases contrast in dark contours.",
        "loss": "Adjacent lines may join, and small gaps and tonal detail may be lost.",
    },
    "二次元赛璐珞顶级精修": {
        "title": "Local smoothing, line deepening and desaturation",
        "summary": "Sequence: local color-difference smoothing, dark-line extraction and "
        "deepening, shadow desaturation and chroma gain.",
        "principle": "Weights by RGB distance between the original and a Gaussian blur, blending "
        "Gaussian and median results. Builds weights with isotropic DoG, square closing and "
        "percentile normalization, then darkens the original multiplicatively. Builds a shadow "
        "mask from encoded luminance and blends toward gray; adjusts non-shadow chroma by "
        "low-saturation weight.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Reduces small local color variation. Increases contrast in dark contours. "
        "Lowers shadow saturation and changes color strength elsewhere.",
        "loss": "Low-contrast fine lines, texture and small color areas may be smoothed. Adjacent "
        "lines may join, and small gaps and tonal detail may be lost. Intentional shadow color "
        "may be lost and strong gain may clip channels.",
    },
    "anime_line_enhance": {
        "title": "Dark-line extraction and deepening",
        "summary": "Builds weights with isotropic DoG, square closing and percentile "
        "normalization, then darkens the original multiplicatively.",
        "principle": "Builds weights with isotropic DoG, square closing and percentile "
        "normalization, then darkens the original multiplicatively.",
        "scenarios": "May provide: increases contrast in dark contours. May lose: adjacent lines "
        "may join, and small gaps and tonal detail may be lost.",
        "benefit": "Increases contrast in dark contours.",
        "loss": "Adjacent lines may join, and small gaps and tonal detail may be lost.",
    },
    "guided_smooth": {
        "title": "Guided edge-preserving smoothing",
        "summary": "Smooths encoded sRGB colors with a local linear guided model, then blends with "
        "the original; fine lines may weaken.",
        "principle": "Performs local linear regression in encoded sRGB; epsilon is in squared "
        "units of normalized guidance. Experimental; real-image acceptance is not complete.",
        "scenarios": "For smoothing flat color areas; low-contrast fine lines may weaken.",
    },
    "rolling_guidance": {
        "title": "Iterative guided texture smoothing",
        "summary": "Iteratively applies joint bilateral guidance after an initial smooth to "
        "reduce fine texture; small structures may disappear.",
        "principle": "Applies an initial scale suppression, then iterative joint bilateral "
        "guidance; this is not repeated plain bilateral filtering. Experimental; real-image "
        "acceptance is not complete.",
        "scenarios": "For reducing fine texture; small text and line patterns may also be erased.",
    },
    "flow_line": {
        "title": "Flow-guided line extraction",
        "summary": "Builds an ETF tangent field, applies normal-direction DoG and tangent "
        "integration, and outputs lines or blends them back.",
        "principle": "Smooths an ETF tangent field, computes DoG in the normal direction and "
        "integrates along the tangent; lines output is a standalone black-line-on-white image. "
        "Experimental; real-image acceptance is not complete.",
        "scenarios": "For extracting or deepening contours; check intersections, thin lines and "
        "line endings.",
    },
    "kuwahara_anisotropic": {
        "title": "Directional color-area smoothing",
        "summary": "Uses a structure tensor to choose a direction, then weights directional "
        "sector statistics; detail shapes may change.",
        "principle": "Uses the structure tensor to choose ellipse orientation and elongation, "
        "then weights sector means and variances. Experimental; real-image acceptance is not "
        "complete.",
        "scenarios": "For directional color-area abstraction; shapes of small structures may "
        "change.",
    },
    "bounded_sharpen": {
        "title": "Bounded luminance sharpening",
        "summary": "Enhances a luminance proxy residual with a local overshoot limit; may clip "
        "genuine highlights.",
        "principle": "Applies a noise gate to high-frequency residuals of an encoded sRGB "
        "luminance proxy and constrains local overshoot. Experimental; real-image acceptance is "
        "not complete.",
        "scenarios": "For controlled sharpening; may weaken genuine highlights or amplify texture.",
    },
    "multiscale_detail": {
        "title": "Scale-adjusted detail",
        "summary": "Adjusts fine, medium and coarse luminance residual gains; 1 leaves them "
        "unchanged and boosts may amplify noise.",
        "principle": "Decomposes a luminance proxy into multi-scale Gaussian residuals; a gain of "
        "1 leaves that layer unchanged. Experimental; real-image acceptance is not complete.",
        "scenarios": "For per-scale contrast adjustment; boosting high frequencies may also "
        "amplify noise.",
    },
    "linear_resize": {
        "title": "Linear-light alpha resize",
        "summary": "Premultiplies alpha in linear sRGB before resizing. Scale and size are "
        "mutually exclusive; the catalog default scale is 1.",
        "principle": "Decodes to linear sRGB, premultiplies alpha, resamples in floating point, "
        "then unpremultiplies and encodes. Experimental; real-image acceptance is not complete.",
        "scenarios": "For resizing transparent edges; scale and size are mutually exclusive and "
        "this is not learned detail restoration.",
    },
    "quantize": {
        "title": "Palette quantization",
        "summary": "Maps RGB to a limited palette, optionally with error diffusion.",
        "principle": "Maps RGB to a limited palette, optionally with error diffusion.",
        "scenarios": "May provide: reduces the number of colors used. May lose: original colors "
        "and gradient levels are lost; banding or grain may appear.",
        "benefit": "Reduces the number of colors used.",
        "loss": "Original colors and gradient levels are lost; banding or grain may appear.",
    },
    "dither": {
        "title": "Quantization error diffusion",
        "summary": "Distributes quantization error to neighboring pixels using the chosen rule.",
        "principle": "Distributes quantization error to neighboring pixels using the chosen rule.",
        "scenarios": "May provide: approximates continuous tones with a spatial dot pattern. May "
        "lose: adds dotted texture and tiny structures may alias with the pattern.",
        "benefit": "Approximates continuous tones with a spatial dot pattern.",
        "loss": "Adds dotted texture and tiny structures may alias with the pattern.",
    },
    "sharpen": {
        "title": "High-frequency residual enhancement",
        "summary": "Enhances high-frequency components through a blur difference.",
        "principle": "Enhances high-frequency components through a blur difference.",
        "scenarios": "May provide: increases local edge contrast. May lose: noise, halos and "
        "overshoot may increase.",
        "benefit": "Increases local edge contrast.",
        "loss": "Noise, halos and overshoot may increase.",
    },
    "grayscale": {
        "title": "Grayscale conversion",
        "summary": "Converts color channels to a single luminance proxy.",
        "principle": "Converts color channels to a single luminance proxy.",
        "scenarios": "May provide: removes color differences to work on tonal structure. May "
        "lose: color information.",
        "benefit": "Removes color differences to work on tonal structure.",
        "loss": "Color information is lost.",
    },
    "autocontrast": {
        "title": "Histogram endpoint stretch",
        "summary": "Remaps the pixel range by histogram endpoints.",
        "principle": "Remaps the pixel range by histogram endpoints.",
        "scenarios": "May provide: expands the tonal range. May lose: noise and tiny luminance "
        "differences may be amplified.",
        "benefit": "Expands the tonal range.",
        "loss": "Noise and tiny luminance differences may be amplified.",
    },
    "highpass": {
        "title": "High-frequency residual extraction",
        "summary": "Subtracts a low-pass component from the original and maps the gain.",
        "principle": "Subtracts a low-pass component from the original and maps the gain.",
        "scenarios": "May provide: emphasizes local luminance change. May lose: slow tonal "
        "structure weakens and noise may increase.",
        "benefit": "Emphasizes local luminance change.",
        "loss": "Slow tonal structure weakens and noise may increase.",
    },
    "surface_blur": {
        "title": "Local color-difference smoothing",
        "summary": "Weights by RGB distance between the original and a Gaussian blur, blending "
        "Gaussian and median results.",
        "principle": "Weights by RGB distance between the original and a Gaussian blur, blending "
        "Gaussian and median results.",
        "scenarios": "May provide: reduces small local color variation. May lose: low-contrast "
        "fine lines, texture and small color areas may be smoothed.",
        "benefit": "Reduces small local color variation.",
        "loss": "Low-contrast fine lines, texture and small color areas may be smoothed.",
    },
    "color_purity": {
        "title": "Shadow desaturation and chroma gain",
        "summary": "Builds a shadow mask from encoded luminance and blends toward gray; adjusts "
        "non-shadow chroma by low-saturation weight.",
        "principle": "Builds a shadow mask from encoded luminance and blends toward gray; adjusts "
        "non-shadow chroma by low-saturation weight.",
        "scenarios": "May provide: lowers shadow saturation and changes color strength "
        "elsewhere. May lose: intentional shadow color may be lost and strong gain may clip "
        "channels.",
        "benefit": "Lowers shadow saturation and changes color strength elsewhere.",
        "loss": "Intentional shadow color may be lost and strong gain may clip channels.",
    },
    "cel_shade": {
        "title": "Level quantization and contour overlay",
        "summary": "Blurs and quantizes color levels, then creates dark contours with a FIND_EDGES "
        "threshold and minimum filter.",
        "principle": "Blurs and quantizes color levels, then creates dark contours with a "
        "FIND_EDGES threshold and minimum filter.",
        "scenarios": "May provide: reduces tonal levels and strengthens edges. May lose: "
        "gradients, small color differences and detail may be lost, and posterization may "
        "appear.",
        "benefit": "Reduces tonal levels and strengthens edges.",
        "loss": "Gradients, small color differences and detail may be lost, and posterization "
        "may appear.",
    },
    "pixelate": {
        "title": "Block-average downsampling and nearest expansion",
        "summary": "Downsamples with BOX and restores the original size with nearest-neighbor.",
        "principle": "Downsamples with BOX and restores the original size with nearest-neighbor.",
        "scenarios": "May provide: replaces local variation with large color blocks. May lose: "
        "spatial detail cannot be recovered; this is not a guarantee that sensitive information "
        "has been safely anonymized.",
        "benefit": "Replaces local variation with large color blocks.",
        "loss": "Spatial detail cannot be recovered; this is not a guarantee that sensitive "
        "information has been safely anonymized.",
    },
    "赛璐璐手绘": {
        "title": "Cel Shade",
        "summary": "Sequence: cel shading.",
        "principle": "Quantizes color levels and overlays dark contours for a hand-drawn cel look.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Reduces tonal levels and strengthens edges.",
        "loss": "Gradients, small color differences and detail may be lost.",
    },
    "像素复古风": {
        "title": "Pixel Art",
        "summary": "Sequence: pixelation.",
        "principle": "Downsamples into blocks and expands with nearest-neighbor for a pixel-art "
        "look.",
        "scenarios": "Steps compound; results depend on input and parameters and are not a "
        "quality guarantee.",
        "benefit": "Replaces local variation with large color blocks.",
        "loss": "Spatial detail cannot be recovered.",
    },
}

ALIASES = {
    "去AI塑料感 (Anti-AI Organic)": "去AI塑料感",
    "抗AI生成伪影综合治理 (Anti-AI Artifacts Suppression)": "抗AI生成伪影综合治理",
    "二次元平涂去杂 (Anime Cel Surface Smoothing)": "二次元平涂去杂",
    "动漫色彩提纯 (Anime Color Purity)": "动漫色彩提纯",
    "二次元线条强化与描墨 (Anime Line Inking)": "二次元线条强化与描墨",
    "二次元赛璐珞顶级精修 (Anime Cel Master)": "二次元赛璐珞顶级精修",
    "日系通透空气感 (Shinkai Airy)": "日系通透空气感",
    "热血集中线 (Manga Action Focus)": "热血集中线",
    "黑白漫画网点 (Manga Screentone)": "黑白漫画网点",
    "立绘贴纸白边 (Die-cut Sticker)": "立绘贴纸白边",
    "XDoG动漫手绘线稿 (Line Art)": "lineart_extract",
    "XDoG动漫手绘线稿": "lineart_extract",
    "复古90年代胶片 (Retro 90s Film)": "复古90年代胶片",
    "赛璐璐手绘 (Cel Shade)": "赛璐璐手绘",
    "像素复古风 (Pixel Art)": "像素复古风",
    "动漫超分辨率 4x (Anime SR)": "动漫超分辨率 4x",
    "通用超分辨率 4x (General SR)": "通用超分辨率 4x",
    "日系治愈空气柔光 (Anime Soft Glow)": "日系治愈空气柔光",
    "大魄力动感鱼眼 (Anime Fisheye)": "大魄力动感鱼眼",
    "橙青影视色彩平衡 (Teal & Orange)": "橙青影视色彩平衡",
    "aces_tonemap": "ACES电影级色调",
    "guided_filter": "引导滤波细节增强",
    "kuwahara": "桑原二次元平涂",
    "anamorphic_flare": "剧场版宽银幕眩光",
    "color_transfer": "日系晴空色彩迁移",
}


_USE_CASES = {
    "local_laplacian_sampled": (
        "Tune fine detail across scales, lowering or boosting small textures"
    ),
    "weighted_guided_smooth": (
        "Smooth color areas while keeping edges; "
        "useful for noise and compression artifacts"
    ),
    "wls_detail": "Adjust brightness detail by scale; cut haze or restore texture",
    "periodic_notch_auto": "Remove periodic interference such as screentones, fabric and grids",
    "fast_guided_smooth": "Quick large-area softening for a first look before fine tuning",
    "weighted_median": "Clean local speckles and impulse noise while keeping main edges",
    "rtv_smooth": "Reduce texture and skin noise while preserving main edges",
    "periodic_notch": "Suppress periodic artifacts at known frequencies",
    "illumination_correct": "Correct uneven lighting and vignetting",
    "l0_smooth": "Strong smoothing that keeps strong edges; useful for cleanup and flat color",
    "domain_transform": "Edge-aware recursive smoothing that lowers fine noise",
    "wls_smooth": "Edge-preserving smoothing that reduces fine texture",
    "chroma_transfer": "Transfer the overall color cast of a reference image",
    "像素化": "Pixel art, mosaic or low-resolution looks",
    "调色板": "Limit colors for retro or poster-like palettes",
    "线稿": "Extract contours for line art or manga drafts",
    "抖动": "Approximate color steps with dots for a printed look",
    "锐化": "Boost edges and detail for a crisper image",
    "赛璐璐": "Flatten color areas and add contours for a cel-shaded look",
    "去AI塑料感": "Reduce the AI-generated look; soften skin and highlights",
    "抗AI生成伪影综合治理": (
        "Reduce the AI-generated look; "
        "suppress high-frequency artifacts and plastic texture"
    ),
    "二次元平涂去杂": "Clean flat color and remove stray color and fine noise",
    "动漫色彩提纯": "Clean shadows and make colors more vivid",
    "anti_checkerboard": "Remove checkerboard and grid-like high-frequency artifacts",
    "subsurface_scattering": "Add soft translucency to skin or translucent materials",
    "de_haloing": "Reduce white halos from sharpening or HDR",
    "film_grain": "Add film grain to reduce the digital or generated look",
    "de_ai_bloom": "Diffuse highlights for a soft glow",
    "filmic_tone": "Add filmic blacks, whites and tonal roll-off",
    "chromatic_aberration": "Simulate lens-edge dispersion for a photographic feel",
    "ACES电影级色调": "Film-style highlight compression and shadow roll-off",
    "引导滤波细节增强": "Restore fine detail and texture; reduce over-smoothed areas",
    "桑原二次元平涂": "Flat anime color and color blocking; suppress photo detail",
    "剧场版宽银幕眩光": "Add horizontal anamorphic flare for a cinematic look",
    "日系晴空色彩迁移": "Transfer a bright, clear sky-like palette",
    "日系通透空气感": "Create a bright, airy Japanese-style look",
    "热血集中线": "Overlay radial action lines for manga impact",
    "focus_lines": "Overlay radial action lines for manga impact",
    "黑白漫画网点": "Turn light and shadow into manga screentones",
    "screentone": "Turn light and shadow into manga screentones",
    "立绘贴纸白边": "Add a white outline and shadow to a transparent cutout",
    "sticker_outline": "Add a white outline and shadow to a transparent cutout",
    "XDoG动漫线稿": "Extract anime line art with a hand-drawn feel",
    "lineart_extract": "Extract line art with a difference of Gaussians",
    "复古90年代胶片": "Add 90s film color shift, vignette and grain",
    "动漫超分辨率 4x": "Upscale illustrations 4× and recover lines and color areas",
    "通用超分辨率 4x": "Upscale photos or mixed images 4×",
    "日系治愈空气柔光": "Soft highlights, clear color and warm line art",
    "大魄力动感鱼眼": "Fisheye distortion and radial lines for a dynamic look",
    "橙青影视色彩平衡": "Teal-and-orange grading with film grain",
    "line_color": "Adjust dark line color so line art blends in",
    "color_balance": "Shift color per brightness range",
    "fisheye": "Radial fisheye distortion",
    "vibrance": "Raise saturation while protecting skin tones",
    "diffuse_glow": "Blur and screen highlights for a glow",
    "vignette": "Darken the edges to focus on the subject",
    "color_temperature": "Adjust warm/cool temperature and tint",
    "superres": "Upscale with model inference",
    "二次元线条强化与描墨": "Strengthen and darken anime lines",
    "二次元赛璐珞顶级精修": "Smooth, ink and desaturate for a finished cel look",
    "anime_line_enhance": "Strengthen anime lines and inking",
    "guided_smooth": "Edge-preserving smoothing that lowers noise and generated texture",
    "rolling_guidance": "Iterative texture smoothing to remove fine noise",
    "flow_line": "Trace lines along contours for a flowing line-art look",
    "kuwahara_anisotropic": "Directional color-area smoothing that keeps edges",
    "bounded_sharpen": "Sharpen with limited overshoot",
    "multiscale_detail": "Adjust detail by scale and control texture strength",
    "linear_resize": "Resize transparent edges in linear light",
    "quantize": "Reduce color count for posterization",
    "dither": "Use error diffusion to restore color steps",
    "sharpen": "Boost high frequencies for clearer edges",
    "grayscale": "Convert to grayscale",
    "autocontrast": "Stretch the histogram for more contrast",
    "highpass": "Extract high-frequency detail for sharpening or texture",
    "surface_blur": "Smooth local color by color distance to reduce stray color",
    "color_purity": "Desaturate shadows for purer color",
    "cel_shade": "Quantize levels and add contours for cel shading",
    "pixelate": "Blocky pixelation",
    "赛璐璐手绘": "Quantize levels and add contours for a hand-drawn cel look",
    "像素复古风": "Retro pixel-art look",
}
_NOTES = {
    "local_laplacian_sampled": [
        "Adjusts detail across scales",
        "Does not guarantee halo-free or hue-preserving output",
    ],
    "weighted_guided_smooth": [
        "Smooths color areas while protecting edges",
        "Halos and thin-line loss are still possible",
    ],
    "wls_detail": [
        "Controls brightness detail at each scale",
        "Strong gains can damage thin lines",
    ],
    "periodic_notch_auto": [
        "Finds and suppresses narrowband periodic noise",
        "Screentones, fabric and grids can be removed too",
    ],
    "fast_guided_smooth": ["Faster large-area smoothing", "May differ from the full filter"],
    "weighted_median": [
        "Cleans local speckles and impulses",
        "Strong outliers remain at narrow radii; colors can shift",
    ],
    "rtv_smooth": [
        "Reduces texture while keeping main edges",
        "Too few iterations or strong settings can erode edges",
    ],
    "periodic_notch": [
        "Targets known periodic artifacts",
        "Also removes intended patterns at that frequency",
    ],
    "illumination_correct": [
        "Evens out uneven lighting and vignetting",
        "Can flatten material shading and amplify noise",
    ],
    "l0_smooth": [
        "Strong smoothing with strong-edge preservation",
        "Thin lines and texture can merge",
    ],
    "domain_transform": [
        "Edge-aware smoothing with less fine noise",
        "Small structures can weaken",
    ],
    "wls_smooth": ["Separates slow structure from detail", "Large images can be slow to solve"],
    "chroma_transfer": [
        "Quickly transfers a reference palette",
        "Relative luminance is not perceptual Lab lightness",
    ],
    "chromatic_aberration": [
        "Adds realistic edge dispersion",
        "Edges can soften or show color fringes",
    ],
    "ACES电影级色调": [
        "Smooth highlight shoulder and shadow roll-off",
        "Changes overall contrast and color, so regrading is needed",
    ],
    "引导滤波细节增强": [
        "Restores hair, skin and fabric texture",
        "Too much amplification also boosts noise and sharpening artifacts",
    ],
    "桑原二次元平涂": [
        "Flattens backgrounds and thick brush strokes",
        "Small structures and high-frequency detail can disappear",
    ],
    "剧场版宽银幕眩光": [
        "Adds horizontal flare and a cinematic feel",
        "Bright areas can clip or show streaks",
    ],
    "日系晴空色彩迁移": [
        "Brightens dull images quickly",
        "Global color shift can affect skin tones and brand colors",
    ],
    "XDoG动漫线稿": [
        "Extracts clean line art from color images",
        "Complex textures can be read as lines",
    ],
    "guided_smooth": [
        "Smooths flat color while protecting edges",
        "Low-contrast thin lines can weaken",
    ],
    "rolling_guidance": ["Reduces fine texture", "Small text and hatching can be erased"],
    "flow_line": ["Traces lines along contours", "Crossings, thin lines and line ends can break"],
    "kuwahara_anisotropic": [
        "Directional color abstraction with edge retention",
        "Small structures can change shape",
    ],
    "bounded_sharpen": [
        "Controlled sharpening with less overshoot",
        "Real highlights can weaken and texture can amplify",
    ],
    "multiscale_detail": [
        "Adjusts contrast and detail by scale",
        "High-frequency boosts also amplify noise",
    ],
    "linear_resize": [
        "Cleaner transparent-edge resizing",
        "Not a learned detail recovery; scale and size are exclusive",
    ],
}
for _name, _text in _USE_CASES.items():
    INTROS[_name]["use_case"] = _text
for _name, _note in _NOTES.items():
    INTROS[_name]["benefit"], INTROS[_name]["loss"] = _note


def intro_en(key: str) -> dict[str, str] | None:
    """English intro text for a catalog key, or None when no translation exists."""
    target = ALIASES.get(key, key)
    entry = INTROS.get(target)
    if entry is None:
        entry = INTROS.get(target.split(" (", 1)[0])
    return entry
