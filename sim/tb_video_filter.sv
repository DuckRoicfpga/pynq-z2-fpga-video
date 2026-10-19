`timescale 1ns/1ps
module tb_video_filter;
    reg clk=0; always #5 clk=~clk;
    reg resetn=0;
    reg [5:0] awaddr=0,araddr=0;
    reg [31:0] wdata=0;
    reg [3:0] wstrb=0;
    reg awvalid=0,wvalid=0,bready=0,arvalid=0,rready=0;
    wire awready,wready,bvalid,arready,rvalid;
    wire [1:0] bresp,rresp;
    wire [31:0] rdata;
    reg [31:0] sin_data=0;
    reg [3:0] sin_keep=15;
    reg sin_last=0,sin_valid=0;
    wire sin_ready;
    wire [31:0] mout_data;
    wire [3:0] mout_keep;
    wire mout_last,mout_valid;
    reg mout_ready=0;
    reg random_ready=0, check_pixels=0;
    reg [31:0] inputs[0:921599], expected[0:921599];
    integer out_seen=0,expected_count=0,passed=0,source_gaps=0;
    integer seed=32'h7326a11, dummy_random;
    reg prior_stall=0;
    reg [31:0] prior_data;
    reg prior_last;
    video_filter_axi dut(
        .s_axi_aclk(clk),.s_axi_aresetn(resetn),
        .s_axi_awaddr(awaddr),.s_axi_awprot(3'b0),.s_axi_awvalid(awvalid),.s_axi_awready(awready),
        .s_axi_wdata(wdata),.s_axi_wstrb(wstrb),.s_axi_wvalid(wvalid),.s_axi_wready(wready),
        .s_axi_bresp(bresp),.s_axi_bvalid(bvalid),.s_axi_bready(bready),
        .s_axi_araddr(araddr),.s_axi_arprot(3'b0),.s_axi_arvalid(arvalid),.s_axi_arready(arready),
        .s_axi_rdata(rdata),.s_axi_rresp(rresp),.s_axi_rvalid(rvalid),.s_axi_rready(rready),
        .s_axis_tdata(sin_data),.s_axis_tkeep(sin_keep),.s_axis_tlast(sin_last),
        .s_axis_tvalid(sin_valid),.s_axis_tready(sin_ready),
        .m_axis_tdata(mout_data),.m_axis_tkeep(mout_keep),.m_axis_tlast(mout_last),
        .m_axis_tvalid(mout_valid),.m_axis_tready(mout_ready));
    always @(negedge clk) begin
        if(random_ready) mout_ready <= $urandom_range(0,3)!=0;
    end
    always @(posedge clk) begin
        if (!resetn || dut.reset_pulse) prior_stall<=0;
        else begin
            if (prior_stall && (!mout_valid || mout_data !== prior_data || mout_last !== prior_last || mout_keep !== 4'hf))
                $fatal(1,"AXIS output changed while stalled");
            prior_stall <= mout_valid && !mout_ready;
            prior_data <= mout_data; prior_last <= mout_last;
            if (check_pixels && mout_valid && mout_ready) begin
                if(out_seen>=expected_count) $fatal(1,"Extra output pixel %0d",out_seen);
                if(mout_data !== expected[out_seen]) $fatal(1,"Pixel %0d got %h expected %h",out_seen,mout_data,expected[out_seen]);
                if(mout_last !== (out_seen==expected_count-1)) $fatal(1,"Bad EOF at pixel %0d",out_seen);
                if(mout_keep !== 15) $fatal(1,"Bad output keep");
                out_seen=out_seen+1;
            end
        end
    end
    task send_aw(input [5:0] address);
        begin
            @(negedge clk);awaddr=address;awvalid=1;
            do @(posedge clk); while(!awready);
            @(negedge clk);awvalid=0;
        end
    endtask
    task send_w(input [31:0] data,input [3:0] strobes);
        begin
            @(negedge clk);wdata=data;wstrb=strobes;wvalid=1;
            do @(posedge clk); while(!wready);
            @(negedge clk);wvalid=0;
        end
    endtask
    task write_reg(input [5:0] address,input [31:0] data,input [3:0] strobes,input integer ordering);
        reg [1:0] saved_resp;
        begin
            bready=0;
            if(ordering==1) begin send_aw(address);repeat(3) @(negedge clk);send_w(data,strobes);end
            else if(ordering==2) begin send_w(data,strobes);repeat(3) @(negedge clk);send_aw(address);end
            else fork send_aw(address);send_w(data,strobes);join
            wait(bvalid);saved_resp=bresp;
            repeat(3) begin @(posedge clk);if(!bvalid||bresp!==saved_resp)$fatal(1,"Unstable B response");end
            if(saved_resp!==0)$fatal(1,"Unexpected write response address%h",address);
            @(negedge clk);bready=1;@(posedge clk);@(negedge clk);bready=0;
        end
    endtask
    task read_reg(input [5:0] address,output [31:0] value);
        begin
            @(negedge clk);araddr=address;arvalid=1;rready=0;
            do @(posedge clk);while(!arready);
            @(negedge clk);arvalid=0;wait(rvalid);value=rdata;
            repeat(3) begin @(posedge clk);if(!rvalid||rdata!==value||rresp!==0)$fatal(1,"Unstable/bad R response");end
            @(negedge clk);rready=1;@(posedge clk);@(negedge clk);rready=0;
        end
    endtask
    task send_pixel(input [31:0] data,input last,input [3:0] keep);
        begin
            @(negedge clk);sin_data=data;sin_last=last;sin_keep=keep;sin_valid=1;
            do @(posedge clk);while(!sin_ready);
            @(negedge clk);sin_valid=0;
        end
    endtask
    task configure(input integer w,h,m,t);
        begin
            write_reg('h10,w,15,1);write_reg('h14,h,15,2);
            write_reg('h18,m,15,0);write_reg('h1c,t,15,1);
        end
    endtask
    task expect_error(input [31:0] flag);
        reg [31:0] value;
        begin
            repeat(4) @(posedge clk);read_reg('h2c,value);
            if(value!==flag)$fatal(1,"Expected error %h got %h",flag,value);
            read_reg('h0c,value);if(value[0]||!value[2])$fatal(1,"Bad error status");
            write_reg('h08,2,1,2);read_reg('h2c,value);if(value!==0)$fatal(1,"Reset did not clear errors");
        end
    endtask
    task run_case(input string name,input integer w,h,m,t,input string input_path,output_path);
        integer n,i,gap,timeout;
        reg [31:0] value;
        begin
            n=w*h;expected_count=n;out_seen=0;
            $readmemh(input_path,inputs,0,n-1);$readmemh(output_path,expected,0,n-1);
            configure(w,h,m,t);check_pixels=1;random_ready=1;
            write_reg('h08,1,1,m%3);
            for(i=0;i<n;i=i+1) begin
                // VGA tests use a faster source with a gap every ~16 pixels.
                gap=(n>1000)?(($urandom_range(0,15)==0)?1:0):$urandom_range(0,2);
                repeat(gap) @(negedge clk);
                send_pixel(inputs[i],i==n-1,15);
            end
            timeout=0;
            while(out_seen<n && timeout<n*10+1000)begin @(posedge clk);timeout=timeout+1;end
            if(out_seen!=n)$fatal(1,"Output timeout %s mode%0d got%0d/%0d",name,m,out_seen,n);
            repeat(3)@(posedge clk);read_reg('h0c,value);
            if(value!==2)$fatal(1,"Bad final status %h",value);
            read_reg('h24,value);if(value!=n)$fatal(1,"Input count %0d",value);
            read_reg('h28,value);if(value!=n)$fatal(1,"Output count %0d",value);
            read_reg('h20,value);
            $display("PASS case=%s mode=%0d pixels=%0d cycles=%0d",name,m,n,value);
            check_pixels=0;random_ready=0;@(negedge clk);mout_ready=1;passed=passed+1;
        end
    endtask
    task continuous_frame(input integer n);
        integer i,timeout;
        begin
            @(negedge clk);sin_valid=1;sin_keep=15;
            for(i=0;i<n;i=i+1)begin
                sin_data=inputs[i];sin_last=(i==n-1);
                do @(posedge clk);while(!sin_ready);
                @(negedge clk);
            end
            sin_valid=0;
            timeout=0;
            while(out_seen<n&&timeout<n*4+100)begin @(posedge clk);timeout=timeout+1;end
            if(out_seen!=n)$fatal(1,"Continuous output timeout");
        end
    endtask
    task constant_case(input string label,input integer w,h,m,t,input [31:0] word,expected_word);
        integer i,n;
        reg [31:0] status_value,cycle_value;
        begin
            n=w*h;for(i=0;i<n;i=i+1)begin inputs[i]=word;expected[i]=expected_word;end
            configure(w,h,m,t);out_seen=0;expected_count=n;
            check_pixels=1;random_ready=0;@(negedge clk);mout_ready=1;
            write_reg(8,1,1,0);continuous_frame(n);repeat(3)@(posedge clk);
            read_reg('h0c,status_value);read_reg('h20,cycle_value);
            if(status_value!==2)$fatal(1,"Continuous final status failed");
            $display("PASS continuous=%s mode=%0d pixels=%0d cycles=%0d",label,m,n,cycle_value);
            check_pixels=0;passed=passed+1;
        end
    endtask
    task reset_sobel_stage(input [3:0] target_state);
        integer pixel_index;
        begin
            configure(3,3,3,128);write_reg(8,1,1,0);
            for(pixel_index=0;pixel_index<9;pixel_index=pixel_index+1)
                send_pixel((pixel_index%3==2)?32'h00ffffff:0,pixel_index==8,15);
            // The ninth accepted pixel forms the first meaningful 3x3
            // window. Reset in each arithmetic stage, including commit.
            while(dut.core.state!=target_state) @(negedge clk);
            resetn=0;repeat(3)@(negedge clk);resetn=1;
            repeat(3)@(posedge clk);
            if(mout_valid||dut.busy||dut.core.sobel_unit.left_sum!==0||
               dut.core.sobel_unit.right_sum!==0||dut.core.sobel_unit.top_sum!==0||
               dut.core.sobel_unit.bottom_sum!==0||dut.core.sobel_unit.gx!==0||
               dut.core.sobel_unit.gy!==0)
                $fatal(1,"Sobel pipeline reset failed state=%0d",target_state);
            $display("PASS reset Sobel pipeline stage=%0d",target_state);
        end
    endtask
    task reset_grayscale_stage(input [3:0] target_state);
        begin
            configure(3,3,2,128);write_reg(8,1,1,0);
            send_pixel(32'h00abcdef,0,15);
            while(dut.core.state!=target_state) @(negedge clk);
            resetn=0;repeat(3)@(negedge clk);resetn=1;
            repeat(3)@(posedge clk);
            if(mout_valid||dut.busy||dut.core.gray_unit.red_product!==0||
               dut.core.gray_unit.green_product!==0||dut.core.gray_unit.blue_product!==0||
               dut.core.gray_unit.red_green_sum!==0||dut.core.gray_unit.blue_round_sum!==0||
               dut.core.gray_unit.gray!==0||dut.core.captured_last!==0)
                $fatal(1,"Grayscale pipeline reset failed state=%0d",target_state);
            $display("PASS reset grayscale pipeline stage=%0d",target_state);
        end
    endtask
    integer case_file, scan_result,w,h,m,t;
    string name,input_path,output_path;
    reg [31:0] value;
    initial begin
        dummy_random=$urandom(seed);
        repeat(5)@(posedge clk);@(negedge clk);resetn=1;mout_ready=1;
        read_reg(0,value);if(value!==32'h56494431)$fatal(1,"Bad ID");
        read_reg(4,value);if(value!==32'h00010000)$fatal(1,"Bad version");
        read_reg('h30,value);if(value!==100000000)$fatal(1,"Bad clock");
        // Byte writes: untouched lanes must survive independent AW/W order.
        write_reg('h10,32'h00000123,15,1);write_reg('h10,32'habcdef45,1,2);
        read_reg('h10,value);if(value!==32'h00000145)$fatal(1,"WSTRB failed");
        configure(2,6,0,128);write_reg(8,1,1,1);expect_error(1);
        configure(8,6,4,128);write_reg(8,1,1,2);expect_error(2);
        configure(65544,6,0,128);write_reg(8,1,1,0);expect_error(1);
        configure(8,6,0,128);write_reg(8,1,1,0);send_pixel(0,1,15);expect_error(4);
        configure(3,3,0,128);write_reg(8,1,1,0);
        repeat(9)send_pixel(0,0,15);expect_error(8);
        configure(8,6,0,128);write_reg(8,1,1,0);send_pixel(0,0,7);expect_error(16);
        configure(8,6,0,128);write_reg(8,1,1,0);send_pixel(32'hff000001,0,15);expect_error(32);
        configure(8,6,0,128);write_reg(8,1,1,0);write_reg(8,1,1,2);expect_error(64);
        // Reset with a stalled output must cancel frame and allow clean restart.
        configure(8,6,0,128);write_reg(8,1,1,0);
        @(negedge clk);mout_ready=0;send_pixel(32'h00123456,0,15);
        repeat(5)@(posedge clk);write_reg(8,2,1,1);repeat(3)@(posedge clk);
        if(mout_valid||dut.busy)$fatal(1,"Mid-frame soft reset failed");
        @(negedge clk);mout_ready=1;
        // Shadow writes during BUSY must not alter an already-started frame.
        configure(3,3,2,128);out_seen=0;expected_count=9;
        for(m=0;m<9;m=m+1)expected[m]=0;
        check_pixels=1;write_reg(8,1,1,0);send_pixel(32'h00404040,0,15);
        configure(8,6,0,0);
        for(m=1;m<9;m=m+1)send_pixel(32'h00404040,m==8,15);
        wait(out_seen==9);repeat(3)@(posedge clk);read_reg('h0c,value);
        if(value!==2)$fatal(1,"Frozen configuration frame failed");
        check_pixels=0;passed=passed+1;$display("PASS configuration frozen during frame");
        // Hard reset during the Sobel warm-up also clears protocol state.
        configure(8,6,3,128);write_reg(8,1,1,0);
        repeat(4)send_pixel(32'h00abcdef,0,15);
        @(negedge clk);resetn=0;repeat(3)@(negedge clk);resetn=1;
        repeat(3)@(posedge clk);if(mout_valid||dut.busy)$fatal(1,"Hard reset failed");
        reset_sobel_stage(7); // Registered weighted side sums.
        reset_sobel_stage(8); // Signed gradients.
        reset_sobel_stage(2); // Absolute value/saturation output commit.
        reset_grayscale_stage(9); // Registered channel products.
        reset_grayscale_stage(10); // Registered partial sums.
        reset_grayscale_stage(11); // Registered Y before threshold/output.
        case_file=$fopen("cases.txt","r");if(case_file==0)$fatal(1,"No cases.txt");
        while(!$feof(case_file)) begin
            scan_result=$fscanf(case_file,"%s %d %d %d %d %s %s\n",name,w,h,m,t,input_path,output_path);
            if(scan_result==7)run_case(name,w,h,m,t,input_path,output_path);
            else if(!$feof(case_file))$fatal(1,"Invalid cases row");
        end
        $fclose(case_file);
        constant_case("VGA_passthrough",640,480,0,128,32'h001e3c5a,32'h001e3c5a);
        constant_case("VGA_grayscale",640,480,1,128,32'h001e3c5a,32'h00363636);
        constant_case("VGA_threshold",640,480,2,128,32'h001e3c5a,0);
        constant_case("VGA_Sobel",640,480,3,128,32'h001e3c5a,0);
        constant_case("720p_Sobel_max_dimensions",1280,720,3,128,32'h00ffffff,0);
        constant_case("3x3_threshold_zero",3,3,2,0,0,32'h00ffffff);
        constant_case("3x3_threshold255",3,3,2,255,32'h00ffffff,32'h00ffffff);
        // Smallest window: one interior pixel has a hand-computed vertical edge.
        for(m=0;m<9;m=m+1)begin inputs[m]=(m%3==2)?32'h00ffffff:0;expected[m]=(m==4)?32'h00ffffff:0;end
        configure(3,3,3,128);out_seen=0;expected_count=9;check_pixels=1;
        write_reg(8,1,1,0);continuous_frame(9);repeat(3)@(posedge clk);
        read_reg('h0c,value);if(value!==2)$fatal(1,"Min Sobel failed");
        check_pixels=0;passed=passed+1;$display("PASS 3x3 hand-computed Sobel center=255");
        if(passed!=101)$fatal(1,"Not all101comparisons exercised: %0d",passed);
        $display("ALL RTL TESTS PASS comparisons=%0d protocol/error/reset_tests=21",passed);
        $finish;
    end
    initial begin
        #1000000000;$fatal(1,"Global simulation timeout");
    end
endmodule
