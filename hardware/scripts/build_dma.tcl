set script_dir [file dirname [file normalize [info script]]]
set root_dir [file dirname [file dirname $script_dir]]
set hw_dir [file join $root_dir hardware]
set out_dir [file join $root_dir output dma_overlay]
file mkdir $out_dir
set_param general.maxThreads 2
source [file join $script_dir package_ip.tcl]
set boards [get_board_parts -quiet *pynq-z2*]
if {[llength $boards] == 0} { error "Install PYNQ-Z2 board files before building" }
set board [lindex $boards 0]
create_project -force video_dma [file join $hw_dir build video_dma] -part xc7z020clg400-1
set_property board_part $board [current_project]
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list [file join $hw_dir ip_repo]] [current_project]
update_ip_catalog
create_bd_design video_dma_system
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} $ps
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_S_AXI_HP0 {1} CONFIG.PCW_S_AXI_HP0_DATA_WIDTH {64} CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_RST0_PORT {1} CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100} CONFIG.PCW_USE_FABRIC_INTERRUPT {1} CONFIG.PCW_IRQ_F2P_INTR {1} CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_0 {0.040} CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_1 {0.058} CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_2 {0.000} CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_3 {0.000}] $ps
set_property CONFIG.PCW_M_AXI_GP0_FREQMHZ.VALUE_SRC DEFAULT $ps
set_property CONFIG.PCW_S_AXI_HP0_FREQMHZ.VALUE_SRC DEFAULT $ps
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_include_mm2s {1} CONFIG.c_include_s2mm {1} CONFIG.c_m_axi_mm2s_data_width {32} CONFIG.c_m_axi_s2mm_data_width {32} CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32} CONFIG.c_include_mm2s_dre {1} CONFIG.c_include_s2mm_dre {1} CONFIG.c_sg_length_width {23} CONFIG.c_mm2s_burst_size {16} CONFIG.c_s2mm_burst_size {16}] $dma
set filter [create_bd_cell -type ip -vlnv video.local:user:video_filter_axi:1.0 video_filter_0]
set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 control_interconnect]
set_property CONFIG.NUM_MI 2 $ctrl
set memory [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 memory_interconnect]
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] $memory
set reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_fclk0]
set_property CONFIG.C_AUX_RESET_HIGH {1} $reset
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero
set irq [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 irq_concat]
set_property CONFIG.NUM_PORTS 2 $irq

connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins control_interconnect/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins control_interconnect/M00_AXI] [get_bd_intf_pins video_filter_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins control_interconnect/M01_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_MM2S] [get_bd_intf_pins memory_interconnect/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXI_S2MM] [get_bd_intf_pins memory_interconnect/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins memory_interconnect/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP0]
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins video_filter_0/S_AXIS]
connect_bd_intf_net [get_bd_intf_pins video_filter_0/M_AXIS] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK] [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK] \
    [get_bd_pins control_interconnect/ACLK] [get_bd_pins control_interconnect/S00_ACLK] [get_bd_pins control_interconnect/M00_ACLK] [get_bd_pins control_interconnect/M01_ACLK] \
    [get_bd_pins memory_interconnect/ACLK] [get_bd_pins memory_interconnect/S00_ACLK] [get_bd_pins memory_interconnect/S01_ACLK] [get_bd_pins memory_interconnect/M00_ACLK] \
    [get_bd_pins axi_dma_0/s_axi_lite_aclk] [get_bd_pins axi_dma_0/m_axi_mm2s_aclk] [get_bd_pins axi_dma_0/m_axi_s2mm_aclk] \
    [get_bd_pins video_filter_0/s_axi_aclk] [get_bd_pins rst_fclk0/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_fclk0/ext_reset_in]
connect_bd_net [get_bd_pins const_one/dout] [get_bd_pins rst_fclk0/dcm_locked]
connect_bd_net [get_bd_pins const_zero/dout] [get_bd_pins rst_fclk0/aux_reset_in] [get_bd_pins rst_fclk0/mb_debug_sys_rst]
connect_bd_net [get_bd_pins rst_fclk0/interconnect_aresetn] [get_bd_pins control_interconnect/ARESETN] [get_bd_pins memory_interconnect/ARESETN]
connect_bd_net [get_bd_pins rst_fclk0/peripheral_aresetn] \
    [get_bd_pins control_interconnect/S00_ARESETN] [get_bd_pins control_interconnect/M00_ARESETN] [get_bd_pins control_interconnect/M01_ARESETN] \
    [get_bd_pins memory_interconnect/S00_ARESETN] [get_bd_pins memory_interconnect/S01_ARESETN] [get_bd_pins memory_interconnect/M00_ARESETN] \
    [get_bd_pins axi_dma_0/axi_resetn] [get_bd_pins video_filter_0/s_axi_aresetn]
