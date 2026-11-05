`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/30 18:53:38
// Design Name: 
// Module Name: ram
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




module ram #(
    parameter integer ADDR_WIDTH = 10
)(
    input wire clk,
    input wire rst,

    // ALU计算出的字节地址
    input wire [31:0] mem_addr,

    // 寄存器堆第二读口：Store的数据来源
    input wire [31:0] rdata2,

    // Load指令
    input wire LB,
    input wire LH,
    input wire LW,
    input wire LBU,
    input wire LHU,

    // Store指令
    input wire SB,
    input wire SH,
    input wire SW,

    // 已完成字节选择和扩展的Load结果
    output reg [31:0] ram_rdata,

    // 地址越界或未对齐
    output wire mem_error
);

    // 每个数组元素32位，占4字节
    localparam integer WORD_COUNT =
        (1 << (ADDR_WIDTH - 2));

    reg [31:0] mem [0:WORD_COUNT-1];

    // 当前是否为访存指令
    wire is_load;
    wire is_store;
    wire active;

    assign is_load =LB || LH || LW || LBU || LHU;

    assign is_store =SB || SH || SW;

    assign active = is_load || is_store;

    // 字节地址拆成：字下标 + 字内字节偏移
    wire [ADDR_WIDTH-3:0] word_index;
    wire [1:0] byte_offset;
    wire [4:0] shift_bits;

    assign word_index =mem_addr[ADDR_WIDTH-1:2];
    assign byte_offset = mem_addr[1:0];

    // 字节偏移乘8，转换成比特移位量
    assign shift_bits = {byte_offset, 3'b000};

    // 检查地址范围
    wire range_ok;
    assign range_ok =
        (mem_addr[31:ADDR_WIDTH] == 0);

    // 检查自然对齐
    wire misaligned;
    assign misaligned =((LH || LHU || SH) && mem_addr[0]) || ((LW || SW) && (mem_addr[1:0] != 2'b00));
    assign mem_error =!rst && active && (!range_ok || misaligned);

    // 组合读

    reg  [31:0] read_word;
    wire [31:0] shifted_read;
    // 将目标字节/半字移动到最低位
    assign shifted_read = read_word >> shift_bits;
    always @(*) begin
        read_word = 32'b0;
        if (!rst && is_load && !mem_error)
            read_word = mem[word_index];
    end
    always @(*) begin
        ram_rdata = 32'b0;
        if (!rst && is_load && !mem_error) begin
            if (LB)
                ram_rdata ={{24{shifted_read[7]}},shifted_read[7:0]};
            else if (LH)
                ram_rdata ={{16{shifted_read[15]}},shifted_read[15:0]};
            else if (LW)
                ram_rdata = read_word;
            else if (LBU)
                ram_rdata ={24'b0, shifted_read[7:0]};
            else if (LHU)
                ram_rdata ={16'b0, shifted_read[15:0]};
        end
    end
    
    // 生成字节写使能

    reg  [3:0] write_strobe;
    wire [31:0] shifted_write;

    // 将源寄存器低位的数据移到目标字节位置
    assign shifted_write = rdata2 << shift_bits;

    always @(*) begin
        write_strobe = 4'b0000;

        if (!rst && is_store && !mem_error) begin
            if (SB)
                write_strobe =4'b0001 << byte_offset;
            else if (SH)
                write_strobe =4'b0011 << byte_offset;
            else if (SW)
                write_strobe = 4'b1111;
        end
    end
    // 时钟上升沿写入
    always @(posedge clk) begin
        if (write_strobe[0])
            mem[word_index][7:0]<= shifted_write[7:0];
        if (write_strobe[1])
            mem[word_index][15:8]<= shifted_write[15:8];
        if (write_strobe[2])
            mem[word_index][23:16]<= shifted_write[23:16];
        if (write_strobe[3])
            mem[word_index][31:24]<= shifted_write[31:24];
    end

endmodule

