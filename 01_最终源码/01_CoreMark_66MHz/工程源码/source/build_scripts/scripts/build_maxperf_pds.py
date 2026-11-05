"""Prepare/run immutable PG2L200H performance candidates; never publish unchecked bits."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time

import package_advanced as pack

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path('D:/codex_rv32_advanced_20261028')
PDS = Path('D:/pango/PDS_2023.2-SP3-ads/bin/pds_shell.exe')
BASE = ROOT/'reports/performance_20261103/selected/pds_multimodal/work'
CLOCK = ROOT/'reports/maxperf_20261103/clock/cpu_clock.v'
RATIOS = {27000000: None, 30000000: (40,36), 32000000: (32,27),
          33000000: (44,36), 36000000: (32,24), 39000000: (26,18),
          42000000: (28,18), 45000000: (30,18), 48000000: (32,18),
          54000000: (32,16), 60000000: (40,18), 66000000: (44,18)}

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def local(p):
    p=(ROOT/p).resolve(); p.relative_to(ROOT); return p
def read(p): return p.read_text(encoding='utf-8-sig')
def get_module(text,name):
    return re.search(r'(?m)^module '+re.escape(name)+r'\b[\s\S]*?^endmodule\b',text).group()

def verify_firmware(fixture,kind):
    stem='coremark' if kind=='coremark' else 'multimodal'
    data=(fixture/(stem+'.elf')).read_bytes()
    assert data[:6]==b'\x7fELF\x01\x01' and struct.unpack_from('<H',data,18)[0]==243
    shoff=struct.unpack_from('<I',data,32)[0]; entry,count=struct.unpack_from('<HH',data,46)
    images={'rom':bytearray(65536),'ram':bytearray(65536)}
    occupied={'rom':[],'ram':[]}
    for i in range(count):
        s=struct.unpack_from('<10I',data,shoff+i*entry)
        kind_id,flags,address,offset,size=s[1:6]
        if not flags&2 or not size:continue
        assert address+size<=65536
        space='rom' if flags&4 else 'ram'
        for start,end in occupied[space]:assert address>=end or address+size<=start
        occupied[space].append((address,address+size))
        if kind_id==1: images[space][address:address+size]=data[offset:offset+size]
        else: assert kind_id==8
    for space,image in images.items():
        name=('coremark_' if kind=='coremark' else 'advanced_')+space+'.hex'
        words=pack.read_hex(fixture/name)
        assert b''.join(words[i].to_bytes(4,'little') for i in range(16384))==image

def clock_top(text,hz):
    if hz == 27000000: return text
    f,o=RATIOS[hz]
    assert 27000000*f == hz*o and 600000000 <= 27000000*f <= 1200000000
    assert text.count('parameter integer CLK_HZ=27000000') == 1
    text=text.replace('parameter integer CLK_HZ=27000000',f'parameter integer CLK_HZ={hz}')
    assert text.count('always @(posedge sys_clk or negedge rst_n)') == 1
    text=text.replace('    reg [7:0] por=', '    reg[7:0]por=')
    # All system-domain state follows the actual PLL output. DDR reference stays separate.
    text=text.replace('always @(posedge sys_clk','always @(posedge cpu_clk')
    text=text.replace('or negedge rst_n)','or negedge run_n)')
    text=text.replace('if(!rst_n)por<=','if(!run_n)por<=')
    text=text.replace('.clk(sys_clk)', '.clk(cpu_clk)').replace('.cpu_clk(sys_clk)', '.cpu_clk(cpu_clk)')
    insert=(f'    wire cpu_clk,pll_locked;\n'
            f'    cpu_clock #(.FB_DIV({f}),.OUT_DIV({o})) u_cpu_clock(\n'
            f'      .ref_clk(sys_clk),.reset_n(rst_n),.clk(cpu_clk),.locked(pll_locked));\n'
            f'    wire run_n=rst_n && pll_locked;\n')
    assert text.count('    reg[7:0]por=') == 1
    return text.replace('    reg[7:0]por=',insert+'    reg[7:0]por=')

def prepare(a):
    rtl,fixture,out=local(a.rtl_dir),local(a.fixture),local(a.output)
    assert not out.exists(), 'Use a fresh candidate output directory'
    meta=json.loads(read(fixture/'build.json'))
    if a.kind=='coremark':
        assert meta['cpu_hz']==a.cpu_hz and meta['iterations']>=1000 and not meta['validation']
        assert meta['uart_gap_ms']==1200 and meta['uart_line_bytes_max']<=48
        assert sha(fixture/'coremark.elf')==meta['elf_sha256']
    else:
        assert not meta['simulation'] and meta['with_llm']
        assert sha(fixture/'multimodal.elf')==meta['elf_sha256']
        assert meta['cpu_hz']==a.cpu_hz and meta['uart_timeout_cycles']==60*a.cpu_hz
    verify_firmware(fixture,a.kind)
    out.mkdir(parents=True)
    shutil.copytree(rtl,out/'source/rtl')
    shutil.copytree(fixture,out/'source/firmware')
    frozen_fixture=out/'source/firmware'
    assert json.loads(read(frozen_fixture/'build.json'))==meta
    assert sha(frozen_fixture/('coremark.elf' if a.kind=='coremark' else 'multimodal.elf'))==meta['elf_sha256']
    verify_firmware(frozen_fixture,a.kind)
    for name,digest in meta['source_sha256'].items():
        source=local(Path(name)); assert sha(source)==digest,name
        destination=out/'source/firmware_sources'/Path(name)
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
    for name in ('scripts/build_maxperf_pds.py','scripts/package_advanced.py',
                 'scripts/run_coremark_perf.py','multimodal/scripts/build.py','multimodal/scripts/assemble_soc.py'):
        destination=out/'source/build_scripts'/name
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,destination)
    work=out/'work'; (work/'rtl').mkdir(parents=True)
    modules={}
    for p in sorted((out/'source/rtl').glob('*.v')):
        for n,t in pack.modules_from(read(p)).items():
            assert n not in modules,n
            modules[n]=t
    top='advanced_top' if a.kind=='coremark' else 'multimodal_top'
    if a.kind=='coremark':
        selected=set(); pending=[top]
        while pending:
            n=pending.pop()
            if n in selected: continue
            selected.add(n); pending.extend(pack.dependencies(modules[n],set(modules))-selected)
        modules={n:modules[n] for n in selected}
        (work/'constraints').mkdir()
        shutil.copyfile(ROOT/'constraints/mini_board_candidate.fdc',work/'constraints/board.fdc')
        constraints=['board.fdc']
    else:
        assert a.soc_file
        frozen=pack.modules_from(read(BASE/'rtl/multimodal_embedded.v'))
        selected_names=set(frozen)
        for n in selected_names & set(modules):
            frozen[n]=modules[n]
        soc=local(a.soc_file); shutil.copyfile(soc,out/'source/multimodal_soc.v')
        frozen['multimodal_soc']=get_module(read(soc),'multimodal_soc')
        modules=frozen
        shutil.copytree(BASE/'constraints',work/'constraints')
        shutil.copytree(BASE/'vendor',work/'vendor')
        constraints=['mini_board_candidate.fdc','matrix_pins.fdc','ddr_pg2l200h.fdc']
    for n,space in [('rom_sync','rom'),('ram_sync','ram')]:
        name=f'coremark_{space}.hex' if a.kind=='coremark' else f'advanced_{space}.hex'
        modules[n]=pack.embed_memory(modules[n],n,frozen_fixture/name)
        words={int(i):int(w,16) for i,w in re.findall(r"mem\[(\d+)\] = 32'h([0-9a-f]+);",modules[n])}
        assert words==pack.read_hex(frozen_fixture/name)
    modules[top]=clock_top(modules[top],a.cpu_hz)
    if a.cpu_hz!=27000000:
        shutil.copyfile(CLOCK,out/'source/cpu_clock.v')
        modules['cpu_clock']=get_module(read(CLOCK),'cpu_clock')
        f,o=RATIOS[a.cpu_hz]; g=math.gcd(f,o)
        constraint=(f'\n# Actual 27 MHz board input drives the Logos2 GPLL.\n'
                    f'create_generated_clock -name {{cpu_clk}} -source [get_ports {{sys_clk}}] '
                    f'[get_pins {{u_cpu_clock.u_pll/CLKOUT0}}] -master_clock [get_clocks {{sys_clk}}] '
                    f'-multiply_by {{{f//g}}} -divide_by {{{o//g}}}\n')
        p=work/'constraints'/constraints[0]
        p.write_text(read(p)+constraint,encoding='utf-8')
    for n in modules:
        modules[n]=re.sub(r'(\b(?:ROM_FILE|RAM_FILE|INIT_FILE)\s*=\s*)"[^"]*"',r'\1""',modules[n])
        assert '$readmemh' not in pack.without_comments(modules[n]),n
    content='`timescale 1ns/1ps\n'+ '\n\n'.join(modules[n] for n in [top]+sorted(set(modules)-{top}))+'\n'
    (work/'rtl/design.v').write_text(content,encoding='utf-8',newline='\n')
    tcl='set_arch -family Logos2 -device PG2L200H -speedgrade -6 -package FBB676\nadd_design {rtl/design.v}\n'
    if a.kind=='multimodal':tcl+='add_design {vendor/ddr3/ddr3_test.idf}\n'
    tcl+=''.join('add_constraint {constraints/'+c+'}\n' for c in constraints)
    pnr='pnr' if a.macro_placement is None else 'pnr -early_block_placement '+a.macro_placement+' -auto_dispose_macro false'
    tcl+=f'compile -top_module {top}\nsynthesize -ads -selected_syn_tool_opt 2\ndev_map\n{pnr}\nreport_timing\ngen_bit_stream -scbv {{High Voltage}}\n'
    (work/'run.tcl').write_text(tcl,encoding='ascii')
    inputs={p.relative_to(work).as_posix():sha(p) for p in work.rglob('*') if p.is_file()}
    source={p.relative_to(out/'source').as_posix():sha(p) for p in (out/'source').rglob('*') if p.is_file()}
    report={'status':'PREPARED','kind':a.kind,'top':top,'cpu_hz':a.cpu_hz,
            'board_ref_hz':27000000,'pll_ratio':RATIOS[a.cpu_hz],'pnr_command':pnr,
            'inputs':inputs,'source':source,'firmware_build':meta,
            'elf_hex_embedded_images_verified':True,'physical_board_validation':'NOT_RUN'}
    save(out/'build.json',report)
    print(json.dumps({'status':'PREPARED','output':str(out),'cpu_hz':a.cpu_hz,'modules':len(modules)}))

def run(a):
    out=local(a.output); work=out/'work'; m=json.loads(read(out/'build.json'))
    assert m['status']=='PREPARED','Do not overwrite an existing run'
    for n,h in m['inputs'].items():assert sha(work/n)==h,n
    native=NATIVE/work.relative_to(ROOT)
    command=[str(PDS),'-file','run.tcl','-work_dir',str(native),'-project_name','maxperf_'+m['kind']]
    m.update(status='RUNNING',command=command); save(out/'build.json',m)
    started=time.monotonic()
    with (out/'pds.log').open('w',encoding='utf-8') as log:
        result=subprocess.run(command,cwd=native,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy())
    log=read(out/'pds.log')
    errors=[s for s in log.splitlines() if re.match(r'^\s*(E:|ERROR\b|Check license failed)',s,re.I)]
    bit=work/'generate_bitstream'/f'{m["top"]}.sbit'
    for n,h in m['inputs'].items():assert sha(work/n)==h,n
    for n,h in m['source'].items():assert sha(out/'source'/n)==h,n
    m.update(status='GENERATED_NEEDS_AUDIT' if result.returncode==0 and not errors and bit.exists() else 'FAILED',
             exit_code=result.returncode,errors=errors,elapsed_seconds=round(time.monotonic()-started,2))
    if bit.exists():m.update(bitstream_sha256=sha(bit),bitstream_bytes=bit.stat().st_size)
    save(out/'build.json',m)
    print(json.dumps({k:v for k,v in m.items() if k not in ('inputs','source','firmware_build')},indent=2))
    return 0 if m['status']=='GENERATED_NEEDS_AUDIT' else 1

def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='action',required=True)
    q=sub.add_parser('prepare');q.add_argument('--kind',choices=('coremark','multimodal'),required=True)
    q.add_argument('--rtl-dir',type=Path,required=True);q.add_argument('--soc-file',type=Path)
    q.add_argument('--fixture',type=Path,required=True);q.add_argument('--cpu-hz',type=int,choices=RATIOS,required=True)
    q.add_argument('--macro-placement',choices=('Greedy','Global','Grouped','Close'),
                   help='Documented PDS macro-placement strategy; timing and IO constraints remain unchanged')
    q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('run');q.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); return prepare(a) if a.action=='prepare' else run(a)
if __name__=='__main__':raise SystemExit(main())
