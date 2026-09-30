"""The extension use case: camera-based device troubleshooting (see `camera.py`)."""

from .camera import FrameDiagnoser, LatestFrame, diagnose_latest, rgba_to_png, summarise

__all__ = ["FrameDiagnoser", "LatestFrame", "diagnose_latest", "rgba_to_png", "summarise"]
