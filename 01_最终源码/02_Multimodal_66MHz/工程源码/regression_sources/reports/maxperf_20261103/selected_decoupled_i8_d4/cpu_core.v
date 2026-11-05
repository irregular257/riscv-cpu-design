`timescale 1ns / 1ps
// Five-stage, in-order RV32I with optional Zmmul: IF -> ID -> EX -> MEM -> WB.
// Completion buses: hold valid/address/payload until valid && ready.
// PRIVILEGED=0 preserves the original fatal-trap execution environment.
// PRIVILEGED=1 provides M-mode CSR/trap/interrupt entry and return.
module cpu_core #(parameter integer BRANCH_PREDICT=0, parameter integer BTB_INDEX_BITS=4,
                  parameter integer PRIVILEGED=0, parameter integer STORE_DATA_BYPASS=1,
                  parameter integer DIRECT_JUMP_PREDICT=0, parameter integer RETURN_PREDICT=0,
                  parameter integer ZMMUL=0) (
    input wire clk, rst,
    input wire irq_timer, irq_software, irq_external,
    output wire imem_valid,
    output wire [31:0] imem_addr,
    input wire imem_ready,
    input wire [31:0] imem_rdata,
    input wire imem_error,
    output wire dmem_valid, dmem_write,
    output wire [31:0] dmem_addr, dmem_wdata,
    output wire [2:0] dmem_funct3,
    input wire dmem_ready,
    input wire [31:0] dmem_rdata,
    input wire dmem_error,
    output wire fence_valid,
    input wire fence_ready,
    output wire fence_i_valid,
    input wire fence_i_ready,
    output reg halted,
    output reg [31:0] trap_cause, trap_pc, trap_value,
    output wire [31:0] debug_pc,
    output wire commit_valid,
    output wire [31:0] commit_pc, commit_inst,
    output wire commit_rd_we,
    output wire [4:0] commit_rd,
    output wire [31:0] commit_wdata,
    output wire redirect_valid,
    output wire [31:0] redirect_target,
    output wire dmem_preview_valid,
    output wire [31:0] dmem_preview_addr
);

    // IF：独立保存尚未应答的取指请求；跳转时标记丢弃，不能中途改地址。
    wire [31:0] pc;
    reg if_pending, if_kill;
    reg [31:0] if_req_pc;

    // IF/ID 级间寄存器：valid=0 表示气泡，不能引发任何架构副作用。
    reg id_valid, id_fetch_error, id_pred_taken;
    reg [31:0] id_pc, id_inst, id_pred_next;
    wire [4:0] id_rs1, id_rs2, id_rd, id_dec_rs1, id_dec_rs2, id_dec_rd;
    wire id_mul=ZMMUL && id_inst[6:0]==7'h33 &&
        id_inst[31:25]==7'h01 && !id_inst[14];
    wire id_csr=PRIVILEGED && id_inst[6:0]==7'h73 &&
        (id_inst[14:12]==1 || id_inst[14:12]==2 || id_inst[14:12]==3 ||
         id_inst[14:12]==5 || id_inst[14:12]==6 || id_inst[14:12]==7);
    wire id_system=PRIVILEGED && (id_inst[6:0]==7'h73 ||
        (id_inst[6:0]==7'h0f && id_inst[14:12]==1));
    assign id_rs1=id_mul ? id_inst[19:15] : id_csr ? (id_inst[14] ? 5'd0 : id_inst[19:15]) : id_dec_rs1;
    assign id_rs2=id_mul ? id_inst[24:20] : id_dec_rs2;
    assign id_rd=(id_mul || id_csr) ? id_inst[11:7] : id_dec_rd;
    wire [31:0] id_i,id_s,id_b,id_u,id_j,rf_a,rf_b;
    wire id_SLLI, id_SRLI, id_SRAI, id_ADD, id_SUB, id_SLL, id_SLT, id_SLTU, id_XOR, id_SRL, id_SRA, id_OR, id_AND, id_JALR, id_LB, id_LH, id_LW, id_LBU, id_LHU, id_ADDI, id_SLTI, id_SLTIU, id_XORI, id_ORI, id_ANDI, id_SB, id_SH, id_SW, id_BEQ, id_BNE, id_BLT, id_BGE, id_BLTU, id_BGEU, id_LUI, id_AUIPC, id_JAL, id_FENCE, id_FENCETSO, id_PAUSE, id_ECALL, id_EBREAK;
    wire [41:0] id_ctrl = {id_SLLI, id_SRLI, id_SRAI, id_ADD, id_SUB, id_SLL, id_SLT, id_SLTU, id_XOR, id_SRL, id_SRA, id_OR, id_AND, id_JALR, id_LB, id_LH, id_LW, id_LBU, id_LHU, id_ADDI, id_SLTI, id_SLTIU, id_XORI, id_ORI, id_ANDI, id_SB, id_SH, id_SW, id_BEQ, id_BNE, id_BLT, id_BGE, id_BLTU, id_BGEU, id_LUI, id_AUIPC, id_JAL, id_FENCE, id_FENCETSO, id_PAUSE, id_ECALL, id_EBREAK};
    decoder u_decoder (
        .inst(id_inst),.rs1(id_dec_rs1),.rs2(id_dec_rs2),.rd(id_dec_rd),
        .imm_i(id_i),.imm_s(id_s),.imm_b(id_b),.imm_u(id_u),.imm_j(id_j),
        .SLLI(id_SLLI),
        .SRLI(id_SRLI),
        .SRAI(id_SRAI),
        .ADD(id_ADD),
        .SUB(id_SUB),
        .SLL(id_SLL),
        .SLT(id_SLT),
        .SLTU(id_SLTU),
        .XOR(id_XOR),
        .SRL(id_SRL),
        .SRA(id_SRA),
        .OR(id_OR),
        .AND(id_AND),
        .JALR(id_JALR),
        .LB(id_LB),
        .LH(id_LH),
        .LW(id_LW),
        .LBU(id_LBU),
        .LHU(id_LHU),
        .ADDI(id_ADDI),
        .SLTI(id_SLTI),
        .SLTIU(id_SLTIU),
        .XORI(id_XORI),
        .ORI(id_ORI),
        .ANDI(id_ANDI),
        .SB(id_SB),
        .SH(id_SH),
        .SW(id_SW),
        .BEQ(id_BEQ),
        .BNE(id_BNE),
        .BLT(id_BLT),
        .BGE(id_BGE),
        .BLTU(id_BLTU),
        .BGEU(id_BGEU),
        .LUI(id_LUI),
        .AUIPC(id_AUIPC),
        .JAL(id_JAL),
        .FENCE(id_FENCE),
        .FENCETSO(id_FENCETSO),
        .PAUSE(id_PAUSE),
        .ECALL(id_ECALL),
        .EBREAK(id_EBREAK)
    );

    // ID/EX：保存操作数、控制信号和该条指令自己的 PC。
    reg ex_valid, ex_fetch_error, ex_pred_taken;
    reg [31:0] ex_pc,ex_inst,ex_a,ex_b,ex_i,ex_s,ex_bimm,ex_u,ex_j,ex_pred_next;
    reg [4:0] ex_rs1,ex_rs2,ex_rd;
    reg [41:0] ex_ctrl;
    wire SLLI, SRLI, SRAI, ADD, SUB, SLL, SLT, SLTU, XOR, SRL, SRA, OR, AND, JALR, LB, LH, LW, LBU, LHU, ADDI, SLTI, SLTIU, XORI, ORI, ANDI, SB, SH, SW, BEQ, BNE, BLT, BGE, BLTU, BGEU, LUI, AUIPC, JAL, FENCE, FENCETSO, PAUSE, ECALL, EBREAK;
    assign {SLLI, SRLI, SRAI, ADD, SUB, SLL, SLT, SLTU, XOR, SRL, SRA, OR, AND, JALR, LB, LH, LW, LBU, LHU, ADDI, SLTI, SLTIU, XORI, ORI, ANDI, SB, SH, SW, BEQ, BNE, BLT, BGE, BLTU, BGEU, LUI, AUIPC, JAL, FENCE, FENCETSO, PAUSE, ECALL, EBREAK} = ex_ctrl;

    // EX/MEM：访存等待期间保持地址、宽度、方向和 Store 数据。
    reg mem_valid,mem_load,mem_store,mem_fence,mem_writes,mem_exc;
    reg mem_csr, mem_mret, mem_wfi, mem_fence_i, mem_serial;
    reg [31:0] mem_csr_source, mem_next_pc;
    reg [31:0] mem_pc,mem_inst,mem_result,mem_addr,mem_store_data;
    reg [31:0] mem_cause,mem_tval;
    reg [4:0] mem_rd;

    // MEM/WB：统一在此写回；MEM 等待时，较早的 WB 指令仍只提交一次。
    reg wb_valid,wb_writes,wb_counter_write;
    reg [31:0] wb_pc,wb_inst,wb_result,wb_mem_rdata,wb_next_pc;
    reg [4:0] wb_rd;

    wire ex_load = LB || LH || LW || LBU || LHU;
    wire ex_mul=ZMMUL && ex_inst[6:0]==7'h33 &&
        ex_inst[31:25]==7'h01 && !ex_inst[14];
    wire ex_csr=PRIVILEGED && ex_inst[6:0]==7'h73 &&
        (ex_inst[14:12]==1 || ex_inst[14:12]==2 || ex_inst[14:12]==3 ||
         ex_inst[14:12]==5 || ex_inst[14:12]==6 || ex_inst[14:12]==7);
    wire ex_mret=PRIVILEGED && ex_inst==32'h30200073;
    wire ex_wfi=PRIVILEGED && ex_inst==32'h10500073;
    wire ex_fence_i=PRIVILEGED && ex_inst[6:0]==7'h0f && ex_inst[14:12]==1;
    wire ex_serial=PRIVILEGED && (ex_inst[6:0]==7'h73 || ex_fence_i);
    wire ex_csr_write=ex_inst[13:12]==1 || ex_inst[19:15]!=0;
    wire ex_store = SB || SH || SW;
    wire ex_mem = ex_load || ex_store;
    wire ex_fence = FENCE || FENCETSO;
    wire ex_writes = ADD || SUB || SLL || SLT || SLTU || XOR || SRL || SRA || OR || AND
        || ADDI || SLTI || SLTIU || XORI || ORI || ANDI || SLLI || SRLI || SRAI
        || LUI || AUIPC || JAL || JALR || ex_load || ex_csr || ex_mul;
    wire known = (|ex_ctrl) || ex_csr || ex_mret || ex_wfi || ex_fence_i || ex_mul;

    // 前递优先最近的写者：MEM > WB > 原操作数；Load 地址不能当前递数据。
    wire mem_forward = mem_valid && mem_writes && !mem_load && !mem_exc && mem_rd!=0;
    wire wb_forward = wb_valid && wb_writes && wb_rd!=0;
    wire [31:0] forward_a = ex_rs1==0 ? 32'd0 :
        (mem_forward && mem_rd==ex_rs1) ? mem_result :
        (wb_forward && wb_rd==ex_rs1) ? wb_result : ex_a;
    wire [31:0] forward_b = ex_rs2==0 ? 32'd0 :
        (mem_forward && mem_rd==ex_rs2) ? mem_result :
        (wb_forward && wb_rd==ex_rs2) ? wb_result : ex_b;
    // One signed 33x33 product implements all four Zmmul operations. Extending
    // an unsigned operand with zero preserves values >= 0x80000000. MUL only
    // uses bits[31:0], so zero extension is also correct for its signed inputs.
    // The product joins the existing EX/MEM register and normal forwarding;
    // no additional transaction can be started or duplicated during a stall.
    wire signed [32:0] mul_a={((ex_inst[13:12]==2'b01 || ex_inst[13:12]==2'b10) && forward_a[31]),forward_a};
    wire signed [32:0] mul_b={(ex_inst[13:12]==2'b01 && forward_b[31]),forward_b};
    wire signed [65:0] mul_product=mul_a * mul_b;
    wire [31:0] multiply_result=ex_inst[13:12]==2'b00 ? mul_product[31:0] : mul_product[63:32];
    // WB -> ID 旁路：消除同一上升沿写寄存器与读取旧值之间的冲突。
    wire [31:0] id_a = (commit_rd_we && wb_rd==id_rs1) ? wb_result : rf_a;
    wire [31:0] id_bdata = (commit_rd_we && wb_rd==id_rs2) ? wb_result : rf_b;
    wire [31:0] alu_result,alu_addr,branch_target;
    wire branch_taken;
    alu u_alu (
        .pc(ex_pc),.rdata1(forward_a),.rdata2(forward_b),
        .imm_i(ex_i),.imm_s(ex_s),.imm_b(ex_bimm),.imm_u(ex_u),.imm_j(ex_j),
        .wdata(alu_result),.mem_addr(alu_addr),
        .branch_taken(branch_taken),.branch_target(branch_target),
        .SLLI(SLLI),
        .SRLI(SRLI),
        .SRAI(SRAI),
        .ADD(ADD),
        .SUB(SUB),
        .SLL(SLL),
        .SLT(SLT),
        .SLTU(SLTU),
        .XOR(XOR),
        .SRL(SRL),
        .SRA(SRA),
        .OR(OR),
        .AND(AND),
        .JALR(JALR),
        .LB(LB),
        .LH(LH),
        .LW(LW),
        .LBU(LBU),
        .LHU(LHU),
        .ADDI(ADDI),
        .SLTI(SLTI),
        .SLTIU(SLTIU),
        .XORI(XORI),
        .ORI(ORI),
        .ANDI(ANDI),
        .SB(SB),
        .SH(SH),
        .SW(SW),
        .BEQ(BEQ),
        .BNE(BNE),
        .BLT(BLT),
        .BGE(BGE),
        .BLTU(BLTU),
        .BGEU(BGEU),
        .LUI(LUI),
        .AUIPC(AUIPC),
        .JAL(JAL),
        .FENCE(FENCE),
        .FENCETSO(FENCETSO),
        .PAUSE(PAUSE),
        .ECALL(ECALL),
        .EBREAK(EBREAK)
    );

    wire data_misaligned = ex_mem &&
        (((LH || LHU || SH) && alu_addr[0]) ||
         ((LW || SW) && alu_addr[1:0]!=0));
    wire [31:0] csr_read_data, csr_trap_vector, csr_return_pc, csr_irq_cause;
    wire csr_illegal, csr_irq_pending, csr_wake_pending;
    reg [31:0] architectural_next_pc;
    reg wfi_sleep;
    reg ex_exc;
    reg [31:0] ex_cause,ex_tval;
    always @(*) begin
        ex_exc=1'b0; ex_cause=0; ex_tval=0;
        if (ex_pc[1:0]!=0) begin ex_exc=1; ex_cause=0; ex_tval=ex_pc; end
        else if (ex_fetch_error) begin ex_exc=1; ex_cause=1; ex_tval=ex_pc; end
        else if (!known || (ex_csr && csr_illegal)) begin ex_exc=1; ex_cause=2; ex_tval=ex_inst; end
        else if (EBREAK) begin ex_exc=1; ex_cause=3; ex_tval=ex_pc; end
        else if (ECALL) begin ex_exc=1; ex_cause=11; ex_tval=0; end
        else if (branch_taken && branch_target[1:0]!=0) begin
            ex_exc=1; ex_cause=0; ex_tval=branch_target;
        end else if (data_misaligned) begin
            ex_exc=1; ex_cause=ex_store ? 6 : 4; ex_tval=alu_addr;
        end
    end

    assign dmem_valid = !rst && !halted && mem_valid && !mem_exc && (mem_load || mem_store);
    assign dmem_write = mem_store;
    assign dmem_addr = mem_addr;
    assign dmem_wdata = mem_store_data;
    assign dmem_funct3 = mem_inst[14:12];
    assign fence_valid = !rst && !halted && mem_valid && !mem_exc && (mem_fence || mem_fence_i);
    assign fence_i_valid = !rst && !halted && mem_valid && !mem_exc && mem_fence_i && fence_ready;
    wire mem_finish = !rst && !halted && mem_valid &&
        (mem_exc || (((!mem_load && !mem_store) || dmem_ready) &&
                    (!(mem_fence || mem_fence_i) || fence_ready) &&
                    (!mem_fence_i || (fence_i_ready === 1'b1))));
    wire bus_fault = dmem_valid && dmem_ready && dmem_error;
    wire trap_fire = mem_finish && (mem_exc || bus_fault);
    wire irq_drain=PRIVILEGED && csr_irq_pending;
    // Do not interrupt a started data transaction. Stop new fetches, retire all
    // already-issued instructions, and enter only at an empty pipeline boundary.
    wire irq_fire=PRIVILEGED && !rst && !halted && csr_irq_pending &&
        !id_valid && !ex_valid && !mem_valid && !wb_valid;
    wire mret_fire=mem_finish && !trap_fire && mem_mret;
    wire fence_i_fire=mem_finish && !trap_fire && mem_fence_i;
    wire wfi_fire=mem_finish && !trap_fire && mem_wfi;
    wire privileged_redirect=PRIVILEGED && (trap_fire || irq_fire || mret_fire || fence_i_fire || wfi_fire);
    wire [31:0] entry_cause=trap_fire ? (mem_exc ? mem_cause : (mem_store ? 32'd7 : 32'd5)) : csr_irq_cause;
    wire [31:0] entry_pc=trap_fire ? mem_pc : architectural_next_pc;
    wire [31:0] entry_value=trap_fire ? (mem_exc ? mem_tval : mem_addr) : 32'd0;
    wire mem_allow = !mem_valid || mem_finish;
    wire ex_fire = !rst && !halted && ex_valid && mem_allow && !trap_fire;
    // A read-only hint for the synchronous D-cache RAM. It never starts a
    // backend transaction or changes architectural state before MEM validation.
    assign dmem_preview_valid=ex_fire && !ex_exc && ex_load;
    assign dmem_preview_addr=alu_addr;
    // MEM 等待时冻结 ID；即便 EX 是气泡，也不能提前放行 load 的使用者。
    wire ex_allow = mem_allow && (!ex_valid || ex_fire);
    // Store data is not needed by the EX address adder. When only rs2 depends
    // on a load, capture the completed load directly into the store payload.
    // Address/ALU/branch dependencies retain the usual load-use bubble; there
    // is deliberately no dmem_rdata -> EX ALU combinational forwarding path.
    wire id_store_data_late=STORE_DATA_BYPASS && (id_SB || id_SH || id_SW);
    wire load_use_stall = id_valid && ex_valid && ex_load && ex_rd!=0 &&
        (id_rs1==ex_rd || (id_rs2==ex_rd && !id_store_data_late));
    wire store_load_forward=STORE_DATA_BYPASS && ex_store && mem_valid && mem_load &&
        mem_writes && !mem_exc && mem_finish && !bus_fault && mem_rd!=0 && mem_rd==ex_rs2;
    wire [31:0] store_data=store_load_forward ? dmem_rdata : forward_b;
    wire [31:0] ex_actual_next=branch_taken ? branch_target : ex_pc+32'd4;
    wire branch_redirect = ex_fire && !ex_exc &&
        (BRANCH_PREDICT ? ((ex_pred_taken != branch_taken) ||
            (ex_pred_taken && branch_taken && ex_pred_next != branch_target)) : branch_taken);
    assign redirect_valid=privileged_redirect || branch_redirect;
    assign redirect_target=privileged_redirect ?
        ((trap_fire || irq_fire) ? csr_trap_vector : mret_fire ? csr_return_pc : mem_pc+32'd4) :
        (BRANCH_PREDICT ? ex_actual_next : branch_target);
    wire serial_inflight=PRIVILEGED && ((ex_valid && ex_serial) || (mem_valid && mem_serial));
    wire serial_older_busy=id_system && (ex_valid || mem_valid || wb_valid);
    wire id_fire = !rst && !halted && id_valid && ex_allow && !load_use_stall &&
        !redirect_valid && !trap_fire && !serial_inflight && !serial_older_busy && !wfi_sleep;
    wire id_allow = !id_valid || id_fire;

    assign imem_valid = !rst && (if_pending ||
        (!halted && !redirect_valid && !trap_fire && !irq_drain && !wfi_sleep && id_allow));
    assign imem_addr = if_pending ? if_req_pc : pc;
    wire fetch_fire = imem_valid && imem_ready;
    wire fetch_keep = fetch_fire && !if_kill && !redirect_valid && !trap_fire && !halted && !irq_drain && !wfi_sleep;
    wire [31:0] csr_source=mem_csr_source;
    wire [31:0] csr_write_data=mem_inst[13:12]==1 ? csr_source :
        mem_inst[13:12]==2 ? csr_read_data | csr_source : csr_read_data & ~csr_source;
    wire mem_csr_write=mem_inst[13:12]==1 || mem_inst[19:15]!=0;
    generate if(PRIVILEGED) begin:g_machine
        machine_csr u_csr (
            .clk(clk),.rst(rst),
            .read_addr(mem_valid && mem_csr ? mem_inst[31:20] : ex_inst[31:20]),
            .read_write_intent(ex_csr && ex_csr_write),
            .read_data(csr_read_data),.illegal(csr_illegal),
            .write_valid(mem_finish && !trap_fire && mem_csr && mem_csr_write),
            .write_addr(mem_inst[31:20]),.write_data(csr_write_data),
            // A CSR write to minstret replaces its implicit increment for that
            // same instruction, although it remains visible on commit_valid.
            .retire(commit_valid && !wb_counter_write),
            .irq_timer(irq_timer),.irq_software(irq_software),.irq_external(irq_external),
            .irq_pending(csr_irq_pending),.irq_cause(csr_irq_cause),.wake_pending(csr_wake_pending),
            .trap_enter(trap_fire || irq_fire),.entry_pc(entry_pc),.entry_cause(entry_cause),.entry_value(entry_value),
            .mret(mret_fire),.trap_vector(csr_trap_vector),.return_pc(csr_return_pc)
        );
    end else begin:g_no_machine
        assign csr_read_data=0; assign csr_illegal=0; assign csr_irq_pending=0;
        assign csr_irq_cause=0; assign csr_wake_pending=0; assign csr_trap_vector=0; assign csr_return_pc=0;
    end endgenerate
    always @(posedge clk) begin
        if(rst) begin architectural_next_pc<=0; wfi_sleep<=0; end
        else begin
            if(commit_valid) architectural_next_pc<=wb_next_pc;
            if(privileged_redirect) architectural_next_pc<=redirect_target;
            if(wfi_fire) wfi_sleep<=1;
            else if(csr_wake_pending || (PRIVILEGED && (trap_fire || irq_fire || mret_fire))) wfi_sleep<=0;
        end
    end
    localparam integer BTB_ENTRIES=1<<BTB_INDEX_BITS;
    reg [BTB_ENTRIES-1:0] btb_valid;
    reg [29-BTB_INDEX_BITS:0] btb_tag[0:BTB_ENTRIES-1];
    reg [31:0] btb_target[0:BTB_ENTRIES-1];
    reg [1:0] btb_counter[0:BTB_ENTRIES-1];
    wire [BTB_INDEX_BITS-1:0] fetch_index=imem_addr[BTB_INDEX_BITS+1:2];
    wire [BTB_INDEX_BITS-1:0] train_index=ex_pc[BTB_INDEX_BITS+1:2];
    wire predict_hit=BRANCH_PREDICT && btb_valid[fetch_index] &&
        btb_tag[fetch_index]==imem_addr[31:BTB_INDEX_BITS+2];
    // Decode a completed fetched JAL directly, avoiding BTB cold/conflict misses.
    // These are prediction hints only: EX still verifies every target and flushes
    // a wrong prediction through the existing precise redirect machinery.
    wire direct_jump=BRANCH_PREDICT && DIRECT_JUMP_PREDICT && !imem_error &&
        imem_rdata[6:0]==7'h6f;
    wire [31:0] direct_target=imem_addr+
        {{11{imem_rdata[31]}},imem_rdata[31],imem_rdata[19:12],
         imem_rdata[20],imem_rdata[30:21],1'b0};
    // Update the small return stack only on resolved, non-faulting EX controls.
    // No speculative stack updates means wrong-path fetches cannot corrupt it.
    // Overflow discards the oldest hint, underflow falls back to the BTB.
    reg [31:0] return_stack[0:15];
    reg [4:0] return_count;
    wire return_hint=BRANCH_PREDICT && RETURN_PREDICT && !imem_error &&
        (imem_rdata==32'h00008067 || imem_rdata==32'h00028067) && return_count!=0;
    wire ex_link_rd=ex_rd==5'd1 || ex_rd==5'd5;
    wire ex_link_rs1=ex_rs1==5'd1 || ex_rs1==5'd5;
    wire return_push=(JAL || JALR) && ex_link_rd;
    wire return_pop=JALR && ex_link_rs1 && (!ex_link_rd || ex_rd!=ex_rs1);
    integer return_index;
    always @(posedge clk) begin
        if(rst || privileged_redirect) return_count<=0;
        else if(BRANCH_PREDICT && RETURN_PREDICT && ex_fire && !ex_exc) begin
            if(return_push && return_pop) begin
                return_stack[0]<=ex_pc+32'd4;
                if(return_count==0) return_count<=1;
            end else if(return_push) begin
                return_stack[0]<=ex_pc+32'd4;
                for(return_index=1;return_index<16;return_index=return_index+1)
                    return_stack[return_index]<=return_stack[return_index-1];
                if(return_count<16) return_count<=return_count+1'b1;
            end else if(return_pop && return_count!=0) begin
                for(return_index=0;return_index<15;return_index=return_index+1)
                    return_stack[return_index]<=return_stack[return_index+1];
                return_count<=return_count-1'b1;
            end
        end
    end
    wire predicted_taken=direct_jump || return_hint || (predict_hit && btb_counter[fetch_index][1]);
    wire [31:0] predicted_next=direct_jump ? direct_target :
        return_hint ? return_stack[0] :
        predicted_taken ? btb_target[fetch_index] : imem_addr+32'd4;
    wire predict_jump=fetch_keep && predicted_taken;
    wire ex_control=BEQ || BNE || BLT || BGE || BLTU || BGEU || JAL || JALR;
    wire train_hit=btb_valid[train_index] && btb_tag[train_index]==ex_pc[31:BTB_INDEX_BITS+2];
    always @(posedge clk) begin
        if(rst || privileged_redirect) btb_valid<=0;
        else if(BRANCH_PREDICT && ex_fire && !ex_exc && ex_control) begin
            btb_valid[train_index]<=1;
            btb_tag[train_index]<=ex_pc[31:BTB_INDEX_BITS+2];
            btb_target[train_index]<=branch_target;
            if(JAL || JALR) btb_counter[train_index]<=2'b11;
            else if(!train_hit) btb_counter[train_index]<=branch_taken ? 2'b10 : 2'b01;
            else if(branch_taken) begin
                if(btb_counter[train_index]!=2'b11) btb_counter[train_index]<=btb_counter[train_index]+1'b1;
            end else if(btb_counter[train_index]!=2'b00) btb_counter[train_index]<=btb_counter[train_index]-1'b1;
        end
    end
    pc_reg u_pc (
        .clk(clk),.rst(rst),.branch_taken(redirect_valid || predict_jump),.branch_target(redirect_valid ? redirect_target : predicted_next),
        .mem_error(halted || (trap_fire && !PRIVILEGED)),.stall(!fetch_keep && !redirect_valid),.pc(pc)
    );
    // While halted show the faulting PC, otherwise show the fetch PC.
    assign debug_pc = halted ? trap_pc : pc;
    assign commit_valid = !rst && !halted && wb_valid;
    assign commit_pc = wb_pc;
    assign commit_inst = wb_inst;
    assign commit_rd = wb_rd;
    assign commit_wdata = wb_result;
    assign commit_rd_we = commit_valid && wb_writes && wb_rd!=0;
    regfile u_regfile (
        .clk(clk),.rst(rst),.raddr1(id_rs1),.raddr2(id_rs2),.rdata1(rf_a),.rdata2(rf_b),
        .we(commit_rd_we),.waddr(wb_rd),.wdata(wb_result)
    );

    // IF transaction state is independent of pipeline flushing. Once a fetch
    // has started it stays valid until acknowledged; wrong-path data is dropped.
    always @(posedge clk) begin
        if (rst) begin if_pending<=0; if_kill<=0; if_req_pc<=0; end
        else begin
            if (imem_valid && !imem_ready && !if_pending) begin
                if_pending<=1; if_req_pc<=imem_addr; if_kill<=0;
            end
            if (if_pending && (redirect_valid || trap_fire || halted)) if_kill<=1;
            if (fetch_fire) begin if_pending<=0; if_kill<=0; end
        end
    end
    always @(posedge clk) begin
        if (rst) begin
            id_valid<=0; id_pc<=0; id_inst<=0; id_fetch_error<=0; id_pred_next<=0; id_pred_taken<=0;
        end else if (halted || trap_fire || redirect_valid) id_valid<=0;
        else begin
            if (id_fire) id_valid<=0;
            if (fetch_keep) begin
                id_valid<=1; id_pc<=imem_addr; id_inst<=imem_rdata; id_fetch_error<=imem_error; id_pred_next<=predicted_next; id_pred_taken<=predicted_taken;
            end
        end
    end
    always @(posedge clk) begin
        if (rst) begin
            ex_valid<=0; ex_pc<=0; ex_inst<=0; ex_fetch_error<=0; ex_pred_next<=0; ex_pred_taken<=0;
            ex_rs1<=0; ex_rs2<=0; ex_rd<=0; ex_ctrl<=0;
            ex_a<=0; ex_b<=0; ex_i<=0; ex_s<=0; ex_bimm<=0; ex_u<=0; ex_j<=0;
        end else if (halted || trap_fire || redirect_valid) ex_valid<=0;
        else begin
            if (ex_fire) ex_valid<=0;
            // EX 停顿时保存已经前递的值，避免 WB 排空后丢失操作数。
            if (ex_valid) begin ex_a<=forward_a; ex_b<=forward_b; end
            if (id_fire) begin
                ex_valid<=1; ex_pc<=id_pc; ex_inst<=id_inst; ex_fetch_error<=id_fetch_error; ex_pred_next<=id_pred_next; ex_pred_taken<=id_pred_taken;
                ex_rs1<=id_rs1; ex_rs2<=id_rs2; ex_rd<=id_rd; ex_ctrl<=id_ctrl;
                ex_a<=id_a; ex_b<=id_bdata;
                ex_i<=id_i; ex_s<=id_s; ex_bimm<=id_b; ex_u<=id_u; ex_j<=id_j;
            end
        end
    end
    always @(posedge clk) begin
        if (rst) begin
            mem_valid<=0; mem_load<=0; mem_store<=0; mem_fence<=0; mem_writes<=0; mem_exc<=0;
            mem_csr<=0; mem_mret<=0; mem_wfi<=0; mem_fence_i<=0; mem_serial<=0;
            mem_csr_source<=0; mem_next_pc<=0;
            mem_pc<=0; mem_inst<=0; mem_result<=0; mem_addr<=0; mem_store_data<=0;
            mem_cause<=0; mem_tval<=0; mem_rd<=0;
        end else if (halted || trap_fire) mem_valid<=0;
        else begin
            if (mem_finish) mem_valid<=0;
            if (ex_fire) begin
                mem_valid<=1; mem_load<=ex_load; mem_store<=ex_store; mem_fence<=ex_fence;
                mem_csr<=ex_csr; mem_mret<=ex_mret; mem_wfi<=ex_wfi;
                mem_fence_i<=ex_fence_i; mem_serial<=ex_serial;
                mem_csr_source<=ex_inst[14] ? {27'd0,ex_inst[19:15]} : forward_a;
                mem_next_pc<=ex_actual_next;
                mem_writes<=ex_writes; mem_exc<=ex_exc; mem_cause<=ex_cause; mem_tval<=ex_tval;
                mem_pc<=ex_pc; mem_inst<=ex_inst; mem_rd<=ex_rd;
                mem_result<=ex_mul ? multiply_result : alu_result; mem_addr<=alu_addr; mem_store_data<=store_data;
            end
        end
    end
    always @(posedge clk) begin
        if (rst) begin
            wb_valid<=0; wb_writes<=0; wb_pc<=0; wb_inst<=0; wb_rd<=0; wb_result<=0; wb_mem_rdata<=0;
            wb_next_pc<=0;
            wb_counter_write<=0;
            halted<=0; trap_pc<=0; trap_cause<=0; trap_value<=0;
        end else begin
            wb_valid<=mem_finish && !trap_fire;
            if (mem_finish && !trap_fire) begin
                wb_writes<=mem_writes; wb_pc<=mem_pc; wb_inst<=mem_inst; wb_rd<=mem_rd;
                wb_result<=mem_csr ? csr_read_data : mem_load ? dmem_rdata : mem_result;
                wb_next_pc<=mem_mret ? csr_return_pc : mem_next_pc;
                wb_counter_write<=mem_csr && mem_csr_write &&
                    (mem_inst[31:20]==12'hb02 || mem_inst[31:20]==12'hb82);
                wb_mem_rdata<=dmem_rdata;
            end
            if (trap_fire) begin
                halted<=!PRIVILEGED; trap_pc<=mem_pc;
                trap_cause<=mem_exc ? mem_cause : (mem_store ? 7 : 5);
                trap_value<=mem_exc ? mem_tval : mem_addr;
            end
            if(irq_fire) begin
                trap_pc<=architectural_next_pc; trap_cause<=csr_irq_cause; trap_value<=0;
            end
        end
    end
endmodule
