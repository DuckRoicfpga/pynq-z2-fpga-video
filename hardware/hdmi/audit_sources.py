"""Check pinned upstream sources and the RTL actually staged in the HDMI BD."""
from pathlib import Path
import hashlib
import json
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((HERE / "source_manifest.json").read_text())
    verified = {}
    for relative, expected in manifest["sha256"].items():
        path = HERE / relative
        actual = digest(path)
        if actual != expected:
            raise RuntimeError(f"Pinned HDMI source changed: {relative}")
        verified[path.relative_to(ROOT).as_posix()] = actual
    generated = HERE / "build/video_hdmi/video_hdmi.gen"
    for staged in sorted((ROOT / "hardware/ip_repo/video_filter_axi_1_0/src").glob("*.v")):
        originals = list((ROOT / "rtl").rglob(staged.name))
        copies = list(generated.rglob(staged.name))
        if len(originals) != 1 or len(copies) != 1:
            raise RuntimeError(f"Expected one original and one generated copy: {staged.name}")
        expected = digest(originals[0])
        if digest(staged) != expected or digest(copies[0]) != expected:
            raise RuntimeError(f"HDMI synthesis RTL differs from the canonical RTL: {staged.name}")
        verified[originals[0].relative_to(ROOT).as_posix()] = expected
        verified[staged.relative_to(ROOT).as_posix()] = expected
        verified[copies[0].relative_to(ROOT).as_posix()] = expected
    report = {
        "upstream_commit": manifest["commit"],
        "source_integrity": "PASS",
        "sha256": verified,
        "physical_board_tested": False,
    }
    if "--final" in sys.argv:
        exported = ROOT / "output/hdmi_overlay"
        edid = HERE / "generated/720p_edid.data"
        staged_edid = HERE / "build/video_hdmi/synth_work/720p_edid.data"
        if edid.read_bytes() != staged_edid.read_bytes():
            raise RuntimeError("Synthesis EDID differs from the unmodified pinned EDID")
        verified[staged_edid.relative_to(ROOT).as_posix()] = digest(staged_edid)
        hwh = exported / "video_hdmi.hwh"
        staged_hwh = generated / "sources_1/bd/video_hdmi_system/hw_handoff/video_hdmi_system.hwh"
        if hwh.read_bytes() != staged_hwh.read_bytes():
            raise RuntimeError("Exported HDMI HWH differs from the synthesized project")
        modules = {
            m.get("INSTANCE"): m for m in ET.parse(hwh).getroot().findall(".//MODULE")
        }
        def params(name):
            return {p.get("NAME"): p.get("VALUE") for p in modules[name].findall("./PARAMETERS/PARAMETER")}
        rx = [m for m in modules.values() if m.get("MODTYPE") == "dvi2rgb"]
        if len(rx) != 1 or params(rx[0].get("INSTANCE"))["kClkRange"] != "2":
            raise RuntimeError("HDMI RX must use kClkRange=2 for 74.25MHz720p input")
        for name in ("rst_fclk0", "rst_fclk1"):
            p = params(name)
            if p["C_EXT_RESET_HIGH"] != "0" or p["C_AUX_RESET_HIGH"] != "1":
                raise RuntimeError(f"Unsafe reset polarity in {name}")
        p = params("system_interrupts")
        if (p["C_NUM_INTR_INPUTS"] != "8" or int(p["C_KIND_OF_INTR"], 0) != 0
                or int(p["C_ASYNC_INTR"], 0) & 0x3F != 0x3F
                or p["C_IRQ_CONNECTION"] != "1"):
            raise RuntimeError(f"Invalid HDMI interrupt controller configuration: {p}")
        ps_irq = modules["processing_system7_0"].find("./PORTS/PORT[@NAME='IRQ_F2P']")
        intc_irq = modules["system_interrupts"].find("./PORTS/PORT[@NAME='irq']")
        if ps_irq.get("SIGNAME") != intc_irq.get("SIGNAME") or ps_irq.get("LEFT") != "0":
            raise RuntimeError("INTC must connect to the first and only PS PL interrupt")
        report["hardware_metadata"] = "PASS"
        report["artifact_sha256"] = {
            name: digest(exported / name)
            for name in ("video_hdmi.bit", "video_hdmi.hwh", "video_hdmi.xsa")
        }
    out = ROOT / "output/hdmi_overlay/source_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"HDMI_SOURCE_AUDIT_PASS: {len(verified)} files")


if __name__ == "__main__":
    main()
