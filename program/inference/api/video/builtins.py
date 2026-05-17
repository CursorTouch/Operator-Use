from program.inference.api.video.base import BaseVideoAPI
from program.inference.api.video.fal_video import FalVideoAPI

VIDEO_APIS: list[tuple[str, type[BaseVideoAPI]]] = [
    ("fal-video", FalVideoAPI),
]
