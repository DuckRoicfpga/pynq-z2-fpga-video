# Compact PYNQ-Z2 HDMI + independent frame-filter DMA overlay.
# Upstream video subsystem is derived from pinned PYNQ v3.0.1 (BSD-3-Clause).
# Usage: Vivado -mode batch -source build_hdmi.tcl -tclargs bd-only
# Omit bd-only to synthesize, route, verify timing and emit bit/hwh/xsa.
set here [file dirname [file normalize [info script]]]
set root [file dirname [file dirname $here]]
set vendor [file join $here vendor PYNQ]
set build_dir [file join $here build video_hdmi]
set out_dir [file join $root output hdmi_overlay]
file mkdir $out_dir
set_param general.maxThreads 2
set bd_only [expr {[llength $argv] && [lindex $argv 0] eq "bd-only"}]
foreach name {color_convert pixel_pack pixel_unpack} {
    if {![file exists [file join $vendor boards ip hls $name solution1 impl ip component.xml]]} {
        error "Missing HLS IP $name; run hardware/hdmi/build_hls.ps1 first."
    }
}
if {![file exists [file join $here generated video_hierarchy.tcl]]} {
    error "Run prepare_sources.py first."
}
set boards [get_board_parts -quiet *pynq-z2*]
if {![llength $boards]} { error "PYNQ-Z2 board files missing." }
create_project -force video_hdmi $build_dir -part xc7z020clg400-1
set_property board_part [lindex $boards 0] [current_project]
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list [file join $root hardware ip_repo] [file join $vendor boards ip]] [current_project]
update_ip_catalog
create_bd_design video_hdmi_system
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} $ps
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_USE_S_AXI_HP0 {1} CONFIG.PCW_S_AXI_HP0_DATA_WIDTH {64} \
    CONFIG.PCW_USE_S_AXI_HP1 {1} CONFIG.PCW_S_AXI_HP1_DATA_WIDTH {64} \
    CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_CLK1_PORT {1} CONFIG.PCW_EN_CLK2_PORT {1} \
    CONFIG.PCW_EN_RST0_PORT {1} CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100} \
    CONFIG.PCW_FPGA1_PERIPHERAL_FREQMHZ {142.857143} CONFIG.PCW_FPGA2_PERIPHERAL_FREQMHZ {200} \
    CONFIG.PCW_USE_FABRIC_INTERRUPT {1} CONFIG.PCW_IRQ_F2P_INTR {1} \
    CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_0 {0.040} \
    CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_1 {0.058} \
    CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_2 {0.000} \
    CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_3 {0.000}] $ps
set_property CONFIG.PCW_M_AXI_GP0_FREQMHZ.VALUE_SRC DEFAULT $ps
set_property CONFIG.PCW_S_AXI_HP0_FREQMHZ.VALUE_SRC DEFAULT $ps
set_property CONFIG.PCW_S_AXI_HP1_FREQMHZ.VALUE_SRC DEFAULT $ps

set script_folder [file join $vendor boards Pynq-Z2 base]
source [file join $here generated video_hierarchy.tcl]
create_hier_cell_video / video
# The upstream template defaults the RX to kClkRange=1 (MMCM x5).
# This project targets 720p74.25MHz: x10 gives an in-range742.5MHz VCO.
# Preserve upstream procedure bodies and apply the board-level override here.
set_property CONFIG.kClkRange {2} [get_bd_cells video/hdmi_in/frontend/dvi2rgb_0]
foreach {name mode vlnv pin} {
    hdmi_in Slave digilentinc.com:interface:tmds_rtl:1.0 TMDS_in
    hdmi_out Master digilentinc.com:interface:tmds_rtl:1.0 TMDS_out
    hdmi_in_ddc Master xilinx.com:interface:iic_rtl:1.0 DDC
} {
    create_bd_intf_port -mode $mode -vlnv $vlnv $name
    connect_bd_intf_net [get_bd_intf_ports $name] [get_bd_intf_pins video/$pin]
}
foreach name {hdmi_in_hpd hdmi_out_hpd} {
    create_bd_port -dir O -from 0 -to 0 $name
    connect_bd_net [get_bd_ports $name] [get_bd_pins video/$name]
}
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list CONFIG.c_include_sg {0} CONFIG.c_include_mm2s {1} CONFIG.c_include_s2mm {1} \
    CONFIG.c_m_axi_mm2s_data_width {32} CONFIG.c_m_axi_s2mm_data_width {32} \
    CONFIG.c_m_axis_mm2s_tdata_width {32} CONFIG.c_s_axis_s2mm_tdata_width {32} \
    CONFIG.c_include_mm2s_dre {1} CONFIG.c_include_s2mm_dre {1} CONFIG.c_sg_length_width {23} \
    CONFIG.c_mm2s_burst_size {16} CONFIG.c_s2mm_burst_size {16}] $dma
