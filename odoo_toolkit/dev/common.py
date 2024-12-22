from enum import Enum
from pathlib import Path

from python_on_whales import DockerClient

DOCKER = DockerClient(compose_files=[Path(__file__).parent.parent / "docker" / "compose.yaml"])


class UbuntuVersion(str, Enum):
    """Ubuntu versions available as Docker images."""

    NOBLE = "noble"
    JAMMY = "jammy"
