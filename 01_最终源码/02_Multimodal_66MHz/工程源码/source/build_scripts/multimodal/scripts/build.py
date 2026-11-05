"""Build isolated dual-vision/matrix firmware, optionally including board LLM."""
import argparse,hashlib,json,os,struct,subprocess,sys
from pathlib import Path
M=Path(__file__).resolve().parents[1]; A=M.parent
N=Path('D:/codex_rv32_advanced_20261028')
if not N.exists():N=A
def rel(p):return Path(p).resolve().relative_to(A).as_posix()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    sys.stdout.reconfigure(encoding='utf-8')
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sim',action='store_true');ap.add_argument('--llm',action='store_true')
    ap.add_argument('--output-dir',type=Path,help='isolated output directory, relative to advanced/')
    ap.add_argument('--march',default='rv32i_zicsr_zifencei',help='ISA supported by the selected CPU RTL')
    ap.add_argument('--opt-vision',choices=('O2','O3','Os'),default='O2')
    ap.add_argument('--opt-runtime',choices=('O2','O3','Os'),default='Os')
    ap.add_argument('--cpu-hz',type=int,default=27000000,help='board CPU frequency for the 60-second UART timeout')
    args=ap.parse_args()
    if not 1<=args.cpu_hz<=0xffffffff//60:ap.error('--cpu-hz must keep 60*cpu-hz within uint32')
    uart_timeout=2000000 if args.sim else 60*args.cpu_hz
    out=args.output_dir if args.output_dir else M/'reports'/('sim_images' if args.sim else 'images')
    if not out.is_absolute():out=A/out
    out=out.resolve()
    try:out.relative_to(A.resolve())
    except ValueError:ap.error('--output-dir must be inside advanced/')
    out.mkdir(parents=True,exist_ok=True)
    cc=str(N/'tools/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-gcc.exe')
    objdump=cc.replace('gcc.exe','objdump.exe')
    common=['-march='+args.march,'-mabi=ilp32','-msmall-data-limit=0','-mno-relax','-ffreestanding','-fno-builtin','-fno-common','-ffunction-sections','-fdata-sections','-fstack-usage','-Wall','-Wextra','-Werror=implicit-function-declaration']
    main_source=(A/'software/runtime/main.c').read_text(encoding='utf-8').replace('#include "../vision/vision_firmware.h"','#include "multimodal.h"').replace('vision_loop();','multimodal_loop();')
    (out/'main.c').write_text(main_source,encoding='utf-8')
    sources=[(A/'software/runtime/start.S','start',[]),(A/'software/runtime/trap.S','trap',[]),(out/'main.c','main',['-DWITH_VISION']),(M/'software/multimodal.c','multimodal',['-DUART_TIMEOUT='+str(uart_timeout)+'u']+(['-DWITH_LLM'] if args.llm else []))]
    for mode in ('digit','hanzi'):
        sources.append((A/f'reports/trained_digit_hanzi_20261101/{mode}/source/vision_firmware.c',mode,[f'-Dvision_infer={mode}_vision_infer',f'-Dvision_last_head={mode}_vision_last_head',f'-Dvision_loop={mode}_unused_loop']))
    if args.llm:
        sources.append((M/'software/minilib.c','minilib',[]))
        llm_sources=sorted((M/'software/llm').glob('*.c'))
        if not llm_sources:raise RuntimeError('LLM runtime has not been written')
        sources += [(p,'llm_'+p.stem,[]) for p in llm_sources if not p.stem.startswith('test_')]
    headers=set((M/'software').rglob('*.h'))|set((A/'software/runtime').rglob('*.h'))
    for mode in ('digit','hanzi'):headers.update((A/f'reports/trained_digit_hanzi_20261101/{mode}/source').rglob('*.h'))
    source_inputs=sorted({p for p,_,_ in sources}|headers|{M/'software/link.ld'})
    source_before={rel(p):sha(p) for p in source_inputs}
    protected=sorted((A/'rtl').glob('*.v'))+sorted((A/'software/runtime').glob('*.*'))
    for mode in ('digit','hanzi'):protected+=sorted((A/f'reports/trained_digit_hanzi_20261101/{mode}/source').glob('*.*'))
    before={rel(p):sha(p) for p in protected}
    commands=[];logs=[];objects=[]
    def run(cmd):
        commands.append(cmd);r=subprocess.run(cmd,cwd=N,text=True,encoding='utf-8',errors='replace',capture_output=True);logs.append(r.stdout+r.stderr)
        (out/'compile.log').write_text('\n'.join(logs),encoding='utf-8')
        if r.returncode:print(r.stdout+r.stderr);r.check_returncode()
    for source,name,flags in sources:
        obj=out/(name+'.o');objects.append(rel(obj))
        run([cc,*common,'-'+(args.opt_vision if name in ('digit','hanzi') else args.opt_runtime),'-I',rel(M/'software'),*flags,'-c',rel(source),'-o',rel(obj)])
    def lib(name):
        p=Path(subprocess.check_output([cc,'-march='+args.march,'-mabi=ilp32','-print-file-name='+name],cwd=N,text=True,encoding='utf-8').strip())
        try:return rel(p)
        except ValueError:return str(p)
    elf=out/'multimodal.elf'
    libraries=([lib('libm.a')] if args.llm else [])+[lib('libgcc.a')]
    # Harvard ROM/RAM intentionally share virtual addresses. Validate overlap
    # separately per physical image below instead of the ELF linker's unified map.
    run([cc,*common,'-nostdlib','-Wl,--no-relax','-Wl,--gc-sections','-Wl,--no-check-sections','-Wl,-Map='+rel(out/'multimodal.map'),'-T',rel(M/'software/link.ld'),*objects,'-Wl,--start-group',*libraries,'-Wl,--end-group','-o',rel(elf)])
    b=elf.read_bytes();shoff=struct.unpack_from('<I',b,32)[0];ent,num,strings=struct.unpack_from('<HHH',b,46)
    sections=[struct.unpack_from('<10I',b,shoff+i*ent) for i in range(num)];ss=sections[strings];names=b[ss[4]:ss[4]+ss[5]]
    rom=bytearray(65536);ram=bytearray(65536);sizes={};occupied={'rom':[],'ram':[]}
    for s in sections:
        name=names[s[0]:].split(b'\0',1)[0].decode();kind,flags,addr,off,size=s[1:6]
        if not flags&2 or not size:continue
        if addr+size>65536:raise ValueError('Section outside 64KiB: '+name)
        space='rom' if flags&4 else 'ram'
        for first,last,other in occupied[space]:
            if addr<last and first<addr+size:raise ValueError(f'{space} overlap: {name}/{other}')
        occupied[space].append((addr,addr+size,name))
        sizes[name]={'address':addr,'bytes':size,'zero_fill':kind==8}
        if kind==1:(rom if flags&4 else ram)[addr:addr+size]=b[off:off+size]
    for name,data in [('rom',rom),('ram',ram)]:
        (out/f'advanced_{name}.hex').write_text(''.join(f'{int.from_bytes(data[i:i+4],"little"):08x}\n' for i in range(0,65536,4)),encoding='ascii')
    run([objdump,'-d',rel(elf)])
    (out/'multimodal.dis').write_text(logs.pop(),encoding='utf-8')
    (out/'compile.log').write_text('\n'.join(logs),encoding='utf-8')
    after={rel(p):sha(p) for p in protected}
    if before!=after:raise RuntimeError('Protected baseline changed during build')
    source_after={rel(p):sha(p) for p in source_inputs}
    if source_before!=source_after:raise RuntimeError('Firmware source/header/linker inputs changed during build')
    report={'result':'PASS_LINK_ONLY','with_llm':args.llm,'simulation':args.sim,'march':args.march,'opt_vision':args.opt_vision,'opt_runtime':args.opt_runtime,'cpu_hz':args.cpu_hz,'sections':sizes,'elf_sha256':sha(elf),'commands':commands,'source_sha256':source_before,'source_headers_and_linker_hashed':True,'protected_baseline_sha256':before,'protected_baseline_unchanged':True,'rom_capacity':65536,'ram_capacity':65536,'stack_reserved_bytes':4096,'uart_timeout_cycles':uart_timeout,'physical_board_verified':False}
    (out/'build.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(sizes));print('LLM='+str(args.llm))
if __name__=='__main__':main()
