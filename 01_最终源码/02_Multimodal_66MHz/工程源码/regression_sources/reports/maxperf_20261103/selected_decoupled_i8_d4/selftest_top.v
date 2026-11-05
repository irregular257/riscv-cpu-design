`timescale 1ns / 1ps
// Built-in RV32I self-test and UART echo, without external memory files.
module selftest_top #(
    parameter integer CLK_HZ = 27000000,
    parameter integer BAUD = 115200
)(
    input wire sys_clk,
    input wire rst_n,
    input wire uart_rx,
    output wire uart_tx,
    output wire [3:0] led
);
    fpga_top #(
        .CLK_HZ(CLK_HZ), .BAUD(BAUD), .SYNC_MEMORY(1),
        .BRANCH_PREDICT(1), .BTB_INDEX_BITS(4), .AI_ENABLE(0),
        .IF_PREFETCH(1), .ROM_ADDR_WIDTH(12), .RAM_ADDR_WIDTH(10),
        .ROM_FILE(""), .RAM_FILE("")
    ) u_board (
        .clk(sys_clk), .rst_n(rst_n), .uart_rx(uart_rx),
        .uart_tx(uart_tx), .led(led)
    );
endmodule
