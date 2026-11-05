`timescale 1ns/1ps
// Board ports intentionally match the successful remote selftest constraints.
module advanced_top #(
    parameter integer CLK_HZ=27000000,
    parameter integer BAUD=115200,
    parameter ROM_FILE="software/runtime/images/advanced_rom.hex",
    parameter RAM_FILE="software/runtime/images/advanced_ram.hex"
)(input wire sys_clk,rst_n,uart_rx,output wire uart_tx,output wire[3:0]led);
    reg[7:0]por=8'hff;
    always @(posedge sys_clk or negedge rst_n)
      if(!rst_n)por<=8'hff;else por<={por[6:0],1'b0};
    wire rst=por[7];
    wire halted;wire[31:0]result,gpo,goe;
    reg[25:0]heartbeat;
    always @(posedge sys_clk)if(rst)heartbeat<=0;else heartbeat<=heartbeat+1'b1;
    wire[3:0]status={result[8],heartbeat[25],halted,result==1};
    assign led=(status & ~goe[3:0]) | (gpo[3:0]&goe[3:0]);
    advanced_soc #(.CLK_HZ(CLK_HZ),.BAUD(BAUD),.ROM_FILE(ROM_FILE),.RAM_FILE(RAM_FILE)) u_soc(
      .clk(sys_clk),.rst(rst),.uart_rx(uart_rx),.uart_tx(uart_tx),.gpio_in(32'b0),.gpio_out(gpo),.gpio_oe(goe),
      .halted(halted),.test_result(result),.debug_pc(),.trap_cause(),.trap_pc(),.trap_value(),
      .commit_valid(),.commit_pc(),.commit_inst(),.commit_rd_we(),.commit_rd(),.commit_wdata());
endmodule
