`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/29 20:43:57
// Design Name: 
// Module Name: decoder
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


module decoder(
input [31:0] inst,
//R 指令
output [4:0]rs1,
output [4:0]rs2,
output [4:0]rd,
output wire  SLLI,
output wire  SRLI,
output wire  SRAI,
output wire  ADD,
output wire  SUB,
output wire  SLL,
output wire  SLT,
output wire  SLTU,
output wire  XOR,
output wire  SRL,
output wire  SRA,
output wire  OR,
output wire  AND,
//I 指令
output wire  JALR,
output wire  LB,
output wire  LH,
output wire  LW,
output wire  LBU,
output wire  LHU,
output wire  ADDI,
output wire  SLTI,
output wire  SLTIU,
output wire  XORI,
output wire  ORI,
output wire  ANDI,
//S 指令
output wire  SB,
output wire  SH,
output wire  SW,
//B 指令
output wire  BEQ,
output wire  BNE,
output wire  BLT,
output wire  BGE,
output wire  BLTU,
output wire  BGEU,
//U 指令
output wire  LUI,
output wire  AUIPC,

//J 指令
output wire  JAL,
//
output wire  FENCE,
output wire  FENCETSO,
output wire  PAUSE,
output wire  ECALL,
output wire  EBREAK,
output wire [31:0] imm_i,
output wire [31:0] imm_s,
output wire [31:0] imm_b,
output wire [31:0] imm_u,
output wire [31:0] imm_j
    );
//R指令控制信号
assign SLLI=(inst[31:25]==7'b0000000&&inst[14:12]==3'b001&&inst[6:0]==7'b0010011)?1:0;
assign SRLI=(inst[31:25]==7'b0000000&&inst[14:12]==3'b101&&inst[6:0]==7'b0010011)?1:0;
assign SRAI=(inst[31:25]==7'b0100000&&inst[14:12]==3'b101&&inst[6:0]==7'b0010011)?1:0;
assign ADD=(inst[31:25]==7'b0000000&&inst[14:12]==3'b000&&inst[6:0]==7'b0110011)?1:0;
assign SUB=(inst[31:25]==7'b0100000&&inst[14:12]==3'b000&&inst[6:0]==7'b0110011)?1:0;
assign SLL=(inst[31:25]==7'b0000000&&inst[14:12]==3'b001&&inst[6:0]==7'b0110011)?1:0;
assign SLT=(inst[31:25]==7'b0000000&&inst[14:12]==3'b010&&inst[6:0]==7'b0110011)?1:0;
assign SLTU=(inst[31:25]==7'b0000000&&inst[14:12]==3'b011&&inst[6:0]==7'b0110011)?1:0;
assign XOR=(inst[31:25]==7'b0000000&&inst[14:12]==3'b100&&inst[6:0]==7'b0110011)?1:0;
assign SRL=(inst[31:25]==7'b0000000&&inst[14:12]==3'b101&&inst[6:0]==7'b0110011)?1:0;
assign SRA=(inst[31:25]==7'b0100000&&inst[14:12]==3'b101&&inst[6:0]==7'b0110011)?1:0;
assign OR=(inst[31:25]==7'b0000000&&inst[14:12]==3'b110&&inst[6:0]==7'b0110011)?1:0;
assign AND=(inst[31:25]==7'b0000000&&inst[14:12]==3'b111&&inst[6:0]==7'b0110011)?1:0;
//I指令控制信号
assign JALR=(inst[14:12]==3'b000&&inst[6:0]==7'b1100111)?1:0;
assign LB=(inst[14:12]==3'b000&&inst[6:0]==7'b0000011)?1:0;
assign LH=(inst[14:12]==3'b001&&inst[6:0]==7'b0000011)?1:0;
assign LW=(inst[14:12]==3'b010&&inst[6:0]==7'b0000011)?1:0;
assign LBU=(inst[14:12]==3'b100&&inst[6:0]==7'b0000011)?1:0;
assign LHU=(inst[14:12]==3'b101&&inst[6:0]==7'b0000011)?1:0;
assign ADDI=(inst[14:12]==3'b000&&inst[6:0]==7'b0010011)?1:0;
assign SLTI=(inst[14:12]==3'b010&&inst[6:0]==7'b0010011)?1:0;
assign SLTIU=(inst[14:12]==3'b011&&inst[6:0]==7'b0010011)?1:0;
assign XORI=(inst[14:12]==3'b100&&inst[6:0]==7'b0010011)?1:0;
assign ORI=(inst[14:12]==3'b110&&inst[6:0]==7'b0010011)?1:0;
assign ANDI=(inst[14:12]==3'b111&&inst[6:0]==7'b0010011)?1:0;
//S指令控制信号
assign SB=(inst[14:12]==3'b000&&inst[6:0]==7'b0100011)?1:0;
assign SH=(inst[14:12]==3'b001&&inst[6:0]==7'b0100011)?1:0;
assign SW=(inst[14:12]==3'b010&&inst[6:0]==7'b0100011)?1:0;
//B指令控制信号
assign BEQ=(inst[14:12]==3'b000&&inst[6:0]==7'b1100011)?1:0;
assign BNE=(inst[14:12]==3'b001&&inst[6:0]==7'b1100011)?1:0;
assign BLT=(inst[14:12]==3'b100&&inst[6:0]==7'b1100011)?1:0;
assign BGE=(inst[14:12]==3'b101&&inst[6:0]==7'b1100011)?1:0;
assign BLTU=(inst[14:12]==3'b110&&inst[6:0]==7'b1100011)?1:0;
assign BGEU=(inst[14:12]==3'b111&&inst[6:0]==7'b1100011)?1:0;
//U指令控制信号
assign LUI=(inst[6:0]==7'b0110111)?1:0;
assign AUIPC=(inst[6:0]==7'b0010111)?1:0;
//J指令控制信号
assign JAL=(inst[6:0]==7'b1101111)?1:0;
// Memory ordering / environment instructions
assign FENCE=(inst[14:12]==3'b000 && inst[6:0]==7'b0001111) && !FENCETSO && !PAUSE;
assign FENCETSO=(inst[31:0]==32'b10000011001100000000000000001111)?1:0;
assign PAUSE=(inst[31:0]==32'b00000001000000000000000000001111)?1:0;
assign ECALL=(inst[31:0]==32'b00000000000000000000000001110011)?1:0;
assign EBREAK=(inst[31:0]==32'b00000000000100000000000001110011)?1:0;
//
assign rd=(LUI|| AUIPC||JAL||JALR||LB||LH||LW||LBU||LHU||ADDI||SLTI||SLTIU||XORI||ORI||ANDI||SLLI||SRLI||SRAI||ADD||SUB||SLL||SLT||SLTU||XOR||SRL||SRA||OR||AND )?inst[11:7]:5'b00000;
assign rs1=(JALR||BEQ||BNE||BLT||BGE||BLTU||BGEU||LB||LH||LW||LBU||LHU||SB||SH||SW||ADDI||SLTI||SLTIU||XORI||ORI||ANDI||SLLI||SRLI||SRAI||ADD||SUB||SLL||SLT||SLTU||XOR||SRL||SRA||OR||AND )?inst[19:15]:5'b00000;
assign rs2=(BEQ||BNE||BLT||BGE||BLTU||BGEU||SB||SH||SW||ADD||SUB||SLL||SLT||SLTU||XOR||SRL||SRA||OR||AND)?inst[24:20]:5'b00000;

//立即数处理
// I 型：ADDI、ANDI、LW、JALR 等
assign imm_i = {{20{inst[31]}}, inst[31:20]};

// S 型：SB、SH、SW
assign imm_s = {{20{inst[31]}},inst[31:25],inst[11:7]};

// B 型：条件分支
assign imm_b = {{19{inst[31]}},inst[31],inst[7], inst[30:25],inst[11:8],1'b0};

// U 型：LUI、AUIPC
assign imm_u = { inst[31:12],12'b0};

// J 型：JAL
assign imm_j = {{11{inst[31]}}, inst[31],inst[19:12],inst[20],inst[30:21],1'b0};

endmodule

