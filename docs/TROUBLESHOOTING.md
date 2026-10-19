# Lỗi đã gặp và cách xử lý / Troubleshooting history

Tài liệu phân biệt **bằng chứng quan sát được**, **nguyên nhân đã tái hiện**, **biện pháp giảm rủi ro** và **phần chưa giải quyết**. Không coi mọi lỗi Python, mạng hay video đều có chung nguyên nhân. Các thao tác hệ thống dưới đây là lịch sử chẩn đoán của một board, không phải script sửa tự động dành cho mọi máy.

## 1. Notebook trắng, file văn bản vẫn mở được — đã phục hồi

- **Triệu chứng:** README mở được nhưng trang notebook không dựng menu/cell; Console báo `SyntaxError: Invalid or unexpected token`.
- **Bằng chứng:** `main.min.js` có 16.384 byte NUL; MathJax.js có 2.052 byte NUL. Notebook JSON hợp lệ không bảo đảm JavaScript frontend còn nguyên vẹn.
- **Xử lý:** giữ bản hỏng để đối chiếu, khôi phục tài nguyên từ đúng Notebook **6.4.12**, so SHA256 trên đĩa và dữ liệu HTTP; không đổi code bộ lọc để sửa giao diện.
- **Kết quả:** menu/cell hiện lại. Không suy ra nguồn gốc vật lý gây hỏng file chỉ từ bằng chứng này.

## 2. Đã thay JavaScript nhưng còn lỗi MathJax — đã phục hồi

Trình duyệt có thể còn dùng bản cache lỗi. Đã kiểm tra bản server trả lại khớp nguồn, sau đó dùng URL alias MathJax mới trỏ vào tài nguyên sạch và restart Jupyter. Phiên kiểm tra cuối hiện đủ menu/cell, không còn modal MathJax. Không tắt công thức, widget hoặc cơ chế đăng nhập để che lỗi.

## 3. Jupyter `Connection failed` / WebSocket bị từ chối — phiên cuối đã chạy lại

HTTP file tĩnh và WebSocket cùng thất bại cho thấy vấn đề vượt quá một file `.ipynb`. Có phiên Jupyter native crash; chỉ sửa frontend không khôi phục được một server đã chết.

Đã dùng UART **115200, 8N1** để đọc shell/log, khôi phục dịch vụ và reboot khi cần. Phép thử tạo kernel riêng qua REST, thực thi `6 * 7`, nhận `execute_reply: ok`, trạng thái idle và HTTP đã qua. Phiên kiểm chứng video sau đó có 25/25 mẫu Jupyter HTTP 200. Không coi một lần phép tính PASS là chứng minh ổn định dài hạn.

## 4. Python `bad marshal data` / lỗi opcode khi import — đã vượt qua sau phục hồi, gốc chưa rõ

Khởi tạo kernel từng lỗi khi đọc bytecode trong dependency của IPython/debugpy. Đã thử cache/profile/runtime mới trong RAM để chẩn đoán, rồi kiểm tra lại sau reboot. Import và thực thi kernel ở phiên sau thành công. Một phiên phục hồi RAM trước đó vẫn native crash, nên không công bố rằng chỉ xóa cache đã sửa mọi nguyên nhân hay toàn bộ runtime đã nguyên vẹn.

## 5. Video chết sau vài frame — lỗi buffer đã tái hiện và sửa

Sau ba frame đúng pixel, một phép thử native crash ở frame tiếp theo trong đường capture. Kiểm tra `PynqBuffer.freebuffer()` đang cài cho thấy:

1. Frame trả pointer về cache và đặt pointer=0.
2. Cờ freed của đối tượng cũ vẫn chưa được đặt.
3. Destructor có thể gọi freebuffer lần nữa, dẫn tới munmap mapping cache đã được frame mới tái sử dụng.

Helper **`frame_ownership.release_capture_frame`** chỉ đánh dấu đã giải phóng khi **chính pointer đó thực sự được cache giữ lại**. Buffer thường và trường hợp cache đầy giữ quy trình disposal gốc. Đã thêm test destructor, trả buffer hai lần, cache đầy và buffer thường; không monkeypatch toàn cục PYNQ.

