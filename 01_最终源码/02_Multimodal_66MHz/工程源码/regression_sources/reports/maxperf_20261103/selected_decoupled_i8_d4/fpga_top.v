`timescale 1ns / 1ps
module fpga_top #(
    parameter integer CLK_HZ=50000000,
    parameter integer BAUD=115200,
    parameter integer SYNC_MEMORY=0,
    parameter integer BRANCH_PREDICT=0,
    parameter integer BTB_INDEX_BITS=4,
    parameter integer AI_ENABLE=0,
    parameter integer IF_PREFETCH=0,
    parameter ROM_FILE="",
    parameter integer ROM_ADDR_WIDTH=12,
    parameter integer RAM_ADDR_WIDTH=10,
    parameter RAM_FILE=""
)(
    input wire clk,
    input wire rst_n,
    input wire uart_rx,
    output wire uart_tx,
    output wire [3:0] led
);
    // Asynchronous assertion, synchronous deassertion. Board reset is active low.
    (* ASYNC_REG = "TRUE" *) reg [2:0] reset_sync;
    initial reset_sync=3'b111;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) reset_sync<=3'b111;
        else reset_sync<={reset_sync[1:0],1'b0};
    end
    wire rst=reset_sync[2];
    wire halted;
    wire [31:0] test_result;
    reg [25:0] heartbeat;
    always @(posedge clk) begin
        if(rst) heartbeat<=0;
        else heartbeat<=heartbeat+1'b1;
    end
    assign led[0]=(test_result==1);
    assign led[1]=halted;
    assign led[2]=heartbeat[25];
    assign led[3]=test_result[8];
    cpu_top #(.CLK_HZ(CLK_HZ),.BAUD(BAUD),.SYNC_MEMORY(SYNC_MEMORY),.IF_PREFETCH(IF_PREFETCH),.BRANCH_PREDICT(BRANCH_PREDICT),.BTB_INDEX_BITS(BTB_INDEX_BITS),.AI_ENABLE(AI_ENABLE),.ROM_FILE(ROM_FILE),.ROM_ADDR_WIDTH(ROM_ADDR_WIDTH),.RAM_ADDR_WIDTH(RAM_ADDR_WIDTH),.RAM_FILE(RAM_FILE)) u_cpu (
        .clk(clk),.rst(rst),.uart_rx(uart_rx),.uart_tx(uart_tx),
        .halted(halted),.test_result(test_result),
        .debug_pc(),.trap_cause(),.trap_pc(),.trap_value(),
        .commit_valid(),.commit_pc(),.commit_inst(),.commit_rd_we(),
        .commit_rd(),.commit_wdata()
    );
endmodule
