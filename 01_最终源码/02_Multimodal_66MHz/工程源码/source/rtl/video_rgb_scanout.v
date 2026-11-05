`timescale 1ns/1ps
// Parallel RGB scanout, NOT an HDMI/TMDS serializer or a panel-specific driver.
// Defaults: 720x480 active, 858x525 total at 27MHz = 59.94Hz (CEA timing).
module video_rgb_scanout #(
    parameter integer H_ACTIVE=720,H_FRONT=16,H_SYNC=62,H_BACK=60,
    parameter integer V_ACTIVE=480,V_FRONT=9,V_SYNC=6,V_BACK=30,
    parameter integer FRAME_WIDTH=32,FRAME_HEIGHT=32,FRAME_ADDR_BITS=10,SCALE_SHIFT=3
)(input wire clk,rst,frame_valid,input wire [7:0] frame_data,
  output wire [FRAME_ADDR_BITS-1:0] frame_addr,
  input wire [7:0] box_x,box_y,box_w,box_h,input wire box_enable,
  input wire [23:0] box_color,output reg [7:0] red,green,blue,
  output reg hsync,vsync,de,output wire pixel_clk);
    localparam integer H_TOTAL=H_ACTIVE+H_FRONT+H_SYNC+H_BACK;
    localparam integer V_TOTAL=V_ACTIVE+V_FRONT+V_SYNC+V_BACK;
    localparam integer X0=(H_ACTIVE-(FRAME_WIDTH<<SCALE_SHIFT))/2;
    localparam integer Y0=(V_ACTIVE-(FRAME_HEIGHT<<SCALE_SHIFT))/2;
    reg [15:0] x,y;
    wire active=x<H_ACTIVE && y<V_ACTIVE;
    wire canvas=frame_valid && x>=X0 && x<X0+(FRAME_WIDTH<<SCALE_SHIFT) && y>=Y0 && y<Y0+(FRAME_HEIGHT<<SCALE_SHIFT);
    wire [15:0] source_x=(x-X0)>>SCALE_SHIFT,source_y=(y-Y0)>>SCALE_SHIFT;
    assign frame_addr=canvas ? source_y*FRAME_WIDTH+source_x : 0;
    wire [15:0] left=X0+(box_x<<SCALE_SHIFT), top=Y0+(box_y<<SCALE_SHIFT);
    wire [15:0] right=left+(box_w<<SCALE_SHIFT),bottom=top+(box_h<<SCALE_SHIFT);
    wire border=box_enable && box_w!=0 && box_h!=0 &&
        x>=left && x<=right && y>=top && y<=bottom &&
        (x==left || x==right || y==top || y==bottom);
    reg active_d,canvas_d,border_d,hs_d,vs_d;
    assign pixel_clk=clk;
    always @(posedge clk) begin
        if(rst) begin x<=0;y<=0;active_d<=0;canvas_d<=0;border_d<=0;hs_d<=1;vs_d<=1;red<=0;green<=0;blue<=0;hsync<=1;vsync<=1;de<=0;end
        else begin
            if(x==H_TOTAL-1) begin x<=0;if(y==V_TOTAL-1)y<=0;else y<=y+1;end else x<=x+1;
            active_d<=active;canvas_d<=canvas;border_d<=border;
            hs_d<=!(x>=H_ACTIVE+H_FRONT && x<H_ACTIVE+H_FRONT+H_SYNC);
            vs_d<=!(y>=V_ACTIVE+V_FRONT && y<V_ACTIVE+V_FRONT+V_SYNC);
            hsync<=hs_d;vsync<=vs_d;de<=active_d;
            if(active_d && border_d) begin red<=box_color[23:16];green<=box_color[15:8];blue<=box_color[7:0];end
            else if(active_d && canvas_d) begin red<=frame_data;green<=frame_data;blue<=frame_data;end
            else begin red<=0;green<=0;blue<=0;end
        end
    end
endmodule
