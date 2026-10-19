# Definitional proc to organize widgets for parameters.
proc init_gui { IPINST } {
  ipgui::add_param $IPINST -name "Component_Name"
  #Adding Page
  set Page_0 [ipgui::add_page $IPINST -name "Page 0"]
  ipgui::add_param $IPINST -name "CLOCK_HZ" -parent ${Page_0}
  ipgui::add_param $IPINST -name "MAX_HEIGHT" -parent ${Page_0}
  ipgui::add_param $IPINST -name "MAX_WIDTH" -parent ${Page_0}


}

proc update_PARAM_VALUE.CLOCK_HZ { PARAM_VALUE.CLOCK_HZ } {
	# Procedure called to update CLOCK_HZ when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.CLOCK_HZ { PARAM_VALUE.CLOCK_HZ } {
	# Procedure called to validate CLOCK_HZ
	return true
}

proc update_PARAM_VALUE.MAX_HEIGHT { PARAM_VALUE.MAX_HEIGHT } {
	# Procedure called to update MAX_HEIGHT when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.MAX_HEIGHT { PARAM_VALUE.MAX_HEIGHT } {
	# Procedure called to validate MAX_HEIGHT
	return true
}

proc update_PARAM_VALUE.MAX_WIDTH { PARAM_VALUE.MAX_WIDTH } {
	# Procedure called to update MAX_WIDTH when any of the dependent parameters in the arguments change
}

proc validate_PARAM_VALUE.MAX_WIDTH { PARAM_VALUE.MAX_WIDTH } {
	# Procedure called to validate MAX_WIDTH
	return true
}


proc update_MODELPARAM_VALUE.MAX_WIDTH { MODELPARAM_VALUE.MAX_WIDTH PARAM_VALUE.MAX_WIDTH } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.MAX_WIDTH}] ${MODELPARAM_VALUE.MAX_WIDTH}
}

proc update_MODELPARAM_VALUE.MAX_HEIGHT { MODELPARAM_VALUE.MAX_HEIGHT PARAM_VALUE.MAX_HEIGHT } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.MAX_HEIGHT}] ${MODELPARAM_VALUE.MAX_HEIGHT}
}

proc update_MODELPARAM_VALUE.CLOCK_HZ { MODELPARAM_VALUE.CLOCK_HZ PARAM_VALUE.CLOCK_HZ } {
	# Procedure called to set VHDL generic/Verilog parameter value(s) based on TCL parameter value
	set_property value [get_property value ${PARAM_VALUE.CLOCK_HZ}] ${MODELPARAM_VALUE.CLOCK_HZ}
}

