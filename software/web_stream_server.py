"""Standalone HDMI -> FPGA -> JPEG server; main thread owns all hardware.

HTTP threads only read immutable JPEG bytes and synchronized application state.
PYNQ, OpenCV and Flask are imported at runtime so host tests need only Python.
Polling avoids asynchronous UIO waits; it does not make arbitrary PYNQ access
from concurrent threads safe.
"""
import argparse
from collections import deque
import logging
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
from frame_ownership import release_capture_frame


LOG = logging.getLogger("fpga_stream")
APPLICATION_ID = "pynq-z2-video-stream-v2"


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Timeout must be finite and positive")
    return number


def control_value(value, name, maximum):
    if isinstance(value, bool):
        raise ValueError("%s must be an integer in 0..%d" % (name, maximum))
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("%s must be an integer in 0..%d" % (name, maximum)) from exc
    if isinstance(value, float) or not 0 <= result <= maximum:
        raise ValueError("%s must be an integer in 0..%d" % (name, maximum))
    return result


class StreamState:
    """Only shared RAM state; HTTP handlers have no hardware references."""
    def __init__(self, stale_after=2.0, clock=time.monotonic):
        self.stale_after = positive_seconds(stale_after)
        self.clock = clock
        self.condition = threading.Condition()
        self.mode, self.threshold = 3, 128
        self.status, self.message = "starting", "Initializing HDMI and FPGA"
        self.error = None
        self.latest_jpeg = None
        self.sequence = 0
        self.last_frame_at = None
        self.frame_times = deque(maxlen=120)
        self.metrics = {}

    def controls(self):
        with self.condition:
            return self.mode, self.threshold

    def set_controls(self, mode=None, threshold=None):
        # Validate both fields before changing either of them.
        mode = None if mode is None else control_value(mode, "mode", 3)
        threshold = None if threshold is None else control_value(threshold, "threshold", 255)
        with self.condition:
            if mode is not None:
                self.mode = mode
            if threshold is not None:
                self.threshold = threshold
            return {"mode": self.mode, "threshold": self.threshold}

    def mark(self, status, message, error=None):
        with self.condition:
            self.status, self.message = status, str(message)
            if error is not None:
                self.error = str(error)
            self.condition.notify_all()

    def publish(self, jpeg, metrics):
        payload = bytes(jpeg)
        if not payload:
            raise ValueError("JPEG encoder returned an empty frame")
        with self.condition:
            now = self.clock()
            self.latest_jpeg, self.last_frame_at = payload, now
            self.frame_times.append(now)
            self.sequence += 1
            self.metrics = dict(metrics)
            self.status, self.message = "running", "Receiving HDMI and processing frames on FPGA"
            self.error = None
            self.condition.notify_all()

    def _snapshot_locked(self):
        now = self.clock()
        age = None if self.last_frame_at is None else max(0.0, now - self.last_frame_at)
        fresh = age is not None and age <= self.stale_after
        healthy = self.status == "running" and fresh and self.error is None
        status = "stale" if self.status == "running" and not fresh else self.status
        times = [stamp for stamp in self.frame_times if now - stamp <= 3.0]
        fps = (len(times) - 1) / (times[-1] - times[0]) if healthy and len(times) > 1 and times[-1] > times[0] else 0.0
        return {"status": status, "healthy": healthy, "message": self.message,
                "error": self.error, "fps": fps, "processed_frames": self.sequence,
                "frame_sequence": self.sequence, "frame_age_ms": None if age is None else age * 1000.0,
                "stale_after_seconds": self.stale_after, "mode": self.mode,
                "threshold": self.threshold, "metrics": dict(self.metrics),
                "fps_scope": "completed JPEG frames; not HDMI source refresh or browser display FPS"}

    def snapshot(self):
        with self.condition:
            return self._snapshot_locked()

    def wait_for_frame(self, previous_sequence, stop_event, timeout=0.5):
        # A real clock bounds Condition.wait even when tests inject a state clock.
        deadline = time.monotonic() + timeout
        with self.condition:
            while not stop_event.is_set():
                if self.sequence > previous_sequence and self._snapshot_locked()["healthy"]:
                    return self.sequence, self.latest_jpeg
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(remaining, 0.25))
        return None


