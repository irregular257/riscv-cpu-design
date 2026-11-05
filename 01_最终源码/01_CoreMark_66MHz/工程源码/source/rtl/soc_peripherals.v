`timescale 1ns/1ps
// One-hart machine timer/software interrupt and 32-bit GPIO.
// Word-only MMIO. Side effects occur only on valid && ready && !error.
module soc_peripherals (
    input wire clk, rst,
    input wire req_valid, req_write,
    input wire [31:0] req_addr, req_wdata,
    input wire [2:0] req_funct3,
    output wire ready, error,
    output reg [31:0] rdata,
    input wire [31:0] gpio_in,
    output reg [31:0] gpio_out, gpio_oe,
    output wire irq_timer, irq_software, irq_gpio
);
    reg [63:0] mtime, mtimecmp;
    reg msip;
    reg [31:0] gpio_meta, gpio_sync, gpio_previous;
    reg [31:0] rising_enable, falling_enable, irq_enable, irq_pending;
    wire clint = req_addr==32'h02000000 || req_addr==32'h02004000 ||
                 req_addr==32'h02004004 || req_addr==32'h0200bff8 || req_addr==32'h0200bffc;
    wire gpio = req_addr>=32'h10000200 && req_addr<=32'h10000220 && req_addr[1:0]==0;
    assign ready = !rst;
    assign error = req_valid && (!clint && !gpio || req_funct3!=3'b010 || req_addr[1:0]!=0 ||
                   (req_write && req_addr==32'h10000200));
    wire write_fire = req_valid && req_write && ready && !error;
    wire [31:0] events = ((gpio_sync & ~gpio_previous) & rising_enable) |
                                ((~gpio_sync & gpio_previous) & falling_enable);
    wire clear_pending = write_fire && req_addr==32'h10000218;
    always @(posedge clk) begin
        if(rst) begin
            mtime<=0; mtimecmp<=64'hffffffffffffffff; msip<=0;
            gpio_meta<=0; gpio_sync<=0; gpio_previous<=0;
            gpio_out<=0; gpio_oe<=0; rising_enable<=0; falling_enable<=0;
            irq_enable<=0; irq_pending<=0;
        end else begin
            mtime<=mtime+1'b1;
            gpio_meta<=gpio_in; gpio_sync<=gpio_meta; gpio_previous<=gpio_sync;
            // New edge wins over simultaneous W1C, so an event cannot be lost.
            irq_pending<=(irq_pending & ~(clear_pending ? req_wdata : 32'b0)) | events;
            if(write_fire) case(req_addr)
                32'h02000000: msip<=req_wdata[0];
                32'h02004000: mtimecmp[31:0]<=req_wdata;
                32'h02004004: mtimecmp[63:32]<=req_wdata;
                32'h0200bff8: mtime[31:0]<=req_wdata;
                32'h0200bffc: mtime[63:32]<=req_wdata;
                32'h10000204: gpio_out<=req_wdata;
                32'h10000208: gpio_oe<=req_wdata;
                32'h1000020c: rising_enable<=req_wdata;
                32'h10000210: falling_enable<=req_wdata;
                32'h10000214: irq_enable<=req_wdata;
                32'h1000021c: gpio_out<=gpio_out | req_wdata;
                32'h10000220: gpio_out<=gpio_out & ~req_wdata;
                default: begin end
            endcase
        end
    end
    assign irq_timer = mtime>=mtimecmp;
    assign irq_software = msip;
    assign irq_gpio = |(irq_pending & irq_enable);
    always @(*) begin
        rdata=0;
        if(req_valid && !req_write && !error) case(req_addr)
            32'h02000000: rdata={31'b0,msip};
            32'h02004000: rdata=mtimecmp[31:0];
            32'h02004004: rdata=mtimecmp[63:32];
            32'h0200bff8: rdata=mtime[31:0];
            32'h0200bffc: rdata=mtime[63:32];
            32'h10000200: rdata=gpio_sync;
            32'h10000204: rdata=gpio_out;
            32'h10000208: rdata=gpio_oe;
            32'h1000020c: rdata=rising_enable;
            32'h10000210: rdata=falling_enable;
            32'h10000214: rdata=irq_enable;
            32'h10000218: rdata=irq_pending;
            default: rdata=0;
        endcase
    end
endmodule
