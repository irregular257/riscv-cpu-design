`timescale 1ns / 1ps
// One outstanding completion transaction. Read data is clocked, suitable for BRAM.
// Stores happen ONLY on the completion edge: valid && ready && !error.
module ram_sync #(parameter integer ADDR_WIDTH=10, parameter INIT_FILE="", parameter integer HAS_DMA=0)(
    input wire clk, rst, req_valid, req_write,
    input wire [31:0] req_addr, req_wdata,
    input wire [2:0] req_funct3,
    output wire ready, error, busy,
    output reg [31:0] rdata,
    input wire dma_en,
    input wire [31:0] dma_addr,
    output wire [31:0] dma_rdata
);
    localparam integer WORD_COUNT=1 << (ADDR_WIDTH-2);
    (* ram_style="block" *) reg [31:0] mem [0:WORD_COUNT-1];
    initial if(INIT_FILE!="")$readmemh(INIT_FILE,mem);
    generate if(HAS_DMA)begin:g_dma
        reg [31:0] read_word_dma;
        always @(posedge clk) if(dma_en && !rst)
            read_word_dma<=mem[dma_addr[ADDR_WIDTH-1:2]];
        assign dma_rdata=read_word_dma;
    end else begin:g_no_dma
        assign dma_rdata=0;
    end endgenerate
    reg pending, saved_error;
    reg [1:0] saved_offset;
    reg [2:0] saved_funct3;
    reg [31:0] read_word;
    wire [ADDR_WIDTH-3:0] index=req_addr[ADDR_WIDTH-1:2];
    wire bad_size=req_write ? (req_funct3>2) :
        !(req_funct3==0 || req_funct3==1 || req_funct3==2 || req_funct3==4 || req_funct3==5);
    wire bad_align=((req_funct3[1:0]==1) && req_addr[0]) ||
        ((req_funct3[1:0]==2) && req_addr[1:0]!=0);
    wire bad_access=bad_size || bad_align || req_addr[31:ADDR_WIDTH]!=0;
    wire launch=req_valid && !pending && !rst;
    assign ready=pending && !rst;
    assign busy=pending && !rst;
    assign error=req_valid && ready && saved_error;
    wire complete=req_valid && ready;
    wire [4:0] shift={req_addr[1:0],3'b000};
    wire [31:0] aligned_write=req_wdata << shift;
    wire [3:0] strobe=req_funct3==0 ? (4'b0001 << req_addr[1:0]) :
        req_funct3==1 ? (4'b0011 << req_addr[1:0]) : 4'b1111;
    // Do not reset the memory array or read register; reset only protocol state.
    // Both accesses use the same word address: a single read/write BRAM port.
    always @(posedge clk) begin
        if (launch && !req_write && !bad_access) read_word<=mem[index];
        if (complete && req_write && !saved_error) begin
            if(strobe[0]) mem[index][7:0]<=aligned_write[7:0];
            if(strobe[1]) mem[index][15:8]<=aligned_write[15:8];
            if(strobe[2]) mem[index][23:16]<=aligned_write[23:16];
            if(strobe[3]) mem[index][31:24]<=aligned_write[31:24];
        end
    end
    always @(posedge clk) begin
        if(rst) begin pending<=0; saved_error<=0; saved_offset<=0; saved_funct3<=0; end
        else begin
            if(complete) pending<=0;
            if(launch) begin
                pending<=1; saved_error<=bad_access;
                saved_offset<=req_addr[1:0]; saved_funct3<=req_funct3;
            end
        end
    end
    wire [31:0] shifted_read=read_word >> {saved_offset,3'b000};
    always @(*) begin
        rdata=0;
        if(ready && !saved_error && !req_write) begin
            case(saved_funct3)
                0: rdata={{24{shifted_read[7]}},shifted_read[7:0]};
                1: rdata={{16{shifted_read[15]}},shifted_read[15:0]};
                2: rdata=read_word;
                4: rdata={24'b0,shifted_read[7:0]};
                5: rdata={16'b0,shifted_read[15:0]};
                default: rdata=0;
            endcase
        end
    end
endmodule