create_bd_cell -type ip -vlnv video.local:user:video_filter_axi:1.0 video_filter_0
set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 control_interconnect]
set_property CONFIG.NUM_MI 4 $ctrl
set memory [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 memory_interconnect]
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] $memory
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_fclk0
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_fclk1
foreach reset {rst_fclk0 rst_fclk1} {
    # EXT_RESET polarity is propagated from FCLK_RESET0_N and is read-only.
    set_property CONFIG.C_AUX_RESET_HIGH {1} [get_bd_cells $reset]
}
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_one]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $one
set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $zero
set irq [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 irq_concat]
set_property -dict [list CONFIG.NUM_PORTS {3} CONFIG.IN0_WIDTH {6}] $irq
# Stock PYNQ images expose the first PL interrupt through UIO. Multiplex all
# video/DMA interrupts through AXI INTC, as the official base overlay does,
# rather than requiring separate device-tree UIO nodes for IRQ61..68.
set intc [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_intc:4.1 system_interrupts]
set_property -dict [list CONFIG.C_KIND_OF_INTR {0x00000000} \
    CONFIG.C_ASYNC_INTR {0xFFFFFFFF} CONFIG.C_IRQ_CONNECTION {1}] $intc
connect_bd_net [get_bd_pins video/video_irq] [get_bd_pins irq_concat/In0]
connect_bd_net [get_bd_pins axi_dma_0/mm2s_introut] [get_bd_pins irq_concat/In1]
connect_bd_net [get_bd_pins axi_dma_0/s2mm_introut] [get_bd_pins irq_concat/In2]
connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins system_interrupts/intr]
connect_bd_net [get_bd_pins system_interrupts/irq] [get_bd_pins processing_system7_0/IRQ_F2P]

