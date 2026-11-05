`timescale 1ns / 1ps
// Blocking, two-way set-associative cache with per-set LRU replacement.
// CPU/backend transactions complete ONLY on valid && ready. No abort protocol.
// D-cache stores are write-through, write-allocate; MMIO must bypass this module.
// SETS and LINE_WORDS must be powers of two >= 2; ADDR_WIDTH <= 32.
module cache_2way_reference #(
    parameter integer READ_ONLY = 0,
    parameter integer SETS = 16,
    parameter integer LINE_WORDS = 4,
    parameter integer ADDR_WIDTH = 16,
    // Read-only sequential lookahead uses the existing clocked data ports.
    // Both switches may be disabled for a cycle-identical baseline.
    parameter integer READ_LOOKAHEAD = READ_ONLY,
    parameter integer REFILL_FORWARD = 1,
    parameter integer DATA_PREVIEW = 0,
    parameter integer WRITE_RESPONSE_FORWARD = 0
)(
    input wire clk, rst,
    input wire req_valid, req_write,
    input wire [31:0] req_addr, req_wdata,
    input wire [2:0] req_funct3,
    output wire ready, error,
    output wire [31:0] rdata,

    output wire bf_req_valid, bf_req_write,
    output wire [31:0] bf_req_addr, bf_req_wdata,
    output wire [2:0] bf_req_funct3,
    input wire bf_ready, bf_error,
    input wire [31:0] bf_rdata,

    // A level request is accepted once until it goes low again. Requests
    // arriving during a transaction are latched and serviced after completion.
    input wire invalidate,
    output reg invalidate_done,
    output wire busy,
    output reg [31:0] hit_count, miss_count,
    output reg [31:0] refill_word_count, backend_write_count,
    input wire preview_valid,
    input wire [31:0] preview_addr
);
    localparam integer SET_BITS = $clog2(SETS);
    localparam integer WORD_BITS = $clog2(LINE_WORDS);
    localparam integer LINE_BITS = WORD_BITS + 2;
    localparam integer ARRAY_WORDS = SETS * LINE_WORDS;
    // High address bits are rejected before any tag lookup can complete or
    // refill can install a line. Store only the architecturally reachable tag.
    localparam integer TAG_BITS = ADDR_WIDTH>LINE_BITS+SET_BITS ? ADDR_WIDTH-LINE_BITS-SET_BITS : 1;
    localparam [3:0] S_IDLE=0, S_READ=1, S_ACCESS=2,
                     S_REFILL=3, S_WRITE=4, S_RESP=5, S_INVALIDATE=6;

    reg [3:0] state;
    reg [SETS-1:0] valid0, valid1, lru_victim;
    reg [TAG_BITS-1:0] tags0 [0:SETS-1];
    reg [TAG_BITS-1:0] tags1 [0:SETS-1];
    // Data and read registers are deliberately not reset. Reads are clocked;
    // each way has a single synchronous read/write port with byte write enables.
    (* ram_style="block" *) reg [31:0] data0 [0:ARRAY_WORDS-1];
    (* ram_style="block" *) reg [31:0] data1 [0:ARRAY_WORDS-1];
    reg [31:0] read0, read1;

    reg saved_write;
    reg [31:0] saved_addr, saved_wdata;
    reg [2:0] saved_funct3;
    reg selected_way;
    reg [WORD_BITS-1:0] refill_index;
    reg [31:0] response_data;
    reg [31:0] refill_requested_word;
    reg response_error;
    reg lookahead_valid;
    reg [31:0] lookahead_addr;
    reg invalidate_pending, invalidate_seen;
    reg [SET_BITS-1:0] invalidate_index;

    wire [SET_BITS-1:0] saved_set = saved_addr[LINE_BITS+SET_BITS-1:LINE_BITS];
    wire [WORD_BITS-1:0] saved_word = saved_addr[LINE_BITS-1:2];
    wire [SET_BITS+WORD_BITS-1:0] access_index = {saved_set, saved_word};
    wire [SET_BITS+WORD_BITS-1:0] fill_index = {saved_set, refill_index};
    wire [TAG_BITS-1:0] saved_tag = saved_addr >> (LINE_BITS+SET_BITS);
    wire [31:0] line_base = saved_addr & ~(LINE_WORDS*4-1);
    wire [SET_BITS-1:0] request_set = req_addr[LINE_BITS+SET_BITS-1:LINE_BITS];
    wire [WORD_BITS-1:0] request_word = req_addr[LINE_BITS-1:2];
    wire [SET_BITS+WORD_BITS-1:0] request_index = {request_set, request_word};
    wire [TAG_BITS-1:0] request_tag = req_addr >> (LINE_BITS+SET_BITS);
    wire hit0 = valid0[request_set] && tags0[request_set] == request_tag;
    wire hit1 = valid1[request_set] && tags1[request_set] == request_tag;
    wire fresh_invalidate = (invalidate === 1'b1) && !invalidate_seen;

    wire size_bad = READ_ONLY ? (req_write || req_funct3 != 3'd2) :
                    req_write ? (req_funct3 > 3'd2) :
                    !(req_funct3==0 || req_funct3==1 || req_funct3==2 ||
                      req_funct3==4 || req_funct3==5);
    wire align_bad = ((req_funct3[1:0]==1) && req_addr[0]) ||
                     ((req_funct3[1:0]==2) && req_addr[1:0]!=0);
    wire address_bad = (req_addr >> ADDR_WIDTH) != 0;
    wire request_bad = size_bad || align_bad || address_bad;
    // A tag match alone is not enough: data must have been clocked for this
    // exact address, and invalidation always takes priority over a new reply.
    wire lookahead_hit = READ_ONLY && READ_LOOKAHEAD && !rst &&
                  state==S_IDLE && req_valid && lookahead_valid &&
                  lookahead_addr==req_addr && (hit0 || hit1) && !request_bad &&
                  !invalidate_pending && !fresh_invalidate;
    wire preview_hit = !READ_ONLY && DATA_PREVIEW && !rst &&
                  state==S_IDLE && req_valid && !req_write && lookahead_valid &&
                  lookahead_addr=={req_addr[31:2],2'b00} && (hit0 || hit1) && !request_bad &&
                  !invalidate_pending && !fresh_invalidate;
    wire launch = !rst && state==S_IDLE && req_valid &&
                  !invalidate_pending && !fresh_invalidate && !lookahead_hit && !preview_hit;
    wire lookahead_read = READ_ONLY && READ_LOOKAHEAD && !rst && req_valid &&
                  !invalidate_pending && !fresh_invalidate &&
                  (lookahead_hit || state==S_ACCESS || (state==S_RESP && !response_error));
    // Read the next EX load address on the EX->MEM edge using the same single
    // synchronous read port. Demand requests, writes and invalidation win.
    wire preview_read = !READ_ONLY && DATA_PREVIEW && !rst &&
                  (preview_valid === 1'b1) && !invalidate_pending && !fresh_invalidate &&
                  ((state==S_IDLE && (!req_valid || preview_hit)) || state==S_ACCESS ||
                   (state==S_RESP && !saved_write && !response_error));
    wire [31:0] next_read_addr=(state==S_IDLE ? req_addr : saved_addr)+32'd4;
    wire [SET_BITS+WORD_BITS-1:0] next_read_index=next_read_addr[LINE_BITS+SET_BITS-1:2];
    // One syntactic array read per way keeps synthesis from inferring separate
    // memory ports for demand, refill re-read and lookahead address choices.
    wire data_read_enable=(!rst && state==S_READ) ||
                  (launch && !request_bad && !req_write) || lookahead_read || preview_read;
    wire [SET_BITS+WORD_BITS-1:0] data_read_index=state==S_READ ? access_index :
                  launch ? request_index : preview_read ? preview_addr[LINE_BITS+SET_BITS-1:2] : next_read_index;

    wire [4:0] store_shift = {saved_addr[1:0],3'b000};
    wire [31:0] aligned_store = saved_wdata << store_shift;
    wire [3:0] byte_strobe = saved_funct3==0 ? (4'b0001 << saved_addr[1:0]) :
                             saved_funct3==1 ? (4'b0011 << saved_addr[1:0]) : 4'b1111;
    wire [31:0] selected_read = selected_way ? read1 : read0;
    wire [31:0] shifted_read = selected_read >> store_shift;
    wire [31:0] lookahead_data = hit0 ? read0 : read1;
    wire refill_complete = !rst && state==S_REFILL && bf_ready;
    wire store_complete = !rst && state==S_WRITE && bf_ready;

    function [31:0] load_value;
        input [31:0] shifted,full_word;
        input [2:0] size;
        begin
            case(size)
                0: load_value={{24{shifted[7]}},shifted[7:0]};
                1: load_value={{16{shifted[15]}},shifted[15:0]};
                2: load_value=full_word;
                4: load_value={24'b0,shifted[7:0]};
                5: load_value={16'b0,shifted[15:0]};
                default: load_value=0;
            endcase
        end
    endfunction
    // A hit performs its synchronous data read on the acceptance edge and
    // completes on the following edge: no additional lookup pipeline bubbles.
    assign ready = !rst && (state==S_RESP || state==S_ACCESS || lookahead_hit || preview_hit ||
                           (WRITE_RESPONSE_FORWARD && store_complete));
    assign error = !rst && ((state==S_RESP && response_error) ||
                           (WRITE_RESPONSE_FORWARD && store_complete && bf_error));
    assign rdata = preview_hit ? load_value(lookahead_data >> {req_addr[1:0],3'b000},lookahead_data,req_funct3) :
                   (WRITE_RESPONSE_FORWARD && store_complete) ? 32'd0 : lookahead_hit ? lookahead_data :
                   state==S_ACCESS ? load_value(shifted_read,selected_read,saved_funct3) : response_data;
    assign busy = !rst && (state!=S_IDLE || invalidate_pending ||
                           fresh_invalidate || req_valid);
    // During a refill valid remains asserted across beats and the address
    // advances only at completion. A zero-wait backend can deliver one word
    // per cycle, with no inter-beat bubbles inserted by this controller.
    assign bf_req_valid = !rst && (state==S_REFILL || state==S_WRITE);
    assign bf_req_write = state==S_WRITE;
    assign bf_req_addr = state==S_WRITE ? saved_addr :
                         line_base + ({{(32-WORD_BITS){1'b0}},refill_index} << 2);
    assign bf_req_wdata = saved_wdata;
    assign bf_req_funct3 = state==S_WRITE ? saved_funct3 : 3'd2;

    // The mutually exclusive refill/store states share one physical write port.
    // Use one address/data/mask mux and one write assignment per byte lane so
    // the device synthesizer sees a byte-enabled RAM rather than two writers.
    wire cache_write_enable=(refill_complete || store_complete) && !bf_error;
    wire [SET_BITS+WORD_BITS-1:0] cache_write_index=refill_complete ? fill_index : access_index;
    wire [31:0] cache_write_data=refill_complete ? bf_rdata : aligned_store;
    wire [3:0] cache_write_strobe=refill_complete ? 4'b1111 : byte_strobe;
    // Separate per-way processes preserve synchronous-read RAM inference.
    always @(posedge clk) begin
        if (data_read_enable) read0 <= data0[data_read_index];
        if (cache_write_enable && !selected_way) begin
            if(cache_write_strobe[0]) data0[cache_write_index][7:0] <= cache_write_data[7:0];
            if(cache_write_strobe[1]) data0[cache_write_index][15:8] <= cache_write_data[15:8];
            if(cache_write_strobe[2]) data0[cache_write_index][23:16] <= cache_write_data[23:16];
            if(cache_write_strobe[3]) data0[cache_write_index][31:24] <= cache_write_data[31:24];
        end
    end
    always @(posedge clk) begin
        if (data_read_enable) read1 <= data1[data_read_index];
        if (cache_write_enable && selected_way) begin
            if(cache_write_strobe[0]) data1[cache_write_index][7:0] <= cache_write_data[7:0];
            if(cache_write_strobe[1]) data1[cache_write_index][15:8] <= cache_write_data[15:8];
            if(cache_write_strobe[2]) data1[cache_write_index][23:16] <= cache_write_data[23:16];
            if(cache_write_strobe[3]) data1[cache_write_index][31:24] <= cache_write_data[31:24];
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            state<=S_IDLE;
            valid0<=0; valid1<=0; lru_victim<=0;
            saved_write<=0; saved_addr<=0; saved_wdata<=0; saved_funct3<=0;
            selected_way<=0; refill_index<=0;
            response_data<=0; response_error<=0; refill_requested_word<=0;
            lookahead_valid<=0; lookahead_addr<=0;
            invalidate_pending<=0; invalidate_seen<=0; invalidate_index<=0;
            invalidate_done<=0;
            hit_count<=0; miss_count<=0; refill_word_count<=0; backend_write_count<=0;
        end else begin
            invalidate_done<=0;
            if(fresh_invalidate || invalidate_pending || state==S_INVALIDATE)
                lookahead_valid<=0;
            else if(preview_read) begin
                lookahead_valid<=1;
                lookahead_addr<={preview_addr[31:2],2'b00};
            end else if(lookahead_read) begin
                lookahead_valid<=1;
                lookahead_addr<=next_read_addr;
            end else if(launch || store_complete || refill_complete) lookahead_valid<=0;
            if (invalidate !== 1'b1) invalidate_seen<=0;
            if (fresh_invalidate) begin
                invalidate_pending<=1;
                invalidate_seen<=1;
            end
            case (state)
                S_IDLE: begin
                    if (invalidate_pending || fresh_invalidate) begin
                        invalidate_pending<=0;
                        invalidate_index<=0;
                        state<=S_INVALIDATE;
                    end else if (lookahead_hit || preview_hit) begin
                        // Completion and the next synchronous lookahead read
                        // share this edge. No new outstanding transaction.
                        hit_count<=hit_count+1'b1;
                        lru_victim[request_set]<=hit0;
                    end else if (req_valid) begin
                        saved_write<=req_write;
                        saved_addr<=req_addr;
                        saved_wdata<=req_wdata;
                        saved_funct3<=req_funct3;
                        response_error<=request_bad;
                        response_data<=0;
                        if (request_bad) state<=S_RESP;
                        else if (hit0 || hit1) begin
                            selected_way<=hit1 && !hit0;
                            hit_count<=hit_count+1'b1;
                            state<=req_write ? S_WRITE : S_ACCESS;
                        end else begin
                            // Prefer an invalid way, then the per-set LRU victim.
                            if (!valid0[request_set]) begin
                                selected_way<=0; valid0[request_set]<=0;
                            end else if (!valid1[request_set]) begin
                                selected_way<=1; valid1[request_set]<=0;
                            end else begin
                                selected_way<=lru_victim[request_set];
                                if(lru_victim[request_set]) valid1[request_set]<=0;
                                else valid0[request_set]<=0;
                            end
                            refill_index<=0;
                            miss_count<=miss_count+1'b1;
                            state<=S_REFILL;
                        end
                    end
                end
                S_READ: state<=S_ACCESS;
                S_ACCESS: if(req_valid) begin
                    lru_victim[saved_set]<=!selected_way;
                    state<=S_IDLE;
                end
                S_REFILL: if (bf_ready) begin
                    if (bf_error) begin
                        // The victim was invalidated before the first beat;
                        // a partially refilled line is never made valid.
                        response_error<=1;
                        response_data<=0;
                        state<=S_RESP;
                    end else begin
                        refill_word_count<=refill_word_count+1'b1;
                        if(refill_index==saved_word) refill_requested_word<=bf_rdata;
                        if (refill_index==LINE_WORDS-1) begin
                            if(selected_way) begin
                                tags1[saved_set]<=saved_tag; valid1[saved_set]<=1;
                            end else begin
                                tags0[saved_set]<=saved_tag; valid0[saved_set]<=1;
                            end
                            // Stores need no RMW read: the backend and cache
                            // both apply byte enables after successful write.
                            // Preserve precise refill errors by waiting for all
                            // beats, then forward the captured requested word.
                            if(!saved_write && REFILL_FORWARD) begin
                                response_data<=load_value(
                                    (refill_index==saved_word ? bf_rdata : refill_requested_word) >> store_shift,
                                    refill_index==saved_word ? bf_rdata : refill_requested_word,
                                    saved_funct3);
                                state<=S_RESP;
                            end else state<=saved_write ? S_WRITE : S_READ;
                        end else refill_index<=refill_index+1'b1;
                    end
                end
                S_WRITE: if (bf_ready) begin
                    response_error<=bf_error;
                    response_data<=0;
                    if (bf_error) begin
                        // Conservatively discard the line after any rejected
                        // backend write; no failed write updates cached bytes.
                        if(selected_way) valid1[saved_set]<=0;
                        else valid0[saved_set]<=0;
                    end else begin
                        backend_write_count<=backend_write_count+1'b1;
                        lru_victim[saved_set]<=!selected_way;
                    end
                    state<=WRITE_RESPONSE_FORWARD ? S_IDLE : S_RESP;
                end
                S_RESP: if(req_valid) begin
                    // Forwarded refill responses bypass S_ACCESS; they must
                    // still mark the newly installed line most recently used.
                    if(!response_error && !saved_write)
                        lru_victim[saved_set]<=!selected_way;
                    state<=S_IDLE;
                end
                S_INVALIDATE: begin
                    valid0[invalidate_index]<=0;
                    valid1[invalidate_index]<=0;
                    lru_victim[invalidate_index]<=0;
                    if(invalidate_index==SETS-1) begin
                        invalidate_done<=1;
                        state<=S_IDLE;
                    end else invalidate_index<=invalidate_index+1'b1;
                end
                default: state<=S_IDLE;
            endcase
        end
    end
endmodule
