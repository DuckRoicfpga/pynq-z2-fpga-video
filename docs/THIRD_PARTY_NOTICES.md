# Nguồn, ghi công và giấy phép / Third-party notices

## Original project

Original custom filter, driver/integration code and project documentation are distributed under the root **MIT LICENSE**, Copyright (c) 2026 **Tran Minh Duc**. Existing third-party copyright headers and notices take precedence for the corresponding files. Listing an upstream component does not transfer its ownership to this project's author.

## PYNQ / Xilinx

- Repository: https://github.com/Xilinx/PYNQ
- Pinned tag: **v3.0.1**
- Pinned commit: **93ddd21ab623883590a8c8b07f0b157b2855da4b**
- License: **BSD-3-Clause**, preserved in [LICENSE_PYNQ.txt](../licenses/LICENSE_PYNQ.txt).
- `hardware/hdmi/generated/video_hierarchy.tcl` contains extracted upstream video procedures with their bodies preserved.
- HDMI XDC is derived from the upstream PYNQ-Z2 base constraints, with project-specific 720p RX clock constraint; EDID is copied from the pinned upstream source.
- The hardware build fetches other upstream video/HLS/IP sources at that commit rather than committing an entire upstream checkout here.
- Source provenance and hash information are retained in `hardware/hdmi/source_manifest.json`.

PYNQ hardware-source version and installed runtime version are separate: the demonstrated board runtime was **PYNQ 3.1.1**.

## Digilent and upstream dependencies

DVI RX/TX and dynamic-clock components obtained through PYNQ have their own notices, including **MIT, Copyright (c) 2017 Digilent**. Preserve [THIRD_PARTY_LICENSES.txt](../licenses/THIRD_PARTY_LICENSES.txt) when redistributing source or generated artifacts. That upstream notice includes other PYNQ dependencies; listing it does not claim this project uses every component named there.

Vivado/Vitis HLS, AXI DMA/VDMA, timing/interconnect and board definitions come from separately installed vendor tools. Their installers/licenses are not included or relicensed by this repository.

## Python runtime and host dependencies

PYNQ, NumPy, Pillow, OpenCV, Flask, IPython/Jupyter and their dependencies retain their respective licenses. This repository imports them; it does not bundle a Python environment or SD operating-system image. Use the image/tool documentation for runtime compatibility.

## Demonstration media

The board photograph and unretouched screenshots record the project experiment. Third-party video/desktop content visible inside screenshots remains the property of its respective owners and is not offered as a project training/test dataset under the software MIT license. Generated test vectors are deterministic project patterns. The timing plot is a visualization of the recorded JSON measurements, not an edited or generated hardware-proof screenshot.

## Technical references

- [PYNQ video architecture and frame handling](https://pynq.readthedocs.io/en/latest/pynq_libraries/video.html)
- [PYNQ DMA](https://pynq.readthedocs.io/en/latest/pynq_libraries/dma.html)
- [PYNQ allocation](https://pynq.readthedocs.io/en/latest/pynq_libraries/allocate.html)
- [AMD PG020 S2MM VDMA status register](https://docs.amd.com/r/en-US/pg020_axi_vdma/S2MM_VDMASR-S2MM-VDMA-Status-Register-Offset-34h)
- [Safe PYNQ shutdown sequence, TU/e](https://pynq.tue.nl/general/disconnect-pynq/)
