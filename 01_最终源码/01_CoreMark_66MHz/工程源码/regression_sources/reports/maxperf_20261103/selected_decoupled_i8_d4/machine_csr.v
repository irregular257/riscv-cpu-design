`timescale 1ns/1ps
// RV32, M-mode-only CSR bank. No U/S privilege, delegation, PMP, debug or NMI.
// Trap entry/MRET and explicit CSR writes are driven at an instruction boundary.
module machine_csr (
    input wire clk, rst,
    input wire [11:0] read_addr,
    input wire read_write_intent,
    output reg [31:0] read_data,
    output wire illegal,
    input wire write_valid,
    input wire [11:0] write_addr,
    input wire [31:0] write_data,
    input wire retire,
    input wire irq_timer, irq_software, irq_external,
    output wire irq_pending,
    output wire [31:0] irq_cause,
    output wire wake_pending,
    input wire trap_enter,
    input wire [31:0] entry_pc, entry_cause, entry_value,
    input wire mret,
    output wire [31:0] trap_vector,
    output wire [31:0] return_pc
);
    reg mie_global, mpie;
    reg [31:0] mie, mtvec, mscratch, mepc, mcause, mtval;
    reg msip_software;
    reg [2:0] mcountinhibit;
    reg [63:0] mcycle, minstret;
    wire sw_pending=(irq_software === 1'b1) || msip_software;
    wire timer_pending=(irq_timer === 1'b1);
    wire ext_pending=(irq_external === 1'b1);
    wire [31:0] mip_value={20'b0,ext_pending,3'b0,timer_pending,3'b0,sw_pending,3'b0};
    wire [31:0] enabled_pending=mie & mip_value;
    assign wake_pending=|enabled_pending;
    assign irq_pending=mie_global && wake_pending;
    // Standard machine external > machine software > machine timer priority.
    assign irq_cause=enabled_pending[11] ? 32'h8000000b :
                     enabled_pending[3] ? 32'h80000003 : 32'h80000007;
    assign trap_vector={mtvec[31:2],2'b00} +
        ((entry_cause[31] && mtvec[1:0]==1) ? {entry_cause[29:0],2'b00} : 32'd0);
    assign return_pc=mepc;
    reg supported;
    wire read_only=read_addr[11:10]==2'b11;
    assign illegal=!supported || (read_only && read_write_intent);
    always @(*) begin
        supported=1;
        read_data=0;
        case(read_addr)
          12'h300: read_data={19'd0,2'b11,3'd0,mpie,3'd0,mie_global,3'd0};
          12'h301: read_data=32'h40000100; // RV32I; Zicsr/Zifencei have no misa bits.
          12'h304: read_data=mie;
          12'h305: read_data=mtvec;
          12'h306: read_data=0; // mcounteren WARL zero: no lower privilege modes.
          12'h310: read_data=0; // mstatush WARL zero: no big endian extension.
          12'h320: read_data={29'd0,mcountinhibit};
          12'h340: read_data=mscratch;
          12'h341: read_data=mepc;
          12'h342: read_data=mcause;
          12'h343: read_data=mtval;
          12'h344: read_data=mip_value;
          12'hb00,12'hc00: read_data=mcycle[31:0];
          12'hb80,12'hc80: read_data=mcycle[63:32];
          12'hb02,12'hc02: read_data=minstret[31:0];
          12'hb82,12'hc82: read_data=minstret[63:32];
          12'hf11,12'hf12,12'hf13,12'hf14,12'hf15: read_data=0;
          default: begin supported=0; read_data=0; end
        endcase
    end
    always @(posedge clk) begin
        if(rst) begin
            mie_global<=0; mpie<=0; mie<=0; mtvec<=0;
            mscratch<=0; mepc<=0; mcause<=0; mtval<=0;
            msip_software<=0; mcountinhibit<=0; mcycle<=0; minstret<=0;
        end else begin
            if(!mcountinhibit[0]) mcycle<=mcycle+64'd1;
            if(retire && !mcountinhibit[2]) minstret<=minstret+64'd1;
            // Explicit writes take precedence over automatic counter increments.
            // Preserve the other half even if an increment would carry into it.
            if(write_valid) begin
                case(write_addr)
                  12'h300: begin mie_global<=write_data[3]; mpie<=write_data[7]; end
                  12'h304: mie<=write_data & 32'h00000888;
                  12'h305: mtvec<={write_data[31:2],1'b0,write_data[1:0]==1};
                  12'h320: mcountinhibit<=write_data[2:0] & 3'b101;
                  12'h340: mscratch<=write_data;
                  12'h341: mepc<={write_data[31:2],2'b00};
                  12'h342: mcause<=write_data;
                  12'h343: mtval<=write_data;
                  12'h344: msip_software<=write_data[3];
                  12'hb00: mcycle<={mcycle[63:32],write_data};
                  12'hb80: mcycle<={write_data,mcycle[31:0]};
                  12'hb02: minstret<={minstret[63:32],write_data};
                  12'hb82: minstret<={write_data,minstret[31:0]};
                  default: begin end // Supported WARL constant CSRs ignore writes.
                endcase
            end
            if(trap_enter) begin
                mpie<=mie_global; mie_global<=0;
                mepc<={entry_pc[31:2],2'b00}; mcause<=entry_cause; mtval<=entry_value;
            end else if(mret) begin mie_global<=mpie; mpie<=1; end
        end
    end
endmodule
