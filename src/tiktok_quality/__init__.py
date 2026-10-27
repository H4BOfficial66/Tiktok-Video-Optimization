"""tiktok-quality 

Usage::

    from tiktok_quality import transform

    # Convert a single file
    stats = transform("input.mp4", "output.mp4", multiplier=10)

    # Python API with all options
    stats = transform(
        input_path="video.mp4",
        output_path="output.mp4",
        multiplier=10,
        comment="MyTag123",
    )
"""

__version__ = "1.1.0"

from .fps_patch import detect_fps, exact_fps, patch_fps, patch_fps_bytes
from .linkcheck import check_tiktok
from .transform import transform

__all__ = [
    "transform",
    "patch_fps",
    "patch_fps_bytes",
    "detect_fps",
    "exact_fps",
    "check_tiktok",
]
