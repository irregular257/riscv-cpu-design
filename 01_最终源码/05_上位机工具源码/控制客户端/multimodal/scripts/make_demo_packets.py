"""Create LabSerial HEX chunks from the actual UART regression fixtures."""
import json
from pathlib import Path
from client import packet,crc16
M=Path(__file__).resolve().parents[1]
def main():
    root=M/'packets';root.mkdir(exist_ok=True)
    for mode,n in (('digit',0),('hanzi',1)):
        (root/f'mode_{mode}.hex').write_text(packet(2,bytes([n])).hex(' ').upper()+'\n',encoding='ascii')
    (root/'status.hex').write_text(packet(1).hex(' ').upper()+'\n',encoding='ascii')
    (root/'display_default.hex').write_text(packet(3,bytes([160,1,192])).hex(' ').upper()+'\n',encoding='ascii')
    result=[]
    for name,case,mode,glyph in [('digit8','digit_native28_digit8','digit',8),('up','hanzi_up','hanzi',10),('down','hanzi_down','hanzi',11),('left','hanzi_left','hanzi',12),('right','hanzi_right','hanzi',13)]:
        b=(M/'reports/integration/fixture'/case/'request.bin').read_bytes()
        assert len(b)==1036 and b[:2]==b'\xa5\x59' and crc16(b[2:-2])==int.from_bytes(b[-2:],'little')
        out=root/name;out.mkdir(exist_ok=True);(out/'request.bin').write_bytes(b)
        for i,offset in enumerate(range(0,len(b),256),1):
            (out/f'{i:02d}.hex').write_text(b[offset:offset+256].hex(' ').upper()+'\n',encoding='ascii')
        result.append({'name':name,'mode':mode,'glyph':glyph,'chunks':[256,256,256,256,12],'source_case':case,'physical_board_verified':False})
    (root/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (root/'README.txt').write_text('先发送 display_default.hex，然后发送 mode_digit.hex 或 mode_hanzi.hex。\n等待每个控制命令的 5A43 回复，再依次发送目标目录中的 01.hex 到 05.hex，均使用 HEX 模式。\n这五块拼成一条原 A559 图像请求，不要加 CR/LF，不要重复同步头，块间隔必须小于60秒。\n等待完整 5A59 回复后再发送下一张图。digit8 应显示8，up/down/left/right 应显示对应箭头。\n这些是通过RTL仿真的样例输入，不是远程板验证记录；需先用PDS编译并烧录新工程。\n',encoding='utf-8')
    print('Created digit8 and four direction frames, each 256+256+256+256+12 bytes')
if __name__=='__main__':main()
