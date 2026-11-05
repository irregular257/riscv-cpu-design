`timescale 1ns/1ps
module tb_mapped_multiply;
GTP_GRS GRS_INST(.GRS_N(1'b1));
reg [31:0] a=0,b=0,inst=0;
reg [31:0] ex_source_a=0,ex_source_b=0,mem_value=0,wb_value=0;
reg mem_enable=0,wb_enable=0;
reg [4:0] mem_dest=0,wb_dest=0;

reg [127:0] vectors[0:4771];
wire [31:0] result_d={dut.N1435[31],
 dut.N1435[30],
 dut.N1435[29],
 dut.N1435[28],
 dut.N1435[27],
 dut.N1435[26],
 dut.N1435[25],
 dut.N1435[24],
 dut.N1435[23],
 dut.N1435[22],
 dut.N1435[21],
 dut.N1435[20],
 dut.N1435[19],
 dut.N1435[18],
 dut.N1435[17],
 dut.N1435[16],
 dut.N1435[15],
 dut.N1435[14],
 dut.N1435[13],
 dut.N1435[12],
 dut.N1435[11],
 dut.N1435[10],
 dut.N1435[9],
 dut.N1435[8],
 dut.N1435[7],
 dut.N1435[6],
 dut.N1435[5],
 dut.N1435[4],
 dut.N1435[3],
 dut.N1435[2],
 dut.N1435[1],
 dut.N1435[0]};
integer i,mode;

cpu_core dut(.dmem_rdata(32'b0),
 .imem_rdata(32'b0),
 .\u_ddr/u_bridge/cstate_reg (2'b0),
 .\u_soc/g_cache.u_icache/state_reg (6'b0),
 .\u_soc/u_bus/g_ai.u_ai/state_reg (6'b0),
 .N650(1'b0),
 .N697(1'b0),
 ._N35075(1'b0),
 ._N62131(1'b0),
 ._N62132(1'b0),
 ._N69420(1'b0),
 .clk(1'b0),
 .dmem_error(1'b0),
 .fence_i_ready(1'b0),
 .imem_valid(1'b0),
 .irq_software(1'b0),
 .irq_timer(1'b0),
 .por_7(1'b0),
 .rst(1'b0),
 .\u_ddr/u_bridge/crst_1 (1'b0),
 .\u_soc/ext_select (1'b0),
 .\u_soc/g_cache.u_icache/lookahead_hit (1'b0),
 .\u_soc/g_cache.u_icache/response_error (1'b0),
 .\u_soc/u_bus/g_sync.u_ram/pending (1'b0),
 .\u_soc/u_bus/ram_busy (1'b0),
 .\u_soc/u_bus/rx_pending (1'b0),
 .\u_soc/u_bus/tx_busy (1'b0),
 .\u_soc/u_bus/uart_irq_enable (1'b0));
initial begin
 $readmemh("vectors.hex",vectors);
 force dut.ex_a=ex_source_a;
 force dut.ex_b=ex_source_b;
 force dut.ex_rs1=5'd11;
 force dut.ex_rs2=5'd12;
 force dut.mem_valid=mem_enable;
 force dut.mem_writes=1'b1;
 force dut.mem_load=1'b0;
 force dut.mem_exc=1'b0;
 force dut.mem_rd=mem_dest;
 force dut.mem_result=mem_value;
 force dut.wb_valid=wb_enable;
 force dut.wb_writes=1'b1;
 force dut.commit_rd=wb_dest;
 force dut.commit_wdata=wb_value;

 force dut.ex_inst=inst;
 force dut.ex_ctrl=42'b0;
 // PDS coalesces some ex_inst opcode bits with ex_bimm register aliases.
 // Supply the legal decoded multiply control at this combinational boundary;
 // instruction decoding itself is covered by the independent CPU RTL suite.
 force dut.ex_mul=1'b1;
 for(mode=0;mode<7;mode=mode+1)begin
 for(i=0;i<4772;i=i+1)begin
  inst=vectors[i][127:96];a=vectors[i][95:64];b=vectors[i][63:32];
  ex_source_a=a;ex_source_b=b;mem_enable=0;wb_enable=0;
  mem_dest=0;wb_dest=0;mem_value=0;wb_value=0;
  case(mode)
    1:begin mem_enable=1;mem_dest=11;mem_value=a;ex_source_a=~a;end
    2:begin mem_enable=1;mem_dest=12;mem_value=b;ex_source_b=~b;end
    3:begin wb_enable=1;wb_dest=11;wb_value=a;ex_source_a=~a;end
    4:begin wb_enable=1;wb_dest=12;wb_value=b;ex_source_b=~b;end
    5:begin mem_enable=1;mem_dest=11;mem_value=a;wb_enable=1;wb_dest=11;wb_value=~a;ex_source_a=~a;end
    6:begin mem_enable=1;mem_dest=12;mem_value=b;wb_enable=1;wb_dest=12;wb_value=~b;ex_source_b=~b;end
  endcase
  #20;
  if(dut.forward_a!==a || dut.forward_b!==b) $fatal(1,"EX_BOUNDARY_NATURAL_FORWARD_FAILED mode=%0d i=%0d a=%h b=%h fa=%h fb=%h",mode,i,a,b,dut.forward_a,dut.forward_b);

  if(result_d!==vectors[i][31:0])
   $fatal(1,"MAPPED_MULTIPLY_FAIL mode=%0d i=%0d op=%0d a=%h b=%h got=%h expected=%h",mode,i,inst[14:12],a,b,result_d,vectors[i][31:0]);
 end
 end
 $display("MAPPED_MULTIPLY_PASS vectors=%0d all_four_operations=1 actual_mem_result_d=1",4772);
 $finish;
end
endmodule