foreach {a b} {
    processing_system7_0/M_AXI_GP0 control_interconnect/S00_AXI
    control_interconnect/M00_AXI video/S_AXI
    control_interconnect/M01_AXI axi_dma_0/S_AXI_LITE
    control_interconnect/M02_AXI video_filter_0/S_AXI
    control_interconnect/M03_AXI system_interrupts/s_axi
    video/M_AXI processing_system7_0/S_AXI_HP0
    axi_dma_0/M_AXI_MM2S memory_interconnect/S00_AXI
    axi_dma_0/M_AXI_S2MM memory_interconnect/S01_AXI
    memory_interconnect/M00_AXI processing_system7_0/S_AXI_HP1
    axi_dma_0/M_AXIS_MM2S video_filter_0/S_AXIS
    video_filter_0/M_AXIS axi_dma_0/S_AXIS_S2MM
} { connect_bd_intf_net [get_bd_intf_pins $a] [get_bd_intf_pins $b] }
set c0 [get_bd_pins processing_system7_0/FCLK_CLK0]
foreach pin {
    processing_system7_0/M_AXI_GP0_ACLK processing_system7_0/S_AXI_HP1_ACLK
    control_interconnect/ACLK control_interconnect/S00_ACLK
    control_interconnect/M00_ACLK control_interconnect/M01_ACLK control_interconnect/M02_ACLK control_interconnect/M03_ACLK
    memory_interconnect/ACLK memory_interconnect/S00_ACLK memory_interconnect/S01_ACLK memory_interconnect/M00_ACLK
    axi_dma_0/s_axi_lite_aclk axi_dma_0/m_axi_mm2s_aclk axi_dma_0/m_axi_s2mm_aclk
    video_filter_0/s_axi_aclk system_interrupts/s_axi_aclk rst_fclk0/slowest_sync_clk video/clk_100M
} { connect_bd_net $c0 [get_bd_pins $pin] }
foreach pin {processing_system7_0/S_AXI_HP0_ACLK rst_fclk1/slowest_sync_clk video/clk_142M} {
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK1] [get_bd_pins $pin]
}
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK2] [get_bd_pins video/clk_200M]
foreach reset {rst_fclk0 rst_fclk1} {
    connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins $reset/ext_reset_in]
    connect_bd_net [get_bd_pins const_one/dout] [get_bd_pins $reset/dcm_locked]
    connect_bd_net [get_bd_pins const_zero/dout] [get_bd_pins $reset/aux_reset_in] [get_bd_pins $reset/mb_debug_sys_rst]
}
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins video/system_resetn]
foreach pin {control_interconnect/ARESETN memory_interconnect/ARESETN video/ic_resetn_clk100M} {
    connect_bd_net [get_bd_pins rst_fclk0/interconnect_aresetn] [get_bd_pins $pin]
}
foreach pin {
    control_interconnect/S00_ARESETN control_interconnect/M00_ARESETN control_interconnect/M01_ARESETN control_interconnect/M02_ARESETN control_interconnect/M03_ARESETN
    memory_interconnect/S00_ARESETN memory_interconnect/S01_ARESETN memory_interconnect/M00_ARESETN
    axi_dma_0/axi_resetn video_filter_0/s_axi_aresetn system_interrupts/s_axi_aresetn video/periph_resetn_clk100M
} { connect_bd_net [get_bd_pins rst_fclk0/peripheral_aresetn] [get_bd_pins $pin] }
connect_bd_net [get_bd_pins rst_fclk1/interconnect_aresetn] [get_bd_pins video/ic_resetn_clk142M]
connect_bd_net [get_bd_pins rst_fclk1/peripheral_aresetn] [get_bd_pins video/periph_resetn_clk142M]

