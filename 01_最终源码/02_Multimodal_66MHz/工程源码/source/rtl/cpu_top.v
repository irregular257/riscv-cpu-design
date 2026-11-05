`timescale 1ns / 1ps
// Complete SoC for simulation/integration: five-stage core + ROM + existing RAM + UART MMIO.
// For physical board pins use fpga_top, which synchronizes external reset.
module cpu_top #(
    parameter integer CLK_HZ=50000000,
    parameter integer BAUD=115200,
    parameter ROM_FILE="",
    parameter integer SYNC_MEMORY=0,
    parameter integer BRANCH_PREDICT=0,
    parameter integer BTB_INDEX_BITS=4,
    parameter integer AI_ENABLE=0,
    parameter integer IF_PREFETCH=0,
    parameter integer ROM_ADDR_WIDTH=12,
    parameter integer RAM_ADDR_WIDTH=10,
    parameter RAM_FILE=""
)(
    input wire clk, rst,
    input wire uart_rx,
    output wire uart_tx,
    output wire halted,
    output wire [31:0] debug_pc, test_result,
    output wire [31:0] trap_cause, trap_pc, trap_value,
    output wire commit_valid,
    output wire [31:0] commit_pc, commit_inst,
    output wire commit_rd_we,
    output wire [4:0] commit_rd,
    output wire [31:0] commit_wdata
);
    wire imem_valid,imem_ready,imem_error;
    wire [31:0] imem_addr,imem_rdata;
    wire dmem_valid,dmem_write,dmem_ready,dmem_error;
    wire [31:0] dmem_addr,dmem_wdata,dmem_rdata;
    wire [2:0] dmem_funct3;
    wire fence_valid,fence_ready;
    wire redirect_valid;
    wire [31:0] redirect_target;
    cpu_core #(.BRANCH_PREDICT(BRANCH_PREDICT),.BTB_INDEX_BITS(BTB_INDEX_BITS)) u_core (
        .clk(clk),.rst(rst),
        .imem_valid(imem_valid),.imem_addr(imem_addr),.imem_ready(imem_ready),
        .imem_rdata(imem_rdata),.imem_error(imem_error),
        .dmem_valid(dmem_valid),.dmem_write(dmem_write),.dmem_addr(dmem_addr),
        .dmem_wdata(dmem_wdata),.dmem_funct3(dmem_funct3),.dmem_ready(dmem_ready),
        .dmem_rdata(dmem_rdata),.dmem_error(dmem_error),
        .fence_valid(fence_valid),.fence_ready(fence_ready),
        .halted(halted),.trap_cause(trap_cause),.trap_pc(trap_pc),.trap_value(trap_value),
        .debug_pc(debug_pc),.commit_valid(commit_valid),.commit_pc(commit_pc),
        .commit_inst(commit_inst),.commit_rd_we(commit_rd_we),
        .commit_rd(commit_rd),.commit_wdata(commit_wdata),
        .redirect_valid(redirect_valid),.redirect_target(redirect_target)
    );
    generate if(SYNC_MEMORY) begin: g_sync_rom
        rom_sync #(.INIT_FILE(ROM_FILE),.ADDR_WIDTH(ROM_ADDR_WIDTH),.PREFETCH(IF_PREFETCH)) u_rom (
            .clk(clk),.rst(rst),.req_valid(imem_valid),.addr(imem_addr),
            .ready(imem_ready),.inst(imem_rdata),.error(imem_error)
        );
    end else begin: g_async_rom
        rom #(.INIT_FILE(ROM_FILE)) u_rom (
            .req_valid(imem_valid),.addr(imem_addr),.ready(imem_ready),
            .inst(imem_rdata),.error(imem_error)
        );
    end endgenerate
    data_bus #(.CLK_HZ(CLK_HZ),.BAUD(BAUD),.SYNC_RAM(SYNC_MEMORY),.AI_ENABLE(AI_ENABLE),.RAM_ADDR_WIDTH(RAM_ADDR_WIDTH),.RAM_FILE(RAM_FILE)) u_bus (
        .clk(clk),.rst(rst),.req_valid(dmem_valid),.req_write(dmem_write),
        .req_addr(dmem_addr),.req_wdata(dmem_wdata),.req_funct3(dmem_funct3),
        .req_ready(dmem_ready),.rsp_rdata(dmem_rdata),.rsp_error(dmem_error),
        .fence_ready(fence_ready),.uart_rx_i(uart_rx),.uart_tx_o(uart_tx),
        .test_result(test_result)
    );
endmodule
