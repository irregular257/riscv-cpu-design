`timescale 1ns/1fs
module tb_cpu_clock;
    reg ref_clk=0, reset_n=0;
    wire clk,locked;
    reg [7:0] por=8'hff;
    wire run_n=reset_n && locked;
    always #18.518518518 ref_clk=~ref_clk;
    cpu_clock #(.FB_DIV(44),.OUT_DIV(18)) dut(ref_clk,reset_n,clk,locked);
    always @(posedge clk or negedge run_n)
        if(!run_n) por<=8'hff; else por<={por[6:0],1'b0};
    realtime first_edge, elapsed;
    integer i;
    task check_clock;
      begin
        wait(locked===1'b1);
        repeat(20) @(posedge clk);
        if(por!==0) $fatal(1,"reset failed to release");
        first_edge=$realtime; $display("FIRST_EDGE=%f count=%d",first_edge,out_count);
        repeat(10) @(posedge clk);
        elapsed=$realtime-first_edge;
        if(elapsed<151.5149515151515 || elapsed>151.5153515151515)
          $fatal(1,"unexpected 66 MHz period: 10 periods = %f ns",elapsed);
        $display("CLOCK_PASS 10_periods_ns=%f locked=%b",elapsed,locked);
      end
    endtask
    initial begin
      #1000 reset_n=1;
      check_clock;
      #7 reset_n=0;
      #1 if(por!==8'hff) $fatal(1,"reset failed to assert");
      #1000 reset_n=1;
      check_clock;
      $display("CLOCK_RESET_PASS"); $finish;
    end
    initial begin #10000000; $fatal(1,"PLL timeout"); end

 integer out_count=0;
 always @(posedge clk) begin out_count=out_count+1; $display("EDGE count=%d t=%f cphase=%0.16f",out_count,$realtime,dut.u_pll.cphase_delay0); end
 always @(dut.u_pll.cphase_delay0) if(dut.u_pll.cphase_delay0<0) $display("NEGATIVE_CPHASE t=%f delay=%0.16f",$realtime,dut.u_pll.cphase_delay0);
 initial begin
  $display("AUDIT_START");
  #100 $display("AUDIT_100 t=%t reset=%b lock=%b rst_n=%b fbdiv=%f period=%f done=%b midd=%0d",$realtime,reset_n,locked,dut.u_pll.rst_n,dut.u_pll.fbdiv_set_int,dut.u_pll.vcoclk_period,dut.u_pll.done,dut.u_pll.midd_set);
  #900 $display("AUDIT_1000 t=%t reset=%b lock=%b",$realtime,reset_n,locked);
  #500 $display("AUDIT_1500 t=%t reset=%b lock=%b rst_n=%b fbdiv=%f period=%f done=%b midd=%0d gate=%b",$realtime,reset_n,locked,dut.u_pll.rst_n,dut.u_pll.fbdiv_set_int,dut.u_pll.vcoclk_period,dut.u_pll.done,dut.u_pll.midd_set,dut.u_pll.clk_gate);
  #500 $display("AUDIT_2000 t=%t reset=%b lock=%b out_count=%d divider=%d odiv_cnt=%d gate=%b",$realtime,reset_n,locked,out_count,dut.u_pll.divider0,dut.u_pll.odiv0_counter,dut.u_pll.clk_out0_gate);
  #1000 $display("AUDIT_3000 t=%t reset=%b lock=%b out_count=%d",$realtime,reset_n,locked,out_count);
  #1000 $display("AUDIT_4000 t=%t reset=%b lock=%b out_count=%d",$realtime,reset_n,locked,out_count);
  #1000 $display("AUDIT_5000 t=%t reset=%b lock=%b out_count=%d",$realtime,reset_n,locked,out_count);
 end
endmodule
