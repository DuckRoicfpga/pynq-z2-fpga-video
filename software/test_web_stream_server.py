"""Host-only server ownership/status tests; no Flask, OpenCV, PYNQ or FPGA."""
from pathlib import Path
import signal
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import web_stream_server as stream


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class Channel:
    def __init__(self, statuses=(0x1000,), running=True):
        self.statuses = list(statuses)
        self.running = running
        self.writes = []
        self.internal_calls = 0
        self._mmio = self
        self.frame = SimpleNamespace(freebuffer=lambda: None)
    def read(self, offset):
        assert offset == 0x34
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
    def write(self, offset, value):
        self.writes.append((offset, value))
    def _readframe_internal(self):
        self.internal_calls += 1
        return self.frame


class PollingTests(unittest.TestCase):
    def test_completion_preserves_upstream_frame_hand_off(self):
        clock, channel = Clock(), Channel((0, 0, 0x1000))
        actual = stream.read_hdmi_frame_sync(channel, 0.01, clock=clock, sleeper=clock.sleep)
        self.assertIs(actual, channel.frame)
        self.assertEqual(channel.writes, [(0x34, 0x1000)])
        self.assertEqual(channel.internal_calls, 1)
        self.assertAlmostEqual(clock.now, 0.002)

    def test_monotonic_timeout_does_not_take_a_buffer(self):
        clock, channel = Clock(), Channel((0,))
        with patch.object(stream.time, "time", side_effect=AssertionError("wall clock used")):
            self.assertIsNone(stream.read_hdmi_frame_sync(channel, 0.003, clock=clock, sleeper=clock.sleep))
        self.assertEqual(channel.internal_calls, 0)
        self.assertEqual(channel.writes, [])
        self.assertAlmostEqual(clock.now, 0.003)

    def test_stop_does_not_access_hardware(self):
        stop = threading.Event()
        stop.set()
        self.assertIsNone(stream.read_hdmi_frame_sync(object(), 1, stop))

    def test_stopped_halted_and_transfer_errors_are_explicit(self):
        for channel in (Channel(running=False), Channel((1,)), Channel((0x1020,)),
                        Channel((0x10,)), Channel((0x44830,))):
            with self.subTest(channel=channel), self.assertRaises(RuntimeError):
                stream.read_hdmi_frame_sync(channel)
            self.assertEqual(channel.internal_calls, 0)

    def test_startup_sync_error_is_discarded_before_later_clean_frame(self):
        clock, channel = Clock(), Channel((0x44810, 0, 0x1000))
        result = stream.read_hdmi_frame_sync(channel, .02, clock=clock, sleeper=clock.sleep)
        self.assertIs(result, channel.frame)
        self.assertEqual(channel.writes, [(0x34, 0x5810), (0x34, 0x1000)])
        self.assertEqual(channel.internal_calls, 1)
        self.assertEqual(channel._codex_sync_recoveries, 1)

    def test_persistent_sync_error_times_out_without_returning_partial_frame(self):
        clock, channel = Clock(), Channel((0x44810,))
        self.assertIsNone(stream.read_hdmi_frame_sync(channel, .003, clock=clock, sleeper=clock.sleep))
        self.assertEqual(channel.internal_calls, 0)
        self.assertGreater(channel._codex_sync_recoveries, 0)
        self.assertAlmostEqual(clock.now, .003)

    def test_invalid_timeout(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                stream.read_hdmi_frame_sync(Channel(), timeout)


class StateTests(unittest.TestCase):
    def test_staleness_zeroes_fps_and_health(self):
        clock = Clock()
        state = stream.StreamState(stale_after=2, clock=clock)
        self.assertFalse(state.snapshot()["healthy"])
        state.publish(b"jpeg1", {"core_active_ms": 4})
        clock.sleep(0.1)
        state.publish(b"jpeg2", {})
        self.assertAlmostEqual(state.snapshot()["fps"], 10)
        clock.sleep(2.1)
        result = state.snapshot()
        self.assertFalse(result["healthy"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["fps"], 0)
        self.assertEqual(result["processed_frames"], 2)

    def test_reader_never_repeats_a_sequence_or_sends_stale_frames(self):
        clock = Clock()
        state, stop = stream.StreamState(clock=clock), threading.Event()
        state.publish(b"a", {})
        self.assertEqual(state.wait_for_frame(0, stop, timeout=0), (1, b"a"))
        self.assertIsNone(state.wait_for_frame(1, stop, timeout=0))
        clock.sleep(3)
        self.assertIsNone(state.wait_for_frame(0, stop, timeout=0))
        state.publish(b"b", {})
        self.assertEqual(state.wait_for_frame(1, stop, timeout=0), (2, b"b"))

    def test_capture_timeout_is_unhealthy_until_new_frame(self):
        state = stream.StreamState()
        state.publish(b"a", {})
        state.mark("waiting", "No HDMI frame")
        self.assertFalse(state.snapshot()["healthy"])
        self.assertEqual(state.snapshot()["fps"], 0)
        self.assertIsNone(state.wait_for_frame(0, threading.Event(), timeout=0))
        state.publish(b"b", {})
        self.assertTrue(state.snapshot()["healthy"])

    def test_controls_validate_atomically(self):
        state = stream.StreamState()
        self.assertEqual(state.set_controls("0", "255"), {"mode": 0, "threshold": 255})
        for mode, threshold in (("4", None), ("1", "256"), ("-1", None), ("1.0", None), (True, None), (None, "bad")):
            with self.subTest(mode=mode, threshold=threshold), self.assertRaises(ValueError):
                state.set_controls(mode, threshold)
            self.assertEqual(state.controls(), (0, 255))


class FakeFlask:
    def __init__(self, name):
        self.routes = {}
    def route(self, path, **options):
        def register(function):
            self.routes[path] = function
            return function
        return register
    def after_request(self, function):
        return function


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = stream.StreamState(clock=self.clock)
        self.stop = threading.Event()
        self.request = SimpleNamespace(args={})
        fake_flask = SimpleNamespace(Flask=FakeFlask, request=self.request,
            jsonify=lambda value: value, Response=lambda body, **kw: SimpleNamespace(body=body, **kw))
        with patch.dict(sys.modules, {"flask": fake_flask}):
            self.app = stream.create_app(self.state, self.stop)

    def test_health_endpoint_changes_between_503_200_and_stale_503(self):
        self.assertEqual(self.app.routes["/health"]()[1], 503)
        self.state.publish(b"one", {})
        self.assertEqual(self.app.routes["/health"]()[1], 200)
        self.clock.sleep(3)
        result, code = self.app.routes["/health"]()
        self.assertEqual((result["status"], code), ("stale", 503))

    def test_invalid_controls_return_400_without_partial_update(self):
        self.request.args = {"mode": "1", "threshold": "999"}
        result, code = self.app.routes["/control"]()
        self.assertEqual(code, 400)
        self.assertIn("threshold", result["error"])
        self.assertEqual(self.state.controls(), (3, 128))
        self.request.args = {"mode": "0", "threshold": "255"}
        self.assertEqual(self.app.routes["/control"]()["status"], "OK")

    def test_stop_signals_main_owner_and_marks_frames_unhealthy(self):
        self.state.publish(b"one", {})
        self.assertTrue(self.state.snapshot()["healthy"])
        self.assertEqual(self.app.routes["/stop"]()["status"], "stopping")
        self.assertTrue(self.stop.is_set())
        self.assertFalse(self.state.snapshot()["healthy"])
        self.assertEqual(self.app.routes["/stats"]()["application_id"], stream.APPLICATION_ID)

    def test_multiple_clients_share_jpeg_without_hardware_or_duplicates(self):
        first = self.app.routes["/video_feed"]().body
        second = self.app.routes["/video_feed"]().body
        self.state.publish(b"one", {})
        self.assertIn(b"\r\n\r\none\r\n", next(first))
        self.assertIn(b"\r\n\r\none\r\n", next(second))
        self.state.publish(b"two", {})
        self.assertIn(b"\r\n\r\ntwo\r\n", next(first))
        self.assertIn(b"\r\n\r\ntwo\r\n", next(second))
        self.stop.set()
        with self.assertRaises(StopIteration):
            next(first)
        with self.assertRaises(StopIteration):
            next(second)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.thread_id = threading.get_ident()
        self.events = []
        self.stop = threading.Event()
        self.state = stream.StreamState()
        self.channel = Channel()
        self.channel.frame = SimpleNamespace(freebuffer=lambda: self.record("free-frame"))
        self.accelerator = SimpleNamespace(process=self.process, last_metrics={"core_active_ms": 1.25})
        self.workflow = SimpleNamespace(configure=self.configure, close=lambda: self.record("close"),
            hdmi_in=SimpleNamespace(_vdma=SimpleNamespace(readchannel=self.channel)),
            source_mode=SimpleNamespace(width=1280, height=720), processing_size=(640, 480),
            accelerator=self.accelerator)

    def record(self, value):
        self.assertEqual(threading.get_ident(), self.thread_id)
        self.events.append(value)

    def configure(self, **options):
        self.record("configure")
        self.assertFalse(options["enable_output"])

    def process(self, image, mode, threshold):
        self.record("process")
        self.assertEqual(self.events[-2], "resize")
        self.assertEqual((mode, threshold), (3, 128))
        return image

    def load(self, path):
        self.record("load")
        self.assertEqual(path, Path("bundle") / "overlays")
        return self.workflow

    def copy(self, frame, shape):
        self.record("copy")
        self.assertEqual(shape, (720, 1280))
        return "raw"

    def resize(self, raw, size):
        self.record("resize")
        self.assertIn("free-frame", self.events)
        self.assertEqual(size, (640, 480))
        return "resized"

    def encode(self, image):
        self.record("encode")
        self.stop.set()
        return True, b"jpeg"

    def run_pipeline(self):
        return stream.run_streaming("bundle", self.state, self.stop, 0.1,
                                    self.load, self.copy, self.resize, self.encode)

    def test_all_hardware_and_cleanup_use_calling_thread(self):
        self.assertEqual(self.run_pipeline(), 0)
        self.assertEqual(self.events, ["load", "configure", "copy", "free-frame", "resize", "process", "encode", "close"])
        result = self.state.snapshot()
        self.assertEqual(result["processed_frames"], 1)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["metrics"]["core_active_ms"], 1.25)

    def test_configuration_failure_closes_partial_workflow(self):
        self.workflow.configure = lambda **kw: (_ for _ in ()).throw(RuntimeError("no source"))
        with self.assertLogs(stream.LOG, level="ERROR"):
            self.assertEqual(self.run_pipeline(), 1)
        self.assertEqual(self.events, ["load", "close"])
        self.assertIn("no source", self.state.snapshot()["error"])

    def test_copy_failure_returns_frame_and_reports_error(self):
        self.copy = lambda *args: (_ for _ in ()).throw(RuntimeError("bad shape"))
        with self.assertLogs(stream.LOG, level="ERROR"):
            self.assertEqual(self.run_pipeline(), 1)
        self.assertEqual(self.events[-2:], ["free-frame", "close"])
        self.assertEqual(self.state.snapshot()["status"], "error")
        self.assertFalse(self.state.snapshot()["healthy"])

    def test_processing_failure_is_fatal_not_silently_retried(self):
        self.accelerator.process = lambda *args: (_ for _ in ()).throw(RuntimeError("DMA fault"))
        with self.assertLogs(stream.LOG, level="ERROR"):
            self.assertEqual(self.run_pipeline(), 1)
        self.assertEqual(self.events[-1], "close")
        self.assertEqual(self.state.snapshot()["processed_frames"], 0)
        self.assertIn("DMA fault", self.state.snapshot()["error"])

    def test_cleanup_failure_is_fatal(self):
        self.workflow.close = lambda: (_ for _ in ()).throw(RuntimeError("stop failed"))
        with self.assertLogs(stream.LOG, level="ERROR"):
            self.assertEqual(self.run_pipeline(), 1)
        self.assertEqual(self.state.snapshot()["status"], "error")
        self.assertIn("Cleanup failed", self.state.snapshot()["error"])

    def test_sigterm_stops_main_owned_hardware_before_http_server_closes(self):
        handlers, http_threads = {}, []
        server_stopped = threading.Event()
        old_handler = object()

        def install_handler(signum, handler):
            previous = handlers.get(signum, old_handler)
            handlers[signum] = handler
            return previous

        def serve_forever():
            http_threads.append(threading.get_ident())
            server_stopped.wait(3)

        def shutdown():
            self.assertEqual(self.events[-1], "close")
            server_stopped.set()

        def encode_jpeg(extension, image, options):
            self.record("encode")
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return True, b"jpeg"

        server = SimpleNamespace(serve_forever=serve_forever, shutdown=shutdown,
                                 server_close=lambda: self.record("http-close"))
        fake_modules = {
            "cv2": SimpleNamespace(imencode=encode_jpeg, IMWRITE_JPEG_QUALITY=1),
            "werkzeug.serving": SimpleNamespace(make_server=lambda *a, **kw: server),
            "hdmi_workflows": SimpleNamespace(HDMIWorkflow=SimpleNamespace(load=self.load),
                                               capture_bgr=self.copy, resize_bgr_nearest=self.resize)}
        args = SimpleNamespace(root=Path("bundle"), stale_after=2, host="127.0.0.1", port=8080, capture_timeout=1)
        with patch.dict(sys.modules, fake_modules), patch.object(stream, "parse_args", return_value=args), \
                patch.object(stream, "create_app", return_value=object()), \
                patch.object(stream.signal, "signal", side_effect=install_handler), \
                patch.object(stream.logging, "basicConfig"), patch.object(sys, "path", list(sys.path)), \
                patch.dict(stream.os.environ, {}, clear=False):
            self.assertEqual(stream.main([]), 0)
        self.assertTrue(http_threads)
        self.assertNotEqual(http_threads[0], self.thread_id)
        self.assertEqual(self.events[-2:], ["close", "http-close"])
        self.assertIs(handlers[signal.SIGINT], old_handler)
        self.assertIs(handlers[signal.SIGTERM], old_handler)


if __name__ == "__main__":
    unittest.main()
