module matrix_display #(
    parameter integer CLK_HZ = 27000000,
    parameter integer SCAN_INTERVAL = CLK_HZ / 1000,
    parameter integer SERIAL_DIV = (CLK_HZ / 1000000 > 0) ? CLK_HZ / 1000000 : 1
)(
    input wire clk, rst,
    input wire req_valid, req_write,
    input wire [31:0] req_addr, req_wdata,
    input wire [2:0] req_funct3,
    output wire ready, error,
    output reg [31:0] rdata,
    output reg [3:0] DS_Matrix,
    output wire [3:0] G_Matrix,
    output reg [3:0] RCK_Matrix, SCLK_Matrix,
    output wire [3:0] SCLR_Matrix
);
    localparam [2:0] BEGIN_COLUMN=0, SHIFT_LOW=1, SHIFT_HIGH=2,
                     LATCH=3, GUARD=4, HOLD_COLUMN=5;
    // PWM advances once every ~1/256 column dwell time. The first increment
    // occurs after a complete PWM quantum, including at brightness=1.
    localparam integer PWM_DIV = (SCAN_INTERVAL / 256 > 0) ? SCAN_INTERVAL / 256 : 1;
    reg cfg_enable, active_enable;
    reg [4:0] cfg_glyph, active_glyph;
    reg [7:0] cfg_brightness, active_brightness;
    reg [2:0] state;
    reg [3:0] column_index;
    reg [2:0] bit_index;
    reg [31:0] serial_count, slot_count, pwm_count;
    reg [7:0] pwm_phase;
    reg [31:0] shift_data;

    wire known_addr = req_addr==32'h10000400 || req_addr==32'h10000404 ||
                      req_addr==32'h10000408 || req_addr==32'h1000040c;
    wire bad_value = (req_addr==32'h10000400 && req_wdata>1) ||
                     (req_addr==32'h10000404 && req_wdata>16) ||
                     (req_addr==32'h10000408 && req_wdata>255);
    assign ready = !rst;
    assign error = req_valid && (!known_addr || req_addr[1:0]!=0 ||
                   req_funct3!=3'b010 || (req_write &&
                   (req_addr==32'h1000040c || bad_value)));
    wire write_fire = req_valid && req_write && ready && !error;
    wire pending_update = cfg_enable!=active_enable || cfg_glyph!=active_glyph ||
                          cfg_brightness!=active_brightness;
    wire show_pixels = !rst && state==HOLD_COLUMN && active_enable &&
                       active_glyph!=15 && (active_brightness==255 ||
                       pwm_phase<active_brightness);
    assign G_Matrix = show_pixels ? 4'b0000 : 4'b1111;
    assign SCLR_Matrix = rst ? 4'b0000 : 4'b1111;

    // Seven rows, five columns, top row first. Digits are scaled 2x and
    // centered in the 16x16 panel; bit 0 of a row driver is the top LED,
    // matching the vertically corrected font in the official example.
    function [34:0] small_font;
        input [4:0] glyph;
        begin
            case(glyph)
             0:small_font={5'b01110,5'b10001,5'b10011,5'b10101,5'b11001,5'b10001,5'b01110};
             1:small_font={5'b00100,5'b01100,5'b00100,5'b00100,5'b00100,5'b00100,5'b01110};
             2:small_font={5'b01110,5'b10001,5'b00001,5'b00010,5'b00100,5'b01000,5'b11111};
             3:small_font={5'b11110,5'b00001,5'b00001,5'b01110,5'b00001,5'b00001,5'b11110};
             4:small_font={5'b00010,5'b00110,5'b01010,5'b10010,5'b11111,5'b00010,5'b00010};
             5:small_font={5'b11111,5'b10000,5'b10000,5'b11110,5'b00001,5'b00001,5'b11110};
             6:small_font={5'b01110,5'b10000,5'b10000,5'b11110,5'b10001,5'b10001,5'b01110};
             7:small_font={5'b11111,5'b00001,5'b00010,5'b00100,5'b01000,5'b01000,5'b01000};
             8:small_font={5'b01110,5'b10001,5'b10001,5'b01110,5'b10001,5'b10001,5'b01110};
             9:small_font={5'b01110,5'b10001,5'b10001,5'b01111,5'b00001,5'b00001,5'b01110};
            14:small_font={5'b01110,5'b10001,5'b00001,5'b00010,5'b00100,5'b00000,5'b00100};
            16:small_font={5'b11111,5'b10001,5'b01010,5'b00100,5'b01010,5'b10001,5'b11111};
            default:small_font=0;
            endcase
        end
    endfunction
    // Fixed columns, bit 0 = top LED; no signed coordinate arithmetic.
    function [15:0] arrow_column;
        input [4:0] glyph;
        input [3:0] x;
        begin
            arrow_column=16'h0000;
            case(glyph)
            5'd10: begin // up
                case(x)
                4'd0: arrow_column=16'h0000;
                4'd1: arrow_column=16'h0000;
                4'd2: arrow_column=16'h0080;
                4'd3: arrow_column=16'h00c0;
                4'd4: arrow_column=16'h00e0;
                4'd5: arrow_column=16'h00f0;
                4'd6: arrow_column=16'h3ff8;
                4'd7: arrow_column=16'h3ffc;
                4'd8: arrow_column=16'h3ffc;
                4'd9: arrow_column=16'h3ff8;
                4'd10: arrow_column=16'h00f0;
                4'd11: arrow_column=16'h00e0;
                4'd12: arrow_column=16'h00c0;
                4'd13: arrow_column=16'h0080;
                4'd14: arrow_column=16'h0000;
                4'd15: arrow_column=16'h0000;
                default: arrow_column=16'h0000;
                endcase
            end
            5'd11: begin // down
                case(x)
                4'd0: arrow_column=16'h0000;
                4'd1: arrow_column=16'h0000;
                4'd2: arrow_column=16'h0100;
                4'd3: arrow_column=16'h0300;
                4'd4: arrow_column=16'h0700;
                4'd5: arrow_column=16'h0f00;
                4'd6: arrow_column=16'h1ffc;
                4'd7: arrow_column=16'h3ffc;
                4'd8: arrow_column=16'h3ffc;
                4'd9: arrow_column=16'h1ffc;
                4'd10: arrow_column=16'h0f00;
                4'd11: arrow_column=16'h0700;
                4'd12: arrow_column=16'h0300;
                4'd13: arrow_column=16'h0100;
                4'd14: arrow_column=16'h0000;
                4'd15: arrow_column=16'h0000;
                default: arrow_column=16'h0000;
                endcase
            end
            5'd12: begin // left
                case(x)
                4'd0: arrow_column=16'h0000;
                4'd1: arrow_column=16'h0000;
                4'd2: arrow_column=16'h0180;
                4'd3: arrow_column=16'h03c0;
                4'd4: arrow_column=16'h07e0;
                4'd5: arrow_column=16'h0ff0;
                4'd6: arrow_column=16'h1ff8;
                4'd7: arrow_column=16'h3ffc;
                4'd8: arrow_column=16'h03c0;
                4'd9: arrow_column=16'h03c0;
                4'd10: arrow_column=16'h03c0;
                4'd11: arrow_column=16'h03c0;
                4'd12: arrow_column=16'h03c0;
                4'd13: arrow_column=16'h03c0;
                4'd14: arrow_column=16'h0000;
                4'd15: arrow_column=16'h0000;
                default: arrow_column=16'h0000;
                endcase
            end
            5'd13: begin // right
                case(x)
                4'd0: arrow_column=16'h0000;
                4'd1: arrow_column=16'h0000;
                4'd2: arrow_column=16'h03c0;
                4'd3: arrow_column=16'h03c0;
                4'd4: arrow_column=16'h03c0;
                4'd5: arrow_column=16'h03c0;
                4'd6: arrow_column=16'h03c0;
                4'd7: arrow_column=16'h03c0;
                4'd8: arrow_column=16'h3ffc;
                4'd9: arrow_column=16'h1ff8;
                4'd10: arrow_column=16'h0ff0;
                4'd11: arrow_column=16'h07e0;
                4'd12: arrow_column=16'h03c0;
                4'd13: arrow_column=16'h0180;
                4'd14: arrow_column=16'h0000;
                4'd15: arrow_column=16'h0000;
                default: arrow_column=16'h0000;
                endcase
            end
            default: arrow_column=16'h0000;
            endcase
        end
    endfunction
    function [15:0] glyph_column;
        input [4:0] glyph;
        input [3:0] x;
        integer y;
        reg [34:0] font;
        begin
            glyph_column=16'h0000;
            font=small_font(glyph);
            case(glyph)
            5'd10,5'd11,5'd12,5'd13:glyph_column=arrow_column(glyph,x);
            default:begin
                for(y=0;y<16;y=y+1) begin
                    if(x>=3 && x<=12 && y>=1 && y<=14)
                        glyph_column[y]=font[(6-((y-1)>>1))*5+(4-((x-3)>>1))];
                end
            end
            endcase
        end
    endfunction
    wire [4:0] next_glyph = column_index==0 ? cfg_glyph : active_glyph;
    wire [15:0] next_rows = glyph_column(next_glyph,column_index);
    wire [15:0] next_columns = ~(16'h0001 << column_index);

    always @(*) begin
        rdata=0;
        if(req_valid && !req_write && !error) case(req_addr)
        32'h10000400:rdata={31'b0,cfg_enable};
        32'h10000404:rdata={27'b0,cfg_glyph};
        32'h10000408:rdata={24'b0,cfg_brightness};
        // bit0 active enable; bit1 pending; bits7:4 column; bits12:8
        // active glyph; bits23:16 active brightness; bit24 serial/blanking.
        32'h1000040c:rdata={7'b0,(state!=HOLD_COLUMN),active_brightness,
                           3'b0,active_glyph,2'b0,column_index,pending_update,active_enable};
        default:rdata=0;
        endcase
    end
    always @(posedge clk) begin
        if(rst) begin
            cfg_enable<=0; cfg_glyph<=15; cfg_brightness<=255;
        end else if(write_fire) case(req_addr)
            32'h10000400:cfg_enable<=req_wdata[0];
            32'h10000404:cfg_glyph<=req_wdata[4:0];
            32'h10000408:cfg_brightness<=req_wdata[7:0];
            default:begin end
        endcase
    end
    always @(posedge clk) begin
        if(rst) begin
            active_enable<=0;active_glyph<=15;active_brightness<=255;
            state<=BEGIN_COLUMN;column_index<=0;bit_index<=0;
            serial_count<=0;slot_count<=0;pwm_count<=0;pwm_phase<=0;
            shift_data<=0;DS_Matrix<=0;RCK_Matrix<=0;SCLK_Matrix<=0;
        end else begin
            if(slot_count!=32'hffffffff)slot_count<=slot_count+1'b1;
            if(state==BEGIN_COLUMN) begin
                // Blank for the complete shift/latch/guard sequence. All four
                // storage-register latches rise together, after exactly 8 bits.
                if(column_index==0) begin
                    active_enable<=cfg_enable;active_glyph<=cfg_glyph;
                    active_brightness<=cfg_brightness;
                end
                shift_data<={next_columns[15:8],next_rows[15:8],next_columns[7:0],next_rows[7:0]};
                bit_index<=0;serial_count<=0;slot_count<=0;
                DS_Matrix<=0;SCLK_Matrix<=0;RCK_Matrix<=0;
                state<=SHIFT_LOW;
            end else if(state==HOLD_COLUMN) begin
                if(slot_count>=SCAN_INTERVAL-1) begin
                    column_index<=column_index+1'b1;state<=BEGIN_COLUMN;
                end
                if(pwm_count>=PWM_DIV-1) begin
                    pwm_count<=0;
                    if(pwm_phase!=255)pwm_phase<=pwm_phase+1'b1;
                end else pwm_count<=pwm_count+1'b1;
            end else if(serial_count>=SERIAL_DIV-1) begin
                serial_count<=0;
                case(state)
                SHIFT_LOW:begin
                    SCLK_Matrix<=0;
                    DS_Matrix<={shift_data[31],shift_data[23],shift_data[15],shift_data[7]};
                    state<=SHIFT_HIGH;
                end
                SHIFT_HIGH:begin
                    SCLK_Matrix<=4'b1111;shift_data<=shift_data<<1;
                    if(bit_index==7)state<=LATCH;
                    else begin bit_index<=bit_index+1'b1;state<=SHIFT_LOW;end
                end
                LATCH:begin
                    SCLK_Matrix<=0;RCK_Matrix<=4'b1111;DS_Matrix<=0;
                    state<=GUARD;
                end
                GUARD:begin
                    RCK_Matrix<=0;state<=HOLD_COLUMN;pwm_phase<=0;pwm_count<=0;
                end
                default:state<=BEGIN_COLUMN;
                endcase
            end else serial_count<=serial_count+1'b1;
        end
    end
endmodule
