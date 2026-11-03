"""Build the development images from Runbot's Dockerfiles, for amd64 and arm64.

Every image is built per architecture and tagged `<repository>:<image>-<arch>`. With `--merge`, the per-architecture
images are combined into one multi-architecture tag per Odoo version, like `dylankiss/odoo-dev:19.0`, so Docker picks
the right architecture on every machine.

Usage:
    uv run scripts/build_images.py --arch arm64 --load           # Build every image for arm64 and load it locally
    uv run scripts/build_images.py --image Docker19 --arch amd64 --push
    uv run scripts/build_images.py --merge                       # After pushing both architectures
    uv run scripts/build_images.py --changed-since origin/master # List the images a change affects, as JSON
"""

import argparse
import json
import subprocess
import sys
from typing import cast

from odoo_toolkit.dev.images import Arch, arch_tag, build_image, get_changed_images, get_image_tags, get_images

ARCHS: list[Arch] = ["amd64", "arm64"]


def build(image: str, arch: Arch, *, push: bool, load: bool) -> None:
    """Build an image for one architecture, showing what is being built."""
    tags = [arch_tag(image, arch), *(get_image_tags(image) if load else [])]
    print(f"Building {image} for {arch}: {', '.join(tags)}", flush=True)
    build_image(image, arch, push=push, load=load)


def merge(image: str) -> None:
    """Combine the pushed per-architecture images into one multi-architecture tag per Odoo version."""
    for tag in get_image_tags(image):
        print(f"Creating {tag}", flush=True)
        subprocess.run(
            ["docker", "buildx", "imagetools", "create", "--tag", tag, *(arch_tag(image, arch) for arch in ARCHS)],
            check=True,
        )


def get_changed_since(ref: str) -> list[str]:
    """Get the images the changes since a commit affect, or all of them if that commit can't be found."""
    try:
        changed_files = _git("diff", "--name-only", ref, "HEAD").split()
        previous_versions: dict[str, dict[str, str]] = json.loads(_git("show", f"{ref}:odoo_toolkit/versions.json"))
    except subprocess.CalledProcessError:
        # Like after a force push, which leaves the earlier commit out of the history.
        return get_images()
    return get_changed_images(changed_files, {name: info["image"] for name, info in previous_versions.items()})


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def main() -> int:
    """Build or merge the images given on the command line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", action="append", choices=get_images(), help="The images to build (default: all).")
    parser.add_argument("--arch", action="append", choices=ARCHS, help="The architectures to build (default: both).")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--push", action="store_true", help="Push the images to Docker Hub.")
    output.add_argument("--load", action="store_true", help="Load the images in the local Docker.")
    output.add_argument("--merge", action="store_true", help="Create the multi-architecture tags of pushed images.")
    output.add_argument(
        "--changed-since",
        metavar="COMMIT",
        help="Print the images the changes since this commit affect, as JSON, instead of building.",
    )
    args = parser.parse_args()

    if args.changed_since:
        print(json.dumps(get_changed_since(args.changed_since)))
        return 0

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
