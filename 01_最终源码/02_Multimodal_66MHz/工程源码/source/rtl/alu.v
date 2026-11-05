`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/29 20:42:37
// Design Name: 
// Module Name: alu
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


module alu(
input [31:0]rdata1,
input [31:0]rdata2,
//R 指令
input wire  SLLI,
input wire  SRLI,
input wire  SRAI,
input wire  ADD,
input wire  SUB,
input wire  SLL,
input wire  SLT,
input wire  SLTU,
input wire  XOR,
input wire  SRL,
input wire  SRA,
input wire  OR,
input wire  AND,
//I 指令
input wire  JALR,
input wire  LB,
input wire  LH,
input wire  LW,
input wire  LBU,
input wire  LHU,
input wire  ADDI,
input wire  SLTI,
input wire  SLTIU,
input wire  XORI,
input wire  ORI,
input wire  ANDI,
//S 指令
input wire  SB,
input wire  SH,
input wire  SW,
//B 指令
input wire  BEQ,
input wire  BNE,
input wire  BLT,
input wire  BGE,
input wire  BLTU,
input wire  BGEU,
//U 指令
input wire  LUI,
input wire  AUIPC,

//J 指令
input wire  JAL,
//
input wire  FENCE,
input wire  FENCETSO,
input wire  PAUSE,
input wire  ECALL,
input wire  EBREAK,
input wire [31:0] imm_i,
input wire [31:0] imm_s,
input wire [31:0] imm_b,
input wire [31:0] imm_u,
input wire [31:0] imm_j,
input  [31:0] pc,

output reg  [31:0] mem_addr,
output reg         branch_taken,
output reg  [31:0] branch_target,
output reg [31:0] wdata
    );
    // Control bits come from the mutually-exclusive instruction decoder.
    wire unsigned_less = rdata1 < rdata2;
    wire signed_less = (rdata1[31] != rdata2[31]) ? rdata1[31] : unsigned_less;
    // Keep EX branch resolution independent of the arithmetic result priority chain.
    always @(*) begin
        branch_taken = JAL || JALR || (BEQ && rdata1==rdata2) ||
            (BNE && rdata1!=rdata2) || (BLT && signed_less) ||
            (BGE && !signed_less) || (BLTU && unsigned_less) || (BGEU && !unsigned_less);
        branch_target = 32'd0;
        if(JALR) branch_target=(rdata1+imm_i) & 32'hffff_fffe;
        else if(JAL) branch_target=pc+imm_j;
        else if(BEQ || BNE || BLT || BGE || BLTU || BGEU) branch_target=pc+imm_b;
    end
    always @(*) begin
        // 默认值：确保所有输出在任何情况下都有赋值
        wdata         = 32'd0;
        mem_addr      = 32'd0;

        
        // 加法
        if (ADD ) begin
            wdata = rdata1 + rdata2;
        end
        //减法
        else if (SUB) begin
            wdata = rdata1 - rdata2;
        end
        //加立即数
        else if (ADDI) begin
            wdata = rdata1 + imm_i;
        end
        
        // 按位与逻辑
        else if (AND ) begin
            wdata = rdata1 & rdata2;
        end
        else if (ANDI ) begin
            wdata = rdata1 & imm_i;
        end
        // 按位或逻辑
        else if (OR ) begin
            wdata = rdata1 | rdata2;
        end
        else if (ORI ) begin
            wdata = rdata1 | imm_i;
        end
        // 按位异或逻辑
        else if (XOR ) begin
            wdata = rdata1 ^ rdata2;
        end
        else if (XORI ) begin
            wdata = rdata1 ^ imm_i;
        end
        
        // 逻辑左移，补0
        else if (SLL ) begin
            wdata = rdata1 << rdata2[4:0];
        end
        else if (SLLI ) begin
            wdata = rdata1 << imm_i[4:0];
        end
        //逻辑右移，补0
        else if (SRL ) begin
            wdata = rdata1 >> rdata2[4:0];
        end
        else if (SRLI ) begin
            wdata = rdata1 >> imm_i[4:0];
        end
        //算术右移，按最高位补
        else if (SRA ) begin
            wdata = $signed(rdata1) >>> rdata2[4:0];
        end
        else if (SRAI) begin
            wdata = $signed(rdata1) >>> imm_i[4:0] ;
        end
       
        // 有符号比较
        else if (SLT ) begin
            wdata =signed_less? 32'd1 : 32'd0;
        end
        else if (SLTI ) begin
            wdata =($signed(rdata1) <$signed(imm_i))? 32'd1 : 32'd0;
        end
        
        // 无符号比较
        else if (SLTU ) begin
            wdata =unsigned_less? 32'd1 : 32'd0;
        end
        else if (SLTIU ) begin
            wdata =(rdata1 < imm_i)? 32'd1 : 32'd0;
        end
        
        // U 型指令
        else if (LUI) begin
            wdata = imm_u;
        end
        else if (AUIPC) begin
            wdata = pc + imm_u;
        end

        // 加载：这里只计算地址
        //LW读32位
        //LB读8位
        //LH读16位
        else if (LB || LH || LW || LBU || LHU) begin
            mem_addr = rdata1 + imm_i;
        end

        // 存储：这里只计算地址
        //SW读32位
        //SB读8位
        //SH读16位
        else if (SB || SH || SW) begin
            mem_addr = rdata1 + imm_s;
        end

        // Both jump instructions write the link address.
        else if (JAL || JALR) begin
            wdata = pc + 32'd4;
        end

        // FENCE、FENCETSO、PAUSE、ECALL、EBREAK
        // 不在本模块中处理，输出保持默认值。
        // 它们的控制行为必须由外部逻辑实现。
    end
endmodule

