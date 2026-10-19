# Cài đặt và chạy / Getting started

## 1. Chuẩn bị

- PYNQ-Z2, microSD có image PYNQ và **nguồn phù hợp hướng dẫn của board**. Không dùng điện áp bất kỳ cho jack DC.
- Ethernet nối laptop với board. Địa chỉ `192.168.2.99` trong ví dụ là cấu hình mạng của phiên thử; máy của bạn có thể khác.
- Laptop HDMI OUT → **HDMI IN** trên board, xuất **1280×720, 60 Hz**. HDMI OUT trên board không cần dùng cho notebook 04.
- Phiên thử dùng Python của board tại `/usr/local/share/pynq-venv/bin/python3`, PYNQ **3.1.1**, với NumPy, Pillow, OpenCV, Flask và IPython. Không dùng Python trên laptop để điều khiển `/dev/mem` của board.

Nguồn hardware HDMI được lấy từ PYNQ v3.0.1; đó là phiên bản nguồn upstream, không phải tuyên bố rằng runtime được thử cũng là 3.0.1. Chưa xác nhận mọi image/phiên bản PYNQ đều tương thích, đặc biệt cách quản lý frame cache.

## 2. Cài gói board

Khi maintainer đã đăng bản phát hành, tải **hai file cùng một Release**:

- `pynq-z2-video-demo-v1.0.0.zip`
- `INSTALL.ipynb`

Không tải toàn bộ working directory hoặc image SD từ người khác để thay thế bước này. Bản source ZIP do GitHub tự tạo không chứa bitstream.

Trên Jupyter của board, tạo/mở thư mục `realtime_image_processing`, upload hai file vào cùng thư mục rồi chạy **INSTALL.ipynb → Cell → Run All**. Installer kiểm tra SHA256 và từng file, giải nén thành `video_demo/`; nó **không nạp FPGA, cài package hay sửa hệ điều hành**. Nếu `video_demo` đã tồn tại, installer sẽ dừng để tránh ghi đè công việc đang có; dừng ứng dụng và lưu/đổi tên bản cũ trước khi cài vào vị trí mới.

Mở `video_demo/04_web_stream.ipynb`. Các file `.bit` và `.hwh` phải giữ cùng tên gốc và đúng cặp; không đổi tên hoặc trộn hai lần build.

## 3. Chạy video

1. Kết thúc ứng dụng khác đang dùng FPGA; giữ HDMI IN có tín hiệu 720p60.
2. Chọn **Cell → Run All** trong notebook 04.
3. Chờ khởi tạo, có thể khoảng một phút ở lần đầu. Khi notebook báo đã nhận frame liên tục, iframe video sẽ xuất hiện.
4. Chọn 0/1/2/3 để đổi bộ lọc. Có thể mở trang riêng `http://<BOARD_IP>:8080/`.

Chạy lại cell khi video còn hoạt động sẽ dùng lại đúng tiến trình, không nạp thêm một bản server. Notebook đang kết nối qua `192.168.2.99` theo cấu hình lab; nếu thay IP, sửa URL hiển thị trong bản notebook của bạn, không thay hợp đồng hardware.

**Dừng:** bấm **Dừng stream**, chờ cleanup vài giây. **Phát lại:** chạy lại cell hoặc Cell → Run All. Đóng tab hoặc Interrupt Kernel không dừng tiến trình video độc lập.

Log tạm ở `/dev/shm/pynq_z2_video_web/server.log`; PID metadata ở `process.json` cùng thư mục. Chúng mất khi reboot. Nếu gặp traceback, giữ log trước khi khởi động lại; không chạy nhiều overlay để cố vượt lỗi DMA cleanup.

## 4. Notebook khác

| File | Vai trò |
|---|---|
| 00 | Inventory tùy chọn |
| 01 | Kiểm chứng DMA, so pixel và benchmark mở rộng |
| 02 | Preview ảnh kiểm thử qua FPGA |
| 03 | Thử HDMI từng frame, tùy chọn HDMI OUT |
| 04 | Stream web với một tiến trình sở hữu phần cứng |

Nguồn notebook 01–03 được cung cấp để tái lập/thử thêm; bản nghiệm thu công khai hiện chứng minh 40 frame/bốn mode qua HDMI và notebook 04, **không ghi trước PASS cho toàn bộ notebook 01 hoặc HDMI OUT**.

## 5. Tắt board

Dừng stream → lưu notebook bằng Ctrl+S → mở Terminal **trên board** rồi chạy từng dòng:

```bash
sync
sudo shutdown -h now
```

Chờ hệ điều hành hoàn tất shutdown. Console UART có thể xác nhận `System halted` / thông báo power down. Sau đó gạt công tắc **OFF** và tháo nguồn. Đèn còn sáng không tự chứng minh Linux còn chạy, và mất kết nối mạng cũng không chứng minh mọi trường hợp shutdown đã sạch. Nếu có lỗi shutdown/filesystem, xem console/log trước khi ngắt nguồn.

## English summary

Use a PYNQ-Z2 with a suitable vendor image and power supply. Connect a 720p60 HDMI source to HDMI IN and use Ethernet for Jupyter. Download the matching deployment ZIP and INSTALL notebook from a published Release, upload both into the same board folder and run the installer. Then open `video_demo/04_web_stream.ipynb` and choose **Cell → Run All**. Use **Dừng stream** to stop, and rerun the notebook cell to restart. The installer refuses to overwrite an existing deployment. Source-only GitHub archives do not contain bitstreams. Shut down Linux before physically switching off power.