def read_hdmi_frame_sync(channel, timeout=2.0, stop_event=None,
                         clock=time.monotonic, sleeper=time.sleep):
    """Bound Python polling, retaining upstream PYNQ frame ownership handling.

    The caller exclusively owns channel lifecycle. A blocked MMIO bus access
    itself cannot be cancelled by a Python deadline. None means timeout/stop.
    """
    deadline = clock() + positive_seconds(timeout)
    if stop_event is not None and stop_event.is_set():
        return None
    if not channel.running:
        raise RuntimeError("HDMI VDMA read channel is not running")
    while True:
        if stop_event is not None and stop_event.is_set():
            return None
        status = channel._mmio.read(0x34)
        if status & 1:
            raise RuntimeError("HDMI VDMA halted (status=0x%08x)" % status)
        if status & 0x60:  # Slave/decode errors always halt the VDMA.
            raise RuntimeError("HDMI VDMA memory transfer error (status=0x%08x)" % status)
        sync_errors = status & 0x8980  # SOF early/late, EOL early/late (PG020).
        if status & 0x10 and not sync_errors:
            raise RuntimeError("HDMI VDMA internal transfer error (status=0x%08x)" % status)
        if sync_errors:
            # PG020: frame-size errors can also set IntErr without halting.
            # Discard this completion; only a later clean frame can be handed
            # to the caller. Upstream activeframe/_readframe_internal likewise
            # clear sticky sync bits before interpreting the park pointer.
            channel._mmio.write(0x34, sync_errors | 0x10 | 0x4000 | 0x1000)
            channel._codex_sync_recoveries = getattr(channel, "_codex_sync_recoveries", 0) + 1
            if clock() >= deadline:
                return None
            sleeper(min(0.001, max(0, deadline - clock())))
            continue
        if status & 0x1000:
            channel._mmio.write(0x34, 0x1000)
            return channel._readframe_internal()
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        sleeper(min(0.001, remaining))


