set_arch -family Logos2 -device PG2L200H -speedgrade -6 -package FBB676
add_design {rtl/design.v}
add_design {vendor/ddr3/ddr3_test.idf}
add_constraint {constraints/mini_board_candidate.fdc}
add_constraint {constraints/matrix_pins.fdc}
add_constraint {constraints/ddr_pg2l200h.fdc}
compile -top_module multimodal_top
synthesize -ads -selected_syn_tool_opt 2
dev_map
pnr -early_block_placement Greedy -auto_dispose_macro false
report_timing
gen_bit_stream -scbv {High Voltage}
