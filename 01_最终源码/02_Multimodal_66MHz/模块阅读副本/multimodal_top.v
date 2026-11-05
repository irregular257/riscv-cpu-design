module multimodal_top #(
    parameter integer CLK_HZ=66000000,BAUD=115200,
    parameter ROM_FILE="",
    parameter RAM_FILE=""
)(
    input wire sys_clk,rst_n,uart_rx,
    output wire uart_tx,output wire [3:0] led,
    output wire [3:0] DS_Matrix,G_Matrix,RCK_Matrix,SCLK_Matrix,SCLR_Matrix,
    input wire ddr_ref_clk_p,ddr_ref_clk_n,
    output wire mem_cs_n,mem_rst_n,mem_ck,mem_ck_n,mem_cke,
    output wire mem_ras_n,mem_cas_n,mem_we_n,mem_odt,
    output wire [14:0] mem_a,output wire [2:0] mem_ba,
    inout wire [3:0] mem_dqs,mem_dqs_n,
    inout wire [31:0] mem_dq,output wire [3:0] mem_dm
);
    wire cpu_clk,pll_locked;
    cpu_clock #(.FB_DIV(44),.OUT_DIV(18)) u_cpu_clock(
      .ref_clk(sys_clk),.reset_n(rst_n),.clk(cpu_clk),.locked(pll_locked));
    wire run_n=rst_n && pll_locked;
    reg[7:0]por=8'hff;
    always @(posedge cpu_clk or negedge run_n)
      if(!run_n)por<=8'hff;else por<={por[6:0],1'b0};
    wire rst=por[7];
    wire halted;wire [31:0] result,gpo,goe;
    reg [25:0] heartbeat;
    always @(posedge cpu_clk)if(rst)heartbeat<=0;else heartbeat<=heartbeat+1'b1;
    wire [3:0] status={result[8],heartbeat[25],halted,result==1};
    assign led=(status & ~goe[3:0]) | (gpo[3:0]&goe[3:0]);
    wire ev,ew,er,ee,dbusy,dready,dfault;
    wire [31:0] ea,ewd,erd;
    wire [2:0] ef;
    multimodal_soc #(.CLK_HZ(CLK_HZ),.BAUD(BAUD),.ROM_FILE(ROM_FILE),.RAM_FILE(RAM_FILE)) u_soc(
      .clk(cpu_clk),.rst(rst),.uart_rx(uart_rx),.uart_tx(uart_tx),.gpio_in(32'b0),.gpio_out(gpo),.gpio_oe(goe),
      .halted(halted),.test_result(result),.debug_pc(),.trap_cause(),.trap_pc(),.trap_value(),
      .commit_valid(),.commit_pc(),.commit_inst(),.commit_rd_we(),.commit_rd(),.commit_wdata(),
      .DS_Matrix(DS_Matrix),.G_Matrix(G_Matrix),.RCK_Matrix(RCK_Matrix),.SCLK_Matrix(SCLK_Matrix),.SCLR_Matrix(SCLR_Matrix),
      .ext_valid(ev),.ext_write(ew),.ext_addr(ea),.ext_wdata(ewd),.ext_funct3(ef),
      .ext_ready(er),.ext_error(ee),.ext_rdata(erd),.ddr_status({29'b0,dbusy,dfault,dready}));
    ddr_pango_subsystem u_ddr(
      .cpu_clk(cpu_clk),.reset_n(!rst),.ddr_ref_clk_p(ddr_ref_clk_p),.ddr_ref_clk_n(ddr_ref_clk_n),
      .req_valid(ev),.req_write(ew),.req_addr(ea),.req_wdata(ewd),.req_funct3(ef),
      .req_ready(er),.rsp_rdata(erd),.rsp_error(ee),.busy(dbusy),
      .ddr_ready_cpu(dready),.ddr_fault_cpu(dfault),.ddr_init_done(),
      .mem_cs_n(mem_cs_n),.mem_rst_n(mem_rst_n),.mem_ck(mem_ck),.mem_ck_n(mem_ck_n),.mem_cke(mem_cke),
      .mem_ras_n(mem_ras_n),.mem_cas_n(mem_cas_n),.mem_we_n(mem_we_n),.mem_odt(mem_odt),
      .mem_a(mem_a),.mem_ba(mem_ba),.mem_dqs(mem_dqs),.mem_dqs_n(mem_dqs_n),.mem_dq(mem_dq),.mem_dm(mem_dm));
endmodule
