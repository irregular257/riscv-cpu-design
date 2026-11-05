`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/29 20:40:11
// Design Name: 
// Module Name: pc_reg
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////

module pc_reg (
    input  wire  clk,
    input  wire  rst,

    // 来自流水线 EX 的有效重定向
    input  wire   branch_taken,
    input  wire [31:0] branch_target,

    // 来自核心的停机/异常与取指停顿控制
    input  wire   mem_error,
    input  wire   stall, // hold fetch PC; cpu_core releases stall for a valid redirect

    output reg  [31:0] pc
);

    always @(posedge clk) begin
        // 高电平同步复位
        if (rst)
            pc <= 32'd0;

        // 访存出错：暂时停在当前指令
        else if (mem_error || stall)
            pc <= pc;

        // 条件分支成立，或者执行JAL/JALR
        else if (branch_taken)
            pc <= branch_target;

        // 普通顺序执行
        else
            pc <= pc + 32'd4;
    end

endmodule

