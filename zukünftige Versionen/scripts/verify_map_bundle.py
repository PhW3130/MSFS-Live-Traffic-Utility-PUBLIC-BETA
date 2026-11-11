#!/usr/bin/env python3
"""Fail a PyInstaller build if its offline map resources are incomplete."""

from pathlib import Path
import argparse
import sys


REQUIRED_MAP_FILES = (
    "map.html",
    "map.js",
    "leaflet/leaflet.js",
    "leaflet/leaflet.css",
    "leaflet/images/layers.png",
    "leaflet/images/layers-2x.png",
    "leaflet/images/marker-icon.png",
    "leaflet/images/marker-icon-2x.png",
    "leaflet/images/marker-shadow.png",
)


def find_map_assets(bundle: Path):
    """Return the first complete gui_web folder in common PyInstaller layouts."""
    bundle = Path(bundle)
    candidates = (bundle / "_internal" / "gui_web", bundle / "gui_web")
    checked = []
    for candidate in candidates:
        missing = [relative for relative in REQUIRED_MAP_FILES
                   if not (candidate / relative).is_file()]
        checked.append((candidate, missing))
        if not missing:
            return candidate, checked
    return None, checked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="PyInstaller one-folder bundle")
    args = parser.parse_args(argv)

    map_dir, checked = find_map_assets(args.bundle)
    if map_dir is not None:
        print(f"Map assets verified: {map_dir}")
        return 0

    print(f"ERROR: packaged map assets are incomplete in {args.bundle}", file=sys.stderr)
    for candidate, missing in checked:
        print(f"  {candidate}", file=sys.stderr)
        for relative in missing:
            print(f"    missing: {relative}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
