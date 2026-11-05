set_arch -family Logos2 -device PG2L200H -speedgrade -6 -package FBB676
add_design {rtl/design.v}
add_constraint {constraints/board.fdc}
compile -top_module advanced_top
synthesize -ads -selected_syn_tool_opt 2
dev_map
pnr
report_timing
gen_bit_stream -scbv {High Voltage}