connect_bd_net [get_bd_pins axi_dma_0/mm2s_introut] [get_bd_pins irq_concat/In0]
connect_bd_net [get_bd_pins axi_dma_0/s2mm_introut] [get_bd_pins irq_concat/In1]
connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins processing_system7_0/IRQ_F2P]
set filter_segments [get_bd_addr_segs -quiet video_filter_0/*]
puts "VIDEO_FILTER_ADDRESS_SEGMENTS: $filter_segments"
if {[llength $filter_segments] != 1} { error "Expected exactly one custom filter register segment: $filter_segments" }
assign_bd_address -offset 0x40000000 -range 0x00010000 -target_address_space [get_bd_addr_spaces processing_system7_0/Data] $filter_segments -force
assign_bd_address -offset 0x40400000 -range 0x00010000 -target_address_space [get_bd_addr_spaces processing_system7_0/Data] [get_bd_addr_segs axi_dma_0/S_AXI_LITE/Reg] -force
assign_bd_address
validate_bd_design
if {[get_property CONFIG.C_EXT_RESET_HIGH $reset] != 0 || [get_property CONFIG.C_AUX_RESET_HIGH $reset] != 1} {
    error "Reset polarity is not compatible with FCLK_RESET0_N and inactive auxiliary reset"
}
save_bd_design
write_bd_tcl -force [file join $out_dir video_dma_system.tcl]
set bd_file [get_files */video_dma_system.bd]
set_property synth_checkpoint_mode None $bd_file
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse [list $wrapper]
set_property top video_dma_system_wrapper [current_fileset]
update_compile_order -fileset sources_1
# Global synthesis keeps the build in this Vivado process and avoids stale
# per-IP worker jobs after interruption. The generated BD/HWH are unchanged.
synth_design -top video_dma_system_wrapper -part xc7z020clg400-1
opt_design
place_design
phys_opt_design
route_design
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file [file join $out_dir timing_post_route.rpt]
report_utilization -hierarchical -file [file join $out_dir utilization_post_route.rpt]
report_drc -file [file join $out_dir drc_post_route.rpt]
set setup_slack [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold_slack [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
set critical_drc [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]
if {$setup_slack < 0 || $hold_slack < 0} { error "Video DMA timing failed: setup=$setup_slack hold=$hold_slack" }
if {[llength $critical_drc]} { error "Critical DRC violations remain: $critical_drc" }
write_checkpoint -force [file join $out_dir video_dma_routed.dcp]
write_bitstream -force [file join $out_dir video_dma.bit]
set hwh_files [glob -nocomplain [file join $hw_dir build video_dma video_dma.gen sources_1 bd video_dma_system hw_handoff *.hwh]]
if {[llength $hwh_files] != 1} { error "Expected one matching HWH, found $hwh_files" }
file copy -force [lindex $hwh_files 0] [file join $out_dir video_dma.hwh]
# In this global (non-run) flow, export the hardware description without
# -include_bit, which requires a completed project implementation run.
# The matching .bit is exported above and delivered alongside .hwh.
write_hw_platform -fixed -force -file [file join $out_dir video_dma.xsa]
set fp [open [file join $out_dir implementation_status.txt] w]
puts $fp "BOARD=$board"
puts $fp "CLOCK_HZ=100000000"
puts $fp "FILTER_AXI_BASE=0x40000000"
puts $fp "DMA_AXI_BASE=0x40400000"
puts $fp "SETUP_SLACK_NS=$setup_slack"
puts $fp "HOLD_SLACK_NS=$hold_slack"
puts $fp "CRITICAL_DRC_COUNT=[llength $critical_drc]"
puts $fp "BUILD_FLOW=GLOBAL_SYNTHESIS_DIRECT_IMPLEMENTATION"
close $fp
puts "VIDEO_DMA_BUILD_COMPLETE: setup=$setup_slack hold=$hold_slack"
exit
