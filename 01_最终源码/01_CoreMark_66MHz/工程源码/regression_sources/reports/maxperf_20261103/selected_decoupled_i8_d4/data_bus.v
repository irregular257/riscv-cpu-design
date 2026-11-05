`timescale 1ns / 1ps
// One outstanding completion handshake: side effects only on valid && ready.
// RAM: 0..1023. UART DATA/STATUS/CONTROL: 0x10000000/04/08.
// TEST_RESULT: 0x1000000c (word R/W). See docs for register details.
module data_bus #(
    parameter integer CLK_HZ=50000000,
    parameter integer BAUD=115200,
    parameter integer RAM_ADDR_WIDTH=10,
    parameter integer SYNC_RAM=0,
    parameter integer AI_ENABLE=0,
    parameter integer UART_FIFO_DEPTH=16,
    parameter RAM_FILE=""
)(
    input wire clk, rst,
    input wire req_valid, req_write,
    input wire [31:0] req_addr, req_wdata,
    input wire [2:0] req_funct3,
    output reg req_ready,
    output reg [31:0] rsp_rdata,
    output wire rsp_error,
    output wire fence_ready,
    input wire uart_rx_i,
    output wire uart_tx_o,
    output reg [31:0] test_result,
    output wire irq_uart
);
    wire ram_select = (req_addr[31:RAM_ADDR_WIDTH]==0);
    wire data_select = (req_addr==32'h10000000);
    wire status_select = (req_addr==32'h10000004);
    wire control_select = (req_addr==32'h10000008);
    wire test_select = (req_addr==32'h1000000c);
    wire cycle_lo_select=(req_addr==32'h10000010);
    wire cycle_hi_select=(req_addr==32'h10000014);
    wire cycle_select=cycle_lo_select || cycle_hi_select;
    reg [63:0] cycle_count;
    always @(posedge clk) begin
        if(rst)cycle_count<=0;
        else cycle_count<=cycle_count+64'd1;
    end
    wire ai_select=AI_ENABLE && SYNC_RAM && req_addr>=32'h10000100 && req_addr<=32'h10000118;
    wire ai_busy,ai_error,ai_dma_en;
    wire [31:0] ai_rdata,ai_dma_addr,ai_dma_data;
    generate if(AI_ENABLE && SYNC_RAM)begin:g_ai
        int8_dot #(.ADDR_WIDTH(RAM_ADDR_WIDTH)) u_ai(
            .clk(clk),.rst(rst),.req_valid(req_valid && ai_select && req_funct3==2 && !rst),
            .req_write(req_write),.req_offset(req_addr[4:0]),.req_wdata(req_wdata),
            .rsp_rdata(ai_rdata),.rsp_error(ai_error),.busy(ai_busy),
            .dma_en(ai_dma_en),.dma_addr(ai_dma_addr),.dma_rdata(ai_dma_data));
    end else begin:g_no_ai
        assign ai_busy=0;assign ai_error=0;assign ai_rdata=0;
        assign ai_dma_en=0;assign ai_dma_addr=0;
    end endgenerate
    wire mmio_select = ai_select || data_select || status_select || control_select || test_select || cycle_select;
    wire size_valid = req_write ? (req_funct3<=3'b010) :
        (req_funct3==0 || req_funct3==1 || req_funct3==2 || req_funct3==4 || req_funct3==5);
    wire alignment_error =
        ((req_funct3[1:0]==1) && req_addr[0]) ||
        ((req_funct3[1:0]==2) && (req_addr[1:0]!=0));
    wire mmio_error = ai_error || (ai_select && req_funct3!=2) || mmio_select &&
        ((status_select && req_write) || (control_select && !req_write) ||
         (test_select && req_funct3!=3'b010) ||
         (cycle_select && (req_write || req_funct3!=3'b010)));
    wire bus_error = !size_valid || alignment_error || mmio_error ||
        (!ram_select && !mmio_select);
    wire ram_access = req_valid && ram_select && !bus_error && !rst && !(ai_busy && req_write);
    wire [31:0] ram_rdata, legacy_ram_rdata, sync_ram_rdata;
    wire ram_error, legacy_ram_error, sync_ram_error, sync_ram_ready, ram_busy;
    assign ram_rdata=SYNC_RAM ? sync_ram_rdata : legacy_ram_rdata;
    assign ram_error=SYNC_RAM ? sync_ram_error : legacy_ram_error;
    generate if(SYNC_RAM) begin: g_sync
        ram_sync #(.ADDR_WIDTH(RAM_ADDR_WIDTH),.INIT_FILE(RAM_FILE),.HAS_DMA(AI_ENABLE)) u_ram (
            .clk(clk),.rst(rst),.req_valid(ram_access),.req_write(req_write),
            .req_addr(req_addr),.req_wdata(req_wdata),.req_funct3(req_funct3),
            .ready(sync_ram_ready),.rdata(sync_ram_rdata),.error(sync_ram_error),.busy(ram_busy),
            .dma_en(ai_dma_en),.dma_addr(ai_dma_addr),.dma_rdata(ai_dma_data)
        );
    end else begin: g_no_sync
        assign sync_ram_ready=1'b1; assign sync_ram_rdata=32'b0;
        assign sync_ram_error=1'b0; assign ram_busy=1'b0; assign ai_dma_data=0;
        ram #(.ADDR_WIDTH(RAM_ADDR_WIDTH)) u_ram (
            .clk(clk), .rst(rst), .mem_addr(req_addr), .rdata2(req_wdata),
            .LB (ram_access && !req_write && req_funct3==0),
            .LH (ram_access && !req_write && req_funct3==1),
            .LW (ram_access && !req_write && req_funct3==2),
            .LBU(ram_access && !req_write && req_funct3==4),
            .LHU(ram_access && !req_write && req_funct3==5),
            .SB (ram_access && req_write && req_funct3==0),
            .SH (ram_access && req_write && req_funct3==1),
            .SW (ram_access && req_write && req_funct3==2),
            .ram_rdata(legacy_ram_rdata), .mem_error(legacy_ram_error)
        );
    end endgenerate
    assign rsp_error = req_valid && !rst && (bus_error || ram_error);
    wire tx_busy;
    wire tx_start = req_valid && req_ready && !rsp_error && !rst && req_write && data_select;
    uart_tx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) u_tx (
        .clk(clk),.rst(rst),.start(tx_start),.data(req_wdata[7:0]),
        .tx(uart_tx_o),.busy(tx_busy)
    );
    wire [7:0] rx_byte;
    wire rx_pulse, rx_frame_error;
    uart_rx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) u_rx (
        .clk(clk),.rst(rst),.rx(uart_rx_i),.data(rx_byte),
        .valid(rx_pulse),.frame_error(rx_frame_error)
    );
    localparam integer RX_PW=$clog2(UART_FIFO_DEPTH);
    reg [7:0] rx_fifo[0:UART_FIFO_DEPTH-1];
    reg [RX_PW-1:0] rx_read_ptr,rx_write_ptr;
    reg [RX_PW:0] rx_count;
    wire [7:0] rx_buffer=rx_fifo[rx_read_ptr];
    wire rx_pending=rx_count!=0;
    reg rx_overrun, rx_bad_frame,uart_irq_enable;
    wire rx_pop = req_valid && req_ready && !rsp_error && !rst && !req_write && data_select;
    wire clear_errors = req_valid && req_ready && !rsp_error && !rst && req_write &&
        control_select && req_wdata[0];
    wire control_write=req_valid && req_ready && !rsp_error && !rst && req_write && control_select;
    wire clear_rx=control_write && req_wdata[1];
    wire fifo_pop=rx_pop && rx_pending;
    wire fifo_push=rx_pulse && (rx_count<UART_FIFO_DEPTH || fifo_pop);
    assign irq_uart=uart_irq_enable && rx_pending;
    always @(posedge clk) begin
        if (rst) begin
            rx_read_ptr<=0;rx_write_ptr<=0;rx_count<=0;
            rx_overrun<=0; rx_bad_frame<=0; test_result<=0;uart_irq_enable<=0;
        end else begin
            if (clear_errors) begin rx_overrun<=0; rx_bad_frame<=0; end
            // SB touches only CONTROL[7:0]; preserve the IRQ bit in byte 1.
            if(control_write && req_funct3!=0) uart_irq_enable<=req_wdata[8];
            if(clear_rx) begin
                rx_read_ptr<=0;rx_write_ptr<=0;rx_count<=0;
            end else begin
                if(fifo_pop) rx_read_ptr<=rx_read_ptr+1'b1;
                if(fifo_push) begin rx_fifo[rx_write_ptr]<=rx_byte;rx_write_ptr<=rx_write_ptr+1'b1;end
                case({fifo_push,fifo_pop})
                  2'b10:rx_count<=rx_count+1'b1;
                  2'b01:rx_count<=rx_count-1'b1;
                  default:begin end
                endcase
                if(rx_pulse && !fifo_push) rx_overrun<=1;
            end
            if (rx_frame_error) rx_bad_frame<=1;
            if (req_valid && req_ready && !rsp_error && req_write && test_select)
                test_result<=req_wdata;
        end
    end
    // Stronger than the ISA requires: fences also drain the UART transmitter.
    assign fence_ready = !tx_busy && !ram_busy && !ai_busy;
    reg [31:0] mmio_word;
    always @(*) begin
        req_ready = !rst;
        if(req_valid && ram_select && !bus_error && SYNC_RAM) req_ready=sync_ram_ready;
        if (req_valid && req_write && data_select && !bus_error && tx_busy)
            req_ready=0;
        if(req_valid && ram_select && req_write && ai_busy && !bus_error) req_ready=0;
        mmio_word=0;
        if(ai_select) mmio_word=ai_rdata;
        else if (data_select) mmio_word=rx_pending ? {24'b0,rx_buffer} : 0;
        else if (status_select) mmio_word={28'b0,rx_bad_frame,rx_overrun,rx_pending,!tx_busy};
        else if (test_select) mmio_word=test_result;
        else if(cycle_lo_select)mmio_word=cycle_count[31:0];
        else if(cycle_hi_select)mmio_word=cycle_count[63:32];
        rsp_rdata=0;
        if (req_valid && !req_write && !rsp_error && !rst) begin
            if (ram_select) rsp_rdata=ram_rdata;
            else case(req_funct3)
                0: rsp_rdata={{24{mmio_word[7]}},mmio_word[7:0]};
                1: rsp_rdata={{16{mmio_word[15]}},mmio_word[15:0]};
                2: rsp_rdata=mmio_word;
                4: rsp_rdata={24'b0,mmio_word[7:0]};
                5: rsp_rdata={16'b0,mmio_word[15:0]};
                default: rsp_rdata=0;
            endcase
        end
    end
endmodule
