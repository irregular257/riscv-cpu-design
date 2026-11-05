`timescale 1ns/1ps
// RGB565 DVP input. A completed frame is held stable until the CPU rearms it.
// No unsynchronised multi-bit frame pointer crosses a clock domain.
module camera_dvp_capture #(
    parameter integer SENSOR_WIDTH=640, SENSOR_HEIGHT=480,
    parameter integer FRAME_WIDTH=32, FRAME_HEIGHT=32,
    parameter integer FRAME_ADDR_BITS=10
)(
    input wire clk, rst, rearm, clear_error,
    input wire cam_pclk, cam_vsync, cam_href,
    input wire [7:0] cam_data,
    input wire cpu_read_en,
    input wire [FRAME_ADDR_BITS-3:0] cpu_read_addr,
    output reg [31:0] cpu_read_data,
    input wire [FRAME_ADDR_BITS-1:0] display_read_addr,
    output wire [7:0] display_read_data,
    output reg frame_valid,
    output reg [31:0] frame_sequence,
    output reg bad_frame
);
    localparam integer PIXELS=FRAME_WIDTH*FRAME_HEIGHT;
    localparam integer WORDS=(PIXELS+3)/4;
    localparam integer X_STEP=SENSOR_WIDTH/FRAME_WIDTH;
    localparam integer Y_STEP=SENSOR_HEIGHT/FRAME_HEIGHT;
    // Duplicate the small buffer to provide one CPU and one display read port.
    // Each copy has a camera write clock and a synchronous system read clock.
    reg [31:0] cpu_mem[0:WORDS-1];
    reg [31:0] display_mem[0:WORDS-1];
    reg [31:0] display_word;
    reg [1:0] display_byte;
    always @(posedge clk) begin
        if(cpu_read_en) cpu_read_data<=cpu_mem[cpu_read_addr];
        display_word<=display_mem[display_read_addr[FRAME_ADDR_BITS-1:2]];
        display_byte<=display_read_addr[1:0];
    end
    assign display_read_data=display_word >> {display_byte,3'b000};

    reg ack_toggle, clear_toggle;
    reg done_meta,done_sync,done_seen,err_meta,err_sync,err_seen;
    reg done_toggle, error_toggle;
    always @(posedge clk) begin
        if(rst) begin
            ack_toggle<=0; clear_toggle<=0;
            done_meta<=0;done_sync<=0;done_seen<=0;
            err_meta<=0;err_sync<=0;err_seen<=0;
            frame_valid<=0;frame_sequence<=0;bad_frame<=0;
        end else begin
            done_meta<=done_toggle;done_sync<=done_meta;
            err_meta<=error_toggle;err_sync<=err_meta;
            if(rearm && frame_valid) begin ack_toggle<=~ack_toggle;frame_valid<=0;end
            if(clear_error) begin clear_toggle<=~clear_toggle;bad_frame<=0;end
            if(done_sync!=done_seen) begin
                done_seen<=done_sync;frame_valid<=1;frame_sequence<=frame_sequence+1;
            end
            if(err_sync!=err_seen) begin err_seen<=err_sync;bad_frame<=1;end
        end
    end
    reg ack_meta,ack_sync,ack_seen,clr_meta,clr_sync,clr_seen;
    reg old_vsync,old_href,half_pixel,full,capturing;
    reg [7:0] high_byte;
    reg [15:0] pixel_x,line_y;
    reg [FRAME_ADDR_BITS:0] sample_count;
    wire [15:0] rgb565={high_byte,cam_data};
    wire [7:0] red={rgb565[15:11],rgb565[15:13]};
    wire [7:0] green={rgb565[10:5],rgb565[10:9]};
    wire [7:0] blue={rgb565[4:0],rgb565[4:2]};
    wire [9:0] weighted_luma={2'b0,red}+{1'b0,green,1'b0}+{2'b0,blue};
    wire [7:0] luma=weighted_luma[9:2];
    wire sample_here=(pixel_x<SENSOR_WIDTH && line_y<SENSOR_HEIGHT &&
                          pixel_x%X_STEP==0 && line_y%Y_STEP==0);
    always @(posedge cam_pclk or posedge rst) begin
        if(rst) begin
            ack_meta<=0;ack_sync<=0;ack_seen<=0;clr_meta<=0;clr_sync<=0;clr_seen<=0;
            old_vsync<=0;old_href<=0;half_pixel<=0;high_byte<=0;
            full<=0;capturing<=0;pixel_x<=0;line_y<=0;sample_count<=0;
            done_toggle<=0;error_toggle<=0;
        end else begin
            ack_meta<=ack_toggle;ack_sync<=ack_meta;
            clr_meta<=clear_toggle;clr_sync<=clr_meta;
            old_vsync<=cam_vsync;old_href<=cam_href;
            if(ack_sync!=ack_seen) begin ack_seen<=ack_sync;full<=0;end
            if(clr_sync!=clr_seen) clr_seen<=clr_sync;
            if(cam_vsync && !old_vsync) begin
                pixel_x<=0;line_y<=0;half_pixel<=0;sample_count<=0;
                if(capturing && sample_count==PIXELS) begin
                    done_toggle<=~done_toggle;full<=1;capturing<=0;
                end else if(!full) begin
                    if(capturing && sample_count!=0) error_toggle<=~error_toggle;
                    capturing<=1;
                end
            end else if(!cam_vsync && capturing && !full) begin
                if(old_href && !cam_href) begin
                    if(half_pixel) error_toggle<=~error_toggle;
                    pixel_x<=0;line_y<=line_y+1;half_pixel<=0;
                end
                if(cam_href) begin
                    half_pixel<=~half_pixel;
                    if(!half_pixel) high_byte<=cam_data;
                    else begin
                        pixel_x<=pixel_x+1;
                        if(sample_here && sample_count<PIXELS) begin
                            case(sample_count[1:0])
                                0: begin cpu_mem[sample_count[FRAME_ADDR_BITS-1:2]][7:0]<=luma;display_mem[sample_count[FRAME_ADDR_BITS-1:2]][7:0]<=luma;end
                                1: begin cpu_mem[sample_count[FRAME_ADDR_BITS-1:2]][15:8]<=luma;display_mem[sample_count[FRAME_ADDR_BITS-1:2]][15:8]<=luma;end
                                2: begin cpu_mem[sample_count[FRAME_ADDR_BITS-1:2]][23:16]<=luma;display_mem[sample_count[FRAME_ADDR_BITS-1:2]][23:16]<=luma;end
                                3: begin cpu_mem[sample_count[FRAME_ADDR_BITS-1:2]][31:24]<=luma;display_mem[sample_count[FRAME_ADDR_BITS-1:2]][31:24]<=luma;end
                            endcase
                            sample_count<=sample_count+1;
                        end
                    end
                end
            end
        end
    end
endmodule
