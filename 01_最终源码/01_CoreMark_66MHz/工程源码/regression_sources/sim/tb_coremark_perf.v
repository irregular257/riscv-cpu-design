`timescale 1ns/1ps
// Passive observer of the actual advanced SoC. No bypass of either cache,
// memory handshake or UART write completion is introduced by this wrapper.
module tb_coremark_perf #(
    parameter ROM_FILE="", RAM_FILE="", parameter integer CPU_HZ=27000000
)(input wire clk,rst,
  output wire halted,commit,if_wait,mem_wait,load_stall,branch,redirect,trap,
  output wire uart_write,marker_write,
  output wire [31:0] write_data,debug_pc,trap_cause,trap_pc,
  output wire [31:0] ihit,imiss,ifill,dhit,dmiss,dfill,dwrites);
  wire tx;
  advanced_soc #(.CLK_HZ(CPU_HZ),.BAUD(CPU_HZ/10),.ROM_FILE(ROM_FILE),.RAM_FILE(RAM_FILE)) dut(
    .clk(clk),.rst(rst),.uart_rx(1'b1),.uart_tx(tx),.gpio_in(32'b0),.gpio_out(),.gpio_oe(),
    .halted(halted),.test_result(),.debug_pc(debug_pc),.trap_cause(trap_cause),.trap_pc(trap_pc),.trap_value(),
    .commit_valid(commit),.commit_pc(),.commit_inst(),.commit_rd_we(),.commit_rd(),.commit_wdata());
  assign uart_write=dut.bus_valid && dut.bus_ready && !dut.bus_error && dut.bus_write && dut.bus_addr==32'h10000000;
  assign marker_write=dut.bus_valid && dut.bus_ready && !dut.bus_error && dut.bus_write && dut.bus_addr==32'h1000000c;
  assign write_data=dut.bus_wdata;
  assign if_wait=dut.iv && !dut.ir;
  assign mem_wait=dut.u_core.mem_valid && !dut.u_core.mem_finish;
  assign load_stall=dut.u_core.load_use_stall && dut.u_core.ex_allow;
  assign branch=dut.u_core.ex_fire && !dut.u_core.ex_exc && dut.u_core.ex_control;
  assign redirect=dut.u_core.branch_redirect;
  assign trap=dut.u_core.trap_fire;
  assign ihit=dut.ihit;assign imiss=dut.imiss;assign ifill=dut.ifill;
  assign dhit=dut.dhit;assign dmiss=dut.dmiss;assign dfill=dut.dfill;assign dwrites=dut.dwrites;
endmodule
