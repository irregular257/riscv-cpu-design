`timescale 1ns / 1ps
// Portable INT8 AI wrapper. The oscillator frequency MUST match CLK_HZ.
// There is no PLL, clock divider, or board-specific pin assignment here.
module remote_top #(
    parameter integer CLK_HZ = 27000000,
    parameter integer BAUD = 115200,
    parameter ROM_FILE = "software/ai/images/digits_rom.hex",
    parameter RAM_FILE = "software/ai/images/digits_ram.hex"
)(
    input wire sys_clk,
    input wire rst_n,
    input wire uart_rx,
    output wire uart_tx,
    output wire [3:0] led
);
    fpga_top #(
        .CLK_HZ(CLK_HZ), .BAUD(BAUD), .SYNC_MEMORY(1),
        .BRANCH_PREDICT(1), .BTB_INDEX_BITS(4), .AI_ENABLE(1),
        .IF_PREFETCH(1), .ROM_ADDR_WIDTH(16), .RAM_ADDR_WIDTH(16),
        .ROM_FILE(ROM_FILE), .RAM_FILE(RAM_FILE)
    ) u_board (
        .clk(sys_clk), .rst_n(rst_n), .uart_rx(uart_rx),
        .uart_tx(uart_tx), .led(led)
    );
endmodule
