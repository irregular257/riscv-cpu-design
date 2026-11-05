`timescale 1ns/1ps
// Same deterministic traffic runs against optimized and baseline parameters.
// Oracle is the backing array; no expected data are taken from the cache.
module tb_cache_lookahead;
    parameter integer LOOKAHEAD=1, FORWARD=1;
    reg clk=0; always #5 clk=~clk;
    reg rst=1,valid=0,write=0,invalidate=0;
    reg [31:0] addr=0,wdata=0;
    reg [2:0] size=2;
    wire ready,error,done,busy,bv,bw;
    wire [31:0] rdata,ba,bd;
    wire [2:0] bs;
    wire [31:0] hits,misses,fills,writes;
    reg fault=0,wait_mode=0;
    reg [31:0] fault_addr=0;
    reg [31:0] mem[0:1023];
    integer cycle=0,checks=0,n,k,last_wait,start_cycle,stream_cycles,cold_wait;
    reg [31:0] old_misses;
    reg held=0; reg [31:0] held_addr;
    wire br=bv && (!wait_mode || cycle%3==0);
    wire be=br && fault && ba==fault_addr;
    wire [31:0] bq=mem[ba[11:2]];
    wire ready_ref;
    wire error_ref;
    wire done_ref;
    wire busy_ref;
    wire bv_ref;
    wire bw_ref;
    wire [31:0] rdata_ref;
    wire [31:0] ba_ref;
    wire [31:0] bd_ref;
    wire [2:0] bs_ref;
    wire [31:0] hits_ref;
    wire [31:0] misses_ref;
    wire [31:0] fills_ref;
    wire [31:0] writes_ref;
    cache_2way #(.READ_ONLY(1),.SETS(4),.LINE_WORDS(4),.ADDR_WIDTH(12),
      .READ_LOOKAHEAD(LOOKAHEAD),.REFILL_FORWARD(FORWARD)) dut(
      .clk(clk),.rst(rst),.req_valid(valid),.req_write(write),.req_addr(addr),.req_wdata(wdata),.req_funct3(size),
      .ready(ready),.error(error),.rdata(rdata),.bf_req_valid(bv),.bf_req_write(bw),.bf_req_addr(ba),
      .bf_req_wdata(bd),.bf_req_funct3(bs),.bf_ready(br),.bf_error(be),.bf_rdata(bq),
      .invalidate(invalidate),.invalidate_done(done),.busy(busy),.hit_count(hits),.miss_count(misses),
      .refill_word_count(fills),.backend_write_count(writes));
    cache_2way_reference #(.READ_ONLY(1),.SETS(4),.LINE_WORDS(4),.ADDR_WIDTH(12),
      .READ_LOOKAHEAD(LOOKAHEAD),.REFILL_FORWARD(FORWARD)) ref_dut(
      .clk(clk),.rst(rst),.req_valid(valid),.req_write(write),.req_addr(addr),.req_wdata(wdata),.req_funct3(size),
      .ready(ready_ref),.error(error_ref),.rdata(rdata_ref),.bf_req_valid(bv_ref),.bf_req_write(bw_ref),.bf_req_addr(ba_ref),
      .bf_req_wdata(bd_ref),.bf_req_funct3(bs_ref),.bf_ready(br),.bf_error(be),.bf_rdata(bq),
      .invalidate(invalidate),.invalidate_done(done_ref),.busy(busy_ref),.hit_count(hits_ref),.miss_count(misses_ref),
      .refill_word_count(fills_ref),.backend_write_count(writes_ref));
    integer equivalent_cycles=0,equivalent_handshakes=0,idle_data_differences=0;
    always @(posedge clk) if(!rst) begin
        if({ready,error,done,busy,bv,bw,ba,bd,bs,hits,misses,fills,writes} !== {ready_ref,error_ref,done_ref,busy_ref,bv_ref,bw_ref,ba_ref,bd_ref,bs_ref,hits_ref,misses_ref,fills_ref,writes_ref}) $fatal(1,"Control/backend mismatch");
        if({dut.state,dut.valid0,dut.valid1,dut.lru_victim,dut.saved_write,dut.saved_addr,dut.saved_wdata,dut.saved_funct3,dut.selected_way,dut.refill_index,dut.response_data,dut.refill_requested_word,dut.response_error,dut.lookahead_valid,dut.lookahead_addr,dut.invalidate_pending,dut.invalidate_seen,dut.invalidate_index} !== {ref_dut.state,ref_dut.valid0,ref_dut.valid1,ref_dut.lru_victim,ref_dut.saved_write,ref_dut.saved_addr,ref_dut.saved_wdata,ref_dut.saved_funct3,ref_dut.selected_way,ref_dut.refill_index,ref_dut.response_data,ref_dut.refill_requested_word,ref_dut.response_error,ref_dut.lookahead_valid,ref_dut.lookahead_addr,ref_dut.invalidate_pending,ref_dut.invalidate_seen,ref_dut.invalidate_index}) $fatal(1,"Sequential metadata mismatch");
        if(valid && ready) begin
            if(rdata !== rdata_ref) $fatal(1,"Handshake rdata mismatch");
            equivalent_handshakes=equivalent_handshakes+1;
        end else if(rdata !== rdata_ref) idle_data_differences=idle_data_differences+1;
        equivalent_cycles=equivalent_cycles+1;
    end

    always @(posedge clk) begin
      cycle=cycle+1;
      if(rst) held<=0;
      else begin
        if(held && (!bv || ba!==held_addr)) $fatal(1,"Backend request changed under wait");
        if(bv && (bw || bs!=2)) $fatal(1,"Read-only cache issued non-word read");
        held<=bv && !br; held_addr<=ba;
      end
    end
    task access;
      input [31:0] a; input wr; input [2:0] f; input want_error;
      reg [31:0] expected;
      begin
        expected=mem[a[11:2]];
        @(negedge clk);valid=1;addr=a;write=wr;size=f;
        last_wait=0;
        @(posedge clk);
        while(!ready) begin
          last_wait=last_wait+1;
          if(last_wait>200) $fatal(1,"Timeout addr=%h state=%d",a,dut.state);
          @(posedge clk);
        end
        if(error!==want_error) $fatal(1,"Error mismatch addr=%h error=%b expected=%b",a,error,want_error);
        if(!want_error && rdata!==expected) $fatal(1,"Data mismatch addr=%h got=%h expected=%h",a,rdata,expected);
        checks=checks+1;
      end
    endtask
    task gap;
      input integer cycles;
      begin @(negedge clk);valid=0;repeat(cycles) @(negedge clk); end
    endtask
    task invalidate_all;
      begin
        @(negedge clk);valid=0;invalidate=1;
        wait(done);@(negedge clk);invalidate=0;
        if(dut.lookahead_valid || dut.valid0!=0 || dut.valid1!=0) $fatal(1,"Invalidation left cached data valid");
        checks=checks+1;
      end
    endtask
    initial begin
      for(n=0;n<1024;n=n+1) mem[n]=32'hd00d0000 ^ (n*32'h10203);
      repeat(3) @(negedge clk);rst=0;
      // Requested word may be first, middle, or final beat of a refill.
      access(0,0,2,0);cold_wait=last_wait;
      if(last_wait!=(FORWARD ? 5 : 6)) $fatal(1,"Cold refill latency wrong %0d",last_wait);
      access(32'h24,0,2,0);access(32'h4c,0,2,0);
      // Repeated addresses, arbitrary branches and idle cycles must never
      // interpret the one-word sequential lookahead as a wildcard response.
      access(0,0,2,0);access(0,0,2,0);
      if(last_wait!=1) $fatal(1,"Repeated address incorrectly treated as next word");
      access(4,0,2,0);
      if(last_wait!=(LOOKAHEAD ? 0 : 1)) $fatal(1,"Sequential hit latency wrong");
      gap(9);access(8,0,2,0);
      if(last_wait!=(LOOKAHEAD ? 0 : 1)) $fatal(1,"Lookahead lost across idle gap");
      access(0,0,2,0);access(12,0,2,0);access(4,0,2,0);
      // Consecutive replacement misses must mark each installed line MRU.
      // Otherwise a forwarded miss can repeatedly evict the same way.
      invalidate_all();
      access(0,0,2,0);access(32'h40,0,2,0);
      access(32'h80,0,2,0);access(32'hc0,0,2,0);
      old_misses=misses;
      access(32'h80,0,2,0);access(32'hc0,0,2,0);
      if(misses!=old_misses) $fatal(1,"Refill completion failed to update LRU recency");
      checks=checks+1;
      // Warm both ways of every set, then stream across line/set/tag changes.
      invalidate_all();
      for(k=0;k<32;k=k+1) access(k*4,0,2,0);
      old_misses=misses;start_cycle=cycle;
      for(k=0;k<128;k=k+1) access((k%32)*4,0,2,0);
      stream_cycles=cycle-start_cycle;
      if(misses!=old_misses) $fatal(1,"Warm stream unexpectedly missed");
      if(stream_cycles!=(LOOKAHEAD ? 132 : 256)) $fatal(1,"Stream latency mismatch %0d",stream_cycles);
      // Legal lookahead address followed by an illegal size/write/misalignment
      // must produce errors, not expose prefetched data as a successful read.
      access(0,0,2,0);access(4,1,2,1);
      access(0,0,2,0);access(4,0,0,1);
      access(0,0,2,0);access(6,0,2,1);
      access(32'hffc,0,2,0);access(32'h1000,0,2,1);
      // Invalidate on the exact cycle the next request could have fast-hit.
      access(0,0,2,0);
      @(negedge clk);invalidate=1;valid=1;addr=4;mem[1]=32'h1234abcd;
      #1;if(ready) $fatal(1,"Fresh invalidate did not block lookahead completion");
      wait(done);@(negedge clk);invalidate=0;
      @(posedge clk);while(!ready) @(posedge clk);
      if(error || rdata!==mem[1]) $fatal(1,"Stale instruction after invalidate");
      checks=checks+1;
      // A late refill error must win even after requested first word arrived.
      invalidate_all();fault=1;fault_addr=32'h20c;
      access(32'h200,0,2,1);fault=0;old_misses=misses;
      access(32'h200,0,2,0);
      if(misses!=old_misses+1) $fatal(1,"Failed refill left a valid cache line");
      // Queued invalidation while a stalled refill is active; original request
      // must finish once, then invalidate every word including lookahead.
      wait_mode=1;
      fork
        begin access(32'h300,0,2,0);gap(1);end
        begin wait(bv);@(negedge clk);invalidate=1;@(negedge clk);invalidate=0;wait(done);end
      join
      @(negedge clk);mem[32'h304/4]=32'h7777aaaa;old_misses=misses;
      access(32'h304,0,2,0);
      if(misses!=old_misses+1) $fatal(1,"Queued invalidate did not discard prefetched word");
      wait_mode=0;
      // Reset discards pending response and speculative word state.
      access(32'h300,0,2,0);
      @(negedge clk);valid=0;rst=1;mem[32'h304/4]=32'hcafef00d;
      repeat(2) @(negedge clk);rst=0;
      access(32'h304,0,2,0);
      if(misses!=1) $fatal(1,"Reset did not discard cached data");
      gap(1);
      $display("PASS CACHE_LOOKAHEAD lookahead=%0d forward=%0d checks=%0d stream_requests=128 stream_cycles=%0d cold_wait=%0d",LOOKAHEAD,FORWARD,checks,stream_cycles,cold_wait);
      $display("EQUIVALENCE cycles=%0d handshakes=%0d idle_data_differences=%0d",equivalent_cycles,equivalent_handshakes,idle_data_differences); $finish;
    end
    initial begin #2000000;$fatal(1,"Lookahead test global timeout");end
endmodule
