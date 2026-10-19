# Kiểm chứng / Validation

Các kết quả board được ghi ngày **18/10/2026** theo máy kiểm thử. Đồng hồ board từng ở năm 2022; không dùng ngày sai trên board để gán lại thời điểm thực hiện.

| Hạng mục | Kết quả và phạm vi | Dữ liệu |
|---|---|---|
| RTL simulation | 101 đối chiếu + 21 test protocol/error/reset; lịch sử mô phỏng, không phải board | `evidence/rtl_simulation.json` |
| Implementation | Vivado 2022.2, timing/DRC và bit/hwh fingerprint; không rebuild lại trong lúc chuẩn bị GitHub | `evidence/implementation_summary.json` |
| Pixel board | 40 frame, luân phiên bốn mode, 0 byte lệch so tham chiếu | `evidence/pixel_checks_40frames.json` |
| Notebook 04 | Start, reuse, stop và restart qua REST/WebSocket và GUI; kernel idle sau launcher | `evidence/board_validation.json` |
| Stream đầu | Quan sát đến 2.143 frame, sau đó dừng có chủ đích | `evidence/board_validation.json` |
| Dừng | Tiến trình thoát, VDMA halted; CMA free sau dừng 49.836 kB | `evidence/board_validation.json` |
| Theo dõi sau restart | 25 mẫu video healthy, frame tăng, Jupyter HTTP 200; 128,45 giây, khoảng 2,22 FPS | `evidence/stream_observation_128s.json` |
| Theo dõi trước mở lại log | Có hai Jupyter timeout; video healthy cả 25 mẫu; vẫn ghi FAIL | `evidence/earlier_observation_summary.json` |
| Host tests của repo công khai | Chạy bằng script, ghi số test thực tế và hash nguồn; không cần FPGA | `evidence/publication_host_tests.json` sau bước kiểm tra bản phát hành |

## Tái kiểm tra trên PC

```bash
python -m pip install -r requirements-dev.txt
python scripts/check_repository.py
```

Script chạy unit tests số học/driver/workflow/frame ownership/server và compile cell Python của notebook, không thực thi notebook hay truy cập board. Test có phần cứng giả lập không được gắn nhãn physical PASS. CI thực hiện cùng phép kiểm tra; không tạo trước badge xanh cho workflow chưa từng chạy trên GitHub.

## Nguồn gốc bằng chứng

- `board_validation.json` là bản công khai rút gọn từ biên bản riêng, giữ hash nguồn và hash biên bản gốc; bỏ đường dẫn Windows, thông tin phiên đăng nhập/kernel và PID.
- `docs/images/notebook04-sobel-running.png` là screenshot thật, giữ nguyên pixel; ảnh cho thấy một thời điểm RUNNING và Sobel. Một ảnh không chứng minh cả phiên chạy dài.
- `docs/images/pynq-z2-setup.png` là ảnh bố trí board từ giai đoạn thao tác trước, không dùng để khẳng định overlay/FPS tại thời điểm chụp.
- Biểu đồ timing được render từ JSON đã ghi, có nhãn phạm vi; không phải ảnh chụp phép đo mới.
- `evidence/source_snapshot.json` ghi bản nguồn được chọn cho repo. Các PowerShell wrapper được đổi cách chọn Python để dùng được ngoài máy phát triển; không sửa RTL để đăng GitHub.

## Chưa nghiệm thu

HDMI OUT trên màn hình thật; 60 FPS xử lý; toàn bộ lượt benchmark notebook 01; độ bền qua nhiều ngày hoặc nhiều image PYNQ; chống mất điện; nguyên nhân và sửa chữa EXT4/thẻ vật lý. Việc tạo gói cài và thử giải nén offline xác nhận tính toàn vẹn gói, không thay thế lần chạy lại bundle trên board sau khi cài mới.

English: reports separate host simulation, routed implementation, physical pixel comparison, bounded stream observation and packaging checks. Earlier failures remain documented. The release bundles the same verified runtime and bitstreams; publication checks do not claim a new clean-board installation or long-term reliability test.
