"""Generate standalone Vietnamese notebooks without adding notebook dependencies."""
import json
from pathlib import Path


def md(source): return {"cell_type":"markdown","metadata":{},"source":source.splitlines(True)}
def code(source): return {"cell_type":"code","metadata":{},"source":source.splitlines(True),"execution_count":None,"outputs":[]}
def save(name,cells):
    notebook={"nbformat":4,"nbformat_minor":5,"metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},
               "language_info":{"name":"python","version":"3"}},"cells":cells}
    for index,cell in enumerate(cells): cell["id"]="cell-%03d"%index
    destination=Path(__file__).resolve().parents[1]/"notebooks"/name
    destination.write_text(json.dumps(notebook,ensure_ascii=False,indent=1)+"\n",encoding="utf-8")


SETUP='''from pathlib import Path
import sys, json
ROOT = Path.cwd()
if not (ROOT / 'video_driver.py').is_file():
    raise RuntimeError('Mở notebook trong thư mục video_demo đã giải nén trên board, có video_driver.py cạnh notebook.')
sys.path.insert(0, str(ROOT))
RESULTS = ROOT / 'results'
RESULTS.mkdir(exist_ok=True)
print('Thư mục demo:', ROOT)
'''


save('01_dma_verify.ipynb',[
md('''# 01 — Kiểm chứng bộ lọc trên FPGA PYNQ-Z2

Notebook này tự đọc cấu hình board, nạp overlay và so **mọi pixel** với kết quả chuẩn. Không cần chạy notebook inventory riêng trước đó, không cần màn hình HDMI ngoài.

1. Upload **`START_HERE.ipynb`** và **`video_pynq_z2_deploy.zip`** vào cùng thư mục Jupyter **của board**. Mở `START_HERE.ipynb`, chọn **Run All** để kiểm tra SHA256 và giải nén thành thư mục `video_demo`; không cần Terminal.
2. Dừng chương trình MNIST hoặc overlay khác. Mở notebook này trong `video_demo`, chọn **Kernel → Restart & Run All**.
3. Chờ báo **PHYSICAL FPGA PASS**. Báo cáo thật được lưu `results/board_dma_results.json`.

Nếu có lỗi, notebook dừng và lưu FAIL, không thay kết quả FPGA bằng phép tính Python. Bitstream đã build/mô phỏng trên PC chỉ được xác nhận chạy board sau khi notebook này PASS.
'''),
code(SETUP),
md('''## Đọc cấu hình tự động
Cell này không hỏi thêm thông tin và không nạp FPGA. Nó ghi cấu hình trong báo cáo để hỗ trợ khi image PYNQ khác phiên bản.
'''),
code('''from board_inventory import collect_inventory
inventory = collect_inventory()
(RESULTS / 'board_inventory.json').write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding='utf-8')
print('Board:', inventory['board_model'] or inventory['board_env'])
print('Python:', inventory['python'], '| PYNQ:', inventory['packages']['pynq'])
print('PYNQ package version có thể trống nếu image cũ không có distribution metadata.')
'''),
md('''## Nạp overlay và kiểm thử đầy đủ

- Kiểm tra SHA256 `.bit/.hwh`, ID/version và FCLK0 100 MHz.
- 23 ảnh × 4 mode = **92 phép đối chiếu toàn bộ pixel**, gồm VGA 640×480.
- **100 frame liên tiếp**, thay mode/kích thước và reset ba lần.
- Mỗi mode benchmark riêng **100 frame VGA**, sau 5 frame warm-up. Không hiển thị ảnh trong khoảng đo.

Chu kỳ core gồm stall/drain; thời gian cả driver gồm đóng gói, cache, DMA, MMIO và đọc lại. FPS trình duyệt và HDMI được đo riêng ở demo tương ứng.
'''),
code('''from board_workflows import run_dma_verification
report = run_dma_verification(ROOT, timeout=5.0)
print('Kết quả:', report['status'])
print('Đối chiếu pixel:', len(report['pixel_tests']), '| Frame liên tiếp:', report['sequential_frames'])
'''),
md('''## Xem số đo trên board
Số dưới đây là số đo của lần chạy hiện tại trên ARM/FPGA. Không dùng số dự đoán từ simulation làm tốc độ board.
'''),
code('''from IPython.display import display, HTML
rows=[]
for b in report['benchmarks']:
    rows.append('<tr><td>%d</td><td>%.3f</td><td>%.3f</td><td>%.3f</td><td>%.1f</td><td>%.3f</td></tr>' % (
        b['mode'],b['core_active']['mean_ms'],b['dma_and_control']['mean_ms'],
        b['whole_driver']['mean_ms'],b['whole_driver']['frames_per_second_from_mean'],
        b['cpu_numpy_reference']['mean_ms']))
display(HTML('<table><tr><th>Mode</th><th>Core ms</th><th>DMA+control ms</th><th>Driver ms</th><th>Driver frame/s</th><th>ARM NumPy ms</th></tr>'+''.join(rows)+'</table>'))
print('Snapshot FPGA: results/fpga_mode0.png ... fpga_mode3.png')
print('Tải results/board_dma_results.json về laptop để lưu bằng chứng.')
'''),
md('''## Tiếp theo
Mở `02_dma_preview.ipynb` để xem ảnh và điều khiển bốn bộ lọc. Bộ đệm DMA của notebook kiểm thử đã được giải phóng. Bản A chạy trên FPGA qua DMA; đây chưa phải demo HDMI vào/ra liên tục.
''')])


