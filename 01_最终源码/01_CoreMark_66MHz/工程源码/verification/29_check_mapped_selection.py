"""Check actual PDS LUTs retain speculative data mux, independent of req_valid."""
from pathlib import Path
import hashlib,json,re
ROOT=Path('D:/codex_rv32_advanced_20261028')
BASE=ROOT/'reports/maxperf_20261103'
runs={}
for kind,top in [('coremark','advanced_top'),('multimodal','multimodal_top')]:
    folder=BASE/('pds_decouple48_'+kind)/'work/synthesize'
    net=folder/(top+'_syn.vm');text=net.read_text()
    cache=re.search(r'(?m)^module cache_2way\s[\s\S]*?^endmodule\b',text).group()
    # PDS uses a dual-output LUT: Z6 has constant I5=1, so only upper half
    # of INIT applies to the data select. Check every remaining input pattern.
    select=re.search(r'GTP_LUT6D\s+#\(\s*\.INIT\(64\x27h([0-9A-Fa-f]+)\)\)\s+N280\s*\(\s*\.Z\s*\(lookahead_data_select\)[\s\S]*?\);',cache)
    assert select
    ports=dict(re.findall(r'\.(\w+)\s*\(([^)]*)\)',select.group()))
    assert ports['I1']=='lookahead_valid' and ports['I2']=='state_reg[0]' and ports['I5']=="1'b1"
    init=int(select[1],16)
    for bits in range(32):assert (init>>(bits|32))&1==((bits>>1)&1)&((bits>>2)&1)
    checked=[]
    for bit in range(32):
        mux=re.search(r'GTP_LUT5\s+#\(\s*\.INIT\(32\x27h([0-9A-Fa-f]+)\)\)\s+\\rdata_2\['+str(bit)+r'\]\s+\(\s*\.Z\s*\(rdata\['+str(bit)+r'\]\)[\s\S]*?\);',cache)
        assert mux
        ports=dict(re.findall(r'\.(\w+)\s*\(([^)]*)\)',mux.group()))
        assert ports['I0']=='hit0' and ports['I1']=='lookahead_data_select'
        assert ports['I2']==f'read0[{bit}]' and ports['I3']==f'read1[{bit}]'
        init=int(mux[1],16)
        for bits in range(32):
            hit,sel,a,b,other=[(bits>>n)&1 for n in range(5)]
            expected=(a if hit else b) if sel else other
            assert (init>>bits)&1==expected
        checked.append(bit)
    snr=folder/(top+'.snr');report=snr.read_text()
    perf=re.search(r'cpu_clk\s+48\.0000 MHz\s+([\d.]+) MHz\s+20\.8333\s+([\d.]+)\s+([\d.]+)',report)
    assert perf
    first=re.search(r'Startpoint\s*:\s*(.*?)\nEndpoint\s*:\s*(.*?)\n',report)
    runs[kind]={'netlist_path':str(net),'netlist_sha256':hashlib.sha256(net.read_bytes()).hexdigest(),
      'icache_rdata_bits_checked':checked,'mux_truth_table_assignments':32*32+32,
      'select_expression':'lookahead_valid & state_reg[0]; no req_valid or lookahead_hit',
      'synthesis_report':str(snr),'synthesis_report_sha256':hashlib.sha256(snr.read_bytes()).hexdigest(),
      'synthesis_estimated_mhz':float(perf[1]),'synthesis_setup_slack_ns':float(perf[3]),
      'synthesis_worst_startpoint':first[1],'synthesis_worst_endpoint':first[2]}
summary={'status':'PASS_ACTUAL_SYNTH_DATA_SELECTION','runs':runs,
 'scope':'Actual mapped final 32-bit I-cache data mux and select LUT truth tables verified. This confirms the previous req_valid/lookahead_hit data-select dependency is removed. SNR figures are pre-route estimates, not final timing closure or physical board measurements.'}
out=BASE/'icache_data_decouple/mapped_selection48.json'
out.write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print('PASS mapped I-cache selection in both actual 48 MHz netlists')
