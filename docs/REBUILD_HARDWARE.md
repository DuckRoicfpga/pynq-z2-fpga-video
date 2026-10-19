# Tái dựng phần cứng / Hardware rebuild

Chỉ cần bước này nếu muốn sửa RTL hoặc tái dựng overlay. Người chạy demo có thể dùng bộ `.bit/.hwh` đã ghép đúng cặp trong Release. Công cụ Vivado/Vitis không được phân phối kèm repo.

## Toolchain và upstream

- Board/part: PYNQ-Z2, `xc7z020clg400-1`; cài board definition TUL PYNQ-Z2 trong Vivado.
- Bản build lịch sử: **Vivado / Vitis HLS 2022.2**. Template PYNQ gốc từ Vivado 2022.1.
- PYNQ source: tag **v3.0.1**, commit **`93ddd21ab623883590a8c8b07f0b157b2855da4b`**.
- Custom filter là Verilog. HLS chỉ build color conversion/pixel pack/unpack của subsystem PYNQ.

Repo không vendor toàn bộ PYNQ hoặc output Vivado. Nguồn đã trích/constraint có trong `hardware/hdmi/generated`; license giữ trong `licenses/`. Build HDMI vẫn cần checkout upstream đúng commit để có các IP video.

## 1. Chuẩn bị Python và vector

Từ root repo, dùng Python 3.10+ có NumPy/Pillow:

```powershell
python -m pip install -r requirements-dev.txt
python scripts/check_repository.py
python software/generate_vectors.py
```

## 2. Chạy mô phỏng trước khi build

```powershell
.\sim\run_sim.ps1 -VivadoBin 'C:\Xilinx\Vivado\2022.2\bin' -Python 'python'
```

Script tạo `output/rtl_validation/rtl_pass.json` với hash RTL. Build DMA wrapper kiểm tra nguồn chưa đổi từ lúc mô phỏng. Bản ghi lịch sử trong `evidence/rtl_simulation.json` chỉ là bằng chứng công bố, không được chép sang `output/` để giả một lần chạy mô phỏng mới.

## 3. Overlay DMA độc lập

```powershell
.\hardware\scripts\build_dma.ps1 -VivadoBin 'C:\Xilinx\Vivado\2022.2\bin' -Python 'python'
```

Tcl đóng gói custom IP từ `rtl/`, dựng Zynq/AXI DMA và xuất vào `output/dma_overlay`. Giữ manifest/hash đi cùng `.bit/.hwh`.

## 4. Overlay HDMI

```powershell
git clone --branch v3.0.1 --depth 1 https://github.com/Xilinx/PYNQ.git hardware/hdmi/vendor/PYNQ
git -C hardware/hdmi/vendor/PYNQ rev-parse HEAD
python hardware/hdmi/prepare_sources.py
.\hardware\hdmi\build_hls.ps1
& 'C:\Xilinx\Vivado\2022.2\bin\vivado.bat' -mode batch -notrace -source hardware/hdmi/build_hdmi.tcl -tclargs bd-only
& 'C:\Xilinx\Vivado\2022.2\bin\vivado.bat' -mode batch -notrace -source hardware/hdmi/build_hdmi.tcl
python hardware/hdmi/audit_sources.py --final
```

`prepare_sources.py` từ chối commit không đúng pin. HLS 2022.2 có hạn chế đường dẫn có dấu cách; wrapper dùng tạm ổ alias `R:` và tháo alias khi xong. Nếu R: đang dùng, truyền `-AliasDrive` là một chữ ổ còn trống. Alias chỉ đổi cách truy cập đường dẫn, không di chuyển dữ liệu.

Nếu tái đóng gói IP thủ công sau khi đổi RTL, dùng `hardware/scripts/package_ip.tcl` với Vivado trước build HDMI. Bản `hardware/ip_repo` trong repo là nguồn custom IP đã đóng gói nhỏ, không phải toàn bộ cache/project Vivado.

### Các quyết định kỹ thuật cần giữ

- HDMI RX: nguồn 720p 74,25 MHz, `kClkRange=2`, constraint phù hợp; không áp VGA input 25 MHz vào cấu hình RX này.
- VDMA/video hierarchy và interrupt controller giữ tên để driver PYNQ bind đúng.
- Custom filter/DMA 100 MHz, BGR0 32 bit và TLAST cuối packet/frame; phân biệt với video TLAST cuối dòng.
- `fix_tx_clocks.tcl` loại clock TX khai báo dư và kiểm chứng serial/pixel 5:1, giữ các constraint CDC cần thiết; không dùng false path/multicycle để che lỗi datapath.
- Xác nhận setup/hold slack, DRC, clock và hash nguồn trước khi coi một build mới đạt.

Chuẩn bị GitHub không chạy lại toàn bộ Vivado/HLS. Thay đổi toolchain/RTL tạo một build mới cần nghiệm thu lại; không tự gắn kết quả board cũ cho bitstream mới.

## 5. Tạo gói board từ nguồn công khai

Sau khi có các cặp overlay và manifest trong cùng một thư mục, cùng vector đã tạo:

```powershell
python scripts/package_board.py --overlays <FOLDER_WITH_PAIRED_OVERLAYS> --vectors output/test_vectors --output dist
```

Script giữ nguyên bitstream, kiểm tra fingerprint và tạo ZIP, INSTALL notebook, SHA256SUMS. Phiên bản script này chỉ gắn trạng thái runtime đã kiểm chứng khi hash khớp bằng chứng; nếu sửa runtime/bitstream thì cần cập nhật bằng chứng bằng kiểm thử thực tế.

English: this source snapshot provides the original RTL/build scripts plus portable Python selection in two PowerShell wrappers. Rebuilds require the vendor toolchain, board files and the pinned upstream checkout. The publication process validated source/tests/packaging, not a new clean Vivado build.