set ps_space [get_bd_addr_spaces processing_system7_0/Data]
foreach {offset segment} {
    0x43000000 video/axi_vdma/S_AXI_LITE/Reg
    0x43C10000 video/hdmi_out/frontend/axi_dynclk/s00_axi/reg0
    0x43C20000 video/hdmi_out/frontend/vtc_out/ctrl/Reg
    0x43C30000 video/hdmi_in/frontend/vtc_in/ctrl/Reg
    0x43C40000 video/hdmi_in/pixel_pack/s_axi_control/Reg
    0x43C50000 video/hdmi_in/color_convert/s_axi_control/Reg
    0x43C60000 video/hdmi_out/color_convert/s_axi_control/Reg
    0x43C70000 video/hdmi_out/pixel_unpack/s_axi_control/Reg
    0x41220000 video/hdmi_in/frontend/axi_gpio_hdmiin/S_AXI/Reg
    0x41230000 video/hdmi_out/frontend/hdmi_out_hpd_video/S_AXI/Reg
    0x40400000 axi_dma_0/S_AXI_LITE/Reg
    0x41200000 system_interrupts/S_AXI/Reg
} { assign_bd_address -offset $offset -range 0x10000 -target_address_space $ps_space [get_bd_addr_segs $segment] -force }
set filter_segments [get_bd_addr_segs -quiet video_filter_0/*]
if {[llength $filter_segments] != 1} { error "Unexpected filter segment: $filter_segments" }
assign_bd_address -offset 0x43C80000 -range 0x10000 -target_address_space $ps_space $filter_segments -force
assign_bd_address
validate_bd_design
foreach reset {rst_fclk0 rst_fclk1} {
    if {[get_property CONFIG.C_EXT_RESET_HIGH [get_bd_cells $reset]] != 0} {
        error "$reset ext_reset_in must be active-low for FCLK_RESET0_N"
    }
}
save_bd_design
write_bd_tcl -force [file join $out_dir video_hdmi_system.tcl]
set bd_file [get_files */video_hdmi_system.bd]
# Use a single global synthesis job instead of spawning a separate run for
# each of the video subsystem's many IP blocks.
set_property synth_checkpoint_mode None $bd_file
generate_target all $bd_file
set wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse [list $wrapper]
add_files -fileset constrs_1 [list [file join $here generated hdmi_pynq_z2.xdc]]
set_property top video_hdmi_system_wrapper [current_fileset]
update_compile_order -fileset sources_1
report_ip_status -file [file join $out_dir ip_status.rpt]
if {$bd_only} { puts "VIDEO_HDMI_BD_VALIDATED"; exit }
source [file join $here fix_tx_clocks.tcl]
video_disable_tx_clock_file
# Keep synthesis and implementation in this process. Besides avoiding dozens
# of OOC jobs, this avoids a second ~1GB parent/worker Vivado pair on a 16GB PC.
# Digilent EEPROM_8b opens kInitFileName relative to the synthesis working
# directory. Stage the unmodified pinned EDID in a dedicated directory, rather
# than depending on whichever directory launched this script.
set synth_dir [file join $build_dir synth_work]
file mkdir $synth_dir
file copy -force [file join $here generated 720p_edid.data] [file join $synth_dir 720p_edid.data]
set saved_cwd [pwd]
cd $synth_dir
set synth_rc [catch {synth_design -top video_hdmi_system_wrapper -part xc7z020clg400-1} synth_result synth_options]
cd $saved_cwd
if {$synth_rc} { return -options $synth_options $synth_result }
# Global synthesis may consume the local initialization-file copy during its
# cleanup. Retain an identical copy beside the synthesis checkpoint for audit.
file copy -force [file join $here generated 720p_edid.data] [file join $synth_dir 720p_edid.data]
video_clean_tx_clock_constraints $out_dir
write_checkpoint -force [file join $out_dir video_hdmi_synth.dcp]
opt_design
place_design
phys_opt_design
route_design
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file [file join $out_dir timing_post_route.rpt]
report_utilization -hierarchical -file [file join $out_dir utilization_post_route.rpt]
report_drc -file [file join $out_dir drc_post_route.rpt]
report_cdc -file [file join $out_dir cdc_post_route.rpt]
report_clocks -file [file join $out_dir clock_report.rpt]
set setup_slack [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold_slack [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
set critical [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]
if {$setup_slack < 0 || $hold_slack < 0 || [llength $critical]} { error "HDMI timing/DRC failed: setup=$setup_slack hold=$hold_slack critical=$critical" }
write_checkpoint -force [file join $out_dir video_hdmi_routed.dcp]
write_bitstream -force [file join $out_dir video_hdmi.bit]
set hwh [glob -nocomplain [file join $build_dir video_hdmi.gen sources_1 bd video_hdmi_system hw_handoff *.hwh]]
if {[llength $hwh] != 1} { error "Expected one HWH: $hwh" }
file copy -force [lindex $hwh 0] [file join $out_dir video_hdmi.hwh]
# Direct global flow has no impl_1 run for write_hw_platform -include_bit.
# Export the hardware description and distribute its verified bit separately.
write_hw_platform -fixed -force -file [file join $out_dir video_hdmi.xsa]
set fp [open [file join $out_dir implementation_status.txt] w]
puts $fp "BOARD=[lindex $boards 0]\nCLOCK_HZ=100000000\nFILTER_AXI_BASE=0x43C80000\nDMA_AXI_BASE=0x40400000\nSETUP_SLACK_NS=$setup_slack\nHOLD_SLACK_NS=$hold_slack\nCRITICAL_DRC_COUNT=[llength $critical]\nBUILD_FLOW=GLOBAL_SYNTHESIS_DIRECT_IMPLEMENTATION\nPHYSICAL_BOARD_TESTED=false"
close $fp
puts "VIDEO_HDMI_BUILD_COMPLETE: setup=$setup_slack hold=$hold_slack"
exit
