"""Read board software/hardware identity; never load an FPGA overlay."""
from datetime import datetime, timezone
from importlib import util
import json
import os
from pathlib import Path
import platform
import sys

try:
    from importlib import metadata
except ImportError:
    try:
        import importlib_metadata as metadata
    except ImportError:
        metadata = None


def read_text_if_present(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").replace("\x00", "").strip()
    except OSError:
        return None


def package_version(name):
    if metadata is None:
        return None
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def collect_inventory():
    """Collect limited configuration; no network, camera, or MMIO operations."""
    base_candidates = [Path("/home/xilinx/jupyter_notebooks/base/base.bit")]
    try:
        spec = util.find_spec("pynq")
        if spec and spec.origin:
            package_root = Path(spec.origin).parent
            base_candidates.insert(0, package_root / "overlays" / "base" / "base.bit")
    except (ImportError, ValueError):
        pass
    base_files = []
    seen = set()
    for bit_path in base_candidates:
        path_text = str(bit_path)
        if path_text in seen:
            continue
        seen.add(path_text)
        try:
            if bit_path.is_file():
                base_files.append({
                    "bit_path": path_text,
                    "bit_bytes": bit_path.stat().st_size,
                    "hwh_present": bit_path.with_suffix(".hwh").is_file(),
                })
        except OSError:
            continue
    memory = read_text_if_present("/proc/meminfo")
    memory_summary = None
    if memory:
        memory_summary = {
            line.split(":", 1)[0]: line.split(":", 1)[1].strip()
            for line in memory.splitlines()
            if line.startswith(("MemTotal:", "MemAvailable:"))
        }
    return {
        "schema_version": 1,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "kernel_release": platform.release(),
        "python": sys.version.split()[0],
        "board_env": os.environ.get("BOARD"),
        "board_model": read_text_if_present("/proc/device-tree/model"),
        "os_release": read_text_if_present("/etc/os-release"),
        "packages": {name: package_version(name) for name in (
            "pynq", "numpy", "Pillow", "ipywidgets", "jupyter_server", "notebook",
            "pynq-metadata", "opencv-python",
        )},
        "base_overlay_files": base_files,
        "memory": memory_summary,
        "fpga_programmed_by_this_script": False,
        "physical_filter_test_by_this_script": False,
        "notes": [
            "Package versions are from distribution metadata; missing metadata does not prove a module is absent.",
            "Base files were checked at common paths only; an empty list does not prove they are unavailable.",
            "Image release may need confirmation from SD image filename; os-release alone is not the PYNQ image version.",
            "On older Python without importlib metadata/backport, package versions are unavailable; no dependency is installed automatically.",
        ],
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("board_inventory.json"))
    args = parser.parse_args()
    report = collect_inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("Saved:", args.output.resolve())


if __name__ == "__main__":
    main()
