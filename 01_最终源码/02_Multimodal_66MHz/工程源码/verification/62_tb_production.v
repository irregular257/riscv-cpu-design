`timescale 1ns/1ps
module tb_production;
GTP_GRS GRS_INST(.GRS_N(1'b1));
matrix_display dut(.req_addr (1'b0),.req_wdata (1'b0),.N2721 (1'b0),._N61952 (1'b0),._N61961 (1'b0),._N61962 (1'b0),._N61965 (1'b0),._N62001 (1'b0),._N62003 (1'b0),._N62006 (1'b0),._N62054 (1'b0),.clk (1'b0),.por_7 (1'b0),.ready (1'b0),.req_write (1'b0),.rst (1'b0));
integer glyph,col,folded_checks=0;
reg [15:0] rows;
reg folded_bit0,bit0_with_zeros,bit0_with_ones;
initial begin
 #10;
 force dut.state_reg=6'b000001;
 force dut.shift_data=0;
 for(glyph=0;glyph<17;glyph=glyph+1)begin
  force dut.next_glyph=glyph;
  for(col=0;col<16;col=col+1)begin
   force dut.column_index=col;
   force dut.state_reg=6'b000100;
   force dut.shift_data=32'h00000000;
   #10;bit0_with_zeros=dut.N2808[1] ;
   force dut.shift_data=32'hffffffff;
   #10;bit0_with_ones=dut.N2808[1] ;
   if(bit0_with_zeros!==1'b0 || bit0_with_ones!==1'b0)
    $fatal(1,"Cannot prove folded shift bit 0 is zero: glyph=%0d col=%0d zeros=%b ones=%b",glyph,col,bit0_with_zeros,bit0_with_ones);
   folded_bit0=bit0_with_zeros;folded_checks=folded_checks+2;
   force dut.state_reg=6'b000001;
   force dut.shift_data=32'h00000000;
   #10;
   rows={dut.N2808[23] ,dut.N2808[22] ,dut.N2808[21] ,dut.N2808[20] ,dut.N2808[19] ,dut.N2808[18] ,dut.N2808[17] ,dut.N2808[16] ,dut.N2808[7] ,dut.N2808[6] ,dut.N2808[5] ,dut.N2808[4] ,dut.N2808[3] ,dut.N2808[2] ,dut.N2808[1] ,folded_bit0};
   $display("PROD_COLUMN glyph=%0d x=%0d rows=%04x",glyph,col,rows);
  end
 end
 $display("FOLDED_BIT0_CHECKS=%0d",folded_checks);
 $finish;
end
endmodule
