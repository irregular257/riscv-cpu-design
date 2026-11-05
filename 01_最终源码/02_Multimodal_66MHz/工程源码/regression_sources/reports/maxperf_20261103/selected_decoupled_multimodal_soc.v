`timescale 1ns/1ps
// RV32I + Zicsr/Zifencei, two-way I/D caches, UART/INT8 DMA, GPIO and CLINT.
module multimodal_soc #(
    parameter integer CLK_HZ=27000000, BAUD=115200,
    parameter integer CACHE_ENABLE=1,
    parameter ROM_FILE="", RAM_FILE=""
)(
    input wire clk,rst,uart_rx,
    output wire uart_tx,
    input wire [31:0] gpio_in,
    output wire [31:0] gpio_out,gpio_oe,
    output wire halted,
    output wire [31:0] test_result,debug_pc,trap_cause,trap_pc,trap_value,
    output wire commit_valid,
    output wire [31:0] commit_pc,commit_inst,
    output wire commit_rd_we,
    output wire [4:0] commit_rd,
    output wire [31:0] commit_wdata,
    output wire [3:0] DS_Matrix,G_Matrix,RCK_Matrix,SCLK_Matrix,SCLR_Matrix,
    output wire ext_valid,ext_write,
    output wire [31:0] ext_addr,ext_wdata,
    output wire [2:0] ext_funct3,
    input wire ext_ready,ext_error,
    input wire [31:0] ext_rdata,ddr_status
);
    wire iv,ir,ie,dv,dw,dr,de,fv,fr,fiv,fir;
    wire [31:0] ia,idata,da,wd,rd;
    wire [2:0] df;
    wire irq_timer,irq_software,irq_gpio,irq_uart;
    wire data_preview_valid;
    wire [31:0] data_preview_addr;
    cpu_core #(.BRANCH_PREDICT(1),.BTB_INDEX_BITS(7),.PRIVILEGED(1),
               .DIRECT_JUMP_PREDICT(1),.RETURN_PREDICT(1),.ZMMUL(1)) u_core(
      .clk(clk),.rst(rst),.imem_valid(iv),.imem_addr(ia),.imem_ready(ir),.imem_rdata(idata),.imem_error(ie),
      .dmem_valid(dv),.dmem_write(dw),.dmem_addr(da),.dmem_wdata(wd),.dmem_funct3(df),
      .dmem_ready(dr),.dmem_rdata(rd),.dmem_error(de),.fence_valid(fv),.fence_ready(fr),
      .fence_i_valid(fiv),.fence_i_ready(fir),
      .irq_timer(irq_timer),.irq_software(irq_software),.irq_external(irq_gpio|irq_uart),
      .halted(halted),
      .debug_pc(debug_pc),.trap_cause(trap_cause),.trap_pc(trap_pc),.trap_value(trap_value),
      .commit_valid(commit_valid),.commit_pc(commit_pc),.commit_inst(commit_inst),
      .commit_rd_we(commit_rd_we),.commit_rd(commit_rd),.commit_wdata(commit_wdata),
      .redirect_valid(),.redirect_target(),
      .dmem_preview_valid(data_preview_valid),.dmem_preview_addr(data_preview_addr));
    wire biv,bir,bie,biw;
    wire [31:0] bia,biwd,bird;
    wire [2:0] bif;
    wire bdr,bde,bdv,bdw;
    wire [31:0] bda,bdwd,bdrd;
    wire [2:0] bdf;
    wire ibusy,dbusy,idone;
    wire [31:0] ihit,imiss,ifill,iwrites,dhit,dmiss,dfill,dwrites;
    reg fi_started,fi_done;
    wire invalidate_i=fiv && !fi_started;
    always @(posedge clk) begin
      if(rst || !fiv) begin fi_started<=0;fi_done<=0;end
      else begin
        if(!fi_started)fi_started<=1;
        if(idone)fi_done<=1;
      end
    end
    assign fir=CACHE_ENABLE ? fi_done : 1'b1;
    wire data_ram=da[31:16]==0;
    wire periph_select=da[31:16]==16'h0200 || da[31:8]==24'h100002;
    wire stats_select=da>=32'h10000300 && da<=32'h1000031c;
    wire matrix_select=da[31:4]==28'h1000040;
    wire ddr_status_select=da[31:4]==28'h1000050;
    wire ext_select=da[31:30]==2'b10;
    assign ext_valid=dv && ext_select;
    assign ext_write=dw;assign ext_addr=da;assign ext_wdata=wd;assign ext_funct3=df;
    wire direct_bus=dv && !data_ram && !periph_select && !stats_select && !matrix_select && !ddr_status_select && !ext_select;
    wire mr,me;wire [31:0]md;
    matrix_display #(.CLK_HZ(CLK_HZ)) u_matrix(
      .clk(clk),.rst(rst),.req_valid(dv && matrix_select),.req_write(dw),
      .req_addr(da),.req_wdata(wd),.req_funct3(df),.ready(mr),.error(me),.rdata(md),
      .DS_Matrix(DS_Matrix),.G_Matrix(G_Matrix),.RCK_Matrix(RCK_Matrix),
      .SCLK_Matrix(SCLK_Matrix),.SCLR_Matrix(SCLR_Matrix));
    wire cr,ce;wire[31:0]cd;
    generate if(CACHE_ENABLE)begin:g_cache
      cache_2way #(.READ_ONLY(1),.SETS(256),.ADDR_WIDTH(16)) u_icache(
       .clk(clk),.rst(rst),.req_valid(iv),.req_write(1'b0),.req_addr(ia),.req_wdata(32'b0),.req_funct3(3'd2),
       .ready(ir),.error(ie),.rdata(idata),.bf_req_valid(biv),.bf_req_write(biw),.bf_req_addr(bia),.bf_req_wdata(biwd),.bf_req_funct3(bif),
       .bf_ready(bir),.bf_error(bie),.bf_rdata(bird),.invalidate(invalidate_i),.invalidate_done(idone),.busy(ibusy),
       .hit_count(ihit),.miss_count(imiss),.refill_word_count(ifill),.backend_write_count(iwrites));
      cache_2way #(.READ_ONLY(0),.SETS(128),.ADDR_WIDTH(16),.DATA_PREVIEW(1),.WRITE_RESPONSE_FORWARD(1)) u_dcache(
       .clk(clk),.rst(rst),.req_valid(dv && data_ram),.req_write(dw),.req_addr(da),.req_wdata(wd),.req_funct3(df),
       .ready(cr),.error(ce),.rdata(cd),.bf_req_valid(bdv),.bf_req_write(bdw),.bf_req_addr(bda),.bf_req_wdata(bdwd),.bf_req_funct3(bdf),
       .bf_ready(bdr),.bf_error(bde),.bf_rdata(bdrd),.invalidate(1'b0),.invalidate_done(),.busy(dbusy),
       .preview_valid(data_preview_valid && data_preview_addr[31:16]==0),.preview_addr(data_preview_addr),
       .hit_count(dhit),.miss_count(dmiss),.refill_word_count(dfill),.backend_write_count(dwrites));
    end else begin:g_no_cache
      assign biv=iv;assign bia=ia;assign biw=0;assign biwd=0;assign bif=2;
      assign ir=bir;assign ie=bie;assign idata=bird;
      assign bdv=dv && data_ram;assign bda=da;assign bdw=dw;assign bdwd=wd;assign bdf=df;
      assign cr=bdr;assign ce=bde;assign cd=bdrd;
      assign ibusy=0;assign dbusy=0;assign idone=0;
      assign ihit=0;assign imiss=0;assign ifill=0;assign iwrites=0;
      assign dhit=0;assign dmiss=0;assign dfill=0;assign dwrites=0;
    end endgenerate
    // The sequential line-fill request stream reuses the ROM's clocked
    // prefetch port: consecutive acknowledged words need no controller gap.
    rom_sync #(.ADDR_WIDTH(16),.PREFETCH(1),.INIT_FILE(ROM_FILE)) u_rom(
      .clk(clk),.rst(rst),.req_valid(biv),.addr(bia),.ready(bir),.error(bie),.inst(bird));
    wire bus_valid=bdv|direct_bus;
    wire bus_write=bdv ? bdw : dw;
    wire [31:0] bus_addr=bdv ? bda : da;
    wire [31:0] bus_wdata=bdv ? bdwd : wd;
    wire [2:0] bus_size=bdv ? bdf : df;
    wire bus_ready,bus_error;wire[31:0]bus_rdata;
    data_bus #(.CLK_HZ(CLK_HZ),.BAUD(BAUD),.RAM_ADDR_WIDTH(16),.SYNC_RAM(1),.AI_ENABLE(1),.RAM_FILE(RAM_FILE)) u_bus(
      .clk(clk),.rst(rst),.req_valid(bus_valid),.req_write(bus_write),.req_addr(bus_addr),.req_wdata(bus_wdata),.req_funct3(bus_size),
      .req_ready(bus_ready),.rsp_rdata(bus_rdata),.rsp_error(bus_error),.fence_ready(fr),
      .uart_rx_i(uart_rx),.uart_tx_o(uart_tx),.test_result(test_result),.irq_uart(irq_uart));
    assign bdr=bus_ready;assign bde=bus_error;assign bdrd=bus_rdata;
    wire pr,pe;wire[31:0]pd;
    soc_peripherals u_peripherals(
      .clk(clk),.rst(rst),.req_valid(dv && periph_select),.req_write(dw),.req_addr(da),.req_wdata(wd),.req_funct3(df),
      .ready(pr),.error(pe),.rdata(pd),.gpio_in(gpio_in),.gpio_out(gpio_out),.gpio_oe(gpio_oe),
      .irq_timer(irq_timer),.irq_software(irq_software),.irq_gpio(irq_gpio));
    reg [31:0] stats;
    always @(*) case(da[4:2])
      0:stats=ihit;1:stats=imiss;2:stats=ifill;3:stats=iwrites;
      4:stats=dhit;5:stats=dmiss;6:stats=dfill;default:stats=dwrites;
    endcase
    assign dr=ext_select ? ext_ready : matrix_select ? mr : ddr_status_select ? !rst : data_ram ? cr : periph_select ? pr : stats_select ? !rst : bus_ready;
    assign de=ext_select ? ext_error : matrix_select ? me : ddr_status_select ? (dw || df!=2 || da[3:0]!=0) : data_ram ? ce : periph_select ? pe : stats_select ? (dw || df!=2 || da[1:0]!=0) : bus_error;
    assign rd=ext_select ? ext_rdata : matrix_select ? md : ddr_status_select ? ddr_status : data_ram ? cd : periph_select ? pd : stats_select ? stats : bus_rdata;
endmodule