HTML_PAGE = '''<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PYNQ-Z2 FPGA Real-Time Video Stream</title><style>
body{font-family:system-ui,sans-serif;background:#0f1117;color:#e0e0e0;text-align:center;margin:0;padding:20px}
.container{max-width:860px;margin:auto}h1{color:#00e676;margin-bottom:4px;font-size:26px}
.subtitle,.note{color:#a0aec0;font-size:14px}.player-card,.slider-panel{background:#181b24;border:1px solid #2d3345;border-radius:12px;padding:14px}
.player-card{display:inline-block;max-width:100%;box-sizing:border-box}.stream-view{width:640px;height:480px;max-width:100%;border-radius:8px;background:#000;display:block;object-fit:contain}
.status-bar{display:flex;gap:16px;justify-content:space-between;flex-wrap:wrap;margin-top:12px;font-size:13px}.badge{background:#57491d;color:#ffe082;padding:3px 8px;border-radius:4px}.badge.live{background:#1b5e20;color:#69f0ae}
.controls-group{margin-top:20px;display:flex;justify-content:center;gap:10px;flex-wrap:wrap}.btn{background:#252a3a;color:white;border:1px solid #3d4560;padding:10px 18px;border-radius:6px;cursor:pointer}.btn.active{background:#00e676;color:#0f1117}
.slider-panel{margin-top:16px;display:inline-flex;align-items:center;gap:16px}.slider-panel input{width:220px;accent-color:#00e676}#message{min-height:1.5em;color:#ffe082}#metrics{line-height:1.7}
</style></head><body><div class="container"><h1>FPGA Real-Time Video Stream</h1>
<p class="subtitle">HDMI IN → ARM resize → FPGA filter → ARM JPEG → Web</p>
<div class="player-card"><img class="stream-view" src="/video_feed" alt="Dang cho frame HDMI...">
<div class="status-bar"><span id="status" class="badge">STARTING</span><span>640 × 480</span><span>Xu ly: <b id="fps">0</b> FPS</span><span>Frame: <b id="count">0</b></span></div></div>
<p id="message">Dang khoi tao...</p><p class="note" id="metrics"></p>
<div class="controls-group"><button class="btn" data-mode="0">0: Goc (Bypass)</button><button class="btn" data-mode="1">1: Xam</button><button class="btn" data-mode="2">2: Nhi phan</button><button class="btn active" data-mode="3">3: Sobel FPGA</button></div>
<div class="slider-panel"><label>Nguong: <b id="threshold-value">128</b></label><input id="threshold" type="range" min="0" max="255" value="128"></div>
<p><button class="btn" id="stop-stream">Dừng stream</button></p>
<p class="note">FPS la so frame xu ly va nen JPEG. Tan so HDMI dau vao va toc do hien thi tren trinh duyet can do rieng.</p>
</div><script>
const text=(id,value)=>document.getElementById(id).textContent=value;
async function control(query){try{let r=await fetch('/control?'+query);let d=await r.json();if(!r.ok)throw Error(d.error);document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('active',Number(b.dataset.mode)===d.mode));text('threshold-value',d.threshold);}catch(e){text('message',String(e));}}
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>control('mode='+b.dataset.mode));
document.getElementById('threshold').onchange=e=>control('threshold='+e.target.value);
document.getElementById('threshold').oninput=e=>text('threshold-value',e.target.value);
const ms=v=>typeof v==='number'?v.toFixed(1)+' ms':'--';
async function refresh(){try{let r=await fetch('/stats',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);let d=await r.json();let s=document.getElementById('status');s.textContent=d.status.toUpperCase();s.classList.toggle('live',d.healthy);text('fps',d.fps.toFixed(1));text('count',d.processed_frames);text('message',d.error||d.message);let m=d.metrics;text('metrics','Tuoi frame: '+ms(d.frame_age_ms)+' | Capture: '+ms(m.capture_ms)+' | Resize ARM: '+ms(m.resize_ms)+' | FPGA driver: '+ms(m.fpga_driver_ms)+' | JPEG ARM: '+ms(m.jpeg_ms)+' | Pipeline: '+ms(m.pipeline_ms));}catch(e){let s=document.getElementById('status');s.textContent='DISCONNECTED';s.classList.remove('live');text('fps','0');text('message','Mat ket noi may chu: '+e);}}
refresh();const refreshTimer=setInterval(refresh,1000);
document.getElementById('stop-stream').onclick=async()=>{try{let r=await fetch('/stop',{method:'POST'});if(!r.ok)throw Error('HTTP '+r.status);clearInterval(refreshTimer);document.querySelector('.stream-view').removeAttribute('src');document.querySelectorAll('button,input').forEach(e=>e.disabled=true);text('status','STOPPING');document.getElementById('status').classList.remove('live');text('message','Đã gửi yêu cầu dừng. Chạy lại notebook 04 để mở stream.');}catch(e){text('message',String(e));}};
</script></body></html>'''


