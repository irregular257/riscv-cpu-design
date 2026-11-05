`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/29 20:43:15
// Design Name: 
// Module Name: regfile
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




module regfile (
    input  wire        clk,
    input  wire        rst,
    // 第一个读口
    input  wire [4:0]  raddr1,
    output wire [31:0] rdata1,
    // 第二个读口
    input  wire [4:0]  raddr2,
    output wire [31:0] rdata2,
    // 写口
    input  wire        we,
    input  wire [4:0]  waddr,
    input  wire [31:0] wdata
);

    reg [31:0] reg_array [31:0];

    integer i;

    // 同步复位、同步写入
    always @(posedge clk) begin
        if (rst) begin
            for (i = 0; i < 32; i = i + 1)
                reg_array[i] <= 32'd0;
        end
        else if (we && (waddr != 5'd0)) begin
            reg_array[waddr] <= wdata;
        end
    end

    // 第一个组合读口
    assign rdata1 =
        (raddr1 == 5'd0)
        ? 32'd0
        : reg_array[raddr1];

    // 第二个组合读口
    assign rdata2 =
        (raddr2 == 5'd0)
        ? 32'd0
        : reg_array[raddr2];

endmodule
