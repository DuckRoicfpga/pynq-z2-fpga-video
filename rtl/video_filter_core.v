`timescale 1ns / 1ps
module video_filter_core #(
    parameter integer MAX_WIDTH = 1280,
    parameter integer MAX_HEIGHT = 720
)(
    input wire clk,
    input wire resetn,
    input wire start,
    input wire soft_reset,
    input wire [31:0] cfg_width,
    input wire [31:0] cfg_height,
    input wire [31:0] cfg_mode,
    input wire [7:0] cfg_threshold,
    output reg busy,
    output reg done,
    output reg [31:0] error_flags,
    output reg [31:0] cycles,
    output reg [31:0] pixels_in,
    output reg [31:0] pixels_out,
    input wire [31:0] s_axis_tdata,
    input wire [3:0] s_axis_tkeep,
    input wire s_axis_tlast,
    input wire s_axis_tvalid,
    output wire s_axis_tready,
    output reg [31:0] m_axis_tdata,
    output wire [3:0] m_axis_tkeep,
    output reg m_axis_tlast,
    output reg m_axis_tvalid,
    input wire m_axis_tready
);
    localparam IDLE=0, INPUT_PIXEL=1, SOBEL_PIXEL=2, TOP_BORDER=3,
               RIGHT_BORDER=4, BOTTOM_BORDER=5, DRAIN=6,
               SOBEL_SUMS=7, SOBEL_GRADIENTS=8,
               GRAY_PARTIALS=9, GRAY_VALUE=10, GRAY_PIXEL=11;
    reg [3:0] state;
    reg [15:0] width, height, column, row, border_column;
    reg [1:0] mode;
    reg [7:0] threshold;
    reg captured_last;
    reg [31:0] total_pixels;
    reg [7:0] top0, top1, mid0, mid1, bot0, bot1;
    wire [7:0] captured_gray, binary_pixel, top_gray, middle_gray, sobel_pixel;
    wire output_available = !m_axis_tvalid || m_axis_tready;
    wire input_fire = s_axis_tvalid && s_axis_tready;
    wire output_fire = m_axis_tvalid && m_axis_tready;
    wire expected_last = (pixels_in == total_pixels - 1);
    wire [31:0] stream_error =
        ((s_axis_tlast && !expected_last) ? 32'h00000004 : 0) |
        ((!s_axis_tlast && expected_last) ? 32'h00000008 : 0) |
        ((s_axis_tkeep != 4'hf) ? 32'h00000010 : 0) |
        ((s_axis_tdata[31:24] != 0) ? 32'h00000020 : 0);
    wire [31:0] next_output_index = pixels_out + (output_fire ? 32'd1 : 32'd0);
    wire output_is_last = next_output_index == total_pixels - 1;
    wire process_sobel = busy && state == SOBEL_PIXEL && !start && !soft_reset &&
        ((row < 2 || column == 1) || output_available);
    assign s_axis_tready = busy && state == INPUT_PIXEL && output_available && !start && !soft_reset;
    assign m_axis_tkeep = 4'hf;
    gray_threshold gray_unit(
        .clk(clk), .resetn(resetn), .soft_reset(soft_reset),
        .capture_products(input_fire && mode != 0 && stream_error == 0),
        .capture_partials(busy && state == GRAY_PARTIALS && !start),
        .capture_gray(busy && state == GRAY_VALUE && !start),
        .bgr(s_axis_tdata[23:0]), .threshold(threshold),
        .gray(captured_gray), .binary_pixel(binary_pixel));
    line_buffer_3x3 #(.MAX_WIDTH(MAX_WIDTH)) line_buffers(
        .clk(clk), .read_en(input_fire && mode == 3 && stream_error == 0),
        .write_en(process_sobel), .column(column[10:0]),
        .row_parity(row[0]), .incoming_gray(captured_gray),
        .top_gray(top_gray), .middle_gray(middle_gray));
    sobel_3x3 sobel_unit(
        .clk(clk), .resetn(resetn), .soft_reset(soft_reset),
        .capture_sums(busy && state == SOBEL_SUMS && !start),
        .capture_gradients(busy && state == SOBEL_GRADIENTS && !start),
        .p00(top0), .p01(top1), .p02(top_gray),
        .p10(mid0), .p11(mid1), .p12(middle_gray),
        .p20(bot0), .p21(bot1), .p22(captured_gray),
        .magnitude(sobel_pixel));

    always @(posedge clk) begin
        if (!resetn || soft_reset) begin
            state <= IDLE; busy <= 0; done <= 0; error_flags <= 0;
            cycles <= 0; pixels_in <= 0; pixels_out <= 0;
            m_axis_tvalid <= 0; m_axis_tdata <= 0; m_axis_tlast <= 0;
            width <= 0; height <= 0; column <= 0; row <= 0; border_column <= 0;
            mode <= 0; threshold <= 0; captured_last <= 0; total_pixels <= 0;
            top0 <= 0; top1 <= 0; mid0 <= 0; mid1 <= 0; bot0 <= 0; bot1 <= 0;
        end else begin
            if (output_fire) begin
                m_axis_tvalid <= 0;
                pixels_out <= pixels_out + 1;
            end
            if (busy) cycles <= cycles + 1;
            if (start) begin
                if (busy) begin
                    error_flags <= error_flags | 32'h00000040;
                    busy <= 0; done <= 0; state <= IDLE;
                end else if (error_flags == 0) begin
                    done <= 0; cycles <= 0; pixels_in <= 0; pixels_out <= 0;
                    if (cfg_width < 3 || cfg_width > MAX_WIDTH ||
                        cfg_height < 3 || cfg_height > MAX_HEIGHT || cfg_mode > 3) begin
                        error_flags <=
                            ((cfg_width < 3 || cfg_width > MAX_WIDTH || cfg_height < 3 || cfg_height > MAX_HEIGHT) ? 32'h1 : 0) |
                            ((cfg_mode > 3) ? 32'h2 : 0);
                        busy <= 0; state <= IDLE;
                    end else begin
                        width <= cfg_width[15:0]; height <= cfg_height[15:0];
                        mode <= cfg_mode[1:0]; threshold <= cfg_threshold;
                        total_pixels <= cfg_width * cfg_height;
                        column <= 0; row <= 0; border_column <= 0;
                        top0 <= 0; top1 <= 0; mid0 <= 0; mid1 <= 0; bot0 <= 0; bot1 <= 0;
                        busy <= 1; state <= (cfg_mode == 3) ? TOP_BORDER : INPUT_PIXEL;
                    end
                end
            end else if (busy) begin
                case (state)
                    TOP_BORDER: if (output_available) begin
                        m_axis_tdata <= 0; m_axis_tvalid <= 1; m_axis_tlast <= output_is_last;
                        if (border_column == width-1) begin
                            border_column <= 0; state <= INPUT_PIXEL;
                        end else border_column <= border_column+1;
                    end
                    INPUT_PIXEL: if (input_fire) begin
                        pixels_in <= pixels_in+1;
                        if (stream_error != 0) begin
                            error_flags <= error_flags | stream_error;
                            busy <= 0; done <= 0; state <= IDLE;
                        end else if (mode != 0) begin
                            captured_last <= expected_last;
                            state <= GRAY_PARTIALS;
                        end else begin
                            m_axis_tdata <= {8'b0,s_axis_tdata[23:0]};
                            m_axis_tvalid <= 1; m_axis_tlast <= expected_last;
                            if (expected_last) state <= DRAIN;
                        end
                    end
                    GRAY_PARTIALS: state <= GRAY_VALUE;
                    GRAY_VALUE: state <= (mode == 3) ? SOBEL_SUMS : GRAY_PIXEL;
                    GRAY_PIXEL: if (output_available) begin
                        m_axis_tdata <= (mode == 1) ?
                            {8'b0,captured_gray,captured_gray,captured_gray} :
                            {8'b0,binary_pixel,binary_pixel,binary_pixel};
                        m_axis_tvalid <= 1; m_axis_tlast <= captured_last;
                        state <= captured_last ? DRAIN : INPUT_PIXEL;
                    end
                    // Keep the complete window and BRAM address fixed across
                    // the arithmetic pipeline. No new input or row-memory
                    // write is allowed until SOBEL_PIXEL commits exactly once.
                    SOBEL_SUMS: state <= SOBEL_GRADIENTS;
                    SOBEL_GRADIENTS: state <= SOBEL_PIXEL;
                    SOBEL_PIXEL: if (process_sobel) begin
                        top0 <= top1; top1 <= top_gray;
                        mid0 <= mid1; mid1 <= middle_gray;
                        bot0 <= bot1; bot1 <= captured_gray;
                        if (row >= 2 && column != 1) begin
                            m_axis_tdata <= (column == 0) ? 32'b0 : {8'b0,sobel_pixel,sobel_pixel,sobel_pixel};
                            m_axis_tvalid <= 1; m_axis_tlast <= output_is_last;
                        end
                        if (column == width-1) begin
                            column <= 0; row <= row+1;
                            if (row >= 2) state <= RIGHT_BORDER;
                            else state <= INPUT_PIXEL;
                        end else begin
                            column <= column+1; state <= INPUT_PIXEL;
                        end
                    end
                    RIGHT_BORDER: if (output_available) begin
                        m_axis_tdata <= 0; m_axis_tvalid <= 1; m_axis_tlast <= output_is_last;
                        if (row == height) begin
                            border_column <= 0; state <= BOTTOM_BORDER;
                        end else state <= INPUT_PIXEL;
                    end
                    BOTTOM_BORDER: if (output_available) begin
                        m_axis_tdata <= 0; m_axis_tvalid <= 1; m_axis_tlast <= output_is_last;
                        if (border_column == width-1) state <= DRAIN;
                        else border_column <= border_column+1;
                    end
                    DRAIN: begin end
                    default: state <= IDLE;
                endcase
                if (output_fire && m_axis_tlast) begin
                    busy <= 0; done <= 1; state <= IDLE;
                end
            end
        end
    end
endmodule