def create_app(state, stop_event):
    from flask import Flask, Response, jsonify, request
    app = Flask(__name__)

    @app.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/")
    def index():
        return Response(HTML_PAGE, mimetype="text/html")

    @app.route("/control")
    def control():
        try:
            values = state.set_controls(request.args.get("mode"), request.args.get("threshold"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(dict(status="OK", **values))

    @app.route("/stats")
    def stats():
        return jsonify(server_snapshot())

    def server_snapshot():
        result = state.snapshot()
        result.update(application_id=APPLICATION_ID, server_pid=os.getpid())
        return result

    @app.route("/health")
    def health():
        result = server_snapshot()
        return jsonify(result), 200 if result["healthy"] else 503

    @app.route("/stop", methods=["POST"])
    def stop():
        state.mark("stopping", "Stopping stream; hardware cleanup is in progress")
        stop_event.set()
        return jsonify({"status": "stopping"})

    def generate_stream():
        sequence = 0
        while not stop_event.is_set():
            item = state.wait_for_frame(sequence, stop_event)
            if item is None:
                continue
            sequence, jpeg = item
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(jpeg)).encode("ascii") + b"\r\n\r\n" + jpeg + b"\r\n")

    @app.route("/video_feed")
    def video_feed():
        return Response(generate_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return app


def run_streaming(root, state, stop_event, capture_timeout, load_workflow,
                  copy_frame, resize_frame, encode_jpeg):
    """Run and close every hardware operation on this calling (main) thread."""
    workflow = None
    failed = False
    try:
        LOG.info("Loading HDMI overlay from %s", Path(root) / "overlays")
        workflow = load_workflow(Path(root) / "overlays")
        LOG.info("Overlay loaded; configuring HDMI IN and VDMA")
        setup = workflow.configure(enable_output=False, detect_timeout=15.0, processing_size=(640, 480))
        LOG.info("HDMI/VDMA configured: %s", setup)
        state.mark("waiting", "Waiting for a complete HDMI frame")
        channel = workflow.hdmi_in._vdma.readchannel
        while not stop_event.is_set():
            started = time.monotonic()
            frame = read_hdmi_frame_sync(channel, capture_timeout, stop_event)
            if frame is None:
                if not stop_event.is_set():
                    state.mark("waiting", "HDMI capture timed out; check HDMI IN cable and source signal")
                continue
            try:
                raw = copy_frame(frame, (workflow.source_mode.height, workflow.source_mode.width))
            finally:
                release_capture_frame(frame)
            captured = time.monotonic()
            source = resize_frame(raw, workflow.processing_size)
            resized = time.monotonic()
            mode, threshold = state.controls()
            actual = workflow.accelerator.process(source, mode, threshold)
            processed = time.monotonic()
            ok, jpeg = encode_jpeg(actual)
            if not ok:
                raise RuntimeError("JPEG encoding failed")
            payload = jpeg.tobytes() if hasattr(jpeg, "tobytes") else bytes(jpeg)
            finished = time.monotonic()
            metrics = {"capture_ms": (captured - started) * 1000,
                       "resize_ms": (resized - captured) * 1000,
                       "fpga_driver_ms": (processed - resized) * 1000,
                       "jpeg_ms": (finished - processed) * 1000,
                       "pipeline_ms": (finished - started) * 1000,
                       "applied_mode": mode, "applied_threshold": threshold}
            metrics["vdma_sync_recoveries"] = getattr(channel, "_codex_sync_recoveries", 0)
            driver_metrics = getattr(workflow.accelerator, "last_metrics", None) or {}
            for name in ("core_active_ms", "core_active_cycles", "pixels_in", "pixels_out"):
                if name in driver_metrics:
                    metrics[name] = driver_metrics[name]
            state.publish(payload, metrics)
    except Exception as exc:
        failed = True
        LOG.exception("Streaming failed")
        state.mark("error", "Hardware stream stopped", "%s: %s" % (type(exc).__name__, exc))
    finally:
        stop_event.set()
        if not failed:
            state.mark("stopping", "Stopping hardware")
        if workflow is not None:
            try:
                workflow.close()
            except Exception as exc:
                failed = True
                LOG.exception("Hardware cleanup failed; reboot before loading another overlay")
                previous = state.snapshot()["error"]
                error = (previous + "; " if previous else "") + "Cleanup failed: %s" % exc
                state.mark("error", "Reboot board before loading another overlay", error)
        if not failed:
            state.mark("stopped", "Stream stopped and hardware closed")
    return 1 if failed else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--capture-timeout", type=positive_seconds, default=2.0)
    parser.add_argument("--stale-after", type=positive_seconds, default=2.0)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    args.root = args.root.expanduser().resolve()
    return args


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.environ.setdefault("XILINX_XRT", "/usr")
    sys.path.insert(0, str(args.root))
    try:
        import cv2
        from werkzeug.serving import make_server
        from hdmi_workflows import HDMIWorkflow, capture_bgr, resize_bgr_nearest
        state = StreamState(args.stale_after)
        stop_event = threading.Event()
        app = create_app(state, stop_event)
        server = make_server(args.host, args.port, app, threaded=True)
    except Exception:
        LOG.exception("Cannot initialize server dependencies/listener")
        return 2

    def request_stop(signum, _frame):
        stop_event.set()

    previous_handlers = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, request_stop)
    http_thread = threading.Thread(target=server.serve_forever, name="http-server", daemon=True)
    http_thread.start()
    LOG.info("HTTP listener %s:%d; /health returns 200 only for fresh FPGA frames", args.host, args.port)
    try:
        return run_streaming(args.root, state, stop_event, args.capture_timeout,
                             HDMIWorkflow.load, capture_bgr, resize_bgr_nearest,
                             lambda image: cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 60]))
    finally:
        stop_event.set()
        with state.condition:
            state.condition.notify_all()
        server.shutdown()
        http_thread.join(timeout=5.0)
        server.server_close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
