"""Derive the unmodified video hierarchy/constraints from pinned upstream PYNQ.

The upstream copy remains intact. This extraction omits the unrelated MicroBlaze,
audio and trace blocks, while retaining the hierarchy names required by PYNQ.
"""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
UPSTREAM = HERE / "vendor" / "PYNQ"
COMMIT = "93ddd21ab623883590a8c8b07f0b157b2855da4b"
NAMES = (
    "create_hier_cell_frontend_1", "create_hier_cell_frontend",
    "create_hier_cell_hdmi_out", "create_hier_cell_hdmi_in",
    "create_hier_cell_video",
)


def main():
    actual = subprocess.check_output(
        ["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != COMMIT:
        raise RuntimeError(f"PYNQ source is {actual}, expected {COMMIT}")
    source = UPSTREAM / "boards/Pynq-Z2/base/base.tcl"
    text = source.read_text(encoding="utf-8")
    procs = []
    for name in NAMES:
        start = text.index(f"proc {name} ")
        next_proc = re.search(r"^proc ", text[start + 5:], re.MULTILINE)
        end = start + 5 + next_proc.start() if next_proc else len(text)
        # Generated upstream separates procedures by a '# Hierarchical cell'
        # comment. Taking the complete slice preserves every Tcl connection.
        procs.append(text[start:end].rstrip())
    generated = HERE / "generated"
    generated.mkdir(exist_ok=True)
    (generated / "video_hierarchy.tcl").write_text(
        "# Copyright (c) 2016-2022 Xilinx, Inc.\n"
        "# SPDX-License-Identifier: BSD-3-Clause\n"
        f"# Extracted without modifying procedure bodies from PYNQ {COMMIT}.\n\n"
        + "\n\n".join(procs) + "\n", encoding="utf-8"
    )
    original_xdc = UPSTREAM / "boards/Pynq-Z2/base/vivado/constraints/base.xdc"
    xdc = original_xdc.read_text(encoding="utf-8")
    start = xdc.index("## HDMI signals")
    end = xdc.index("## Raspberry PI", start)
    hdmi_xdc = xdc[start:end].replace(
        "create_clock -period 8.334 -waveform {0.000 4.167} [get_ports hdmi_in_clk_p]",
        "# Project HDMI input: 1280x720@60, pixel clock 74.25 MHz.\n"
        "create_clock -period 13.468013468 -waveform {0.000 6.734006734} [get_ports hdmi_in_clk_p]",
    )
    if "-period 13.468013468" not in hdmi_xdc:
        raise RuntimeError("Expected upstream HDMI clock constraint was not found")
    (generated / "hdmi_pynq_z2.xdc").write_text(
        "# Copyright (C) 2022 Xilinx, Inc\n"
        "# SPDX-License-Identifier: BSD-3-Clause\n" + hdmi_xdc,
        encoding="utf-8",
    )
    edid_source = UPSTREAM / "boards/ip/dvi2rgb_v1_7/src/720p_edid.data"
    shutil.copyfile(edid_source, generated / "720p_edid.data")
    hashes = {}
    for path in (source, original_xdc, generated / "video_hierarchy.tcl",
                 generated / "hdmi_pynq_z2.xdc", edid_source,
                 generated / "720p_edid.data"):
        hashes[path.relative_to(HERE).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "repository": "https://github.com/Xilinx/PYNQ", "tag": "v3.0.1",
        "commit": COMMIT, "upstream_vivado": "2022.1", "build_vivado": "2022.2",
        "extracted_procedures": list(NAMES), "sha256": hashes,
        "project_overrides": {
            "hdmi_input_mode": "1280x720@60",
            "hdmi_input_pixel_clock_hz": 74250000,
            "hdmi_input_clock_period_ns": 13.468013468,
            "dvi2rgb_rx_kClkRange": 2,
            "note": "RX MMCM multiplier=10, VCO=742.5MHz; upstream procedure remains unmodified, top BD overrides original RX kClkRange=1",
        },
        "physical_board_tested": False,
    }
    (HERE / "source_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print("Prepared pinned HDMI hierarchy and PYNQ-Z2 pin constraints.")


if __name__ == "__main__":
    main()
