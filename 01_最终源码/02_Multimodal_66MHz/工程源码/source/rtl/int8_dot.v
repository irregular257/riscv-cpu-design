`timescale 1ns/1ps
// MMIO-controlled signed INT8 dot product using the second synchronous RAM port.
// Four parallel signed MAC lanes for aligned vectors; scalar fallback otherwise.
// X/W may be byte aligned. Each start snapshots its descriptor; CPU stores wait
// while DMA is busy, so its input arrays cannot change during a calculation.
module int8_dot #(parameter integer ADDR_WIDTH=16)(
 input wire clk,rst,req_valid,req_write,
 input wire [4:0] req_offset,
 input wire [31:0] req_wdata,
 output reg [31:0] rsp_rdata,
 output wire rsp_error, busy,
 output wire dma_en,
 output wire [31:0] dma_addr,
 input wire [31:0] dma_rdata
);
 localparam IDLE=0,READ_X=1,WAIT_X=2,READ_W=3,WAIT_W=4,MAC=5;
 reg [2:0] state;
 reg done,error;
 reg [31:0] cfg_x,cfg_w,cfg_len,cfg_bias;
 reg [31:0] xptr,wptr,left;
 reg signed [31:0] accumulator,result;
 reg signed [7:0] xbyte,wbyte;
 reg [31:0] xword,wword;
 wire packed_mode=xptr[1:0]==0 && wptr[1:0]==0 && left>=4;
 wire [31:0] step=packed_mode ? 4 : 1;
 (* use_dsp="yes" *) wire signed [15:0] product=xbyte*wbyte;
 (* use_dsp="yes" *) wire signed [15:0] product1=$signed(xword[15:8])*$signed(wword[15:8]);
 (* use_dsp="yes" *) wire signed [15:0] product2=$signed(xword[23:16])*$signed(wword[23:16]);
 (* use_dsp="yes" *) wire signed [15:0] product3=$signed(xword[31:24])*$signed(wword[31:24]);
 wire signed [16:0] sum01={product[15],product}+{product1[15],product1};
 wire signed [16:0] sum23={product2[15],product2}+{product3[15],product3};
 wire signed [17:0] packed_sum={sum01[16],sum01}+{sum23[16],sum23};
 wire signed [31:0] term=packed_mode ? {{14{packed_sum[17]}},packed_sum} : {{16{product[15]}},product};
 wire signed [31:0] next_sum=accumulator+term;
 wire [32:0] xend={1'b0,cfg_x}+{1'b0,cfg_len};
 wire [32:0] wend={1'b0,cfg_w}+{1'b0,cfg_len};
 wire bad_descriptor=cfg_len==0 || cfg_len>256 ||
     xend>(33'd1<<ADDR_WIDTH) || wend>(33'd1<<ADDR_WIDTH);
 wire known=req_offset==0 || req_offset==4 || req_offset==8 ||
     req_offset==12 || req_offset==16 || req_offset==20 || req_offset==24;
 assign busy=state!=IDLE;
 assign rsp_error=req_valid && (!known ||
     (req_write && (busy || req_offset==4 || req_offset==24)) ||
     (!req_write && req_offset==0));
 assign dma_en=state==READ_X || state==READ_W;
 assign dma_addr=state==READ_W ? wptr : xptr;
 always @(*) begin
  rsp_rdata=0;
  case(req_offset)
   4:rsp_rdata={29'b0,error,done,busy};
   8:rsp_rdata=cfg_x;
   12:rsp_rdata=cfg_w;
   16:rsp_rdata=cfg_len;
   20:rsp_rdata=cfg_bias;
   24:rsp_rdata=result;
  endcase
 end
 always @(posedge clk) begin
  if(rst)begin
   state<=IDLE;done<=0;error<=0;cfg_x<=0;cfg_w<=0;cfg_len<=0;cfg_bias<=0;
   xptr<=0;wptr<=0;left<=0;accumulator<=0;result<=0;xbyte<=0;wbyte<=0;xword<=0;wword<=0;
  end else begin
   if(req_valid && req_write && !rsp_error)begin
    case(req_offset)
     8:cfg_x<=req_wdata;
     12:cfg_w<=req_wdata;
     16:cfg_len<=req_wdata;
     20:cfg_bias<=req_wdata;
     0:begin
      if(req_wdata[1])begin done<=0;error<=0;end
      if(req_wdata[0])begin
       done<=bad_descriptor;error<=bad_descriptor;
       if(!bad_descriptor)begin
        xptr<=cfg_x;wptr<=cfg_w;left<=cfg_len;
        accumulator<=cfg_bias;state<=READ_X;
       end
      end
     end
    endcase
   end
   case(state)
    READ_X:state<=WAIT_X;
    WAIT_X:begin xword<=dma_rdata;xbyte<=dma_rdata >> {xptr[1:0],3'b000};state<=READ_W;end
    READ_W:state<=WAIT_W;
    WAIT_W:begin wword<=dma_rdata;wbyte<=dma_rdata >> {wptr[1:0],3'b000};state<=MAC;end
    MAC:begin
     accumulator<=next_sum;
     if(left==step)begin result<=next_sum;done<=1;state<=IDLE;end
     else begin xptr<=xptr+step;wptr<=wptr+step;left<=left-step;state<=READ_X;end
    end
   endcase
  end
 end
endmodule
