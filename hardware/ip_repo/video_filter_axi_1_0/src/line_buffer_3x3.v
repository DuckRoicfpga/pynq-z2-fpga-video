`timescale 1ns / 1ps
module line_buffer_3x3 #(
    parameter integer MAX_WIDTH = 1280
)(
    input wire clk,
    input wire read_en,
    input wire write_en,
    input wire [10:0] column,
    input wire row_parity,
    input wire [7:0] incoming_gray,
    output wire [7:0] top_gray,
    output wire [7:0] middle_gray
);
    // The row being overwritten contains row y-2. The other contains y-1.
    // Arrays do not require a reset: the first two rows initialize all entries
    // before any Sobel calculation can observe them.
    (* ram_style = "block" *) reg [7:0] row0 [0:MAX_WIDTH-1];
    (* ram_style = "block" *) reg [7:0] row1 [0:MAX_WIDTH-1];
    reg [7:0] read0, read1;
    reg parity_read;
    always @(posedge clk) begin
        if (read_en) begin
            read0 <= row0[column];
            read1 <= row1[column];
            parity_read <= row_parity;
        end
        if (write_en && !row_parity) row0[column] <= incoming_gray;
        if (write_en && row_parity) row1[column] <= incoming_gray;
    end
    assign top_gray = parity_read ? read1 : read0;
    assign middle_gray = parity_read ? read0 : read1;
endmodule
