"""Notebook 04: run all starts/reuses a standalone video process, then embeds UI."""


def cell(kind, source):
    value={'cell_type':kind,'metadata':{},'source':source.splitlines(True)}
    if kind=='code':
        value.update(execution_count=None,outputs=[])
    return value


def build_notebook():
    return {'nbformat':4,'nbformat_minor':4,'metadata':{
        'kernelspec':{'display_name':'Python 3 (ipykernel)','language':'python','name':'python3'},
        'language_info':{'name':'python'}},'cells':[
        cell('markdown', '''# Video HDMI → FPGA → Web

1. Nối **HDMI OUT của laptop → HDMI IN của PYNQ-Z2**, xuất hình **1280 × 720, 60 Hz**. Giữ kết nối Ethernet.
2. Chọn **Cell → Run All**. Lần đầu có thể cần khoảng một phút để khởi tạo; video sẽ hiện bên dưới khi nhận được frame.
3. Chọn ảnh gốc, xám, nhị phân hoặc Sobel trên trang video. Muốn mở riêng, bấm liên kết phía trên khung hình.

Chỉ chạy một ứng dụng dùng FPGA tại một thời điểm. Chạy lại notebook này khi stream còn hoạt động sẽ dùng lại tiến trình hiện tại.
'''),
        cell('code', '''from pathlib import Path
import importlib
import sys
from IPython.display import HTML, display

ROOT = Path.cwd()
if not (ROOT / 'web_stream_notebook.py').is_file():
    ROOT = Path('/home/xilinx/jupyter_notebooks/realtime_image_processing/video_demo')
if not (ROOT / 'web_stream_notebook.py').is_file():
    raise FileNotFoundError('Mở notebook trong thư mục video_demo đã cập nhật đủ gói phần mềm.')
sys.path.insert(0, str(ROOT))
import web_stream_notebook
web_stream_notebook = importlib.reload(web_stream_notebook)
stats = web_stream_notebook.start_stream(ROOT)
display(HTML('<p><a href="http://192.168.2.99:8080/" target="_blank" rel="noopener">Mở trang video riêng</a></p>'
             '<iframe src="http://192.168.2.99:8080/" style="width:100%;height:960px;border:0" title="Video FPGA"></iframe>'))
'''),
        cell('markdown', '''**Dừng:** bấm **Dừng stream** trên trang video, chờ vài giây rồi chạy lại cell phía trên để mở lại.

Đóng tab hoặc Interrupt Kernel không tắt tiến trình video. Số frame/FPS trên trang là kết quả capture, xử lý và nén JPEG; không phải phép đo độ trễ HDMI đầu-cuối.
''')]
    }