save('02_dma_preview.ipynb',[
md('''# 02 — Xem ảnh từ FPGA và đổi bộ lọc

Chạy `01_dma_verify.ipynb` PASS trước. Mở notebook này trên board và chọn **Kernel → Restart & Run All**. Notebook nạp overlay video; dừng ứng dụng MNIST trước khi chạy.

Mỗi ảnh output bên dưới được gửi qua **AXI DMA → FPGA → AXI DMA**. Ảnh “CPU tham chiếu” chỉ để đối chiếu. Preview Jupyter có truyền/mã hóa PNG, không đại diện cho tốc độ HDMI hay tốc độ core.
'''),
code(SETUP),
code('''from video_driver import VideoAccelerator
from board_workflows import assert_pixels
verification_path = RESULTS / 'board_dma_results.json'
if not verification_path.is_file():
    raise RuntimeError('Chạy 01_dma_verify.ipynb trước.')
verification = json.loads(verification_path.read_text(encoding='utf-8'))
if verification.get('status') != 'PASS' or not verification.get('physical_board_verified'):
    raise RuntimeError('Kiểm chứng board chưa PASS; xem results/board_dma_results.json.')
accelerator = VideoAccelerator.load(ROOT / 'overlays')
if verification['overlay_manifest']['bit_sha256'] != accelerator.fingerprint['bit_sha256']:
    accelerator.close()
    raise RuntimeError('Bitstream đã đổi: chạy lại notebook 01.')
print('Backend: FPGA AXI DMA | FCLK0:', accelerator.clock_mhz, 'MHz')
'''),
code('''import base64, io, time
import numpy as np
from PIL import Image, ImageDraw
from IPython.display import display, HTML, clear_output
from reference_filters import apply_filter, rgb_for_display, MODE_NAMES

def make_frame(index=0, width=640, height=480):
    x=np.arange(width)[None,:]
    y=np.arange(height)[:,None]
    rgb=np.empty((height,width,3),np.uint8)
    rgb[:,:,0]=(x+index*5)%256
    rgb[:,:,1]=(y+index*3)%256
    rgb[:,:,2]=((x//40+y//40)%2)*255
    image=Image.fromarray(rgb)
    draw=ImageDraw.Draw(image)
    bx=(index*13)%max(1,width-100)
    draw.rectangle((bx,80,bx+90,170),fill=(255,255,255))
    draw.ellipse((width//2-60,height//2-60,width//2+60,height//2+60),fill=(0,0,0))
    draw.line((0,height-40,width-1,30),fill=(255,255,255),width=4)
    return np.asarray(image)[:,:,::-1].copy()

def image_data(image):
    stream=io.BytesIO()
    Image.fromarray(rgb_for_display(image)).save(stream,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode('ascii')

def show_frame(mode=3, threshold=128, index=0, image=None):
    source=make_frame(index) if image is None else image
    actual=accelerator.process(source,int(mode),int(threshold))
    golden=apply_filter(source,int(mode),int(threshold))
    assert_pixels(actual,golden,'preview')
    figures=[]
    for label,array in [('Ảnh đầu vào',source),('Output FPGA — '+MODE_NAMES[int(mode)],actual),('CPU tham chiếu — đối chiếu',golden)]:
        figures.append('<figure style="margin:8px;flex:1"><figcaption>'+label+'</figcaption><img style="max-width:100%" src="'+image_data(array)+'"></figure>')
    display(HTML('<div style="display:flex;flex-wrap:wrap">'+''.join(figures)+'</div>'))
    metrics=accelerator.last_metrics.copy()
    print('Pixel: PASS | core %.3f ms | DMA+control %.3f ms | cả driver %.3f ms' % (
        metrics['core_active_ms'],metrics['dma_and_control_ms'],metrics['total_driver_ms']))
    return actual

_ = show_frame(3)
'''),
md('''## Điều khiển bộ lọc
Nếu image có `ipywidgets`, menu dưới đây cập nhật output FPGA. Nếu menu không hiện, chạy cell thủ công `show_frame(1)` hoặc `show_frame(2, threshold=128)`; không cần cài thêm thư viện để dùng cách thủ công.
'''),
code('''try:
    import ipywidgets as widgets
except ImportError:
    print('Không có ipywidgets. Dùng show_frame(mode, threshold, index) trực tiếp.')
else:
    mode_control=widgets.Dropdown(options=[(MODE_NAMES[m],m) for m in range(4)],value=3,description='Bộ lọc')
    threshold_control=widgets.IntSlider(value=128,min=0,max=255,step=1,description='Ngưỡng',continuous_update=False)
    frame_control=widgets.IntSlider(value=0,min=0,max=100,step=1,description='Frame',continuous_update=False)
    widget_output=widgets.Output()
    def update_widget(change=None):
        with widget_output:
            clear_output(wait=True)
            show_frame(mode_control.value,threshold_control.value,frame_control.value)
    for control in (mode_control,threshold_control,frame_control): control.observe(update_widget,names='value')
    display(widgets.VBox([mode_control,threshold_control,frame_control,widget_output]))
    update_widget()
'''),
md('''## Thử ảnh của em
Upload PNG/JPEG vào thư mục `video_demo`, rồi sửa tên trong cell dưới. Ảnh được resize về 640×480 trên ARM trước khi gửi FPGA; resize không phải tác vụ của IP bộ lọc.
'''),
code('''IMAGE_PATH = None  # Ví dụ: 'anh_cua_em.jpg'
if IMAGE_PATH:
    input_rgb=Image.open(ROOT / IMAGE_PATH).convert('RGB').resize((640,480),Image.Resampling.BILINEAR if hasattr(Image,'Resampling') else Image.BILINEAR)
    input_bgr=np.asarray(input_rgb)[:,:,::-1].copy()
    _=show_frame(3,image=input_bgr)
else:
    print('Ảnh cá nhân là tùy chọn; các ảnh kiểm thử và demo có sẵn trong gói.')
'''),
md('''## Preview chuyển động tùy chọn
Cell dưới mặc định không chạy vòng lặp. Đổi `RUN_ANIMATION=True` để xem 50 frame tổng hợp, mỗi frame thực sự qua FPGA. Đo nhịp toàn vòng preview riêng; nó gồm tạo ảnh, đối chiếu, PNG và truyền notebook. Đây không phải nguồn camera hoặc HDMI trực tiếp.
'''),
code('''RUN_ANIMATION = False
if RUN_ANIMATION:
    started=time.perf_counter()
    for frame_index in range(50):
        clear_output(wait=True)
        _=show_frame(3,index=frame_index)
        time.sleep(0.1)
    print('Nhịp vòng preview %.2f frame/s; gồm hiển thị, khác tốc độ FPGA.' % (50/(time.perf_counter()-started)))
'''),
md('''## Khi kết thúc
Chạy `accelerator.close()` trong một cell để dừng DMA và giải phóng bộ đệm. Trước khi chuyển về MNIST hoặc nạp overlay khác, dừng preview và đóng accelerator; chỉ một overlay chạy trên cùng board tại một thời điểm.
''')])


