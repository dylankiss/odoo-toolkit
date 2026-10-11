"""Build the development images from Runbot's Dockerfiles, for amd64 and arm64.

Every image is built per architecture and tagged `<repository>:<image>-<arch>`. With `--merge`, the per-architecture
images are combined into one multi-architecture tag per Odoo version, like `dylankiss/odoo-dev:19.0`, so Docker picks
the right architecture on every machine.

Usage:
    uv run scripts/build_images.py --arch arm64 --load           # Build every image for arm64 and load it locally
    uv run scripts/build_images.py --image Docker19 --arch amd64 --push
    uv run scripts/build_images.py --merge                       # After pushing both architectures
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

from odoo_toolkit.dev.images import DOCKER_REPOSITORY, Arch, get_image_tags, get_images, render_dockerfile

ARCHS: list[Arch] = ["amd64", "arm64"]


def arch_tag(image: str, arch: Arch) -> str:
    """Get the tag of an image for one architecture, like `dylankiss/odoo-dev:docker19-arm64`."""
    return f"{DOCKER_REPOSITORY}:{image.lower()}-{arch}"


def build(image: str, arch: Arch, *, push: bool, load: bool) -> None:
    """Build an image for one architecture.

    :param image: The Runbot image name, like `Docker19`.
    :param arch: The architecture to build for.
    :param push: Whether to push the image to the registry.
    :param load: Whether to load the image in the local Docker, tagged with its Odoo versions.
    """
    with tempfile.TemporaryDirectory() as context_dir:
        dockerfile = Path(context_dir) / "Dockerfile"
        dockerfile.write_text(render_dockerfile(image, arch), encoding="utf-8")
        tags = [arch_tag(image, arch), *(get_image_tags(image) if load else [])]
        cmd = [
            "docker", "buildx", "build",
            "--platform", f"linux/{arch}",
            "--file", str(dockerfile),
            *(arg for tag in tags for arg in ("--tag", tag)),
            *(["--push"] if push else []),
            *(["--load"] if load else []),
            context_dir,
        ]
        print(f"Building {image} for {arch}: {', '.join(tags)}", flush=True)
        subprocess.run(cmd, check=True)


def merge(image: str) -> None:
    """Combine the pushed per-architecture images into one multi-architecture tag per Odoo version."""
    for tag in get_image_tags(image):
        print(f"Creating {tag}", flush=True)
        subprocess.run(
            ["docker", "buildx", "imagetools", "create", "--tag", tag, *(arch_tag(image, arch) for arch in ARCHS)],
            check=True,
        )


def main() -> int:
    """Build or merge the images given on the command line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", action="append", choices=get_images(), help="The images to build (default: all).")
    parser.add_argument("--arch", action="append", choices=ARCHS, help="The architectures to build (default: both).")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--push", action="store_true", help="Push the images to Docker Hub.")
    output.add_argument("--load", action="store_true", help="Load the images in the local Docker.")
    output.add_argument("--merge", action="store_true", help="Create the multi-architecture tags of pushed images.")
    args = parser.parse_args()

    images: list[str] = args.image or get_images()
    archs = cast("list[Arch]", args.arch or ARCHS)
    try:
        for image in images:
            if args.merge:
                merge(image)
                continue
            for arch in archs:
                build(image, arch, push=args.push, load=args.load)
    except subprocess.CalledProcessError as e:
        print(f"Failed: {' '.join(e.cmd)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
