"""Serial transport only: images/prompts in, FPGA results out. No LLM on PC."""
import argparse,codecs,json,struct,sys,time,zlib
from pathlib import Path

COMMANDS={'status':1,'mode':2,'display':3,'history':4,'clear-stats':5,'ddr-test':0x10,'model-begin':0x20,'model-write':0x21,'model-commit':0x22,'chat':0x30,'chat-reset':0x31}
ERRORS={1:'invalid header/argument',2:'CRC mismatch',4:'DDR/model unavailable or DDR test failed',5:'model offset/CRC mismatch',6:'LLM runtime error (signed result in payload)',7:'unsupported command/build'}
def crc16(b):
    c=0xffff
    for x in b:
        c^=x<<8
        for _ in range(8):c=((c<<1)^0x1021 if c&0x8000 else c<<1)&0xffff
    return c
def packet(cmd,payload=b'',seq=1):
    if len(payload)>1024:raise ValueError('payload too large')
    b=struct.pack('<BBHH',1,cmd,seq,len(payload))+payload
    return b'\xa5\x43'+b+struct.pack('<H',crc16(b))
def parse_reply(b):
    if len(b)<11 or b[:2]!=b'\x5a\x43':raise ValueError('invalid control reply')
    v,cmd,seq,status,n=struct.unpack_from('<BBHBH',b,2)
    if v!=1 or n>4096 or len(b)!=11+n or crc16(b[2:-2])!=int.from_bytes(b[-2:],'little'):raise ValueError('reply length/version/CRC invalid')
    return cmd,seq,status,b[9:-2]
def decode_status(p):
    if len(p)!=40:raise ValueError('status length')
    return dict(mode=('digit','hanzi')[p[0]],objectness_threshold=p[1],stable_frames=p[2],glyph=p[3],**dict(zip(['ddr_status','frames','accepted','unknown','crc_errors','last_cycles','model_size','model_uploaded'],struct.unpack_from('<8I',p,4))),model_ready=bool(p[36]),ddr_tested=bool(p[37]),upload_active=bool(p[38]),llm_compiled=bool(p[39]))

def chat_blockers(s):
    """Explain readiness without probing or modifying DDR/model contents."""
    reasons=[]
    if not s['llm_compiled']:
        reasons.append('当前固件没有编入 LLM 运行程序。')
    if s['ddr_status']&2:
        reasons.append('DDR 报告访问故障；需要复位并重新验证 DDR。')
    elif not s['ddr_status']&1:
        reasons.append('DDR 尚未校准就绪。')
    elif s['ddr_status']&4:
        reasons.append('DDR 访问仍处于忙状态。')
    if not s['model_ready']:
        if s['model_size']==0:
            reasons.append('模型权重尚未装载：model_size=0；烧录 .sbit 不会自动装载 Qwen 权重。')
        elif s['model_uploaded']<s['model_size']:
            reasons.append(f"模型尚未传完：{s['model_uploaded']}/{s['model_size']} bytes。")
        else:
            reasons.append('模型数据已传完，但校验和初始化尚未成功；model_ready=false。')
    return reasons

def describe_reply(raw):
    """Decode one complete HEX reply copied from the remote serial page."""
    cmd,seq,status,p=parse_reply(raw)
    result=dict(command=cmd,sequence=seq,status=status,
                status_text='OK' if status==0 else 'UTF-8 fragment' if status==0x80 else ERRORS.get(status,'unknown'),
                payload_hex=p.hex(' ').upper())
    if cmd==1 and status==0:
        result['board']=decode_status(p)
        result['chat_blockers']=chat_blockers(result['board'])
        result['chat_ready']=not result['chat_blockers']
    elif status==0x80:
        result['text_fragment']=p.decode('utf-8',errors='replace')
    elif len(p)==4 and cmd in (0x22,0x30):
        result['llm_result']=struct.unpack('<i',p)[0]
    return result
