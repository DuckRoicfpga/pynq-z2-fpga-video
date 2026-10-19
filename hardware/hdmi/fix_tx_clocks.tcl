# The external TX serial clock is driven directly from axi_dynclk's MMCM,
# while its pixel clock is driven from a parallel BUFR divide-by-5 branch.
# A generated clock from PixelClk backwards to SerialClk has no logical path.
# Keep the actual MMCM/BUFR auto-derived clocks, without timing exceptions.
set ::video_tx_clock_source_path [file join [file dirname [file normalize [info script]]] build video_hdmi video_hdmi.gen sources_1 bd video_hdmi_system ip video_hdmi_system_rgb2dvi_0_0 src rgb2dvi_clocks.xdc]
proc video_disable_tx_clock_file {} {
    set files [get_files -quiet -all *rgb2dvi_clocks.xdc]
    if {![llength $files]} { error "Expected rgb2dvi_clocks.xdc in generated project" }
    set_property IS_ENABLED false $files
    puts "VIDEO_DISABLED_REDUNDANT_TX_CLOCK_XDC: $files"
    return $files
}

proc video_clean_tx_clock_constraints {out_dir {require_removed 0}} {
    set bad [get_clocks -quiet *rgb2dvi_0/U0/SerialClk]
    if {$require_removed && [llength $bad] != 1} {
        error "Expected exactly one redundant manual TX SerialClk in the saved checkpoint: $bad"
    }
    if {[llength $bad] > 1} { error "Ambiguous redundant TX clock: $bad" }
    set removed {}
    if {[llength $bad]} {
        set removed [get_property NAME $bad]
        # Vivado 2022.2 has no delete_clocks command. Use the documented
        # export/reset/read flow, retaining every other exported command.
        file mkdir $out_dir
        set original_xdc [file join $out_dir timing_constraints_before.xdc]
        set corrected_xdc [file join $out_dir timing_constraints_corrected.xdc]
        # DCP loading may restore the project's file-enabled flag. Disable the
        # source again, and export invalid entries too so the delta is explicit.
        write_xdc -force -constraints all -type timing $original_xdc
        set input [open $original_xdc r]
        set output [open $corrected_xdc w]
        set command {}
        set removed_commands 0
        while {[gets $input line] >= 0} {
            append command $line "\n"
            if {![info complete $command]} { continue }
            if {[string first "create_generated_clock" $command] >= 0 &&
                ([string first $removed $command] >= 0 ||
                 [string first "SerialClk" $command] >= 0)} {
                incr removed_commands
            } else {
                puts -nonewline $output $command
            }
            set command {}
        }
        close $input
        close $output
        # write_xdc omits commands from a project XDC already disabled above,
        # even though a restored DCP still contains its clock object.
        if {$removed_commands == 0} {
            # open_checkpoint exposes a netlist project without the original
            # get_files inventory. Verify the pinned staged XDC directly.
            set src [open $::video_tx_clock_source_path r]
            set source_commands {}
            foreach source_line [split [read $src] "\n"] {
                set source_line [string trim $source_line]
                if {$source_line ne {} && ![string match {#*} $source_line]} {
                    lappend source_commands $source_line
                }
            }
            close $src
            set expected {create_generated_clock -source [get_ports PixelClk] -multiply_by 5 [get_ports SerialClk]}
            if {[llength $source_commands] != 1 || [lindex $source_commands 0] ne $expected} {
                error "Disabled TX source must contain only the known incorrect clock declaration"
            }
        }
        if {[string trim $command] ne {} || $removed_commands > 1} {
            error "Expected exactly one complete TX generated-clock command; removed=$removed_commands"
        }
        reset_timing
        read_xdc [list $corrected_xdc]
    }
    update_timing
    if {[llength [get_clocks -quiet *rgb2dvi_0/U0/SerialClk]]} {
        error "Incorrect manual TX generated clock still exists after constraint reload"
    }
    set serial_pin [get_pins -quiet -hierarchical -filter {NAME =~ */hdmi_out/frontend/rgb2dvi_0/U0/SerialClk}]
    set pixel_pin [get_pins -quiet -hierarchical -filter {NAME =~ */hdmi_out/frontend/rgb2dvi_0/U0/PixelClk}]
    if {[llength $serial_pin] != 1 || [llength $pixel_pin] != 1} {
        error "Expected one TX serial/pixel clock sink: serial=$serial_pin pixel=$pixel_pin"
    }
    set serial [get_clocks -quiet -of_objects $serial_pin]
    set pixel [get_clocks -quiet -of_objects $pixel_pin]
    if {[llength $serial] != 1 || [llength $pixel] != 1} {
        error "Auto-derived TX clocks are missing/ambiguous: serial=$serial pixel=$pixel"
    }
    set serial_period [get_property PERIOD $serial]
    set pixel_period [get_property PERIOD $pixel]
    if {$serial_period <= 0 || abs($pixel_period / $serial_period - 5.0) > 0.001} {
        error "Actual TX auto-derived clocks must have serial frequency =5x pixel: periods $serial_period / $pixel_period"
    }
    if {[get_property NAME $serial] eq [get_property NAME $pixel]} {
        error "TX pixel and serial clocks unexpectedly share one frequency"
    }
    foreach clock [list $serial $pixel] {
        if {![get_property IS_GENERATED $clock] || ![llength [get_property SOURCE_PINS $clock]]} {
            error "TX clock must remain a generated clock with an actual source pin: $clock"
        }
    }
    file mkdir $out_dir
    set fp [open [file join $out_dir clock_constraints.rpt] w]
    puts $fp "CLOCK_CONSTRAINT_CORRECTION=REDUNDANT_MANUAL_TX_CLOCK_REMOVED"
    puts $fp "REMOVED_MANUAL_CLOCKS=$removed"
    puts $fp "REMOVAL_METHOD=WRITE_XDC_RESET_TIMING_READ_XDC"
    puts $fp "SERIAL_SINK_PIN=[get_property NAME $serial_pin]"
    puts $fp "PIXEL_SINK_PIN=[get_property NAME $pixel_pin]"
    foreach {label clock} [list SERIAL $serial PIXEL $pixel] {
        puts $fp "${label}_CLOCK_NAME=[get_property NAME $clock]"
        puts $fp "${label}_PERIOD_NS=[get_property PERIOD $clock]"
        puts $fp "${label}_SOURCE_PINS=[get_property SOURCE_PINS $clock]"
        puts $fp "${label}_MASTER_CLOCK=[get_property MASTER_CLOCK $clock]"
        puts $fp "${label}_IS_GENERATED=[get_property IS_GENERATED $clock]"
    }
    puts $fp "SERIAL_FREQUENCY_TO_PIXEL_RATIO=[expr {$pixel_period/$serial_period}]"
    puts $fp "ADDED_FALSE_PATHS=0\nADDED_MULTICYCLE_PATHS=0"
    close $fp
    report_clocks -file [file join $out_dir clock_report.rpt]
    puts "VIDEO_TX_AUTO_CLOCK_PROOF_PASS: serial_period=$serial_period pixel_period=$pixel_period"
}
