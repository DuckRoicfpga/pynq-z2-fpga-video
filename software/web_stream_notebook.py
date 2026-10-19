"""Launch one standalone hardware owner from notebook 04; standard library only."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

APPLICATION_ID = 'pynq-z2-video-stream-v2'
RUN_DIR = Path('/dev/shm/pynq_z2_video_web')


def _process_state(pid, script):
    try:
        command = Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\0')
        status = Path('/proc/%d/status' % pid).read_text()
    except FileNotFoundError:
        return False
    if os.fsencode(str(script)) not in command:
        return False
    if 'CoreDumping:\t1' in status:
        raise RuntimeError('Tiến trình video đã crash; cần khôi phục board trước khi nạp lại FPGA.')
    state = next((line.split()[1] for line in status.splitlines() if line.startswith('State:')), '')
    return state not in ('Z', 'X')


def read_stats(timeout=2):
    opener = build_opener(ProxyHandler({}))
    with opener.open('http://127.0.0.1:8080/stats', timeout=timeout) as response:
        return json.load(response)


def start_stream(root, startup_timeout=100, report=print):
    import fcntl
    root = Path(root).resolve()
    script = root / 'web_stream_server.py'
    if not script.is_file():
        raise FileNotFoundError('Thiếu web_stream_server.py bên cạnh notebook 04.')
    manifest = root / 'web_stream_runtime_manifest.json'
    if not manifest.is_file():
        raise FileNotFoundError('Thiếu manifest phần mềm web stream; cần cập nhật đủ gói.')
    for name, expected in json.loads(manifest.read_text())['files_sha256'].items():
        if Path(name).name != name or hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('File phần mềm không khớp manifest: ' + name)
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    RUN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    pidfile, logfile = RUN_DIR / 'process.json', RUN_DIR / 'server.log'
    reused = False
    with (RUN_DIR / 'launch.lock').open('a') as lock:
        until = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise RuntimeError('Một notebook khác đang mở stream; đợi vài giây rồi chạy lại.')
                time.sleep(.1)
        try:
            saved = json.loads(pidfile.read_text()) if pidfile.exists() else {}
            pid = saved.get('pid')
            if isinstance(pid, int) and _process_state(pid, script):
                if saved.get('sha256') != digest:
                    raise RuntimeError('Hãy bấm Dừng stream trên web trước khi chạy phiên bản phần mềm mới.')
                reused = True
            else:
                with socket.socket() as probe:
                    probe.settimeout(.3)
                    if probe.connect_ex(('127.0.0.1', 8080)) == 0:
                        raise RuntimeError('Cổng 8080 đang được chương trình khác dùng. Dừng chương trình đó trước.')
                env = dict(os.environ, XILINX_XRT='/usr', PYTHONDONTWRITEBYTECODE='1')
                with logfile.open('wb') as output:
                    child = subprocess.Popen([sys.executable, '-u', '-B', '-X', 'faulthandler', str(script), '--root', str(root)],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        cwd=root, env=env, start_new_session=True)
                pid = child.pid
                pidfile.write_text(json.dumps({'pid':pid, 'sha256':digest, 'script':str(script)}))
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    report(('Dùng lại' if reused else 'Đã mở') + ' tiến trình video PID %d.' % pid)
    deadline, previous, first_sequence = time.monotonic()+startup_timeout, None, None
    while time.monotonic() < deadline:
        if not _process_state(pid, script):
            tail = logfile.read_text(errors='replace')[-6000:] if logfile.exists() else ''
            raise RuntimeError('Server video đã dừng. Log:\n' + tail)
        try:
            stats = read_stats()
            if stats.get('application_id') != APPLICATION_ID or stats.get('server_pid') != pid:
                raise RuntimeError('Cổng 8080 không thuộc tiến trình vừa khởi động; không nạp thêm overlay.')
            status = (stats.get('status'), stats.get('message'))
            if status != previous:
                report('Trạng thái: %s — %s' % status)
                previous = status
            if stats.get('error'):
                raise RuntimeError(stats['error'])
            if stats.get('healthy'):
                sequence = stats['processed_frames']
                if first_sequence is not None and sequence > first_sequence:
                    report('Đã nhận frame liên tục. Mở khung video bên dưới hoặc trang web riêng.')
                    return stats
                first_sequence = sequence
        except (OSError, HTTPError):
            pass
        time.sleep(.5)
    raise TimeoutError('Chưa có frame HDMI hợp lệ. Kiểm tra HDMI IN và tín hiệu 1280×720, 60 Hz; log: ' + str(logfile))
