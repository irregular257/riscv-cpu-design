module ddr_pango_subsystem #(
    parameter integer UI_TIMEOUT_CYCLES=125000000,
    parameter integer CPU_TIMEOUT_CYCLES=270000000
)(
    input wire cpu_clk, reset_n,
    input wire ddr_ref_clk_p,ddr_ref_clk_n,
    input wire req_valid,req_write,
    input wire [31:0] req_addr,req_wdata,
    input wire [2:0] req_funct3,
    output wire req_ready,
    output wire [31:0] rsp_rdata,
    output wire rsp_error,busy,
    output wire ddr_ready_cpu,ddr_fault_cpu,
    output wire ddr_init_done,
    output wire mem_cs_n,mem_rst_n,mem_ck,mem_ck_n,mem_cke,
    output wire mem_ras_n,mem_cas_n,mem_we_n,mem_odt,
    output wire [14:0] mem_a,
    output wire [2:0] mem_ba,
    inout wire [3:0] mem_dqs,mem_dqs_n,
    inout wire [31:0] mem_dq,
    output wire [3:0] mem_dm
);
    wire ref_clk,ui_clk,pll_lock,phy_pll_lock,gpll_lock,rst_gpll_lock,cpd_lock;
    wire init_failed,align_error,bridge_fault,cpu_fault;
    wire [27:0] awaddr,araddr;
    wire awap,arap,awready,awvalid,wready,wlast,arready,arvalid,rlast,rvalid;
    wire [3:0] awid,awlen,wid,arid,arlen,rid;
    wire [255:0] wdata,rdata;
    wire [31:0] wstrb;
    GTP_INBUFDS refclk_inbuf(.O(ref_clk),.I(ddr_ref_clk_p),.IB(ddr_ref_clk_n));
    wire ui_usable=ddr_init_done && pll_lock && !init_failed && !align_error;
    (* ASYNC_REG="TRUE" *) reg [1:0] ready_sync,fault_sync;
    reg fatal_cpu,ever_ready;
    always @(posedge cpu_clk or negedge reset_n) begin
        if(!reset_n) begin ready_sync<=0;fault_sync<=0;fatal_cpu<=0;ever_ready<=0;end
        else begin
            ready_sync<={ready_sync[0],ui_usable};
            fault_sync<={fault_sync[0],bridge_fault | init_failed | align_error};
            if(ready_sync[1]) ever_ready<=1;
            if(fault_sync[1] || cpu_fault || (ever_ready && !ready_sync[1])) fatal_cpu<=1;
        end
    end
    assign ddr_fault_cpu=fatal_cpu | cpu_fault | fault_sync[1];
    assign ddr_ready_cpu=ready_sync[1] && !ddr_fault_cpu;
    ddr_cpu_bridge #(.UI_TIMEOUT_CYCLES(UI_TIMEOUT_CYCLES),.CPU_TIMEOUT_CYCLES(CPU_TIMEOUT_CYCLES)) u_bridge(
        .cpu_clk(cpu_clk),.cpu_rst(!reset_n),.cpu_abort(fatal_cpu),.req_valid(req_valid),.req_write(req_write),
        .req_addr(req_addr),.req_wdata(req_wdata),.req_funct3(req_funct3),
        .req_ready(req_ready),.rsp_rdata(rsp_rdata),.rsp_error(rsp_error),.busy(busy),.cpu_fault(cpu_fault),
        .ui_clk(ui_clk),.ui_rst(!reset_n),.ui_init_done(ui_usable),.ui_fault(bridge_fault),
        .axi_awaddr(awaddr),.axi_awuser_ap(awap),.axi_awuser_id(awid),.axi_awlen(awlen),
        .axi_awready(awready),.axi_awvalid(awvalid),.axi_wdata(wdata),.axi_wstrb(wstrb),
        .axi_wready(wready),.axi_wusero_id(wid),.axi_wusero_last(wlast),
        .axi_araddr(araddr),.axi_aruser_ap(arap),.axi_aruser_id(arid),.axi_arlen(arlen),
        .axi_arready(arready),.axi_arvalid(arvalid),.axi_rdata(rdata),.axi_rid(rid),
        .axi_rlast(rlast),.axi_rvalid(rvalid));
    // Debug controls match the generated example's default control words.
    ddr3_test u_ip(
        .ref_clk(ref_clk),.resetn(reset_n),.core_clk(ui_clk),.pll_lock(pll_lock),
        .phy_pll_lock(phy_pll_lock),.gpll_lock(gpll_lock),.rst_gpll_lock(rst_gpll_lock),
        .ddrphy_cpd_lock(cpd_lock),.ddr_init_done(ddr_init_done),
        .axi_awaddr(awaddr),.axi_awuser_ap(awap),.axi_awuser_id(awid),.axi_awlen(awlen),
        .axi_awready(awready),.axi_awvalid(awvalid),.axi_wdata(wdata),.axi_wstrb(wstrb),
        .axi_wready(wready),.axi_wusero_id(wid),.axi_wusero_last(wlast),
        .axi_araddr(araddr),.axi_aruser_ap(arap),.axi_aruser_id(arid),.axi_arlen(arlen),
        .axi_arready(arready),.axi_arvalid(arvalid),.axi_rdata(rdata),.axi_rid(rid),
        .axi_rlast(rlast),.axi_rvalid(rvalid),
        .apb_clk(1'b0),.apb_rst_n(1'b0),.apb_sel(1'b0),.apb_enable(1'b0),.apb_addr(8'b0),
        .apb_write(1'b0),.apb_ready(),.apb_wdata(16'b0),.apb_rdata(),
        .mem_cs_n(mem_cs_n),.mem_rst_n(mem_rst_n),.mem_ck(mem_ck),.mem_ck_n(mem_ck_n),
        .mem_cke(mem_cke),.mem_ras_n(mem_ras_n),.mem_cas_n(mem_cas_n),.mem_we_n(mem_we_n),
        .mem_odt(mem_odt),.mem_a(mem_a),.mem_ba(mem_ba),.mem_dqs(mem_dqs),
        .mem_dqs_n(mem_dqs_n),.mem_dq(mem_dq),.mem_dm(mem_dm),
        .dbg_gate_start(1'b0),.dbg_cpd_start(1'b0),.dbg_ddrphy_rst_n(1'b1),.dbg_gpll_scan_rst(1'b0),
        .samp_position_dyn_adj(1'b0),.init_samp_position_even(32'b0),.init_samp_position_odd(32'b0),
        .wrcal_position_dyn_adj(1'b0),.init_wrcal_position(32'b0),.force_read_clk_ctrl(1'b0),
        .init_slip_step(16'b0),.init_read_clk_ctrl(12'b0),
        .debug_calib_ctrl(),.dbg_slice_status(),.dbg_slice_state(),.debug_data(),
        .dbg_dll_upd_state(),.debug_gpll_dps_phase(),.dbg_rst_dps_state(),.dbg_tran_err_rst_cnt(),
        .dbg_ddrphy_init_fail(init_failed),.debug_cpd_offset_adj(1'b0),.debug_cpd_offset_dir(1'b0),
        .debug_cpd_offset(10'b0),.debug_dps_cnt_dir0(),.debug_dps_cnt_dir1(),
        .ck_dly_en(1'b0),.init_ck_dly_step(8'b0),.ck_dly_set_bin(),
        .align_error(align_error),.debug_rst_state(),.debug_cpd_state());
endmodule
