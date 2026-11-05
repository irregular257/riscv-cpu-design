`timescale 1ns/1ps
// Single-master 7-bit-device/16-bit-register/8-bit-value SCCB write transaction.
// Open-drain SDA, no clock stretching/arbitration. Register tables are firmware data.
module camera_sccb_write #(
    parameter integer CLK_HZ=27000000, SCL_HZ=100000
)(input wire clk,rst,start,input wire [6:0] device,
  input wire [15:0] register_addr,input wire [7:0] value,
  output reg busy,done,ack_error,output reg scl,inout wire sda);
    localparam integer HALF_DIV=(CLK_HZ+2*SCL_HZ-1)/(2*SCL_HZ);
    localparam IDLE=0,START=1,LOW=2,HIGH=3,ACK_LOW=4,ACK_HIGH=5,STOP_LOW=6,STOP_HIGH=7,STOP_END=8;
    reg [3:0] state;
    reg sda_low;
    reg [31:0] bits;
    reg [5:0] bit_count;
    integer divider;
    assign sda=sda_low ? 1'b0 : 1'bz;
    always @(posedge clk) begin
        if(rst) begin busy<=0;done<=0;ack_error<=0;scl<=1;sda_low<=0;state<=IDLE;divider<=0;bits<=0;bit_count<=0;end
        else begin
            done<=0;
            if(state==IDLE) begin
                if(start) begin
                    busy<=1;ack_error<=0;bits<={device,1'b0,register_addr,value};bit_count<=0;
                    sda_low<=1;scl<=1;divider<=HALF_DIV-1;state<=START;
                end
            end else if(divider!=0) divider<=divider-1;
            else begin
                divider<=HALF_DIV-1;
                case(state)
                    START: begin scl<=0;sda_low<=!bits[31];state<=LOW;end
                    LOW: begin scl<=1;state<=HIGH;end
                    HIGH: begin
                        scl<=0;bits<={bits[30:0],1'b0};bit_count<=bit_count+1;
                        if(bit_count[2:0]==7) begin sda_low<=0;state<=ACK_LOW;end
                        else begin sda_low<=!bits[30];state<=LOW;end
                    end
                    ACK_LOW: begin scl<=1;state<=ACK_HIGH;end
                    ACK_HIGH: begin
                        scl<=0;
                        if(sda!==1'b0) ack_error<=1;
                        if(bit_count==32) begin sda_low<=1;state<=STOP_LOW;end
                        else begin sda_low<=!bits[31];state<=LOW;end
                    end
                    STOP_LOW: begin scl<=1;sda_low<=1;state<=STOP_HIGH;end
                    STOP_HIGH: begin sda_low<=0;state<=STOP_END;end
                    STOP_END: begin busy<=0;done<=1;state<=IDLE;end
                    default: state<=IDLE;
                endcase
            end
        end
    end
endmodule
