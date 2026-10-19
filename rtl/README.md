# DMA frame filter RTL

Top module: `video_filter_axi`, one `s_axi_aclk` (100 MHz in the delivered overlay), active-low synchronous `s_axi_aresetn`.

The input/output streams are **DMA packet streams**, not native AXI video. One 32-bit word is one BGR pixel: `[7:0]=B`, `[15:8]=G`, `[23:16]=R`, `[31:24]=0`. Every beat has `TKEEP=0xf`. `TLAST` is asserted once on the final pixel of a complete frame. There is no `TUSER` or end-of-line `TLAST`. Direct connection to an HDMI AXI-video pipeline would need framing adapters.

`TVALID && TREADY` is the only transfer event. Output data, keep, last, and valid remain stable while stalled. Passthrough accepts one pixel every clock when both streams run continuously. Grayscale/threshold accept one pixel every four clocks. Sobel accepts one pixel every six clocks, plus border output cycles.

## Registers

AXI4-Lite has independent AW/W capture, byte strobes, and stable B/R responses under backpressure. Width, height, mode and threshold are shadow configuration, captured atomically on START. Writes while BUSY prepare the next frame and do not alter the active frame.

| Offset | Register | Value / meaning |
|---|---|---|
| 0x00 | ID | `0x56494431` |
| 0x04 | VERSION | `0x00010000` |
| 0x08 | CONTROL | Write bit0 START; bit1 SOFTRESET has priority; reads zero |
| 0x0c | STATUS | bit0 BUSY, bit1 sticky DONE, bit2 ERROR |
| 0x10 | WIDTH | Default 640; valid 3..1280 |
| 0x14 | HEIGHT | Default 480; valid 3..720 |
| 0x18 | MODE | 0 passthrough, 1 grayscale, 2 threshold, 3 Sobel |
| 0x1c | THRESHOLD | Low eight bits; default 128; upper bits reserved/ignored |
| 0x20 | CYCLES | Active wall-clock cycles, including stalls and border padding |
| 0x24 | PIXELS_IN | Number of accepted input beats |
| 0x28 | PIXELS_OUT | Number of accepted output beats |
| 0x2c | ERROR_FLAGS | See below; reset required to clear |
| 0x30 | CLOCK_HZ | Top parameter, default `100000000` |

DONE clears on START or reset. Counters clear on a valid new START or reset. CYCLES ends when the final output pixel is accepted; it is not a pure arithmetic-only counter. At 100 MHz the 32-bit counter wraps after approximately 42.95 seconds; software uses a bounded timeout well below this.

ERROR_FLAGS: bit0 invalid dimensions, bit1 invalid mode, bit2 early TLAST, bit3 missing TLAST on expected final input, bit4 incorrect TKEEP, bit5 nonzero high byte, bit6 START while already BUSY. WIDTH/HEIGHT/MODE validate their entire 32-bit register value; upper bits cannot alias a valid configuration. Invalid stream beats are counted as accepted input, then abort the frame and latch ERROR. A previously queued output is retained until its handshake to preserve the stall contract. Aborted output does not constitute a complete DMA packet: software must reset both DMA channels and the core before retrying. START is ignored while ERROR remains latched.

## Sobel implementation

Grayscale has a registered arithmetic pipeline: three parallel 16-bit constant channel products, two parallel partial sums (`77R+150G` and `29B+128`), then the final sum's upper byte. Grayscale replication or threshold comparison is committed to the AXIS output in the next cycle. The accepted pixel's EOF flag is retained across these stages. This keeps constant multiplication, summation and threshold comparison out of a single-cycle path. Passthrough bypasses these arithmetic stages.

Two 1280-byte synchronous row memories infer block RAM. During row y, one memory holds y-2 and is overwritten with the incoming row; the other holds y-1. Reads occur on input acceptance. The following stages register the four weighted side sums, then signed Gx/Gy; the final processing cycle evaluates absolute value, sum and saturation into the output register and writes the row memory exactly once. Window pixels, incoming gray pixel and row/column counters stay unchanged across arithmetic stages and output stalls. The first two rows initialize the memories, so clearing the entire RAM on reset is unnecessary.

The arithmetic pipelines remove the single-cycle BRAM-to-gradient-to-saturation and DMA-to-grayscale-to-threshold paths that failed the initial 100 MHz routed timing runs. No multicycle or false-path exception relaxes these datapaths. Reset clears the FSM, arithmetic registers and AXIS valid; the simulation explicitly resets every grayscale and Sobel arithmetic stage. Physical timing success must still be confirmed by the routed timing report for the current RTL hash.

The FSM emits the zero top row, processes all input pixels, emits a zero left/right border for each interior row, and finally drains the zero bottom row. Thus output remains W×H even though the Sobel window needs one row/column of future pixels. All four borders are exactly zero, and the EOF is on the bottom-right output pixel. No full frame buffer is stored in the IP.

Arithmetic: rounded grayscale `(77*R+150*G+29*B+128)>>8`; threshold `gray>=T`; Sobel signed Gx/Gy and `min(255,abs(Gx)+abs(Gy))`.

## Verification

Run `sim/run_sim.ps1`. Xilinx xvlog/xelab/xsim 2022.2 compares 92 Python golden case/mode pairs, plus directed configuration, minimum/maximum dimensions and continuous streaming comparisons. The final suite has 101 comparisons and 21 register/protocol/error/reset checks. It exercises independent AW/W arrival, strobes, held B/R responses, random stalls, reset during output stall, Sobel warm-up, all three grayscale stages and all three Sobel arithmetic stages, frame errors, multi-frame operation, and unchanged configuration during BUSY.

`output/rtl_validation/rtl_pass.json` records PASS, exact RTL SHA256 values and `actual_board_test=false`. The source hash is a build gate.

Observed **simulation only**, continuous input and ready output, 100 MHz:

| Frame / mode | Active cycles | Cycle-equivalent duration |
|---|---:|---:|
| 640×480 mode 0 | 307,205 | 3.07205 ms |
| 640×480 modes 1/2 | 1,228,805 | 12.28805 ms |
| 640×480 Sobel | 1,844,959 | 18.44959 ms |
| 1280×720 Sobel | 5,532,879 | 55.32879 ms |

The modeled counts include five initial command/source cycles. They exclude DDR arbitration, AXI DMA setup, Python overhead and preview encoding; they are not physical board measurements or a promise of end-to-end FPS. In particular, 1280×720 Sobel at 100 MHz has a core-only ceiling below 30 FPS with this timing-oriented sequential pipeline; 640×480 remains the real-time target, subject to board measurement.