class Client:
    def __init__(self,port,baud=115200,timeout=60):
        import serial
        self.serial=serial.Serial(port,baudrate=baud,timeout=0.2,write_timeout=30)
        self.timeout=timeout;self.seq=0
    def exact(self,n,deadline):
        b=bytearray()
        while len(b)<n:
            if time.monotonic()>deadline:raise TimeoutError('No complete FPGA reply; do not send a new request while inference is still running')
            b+=self.serial.read(n-len(b))
        return bytes(b)
    def receive(self,deadline):
        sync=b''
        while sync!=b'\x5a\x43':sync=(sync+self.exact(1,deadline))[-2:]
        h=self.exact(7,deadline);n=int.from_bytes(h[5:7],'little')
        if n>4096:raise ValueError('oversized reply')
        return parse_reply(sync+h+self.exact(n+2,deadline))
    def command(self,cmd,payload=b'',stream=None,timeout=None):
        self.seq=(self.seq+1)&65535;self.serial.write(packet(cmd,payload,self.seq));self.serial.flush()
        wait=self.timeout if timeout is None else timeout
        deadline=time.monotonic()+wait
        while True:
            rcmd,seq,status,p=self.receive(deadline)
            if (rcmd,seq)!=(cmd,self.seq):raise ValueError('unexpected reply sequence; port is shared or a prior operation is unfinished')
            if status==0x80:
                if stream:stream(p)
                deadline=time.monotonic()+wait
                continue
            if status:
                detail=struct.unpack('<i',p)[0] if len(p)==4 else p.hex()
                raise RuntimeError(f'FPGA status {status}: {ERRORS.get(status,"unknown")}; detail={detail}')
            return p
    def chat(self,payload,stream=None):
        if not 1<=len(payload)<=512:raise ValueError('UTF-8 prompt must contain 1..512 bytes')
        # Status is read-only. Do not start an hours-long upload or a destructive
        # DDR probe as a side effect of the user's attempt to send a prompt.
        s=decode_status(self.command(1,timeout=min(self.timeout,10)))
        reasons=chat_blockers(s)
        if reasons:raise RuntimeError('当前无法对话：'+' '.join(reasons)+' 请先用 status / load 检查和装载模型。')
        print('FPGA 已接收对话前的状态查询，模型就绪；正在请求板端生成，首 token 速度尚无实板保证。',file=sys.stderr,flush=True)
        return self.command(0x30,payload,stream)
    def load(self,path):
        size=path.stat().st_size
        if size<256 or size>0x3e000000:raise ValueError('image does not fit DDR model region')
        s=decode_status(self.command(1))
        if not s['llm_compiled']:raise RuntimeError('This firmware was built without --llm')
        if not s['ddr_tested']:self.command(0x10)
        crc=0
        with path.open('rb') as f:
            while b:=f.read(1024*1024):crc=zlib.crc32(b,crc)
        print(f'{size/1048576:.1f} MiB, CRC32={crc:08x}; raw UART lower bound {size*10/self.serial.baudrate/3600:.2f} hours (ACK/DDR overhead adds time)',flush=True)
        self.command(0x20,struct.pack('<II',size,crc));offset=0;start=time.monotonic();last=0
        with path.open('rb') as f:
            while b:=f.read(1020):
                # Only exactly the last accepted chunk may be retried, using
                # the same offset/data. No retries of arbitrary commands.
                p=self.command(0x21,struct.pack('<I',offset)+b)
                offset+=len(b)
                if len(p)!=4 or int.from_bytes(p,'little')!=offset:raise RuntimeError('upload offset acknowledgement mismatch')
                if time.monotonic()-last>=10:
                    last=time.monotonic();print(f'{offset}/{size} bytes, {(offset/(last-start)):.0f} B/s',flush=True)
        print('FPGA DDR readback CRC and model initialization running; keep port open.',flush=True)
        self.command(0x22);print('FPGA model initialized.')
def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port');ap.add_argument('--baud',type=int,default=115200)
    ap.add_argument('--timeout',type=float,default=3600,help='seconds per reply/token; RV32I inference can be very slow')
    ap.add_argument('--hex-out',type=Path,help='generate a control frame for LabSerial; no connection opened')
    sub=ap.add_subparsers(dest='command',required=True)
    for name in ('status','history','clear-stats','ddr-test','chat-reset'):sub.add_parser(name)
    p=sub.add_parser('mode');p.add_argument('mode',choices=('digit','hanzi'))
    p=sub.add_parser('display');p.add_argument('--threshold',type=int,default=160);p.add_argument('--stable',type=int,default=1);p.add_argument('--brightness',type=int,default=192)
    p=sub.add_parser('chat');p.add_argument('text')
    p=sub.add_parser('load');p.add_argument('image',type=Path)
    p=sub.add_parser('decode-reply',help='离线解析网页串口的一条完整 5A43 HEX 回包，不连接板卡')
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--hex',dest='reply_hex')
    source.add_argument('--file',type=Path,help='仅包含十六进制字节及空白的文本文件')
    args=ap.parse_args();payload=b''
    if args.command=='decode-reply':
        raw=args.reply_hex if args.reply_hex is not None else args.file.read_text(encoding='utf-8-sig')
        print(json.dumps(describe_reply(bytes.fromhex(raw)),ensure_ascii=False,indent=2));return
    if args.command=='mode':payload=bytes([args.mode=='hanzi'])
    elif args.command=='display':
        if not (0<=args.threshold<=255 and 1<=args.stable<=16 and 0<=args.brightness<=255):ap.error('threshold/brightness 0..255, stable 1..16')
        payload=bytes([args.threshold,args.stable,args.brightness])
    elif args.command=='chat':
        payload=args.text.encode('utf-8')
        if not 1<=len(payload)<=512:ap.error('UTF-8 prompt must contain 1..512 bytes')
    if args.hex_out:
        if args.command=='load':ap.error('model loading requires a bidirectional serial transport; browser manual HEX is impractical for a large model')
        args.hex_out.parent.mkdir(parents=True,exist_ok=True)
        args.hex_out.write_text(packet(COMMANDS[args.command],payload).hex(' ').upper()+'\n',encoding='ascii');print(args.hex_out);return
    if not args.port:ap.error('--port is required unless --hex-out is used')
    c=Client(args.port,args.baud,args.timeout)
    try:
        if args.command=='load':c.load(args.image);return
        # A generation limit can end between bytes of a UTF-8 character.
        # Consume the entire framed reply even for malformed model output.
        decoder=codecs.getincrementaldecoder('utf-8')('replace')
        stream=lambda b:print(decoder.decode(b),end='',flush=True)
        if args.command=='chat':
            print('正在查询 FPGA 对话状态（最多等待 10 秒）…',file=sys.stderr,flush=True)
            p=c.chat(payload,stream)
        else:p=c.command(COMMANDS[args.command],payload)
        if args.command=='chat':print(decoder.decode(b'',final=True));return
        if args.command=='status':
            s=decode_status(p)
            print(json.dumps(dict(**s,chat_ready=not chat_blockers(s),chat_blockers=chat_blockers(s)),ensure_ascii=False,indent=2))
        elif args.command=='history':
            if not p or len(p)!=1+8*p[0]:raise ValueError('history length')
            print(json.dumps([dict(zip(('seq','mode','label','objectness','glyph','boxes','stable'),struct.unpack_from('<H6B',p,1+8*i))) for i in range(p[0])],indent=2))
        else:print('OK')
    finally:c.serial.close()
if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,TimeoutError,OSError) as error:
        print('错误：'+str(error),file=sys.stderr)
        raise SystemExit(1)
