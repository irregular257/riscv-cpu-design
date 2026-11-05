#!/usr/bin/env python3
"""Compare actual advanced_soc RTL with unmodified CoreMark v1.0 algorithms.

A short simulation is a cycle/CRC regression, NOT a reportable CoreMark score.
--firmware reuses the exact compiled ELF/HEX fixture for fair RTL comparisons.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time

REAL_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.getenv('COREMARK_ASCII_ROOT', 'D:/codex_rv32_advanced_20261028'))

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def rel(p): return p.relative_to(ROOT).as_posix()
def local(p):
    p = p if p.is_absolute() else REAL_ROOT / p
    return ROOT / p.resolve().relative_to(REAL_ROOT)
def run(cmd, out, env):
    r = subprocess.run([str(x) for x in cmd], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    out.write_text(r.stdout+r.stderr, encoding='utf-8')
    if r.returncode: print((r.stdout+r.stderr)[-5000:], flush=True)
    r.check_returncode()
    return r.stdout

def build(fixture, iterations, validation, env, uart_gap_ms=0,
          march='rv32i', opt='O2', lto=False, cpu_hz=27000000, extra_cflags=()):
    fixture.mkdir(parents=True, exist_ok=True)
    port = ROOT / 'software/coremark_perf'
    gcc = ROOT / 'tools/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-gcc.exe'
    sources = [port/'start.S'] + [port/'official'/(s+'.c') for s in ('core_list_join','core_main','core_matrix','core_state','core_util')] + [port/'core_portme.c',port/'ee_printf.c']
    headers = [port/'core_portme.h',port/'official/coremark.h',port/'link.ld']
    hashes = {rel(p): sha(p) for p in sources+headers}
    for source in sources+headers:
        copy=fixture/'source'/source.relative_to(port)
        copy.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,copy)
    elf = fixture/'coremark.elf'
    compiler_flags = ['-'+opt,'-march='+march,'-mabi=ilp32','-msmall-data-limit=0',
                      '-ffreestanding','-fno-builtin','-fno-common',
                      '-ffunction-sections','-fdata-sections'] + (['-flto'] if lto else []) + list(extra_cflags)
    cmd = [gcc]+compiler_flags+[
           '-DCOMPILER_FLAGS='+json.dumps(' '.join(compiler_flags)),
           '-I'+rel(port),'-I'+rel(port/'official'),'-DITERATIONS='+str(iterations),'-DCPU_HZ='+str(cpu_hz),
           '-DCOREMARK_UART_GAP_MS='+str(uart_gap_ms),
           '-D'+('VALIDATION_RUN' if validation else 'PERFORMANCE_RUN')+'=1','-nostdlib','-Wl,--gc-sections',
           '-Wl,-Map='+rel(fixture/'coremark.map'),'-T',rel(port/'link.ld')]+[rel(p) for p in sources]
    libgcc=subprocess.check_output([str(gcc),'-march='+march,'-mabi=ilp32','-print-libgcc-file-name'],cwd=ROOT,text=True,encoding='utf-8').strip()
    cmd += [rel(local(Path(libgcc))),'-o',rel(elf)]
    run(cmd,fixture/'compile.log',env)
    b=elf.read_bytes();shoff=struct.unpack_from('<I',b,32)[0];ent,num,strings=struct.unpack_from('<HHH',b,46)
    sections=[struct.unpack_from('<10I',b,shoff+i*ent) for i in range(num)]
    ss=sections[strings];names=b[ss[4]:ss[4]+ss[5]]
    rom=bytearray(65536);ram=bytearray(65536);sizes={}
    for s in sections:
        name=names[s[0]:].split(b'\0',1)[0].decode();kind,flags,addr,off,size=s[1:6]
        if not flags&2 or not size:continue
        if addr+size>65536:raise ValueError('Section overflow: '+name)
        sizes[name]={'address':addr,'bytes':size}
        if kind==1:(rom if flags&4 else ram)[addr:addr+size]=b[off:off+size]
    for name,image in [('rom',rom),('ram',ram)]:
        (fixture/('coremark_'+name+'.hex')).write_text(''.join(f'{int.from_bytes(image[i:i+4],"little"):08x}\n' for i in range(0,65536,4)),encoding='ascii')
    objdump=gcc.with_name('riscv-none-elf-objdump.exe')
    run([objdump,'-d',rel(elf)],fixture/'coremark.dis',env)
    manifest={'iterations':iterations,'validation':validation,'cpu_hz':cpu_hz,'data_size_bytes':2000,
              'march':march,'optimization':opt,'lto':lto,'extra_cflags':list(extra_cflags),'compiler_flags':' '.join(compiler_flags),
              'uart_line_bytes_max':48,'uart_gap_ms':uart_gap_ms,
              'compiler':subprocess.check_output([str(gcc),'--version'],text=True,encoding='utf-8').splitlines()[0],
              'command':[str(c) for c in cmd],'source_sha256':hashes,'sections':sizes,'elf_sha256':sha(elf),
              'official_source_commit':'1f483d5b8316753a742cbf5590caf5bd0a4e4777',
              'note':'Official algorithms unchanged; port adds test-result MMIO markers outside timed interval. Short run intentionally prints >=10s validity error.'}
    (fixture/'build.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rtl-dir',type=Path,default=Path('rtl'))
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--firmware',type=Path,help='reuse existing exact fixture')
    p.add_argument('--iterations',type=int,default=1)
    p.add_argument('--validation',action='store_true')
    p.add_argument('--build-only',action='store_true',help='build a longer physical-board firmware without simulating it')
    p.add_argument('--uart-gap-ms',type=int,default=0,help='quiet interval after each <=48-byte output line; use 1200 for the remote web console, 0 for fast RTL simulation')
    p.add_argument('--march',choices=('rv32i','rv32i_zmmul','rv32im'),default='rv32i',help='ISA supported by the selected RTL; compiler setting does not add hardware support')
    p.add_argument('--opt',choices=('O2','O3','Os'),default='O2')
    p.add_argument('--lto',action='store_true',help='enable link-time optimization without modifying official algorithms')
    p.add_argument('--cpu-hz',type=int,default=27000000,help='actual hardware clock for firmware timing; changing this does not create a physical clock')
    p.add_argument('--extra-cflag',action='append',default=[],help='additional optimization option, e.g. --extra-cflag=-funroll-loops; included in printed flags')
    p.add_argument('--max-cycles',type=int,default=30000000)
    p.add_argument('--host-gxx',type=Path,default=Path('C:/Users/yh21t/.codex/tmp/rv32-host-gcc-20261028/bin/g++.exe'))
    a=p.parse_args()
    if a.iterations<1:p.error('iterations must be positive')
    if not 1000000<=a.cpu_hz<=1000000000:p.error('cpu-hz must be between 1 MHz and 1 GHz')
    if not 0<=a.uart_gap_ms<=60000:p.error('uart-gap-ms must be between 0 and 60000')
    if a.firmware and a.uart_gap_ms:p.error('--uart-gap-ms only applies when building firmware, not with --firmware')
    rtl=local(a.rtl_dir);out=local(a.output);out.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();suite=ROOT/'tools/oss-cad-suite';include=suite/'share/verilator/include'
    env['VERILATOR_ROOT']=str(suite/'share/verilator')
    env['PATH']=os.pathsep.join([str(a.host_gxx.parent),str(suite/'lib'),env.get('PATH','')])
    fixture=local(a.firmware) if a.firmware else out/'fixture'
    if not a.firmware:build(fixture,a.iterations,a.validation,env,a.uart_gap_ms,a.march,a.opt,a.lto,a.cpu_hz,a.extra_cflag)
    if a.build_only:
        print('Firmware built: '+str(fixture),flush=True)
        return
    meta=json.loads((fixture/'build.json').read_text(encoding='utf-8'))
    sources=sorted(rtl.glob('*.v'))+[ROOT/'sim/tb_coremark_perf.v']
    runner=ROOT/'sim/coremark_perf_runner.cpp'
    evidence={'scope':'Short RTL architecture regression, not a valid >=10s physical CoreMark score or Fmax measurement',
              'rtl_dir':rel(rtl),'firmware':rel(fixture),'build':meta,
              'sources':{rel(x):sha(x) for x in sources+[runner]},
              'fixtures':{x.name:sha(x) for x in fixture.iterdir() if x.is_file()}}
    key=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()[:12]
    obj=out/('obj_'+key);exe=obj/'coremark_perf.exe';started=time.monotonic()
    if not exe.exists():
        cmd=[suite/'bin/verilator_bin.exe','--cc','--top-module','tb_coremark_perf','--Mdir',rel(obj),'-Wno-fatal',
             '-GROM_FILE="'+rel(fixture/'coremark_rom.hex')+'"','-GRAM_FILE="'+rel(fixture/'coremark_ram.hex')+'"',
             '-GCPU_HZ='+str(meta['cpu_hz'])]+[rel(s) for s in sources]
        run(cmd,out/'verilator.log',env)
        generated=[s for s in sorted(obj.glob('*.cpp')) if not s.name.endswith('__ALL.cpp')]
        cmd=[a.host_gxx,'-std=c++20','-O2','-pthread','-I'+str(obj),'-I'+str(include),'-I'+str(include/'vltstd')]+generated+[include/'verilated.cpp',include/'verilated_threads.cpp',runner,'-o',exe]
        run(cmd,out/'cpp_compile.log',env)
    print('Running actual advanced_soc CoreMark RTL regression: '+rel(out),flush=True)
    console=run([exe,out,str(a.max_cycles)],out/'run.log',env)
    if evidence['sources']!={rel(x):sha(x) for x in sources+[runner]}:
        raise RuntimeError('RTL or runner changed while building/running; rerun with a stable source snapshot')
    if evidence['fixtures']!={x.name:sha(x) for x in fixture.iterdir() if x.is_file()}:
        raise RuntimeError('Firmware fixture changed during the simulation')
    print(console,flush=True)
    metrics=json.loads((out/'metrics.json').read_text())
    uart=(out/'uart.log').read_text()
    expected={'seedcrc':'18f2','crclist':'e3c1','crcmatrix':'0747','crcstate':'8d84'} if meta['validation'] else {'seedcrc':'e9f5','crclist':'e714','crcmatrix':'1fd7','crcstate':'8e3a'}
    crc={}
    for name,want in expected.items():
        m=re.search(re.escape(name)+r'\s*:\s*0x([0-9a-fA-F]+)',uart)
        if not m or m[1].lower()!=want:raise RuntimeError(f'{name} mismatch expected {want}')
        crc[name]=m[1]
    final=re.search(r'crcfinal\s*:\s*0x([0-9a-fA-F]+)',uart)
    crc['crcfinal']=final[1] if final else None
    errors=[line for line in uart.splitlines() if 'ERROR!' in line and 'Must execute for at least 10 secs' not in line]
    if errors:raise RuntimeError('CoreMark functional errors: '+str(errors))
    ticks=int(re.search(r'Total ticks\s*:\s*(\d+)',uart)[1])
    metrics.update({'timed_cycles':ticks,'iterations':meta['iterations'],
                    'cycles_per_iteration':ticks/meta['iterations'],
                    'estimated_iterations_per_mhz':meta['iterations']*1e6/ticks,
                    'window_cpi':metrics['window_cycles']/metrics['retired']})
    evidence.update({'result':'PASS','metrics':metrics,'crc':crc,'wall_seconds':round(time.monotonic()-started,3),
                     'counters_note':'Window marker interval includes timing-call overhead. IF/MEM waits may overlap; do not sum stall counters. Reported timed_cycles comes from unmodified CoreMark timed interval.',
                     'reported_score_valid':False})
    (out/'summary.json').write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(metrics,indent=2),flush=True)
if __name__=='__main__':main()
