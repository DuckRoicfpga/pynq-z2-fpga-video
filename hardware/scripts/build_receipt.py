"""Bind a successful Vivado overlay build to its actual staged RTL and artifacts.

Normal use: begin before Vivado; complete only after Vivado exits successfully.
An explicitly labelled in-progress observation is supported for the initial DMA
build, captured before any successful bit/HWH/XSA/status/checkpoint existed.
These receipts are PC build evidence, never evidence of a physical board test.
"""
import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]
EXTENSIONS = {".v", ".sv", ".vh"}
SPECS = {
    "video_dma": {"folder": "output/dma_overlay", "project": "hardware/build/video_dma",
                  "bd": "video_dma_system", "marker": "VIDEO_DMA_BUILD_COMPLETE:",
                  "scripts": ["hardware/scripts/build_dma.tcl", "hardware/scripts/package_ip.tcl"]},
    "video_hdmi": {"folder": "output/hdmi_overlay", "project": "hardware/hdmi/build/video_hdmi",
                   "bd": "video_hdmi_system", "marker": "VIDEO_HDMI_BUILD_COMPLETE:",
                   "scripts": ["hardware/hdmi/build_hdmi.tcl", "hardware/hdmi/generated/video_hierarchy.tcl",
                               "hardware/hdmi/generated/hdmi_pynq_z2.xdc", "hardware/hdmi/source_manifest.json",
                               "hardware/hdmi/fix_tx_clocks.tcl"]},
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def digest_paths(paths):
    return {relative(path): sha(path) for path in sorted(paths)}


def source_files(folder):
    return [path for path in Path(folder).rglob("*") if path.is_file() and path.suffix in EXTENSIONS]


def canonical_rtl():
    files = source_files(ROOT / "rtl")
    if not files:
        raise RuntimeError("No canonical RTL sources")
    return digest_paths(files)


def verify_hashes(hashes, label):
    if not hashes:
        raise RuntimeError("Missing " + label + " fingerprints")
    for name, expected in hashes.items():
        path = (ROOT / name).resolve()
        if ROOT not in path.parents or not path.is_file() or sha(path) != expected:
            raise RuntimeError(label + " changed: " + name)


def verify_validation_gate(expected=None):
    path = ROOT / "output/rtl_validation/rtl_pass.json"
    gate = json.loads(path.read_text(encoding="utf-8-sig"))
    if gate.get("status") != "PASS" or gate.get("actual_board_test") is not False:
        raise RuntimeError("Require a successful PC-only RTL validation gate")
    current = canonical_rtl()
    if current != gate.get("source_sha256"):
        raise RuntimeError("Canonical RTL differs from the complete simulation source set")
    fingerprint = sha(path)
    if expected is not None and expected != fingerprint:
        raise RuntimeError("RTL validation gate changed during/since build")
    return current, fingerprint


def matching_source_set(folder, canonical, label):
    names = {Path(name).name for name in canonical}
    files = [p for p in source_files(folder) if p.name in names]
    if len(files) != len(canonical) or {p.name for p in files} != names:
        raise RuntimeError("Missing/duplicate " + label + " RTL source set: " + str(folder))
    result = digest_paths(files)
    for name, fingerprint in result.items():
        if fingerprint != canonical["rtl/" + Path(name).name]:
            raise RuntimeError(label + " RTL is stale: " + name)
    return result


def staged_folder(stem):
    spec = SPECS[stem]
    return ROOT / spec["project"] / (stem + ".gen") / "sources_1/bd" / spec["bd"] / "ipshared"


def output_names(stem):
    return [stem + suffix for suffix in (".bit", ".hwh", ".xsa")] + [
        "implementation_status.txt", "timing_post_route.rpt", "utilization_post_route.rpt", "drc_post_route.rpt"]


def artifact_snapshot(stem):
    folder = ROOT / SPECS[stem]["folder"]
    return {name: {"sha256": sha(folder / name), "bytes": (folder / name).stat().st_size,
                   "mtime_ns": (folder / name).stat().st_mtime_ns}
            for name in output_names(stem) if (folder / name).is_file()}


def begin(stem, log):
    canonical, gate = verify_validation_gate()
    spec = SPECS[stem]
    folder = ROOT / spec["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    log_path = (ROOT / log).resolve()
    if ROOT not in log_path.parents:
        raise RuntimeError("Build log must be inside this project")
    record = {"schema_version": 1, "status": "IN_PROGRESS", "stem": stem,
              "hdmi": stem == "video_hdmi", "capture_phase": "before_build",
              "captured_at_utc": datetime.now(timezone.utc).isoformat(), "captured_at_ns": time.time_ns(),
              "canonical_rtl_sha256": canonical, "validation_gate_sha256": gate,
              "build_inputs_sha256": digest_paths([ROOT / name for name in spec["scripts"]]),
              "log": relative(log_path), "initial_artifacts": artifact_snapshot(stem)}
    (folder / "build_receipt.pending.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    # Overwrite a previous receipt with an unmistakably incomplete state. A failed
    # rebuild must not leave an old successful receipt eligible for packaging.
    (folder / "build_receipt.json").write_text(json.dumps({"status": "IN_PROGRESS", "stem": stem}, indent=2) + "\n", encoding="utf-8")
    return record


def verify_pending(record):
    if record.get("schema_version") != 1 or record.get("status") != "IN_PROGRESS":
        raise RuntimeError("Missing active build snapshot")
    stem = record["stem"]
    if stem not in SPECS:
        raise RuntimeError("Unknown build")
    canonical, gate = verify_validation_gate(record["validation_gate_sha256"])
    if canonical != record["canonical_rtl_sha256"]:
        raise RuntimeError("Canonical RTL changed during build")
    verify_hashes(record["build_inputs_sha256"], "Build input")
    for key in ("packaged_rtl_sha256", "staged_rtl_sha256"):
        if record.get(key):
            verify_hashes(record[key], key)
    return canonical


def verify_export_patch(original_inputs):
    """Allow only the documented XSA export fix, never a design change."""
    name = "hardware/scripts/build_dma.tcl"
    path = ROOT / name
    current = path.read_bytes()
    new = (b"# In this global (non-run) flow, export the hardware description without\n"
           b"# -include_bit, which requires a completed project implementation run.\n"
           b"# The matching .bit is exported above and delivered alongside .hwh.\n"
           b"write_hw_platform -fixed -force -file [file join $out_dir video_dma.xsa]\n")
    old = b"write_hw_platform -fixed -include_bit -force -file [file join $out_dir video_dma.xsa]\n"
    if current.count(new) != 1:
        raise RuntimeError("Export recovery requires the exact documented XSA-only patch")
    reconstructed = current.replace(new, old)
    if hashlib.sha256(reconstructed).hexdigest() != original_inputs.get(name):
        raise RuntimeError("Build script changed beyond the documented export-only patch")
    verify_hashes({key: value for key, value in original_inputs.items() if key != name}, "Original build input")
    return {"script": name, "original_sha256": original_inputs[name], "patched_sha256": sha(path),
            "removed": old.decode(), "replacement": new.decode(),
            "scope": "Only hardware-description XSA export changes; RTL, constraints and routed bitstream unchanged."}


def recover_export(stem, recovery_log):
    if stem != "video_dma":
        raise RuntimeError("Export-only recovery is supported only for the observed initial DMA build")
    spec = SPECS[stem]
    folder = ROOT / spec["folder"]
    record = json.loads((folder / "build_receipt.pending.json").read_text(encoding="utf-8"))
    if record.get("status") != "IN_PROGRESS" or record.get("stem") != stem:
        raise RuntimeError("Missing original observed build snapshot")
    original_inputs = record["build_inputs_sha256"].copy()
    patch = verify_export_patch(original_inputs)
    record["original_build_inputs_sha256"] = original_inputs
    record["build_inputs_sha256"][patch["script"]] = patch["patched_sha256"]
    canonical = verify_pending(record)
    original_log = ROOT / record["log"]
    observed_completion = original_log.read_bytes()[int(record["observed_log_bytes"]):].decode("utf-8", errors="replace")
    if ("write_bitstream completed successfully" not in observed_completion or
            "Unable to get BIT file from implementation run" not in observed_completion):
        raise RuntimeError("Original build did not prove bitstream success followed only by the known XSA export failure")
    recovery_script = ROOT / "hardware/scripts/recover_dma_export.tcl"
    recovery_path = (ROOT / recovery_log).resolve()
    if ROOT not in recovery_path.parents:
        raise RuntimeError("Recovery log must be inside the project")
    recovery_text = recovery_path.read_text(encoding="utf-8", errors="replace")
    if "VIDEO_DMA_EXPORT_COMPLETE:" not in recovery_text:
        raise RuntimeError("Export-only recovery has not completed")
    record["build_inputs_sha256"][relative(recovery_script)] = sha(recovery_script)
    record["export_recovery"] = {
        "phase": "hardware_description_export_from_existing_routed_checkpoint",
        "script_patch": patch, "script": relative(recovery_script), "script_sha256": sha(recovery_script),
        "log": relative(recovery_path), "log_sha256": sha(recovery_path),
        "checkpoint": relative(folder / "video_dma_routed.dcp"),
        "checkpoint_sha256": sha(folder / "video_dma_routed.dcp"),
        "original_build_failed_at_export": True, "rtl_or_route_rerun": False,
    }
    return finish_record(record, canonical)


def begin_constraint_recovery(stem, script, log, extra_inputs=(), checkpoint_stage="synth"):
    """Preserve an original synth-bound observation and disclose clock-only deltas."""
    if stem != "video_hdmi":
        raise RuntimeError("Clock-constraint checkpoint recovery is supported only for HDMI")
    folder = ROOT / SPECS[stem]["folder"]
    pending_path = folder / "build_receipt.pending.json"
    record = json.loads(pending_path.read_text(encoding="utf-8"))
    observation_path = folder / "constraint_recovery.observed.json"
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    if observation.get("original_pending_sha256") != sha(pending_path):
        raise RuntimeError("Clock recovery does not belong to this observed source build")
    canonical, _ = verify_validation_gate(record["validation_gate_sha256"])
    if canonical != record["canonical_rtl_sha256"]:
        raise RuntimeError("Clock-only recovery cannot change RTL")
    checkpoint = ROOT / observation["checkpoint"]
    if sha(checkpoint) != observation["checkpoint_sha256"] or not observation["checkpoint_written_after_build_capture"]:
        raise RuntimeError("Synthesis checkpoint changed or lacks its source-build binding")
    synthesis_checkpoint = checkpoint
    if checkpoint_stage == "routed":
        checkpoint = folder / "video_hdmi_routed.dcp"
        original_log = (ROOT / record["log"]).read_text(encoding="utf-8", errors="replace")
        if (not checkpoint.is_file() or checkpoint.stat().st_mtime_ns < record["captured_at_ns"] or
                SPECS[stem]["marker"] not in original_log):
            raise RuntimeError("Routed recovery requires a fresh checkpoint and completed original source build")
    allowed_changes = {"hardware/hdmi/build_hdmi.tcl", "hardware/hdmi/resume_hdmi.tcl"}
    allowed_changes.update(name for name in observation["original_inputs"] if name.endswith("/rgb2dvi_clocks.xdc"))
    changes = {}
    for name, previous in observation["original_inputs"].items():
        current = ROOT / name
        if sha(current) != previous["sha256"]:
            if name not in allowed_changes:
                raise RuntimeError("Not an allowed clock-only build input change: " + name)
            changes[name] = {"original_sha256": previous["sha256"], "current_sha256": sha(current),
                             "unified_diff": "".join(difflib.unified_diff(previous["text"].splitlines(True),
                                 current.read_text(encoding="utf-8").splitlines(True), fromfile=name + ":original", tofile=name + ":clock-recovery"))}
    current_inputs = digest_paths([ROOT / name for name in observation["original_inputs"]])
    recovery_script, recovery_log = (ROOT / script).resolve(), (ROOT / log).resolve()
    if ROOT not in recovery_script.parents or ROOT not in recovery_log.parents:
        raise RuntimeError("Clock recovery evidence must be inside the project")
    current_inputs[relative(recovery_script)] = sha(recovery_script)
    for name in extra_inputs:
        path = (ROOT / name).resolve()
        if ROOT not in path.parents:
            raise RuntimeError("Extra recovery input must be inside project")
        current_inputs[relative(path)] = sha(path)
    record["original_build_inputs_sha256"] = record["build_inputs_sha256"].copy()
    record["build_inputs_sha256"] = current_inputs
    record["packaged_rtl_sha256"] = matching_source_set(ROOT / "hardware/ip_repo/video_filter_axi_1_0/src", canonical, "Packaged")
    record["staged_rtl_sha256"] = matching_source_set(staged_folder(stem), canonical, "Vivado staged")
    record["constraint_recovery"] = {
        "phase": "clock_constraints_from_source_bound_" + checkpoint_stage + "_checkpoint",
        "observation": relative(observation_path), "observation_sha256": sha(observation_path),
        "checkpoint": relative(checkpoint), "checkpoint_sha256": sha(checkpoint),
        "checkpoint_stage": checkpoint_stage,
        "synthesis_checkpoint": relative(synthesis_checkpoint), "synthesis_checkpoint_sha256": sha(synthesis_checkpoint),
        "corrected_output_checkpoint": relative(folder / "video_hdmi_clock_fixed_routed.dcp"),
        "script": relative(recovery_script), "script_sha256": sha(recovery_script),
        "log": relative(recovery_log), "original_log_sha256": sha(ROOT / record["log"]),
        "constraint_input_changes": changes, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "started_at_ns": time.time_ns(), "rtl_resynthesized": False, "place_and_route_rerun": checkpoint_stage == "synth",
        "scope": "Original source-bound synthesis reused; clock constraint delta and recovery script are explicit. No physical board test."}
    (folder / "build_receipt.constraint.pending.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    (folder / "build_receipt.json").write_text(json.dumps({"status": "IN_PROGRESS", "stem": stem}, indent=2) + "\n", encoding="utf-8")
    return record


def complete_constraint_recovery(stem, clock_reports=()):
    folder = ROOT / SPECS[stem]["folder"]
    record = json.loads((folder / "build_receipt.constraint.pending.json").read_text(encoding="utf-8"))
    canonical = verify_pending(record)
    recovery = record["constraint_recovery"]
    for name in ("observation", "checkpoint", "synthesis_checkpoint", "script"):
        if sha(ROOT / recovery[name]) != recovery[name + "_sha256"]:
            raise RuntimeError("Clock recovery evidence changed: " + recovery[name])
    if sha(ROOT / record["log"]) != recovery["original_log_sha256"]:
        raise RuntimeError("Original Vivado build log changed during clock recovery")
    log_path = ROOT / recovery["log"]
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if "VIDEO_HDMI_CLOCK_RECOVERY_COMPLETE:" not in text or log_path.stat().st_mtime_ns < recovery["started_at_ns"]:
        raise RuntimeError("Clock recovery has not reached its own fresh completion marker")
    if ("VIDEO_HDMI_RECOVERY_STAGE=" + recovery["checkpoint_stage"]) not in text:
        raise RuntimeError("Recovery log checkpoint stage does not match the observed snapshot")
    selected = [line.split("=", 1)[1] for line in text.splitlines()
                if line.startswith("VIDEO_HDMI_RECOVERY_CHECKPOINT=")]
    if len(selected) != 1 or Path(selected[0]).resolve() != (ROOT / recovery["checkpoint"]).resolve():
        raise RuntimeError("Recovery opened a different checkpoint than the fingerprinted input")
    corrected = ROOT / recovery["corrected_output_checkpoint"]
    if not corrected.is_file() or corrected.stat().st_mtime_ns < recovery["started_at_ns"]:
        raise RuntimeError("Missing fresh corrected routed checkpoint")
    recovery["corrected_output_checkpoint_sha256"] = sha(corrected)
    if not clock_reports:
        raise RuntimeError("Explicit clock-relation/check-timing report is required")
    reports = []
    for name in clock_reports:
        path = (ROOT / name).resolve()
        if ROOT not in path.parents or path.stat().st_mtime_ns < recovery["started_at_ns"]:
            raise RuntimeError("Clock proof report predates recovery or is outside project")
        reports.append(path)
    recovery["clock_proof_sha256"] = digest_paths(reports)
    recovery["log_sha256"] = sha(log_path)
    # Unlike HWH, all bit/route reports must be newly produced by recovery.
    for name, metadata in artifact_snapshot(stem).items():
        if not name.endswith(".hwh") and metadata["mtime_ns"] < recovery["started_at_ns"]:
            raise RuntimeError("Recovered artifact predates reimplementation: " + name)
    return finish_record(record, canonical)


def finish_record(record, canonical):
    stem = record["stem"]
    folder = ROOT / SPECS[stem]["folder"]
    captured_ns = int(record["captured_at_ns"])
    artifacts = artifact_snapshot(stem)
    if set(artifacts) != set(output_names(stem)):
        raise RuntimeError("Successful build artifacts are incomplete")
    # HWH can preserve its earlier generation timestamp when copied by Tcl.
    for name in (stem + ".bit", stem + ".xsa", "implementation_status.txt"):
        if artifacts[name]["mtime_ns"] < captured_ns:
            raise RuntimeError("Build artifact predates source snapshot: " + name)
    generated_hwh = list((staged_folder(stem).parent / "hw_handoff").glob("*.hwh"))
    if len(generated_hwh) != 1 or sha(generated_hwh[0]) != artifacts[stem + ".hwh"]["sha256"]:
        raise RuntimeError("Delivered HWH differs from this generated block design's HWH")
    record["generated_hwh_sha256"] = digest_paths(generated_hwh)
    record["packaged_rtl_sha256"] = matching_source_set(
        ROOT / "hardware/ip_repo/video_filter_axi_1_0/src", canonical, "Packaged")
    record["staged_rtl_sha256"] = matching_source_set(staged_folder(stem), canonical, "Vivado staged")
    record.update({"status": "PASS", "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                   "artifacts": artifacts, "build_log_sha256": sha(ROOT / record["log"]),
                   "physical_board_verified": False,
                   "evidence": "Observed Vivado build, matching canonical/packaged/generated RTL and generated HWH, SHA256-bound artifacts, with any export recovery explicitly recorded; no physical board test."})
    (folder / "build_receipt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return validate_receipt(folder, stem)


def complete(stem):
    spec = SPECS[stem]
    folder = ROOT / spec["folder"]
    record = json.loads((folder / "build_receipt.pending.json").read_text(encoding="utf-8"))
    if record.get("stem") != stem:
        raise RuntimeError("Build snapshot belongs to another overlay")
    canonical = verify_pending(record)
    captured_ns = int(record["captured_at_ns"])
    log_path = ROOT / record["log"]
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    if record["capture_phase"] == "before_build":
        if log_path.stat().st_mtime_ns < captured_ns:
            raise RuntimeError("Vivado log predates build snapshot")
        completion_text = log_text
    elif record["capture_phase"] in ("synthesis_in_progress", "implementation_in_progress"):
        absent = {stem + ".bit", stem + ".hwh", stem + ".xsa", "implementation_status.txt"}
        if not absent <= set(record.get("artifacts_absent_at_capture", [])):
            raise RuntimeError("In-progress snapshot lacks explicit absent-output observation")
        completion_text = log_path.read_bytes()[int(record["observed_log_bytes"]):].decode("utf-8", errors="replace")
    else:
        raise RuntimeError("Unsupported build capture phase")
    if spec["marker"] not in completion_text:
        raise RuntimeError("This observed Vivado build has not reached its completion marker")
    return finish_record(record, canonical)


def validate_receipt(folder, stem):
    folder = Path(folder)
    record = json.loads((folder / "build_receipt.json").read_text(encoding="utf-8"))
    if record.get("status") != "PASS" or record.get("stem") != stem or record.get("physical_board_verified") is not False:
        raise RuntimeError("No completed PC build receipt for " + stem)
    canonical, gate = verify_validation_gate(record.get("validation_gate_sha256"))
    if canonical != record.get("canonical_rtl_sha256"):
        raise RuntimeError("Receipt does not match current canonical RTL")
    verify_hashes(record["build_inputs_sha256"], "Built design input")
    verify_hashes(record["packaged_rtl_sha256"], "Packaged RTL")
    verify_hashes(record["staged_rtl_sha256"], "Actual Vivado staged RTL")
    verify_hashes(record["generated_hwh_sha256"], "Generated HWH")
    if matching_source_set(staged_folder(stem), canonical, "Vivado staged") != record["staged_rtl_sha256"]:
        raise RuntimeError("Vivado staged RTL set differs from build receipt")
    if artifact_snapshot(stem) != record.get("artifacts"):
        raise RuntimeError("Bit/HWH/reports differ from completed build receipt")
    if sha(ROOT / record["log"]) != record.get("build_log_sha256"):
        raise RuntimeError("Vivado log changed since successful build receipt")
    if record.get("export_recovery"):
        recovery = record["export_recovery"]
        if verify_export_patch(record["original_build_inputs_sha256"]) != recovery["script_patch"]:
            raise RuntimeError("Export recovery script diff changed")
        for name in ("script", "log", "checkpoint"):
            if sha(ROOT / recovery[name]) != recovery[name + "_sha256"]:
                raise RuntimeError("Export recovery evidence changed: " + recovery[name])
    if record.get("constraint_recovery"):
        recovery = record["constraint_recovery"]
        for name in ("observation", "checkpoint", "synthesis_checkpoint", "corrected_output_checkpoint", "script", "log"):
            if sha(ROOT / recovery[name]) != recovery[name + "_sha256"]:
                raise RuntimeError("Clock recovery evidence changed: " + recovery[name])
        verify_hashes(recovery["clock_proof_sha256"], "Clock recovery proof report")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("begin", "complete", "validate", "recover-export", "begin-constraint-recovery", "complete-constraint-recovery"))
    parser.add_argument("--stem", choices=tuple(SPECS), required=True)
    parser.add_argument("--log")
    parser.add_argument("--recovery-log")
    parser.add_argument("--recovery-script")
    parser.add_argument("--extra-input", action="append", default=[])
    parser.add_argument("--clock-report", action="append", default=[])
    parser.add_argument("--checkpoint-stage", choices=("synth", "routed"), default="synth")
    args = parser.parse_args()
    if args.action == "begin":
        if not args.log:
            parser.error("begin requires --log")
        result = begin(args.stem, args.log)
    elif args.action == "complete":
        result = complete(args.stem)
    elif args.action == "recover-export":
        if not args.recovery_log:
            parser.error("recover-export requires --recovery-log")
        result = recover_export(args.stem, args.recovery_log)
    elif args.action == "begin-constraint-recovery":
        if not args.recovery_script or not args.recovery_log:
            parser.error("begin-constraint-recovery requires --recovery-script and --recovery-log")
        result = begin_constraint_recovery(args.stem, args.recovery_script, args.recovery_log, args.extra_input, args.checkpoint_stage)
    elif args.action == "complete-constraint-recovery":
        result = complete_constraint_recovery(args.stem, args.clock_report)
    else:
        result = validate_receipt(ROOT / SPECS[args.stem]["folder"], args.stem)
    print(json.dumps({"status": result["status"], "stem": args.stem,
                      "capture_phase": result["capture_phase"], "physical_board_verified": False}))


if __name__ == "__main__":
    main()
