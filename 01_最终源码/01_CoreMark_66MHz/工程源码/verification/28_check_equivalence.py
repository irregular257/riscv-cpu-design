"""Handshake equivalence of frozen and decoupled I-cache muxes."""
from pathlib import Path
import hashlib
import itertools
import json
import re
import subprocess

ROOT=Path('D:/codex_rv32_advanced_20261028')
BASE=ROOT/'reports/maxperf_20261103/icache_data_decouple'
OLD=ROOT/'reports/maxperf_20261103/selected_bram_i8_d4/rtl/cache_2way.v'
NEW=BASE/'rtl/cache_2way.v'
OUT=BASE/'equivalence'
OUT.mkdir(exist_ok=True)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(args,path):
    p=subprocess.run(list(map(str,args)),cwd=ROOT,capture_output=True,text=True,timeout=60)
    path.write_text(p.stdout+p.stderr,encoding='utf-8');p.check_returncode()
    return p.stdout+p.stderr

# Exhaust all Boolean qualifications, including unreachable state combinations.
# Data symbols represent identical combinational data expressions in both RTLs.
comb=0
for ro,la,pv,wf,rst,valid,lav,addr_match,tag,bad,ip,fi,br in itertools.product((0,1),repeat=13):
    for state in range(16):
        for write in (0,1):
            lh=ro and la and not rst and state==0 and valid and lav and addr_match and tag and not bad and not ip and not fi
            ph=not ro and pv and not rst and state==0 and valid and not write and lav and addr_match and tag and not bad and not ip and not fi
            sc=not rst and state==4 and br
            ready=not rst and (state in (2,5) or lh or ph or (wf and sc))
            ds=ro and la and state==0 and lav
            old='preview' if ph else 'zero' if wf and sc else 'lookahead' if lh else 'access' if state==2 else 'response'
            new='preview' if ph else 'zero' if wf and sc else 'lookahead' if ds else 'access' if state==2 else 'response'
            if valid and ready:assert old==new
            if not ro:assert old==new
            comb+=1

# Instantiate old/new RTL in lockstep under real refill/error/invalidate traffic.
# Compare every external control/backend output each cycle, data on handshakes,
# and all scalar state metadata (data RAM is checked by the independent oracle).
ref=OUT/'cache_2way_reference.v'
ref.write_text(OLD.read_text().replace('module cache_2way #(', 'module cache_2way_reference #(',1),encoding='utf-8')
source=(ROOT/'sim/tb_cache_lookahead.v').read_text()
inst=re.search(r'    cache_2way #\([\s\S]*?backend_write_count\(writes\)\);',source).group()
outputs={'ready':1,'error':1,'done':1,'busy':1,'bv':1,'bw':1,'rdata':32,'ba':32,'bd':32,'bs':3,'hits':32,'misses':32,'fills':32,'writes':32}
other=inst.replace('cache_2way #(', 'cache_2way_reference #(',1).replace(')) dut(', ')) ref_dut(',1)
for name in outputs:other=other.replace('('+name+')','('+name+'_ref)')
decl='\n'.join('    wire '+('['+str(w-1)+':0] ' if w>1 else '')+n+'_ref;' for n,w in outputs.items())
control=','.join(n for n in outputs if n!='rdata')
control_ref=','.join(n+'_ref' for n in outputs if n!='rdata')
state_fields=('state','valid0','valid1','lru_victim','saved_write','saved_addr','saved_wdata','saved_funct3',
              'selected_way','refill_index','response_data','refill_requested_word','response_error',
              'lookahead_valid','lookahead_addr','invalidate_pending','invalidate_seen','invalidate_index')
state=','.join('dut.'+n for n in state_fields)
state_ref=','.join('ref_dut.'+n for n in state_fields)
checks=f'''
    integer equivalent_cycles=0,equivalent_handshakes=0,idle_data_differences=0;
    always @(posedge clk) if(!rst) begin
        if({{{control}}} !== {{{control_ref}}}) $fatal(1,"Control/backend mismatch");
        if({{{state}}} !== {{{state_ref}}}) $fatal(1,"Sequential metadata mismatch");
        if(valid && ready) begin
            if(rdata !== rdata_ref) $fatal(1,"Handshake rdata mismatch");
            equivalent_handshakes=equivalent_handshakes+1;
        end else if(rdata !== rdata_ref) idle_data_differences=idle_data_differences+1;
        equivalent_cycles=equivalent_cycles+1;
    end
'''
source=source.replace(inst,decl+'\n'+inst+'\n'+other+checks)
source=source.replace('$finish;', '$display("EQUIVALENCE cycles=%0d handshakes=%0d idle_data_differences=%0d",equivalent_cycles,equivalent_handshakes,idle_data_differences); $finish;')
tb=OUT/'tb_cache_lookahead_equivalence.v';tb.write_text(source,encoding='utf-8')
runs=[]
for lookahead,forward in ((0,0),(0,1),(1,1)):
    name=f'la{lookahead}_forward{forward}';exe=OUT/(name+'.vvp')
    run(['D:/iverilog/bin/iverilog.exe','-g2005','-Wall','-s','tb_cache_lookahead',
         f'-Ptb_cache_lookahead.LOOKAHEAD={lookahead}',f'-Ptb_cache_lookahead.FORWARD={forward}',
         '-o',exe,NEW,ref,tb],OUT/(name+'_compile.log'))
    log=run(['D:/iverilog/bin/vvp.exe',exe],OUT/(name+'.log'))
    assert 'PASS CACHE_LOOKAHEAD' in log and 'EQUIVALENCE' in log
    count=re.search(r'EQUIVALENCE cycles=(\d+) handshakes=(\d+) idle_data_differences=(\d+)',log)
    values=list(map(int,count.groups()))
    if lookahead:assert values[2]>0
    runs.append(dict(lookahead=lookahead,refill_forward=forward,cycles=values[0],handshakes=values[1],idle_data_differences=values[2]))
summary={'status':'PASS','combinational_assignments':comb,'runs':runs,'old_sha256':sha(OLD),'new_sha256':sha(NEW),
 'scope':'Exhaustive Boolean mux implication plus lockstep RTL regression; no PDS timing or physical board result implied.',
 'proof':'Only I-cache rdata mux selection changes. When req_valid && ready in S_IDLE, lookahead_hit must be true, so speculative data selection is true and identical. In other states selection is false; D-cache selection always false. All sequential logic and ready/error equations are unchanged.',
 'consumer':'cpu_core fetch_keep gates id_inst capture and predicted branch redirect; a non-handshake rdata change cannot become an architectural instruction.'}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print(json.dumps(summary,indent=2))
