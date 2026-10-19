`timescale 1ns / 1ps
module sobel_3x3(
    input wire clk,
    input wire resetn,
    input wire soft_reset,
    input wire capture_sums,
    input wire capture_gradients,
    input wire [7:0] p00, p01, p02,
    input wire [7:0] p10, p11, p12,
    input wire [7:0] p20, p21, p22,
    output wire [7:0] magnitude
);
    // Each weighted side is in 0..1020. Register these sums after the
    // synchronous BRAM read; never chain the RAM, both signed gradients,
    // absolute values and final saturation in one 100 MHz clock period.
    reg [9:0] left_sum, right_sum, top_sum, bottom_sum;
    reg signed [10:0] gx, gy;
    always @(posedge clk) begin
        if (!resetn || soft_reset) begin
            left_sum <= 0; right_sum <= 0; top_sum <= 0; bottom_sum <= 0;
            gx <= 0; gy <= 0;
        end else begin
            if (capture_sums) begin
                left_sum <= {2'b0,p00} + {1'b0,p10,1'b0} + {2'b0,p20};
                right_sum <= {2'b0,p02} + {1'b0,p12,1'b0} + {2'b0,p22};
                top_sum <= {2'b0,p00} + {1'b0,p01,1'b0} + {2'b0,p02};
                bottom_sum <= {2'b0,p20} + {1'b0,p21,1'b0} + {2'b0,p22};
            end
            if (capture_gradients) begin
                gx <= $signed({1'b0,right_sum}) - $signed({1'b0,left_sum});
                gy <= $signed({1'b0,bottom_sum}) - $signed({1'b0,top_sum});
            end
        end
    end
    // A third clock stage is the enclosing core's output AXIS register.
    wire [10:0] abs_x = gx[10] ? -gx : gx;
    wire [10:0] abs_y = gy[10] ? -gy : gy;
    wire [11:0] sum_abs = {1'b0,abs_x} + {1'b0,abs_y};
    assign magnitude = (sum_abs > 12'd255) ? 8'hff : sum_abs[7:0];
endmodule