Sau sửa: 40 frame/bốn mode khớp từng byte, stream hơn 2.000 frame, dừng và mở lại qua notebook thành công. Đây là kết quả của runtime đã thử; không áp dụng mù helper cho mọi phiên bản PYNQ.

## 6. VDMA timeout khi khởi tạo HDMI — đã qua ở cấu hình thử

Khởi tạo cache/kênh VDMA có thể lâu hơn một giao dịch frame bình thường. Đã tách ngân sách khởi tạo **30 giây** khỏi giới hạn chuyển frame/dừng **5 giây**, giữ guard timeout và kiểm tra trạng thái. Không tăng vô hạn hoặc bỏ qua lỗi DMA cleanup. Nguồn thử là **1280×720, 60 Hz**, không phải VGA input.

## 7. VDMA báo IntErr cùng SOF/EOL mismatch lúc có tín hiệu — đã xử lý frame lỗi

Theo PG020, một số lỗi kích thước dòng/frame có thể đặt IntErr mà kênh chưa halted. Reader cũ coi tất cả IntErr là lỗi chết.

Reader mới bỏ completion lỗi, xóa sticky sync/interrupt flags, rồi đợi completion sạch trong deadline. Halted, slave/decode và internal error độc lập vẫn làm dừng. Không trả frame lỗi như frame hợp lệ. Xem [AMD PG020 S2MM status](https://docs.amd.com/r/en-US/pg020_axi_vdma/S2MM_VDMASR-S2MM-VDMA-Status-Register-Offset-34h).

## 8. Chạy trùng notebook/server hoặc cleanup khi còn dùng DMA — đã tổ chức lại vòng đời

Thiết kế cũ có rủi ro worker còn truy cập frame khi workflow đã đóng. Bản mới có **một main thread sở hữu hardware**; HTTP threads chỉ đọc JPEG/trạng thái RAM. Launcher xác minh manifest, lock, PID/cmdline và phiên server trước khi reuse. Nút dừng yêu cầu main thread cleanup.

Notebook chỉ mở tiến trình phần cứng độc lập. Tách tiến trình bảo vệ vòng đời kernel; bản sửa buffer mới là phần giải quyết native crash đã tái hiện. Chạy thêm tiến trình không làm phần cứng tự an toàn.

## 9. Dịch vụ video cũ tự chạy lúc boot — đã tắt autostart

Đã phát hiện dịch vụ cũ sử dụng FPGA khi người dùng đang phục hồi Jupyter/thử notebook. Dịch vụ đó được dừng và **disable autostart**, giữ bản unit cũ để đối chiếu. Repo không tự cài/bật service. Chỉ một ứng dụng nạp overlay tại một thời điểm.

## 10. Ảnh đứng nhưng UI còn giống đang LIVE — đã sửa biểu diễn trạng thái

Bản cũ có thể lặp JPEG cuối, giữ số FPS cũ và nuốt exception. Bản mới cung cấp số frame, tuổi frame, trạng thái healthy/waiting/stale/error, metrics từng công đoạn và log lỗi. Web không dùng ảnh cuối làm bằng chứng rằng có frame mới. `/health` phản ánh dữ liệu mới thay vì chỉ kiểm tra server đang nghe cổng.

## 11. Jupyter phản hồi chậm trong khi video còn khỏe — đã giảm lỗi ghi log

Một file `kern.log` trên filesystem lỗi không ghi được, gây chuỗi thông báo lỗi của daemon log. Đã **giữ file cũ bằng đổi tên**, tạo file log mới cùng owner/mode và SIGHUP rsyslog để mở lại; không tắt daemon, không dùng fsck trong thao tác này.

Phép theo dõi trước đó có hai Jupyter timeout nhưng video khỏe cả 25 mẫu; phần thất bại vẫn được lưu ở `evidence/earlier_observation_summary.json`. Phép theo dõi sau sửa có 25/25 HTTP 200. Không biến kết quả thất bại trước đó thành PASS hay kết luận filesystem đã được sửa.

## 12. EXT4 checksum / directory / block errors — **chưa sửa**

Đã có lỗi filesystem thực tế. Người dùng yêu cầu tạm hoãn kiểm tra/sửa SD trong lần sửa notebook 04. Không chạy fsck, format hoặc ghi lại image trong đợt sửa này. Chương trình chạy được không chứng minh mọi vùng lưu trữ an toàn. Chưa quy được nguyên nhân gốc cho thẻ vật lý, nguồn, thao tác tắt máy hay yếu tố khác.

Trước mọi sửa chữa lưu trữ về sau cần giữ dữ liệu cần thiết và xác định đúng thiết bị/phân vùng. Không chạy công cụ sửa filesystem lên phân vùng root đang được mount đọc/ghi như một thao tác thử tùy tiện.

## 13. Các vấn đề từ giai đoạn thao tác ban đầu

| Hiện tượng | Điều có thể kết luận / cách đã hướng dẫn |
|---|---|
| Không mở được IP Jupyter | Cần tách boot/nguồn, mạng/IP và dịch vụ; chưa có một nguyên nhân duy nhất được chứng minh cho mọi lần timeout |
| Terminal mới ở `/` | Dùng `cd` tới thư mục dự án trước lệnh tương đối; không cần cài hoặc giải nén lại chỉ vì mở Terminal mới |
| `$'\E[200~uptime': command not found` | Ký tự điều khiển bracketed-paste đi vào lệnh; gõ lại dòng lệnh sạch, không phải lỗi FPGA |
| User LEDs tắt sau nạp overlay | LED người dùng phụ thuộc cấu hình PL; LED sáng/tắt không đủ để kết luận AI/video chạy đúng hoặc board hỏng |
| Đã bấm Dừng stream, ảnh đen/STOPPING | Dừng có chủ đích; chạy lại cell notebook 04 hoặc Cell → Run All để mở lại |
| Shutdown rồi đèn vẫn sáng | Linux halt và ngắt nguồn vật lý là hai bước; không dùng LED làm bằng chứng duy nhất của shutdown sạch |
| SD backup đọc chậm, ETA tăng | Tốc độ đầu đọc/USB/thẻ và retry có thể ảnh hưởng; không chẩn đoán thẻ hỏng chỉ bằng ETA |
| Win32 Disk Imager báo verify thất bại tại sector 0 | Sự kiện đã gặp trong giai đoạn SD trước đó; chưa có nguyên nhân gốc được kiểm chứng, không được công bố là đã sửa bởi patch video |

Đọc image SD để backup khác với ghi image hệ điều hành, và cả hai khác với nạp `.bit/.hwh` bằng PYNQ Overlay. Ghi image lên thẻ có thể thay nội dung thẻ; nạp overlay không phải thao tác backup hệ thống.

## 14. Những giả thuyết không được ghi thành kết luận

- CMA cạn kiệt: driver đã tái sử dụng buffer; mẫu kiểm tra không đủ xác nhận đây là nguyên nhân mọi crash.
- Thiếu TLAST: có quy ước frame/packet và line khác nhau; chưa có bằng chứng thiếu TLAST là nguyên nhân crash đã sửa.
- UIO/asyncio trong thread: có rủi ro API/vòng đời; chưa tái hiện để quy mọi native crash cho giả thuyết này.
- Cắm HDMI làm hỏng board: trùng thời điểm mất kết nối chưa chứng minh quan hệ nhân quả.
- 60 Hz output nghĩa là 60 kết quả Sobel mới/giây: không đúng cách đọc FPS.

## English summary

Recovered corrupted Notebook/MathJax assets and stale browser cache; diagnosed backend/kernel failures separately; corrected VDMA startup/sync-error handling; reproduced and fixed cached-frame double disposal; established a single hardware owner and ordered stop/restart; improved health/FPS reporting; disabled the old autostart service; and reopened a failing log file while preserving the original. **EXT4 errors and the earlier SD sector-0 verification issue remain unresolved.** The public reports distinguish successful bounded observations from earlier failures and unproven hypotheses.
