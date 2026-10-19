"""HDMI host transport tests with fakes; never substitutes for a board run."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from hdmi_workflows import HDMIWorkflow, capture_bgr, _bounded_control, run_hdmi_demo, resize_bgr_nearest
from reference_filters import apply_filter
from video_driver import VideoHardwareError


class Frame(np.ndarray):
    def __new__(cls, shape):
        value = np.zeros(shape, dtype=np.uint8).view(cls)
        value.free_count = 0
        return value
    def freebuffer(self): self.free_count += 1


class MMIO:
    def read(self, offset): return 0
    def write(self, offset, value): pass


class Input:
    def __init__(self, width=8, height=6):
        self.mode = SimpleNamespace(width=width, height=height, bits_per_pixel=32, fps=60)
        self._vdma = SimpleNamespace(readchannel=SimpleNamespace(_mmio=MMIO()))
        self.detect_timeouts = []
        self.frontend = SimpleNamespace(start=lambda init_timeout=60: self.detect_timeouts.append(init_timeout))
        self.frames = []
        self.started = self.closed = False
        self.timeout = False
        self.configure_error = False
    def configure(self, pixel_format):
        self.frontend.start()
        if self.configure_error: raise RuntimeError("No HDMI signal")
    def start(self): self.started = True
    def close(self): self.closed = True
    async def readframe_async(self):
        if self.timeout: await asyncio.sleep(10)
        frame = Frame((self.mode.height, self.mode.width, 4))
        frame[:, :, 0] = 17
        frame[:, :, 1] = 39
        frame[:, :, 2] = 231
        frame[:, :, 3] = 255  # Pad byte is intentionally ignored, not treated as R.
        self.frames.append(frame)
        return frame


class Output:
    def __init__(self):
        self._vdma = SimpleNamespace(writechannel=SimpleNamespace(_mmio=MMIO(), _frames=[None] * 4))
        self.started = self.closed = False
        self.buffers = []
        self.mode = None
        self.fail = None
    def configure(self, mode, pixel_format): self.mode = mode
    def start(self): self.started = True
    def close(self): self.closed = True
    def newframe(self):
        frame = Frame((self.mode.height, self.mode.width, 4))
        self.buffers.append(frame)
        return frame
    async def writeframe_async(self, frame):
        if self.fail == "timeout": await asyncio.sleep(10)
        if self.fail == "before": raise RuntimeError("before ownership")
        self._vdma.writechannel._frames[0] = frame
        if self.fail == "after": raise RuntimeError("after ownership")


class Accelerator:
    def __init__(self):
        self.physical_backend = True  # Test double; report stays in temporary directory.
        self.fingerprint = {"mock": True}
        self.closed = False
        self.calls = []
        self.bad_pixels = False
    def process(self, source, mode, threshold):
        self.calls.append((source.copy(), mode, threshold))
        self.last_metrics = {"total_driver_ms": .01, "mode": mode}
        actual = apply_filter(source, mode, threshold)
        if self.bad_pixels: actual[0, 0, 0] ^= np.uint8(1)
        return actual
    def close(self): self.closed = True


def workflow(width=8, height=6):
    return HDMIWorkflow(Accelerator(), Input(width, height), Output(), control_timeout=.01)


class HDMITests(unittest.IsolatedAsyncioTestCase):
    def test_bgr0_copy_and_invalid_shape(self):
        frame = Frame((6, 8, 4))
        frame[:] = [3, 7, 19, 255]
        image = capture_bgr(frame, (6, 8))
        frame[:] = 0
        np.testing.assert_array_equal(image[0, 0], [3, 7, 19])
        with self.assertRaises(VideoHardwareError): capture_bgr(np.zeros((6, 8, 3), np.uint8), (6, 8))

    def test_configure_deadline_restores_frontend_and_rejects_1080p(self):
        demo = workflow(1920, 1080)
        original = demo.hdmi_in.frontend.start
        with self.assertRaisesRegex(VideoHardwareError, "unsupported"):
            demo.configure(pixel_format=SimpleNamespace(bits_per_pixel=32), detect_timeout=2.1)
        self.assertEqual(demo.hdmi_in.detect_timeouts, [3])
        self.assertIs(demo.hdmi_in.frontend.start, original)
        demo.close()
        self.assertTrue(demo.hdmi_in.closed)
        demo = workflow()
        demo.hdmi_in.configure_error = True
        original = demo.hdmi_in.frontend.start
        with self.assertRaisesRegex(RuntimeError, "signal"):
            demo.configure(pixel_format=SimpleNamespace(bits_per_pixel=32))
        self.assertIs(demo.hdmi_in.frontend.start, original)
        demo.close()

    def test_vdma_control_poll_is_bounded_and_mmio_restored(self):
        channel = SimpleNamespace(_mmio=MMIO())
        original = channel._mmio
        with self.assertRaisesRegex(VideoHardwareError, "control timeout"):
            with _bounded_control(channel, .001):
                while channel._mmio.read(0) == 0: pass
        self.assertIs(channel._mmio, original)

    def test_nearest_resize_preserves_aspect_and_deterministic_indices(self):
        image = np.arange(720 * 1280 * 3, dtype=np.uint8).reshape(720, 1280, 3)
        output = resize_bgr_nearest(image, (640, 480))
        self.assertEqual(output.shape, (480, 640, 3))
        self.assertTrue(np.all(output[:60] == 0))
        self.assertTrue(np.all(output[420:] == 0))
        np.testing.assert_array_equal(output[60, 0], image[0, 0])
        np.testing.assert_array_equal(output[419, 639], image[718, 1278])
        self.assertIs(resize_bgr_nearest(image, None), image)
        with self.assertRaises(ValueError): resize_bgr_nearest(image, (0, 480))

    async def test_720p_capture_vga_processing_output_dimensions_and_timing(self):
        demo = workflow(1280, 720)
        with patch("hdmi_workflows._output_mode", side_effect=lambda w, h, fps: SimpleNamespace(
                width=w, height=h, bits_per_pixel=32, fps=fps)) as mode_factory:
            setup = demo.configure(enable_output=True, pixel_format=SimpleNamespace(bits_per_pixel=32), processing_size=(640, 480))
        mode_factory.assert_called_once_with(640, 480, 60)
        self.assertEqual((setup["source_width"], setup["source_height"]), (1280, 720))
        self.assertEqual((demo.hdmi_out.mode.width, demo.hdmi_out.mode.height), (640, 480))
        with tempfile.TemporaryDirectory() as tmp:
            report = await demo.run(frames=1, mode=0, report_path=Path(tmp) / "results.json")
        self.assertEqual((report["source_width"], report["source_height"]), (1280, 720))
        self.assertEqual((report["processing_width"], report["processing_height"]), (640, 480))
        self.assertEqual(demo.accelerator.calls[0][0].shape, (480, 640, 3))
        self.assertTrue(np.all(demo.accelerator.calls[0][0][:60] == 0))
        self.assertEqual(demo.hdmi_out.buffers[0].shape, (480, 640, 4))
        self.assertGreaterEqual(report["arm_resize_time"]["mean_ms"], 0)
        self.assertGreaterEqual(report["whole_frame_without_preview"]["mean_ms"], report["arm_resize_time"]["mean_ms"])
        self.assertTrue(all(frame.free_count == 1 for frame in demo.hdmi_in.frames))
        demo.close()

    async def test_preview_fpga_pixels_buffer_release_and_measured_fields(self):
        demo = workflow()
        demo.configure(pixel_format=SimpleNamespace(bits_per_pixel=32))
        previews = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.json"
            report = await demo.run(frames=4, preview_every=2, verify_every=2,
                controls=lambda index: (index % 4, 128), report_path=path,
                preview=lambda source, actual, index, metrics: previews.append(index))
            self.assertEqual(json.loads(path.read_text())["status"], "PASS")
        self.assertEqual(report["frames_processed"], 4)
        self.assertEqual(len(demo.accelerator.calls), 4)
        self.assertEqual(previews, [0, 2, 3])
        self.assertTrue(all(frame.free_count == 1 for frame in demo.hdmi_in.frames))
        self.assertIsNone(report["source_frames_dropped"])
        self.assertIsNone(report["source_frame_rate_measured"])
        self.assertFalse(report["hdmi_output_visible_verified"])
        self.assertFalse(demo.hdmi_out.started)
        demo.close()
        self.assertTrue(demo.accelerator.closed)

    async def test_read_timeout_and_pixel_failure_are_fail_not_cpu_fallback(self):
        for cause in ("timeout", "pixels"):
            demo = workflow()
            demo.configure(pixel_format=SimpleNamespace(bits_per_pixel=32))
            demo.hdmi_in.timeout = cause == "timeout"
            demo.accelerator.bad_pixels = cause == "pixels"
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "failure.json"
                with self.assertRaises((VideoHardwareError, AssertionError)):
                    await demo.run(frames=1, frame_timeout=.001, report_path=path)
                report = json.loads(path.read_text())
                self.assertEqual(report["status"], "FAIL")
                self.assertFalse(report["physical_board_verified"])
                self.assertEqual(report["frames_processed"], 0)
            self.assertTrue(all(frame.free_count == 1 for frame in demo.hdmi_in.frames))
            demo.close()

    async def test_output_zeros_pad_and_obeys_ownership_on_timeout_or_failure(self):
        demo = workflow(640, 480)
        demo.configure(enable_output=True, pixel_format=SimpleNamespace(bits_per_pixel=32))
        image = np.full((480, 640, 3), 87, np.uint8)
        await demo._write_output(image, .01)
        submitted = demo.hdmi_out.buffers[-1]
        self.assertTrue(np.all(submitted[:, :, :3] == 87))
        self.assertTrue(np.all(submitted[:, :, 3] == 0))
        self.assertEqual(submitted.free_count, 0)
        for cause in ("timeout", "before", "after"):
            demo.hdmi_out.fail = cause
            with self.assertRaises((asyncio.TimeoutError, RuntimeError)):
                await demo._write_output(image, .001)
            expected_frees = 0 if cause == "after" else 1
            self.assertEqual(demo.hdmi_out.buffers[-1].free_count, expected_frees)
        demo.close()

    async def test_mock_backend_and_invalid_limits_rejected(self):
        demo = workflow()
        demo.configure(pixel_format=SimpleNamespace(bits_per_pixel=32))
        demo.accelerator.physical_backend = False
        with self.assertRaisesRegex(VideoHardwareError, "real PYNQ"):
            await demo.run(frames=1)
        demo.accelerator.physical_backend = True
        for options in ({"frames": 0}, {"frame_timeout": float("nan")}, {"mode": 4}, {"verify_every": 0}):
            with self.assertRaises(ValueError): await demo.run(**options)
        demo.close()

    async def test_cleanup_failure_revokes_report_pass(self):
        demo = workflow()
        real_configure = demo.configure
        demo.configure = lambda enable_output, detect_timeout, processing_size=None: real_configure(
            enable_output, detect_timeout, SimpleNamespace(bits_per_pixel=32), processing_size)
        demo.hdmi_in.close = lambda: (_ for _ in ()).throw(RuntimeError("VDMA stop failed"))
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(HDMIWorkflow, "load", return_value=demo):
                with self.assertRaisesRegex(VideoHardwareError, "cleanup failed"):
                    await run_hdmi_demo(tmp, frames=1)
            report = json.loads((Path(tmp) / "results" / "hdmi_results.json").read_text())
            self.assertEqual(report["status"], "FAIL")
            self.assertFalse(report["physical_board_verified"])
            self.assertIn("VDMA stop failed", report["cleanup_error"])


if __name__ == "__main__": unittest.main()
