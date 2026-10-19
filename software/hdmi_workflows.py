"""PYNQ-Z2 HDMI capture -> custom FPGA filter -> preview/optional HDMI output.

Uses PYNQ v3.0.1 video hierarchy API. CPU reference is used only for checks.
No CPU fallback, unbounded capture loop or assertion of unmeasured source FPS.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import numpy as np
from board_workflows import assert_pixels, stats_ms
from reference_filters import apply_filter, Mode, validate_image
from video_driver import VideoAccelerator, VideoHardwareError, validate_overlay_files
from frame_ownership import release_capture_frame


def _positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")
    return value


class _DeadlineMMIO:
    """Bound upstream Python VDMA start/stop polls without background threads.

    PYNQ's readframe_async is cancellable; its start/stop polling loops are not.
    The temporary proxy enforces a deadline on the channel's existing MMIO reads.
    On timeout channel-owned buffers stay allocated: reboot before reprogramming.
    """
    def __init__(self, mmio, timeout):
        self.mmio = mmio
        self.deadline = time.monotonic() + timeout

    def _check(self):
        if time.monotonic() >= self.deadline:
            raise VideoHardwareError("VDMA control timeout; reboot board before reprogramming if stop fails.")

    def read(self, *args, **kwargs):
        self._check()
        return self.mmio.read(*args, **kwargs)

    def write(self, *args, **kwargs):
        self._check()
        return self.mmio.write(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.mmio, name)


@contextmanager
def _bounded_control(channel, timeout):
    original = channel._mmio
    channel._mmio = _DeadlineMMIO(original, timeout)
    try:
        yield
    finally:
        channel._mmio = original


def capture_bgr(frame, expected_shape):
    """Detach BGR0 VDMA frame before returning its allocation to the frame cache."""
    value = np.asarray(frame)
    if value.dtype != np.uint8 or value.shape != tuple(expected_shape) + (4,):
        raise VideoHardwareError("Unexpected HDMI frame type/shape; require H x W x 4 BGR0.")
    return value[:, :, :3].copy()


def _processing_dimensions(size, source_width, source_height):
    if size is None:
        return int(source_width), int(source_height)
    if not isinstance(size, (tuple, list)) or len(size) != 2:
        raise ValueError("processing_size must be None or (width, height)")
    width, height = size
    if (not isinstance(width, int) or not isinstance(height, int)
            or not 3 <= width <= 1280 or not 3 <= height <= 720):
        raise ValueError("Processing dimensions require integer width 3..1280, height 3..720")
    return width, height


def resize_bgr_nearest(image, size):
    """ARM preprocessing: nearest-neighbour fit plus centered zero padding.

    Rational index arithmetic preserves aspect ratio. 1280x720 -> 640x480
    produces 640x360 active content and 60 black rows above and below.
    This resize is not an FPGA accelerator operation.
    """
    source = validate_image(image)
    source_height, source_width = source.shape[:2]
    width, height = _processing_dimensions(size, source_width, source_height)
    if (width, height) == (source_width, source_height):
        return source
    if width * source_height <= height * source_width:
        fitted_width = width
        fitted_height = max(1, source_height * width // source_width)
    else:
        fitted_height = height
        fitted_width = max(1, source_width * height // source_height)
    x = np.arange(fitted_width, dtype=np.int64) * source_width // fitted_width
    y = np.arange(fitted_height, dtype=np.int64) * source_height // fitted_height
    output = np.zeros((height, width, 3), dtype=np.uint8)
    left, top = (width - fitted_width) // 2, (height - fitted_height) // 2
    output[top:top + fitted_height, left:left + fitted_width] = source[y[:, None], x[None, :]]
    return output


def _output_mode(width, height, fps):
    from pynq.lib.video import VideoMode
    return VideoMode(width, height, 32, fps)


class HDMIWorkflow:
    def __init__(self, accelerator, hdmi_in, hdmi_out, control_timeout=5.0, startup_timeout=30.0):
        self.accelerator = accelerator
        self.hdmi_in = hdmi_in
        self.hdmi_out = hdmi_out
        self.control_timeout = _positive(control_timeout, "control_timeout")
        self.startup_timeout = _positive(startup_timeout, "startup_timeout")
        self.configured = False
        self.input_touched = False
        self.output_touched = False
        self.enable_output = False
        self.closed = False

    @classmethod
    def load(cls, overlay_dir="overlays", timeout=5.0):
        bit_path, manifest = validate_overlay_files(overlay_dir, "hdmi_overlay_manifest.json")
        try:
            import pynq.lib.video  # Registers hierarchy drivers before Overlay construction.
            from pynq import Overlay
        except ImportError as exc:
            raise VideoHardwareError("Run HDMI notebook on the PYNQ-Z2 board Jupyter.") from exc
        overlay = Overlay(str(bit_path), download=True)
        try:
            hdmi_in, hdmi_out = overlay.video.hdmi_in, overlay.video.hdmi_out
            if not callable(getattr(hdmi_in, "readframe_async", None)):
                raise VideoHardwareError("HDMI hierarchy not bound: check PYNQ image/video driver compatibility.")
            accelerator = VideoAccelerator.from_overlay(overlay, manifest, timeout)
        except Exception as exc:
            raise VideoHardwareError("Cannot bind HDMI overlay: %s" % exc) from exc
        return cls(accelerator, hdmi_in, hdmi_out, timeout)

    def configure(self, enable_output=False, detect_timeout=15.0, pixel_format=None, processing_size=None):
        if self.closed or self.configured:
            raise VideoHardwareError("Create a fresh HDMIWorkflow before configuring again.")
        detect_timeout = _positive(detect_timeout, "detect_timeout")
        if pixel_format is None:
            from pynq.lib.video import PixelFormat, COLOR_IN_BGR, COLOR_OUT_BGR
            pixel_format = PixelFormat(32, COLOR_IN_BGR, COLOR_OUT_BGR)
        if pixel_format.bits_per_pixel != 32:
            raise ValueError("HDMI workflow requires explicit BGR32 format, not PIXEL_RGBA.")
        # Official VideoIn.configure calls frontend.start() without forwarding a timeout.
        # Supply the documented DVI frontend init_timeout for that one call, then restore.
        frontend = self.hdmi_in.frontend
        original_start = frontend.start
        frontend.start = lambda: original_start(init_timeout=max(1, int(math.ceil(detect_timeout))))
        self.input_touched = True
        try:
            with _bounded_control(self.hdmi_in._vdma.readchannel, self.control_timeout + math.ceil(detect_timeout)):
                self.hdmi_in.configure(pixel_format)
        finally:
            frontend.start = original_start
        mode = self.hdmi_in.mode
        width, height = int(mode.width), int(mode.height)
        if width < 3 or height < 3 or width > 1280 or height > 720:
            raise VideoHardwareError("HDMI source %dx%d unsupported: set Windows HDMI input to 1280x720 at 60 Hz." % (width, height))
        if int(mode.bits_per_pixel) != 32:
            raise VideoHardwareError("HDMI capture did not select 32 bits per pixel.")
        self.source_mode = mode
        self.processing_size = _processing_dimensions(processing_size, width, height)
        processing_width, processing_height = self.processing_size
        self.enable_output = bool(enable_output)
        if self.enable_output:
            if self.processing_size not in ((640, 480), (800, 600), (1280, 720)):
                raise VideoHardwareError("HDMI output accepts VGA, 800x600 or 720p in this demo.")
            output_mode = mode if self.processing_size == (width, height) else _output_mode(
                processing_width, processing_height, mode.fps)
            self.output_touched = True
            with _bounded_control(self.hdmi_out._vdma.writechannel, self.control_timeout):
                self.hdmi_out.configure(output_mode, pixel_format)
                self.hdmi_out.start()
        # Starting includes frame-cache allocation and cold Python/device setup.
        # Give that phase its own budget; DMA transfers and stop retain the
        # shorter control_timeout and remain independently bounded.
        with _bounded_control(self.hdmi_in._vdma.readchannel, max(self.control_timeout, self.startup_timeout)):
            self.hdmi_in.start()
        self.configured = True
        return {"source_width": width, "source_height": height,
                "processing_width": processing_width, "processing_height": processing_height, "bits_per_pixel": 32,
                "resize_backend": "ARM NumPy nearest-neighbour fit + centered black padding",
                "video_mode_fps_metadata_not_measurement": float(mode.fps),
                "hdmi_output_enabled": self.enable_output}

    async def _read_bgr(self, timeout):
        try:
            frame = await asyncio.wait_for(self.hdmi_in.readframe_async(), timeout)
        except asyncio.TimeoutError as exc:
            raise VideoHardwareError("HDMI capture timed out; check HDMI IN cable and select Windows output 1280x720 at 60 Hz.") from exc
        try:
            return capture_bgr(frame, (self.source_mode.height, self.source_mode.width))
        finally:
            release_capture_frame(frame)

    async def _write_output(self, image, timeout):
        frame = self.hdmi_out.newframe()
        try:
            if frame.shape != image.shape[:2] + (4,) or frame.dtype != np.uint8:
                raise VideoHardwareError("Unexpected HDMI output buffer shape/type.")
            frame[:] = 0
            frame[:, :, :3] = image
            await asyncio.wait_for(self.hdmi_out.writeframe_async(frame), timeout)
            # Ownership is now VDMA's. Do not free/access this allocation.
        except BaseException:
            # A cancelled await has not handed over ownership. On a synchronous
            # MMIO failure after queue insertion, the upstream frame table owns it.
            queued = self.hdmi_out._vdma.writechannel._frames
            if not any(queued[index] is frame for index in range(len(queued))):
                release_capture_frame(frame)
            raise

    async def run(self, frames=120, mode=3, threshold=128, frame_timeout=3.0,
                  preview=None, preview_every=15, verify_every=30, controls=None,
                  report_path="results/hdmi_results.json"):
        if not self.configured or self.closed:
            raise VideoHardwareError("Configure HDMI before run().")
        if not getattr(self.accelerator, "physical_backend", False):
            raise VideoHardwareError("HDMI physical PASS requires a real PYNQ MMIO accelerator.")
        for value, name in ((frames, "frames"), (preview_every, "preview_every"), (verify_every, "verify_every")):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(name + " must be a positive integer")
        frame_timeout = _positive(frame_timeout, "frame_timeout")
        mode = int(Mode(mode))
        if not isinstance(threshold, int) or not 0 <= threshold <= 255:
            raise ValueError("Threshold must be integer 0..255")
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {"schema_version": 1, "status": "RUNNING", "physical_board_verified": False,
                  "backend": "HDMI VDMA -> custom FPGA AXI DMA filter",
                  "started_at_utc": datetime.now(timezone.utc).isoformat(),
                  "overlay_manifest": self.accelerator.fingerprint,
                  "source_width": int(self.source_mode.width), "source_height": int(self.source_mode.height),
                  "width": self.processing_size[0], "height": self.processing_size[1],
                  "processing_width": self.processing_size[0], "processing_height": self.processing_size[1],
                  "resize_backend": "ARM NumPy nearest-neighbour fit + centered black padding",
                  "frames_requested": frames, "frames_processed": 0, "preview_frames": 0,
                  "hdmi_frames_submitted": 0, "hdmi_output_enabled": self.enable_output,
                  "hdmi_output_visible_verified": False, "pixel_checks": [], "frame_metrics": [],
                  "source_frames_dropped": None, "source_frame_rate_measured": None,
                  "source_frame_ids_available": False,
                  "video_mode_fps_metadata_not_measurement": float(self.source_mode.fps),
                  "notes": ["VDMA may skip source frames when software is slower; drop count is unknown.",
                            "Frames processed are capture completions, not proof of distinct source content.",
                            "HDMI output refresh metadata is not new processed-frame throughput.",
                            "Browser rendering completion is not measured by the Python preview callback."]}
        def save():
            path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        save()
        driver_times = []
        resize_times = []
        frame_times = []
        datapath_seconds = 0.0
        started = time.perf_counter()
        try:
            # Upstream documents one possibly stale buffered frame after a pause.
            await self._read_bgr(frame_timeout)
            for index in range(frames):
                stage_started = time.perf_counter()
                current_mode, current_threshold = (mode, threshold) if controls is None else controls(index)
                current_mode = int(Mode(current_mode))
                captured = await self._read_bgr(frame_timeout)
                resize_started = time.perf_counter()
                source = resize_bgr_nearest(captured, self.processing_size)
                resize_ms = (time.perf_counter() - resize_started) * 1000
                actual = self.accelerator.process(source, current_mode, current_threshold)
                metrics = self.accelerator.last_metrics.copy()
                if index % verify_every == 0 or index == frames - 1:
                    assert_pixels(actual, apply_filter(source, current_mode, current_threshold), "HDMI frame%d" % index)
                    report["pixel_checks"].append({"frame_index": index, "mode": current_mode, "status": "PASS", "mismatched_pixels": 0})
                if self.enable_output:
                    await self._write_output(actual, frame_timeout)
                    report["hdmi_frames_submitted"] += 1
                frame_ms = (time.perf_counter() - stage_started) * 1000
                datapath_seconds += frame_ms / 1000
                resize_times.append(resize_ms)
                frame_times.append(frame_ms)
                metrics.update({"arm_resize_ms": resize_ms, "whole_frame_without_preview_ms": frame_ms,
                                "whole_frame_includes_capture_resize_driver_pixel_check_and_output": True})
                driver_times.append(metrics["total_driver_ms"])
                report["frame_metrics"].append(metrics)
                report["frames_processed"] += 1
                if preview is not None and (index % preview_every == 0 or index == frames - 1):
                    preview(source, actual, index, metrics)
                    report["preview_frames"] += 1
                await asyncio.sleep(0)  # Let Jupyter deliver widget updates / interruption.
            report["status"] = "PASS"
            report["physical_board_verified"] = True
            return report
        except BaseException as exc:
            report["status"] = "FAIL"
            report["error"] = "%s: %s" % (type(exc).__name__, exc)
            raise
        finally:
            elapsed = time.perf_counter() - started
            report["loop_seconds_including_preview_and_warmup"] = elapsed
            report["observed_processed_frames_per_second_including_preview"] = report["frames_processed"] / elapsed
            report["capture_filter_output_seconds_including_pixel_checks"] = datapath_seconds
            report["driver_time"] = stats_ms(driver_times) if driver_times else None
            report["arm_resize_time"] = stats_ms(resize_times) if resize_times else None
            report["whole_frame_without_preview"] = stats_ms(frame_times) if frame_times else None
            report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            save()

    def close(self):
        if self.closed:
            return
        errors = []
        for touched, port, channel in ((self.input_touched, self.hdmi_in, self.hdmi_in._vdma.readchannel),
                                       (self.output_touched, self.hdmi_out, self.hdmi_out._vdma.writechannel)):
            if touched:
                try:
                    with _bounded_control(channel, self.control_timeout):
                        port.close()
                except Exception as exc:
                    errors.append(str(exc))
        try:
            self.accelerator.close()
        except Exception as exc:
            errors.append(str(exc))
        if errors:
            raise VideoHardwareError("HDMI cleanup failed; reboot before changing overlay: " + "; ".join(errors))
        self.closed = True


async def run_hdmi_demo(root=".", enable_output=False, detect_timeout=15.0, processing_size=None, **run_options):
    """One bounded notebook call, including cleanup on error or interruption."""
    root = Path(root)
    workflow = None
    report_path = root / "results" / "hdmi_results.json"
    try:
        workflow = HDMIWorkflow.load(root / "overlays")
        print("HDMI source:", workflow.configure(enable_output, detect_timeout, processing_size=processing_size))
        run_options.setdefault("report_path", report_path)
        return await workflow.run(**run_options)
    except BaseException as exc:
        if workflow is None or not workflow.configured:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({"status": "FAIL", "physical_board_verified": False,
                "error": "%s: %s" % (type(exc).__name__, exc)}, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        if workflow is not None:
            try:
                workflow.close()
            except Exception as exc:
                # A pixel PASS is insufficient if DMA could not be safely stopped.
                path = Path(run_options.get("report_path", report_path))
                existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
                existing.update({"status": "FAIL", "physical_board_verified": False,
                                 "cleanup_error": "%s: %s" % (type(exc).__name__, exc)})
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                raise
