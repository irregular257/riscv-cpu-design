`timescale 1ns / 1ps
// 8 data bits, no parity, 1 stop bit; idle high; LSB first.
module uart_tx #(
    parameter integer CLK_HZ = 50000000,
    parameter integer BAUD = 115200
)(
    input wire clk, rst,
    input wire start,
    input wire [7:0] data,
    output wire tx,
    output reg busy
);
    localparam integer DIV = (CLK_HZ + BAUD/2) / BAUD;
    localparam integer CW = (DIV < 2) ? 1 : $clog2(DIV);
    reg [CW-1:0] count;
    reg [3:0] bit_index;
    reg [9:0] shift;
    assign tx = busy ? shift[0] : 1'b1;
    always @(posedge clk) begin
        if (rst) begin
            busy <= 0; count <= 0; bit_index <= 0; shift <= 10'h3ff;
        end else if (!busy) begin
            if (start) begin
                shift <= {1'b1, data, 1'b0};
                busy <= 1; count <= DIV-1; bit_index <= 0;
            end
        end else if (count != 0) begin
            count <= count - 1'b1;
        end else begin
            count <= DIV-1;
            if (bit_index == 9) begin
                busy <= 0;
            end else begin
                shift <= {1'b1, shift[9:1]};
                bit_index <= bit_index + 1'b1;
            end
        end
    end
endmodule
