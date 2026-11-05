module ddr_cpu_bridge #(
    parameter integer UI_TIMEOUT_CYCLES = 125000000,
    parameter integer CPU_TIMEOUT_CYCLES = 270000000
)(
    input wire cpu_clk, cpu_rst, cpu_abort,
    input wire req_valid, req_write,
    input wire [31:0] req_addr, req_wdata,
    input wire [2:0] req_funct3,
    output wire req_ready,
    output reg [31:0] rsp_rdata,
    output reg rsp_error,
    output wire busy,
    output reg cpu_fault,
    input wire ui_clk, ui_rst, ui_init_done,
    output reg ui_fault,
    output wire [27:0] axi_awaddr,
    output wire axi_awuser_ap,
    output wire [3:0] axi_awuser_id, axi_awlen,
    input wire axi_awready,
    output wire axi_awvalid,
    output wire [255:0] axi_wdata,
    output wire [31:0] axi_wstrb,
    input wire axi_wready,
    input wire [3:0] axi_wusero_id,
    input wire axi_wusero_last,
    output wire [27:0] axi_araddr,
    output wire axi_aruser_ap,
    output wire [3:0] axi_aruser_id, axi_arlen,
    input wire axi_arready,
    output wire axi_arvalid,
    input wire [255:0] axi_rdata,
    input wire [3:0] axi_rid,
    input wire axi_rlast, axi_rvalid
);
    wire joint_reset = cpu_rst | ui_rst;
    (* ASYNC_REG="TRUE" *) reg [1:0] cpu_reset_pipe, ui_reset_pipe;
    always @(posedge cpu_clk or posedge joint_reset)
        if(joint_reset) cpu_reset_pipe<=2'b11;
        else cpu_reset_pipe<={cpu_reset_pipe[0],1'b0};
    always @(posedge ui_clk or posedge joint_reset)
        if(joint_reset) ui_reset_pipe<=2'b11;
        else ui_reset_pipe<={ui_reset_pipe[0],1'b0};
    wire crst=joint_reset | cpu_reset_pipe[1];
    wire urst=joint_reset | ui_reset_pipe[1];

    localparam C_IDLE=0,C_WAIT=1,C_RESPONSE=2;
    reg [1:0] cstate;
    reg request_toggle;
    // Bundled payload stays unchanged until the response has crossed back.
    reg request_write;
    reg [31:0] request_addr,request_data;
    reg [2:0] request_size;
    reg response_toggle;
    reg [31:0] response_data;
    reg response_error;
    reg [31:0] cpu_timeout_count;
    (* ASYNC_REG="TRUE" *) reg [1:0] response_sync;
    wire invalid_size = req_write ? (req_funct3>2) :
        !(req_funct3==0 || req_funct3==1 || req_funct3==2 || req_funct3==4 || req_funct3==5);
    wire invalid_alignment = ((req_funct3[1:0]==1) && req_addr[0]) ||
                             ((req_funct3[1:0]==2) && |req_addr[1:0]);
    wire invalid_access = req_addr[31:30]!=2'b10 || invalid_size || invalid_alignment;
    assign req_ready = !crst && cstate==C_RESPONSE;
    assign busy = !crst && cstate!=C_IDLE;
    always @(posedge cpu_clk or posedge crst) begin
        if(crst) begin
            cstate<=C_IDLE;request_toggle<=0;request_write<=0;request_addr<=0;
            request_data<=0;request_size<=0;response_sync<=0;rsp_rdata<=0;rsp_error<=0;
            cpu_timeout_count<=0;cpu_fault<=0;
        end else begin
            response_sync<={response_sync[0],response_toggle};
            if(cstate==C_WAIT) cpu_timeout_count<=cpu_timeout_count+1'b1;
            else cpu_timeout_count<=0;
            case(cstate)
                C_IDLE: if(req_valid) begin
                    if(invalid_access || cpu_fault || cpu_abort) begin
                        rsp_rdata<=0;rsp_error<=1;cstate<=C_RESPONSE;
                    end else begin
                        request_write<=req_write;request_addr<=req_addr;request_data<=req_wdata;
                        request_size<=req_funct3;request_toggle<=~request_toggle;cstate<=C_WAIT;
                    end
                end
                C_WAIT: if(cpu_abort) begin
                    cpu_fault<=1;rsp_rdata<=0;rsp_error<=1;cstate<=C_RESPONSE;
                end else if(response_sync[1]==request_toggle) begin
                    rsp_rdata<=response_data;rsp_error<=response_error;cstate<=C_RESPONSE;
                end else if(CPU_TIMEOUT_CYCLES!=0 && cpu_timeout_count>=CPU_TIMEOUT_CYCLES-1) begin
                    // This watchdog runs even if the DDR UI clock stops.
                    cpu_fault<=1;rsp_rdata<=0;rsp_error<=1;cstate<=C_RESPONSE;
                end
                C_RESPONSE: if(req_valid) cstate<=C_IDLE;
                default: cstate<=C_IDLE;
            endcase
        end
    end

    localparam U_IDLE=0,U_CAL=1,U_AW=2,U_W=3,U_AR=4,U_R=5;
    reg [2:0] ustate;
    (* ASYNC_REG="TRUE" *) reg [1:0] request_sync;
    (* ASYNC_REG="TRUE" *) reg [1:0] cpu_fault_sync;
    reg seen_request;
    reg write_latched;
    reg [27:0] address_latched;
    reg [4:0] byte_offset;
    reg [2:0] size_latched;
    reg [255:0] data_latched;
    reg [31:0] strobe_latched;
    reg [31:0] timeout_count;
    reg initialized_once;
    wire [31:0] shifted_read=axi_rdata >> {byte_offset,3'b000};
    wire timed_out=(UI_TIMEOUT_CYCLES!=0) && timeout_count>=UI_TIMEOUT_CYCLES-1;
    function [31:0] extended_read;
        input [31:0] value; input [2:0] size;
        begin
            case(size)
                0:extended_read={{24{value[7]}},value[7:0]};
                1:extended_read={{16{value[15]}},value[15:0]};
                4:extended_read={24'b0,value[7:0]};
                5:extended_read={16'b0,value[15:0]};
                default:extended_read=value;
            endcase
        end
    endfunction
    assign axi_awaddr=address_latched;
    assign axi_araddr=address_latched;
    assign axi_awuser_ap=1'b0;
    assign axi_aruser_ap=1'b0;
    assign axi_awuser_id=0;
    assign axi_aruser_id=0;
    assign axi_awlen=0;
    assign axi_arlen=0;
    assign axi_wdata=data_latched;
    assign axi_wstrb=strobe_latched;
    assign axi_awvalid=!urst && !ui_fault && !cpu_fault_sync[1] && ui_init_done && ustate==U_AW;
    assign axi_arvalid=!urst && !ui_fault && !cpu_fault_sync[1] && ui_init_done && ustate==U_AR;
    always @(posedge ui_clk or posedge urst) begin
        if(urst) begin
            request_sync<=0;seen_request<=0;response_toggle<=0;response_data<=0;response_error<=0;
            ustate<=U_IDLE;write_latched<=0;address_latched<=0;byte_offset<=0;size_latched<=0;
            data_latched<=0;strobe_latched<=0;timeout_count<=0;ui_fault<=0;cpu_fault_sync<=0;initialized_once<=0;
        end else begin
            request_sync<={request_sync[0],request_toggle};
            cpu_fault_sync<={cpu_fault_sync[0],cpu_fault};
            if(ui_init_done) initialized_once<=1;
            if(ustate==U_IDLE || ustate==U_CAL) timeout_count<=0;
            else timeout_count<=timeout_count+1'b1;
            if(cpu_fault_sync[1] || (initialized_once && !ui_init_done) || (ustate>=U_AW && timed_out)) begin
                ui_fault<=1;response_data<=0;response_error<=1;
                response_toggle<=seen_request;ustate<=U_IDLE;
            end else case(ustate)
                U_IDLE: if(request_sync[1]!=seen_request) begin
                    seen_request<=request_sync[1];write_latched<=request_write;
                    // Subtracting 80000000 leaves request_addr[29:0].
                    address_latched<={request_addr[29:5],3'b000};
                    byte_offset<=request_addr[4:0];size_latched<=request_size;
                    data_latched<={224'b0,request_data} << {request_addr[4:0],3'b000};
                    strobe_latched<=(request_size==0 ? 32'h1 : request_size==1 ? 32'h3 : 32'hf) << request_addr[4:0];
                    ustate<=U_CAL;
                end
                U_CAL: begin
                    if(ui_fault) begin
                        response_data<=0;response_error<=1;response_toggle<=seen_request;ustate<=U_IDLE;
                    end else if(ui_init_done) ustate<=write_latched ? U_AW : U_AR;
                end
                U_AW: if(axi_awready) begin
                    // Also tolerate the first WREADY on the AW handshake edge.
                    if(axi_wready) begin
                        // The supplied vendor BIST advances solely on WREADY.
                        // WUSERO_ID/LAST timing is not assumed here.
                        response_data<=0;response_error<=0;
                        response_toggle<=seen_request;ustate<=U_IDLE;
                    end else ustate<=U_W;
                end
                U_W: if(axi_wready) begin
                    response_data<=0;response_error<=0;
                    response_toggle<=seen_request;ustate<=U_IDLE;
                end
                U_AR: if(axi_arready) begin
                    if(axi_rvalid) begin
                        response_data<=extended_read(shifted_read,size_latched);
                        response_error<=axi_rid!=0 || !axi_rlast;
                        if(axi_rid!=0 || !axi_rlast) ui_fault<=1;
                        response_toggle<=seen_request;ustate<=U_IDLE;
                    end else ustate<=U_R;
                end
                U_R: if(axi_rvalid) begin
                    response_data<=extended_read(shifted_read,size_latched);
                    response_error<=axi_rid!=0 || !axi_rlast;
                    if(axi_rid!=0 || !axi_rlast) ui_fault<=1;
                    response_toggle<=seen_request;ustate<=U_IDLE;
                end
                default: ustate<=U_IDLE;
            endcase
        end
    end
endmodule
