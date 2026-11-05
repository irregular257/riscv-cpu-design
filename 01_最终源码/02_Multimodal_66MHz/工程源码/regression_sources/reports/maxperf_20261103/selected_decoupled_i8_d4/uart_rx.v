`timescale 1ns / 1ps
// CLK_HZ/BAUD must be >=8. Two-flop input synchronizer, center sampling.
module uart_rx #(
    parameter integer CLK_HZ = 50000000,
    parameter integer BAUD = 115200
)(
    input wire clk, rst,
    input wire rx,
    output reg [7:0] data,
    output reg valid,
    output reg frame_error
);
    localparam integer DIV = (CLK_HZ + BAUD/2) / BAUD;
    localparam integer CW = (DIV < 2) ? 1 : $clog2(DIV);
    localparam [1:0] IDLE=0, START=1, DATA=2, STOP=3;
    (* ASYNC_REG = "TRUE" *) reg rx_meta, rx_sync;
    reg [1:0] state;
    reg [CW-1:0] count;
    reg [2:0] bit_index;
    reg [7:0] shift;
    always @(posedge clk) begin
        if (rst) begin rx_meta<=1; rx_sync<=1; end
        else begin rx_meta<=rx; rx_sync<=rx_meta; end
    end
    always @(posedge clk) begin
        if (rst) begin
            state<=IDLE; count<=0; bit_index<=0;
            shift<=0; data<=0; valid<=0; frame_error<=0;
        end else begin
            valid<=0; frame_error<=0;
            case (state)
                IDLE: if (!rx_sync) begin state<=START; count<=DIV/2-1; end
                START: begin
                    if (count!=0) count<=count-1'b1;
                    else if (rx_sync) state<=IDLE; // reject a short glitch
                    else begin state<=DATA; count<=DIV-1; bit_index<=0; end
                end
                DATA: begin
                    if (count!=0) count<=count-1'b1;
                    else begin
                        shift[bit_index]<=rx_sync; count<=DIV-1;
                        if (bit_index==7) state<=STOP;
                        else bit_index<=bit_index+1'b1;
                    end
                end
                STOP: begin
                    if (count!=0) count<=count-1'b1;
                    else begin
                        state<=IDLE;
                        if (rx_sync) begin data<=shift; valid<=1; end
                        else frame_error<=1;
                    end
                end
                default: state<=IDLE;
            endcase
        end
    end
endmodule
