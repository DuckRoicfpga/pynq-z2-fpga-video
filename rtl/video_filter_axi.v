`timescale 1ns / 1ps
module video_filter_axi #(
    parameter integer MAX_WIDTH = 1280,
    parameter integer MAX_HEIGHT = 720,
    parameter integer CLOCK_HZ = 100000000
)(
    input wire s_axi_aclk,
    input wire s_axi_aresetn,
    input wire [5:0] s_axi_awaddr,
    input wire [2:0] s_axi_awprot,
    input wire s_axi_awvalid,
    output wire s_axi_awready,
    input wire [31:0] s_axi_wdata,
    input wire [3:0] s_axi_wstrb,
    input wire s_axi_wvalid,
    output wire s_axi_wready,
    output reg [1:0] s_axi_bresp,
    output reg s_axi_bvalid,
    input wire s_axi_bready,
    input wire [5:0] s_axi_araddr,
    input wire [2:0] s_axi_arprot,
    input wire s_axi_arvalid,
    output wire s_axi_arready,
    output reg [31:0] s_axi_rdata,
    output reg [1:0] s_axi_rresp,
    output reg s_axi_rvalid,
    input wire s_axi_rready,
    input wire [31:0] s_axis_tdata,
    input wire [3:0] s_axis_tkeep,
    input wire s_axis_tlast,
    input wire s_axis_tvalid,
    output wire s_axis_tready,
    output wire [31:0] m_axis_tdata,
    output wire [3:0] m_axis_tkeep,
    output wire m_axis_tlast,
    output wire m_axis_tvalid,
    input wire m_axis_tready
);
    reg aw_pending, w_pending;
    reg [5:0] aw_address;
    reg [31:0] w_data;
    reg [3:0] w_strobes;
    reg [31:0] width_reg, height_reg, mode_reg, threshold_reg;
    reg start_pulse, reset_pulse;
    wire busy, done;
    wire [31:0] errors, cycles, pixels_in, pixels_out;
    assign s_axi_awready = !aw_pending && !s_axi_bvalid;
    assign s_axi_wready = !w_pending && !s_axi_bvalid;
    assign s_axi_arready = !s_axi_rvalid || s_axi_rready;
    function [31:0] merge_bytes;
        input [31:0] original;
        input [31:0] replacement;
        input [3:0] strobes;
        integer k;
        begin
            merge_bytes = original;
            for (k=0;k<4;k=k+1)
                if (strobes[k]) merge_bytes[k*8 +: 8] = replacement[k*8 +: 8];
        end
    endfunction
    function [31:0] read_register;
        input [5:0] address;
        begin
            case (address)
                6'h00: read_register=32'h56494431;
                6'h04: read_register=32'h00010000;
                6'h08: read_register=0;
                6'h0c: read_register={29'b0,(errors != 0),done,busy};
                6'h10: read_register=width_reg;
                6'h14: read_register=height_reg;
                6'h18: read_register=mode_reg;
                6'h1c: read_register=threshold_reg;
                6'h20: read_register=cycles;
                6'h24: read_register=pixels_in;
                6'h28: read_register=pixels_out;
                6'h2c: read_register=errors;
                6'h30: read_register=CLOCK_HZ;
                default: read_register=0;
            endcase
        end
    endfunction
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            aw_pending<=0; w_pending<=0; aw_address<=0; w_data<=0; w_strobes<=0;
            s_axi_bvalid<=0; s_axi_bresp<=0; s_axi_rvalid<=0; s_axi_rresp<=0; s_axi_rdata<=0;
            width_reg<=640; height_reg<=480; mode_reg<=0; threshold_reg<=128;
            start_pulse<=0; reset_pulse<=0;
        end else begin
            start_pulse<=0; reset_pulse<=0;
            if (s_axi_awvalid && s_axi_awready) begin
                aw_pending<=1; aw_address<=s_axi_awaddr;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                w_pending<=1; w_data<=s_axi_wdata; w_strobes<=s_axi_wstrb;
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid<=0;
            if (aw_pending && w_pending && !s_axi_bvalid) begin
                aw_pending<=0; w_pending<=0; s_axi_bvalid<=1; s_axi_bresp<=0;
                case (aw_address)
                    6'h08: if (w_strobes[0]) begin
                        reset_pulse<=w_data[1]; start_pulse<=w_data[0] && !w_data[1];
                    end
                    6'h10: width_reg<=merge_bytes(width_reg,w_data,w_strobes);
                    6'h14: height_reg<=merge_bytes(height_reg,w_data,w_strobes);
                    6'h18: mode_reg<=merge_bytes(mode_reg,w_data,w_strobes);
                    6'h1c: threshold_reg<=merge_bytes(threshold_reg,w_data,w_strobes);
                    default: s_axi_bresp<=2'b10;
                endcase
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid<=0;
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rdata<=read_register(s_axi_araddr); s_axi_rvalid<=1;
                s_axi_rresp <= (s_axi_araddr <= 6'h30 && s_axi_araddr[1:0]==0) ? 2'b00 : 2'b10;
            end
        end
    end
    video_filter_core #(.MAX_WIDTH(MAX_WIDTH),.MAX_HEIGHT(MAX_HEIGHT)) core(
        .clk(s_axi_aclk),.resetn(s_axi_aresetn),.start(start_pulse),.soft_reset(reset_pulse),
        .cfg_width(width_reg),.cfg_height(height_reg),
        .cfg_mode(mode_reg),.cfg_threshold(threshold_reg[7:0]),
        .busy(busy),.done(done),.error_flags(errors),.cycles(cycles),
        .pixels_in(pixels_in),.pixels_out(pixels_out),
        .s_axis_tdata(s_axis_tdata),.s_axis_tkeep(s_axis_tkeep),.s_axis_tlast(s_axis_tlast),
        .s_axis_tvalid(s_axis_tvalid),.s_axis_tready(s_axis_tready),
        .m_axis_tdata(m_axis_tdata),.m_axis_tkeep(m_axis_tkeep),.m_axis_tlast(m_axis_tlast),
        .m_axis_tvalid(m_axis_tvalid),.m_axis_tready(m_axis_tready));
endmodule