save('03_hdmi_demo.ipynb',[
md('''# 03 — HDMI trực tiếp → FPGA → Jupyter / màn hình tùy chọn

Notebook chạy trên **PYNQ-Z2**, dùng overlay `video_hdmi`. Không cần làm inventory riêng. Nên chạy notebook 01 PASS trước để xác nhận bộ lọc/DMA với ảnh kiểm thử.

**Chưa có màn hình ngoài vẫn chạy được:** nối một cáp từ **HDMI OUT laptop → HDMI IN của board**. Jupyter vẫn mở trên laptop qua Ethernet. Trên Windows chọn **Win+P → Duplicate**, đặt tín hiệu HDMI **1280×720, 60 Hz** trong Settings → Display → Advanced display. Đây là chế độ EDID mặc định, phù hợp clock RX của thiết kế. Không chọn đầu vào HDMI VGA; pipeline RX hiện tại không bảo đảm bắt được pixel clock VGA. Nếu driver báo 1920×1080, đổi đầu ra Windows về 720p rồi chạy lại cell.

Đường đi: HDMI IN 1280×720 → VDMA/DDR → **ARM resize về 640×480** → AXI DMA → **Verilog bộ lọc** → AXI DMA/DDR. Resize dùng nearest-neighbour NumPy, giữ tỷ lệ 16:9: nội dung 640×360 được thêm 60 hàng đen phía trên/dưới. Resize và preview chạy trên ARM, cả bốn bộ lọc chạy trên FPGA. Thời gian resize được đo riêng. Mặc định **không bật HDMI OUT** vì chưa có màn hình ngoài.

Upload **`START_HERE.ipynb`** và **`video_pynq_z2_deploy.zip`** vào cùng thư mục Jupyter của board. Chạy **Run All** trong `START_HERE.ipynb` để kiểm tra SHA256 và giải nén, không cần Terminal. Mở notebook này trong `video_demo`, dừng notebook/ứng dụng MNIST hoặc DMA khác, chọn **Kernel → Restart & Run All**. Khi đổi overlay, phải dừng DMA của notebook cũ trước.
'''),
code(SETUP),
code('''from board_inventory import collect_inventory
inventory = collect_inventory()
(RESULTS / 'board_inventory_hdmi.json').write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding='utf-8')
print('PYNQ:', inventory['packages']['pynq'], '| Board:', inventory['board_model'] or inventory['board_env'])
'''),
md('''## Chọn bộ lọc và giới hạn lần chạy

Mặc định xử lý tối đa 120 frame rồi tự dừng, giải phóng buffer. Chạy lại cell thực thi để xem tiếp. Đổi bộ lọc trong menu khi vòng lặp đang chạy; thay đổi chỉ áp dụng từ frame tiếp theo. Nếu image không có widget, sửa `MODE`/`THRESHOLD` rồi chạy lại.

`PROCESSING_SIZE=(640,480)` là mặc định xử lý VGA từ nguồn HDMI 720p. Đổi thành `None` để xử lý đầy đủ 1280×720; độ trễ và tải DDR tăng, Sobel không được cam kết 30 FPS. Báo cáo lưu riêng kích thước nguồn và kích thước xử lý.

Chỉ đặt `ENABLE_HDMI_OUTPUT=True` sau khi có cáp từ **HDMI OUT board → HDMI IN màn hình/TV**. Cổng HDMI laptop không dùng để nhận đầu ra board. Khi có màn hình, nội dung mới được cập nhật theo tốc độ xử lý thực tế; màn hình có thể lặp frame để duy trì refresh 60 Hz.
'''),
code('''ENABLE_HDMI_OUTPUT = False
PROCESSING_SIZE = (640, 480)  # ARM resize + black padding; None: FPGA xử lý đủ 720p
FRAMES = 120
MODE = 3                    # 0: gốc, 1: xám, 2: ngưỡng, 3: Sobel
THRESHOLD = 128
PREVIEW_EVERY = 10           # Tăng để giảm tải Jupyter
VERIFY_EVERY = 30            # CPU chỉ kiểm chứng vài frame, không thay output FPGA
FRAME_TIMEOUT = 3.0
DETECT_TIMEOUT = 15.0        # Thiếu tín hiệu HDMI sẽ báo lỗi sau khoảng này

from reference_filters import MODE_NAMES
mode_control = threshold_control = None
try:
    import ipywidgets as widgets
except ImportError:
    print('Không có widget: dùng MODE/THRESHOLD ở đầu cell.')
else:
    from IPython.display import display
    mode_control = widgets.Dropdown(options=[(MODE_NAMES[m],m) for m in range(4)], value=MODE, description='Bộ lọc')
    threshold_control = widgets.IntSlider(value=THRESHOLD,min=0,max=255,description='Ngưỡng',continuous_update=False)
    display(widgets.HBox([mode_control, threshold_control]))

def current_controls(index):
    return (MODE, THRESHOLD) if mode_control is None else (mode_control.value, threshold_control.value)
'''),
md('''## Chạy demo

Cell dùng `await` của Jupyter để đọc/ghi frame có timeout, cho phép menu cập nhật. Không dùng `asyncio.run()` trong notebook. Có một frame đầu được bỏ để tránh buffer cũ; mỗi frame sau được đưa qua bộ lọc FPGA.

Preview được thu nhỏ và mã hóa JPEG trên ARM, nên nhịp hình trong trình duyệt chậm hơn đường HDMI. Báo cáo không giả định số frame nguồn bị bỏ bằng 0: VDMA không cung cấp sequence ID của nguồn cho luồng này.
'''),
code('''import base64, io
import numpy as np
from PIL import Image
from IPython.display import display, HTML
from reference_filters import rgb_for_display
from hdmi_workflows import run_hdmi_demo

preview_handle = display(HTML('Đang chờ tín hiệu HDMI...'), display_id=True)

def jpeg_data(bgr):
    image = Image.fromarray(rgb_for_display(bgr))
    image.thumbnail((480, 270))
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=80)
    return 'data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode('ascii')

def show_hdmi_preview(source, actual, index, metrics):
    mode = int(metrics['mode'])
    pictures = []
    input_label = 'Đầu vào bộ lọc sau resize ARM (%dx%d)' % (source.shape[1], source.shape[0])
    for label, image in [(input_label, source), ('Output FPGA — ' + MODE_NAMES[mode], actual)]:
        pictures.append('<figure style="margin:8px"><figcaption>' + label + '</figcaption><img style="max-width:100%" src="' + jpeg_data(image) + '"></figure>')
    text = 'Frame %d | resize ARM %.3f ms | core %.3f ms | driver %.3f ms | toàn frame chưa preview %.3f ms' % (
        index, metrics['arm_resize_ms'], metrics['core_active_ms'], metrics['total_driver_ms'], metrics['whole_frame_without_preview_ms'])
    preview_handle.update(HTML('<div style="display:flex;flex-wrap:wrap">' + ''.join(pictures) + '</div><p>' + text + '</p>'))

hdmi_report = await run_hdmi_demo(ROOT,
    enable_output=ENABLE_HDMI_OUTPUT, detect_timeout=DETECT_TIMEOUT, processing_size=PROCESSING_SIZE,
    frames=FRAMES, mode=MODE, threshold=THRESHOLD,
    frame_timeout=FRAME_TIMEOUT, controls=current_controls,
    preview=show_hdmi_preview, preview_every=PREVIEW_EVERY, verify_every=VERIFY_EVERY)
print('HDMI FPGA:', hdmi_report['status'], '| Frame:', hdmi_report['frames_processed'])
print('Nguồn HDMI: %dx%d | FPGA xử lý: %dx%d' % (hdmi_report['source_width'],hdmi_report['source_height'],hdmi_report['processing_width'],hdmi_report['processing_height']))
print('Nhịp xử lý quan sát gồm preview: %.2f frame/s' % hdmi_report['observed_processed_frames_per_second_including_preview'])
print('Đối chiếu pixel:', len(hdmi_report['pixel_checks']), '| Driver trung bình: %.3f ms' % hdmi_report['driver_time']['mean_ms'])
print('Resize ARM trung bình %.3f ms | Cả frame không gồm preview %.3f ms' % (hdmi_report['arm_resize_time']['mean_ms'],hdmi_report['whole_frame_without_preview']['mean_ms']))
print('Nguồn bị bỏ bao nhiêu frame: chưa đo; tốc độ render thật của trình duyệt: chưa đo.')
print('Đã dừng VDMA/AXI DMA. Báo cáo:', RESULTS / 'hdmi_results.json')
'''),
md('''## Đọc kết quả và xử lý lỗi

- `results/hdmi_results.json`: kích thước nguồn/thực sự xử lý, mode từng frame, thời gian resize ARM, chu kỳ core, thời gian driver/cả frame, nhịp vòng lặp gồm preview và kết quả so pixel mẫu.
- **PASS** xác nhận các frame đã đọc được qua FPGA và các frame kiểm chứng khớp; không chứng minh mọi frame HDMI của nguồn đều được xử lý, không tự xác nhận hình trên màn hình ngoài đẹp/ổn định.
- Timeout đầu vào: kiểm tra đúng **HDMI IN**, dây cáp và Windows đang xuất **1280×720, 60 Hz**. Không chọn VGA cho đầu vào HDMI với cấu hình RX hiện tại. Cắm lại nguồn video rồi chạy lại cell thực thi.
- Lỗi binding hierarchy: gửi `results/board_inventory_hdmi.json` và traceback; image PYNQ khác phiên bản có thể cần điều chỉnh driver.
- Lỗi timeout khi dừng VDMA hoặc reset DMA: **khởi động lại board trước khi nạp overlay khác**. Không giải phóng vùng nhớ mà DMA có thể còn sử dụng.

Notebook tự đóng DMA khi hoàn tất, lỗi hoặc ngắt cell. Khi thử đầu ra với màn hình sau này, em cần tự xác nhận màu, hướng ảnh, biên, chuyển mode và độ ổn định; lưu thêm video demo làm bằng chứng vật lý.
''')])


if __name__=='__main__': print('Created 01_dma_verify.ipynb, 02_dma_preview.ipynb and 03_hdmi_demo.ipynb')
