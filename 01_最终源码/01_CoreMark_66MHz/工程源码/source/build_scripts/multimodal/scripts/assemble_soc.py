"""Derive the isolated SoC from the unchanged, optimized baseline."""
import argparse
from pathlib import Path
M = Path(__file__).resolve().parents[1]
A = M.parent

def assemble(s):
    s = s.replace('module advanced_soc #(', 'module multimodal_soc #(')
    s = s.replace('output wire [31:0] commit_wdata\n', '''output wire [31:0] commit_wdata,
    output wire [3:0] DS_Matrix,G_Matrix,RCK_Matrix,SCLK_Matrix,SCLR_Matrix,
    output wire ext_valid,ext_write,
    output wire [31:0] ext_addr,ext_wdata,
    output wire [2:0] ext_funct3,
    input wire ext_ready,ext_error,
    input wire [31:0] ext_rdata,ddr_status
''')
    s = s.replace('wire direct_bus=dv && !data_ram && !periph_select && !stats_select;', '''wire matrix_select=da[31:4]==28'h1000040;
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
      .SCLK_Matrix(SCLK_Matrix),.SCLR_Matrix(SCLR_Matrix));''')
    s = s.replace('assign dr=data_ram ?', 'assign dr=ext_select ? ext_ready : matrix_select ? mr : ddr_status_select ? !rst : data_ram ?')
    s = s.replace('assign de=data_ram ?', "assign de=ext_select ? ext_error : matrix_select ? me : ddr_status_select ? (dw || df!=2 || da[3:0]!=0) : data_ram ?")
    s = s.replace('assign rd=data_ram ?', 'assign rd=ext_select ? ext_rdata : matrix_select ? md : ddr_status_select ? ddr_status : data_ram ?')
    # FENCE must include the DDR request drain; there is only one outstanding
    # in-order CPU memory operation, so no posted write survives ext_ready.
    return s

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=A/'rtl/advanced_soc.v')
    p.add_argument('--output',type=Path,default=M/'rtl/multimodal_soc.v')
    a=p.parse_args()
    s=assemble(a.source.read_text(encoding='utf-8'))
    assert s.count('module multimodal_soc #(')==1
    assert 'matrix_display #' in s and 'assign ext_valid=' in s
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(s,encoding='utf-8')

if __name__=='__main__': main()
