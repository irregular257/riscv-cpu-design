`timescale 1ns/1ps
module tb_matrix_display;
    reg clk=0;always #5 clk=~clk;
    reg rst=1,req_valid=0,req_write=0;
    reg [31:0] req_addr=0,req_wdata=0;
    reg [2:0] req_funct3=2;
    wire ready,error;
    wire [31:0] rdata;
    wire [3:0] ds,g,rck,sclk,sclr;
    matrix_display #(.CLK_HZ(1000000),.SCAN_INTERVAL(512),.SERIAL_DIV(2)) dut(
       .clk(clk),.rst(rst),.req_valid(req_valid),.req_write(req_write),
       .req_addr(req_addr),.req_wdata(req_wdata),.req_funct3(req_funct3),
       .ready(ready),.error(error),.rdata(rdata),.DS_Matrix(ds),
       .G_Matrix(g),.RCK_Matrix(rck),.SCLK_Matrix(sclk),.SCLR_Matrix(sclr));
    integer checks=0,latches=0,frames=0,serial_bits=0,last_column=15;
    integer shadow_glyph=15,shadow_enable=0,shadow_brightness=255;
    integer frame_glyph=15,frame_enable=0,frame_brightness=255;
    integer j,col,low_count,lit_clocks=0,total_clocks=0,start_lit,start_total;
    integer full_lit,low_lit,one_lit,zero_lit;
    reg [3:0] prev_sclk=0,prev_rck=0,prev_ds=0;
    reg [7:0] sr[0:3];
    reg [15:0] panel_rows,panel_columns;
    reg [255:0] wanted;

    // Golden images are fixed 16x16 pixel maps generated independently from
    // readable row strings. No DUT font function or DUT state is used here.
    function [255:0] expected_image;
        input integer glyph;
        begin
            case(glyph)
             0:expected_image=256'h0000000000001ff81ff86066606661866186660666061ff81ff8000000000000;
             1:expected_image=256'h00000000000000000000600060007ffe7ffe6018601800000000000000000000;
             2:expected_image=256'h0000000000006078607861866186660666067806780660186018000000000000;
             3:expected_image=256'h0000000000001e781e7861866186618661866186618660066006000000000000;
             4:expected_image=256'h000000000000060006007ffe7ffe061806180660066007800780000000000000;
             5:expected_image=256'h0000000000001e061e0661866186618661866186618661fe61fe000000000000;
             6:expected_image=256'h0000000000001e001e006186618661866186618661861ff81ff8000000000000;
             7:expected_image=256'h000000000000001e001e00660066018601867e067e0600060006000000000000;
             8:expected_image=256'h0000000000001e781e786186618661866186618661861e781e78000000000000;
             9:expected_image=256'h0000000000001ff81ff861866186618661866186618600780078000000000000;
            10:expected_image=256'h00000000008000c000e000f03ff83ffc3ffc3ff800f000e000c0008000000000;
            11:expected_image=256'h000000000100030007000f001ffc3ffc3ffc1ffc0f0007000300010000000000;
            12:expected_image=256'h0000000003c003c003c003c003c003c03ffc1ff80ff007e003c0018000000000;
            13:expected_image=256'h00000000018003c007e00ff01ff83ffc03c003c003c003c003c003c000000000;
            14:expected_image=256'h0000000000000078007801860186660666060006000600180018000000000000;
            15:expected_image=0;
            16:expected_image=256'h000000000000781e781e666666666186618666666666781e781e000000000000;
            default:expected_image=0;
            endcase
        end
    endfunction
    task check;
        input condition;
        input [8*120-1:0] message;
        begin
            checks=checks+1;
            if(condition!==1'b1)begin
                $display("FAIL %0s t=%0t latches=%0d frame=%0d col=%0d glyph=%0d",message,$time,latches,frames,last_column,frame_glyph);
                $fatal(1);
            end
        end
    endtask
    task access;
        input writing;
        input [31:0] address,value;
        input [2:0] size;
        input expect_error;
        begin
            @(negedge clk);req_valid=1;req_write=writing;req_addr=address;req_wdata=value;req_funct3=size;
            #1;check(ready,"MMIO ready");check(error===expect_error,"MMIO error indication");
            @(posedge clk);#1;
            if(writing && !expect_error)case(address)
                32'h10000400:shadow_enable=value;
                32'h10000404:shadow_glyph=value;
                32'h10000408:shadow_brightness=value;
            endcase
            @(negedge clk);req_valid=0;req_write=0;
        end
    endtask
    task write_reg;
        input [31:0] address,value;
        begin access(1,address,value,2,0);end
    endtask
    task read_check;
        input [31:0] address,mask,value;
        begin
            @(negedge clk);req_valid=1;req_write=0;req_addr=address;req_funct3=2;
            #1;check(ready && !error,"MMIO read accepted");check((rdata&mask)===value,"MMIO read value");
            @(negedge clk);req_valid=0;
        end
    endtask
    task wait_columns;
        input integer number;
        integer target;
        begin target=latches+number;wait(latches>=target);@(negedge clk);end
    endtask
    task measure_frame;
        output integer lit;
        begin
            wait(last_column==15);wait_columns(1);
            start_lit=lit_clocks;start_total=total_clocks;
            wait_columns(16);lit=lit_clocks-start_lit;
            check(total_clocks-start_total==16*513,"constant frame period");
        end
    endtask
    task reset_panel;
        begin
            @(negedge clk);rst=1;#1;
            check(g==15 && sclr==0,"reset immediately blanks and clears shift registers");
            repeat(3)@(posedge clk);
            @(negedge clk);shadow_enable=0;shadow_glyph=15;shadow_brightness=255;rst=0;
            wait_columns(18);check(g==15,"display remains disabled after reset");
        end
    endtask

    // Model the actual four independent 595 chains using only physical pins.
    always @(negedge clk)begin
        #1;
        total_clocks=total_clocks+1;
        if(rst)begin
            check(g==15,"G blank while reset");check(sclr==0,"SCLR active while reset");
            serial_bits=0;last_column=15;frame_glyph=15;frame_enable=0;frame_brightness=255;
            for(j=0;j<4;j=j+1)sr[j]=0;
        end else begin
            check(sclr==15,"SCLR inactive in normal operation");
            check((sclk==0 || sclk==15) && (rck==0 || rck==15),"four lanes clock together");
            if(sclk[0] && !prev_sclk[0])begin
                check(g==15,"shift is fully blanked");check(ds==prev_ds,"serial data setup before rising edge");
                for(j=0;j<4;j=j+1)sr[j]={sr[j][6:0],ds[j]};
                serial_bits=serial_bits+1;
            end
            if(rck[0] && !prev_rck[0])begin
                check(g==15,"latch is fully blanked");check(serial_bits==8,"eight serial bits per latch");
                serial_bits=0;panel_rows={sr[2],sr[0]};panel_columns={sr[3],sr[1]};
                col=0;low_count=0;
                for(j=0;j<16;j=j+1)if(!panel_columns[j])begin col=j;low_count=low_count+1;end
                check(low_count==1,"exactly one active-low selected column");
                check(col==((last_column+1)%16),"all sixteen columns in order");last_column=col;
                if(col==0)begin
                    frames=frames+1;frame_glyph=shadow_glyph;frame_enable=shadow_enable;frame_brightness=shadow_brightness;
                end
                wanted=expected_image(frame_glyph);
                check(panel_rows===((wanted>>(col*16))&16'hffff),"serial pixels match complete golden glyph/frame");
                latches=latches+1;
            end
            if(g==0)begin
                lit_clocks=lit_clocks+1;
                check(frame_enable!=0 && frame_glyph!=15 && frame_brightness!=0,"only enabled visible glyph can illuminate");
                check(sclk==0 && rck==0,"output enable follows quiet latch/shift pins");
            end else check(g==15,"all output enables share blanking/PWM");
        end
        prev_sclk=sclk;prev_rck=rck;prev_ds=ds;
    end
    integer glyph;
    initial begin
        repeat(4)@(posedge clk);@(negedge clk);rst=0;
        wait_columns(18);check(g==15,"power-up is dark");
        read_check(32'h10000400,32'hffffffff,0);
        read_check(32'h10000404,32'hffffffff,15);
        read_check(32'h10000408,32'hffffffff,255);
        write_reg(32'h10000404,0);write_reg(32'h10000400,1);
        for(glyph=0;glyph<=16;glyph=glyph+1)begin
            write_reg(32'h10000404,glyph);wait_columns(33);
            read_check(32'h10000404,32'hffffffff,glyph);
            read_check(32'h1000040c,32'h00ff1f03,(255<<16)|(glyph<<8)|1);
        end
        write_reg(32'h10000404,8);wait_columns(33);measure_frame(full_lit);
        check(full_lit>7000,"full brightness illuminates dwell");
        write_reg(32'h10000408,1);wait_columns(33);measure_frame(low_lit);
        one_lit=low_lit;
        check(low_lit>0 && low_lit*100<full_lit,"brightness=1 stays visible with low duty");
        write_reg(32'h10000408,0);wait_columns(33);measure_frame(zero_lit);check(zero_lit==0,"zero brightness is dark");
        write_reg(32'h10000408,128);wait_columns(33);measure_frame(low_lit);
        check(low_lit>full_lit/3 && low_lit<full_lit*2/3,"intermediate PWM brightness");
        // Mid-frame writes must not change remaining columns of the old glyph.
        wait(last_column==7);write_reg(32'h10000404,10);write_reg(32'h10000408,255);
        read_check(32'h1000040c,2,2);wait_columns(25);
        read_check(32'h1000040c,32'h00ff1f03,(255<<16)|(10<<8)|1);
        access(1,32'h10000400,2,2,1);access(1,32'h10000404,17,2,1);
        access(1,32'h10000408,256,2,1);access(1,32'h1000040c,0,2,1);
        access(1,32'h10000410,0,2,1);access(1,32'h10000405,0,2,1);
        access(1,32'h10000404,0,0,1);access(1,32'h10000404,0,1,1);
        access(0,32'h10000404,0,3,1);access(0,32'h10000403,0,2,1);
        read_check(32'h10000400,32'hffffffff,1);read_check(32'h10000404,32'hffffffff,10);
        read_check(32'h10000408,32'hffffffff,255);wait_columns(20);
        check(frame_glyph==10 && frame_enable==1 && frame_brightness==255,"illegal writes have no display side effect");
        write_reg(32'h10000400,0);wait_columns(33);measure_frame(zero_lit);check(zero_lit==0,"disabled display is dark");
        write_reg(32'h10000400,1);wait_columns(20);wait(sclk==15);reset_panel();
        write_reg(32'h10000404,13);write_reg(32'h10000400,1);wait_columns(33);wait(g==0);reset_panel();
        $display("MATRIX PASS checks=%0d frames=%0d latches=%0d full_on=%0d low1_on=%0d half_on=%0d",checks,frames,latches,full_lit,one_lit,low_lit);
        $finish;
    end
    initial begin #10000000;$display("FAIL timeout");$fatal(1);end
endmodule
