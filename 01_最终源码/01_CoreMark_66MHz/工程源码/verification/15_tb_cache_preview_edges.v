`timescale 1ns/1ps
module tb_cache_preview_edges;
 reg clk=0;always #5 clk=~clk;
 reg rst=1,dv=0,dw=0,pv=0,inv=0,badw=0;
 reg [31:0] da=0,wd=0,pa=0;
 reg [2:0] sz=2;
 wire dr,de,bv,bw,br,be,done;
 wire [31:0] data,ba,bdata,bq,reads,writes;
 wire [2:0] bs;
 integer checks=0,n;
 cache_2way #(.SETS(2),.ADDR_WIDTH(12),.DATA_PREVIEW(1),.WRITE_RESPONSE_FORWARD(1)) dut(
   .clk(clk),.rst(rst),.req_valid(dv),.req_write(dw),.req_addr(da),.req_wdata(wd),.req_funct3(sz),
   .ready(dr),.error(de),.rdata(data),.bf_req_valid(bv),.bf_req_write(bw),.bf_req_addr(ba),
   .bf_req_wdata(bdata),.bf_req_funct3(bs),.bf_ready(br),.bf_error(be),.bf_rdata(bq),
   .invalidate(inv),.invalidate_done(done),.busy(),.hit_count(),.miss_count(),.refill_word_count(),
   .backend_write_count(),.preview_valid(pv),.preview_addr(pa));
 cache_test_backend backend(.clk(clk),.rst(rst),.valid(bv),.write(bw),.addr(ba),.wdata(bdata),.funct3(bs),
   .ready(br),.error(be),.rdata(bq),.inject_read(1'b0),.inject_write(badw),.fault_addr(32'h04),
   .read_completions(reads),.write_completions(writes));
 task hint;
 input[31:0] a;
 begin @(negedge clk);pv=1;pa=a;@(negedge clk);pv=0;end
 endtask
 task access;
 input wr;input[31:0] a,value,expected;input[2:0] size;input err;
 integer waits;
 begin
  @(negedge clk);dv=1;dw=wr;da=a;wd=value;sz=size;
  @(posedge clk);waits=0;
  while(!dr)begin @(posedge clk);waits=waits+1;if(waits>200)$fatal(1,"Timeout");end
  if(de!==err || (!wr&&!err&&data!==expected))$fatal(1,"Mismatch addr=%h data=%h expected=%h error=%b",a,data,expected,de);
  checks=checks+1;@(negedge clk);dv=0;
 end
 endtask
 task invalidate;
 begin @(negedge clk);inv=1;@(posedge clk);while(!done)@(posedge clk);@(negedge clk);inv=0;end
 endtask
 initial begin
  repeat(4)@(negedge clk);rst=0;
  access(0,0,0,32'h7a55300b,2,0);
  access(0,4,0,32'h0ee9c49f,2,0);
  // One-cycle consecutive loads use only the single clocked RAM port. Each
  // completed load overlaps the independent hint for the next address.
  hint(0);
  @(negedge clk);dv=1;dw=0;da=0;sz=2;pv=1;pa=4;
  @(posedge clk);if(!dr||de||data!==32'h7a55300b)$fatal(1,"First preview did not hit");checks=checks+1;
  @(negedge clk);da=4;pa=0;
  @(posedge clk);if(!dr||de||data!==32'h0ee9c49f)$fatal(1,"Consecutive preview did not hit");checks=checks+1;
  @(negedge clk);da=1;sz=4;pv=0;
  @(posedge clk);if(!dr||de||data!==32'h30)$fatal(1,"Subword preview failed");checks=checks+1;
  @(negedge clk);dv=0;
  // A wrong address hint must fall back to demand lookup, never return the
  // hinted word just because its tag matches the same cache line.
  hint(0);access(0,4,0,32'h0ee9c49f,2,0);
  // A store and its hint target the same word on the backend completion edge.
  // The RAM's read-during-write mode must not determine the next load value.
  fork
   access(1,0,32'hdeadbeef,0,2,0);
   begin wait(bv&&bw);@(negedge clk);pv=1;pa=0;wait(br);@(negedge clk);pv=0;end
  join
  access(0,0,0,32'hdeadbeef,2,0);
  // Rejected stores must not acknowledge success or retain stale preview data.
  hint(4);badw=1;
  fork
   access(1,4,32'h12345678,0,2,1);
   begin wait(bv&&bw);@(negedge clk);pv=1;pa=4;wait(br);@(negedge clk);pv=0;end
  join
  badw=0;access(0,4,0,32'h0ee9c49f,2,0);
  // A fresh invalidation cancels a prepared hint before demand acceptance.
  hint(0);
  fork
   invalidate();
   begin @(negedge clk);pv=1;pa=0;wait(done);@(negedge clk);pv=0;end
  join
  @(negedge clk);backend.mem[0]=32'h11223344;
  access(0,0,0,32'h11223344,2,0);
  // Reset likewise drops a prepared hint without touching backing memory.
  hint(0);@(negedge clk);rst=1;repeat(2)@(negedge clk);rst=0;
  @(negedge clk);backend.mem[0]=32'h55667788;
  access(0,0,0,32'h55667788,2,0);
  // Out-of-range or misaligned loads cannot consume a coincident hint.
  hint(0);access(0,2,0,0,2,1);hint(4096);access(0,4096,0,0,2,1);
  $display("PASS DATA_PREVIEW_EDGES checks=%0d",checks);$finish;
 end
 initial begin #1000000;$fatal(1,"Global timeout");end
endmodule
