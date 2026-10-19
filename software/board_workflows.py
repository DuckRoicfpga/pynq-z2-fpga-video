"""Board-only validation/benchmark workflow; numerical reference is explicitly CPU."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from PIL import Image
from board_inventory import collect_inventory
from reference_filters import apply_filter, rgb_for_display
from video_driver import file_sha256, VideoHardwareError, VideoAccelerator


def load_vector_manifest(folder):
    folder=Path(folder)
    manifest=json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version")!=1 or len(manifest.get("cases",[]))<23:
        raise ValueError("Incomplete test-vector bundle.")
    for name,metadata in manifest["files"].items():
        path=(folder/name).resolve()
        if folder.resolve() not in path.parents or not path.is_file() or file_sha256(path)!=metadata["sha256"]:
            raise ValueError("Vector fingerprint failed: "+name)
    return manifest


def load_case(folder,case):
    with np.load(str(Path(folder)/case["npz"]),allow_pickle=False) as archive:
        arrays={key:archive[key].copy() for key in archive.files}
    for key in ("input","mode0","mode1","mode2","mode3"):
        if key not in arrays or arrays[key].dtype!=np.uint8 or arrays[key].shape!=(case["height"],case["width"],3):
            raise ValueError("Invalid vector array shape/type: "+key)
    return arrays


def assert_pixels(actual,expected,label):
    if actual.shape!=expected.shape or actual.dtype!=np.uint8:
        raise AssertionError(label+": invalid output dimensions/type")
    difference=np.any(actual!=expected,axis=2)
    count=int(np.count_nonzero(difference))
    if count:
        y,x=np.argwhere(difference)[0]
        raise AssertionError("%s: %d wrong pixels; first (%d,%d) actualBGR=%s goldenBGR=%s" %
                             (label,count,x,y,actual[y,x].tolist(),expected[y,x].tolist()))


def stats_ms(values):
    values=np.asarray(values,dtype=np.float64)
    return {"mean_ms":float(np.mean(values)),"median_ms":float(np.median(values)),
            "p95_ms":float(np.percentile(values,95)),"min_ms":float(np.min(values)),
            "max_ms":float(np.max(values)),"frames_per_second_from_mean":float(1000/np.mean(values))}


def benchmark(driver,image,mode,frames=100,warmup=5):
    golden=apply_filter(image,mode)
    for _ in range(warmup): assert_pixels(driver.process(image,mode),golden,"warmup")
    dma_times=[];core_times=[];total_times=[];cpu_times=[]
    measured_started=time.perf_counter()
    for _ in range(frames):
        actual=driver.process(image,mode)
        metrics=driver.last_metrics.copy()
        # Correctness check is outside per-call recorded timings; no display in this loop.
        assert_pixels(actual,golden,"benchmark mode%d" % mode)
        dma_times.append(metrics["dma_and_control_ms"])
        core_times.append(metrics["core_active_ms"])
        total_times.append(metrics["total_driver_ms"])
    benchmark_wall=time.perf_counter()-measured_started
    for _ in range(frames):
        started=time.perf_counter()
        cpu=apply_filter(image,mode)
        cpu_times.append((time.perf_counter()-started)*1000)
    assert_pixels(cpu,golden,"CPU reference")
    return {"mode":mode,"frames":frames,"warmup_frames":warmup,"width":image.shape[1],"height":image.shape[0],
            "core_active":stats_ms(core_times),"dma_and_control":stats_ms(dma_times),
            "whole_driver":stats_ms(total_times),"cpu_numpy_reference":stats_ms(cpu_times),
            "validation_loop_wall_seconds_including_comparison":benchmark_wall,
            "notes":["Core cycles include stream stalls and drain; they are not pure arithmetic cycles.",
                     "Whole driver includes packing, caches, MMIO, DMA, polling, unpacking; excludes display.",
                     "CPU baseline is this NumPy implementation on the board ARM, not an optimized C/OpenCV baseline.",
                     "Browser preview FPS and HDMI frame rate are not measured here."]}


def run_verification(driver,vector_dir="test_vectors",output_dir="results",progress=print):
    if not getattr(driver,"physical_backend",False):
        raise VideoHardwareError("Physical PASS requires VideoAccelerator.load/from_overlay on the board.")
    vector_dir=Path(vector_dir);output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    report={"schema_version":1,"started_at_utc":datetime.now(timezone.utc).isoformat(),
            "status":"RUNNING","physical_board_verified":False,"backend":"FPGA AXI DMA",
            "inventory":collect_inventory(),"overlay_manifest":driver.fingerprint,
            "vector_manifest_sha256":file_sha256(vector_dir/"manifest.json"),
            "pixel_tests":[],"sequential_frames":0,"reset_tests":[],"benchmarks":[]}
    result_path=output_dir/"board_dma_results.json"
    def save(): result_path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    save()
    try:
        manifest=load_vector_manifest(vector_dir)
        for case in manifest["cases"]:
            arrays=load_case(vector_dir,case)
            for mode in range(4):
                actual=driver.process(arrays["input"],mode,case["threshold"])
                assert_pixels(actual,arrays["mode%d"%mode],case["name"]+" mode%d"%mode)
                report["pixel_tests"].append({"case":case["name"],"mode":mode,"status":"PASS",
                                            "mismatched_pixels":0,"metrics":driver.last_metrics.copy()})
            progress("PASS "+case["name"]+" — 4 modes")
        # Alternate sizes/modes and reset at known frame boundaries to expose stale line-buffer state.
        small_cases=[case for case in manifest["cases"] if case["width"]<640]
        for index in range(100):
            case=small_cases[index%len(small_cases)]
            arrays=load_case(vector_dir,case)
            mode=index%4
            if index in (0,33,66):
                driver.reset()
                report["reset_tests"].append({"before_sequence_frame":index,"status":"PASS"})
            assert_pixels(driver.process(arrays["input"],mode,case["threshold"]),arrays["mode%d"%mode],"sequence%d"%index)
            report["sequential_frames"]+=1
        progress("PASS 100 consecutive frames, switching modes/sizes and 3 resets")
        vga_case=next(case for case in manifest["cases"] if case["name"]=="640x480_random")
        arrays=load_case(vector_dir,vga_case)
        for mode in range(4):
            progress("Benchmark mode%d: 5 warmup + 100 measured VGA frames"%mode)
            report["benchmarks"].append(benchmark(driver,arrays["input"],mode))
            output=driver.process(arrays["input"],mode)
            assert_pixels(output,arrays["mode%d"%mode],"snapshot mode%d"%mode)
            Image.fromarray(rgb_for_display(output)).save(str(output_dir/("fpga_mode%d.png"%mode)))
        # The outer workflow owns driver.close(). Do not persist a physical
        # completion PASS until both DMA channels are safely stopped/freed.
        report["status"]="PIXELS_PASS_PENDING_CLEANUP"
        report["physical_board_verified"]=False
        report["finished_at_utc"]=datetime.now(timezone.utc).isoformat()
        save()
        progress("PIXELS PASS — waiting for DMA cleanup before physical completion PASS")
        return report
    except Exception as exc:
        report["status"]="FAIL"
        report["physical_board_verified"]=False
        report["error"]="%s: %s"%(type(exc).__name__,exc)
        report["finished_at_utc"]=datetime.now(timezone.utc).isoformat()
        save()
        raise


def run_dma_verification(root=".", progress=print, timeout=5.0):
    """Commit physical PASS only after checks AND driver cleanup succeed."""
    root = Path(root)
    output_dir = root / "results"
    result_path = output_dir / "board_dma_results.json"
    pixel_checks_finished = False
    try:
        with VideoAccelerator.load(root / "overlays", timeout=timeout) as accelerator:
            report = run_verification(accelerator, root / "test_vectors", output_dir, progress)
            pixel_checks_finished = True
        report.update({"status": "PASS", "physical_board_verified": True,
                       "dma_cleanup_verified": True,
                       "finished_at_utc": datetime.now(timezone.utc).isoformat()})
        result_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        progress("PHYSICAL FPGA PASS — checks and DMA cleanup complete; saved " + str(result_path.resolve()))
        return report
    except BaseException as exc:
        output_dir.mkdir(parents=True, exist_ok=True)
        report = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
        error_key = "cleanup_error" if pixel_checks_finished else "workflow_error"
        report.update({"status": "FAIL", "physical_board_verified": False,
                       "dma_cleanup_verified": False,
                       error_key: "%s: %s" % (type(exc).__name__, exc),
                       "finished_at_utc": datetime.now(timezone.utc).isoformat()})
        result_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        raise
