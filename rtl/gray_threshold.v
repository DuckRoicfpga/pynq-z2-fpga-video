`timescale 1ns / 1ps
module gray_threshold(
    input wire clk,
    input wire resetn,
    input wire soft_reset,
    input wire capture_products,
    input wire capture_partials,
    input wire capture_gray,
    input wire [23:0] bgr,
    input wire [7:0] threshold,
    output reg [7:0] gray,
    output wire [7:0] binary_pixel
);
    // The rounded unsigned total is at most 65408; all arithmetic is
    // explicitly 16 bits. Break constant multiplication, partial sums,
    // final sum and threshold/output across registers at 100 MHz.
    reg [15:0] red_product, green_product, blue_product;
    reg [15:0] red_green_sum, blue_round_sum;
    wire [15:0] weighted = red_green_sum + blue_round_sum;
    always @(posedge clk) begin
        if (!resetn || soft_reset) begin
            red_product <= 0; green_product <= 0; blue_product <= 0;
            red_green_sum <= 0; blue_round_sum <= 0; gray <= 0;
        end else begin
            if (capture_products) begin
                red_product <= 16'd77 * bgr[23:16];
                green_product <= 16'd150 * bgr[15:8];
                blue_product <= 16'd29 * bgr[7:0];
            end
            if (capture_partials) begin
                red_green_sum <= red_product + green_product;
                blue_round_sum <= blue_product + 16'd128;
            end
            if (capture_gray) gray <= weighted[15:8];
        end
    end
    assign binary_pixel = (gray >= threshold) ? 8'hff : 8'h00;
endmodule
