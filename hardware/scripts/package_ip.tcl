# Import the verified RTL into a relocatable IP repository.
set script_dir [file dirname [file normalize [info script]]]
set root_dir [file dirname [file dirname $script_dir]]
set hw_dir [file join $root_dir hardware]
set ip_dir [file join $hw_dir ip_repo video_filter_axi_1_0]
file mkdir $ip_dir
create_project -force video_ip_pack [file join $hw_dir build ip_pack] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set rtl_files [concat [glob -nocomplain [file join $root_dir rtl *.v]] [glob -nocomplain [file join $root_dir rtl *.sv]] [glob -nocomplain [file join $root_dir rtl *.vh]]]
if {[llength $rtl_files] == 0} { error "Video RTL sources are missing" }
add_files -norecurse $rtl_files
set_property include_dirs [list [file join $root_dir rtl]] [current_fileset]
set_property top video_filter_axi [current_fileset]
update_compile_order -fileset sources_1
ipx::package_project -root_dir $ip_dir -vendor video.local -library user -taxonomy /UserIP -import_files -set_current true
set core [ipx::current_core]
set_property name video_filter_axi $core
set_property version 1.0 $core
set_property display_name {PYNQ-Z2 BGR Video Filters} $core
set_property description {DMA frame accelerator: passthrough, integer grayscale, threshold and Sobel; AXI4-Lite control and AXI4-Stream 32-bit BGR pixels.} $core
set_property vendor_display_name {FPGA Video Project} $core
set_property supported_families {zynq Production} $core
set_property core_revision 1 $core

# Vivado infers lowercase interfaces from lowercase RTL port prefixes.
# Replace them, rather than exposing the same physical ports twice.
foreach inferred {s_axi s_axis m_axis} {
    if {[llength [ipx::get_bus_interfaces $inferred -of_objects $core]]} {
        ipx::remove_bus_interface $inferred $core
    }
}
foreach map [ipx::get_memory_maps -of_objects $core] {
    ipx::remove_memory_map [get_property name $map] $core
}
set inferred_clock [ipx::get_bus_interfaces s_axi_aclk -of_objects $core]
if {[llength $inferred_clock]} {
    set old_association [ipx::get_bus_parameters ASSOCIATED_BUSIF -of_objects $inferred_clock]
    if {[llength $old_association]} { set_property value {} $old_association }
}

proc video_bus {core name mode abstraction bus_type mappings} {
    set existing [ipx::get_bus_interfaces $name -of_objects $core]
    if {[llength $existing]} { ipx::remove_bus_interface $name $core }
    set bus [ipx::add_bus_interface $name $core]
    set_property interface_mode $mode $bus
    set_property abstraction_type_vlnv $abstraction $bus
    set_property bus_type_vlnv $bus_type $bus
    foreach {logical physical} $mappings {
        if {[llength [ipx::get_ports $physical -of_objects $core]] != 1} { error "Missing RTL port $physical for $name" }
        set map [ipx::add_port_map $logical $bus]
        set_property physical_name $physical $map
    }
    return $bus
}
set ctrl [video_bus $core S_AXI slave xilinx.com:interface:aximm_rtl:1.0 xilinx.com:interface:aximm:1.0 {
    AWADDR s_axi_awaddr AWPROT s_axi_awprot AWVALID s_axi_awvalid AWREADY s_axi_awready
    WDATA s_axi_wdata WSTRB s_axi_wstrb WVALID s_axi_wvalid WREADY s_axi_wready
    BRESP s_axi_bresp BVALID s_axi_bvalid BREADY s_axi_bready
    ARADDR s_axi_araddr ARPROT s_axi_arprot ARVALID s_axi_arvalid ARREADY s_axi_arready
    RDATA s_axi_rdata RRESP s_axi_rresp RVALID s_axi_rvalid RREADY s_axi_rready
}]
set protocol [ipx::add_bus_parameter PROTOCOL $ctrl]
set_property value AXI4LITE $protocol
set input_stream [video_bus $core S_AXIS slave xilinx.com:interface:axis_rtl:1.0 xilinx.com:interface:axis:1.0 {
    TDATA s_axis_tdata TKEEP s_axis_tkeep TLAST s_axis_tlast TVALID s_axis_tvalid TREADY s_axis_tready
}]
set output_stream [video_bus $core M_AXIS master xilinx.com:interface:axis_rtl:1.0 xilinx.com:interface:axis:1.0 {
    TDATA m_axis_tdata TKEEP m_axis_tkeep TLAST m_axis_tlast TVALID m_axis_tvalid TREADY m_axis_tready
}]
set memmap [ipx::add_memory_map S_AXI $core]
set addrblock [ipx::add_address_block Reg $memmap]
set_property base_address 0 $addrblock
set_property range 65536 $addrblock
set_property width 32 $addrblock
set_property usage register $addrblock
set_property slave_memory_map_ref S_AXI $ctrl
foreach {name offset access description} {
    ID 0 read-only {0x56494431 VID1 signature}
    VERSION 4 read-only {Register map version 0x00010000}
    CONTROL 8 read-write {W1 pulse: bit0 START, bit1 SOFTRESET}
    STATUS 12 read-only {bit0 BUSY, bit1 DONE, bit2 ERROR}
    WIDTH 16 read-write {Frame width in pixels; snapshot at START}
    HEIGHT 20 read-write {Frame height in pixels; snapshot at START}
    MODE 24 read-write {0 passthrough, 1 gray, 2 threshold, 3 Sobel}
    THRESHOLD 28 read-write {UINT8 threshold, inclusive greater-or-equal}
    CYCLES 32 read-only {Wall-clock cycles between accepted start and frame completion; includes stalls}
    PIXELS_IN 36 read-only {Accepted input beats in current or completed frame}
    PIXELS_OUT 40 read-only {Accepted output beats in current or completed frame}
    ERROR_FLAGS 44 read-only {Sticky frame/configuration protocol error bits}
    CLOCK_HZ 48 read-only {Accelerator clock frequency}
} {
    set reg [ipx::add_register $name $addrblock]
    set_property address_offset $offset $reg
    set_property size 32 $reg
    set_property access $access $reg
    set_property description $description $reg
}
foreach busif {S_AXI S_AXIS M_AXIS} { ipx::associate_bus_interfaces -busif $busif -clock s_axi_aclk $core }
set clk [ipx::get_bus_interfaces s_axi_aclk -of_objects $core]
if {[llength $clk] == 1} {
    set association [ipx::get_bus_parameters ASSOCIATED_RESET -of_objects $clk]
    if {[llength $association] == 0} { set association [ipx::add_bus_parameter ASSOCIATED_RESET $clk] }
    set_property value s_axi_aresetn $association
}
ipx::create_xgui_files $core
foreach fg [ipx::get_file_groups -of_objects $core] { set_property dependency {} $fg }
ipx::update_checksums $core
ipx::check_integrity -quiet $core
ipx::save_core $core
puts "VIDEO_IP_PACKAGED: [get_property vlnv $core]"
close_project
