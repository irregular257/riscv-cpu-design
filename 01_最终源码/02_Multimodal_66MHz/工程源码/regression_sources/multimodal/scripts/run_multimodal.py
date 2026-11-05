#!/usr/bin/env python3
"""Run actual CPU/INT8/UART dual-model/matrix regression with DDR unavailable.

Build the isolated simulation firmware first: python multimodal/scripts/build.py --sim
This script never programs a board or supplies off-board results to the CPU.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import zlib

ROOT=Path(__file__).resolve().parents[2]
NATIVE=Path('D:/codex_rv32_advanced_20261028')
if not NATIVE.is_dir():NATIVE=ROOT
M=ROOT/'multimodal'
OUT=M/'reports/integration'
TRAINED=ROOT/'reports/trained_digit_hanzi_20261101'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rel(path):return Path(path).resolve().relative_to(ROOT).as_posix()
def native(path):return str(NATIVE/rel(path))
def save_json(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def crc16(b):
    c=0xffff
    for x in b:
        c^=x<<8
        for _ in range(8):c=((c<<1)^0x1021)&65535 if c&0x8000 else (c<<1)&65535
    return c


class Detection(ctypes.Structure):
    _fields_=[(x,ctypes.c_uint8) for x in ('x','y','w','h','label','confidence')]


def load_reference(mode):
    folder=TRAINED/mode
    report=json.loads((folder/'c_reference/summary.json').read_text(encoding='utf-8'))
    library=folder/f'c_reference/vision_reference_{mode}.dll'
    if report['library_sha256']!=sha(library) or report['model_header_sha256']!=sha(folder/'source/vision_model.h'):
        raise RuntimeError('Independent C reference manifest mismatch: '+mode)
    dll=ctypes.CDLL(native(library))
    dll.vision_infer.argtypes=[ctypes.POINTER(ctypes.c_uint8),ctypes.POINTER(Detection)]
    dll.vision_infer.restype=ctypes.c_uint
    dll.vision_last_head.argtypes=[];dll.vision_last_head.restype=ctypes.POINTER(ctypes.c_int16)
    def infer(pixels):
        a=(ctypes.c_uint8*1024)(*pixels);boxes=(Detection*4)();n=dll.vision_infer(a,boxes)
        if n>4:raise ValueError('Reference detection count')
        h=dll.vision_last_head()
        return [h[ch*16+cell] for cell in range(16) for ch in range(15)],[[getattr(boxes[i],k) for k,_ in Detection._fields_] for i in range(n)]
    return dll,infer,{'library_sha256':sha(library),'model_header_sha256':sha(folder/'source/vision_model.h'),'c_sha256':sha(folder/'source/vision_firmware.c')}


def prepare_plan(with_llm):
    fixture=OUT/'fixture';fixture.mkdir(parents=True,exist_ok=True)
    references={};infer={};libraries=[]
    for mode in ('digit','hanzi'):
        dll,fn,provenance=load_reference(mode);libraries.append(dll);infer[mode]=fn;references[mode]=provenance
    state={'mode':0,'threshold':160,'required':1,'shown':15,'brightness':192,'frames':0,'accepted':0,'unknown':0,'crc_errors':0,'candidate':15,'stable':0,'history':[],
           'ddr_status':0,'ddr_tested':0,'model_size':0,'model_offset':0,'model_ready':0,'upload_active':0}
    corrupt_reads=False
    rows=[];cases=[];control_seq=1000
    def add(name,kind,packet,glyph,status,dots=0,head=None,boxes=None,**extra):
        dest=fixture/name;dest.mkdir(exist_ok=True);(dest/'request.bin').write_bytes(packet)
        if head is not None:(dest/'expected_head.bin').write_bytes(struct.pack('<240h',*head));(dest/'expected_boxes.bin').write_bytes(bytes(x for b in boxes for x in b))
        fields=[name,kind,f'{name}/request.bin',f'{name}/expected_head.bin' if head is not None else '-',f'{name}/expected_boxes.bin' if head is not None else '-',str(glyph),str(status),str(dots),str(state['ddr_status']),str(int(corrupt_reads))]
        rows.append('\t'.join(fields))
        cases.append({'name':name,'kind':kind,'request_sha256':sha(dest/'request.bin'),'glyph':glyph,'status':status,'expected_brightness':state['brightness'],'behavioral_ddr_enabled':bool(state['ddr_status']),**extra})
    def control(name,cmd,payload=b'',status=0,bad_crc=False,expected_payload=None):
        nonlocal control_seq
        control_seq+=1;body=bytes([1,cmd])+struct.pack('<HH',control_seq,len(payload))+payload
        packet=b'\xa5\x43'+body+struct.pack('<H',crc16(body)^(1 if bad_crc else 0));status_check=None
        if bad_crc:state['crc_errors']+=1
        elif status==0:
            if cmd==2:state['mode']=payload[0];state['shown']=15;state['candidate']=15;state['stable']=0
            elif cmd==3:state['threshold'],state['required'],state['brightness']=payload;state['shown']=15;state['candidate']=15;state['stable']=0
            elif cmd==4:expected_payload=bytes([len(state['history'])])+bytes(x for row in state['history'] for x in row)
            elif cmd==5:
                for key in ('frames','accepted','unknown','crc_errors'):state[key]=0
                state['history']=[]
            elif cmd==1:
                status_check={k:state[k] for k in ('mode','threshold','required','shown','frames','accepted','unknown','crc_errors','ddr_status','ddr_tested','model_size','model_offset','model_ready','upload_active')}
                status_check['with_llm']=int(with_llm)
            elif cmd==0x10:state['ddr_tested']=1
            elif cmd==0x20:state.update(model_size=struct.unpack_from('<I',payload)[0],model_offset=0,model_ready=0,upload_active=1)
        add(name,'C',packet,state['shown'],status,command=cmd,expected_payload_hex=None if expected_payload is None else expected_payload.hex(),status_fields=status_check)
    def vision(name,packet,mode=None,bad_crc=False,provenance=None):
        chosen=mode or ('hanzi' if state['mode'] else 'digit');head,boxes=infer[chosen](packet[10:-2])
        if bad_crc:
            packet=packet[:-1]+bytes([packet[-1]^1]);state['crc_errors']+=1
            add(name,'V',packet,state['shown'],2,reference=chosen);return
        candidate=14;label=255;score=0
        if boxes:
            label,score=boxes[0][4:6]
            if score>=state['threshold']:
                if not state['mode'] and label<10:candidate=label
                elif state['mode'] and 2<=label<=5:candidate=label+8
        state['frames']+=1
        if candidate<14:
            state['stable']=min(255,state['stable']+1) if candidate==state['candidate'] else 1
            state['candidate']=candidate
            if state['stable']>=state['required']:state['shown']=candidate;state['accepted']+=1
            else:state['shown']=16
        else:state['unknown']+=1;state['stable']=0;state['candidate']=15;state['shown']=14
        sequence=struct.unpack_from('<H',packet,6)[0]
        state['history']=(state['history']+[[sequence&255,sequence>>8,state['mode'],label,score,state['shown'],len(boxes),state['stable']]])[-16:]
        add(name,'V',packet,state['shown'],0,4080,head,boxes,reference=chosen,expected_boxes=boxes,provenance=provenance)
    digit=json.loads((TRAINED/'digit/cases.json').read_text(encoding='utf-8'))['cases']
    hanzi=json.loads((TRAINED/'hanzi/cases.json').read_text(encoding='utf-8'))['cases']
    control('initial_status',1)
    for c in digit:vision('digit_'+c['name'],(TRAINED/'digit'/c['request']).read_bytes(),provenance=c['source'])
    digit_raw=(TRAINED/'digit'/digit[1]['request']).read_bytes()
    vision('bad_vision_crc_preserves_display',digit_raw,bad_crc=True)
    control('bad_mode_crc_preserves_display',2,b'\x01',2,True)
    control('status_after_crc',1)
    control('set_high_threshold',3,bytes([255,1,64]))
    vision('threshold_rejects_digit8',digit_raw)
    control('set_two_frame_stability',3,bytes([160,2,192]))
    vision('stable_first_is_busy',digit_raw);vision('stable_second_is_digit8',digit_raw)
    control('history_before_mode_switch',4);control('status_before_mode_switch',1)
    control('switch_to_hanzi',2,b'\x01')
    for c in hanzi[:2]:vision('hanzi_'+c['name'],(TRAINED/'hanzi'/c['request']).read_bytes(),provenance=c.get('truth_label'))
    control('set_one_frame_stability',3,bytes([160,1,192]))
    imported=json.loads((TRAINED/'hanzi/import_summary.json').read_text(encoding='utf-8'))
    vectors_path=Path(imported['training_report_directory'])/'test_vectors.json'
    vectors=json.loads(vectors_path.read_text(encoding='utf-8'))
    references['hanzi']['validation_vectors_sha256']=sha(vectors_path)
    directions=[]
    for label,name in enumerate(('up','down','left','right'),2):
        v=next(v for v in vectors if v['truth_class']==label and v['detections'] and v['detections'][0][4]==label)
        head,boxes=infer['hanzi'](v['pixels'])
        if head!=v['head_q4'] or boxes!=v['detections']:raise ValueError('Supplied Hanzi vector differs from independent C reference')
        body=bytes([1,32,32,1])+struct.pack('<HH',200+label,1024)+bytes(v['pixels']);packet=b'\xa5\x59'+body+struct.pack('<H',crc16(body))
        vision('hanzi_'+name,packet,provenance={k:v[k] for k in ('source_id','sample_id','writer','source_partition','truth_class','truth_label')});directions.append(packet)
    vision('hanzi_blank',(TRAINED/'hanzi'/hanzi[2]['request']).read_bytes())
    # Exercise the circular-history boundary with real repeated directional inference.
    for i in range(5):vision(f'history_wrap_frame_{i}',directions[i%4])
    control('history_after_wrap',4);control('status_after_wrap',1)
    control('ddr_probe_not_ready',0x10,status=4)
    control('model_begin_not_ready',0x20,struct.pack('<II',1024,0),status=4)
    control('status_after_ddr_rejection',1)
    control('invalid_mode_preserves_state',2,b'\x02',status=1)
    control('unsupported_command',0x7f,status=7)
    control('clear_statistics',5);control('history_after_clear',4);control('status_after_clear',1)
    if with_llm:
        # CPU-side behavioral DDR only: the model maps the probe's sparse
        # addresses plus 8 KiB of low memory, not a vendor DDR3 implementation.
        state['ddr_status']=1
        control('behavioral_ddr_probe',0x10)
        control('status_after_behavioral_probe',1)
        image=bytes(range(256));good_crc=zlib.crc32(image)&0xffffffff
        control('loader_begin_bad_file_crc',0x20,struct.pack('<II',256,good_crc^1))
        control('loader_out_of_order_rejected',0x21,struct.pack('<I',64)+image[64:128],5,expected_payload=struct.pack('<I',0))
        state['model_offset']=64
        control('loader_first_chunk',0x21,struct.pack('<I',0)+image[:64],expected_payload=struct.pack('<I',64))
        control('loader_duplicate_chunk',0x21,struct.pack('<I',0)+image[:64],expected_payload=struct.pack('<I',64))
        control('loader_conflicting_duplicate',0x21,struct.pack('<I',0)+bytes([image[0]^1])+image[1:64],5,expected_payload=struct.pack('<I',64))
        state['model_offset']=256
        control('loader_remaining_chunk',0x21,struct.pack('<I',64)+image[64:],expected_payload=struct.pack('<I',256))
        control('loader_bad_file_crc_rejected',0x22,status=5)
        control('loader_begin_good_crc',0x20,struct.pack('<II',256,good_crc))
        state['model_offset']=256
        control('loader_complete_image',0x21,struct.pack('<I',0)+image,expected_payload=struct.pack('<I',256))
        corrupt_reads=True
        control('loader_ddr_readback_crc_rejected',0x22,status=5)
        corrupt_reads=False
        control('loader_invalid_llm_image_rejected',0x22,status=6,expected_payload=struct.pack('<i',-1))
        control('status_after_invalid_image',1)
        control('chat_without_ready_model_rejected',0x30,b'hello',status=4)
    (fixture/'plan.tsv').write_text('\n'.join(rows)+'\n',encoding='ascii')
    save_json(fixture/'expected_cases.json',cases)
    return cases,references


def run(command,log,env,timeout=600):
    print('RUN '+log.name,flush=True);started=time.monotonic()
    p=subprocess.run([str(c) for c in command],cwd=NATIVE,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
    log.write_text(p.stdout+p.stderr,encoding='utf-8')
    if p.returncode:raise RuntimeError(f'{log}: exit {p.returncode}\n'+(p.stdout+p.stderr)[-3500:])
    return {'seconds':round(time.monotonic()-started,3),'command':[str(c) for c in command]}


def validate_control(cases):
    checked=[]
    for c in cases:
        b=(OUT/c['name']/'response.bin').read_bytes();metrics=json.loads((OUT/c['name']/'metrics.json').read_text())
        if metrics['brightness']!=c['expected_brightness']:raise AssertionError('Brightness mismatch: '+c['name'])
        if c['kind']=='C':
            p=b[9:-2]
            if c['expected_payload_hex'] is not None and p.hex()!=c['expected_payload_hex']:raise AssertionError('History payload mismatch: '+c['name'])
            if c['status_fields']:
                if len(p)!=40:raise AssertionError('Status size')
                f=c['status_fields'];actual={'mode':p[0],'threshold':p[1],'required':p[2],'shown':p[3],
                    **dict(zip(('ddr_status','frames','accepted','unknown','crc_errors','last_cycles','model_size','model_offset'),struct.unpack_from('<8I',p,4))),
                    'model_ready':p[36],'ddr_tested':p[37],'upload_active':p[38],'with_llm':p[39]}
                if any(actual[k]!=v for k,v in f.items()):raise AssertionError('Status counter/selection mismatch: '+c['name']+' '+str(actual)+' expected '+str(f))
                c['decoded_status']=actual
        checked.append({**c,'metrics':metrics})
    return checked


def main():
    global OUT
    sys.stdout.reconfigure(encoding='utf-8')
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--images-dir',type=Path,default=Path('multimodal/reports/sim_images'))
    ap.add_argument('--rtl-dir',type=Path,default=Path('rtl'))
    ap.add_argument('--soc-file',type=Path,default=Path('multimodal/rtl/multimodal_soc.v'))
    ap.add_argument('--output-dir',type=Path,default=Path('multimodal/reports/integration'))
    ap.add_argument('--host-gxx',type=Path,default=Path('C:/Users/yh21t/.codex/tmp/rv32-host-gcc-20261028/bin/g++.exe'))
    args=ap.parse_args()
    OUT=(ROOT/args.output_dir).resolve()
    rtl_dir=(ROOT/args.rtl_dir).resolve();soc_file=(ROOT/args.soc_file).resolve()
    for path in (OUT,rtl_dir,soc_file):path.relative_to(ROOT)
    if 'reports' not in OUT.relative_to(ROOT).parts:ap.error('Output must be inside a reports directory')
    OUT.mkdir(parents=True,exist_ok=True)
    images=(ROOT/args.images_dir).resolve();build=json.loads((images/'build.json').read_text(encoding='utf-8'))
    if not build.get('simulation'):raise RuntimeError('Build --sim firmware first')
    cases,references=prepare_plan(build['with_llm'])
    firmware=OUT/'firmware';firmware.mkdir(exist_ok=True)
    for name in ('advanced_rom.hex','advanced_ram.hex','multimodal.elf','build.json'):shutil.copyfile(images/name,firmware/name)
    sources=sorted(rtl_dir.glob('*.v'))+[soc_file,M/'rtl/matrix_display.v',M/'sim/vision_soc.v']
    runner=M/'sim/multimodal_runner.cpp'
    manifest={'result':'RUNNING','scope':'Actual RV32I/cache/INT8/UART RTL: vision with DDR unavailable, then optional sparse behavioral DDR loader only; no physical board, DDR PHY or LLM inference claim',
        'sources':{rel(p):sha(p) for p in sources+[runner,Path(__file__)]},'firmware':{p.name:sha(p) for p in firmware.iterdir()},
        'references':references,'simulation_clock_hz':1000000,'uart_baud':10000,'uart_divisor':100,'physical_ddr_available':False,'behavioral_loader_tests':build['with_llm']}
    save_json(OUT/'summary.json',manifest)
    key=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:12]
    obj=OUT/('obj_'+key);exe=obj/'multimodal_runner.exe'
    env=os.environ.copy();env['VERILATOR_ROOT']=str(NATIVE/'tools/oss-cad-suite/share/verilator')
    env['PATH']=os.pathsep.join((str(args.host_gxx.parent),str(NATIVE/'tools/oss-cad-suite/lib'),env.get('PATH','')))
    commands=[]
    if not exe.is_file():
        commands.append(run([NATIVE/'tools/oss-cad-suite/bin/verilator_bin.exe','--cc','--top-module','vision_soc','--Mdir',rel(obj),'-Wno-fatal',
            '-GROM_FILE="'+rel(firmware/'advanced_rom.hex')+'"','-GRAM_FILE="'+rel(firmware/'advanced_ram.hex')+'"',*[rel(p) for p in sources]],OUT/'verilator.log',env))
        inc=NATIVE/'tools/oss-cad-suite/share/verilator/include'
        generated=[p for p in sorted(obj.glob('*.cpp')) if not p.name.endswith('__ALL.cpp')]
        commands.append(run([args.host_gxx,'-std=c++20','-O2','-pthread','-I'+native(obj),'-I'+str(inc),'-I'+str(inc/'vltstd'),
            *[native(p) for p in generated],inc/'verilated.cpp',inc/'verilated_threads.cpp',native(runner),'-o',native(exe)],OUT/'cpp_compile.log',env))
    commands.append(run([native(exe),native(OUT/'fixture/plan.tsv'),native(OUT),'200000000','100'],OUT/'run.log',env,timeout=600))
    metrics=json.loads((OUT/'runner_metrics.json').read_text());checked=validate_control(cases)
    if manifest['sources']!={rel(p):sha(p) for p in sources+[runner,Path(__file__)]}:raise RuntimeError('Sources changed during regression; rerun with stable revision')
    manifest.update(result='PASS_RTL_BEHAVIORAL_DDR' if build['with_llm'] else 'PASS_RTL_DDR_NOT_READY',metrics=metrics,cases=checked,commands=commands,
        limitations=['No physical display/DDR calibration/placement/timing tested','Hanzi vectors are supplied validation/protocol fixtures, not a new accuracy benchmark','LLM model loading/inference requires separately tested DDR-ready hardware'])
    save_json(OUT/'summary.json',manifest)
    print(json.dumps({'result':manifest['result'],'cases':len(checked),'simulation_cycles':metrics['simulation_cycles'],'accelerator_starts':metrics['accelerator_starts'],'external_requests':metrics['external_requests']},indent=2),flush=True)


if __name__=='__main__':main()
