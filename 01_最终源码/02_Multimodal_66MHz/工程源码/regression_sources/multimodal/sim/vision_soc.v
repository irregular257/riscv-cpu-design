`timescale 1ns/1ps
// Observation only: executes the actual multimodal CPU/cache/DMA/UART design.
// DDR starts unavailable. Optional sparse behavioral memory tests only the
// CPU-side loader: it is not the vendor DDR controller, PHY or physical timing.
module vision_soc #(
    parameter integer CLK_HZ=1000000, BAUD=10000,
    parameter ROM_FILE="multimodal/reports/sim_images/advanced_rom.hex",
    parameter RAM_FILE="multimodal/reports/sim_images/advanced_ram.hex"
)(
    input wire clk,rst,rx,ddr_enable,ddr_corrupt_reads,
    output wire tx,halted,
    output wire [31:0] test_result,debug_pc,trap_cause,trap_pc,
    output reg [31:0] retired,dot_starts,external_requests,
    output wire rx_overrun,
    output wire [4:0] rx_fifo_count,
    output wire [4:0] cfg_glyph,active_glyph,
    output wire [7:0] cfg_brightness,active_brightness,
    output wire display_enabled
);
    wire commit,ev,ew;
    wire [31:0] ea,ed;
    wire [2:0] ef;
    reg [31:0] memory[0:2052];
    integer i;
    function [11:0] sparse_index;
        input [31:0] address;
        begin
            if(address[29:13]==0)sparse_index={1'b0,address[12:2]};
            else case(address[29:0])
            30'h08000000:sparse_index=2048;
            30'h10000000:sparse_index=2049;
            30'h20000000:sparse_index=2050;
            30'h30000000:sparse_index=2051;
            30'h3ffffffc:sparse_index=2052;
            default:sparse_index=4095;
            endcase
        end
    endfunction
    wire [11:0] mi=sparse_index(ea);
    wire bad_size=ew ? ef>2 : !(ef==0 || ef==1 || ef==2 || ef==4 || ef==5);
    wire bad_alignment=(ef[1:0]==1 && ea[0]) || (ef[1:0]==2 && ea[1:0]!=0);
    wire model_error=!ddr_enable || mi==4095 || bad_size || bad_alignment;
    wire [31:0] word_read=(mi==4095 ? 32'b0 : memory[mi]) ^ (ddr_corrupt_reads ? 32'b1 : 32'b0);
    wire [31:0] shifted=word_read >> {ea[1:0],3'b0};
    reg [31:0] model_read;
    always @(*)case(ef)
    0:model_read={{24{shifted[7]}},shifted[7:0]};
    1:model_read={{16{shifted[15]}},shifted[15:0]};
    4:model_read={24'b0,shifted[7:0]};
    5:model_read={16'b0,shifted[15:0]};
    default:model_read=word_read;
    endcase
    wire [31:0] shifted_write=ed << {ea[1:0],3'b0};
    wire [3:0] strobe=ef==0 ? (4'b0001 << ea[1:0]) : ef==1 ? (4'b0011 << ea[1:0]) : 4'b1111;
    always @(posedge clk)begin
        if(rst)begin for(i=0;i<2053;i=i+1)memory[i]<=0;end
        else if(ev && ew && !model_error)begin
            if(strobe[0])memory[mi][7:0]<=shifted_write[7:0];
            if(strobe[1])memory[mi][15:8]<=shifted_write[15:8];
            if(strobe[2])memory[mi][23:16]<=shifted_write[23:16];
            if(strobe[3])memory[mi][31:24]<=shifted_write[31:24];
        end
    end
    multimodal_soc #(.CLK_HZ(CLK_HZ),.BAUD(BAUD),.CACHE_ENABLE(1),.ROM_FILE(ROM_FILE),.RAM_FILE(RAM_FILE)) dut(
      .clk(clk),.rst(rst),.uart_rx(rx),.uart_tx(tx),.gpio_in(32'b0),.gpio_out(),.gpio_oe(),
      .halted(halted),.test_result(test_result),.debug_pc(debug_pc),.trap_cause(trap_cause),.trap_pc(trap_pc),.trap_value(),
      .commit_valid(commit),.commit_pc(),.commit_inst(),.commit_rd_we(),.commit_rd(),.commit_wdata(),
      .DS_Matrix(),.G_Matrix(),.RCK_Matrix(),.SCLK_Matrix(),.SCLR_Matrix(),
      .ext_valid(ev),.ext_write(ew),.ext_addr(ea),.ext_wdata(ed),.ext_funct3(ef),
      .ext_ready(1'b1),.ext_error(model_error),.ext_rdata(model_read),.ddr_status(ddr_enable ? 32'b1 : 32'b0));
    assign rx_overrun=dut.u_bus.rx_overrun;
    assign rx_fifo_count=dut.u_bus.rx_count;
    assign cfg_glyph=dut.u_matrix.cfg_glyph;
    assign active_glyph=dut.u_matrix.active_glyph;
    assign cfg_brightness=dut.u_matrix.cfg_brightness;
    assign active_brightness=dut.u_matrix.active_brightness;
    assign display_enabled=dut.u_matrix.active_enable;
    always @(posedge clk)begin
        if(rst)begin retired<=0;dot_starts<=0;external_requests<=0;end
        else begin
            if(commit)retired<=retired+1'b1;
            if(ev)external_requests<=external_requests+1'b1;
            if(dut.bus_valid && dut.bus_ready && !dut.bus_error && dut.bus_write &&
               dut.bus_addr==32'h10000100 && dut.bus_wdata[0])dot_starts<=dot_starts+1'b1;
        end
    end
endmodule
