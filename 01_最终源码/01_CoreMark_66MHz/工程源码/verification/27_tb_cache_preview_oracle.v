`timescale 1ns / 1ps
// Independent byte-addressed oracle + randomized completion-bus latency.
module cache_test_backend #(
    parameter integer SEED=32'h9e3779b9
)(
    input wire clk,rst,valid,write,
    input wire [31:0] addr,wdata,
    input wire [2:0] funct3,
    output wire ready,error,
    output wire [31:0] rdata,
    input wire inject_read,inject_write,
    input wire [31:0] fault_addr,
    output reg [31:0] read_completions,write_completions
);
    reg [31:0] mem[0:1023];
    reg pending,pwrite;
    reg [31:0] paddr,pdata,rng;
    reg [2:0] psize,delay_count;
    integer n;
    function [7:0] original_byte;
        input integer a;
        begin original_byte=(a*37+11)&255; end
    endfunction
    initial for(n=0;n<1024;n=n+1)
        mem[n]={original_byte(n*4+3),original_byte(n*4+2),original_byte(n*4+1),original_byte(n*4)};
    assign ready=pending && delay_count==0 && !rst;
    assign error=ready && ((inject_read && !pwrite && paddr==fault_addr) ||
                           (inject_write && pwrite && paddr==fault_addr));
    assign rdata=mem[paddr[11:2]];
    wire [31:0] shifted=pdata << {paddr[1:0],3'b000};
    wire [3:0] mask=psize==0 ? 4'b1 << paddr[1:0] : psize==1 ? 4'b11 << paddr[1:0] : 4'b1111;
    reg held;
    reg [31:0] held_addr,held_data;
    reg held_write;
    reg [2:0] held_size;
    always @(posedge clk) begin
        if(rst) begin
            pending<=0; pwrite<=0;paddr<=0;pdata<=0;psize<=0;delay_count<=0;rng<=SEED;
            read_completions<=0;write_completions<=0;held<=0;
        end else begin
            if(held && (!valid || addr!==held_addr || wdata!==held_data ||
                        write!==held_write || funct3!==held_size))
                $fatal(1,"Backend request changed before completion");
            held<=valid && !ready;
            held_addr<=addr;held_data<=wdata;held_write<=write;held_size<=funct3;
            if(!pending && valid) begin
                pending<=1;pwrite<=write;paddr<=addr;pdata<=wdata;psize<=funct3;
                delay_count<={1'b0,rng[1:0]};
                rng<={rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]};
            end else if(pending && delay_count!=0) delay_count<=delay_count-1'b1;
            if(valid && ready) begin
                pending<=0;
                if(!error) begin
                    if(pwrite) begin
                        if(mask[0]) mem[paddr[11:2]][7:0]<=shifted[7:0];
                        if(mask[1]) mem[paddr[11:2]][15:8]<=shifted[15:8];
                        if(mask[2]) mem[paddr[11:2]][23:16]<=shifted[23:16];
                        if(mask[3]) mem[paddr[11:2]][31:24]<=shifted[31:24];
                        write_completions<=write_completions+1;
                    end else read_completions<=read_completions+1;
                end
            end
        end
    end
endmodule

module tb_cache_2way;
    reg clk=0; always #5 clk=~clk;
    reg rst=1;
    reg dv=0,dw=0; reg pv=0; reg [31:0] pa=0; integer preview_completions=0;
    reg [31:0] da=0,dd=0;
    reg [2:0] ds=2;
    wire dr,de;wire[31:0] dq;
    wire bv,bw,br,be;wire[31:0] ba,bd,bq;wire[2:0] bs;
    reg inv=0;wire inv_done,cache_busy;
    wire[31:0] hits,misses,fill_words,write_words,bread,bwrites;
    reg bad_read=0,bad_write=0;reg[31:0] bad_address=0;
    cache_2way #(.SETS(2),.LINE_WORDS(4),.ADDR_WIDTH(12),.DATA_PREVIEW(1),.WRITE_RESPONSE_FORWARD(1)) dut(
 .preview_valid(pv),.preview_addr(pa),
        .clk(clk),.rst(rst),.req_valid(dv),.req_write(dw),.req_addr(da),.req_wdata(dd),.req_funct3(ds),
        .ready(dr),.error(de),.rdata(dq),.bf_req_valid(bv),.bf_req_write(bw),.bf_req_addr(ba),
        .bf_req_wdata(bd),.bf_req_funct3(bs),.bf_ready(br),.bf_error(be),.bf_rdata(bq),
        .invalidate(inv),.invalidate_done(inv_done),.busy(cache_busy),.hit_count(hits),.miss_count(misses),
        .refill_word_count(fill_words),.backend_write_count(write_words));
    cache_test_backend backend(.clk(clk),.rst(rst),.valid(bv),.write(bw),.addr(ba),.wdata(bd),.funct3(bs),
        .ready(br),.error(be),.rdata(bq),.inject_read(bad_read),.inject_write(bad_write),.fault_addr(bad_address),
        .read_completions(bread),.write_completions(bwrites));

    reg iv=0,iw=0;reg[31:0] ia=0,idata=0;reg[2:0] isize=2;
    wire ir,ie;wire[31:0] iq;
    wire ibv,ibw,ibr,ibe;wire[31:0] iba,ibd,ibq;wire[2:0] ibs;
    reg iinv=0;wire iinv_done;
    wire[31:0] ihits,imisses,ifills,iwrites,ibreads,ibwrites;
    cache_2way #(.READ_ONLY(1),.SETS(2),.LINE_WORDS(4),.ADDR_WIDTH(12)) icache(
        .clk(clk),.rst(rst),.req_valid(iv),.req_write(iw),.req_addr(ia),.req_wdata(idata),.req_funct3(isize),
        .ready(ir),.error(ie),.rdata(iq),.bf_req_valid(ibv),.bf_req_write(ibw),.bf_req_addr(iba),
        .bf_req_wdata(ibd),.bf_req_funct3(ibs),.bf_ready(ibr),.bf_error(ibe),.bf_rdata(ibq),
        .invalidate(iinv),.invalidate_done(iinv_done),.busy(),.hit_count(ihits),.miss_count(imisses),
        .refill_word_count(ifills),.backend_write_count(iwrites));
    cache_test_backend #(.SEED(32'h12345678)) ibackend(.clk(clk),.rst(rst),.valid(ibv),.write(ibw),.addr(iba),
        .wdata(ibd),.funct3(ibs),.ready(ibr),.error(ibe),.rdata(ibq),.inject_read(1'b0),.inject_write(1'b0),
        .fault_addr(32'b0),.read_completions(ibreads),.write_completions(ibwrites));

    // Zero-wait read backend proves that a line burst contains no controller
    // bubbles: four accepted word beats must occupy four consecutive cycles.
    reg zv=0;wire zr,ze;wire[31:0] zq;
    wire zbv,zbw;wire[31:0] zba,zbd;wire[2:0] zbs;
    wire[31:0] zbackend_data=zba ^ 32'h5a5aa5a5;
    cache_2way #(.READ_ONLY(1),.SETS(2),.LINE_WORDS(4),.ADDR_WIDTH(12)) zcache(
        .clk(clk),.rst(rst),.req_valid(zv),.req_write(1'b0),.req_addr(32'h304),.req_wdata(32'b0),.req_funct3(3'd2),
        .ready(zr),.error(ze),.rdata(zq),.bf_req_valid(zbv),.bf_req_write(zbw),.bf_req_addr(zba),
        .bf_req_wdata(zbd),.bf_req_funct3(zbs),.bf_ready(zbv),.bf_error(1'b0),.bf_rdata(zbackend_data),
        .invalidate(1'b0),.invalidate_done(),.busy(),.hit_count(),.miss_count(),.refill_word_count(),.backend_write_count());
    integer cycle_number=0,burst_first_cycle=0,burst_beats=0;
    reg[31:0] burst_last_address=0;
    always @(posedge clk) begin
        cycle_number=cycle_number+1; if(!rst && dut.preview_hit)preview_completions=preview_completions+1;
        if(!rst && zbv) begin
            if(zbw || zbs!=2) $fatal(1,"Refill burst was not word reads");
            if(burst_beats==0) burst_first_cycle=cycle_number;
            else if(zba!=burst_last_address+4) $fatal(1,"Refill burst was not contiguous");
            burst_last_address=zba;
            burst_beats=burst_beats+1;
            if(burst_beats==4 && cycle_number-burst_first_cycle!=3)
                $fatal(1,"Refill inserted inter-beat bubbles");
        end
    end

    reg [7:0] oracle[0:4095];
    reg override_expected=0;reg[31:0] overridden_value=0;
    integer checks=0,n,k,timeout_count,last_i_wait_cycles;
    reg[31:0] rng=32'hc0ffee19;
    reg[31:0] saved_count,stale_word,random_address,random_data;
    reg[31:0] locality_start_hits,locality_start_misses,locality_start_reads;
    reg[2:0] random_size;
    reg random_write;
    function [31:0] random_next;
        input [31:0] current;
        begin random_next={current[30:0],current[31]^current[21]^current[1]^current[0]}; end
    endfunction
    function [31:0] golden_read;
        input [31:0] a;
        input [2:0] size;
        reg[31:0] word_value;
        begin
            word_value={oracle[a+3],oracle[a+2],oracle[a+1],oracle[a]};
            case(size)
                0: golden_read={{24{word_value[7]}},word_value[7:0]};
                1: golden_read={{16{word_value[15]}},word_value[15:0]};
                2: golden_read=word_value;
                4: golden_read={24'b0,word_value[7:0]};
                5: golden_read={16'b0,word_value[15:0]};
                default: golden_read=0;
            endcase
        end
    endfunction
    task access;
        input wr;input[31:0] a,value;input[2:0] size;input want_error;
        reg[31:0] expected;
        integer wait_cycles,byte_count,j;
        begin
            expected=override_expected ? overridden_value : want_error ? 0 : golden_read(a,size);
            @(negedge clk);pv=1;pa=a; @(negedge clk);pv=0;dv=1;dw=wr;da=a;dd=value;ds=size;
            wait_cycles=0;
            @(posedge clk);
            while(!dr) begin
                wait_cycles=wait_cycles+1;
                if(wait_cycles>1000) $fatal(1,"D-cache timeout addr=%h state=%d",a,dut.state);
                @(posedge clk);
            end
            if(de!==want_error) $fatal(1,"D-cache error addr=%h got=%b expected=%b",a,de,want_error);
            if(!wr && !want_error && dq!==expected)
                $fatal(1,"D-cache read addr=%h size=%d got=%h expected=%h",a,size,dq,expected);
            if(wr && !want_error) begin
                byte_count=(size==0) ? 1 : (size==1) ? 2 : 4;
                for(j=0;j<byte_count;j=j+1) oracle[a+j]=(value >> (8*j))&255;
            end
            checks=checks+1;
            @(negedge clk);dv=0;
        end
    endtask
    task instruction;
        input wr;input[31:0] a;input[2:0] size;input want_error;input[31:0] expected;
        integer wait_cycles;
        begin
            @(negedge clk);iv=1;iw=wr;ia=a;idata=32'hffffffff;isize=size;
            wait_cycles=0;@(posedge clk);
            while(!ir) begin
                wait_cycles=wait_cycles+1;
                if(wait_cycles>1000) $fatal(1,"I-cache timeout");
                @(posedge clk);
            end
            if(ie!==want_error || (!want_error && iq!==expected))
                $fatal(1,"I-cache mismatch addr=%h err=%b data=%h expected=%h",a,ie,iq,expected);
            last_i_wait_cycles=wait_cycles;
            checks=checks+1;
            @(negedge clk);iv=0;
        end
    endtask
    task invalidate_data;
        integer wait_cycles;
        begin
            @(negedge clk); inv=1;
            wait_cycles=0;@(posedge clk);
            while(!inv_done) begin
                wait_cycles=wait_cycles+1;
                if(wait_cycles>1000) $fatal(1,"Invalidate timeout");
                @(posedge clk);
            end
            @(negedge clk);inv=0;
            if(dut.valid0!==0 || dut.valid1!==0) $fatal(1,"Invalidation left valid lines");
            checks=checks+1;
        end
    endtask

    initial begin
        for(n=0;n<4096;n=n+1) oracle[n]=(n*37+11)&255;
        repeat(4) @(negedge clk);rst=0;
        // Two ways must coexist in one set; a third tag evicts the LRU only.
        access(0,32'h00,0,2,0);
        access(0,32'h0c,0,2,0);
        access(0,32'h20,0,2,0);
        access(0,32'h00,0,2,0);
        access(0,32'h40,0,2,0);
        access(0,32'h00,0,2,0);
        access(0,32'h20,0,2,0);
        if(hits!=3 || misses!=4 || fill_words!=16) $fatal(1,"Two-way/LRU accounting wrong h=%d m=%d f=%d",hits,misses,fill_words);
        checks=checks+1;
        // Byte/halfword signed and unsigned loads, byte enables and write-through.
        access(1,32'h01,32'h80,0,0);
        access(0,32'h01,0,0,0); access(0,32'h01,0,4,0);
        access(1,32'h02,32'h8123,1,0);
        access(0,32'h02,0,1,0); access(0,32'h02,0,5,0);
        access(0,32'h00,0,2,0);
        access(1,32'h04,32'hdeadbeef,2,0);access(0,32'h04,0,2,0);
        access(1,32'h83,32'h7f,0,0);access(0,32'h83,0,4,0);
        if(bwrites!=4 || write_words!=4) $fatal(1,"Write-through count mismatch");
        // Malformed accesses must not touch the backend.
        saved_count=bread+bwrites;
        access(0,32'h01,0,1,1);access(0,32'h02,0,2,1);
        access(1,32'h03,32'h1234,1,1);access(1,32'h02,32'h1234,2,1);
        access(1,32'h00,32'h1234,3,1);access(0,32'h00,0,6,1);
        access(0,32'h1000,0,2,1);
        if(bread+bwrites!=saved_count) $fatal(1,"Bad access reached backend");
        checks=checks+1;
        // Partial refill error: the next read must miss and refill the whole line.
        bad_read=1;bad_address=32'h108;
        access(0,32'h100,0,2,1);
        bad_read=0;saved_count=misses;
        access(0,32'h10c,0,2,0);
        if(misses!=saved_count+1) $fatal(1,"Failed refill left a valid line");
        checks=checks+1;
        // Failed write must not update memory or cache, and discards the line.
        access(0,32'h180,0,2,0);
        bad_write=1;bad_address=32'h181;
        access(1,32'h181,32'h55,0,1);
        bad_write=0;saved_count=misses;
        access(0,32'h180,0,2,0);
        if(misses!=saved_count+1) $fatal(1,"Failed write did not invalidate line");
        checks=checks+1;
        // DMA writes are externally coherent only after explicit invalidation.
        access(0,32'h200,0,2,0);stale_word=golden_read(32'h200,2);
        @(negedge clk);backend.mem[32'h200/4]=32'h12345678;
        oracle[32'h200]=8'h78;oracle[32'h201]=8'h56;oracle[32'h202]=8'h34;oracle[32'h203]=8'h12;
        override_expected=1;overridden_value=stale_word;
        access(0,32'h200,0,2,0);override_expected=0;
        invalidate_data();access(0,32'h200,0,2,0);
        // A one-cycle invalidation pulse during a refill must be remembered.
        fork
            begin access(0,32'h280,0,2,0); end
            begin
                wait(bv);@(negedge clk);inv=1;@(negedge clk);inv=0;
                wait(inv_done);@(negedge clk);
            end
        join
        if(dut.valid0!==0 || dut.valid1!==0) $fatal(1,"Queued invalidation was lost");
        checks=checks+1;
        // Independent instruction cache, including read-only protections.
        instruction(0,32'h000,2,0,ibackend.mem[0]);
        // I backing memory has not observed D writes: compare its own word.
        instruction(0,32'h004,2,0,ibackend.mem[1]);
        instruction(1,32'h000,2,1,0);instruction(0,32'h000,0,1,0);
        instruction(0,32'h002,2,1,0);instruction(0,32'h1000,2,1,0);
        if(ibwrites!=0 || iwrites!=0 || ihits==0 || imisses!=1) $fatal(1,"I-cache protection/accounting");
        checks=checks+1;
        @(negedge clk);ibackend.mem[0]=32'hface0123;iinv=1;
        wait(iinv_done);@(negedge clk);iinv=0;
        instruction(0,32'h000,2,0,32'hface0123);
        instruction(0,32'h000,2,0,32'hface0123);
        if(last_i_wait_cycles!=1) $fatal(1,"Synchronous cache hit added lookup bubbles: %d",last_i_wait_cycles);
        checks=checks+1;
        locality_start_hits=ihits;locality_start_misses=imisses;locality_start_reads=ibreads;
        for(k=0;k<128;k=k+1)
            instruction(0,32'h300+(k%4)*4,2,0,ibackend.mem[32'h300/4+(k%4)]);
        if(ihits-locality_start_hits!=127 || imisses-locality_start_misses!=1 || ibreads-locality_start_reads!=4)
            $fatal(1,"Locality traffic reduction mismatch");
        checks=checks+1;
        @(negedge clk);zv=1;wait(zr);@(posedge clk);
        if(ze || zq!==(32'h304 ^ 32'h5a5aa5a5) || burst_beats!=4)
            $fatal(1,"Zero-wait burst completion mismatch");
        @(negedge clk);zv=0;checks=checks+1;
        // 800 deterministic randomized requests with independent byte oracle.
        for(k=0;k<800;k=k+1) begin
            rng=random_next(rng);random_write=rng[0];
            rng=random_next(rng);random_size=rng%3;
            if(!random_write && random_size<2 && rng[4]) random_size=random_size+4;
            rng=random_next(rng);random_address=rng&32'h7ff;
            if(random_size[1:0]==1) random_address=random_address&32'hfffffffe;
            if(random_size[1:0]==2) random_address=random_address&32'hfffffffc;
            rng=random_next(rng);random_data=rng;
            access(random_write,random_address,random_data,random_size,0);
        end
        // Every write was observed exactly once, including all untouched bytes.
        for(n=0;n<1024;n=n+1)
            if(backend.mem[n]!=={oracle[n*4+3],oracle[n*4+2],oracle[n*4+1],oracle[n*4]})
                $fatal(1,"Final backend memory differs from byte oracle word=%d",n);
        if(write_words!=bwrites || fill_words!=bread) $fatal(1,"Completion statistics mismatch");
        checks=checks+1024;
        if(preview_completions<20)$fatal(1,"Data preview path unexercised");
        $display("DATA_PREVIEW completions=%0d",preview_completions);
        $display("PASS: 2-way I/D cache; checks=%0d randomized=800 D_hits=%0d D_misses=%0d refill_words=%0d writes=%0d; locality_requests=128 locality_backend_reads=4 hit_wait_cycles=1 burst_beats=4 burst_cycles=4; LRU, byte lanes, waits, errors, DMA invalidation",checks,hits,misses,fill_words,write_words);
        $finish;
    end
    initial begin #2000000;$fatal(1,"Cache regression global timeout");end
endmodule
