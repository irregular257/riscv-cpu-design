`timescale 1ns/1ps
// Logos2 GPLL: reference 27 MHz; VCO=27*FB_DIV MHz, output=VCO/OUT_DIV.
// The supported integer ratios keep the GPLL VCO within 600..1200 MHz.
module cpu_clock #(
    parameter integer FB_DIV=32, OUT_DIV=27
)(input wire ref_clk, reset_n, output wire clk, locked);
    GTP_GPLL #(
        .CLKIN_FREQ(27.0), .STATIC_RATIOI(1), .STATIC_RATIOM(1),
        .STATIC_RATIOF(FB_DIV), .STATIC_DUTYF(FB_DIV),
        .STATIC_RATIO0(OUT_DIV), .STATIC_DUTY0(OUT_DIV),
        .STATIC_RATIO1(8), .STATIC_DUTY1(8),
        .STATIC_RATIO2(8), .STATIC_DUTY2(8),
        .STATIC_RATIO3(8), .STATIC_DUTY3(8),
        .STATIC_RATIO4(8), .STATIC_DUTY4(8),
        .STATIC_RATIO5(8), .STATIC_DUTY5(8),
        .STATIC_RATIO6(8), .STATIC_DUTY6(8),
        .INTERNAL_FB("CLKOUTF"), .EXTERNAL_FB("DISABLE")
    ) u_pll (
        .CLKIN1(ref_clk), .CLKIN2(1'b0), .CLKIN_SEL(1'b0), .CLKFB(1'b0),
        .CLKOUT0(clk), .LOCK(locked), .PLL_PWD(1'b0), .RST(!reset_n),
        .DPS_CLK(1'b0), .DPS_EN(1'b0), .DPS_DIR(1'b0),
        .CLKOUT0_SYN(1'b0), .CLKOUT1_SYN(1'b0), .CLKOUT2_SYN(1'b0),
        .CLKOUT3_SYN(1'b0), .CLKOUT4_SYN(1'b0), .CLKOUT5_SYN(1'b0),
        .CLKOUT6_SYN(1'b0), .CLKOUTF_SYN(1'b0),
        .APB_CLK(1'b0), .APB_RST_N(1'b0), .APB_ADDR(5'b0),
        .APB_SEL(1'b0), .APB_EN(1'b0), .APB_WRITE(1'b0), .APB_WDATA(16'b0)
    );
endmodule
