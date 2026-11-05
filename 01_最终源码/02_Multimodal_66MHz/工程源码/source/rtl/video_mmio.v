`timescale 1ns/1ps
// Completion-bus peripheral, root integration base 0x20000000.
// Camera and display pin locations/sensor register table are board-specific.
module video_mmio #(
    parameter integer CLK_HZ=27000000,
    parameter integer SENSOR_WIDTH=640,SENSOR_HEIGHT=480,
    parameter integer FRAME_WIDTH=32,FRAME_HEIGHT=32,FRAME_ADDR_BITS=10,
    parameter integer H_ACTIVE=720,H_FRONT=16,H_SYNC=62,H_BACK=60,
    parameter integer V_ACTIVE=480,V_FRONT=9,V_SYNC=6,V_BACK=30,SCALE_SHIFT=3
)(input wire clk,rst,req_valid,req_write,input wire [15:0] req_addr,
  input wire [31:0] req_wdata,input wire [2:0] req_funct3,
  output wire ready,error,output reg [31:0] rdata,output wire frame_irq,
  input wire cam_pclk,cam_vsync,cam_href,input wire [7:0] cam_data,
  inout wire cam_sda,output wire cam_scl,cam_reset_n,cam_pwdn,
  output wire display_pixel_clk,display_hsync,display_vsync,display_de,
  output wire [7:0] display_red,display_green,display_blue);
    wire frame_valid,bad_frame,sccb_busy,sccb_done,sccb_error;
    wire [31:0] frame_sequence,cpu_frame_word;
    wire [FRAME_ADDR_BITS-1:0] display_frame_addr;
    wire [7:0] display_frame_data;
    reg [31:0] sccb_payload;
    reg [1:0] sensor_control;
    reg [15:0] box_xy,box_wh;
    reg box_enable;
    reg [23:0] box_color;
    reg read_pending;
    localparam [31:0] DIMENSIONS=(FRAME_HEIGHT<<16)|FRAME_WIDTH;
    wire frame_sel=req_addr>=16'h1000 && req_addr<16'h1000+(1<<FRAME_ADDR_BITS);
    wire supported_register=req_addr==0 || req_addr==4 || req_addr==8 || req_addr==12 ||
        req_addr==16 || req_addr==20 || req_addr==24 || req_addr==32 || req_addr==36 || req_addr==40 || req_addr==44;
    wire legal_size=req_funct3==0 || req_funct3==1 || req_funct3==2 || req_funct3==4 || req_funct3==5;
    wire misaligned=(req_funct3[1:0]==1 && req_addr[0]) || (req_funct3[1:0]==2 && req_addr[1:0]!=0);
    wire invalid=frame_sel ? (req_write || !legal_size || misaligned || !frame_valid) :
        (!supported_register || req_funct3!=2 || req_addr[1:0]!=0 ||
         (req_write && (req_addr==0 || req_addr==8 || req_addr==12)) ||
         (req_write && req_addr==20 && req_wdata[0] && sccb_busy));
    assign ready=!rst && (!req_valid || invalid || !frame_sel || read_pending);
    assign error=!rst && req_valid && ready && invalid;
    wire complete=req_valid && ready && !error;
    wire write=complete && req_write;
    wire rearm=write && req_addr==4 && req_wdata[0];
    wire clear_error=write && req_addr==4 && req_wdata[1];
    wire sccb_start=write && req_addr==20 && req_wdata[0];
    wire frame_read=req_valid && frame_sel && !invalid && !read_pending;
    assign frame_irq=frame_valid;
    assign cam_reset_n=!rst && sensor_control[0];
    assign cam_pwdn=sensor_control[1];
    camera_dvp_capture #(.SENSOR_WIDTH(SENSOR_WIDTH),.SENSOR_HEIGHT(SENSOR_HEIGHT),
        .FRAME_WIDTH(FRAME_WIDTH),.FRAME_HEIGHT(FRAME_HEIGHT),.FRAME_ADDR_BITS(FRAME_ADDR_BITS)) capture(
        .clk(clk),.rst(rst),.rearm(rearm),.clear_error(clear_error),
        .cam_pclk(cam_pclk),.cam_vsync(cam_vsync),.cam_href(cam_href),.cam_data(cam_data),
        .cpu_read_en(frame_read),.cpu_read_addr(req_addr[FRAME_ADDR_BITS-1:2]),.cpu_read_data(cpu_frame_word),
        .display_read_addr(display_frame_addr),.display_read_data(display_frame_data),
        .frame_valid(frame_valid),.frame_sequence(frame_sequence),.bad_frame(bad_frame));
    camera_sccb_write #(.CLK_HZ(CLK_HZ)) config_bus(
        .clk(clk),.rst(rst),.start(sccb_start),.device(sccb_payload[30:24]),
        .register_addr(sccb_payload[23:8]),.value(sccb_payload[7:0]),
        .busy(sccb_busy),.done(sccb_done),.ack_error(sccb_error),.scl(cam_scl),.sda(cam_sda));
    video_rgb_scanout #(.H_ACTIVE(H_ACTIVE),.H_FRONT(H_FRONT),.H_SYNC(H_SYNC),.H_BACK(H_BACK),
        .V_ACTIVE(V_ACTIVE),.V_FRONT(V_FRONT),.V_SYNC(V_SYNC),.V_BACK(V_BACK),
        .FRAME_WIDTH(FRAME_WIDTH),.FRAME_HEIGHT(FRAME_HEIGHT),.FRAME_ADDR_BITS(FRAME_ADDR_BITS),.SCALE_SHIFT(SCALE_SHIFT)) display(
        .clk(clk),.rst(rst),.frame_valid(frame_valid),.frame_data(display_frame_data),.frame_addr(display_frame_addr),
        .box_x(box_xy[7:0]),.box_y(box_xy[15:8]),.box_w(box_wh[7:0]),.box_h(box_wh[15:8]),
        .box_enable(box_enable),.box_color(box_color),.red(display_red),.green(display_green),.blue(display_blue),
        .hsync(display_hsync),.vsync(display_vsync),.de(display_de),.pixel_clk(display_pixel_clk));
    always @(posedge clk) begin
        if(rst) begin
            sccb_payload<=0;sensor_control<=0;box_xy<=0;box_wh<=0;box_enable<=0;box_color<=24'hffff00;read_pending<=0;
        end else begin
            if(frame_read) read_pending<=1;
            else if(read_pending && (!req_valid || ready)) read_pending<=0;
            if(write) case(req_addr)
                16: sccb_payload<=req_wdata;
                24: sensor_control<=req_wdata[1:0];
                32: box_xy<=req_wdata[15:0];
                36: box_wh<=req_wdata[15:0];
                40: box_enable<=req_wdata[0];
                44: box_color<=req_wdata[23:0];
            endcase
        end
    end
    wire [31:0] shifted=cpu_frame_word >> {req_addr[1:0],3'b000};
    always @(*) begin
        rdata=0;
        if(frame_sel) case(req_funct3)
            0:rdata={{24{shifted[7]}},shifted[7:0]};
            1:rdata={{16{shifted[15]}},shifted[15:0]};
            2:rdata=cpu_frame_word;
            4:rdata={24'b0,shifted[7:0]};
            5:rdata={16'b0,shifted[15:0]};
        endcase
        else case(req_addr)
            0:rdata={28'b0,sccb_error,sccb_busy,bad_frame,frame_valid};
            8:rdata=frame_sequence;
            12:rdata=DIMENSIONS;
            16:rdata=sccb_payload;
            24:rdata={30'b0,sensor_control};
            32:rdata={16'b0,box_xy};
            36:rdata={16'b0,box_wh};
            40:rdata={31'b0,box_enable};
            44:rdata={8'b0,box_color};
        endcase
    end
endmodule
