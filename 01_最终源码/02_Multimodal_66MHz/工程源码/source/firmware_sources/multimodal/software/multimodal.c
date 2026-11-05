#include "multimodal.h"
#ifdef WITH_LLM
#include "llm/llm.h"
#endif
#define REG(a) (*(volatile uint32_t *)(a))
#define UART REG(0x10000000u)
#define US REG(0x10000004u)
#define CYCLE REG(0x10000010u)
#define DDR_STATUS REG(0x10000500u)
#define DDR_BASE 0x80000000u
#define DDR_BYTES 0x40000000u
#ifndef UART_TIMEOUT
#define UART_TIMEOUT 1620000000u
#endif
static uint8_t frame[1024],reply[256];
static uint8_t mode,threshold=160,stable_required=1,stable_count,last_candidate=15,shown=15;
static uint32_t frames,accepted,unknown,crc_errors,last_cycles;
static uint8_t history[16][8];
static unsigned history_next,history_count;
static uint32_t model_size,model_offset,model_expected_crc,model_crc,last_offset,last_length;
static uint8_t ddr_tested,model_ready,upload_active;

static void put(uint8_t b){while(!(US&1)){}UART=b;}
static void text(const char*s){while(*s)put((uint8_t)*s++);}
static int get(uint8_t*b){uint32_t start=CYCLE;while(!(US&2))if((uint32_t)(CYCLE-start)>UART_TIMEOUT)return 0;*b=(uint8_t)UART;return 1;}
static uint16_t crc16(uint16_t c,uint8_t b){c^=(uint16_t)b<<8;for(unsigned i=0;i<8;i++)c=(c&0x8000)?(uint16_t)((c<<1)^0x1021):(uint16_t)(c<<1);return c;}
static uint32_t crc32(uint32_t c,uint8_t b){c^=b;for(unsigned i=0;i<8;i++)c=(c>>1)^((0u-(c&1u))&0xedb88320u);return c;}
static uint32_t load32(const uint8_t*p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void save32(uint8_t*p,uint32_t x){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(x>>(i*8));}
static void send(uint8_t b,uint16_t*c){put(b);*c=crc16(*c,b);}
static void glyph(unsigned g){shown=(uint8_t)g;REG(0x10000404u)=g;}
static void display_reset(void){stable_count=0;last_candidate=15;glyph(15);}
static int ddr_ok(void){return (DDR_STATUS&7u)==1u;}
static void control_reply(uint8_t cmd,uint16_t seq,uint8_t status,const uint8_t*p,unsigned n){
    uint16_t c=0xffff;put(0x5a);put(0x43);send(1,&c);send(cmd,&c);
    send((uint8_t)seq,&c);send((uint8_t)(seq>>8),&c);send(status,&c);
    send((uint8_t)n,&c);send((uint8_t)(n>>8),&c);
    for(unsigned i=0;i<n;i++)send(p[i],&c);
    put((uint8_t)c);put((uint8_t)(c>>8));
}
static void vision_reply(unsigned status,uint16_t seq,unsigned flags,uint32_t cycles,unsigned n,vision_detection_t*b){
    uint16_t c=0xffff;put(0x5a);put(0x59);send(1,&c);send((uint8_t)status,&c);
    send((uint8_t)seq,&c);send((uint8_t)(seq>>8),&c);send((uint8_t)n,&c);send((uint8_t)flags,&c);
    for(unsigned i=0;i<4;i++)send((uint8_t)(cycles>>(i*8)),&c);
    for(unsigned i=0;i<n;i++){send(b[i].x,&c);send(b[i].y,&c);send(b[i].w,&c);send(b[i].h,&c);send(b[i].label,&c);send(b[i].confidence,&c);send(0,&c);send(0,&c);}
    const int16_t*h=mode?hanzi_vision_last_head():digit_vision_last_head();
    if(flags&1)for(unsigned cell=0;cell<16;cell++)for(unsigned ch=0;ch<15;ch++){uint16_t v=(uint16_t)h[ch*16+cell];send((uint8_t)v,&c);send((uint8_t)(v>>8),&c);}
    put((uint8_t)c);put((uint8_t)(c>>8));
}
static void display_result(uint16_t seq,unsigned n,vision_detection_t*b){
    unsigned g=14,score=0,label=255;
    /* Detection array is already sorted by objectness. Preserve all returned
       labels, but display only digits and the four supported direction signs. */
    if(n){label=b[0].label;score=b[0].confidence;if(score>=threshold){
        if(!mode && label<10)g=label;
        else if(mode && label>=2 && label<=5)g=label+8;
    }}
    frames++;
    if(g<14){if(g==last_candidate){if(stable_count<255)stable_count++;}else{last_candidate=(uint8_t)g;stable_count=1;}
        if(stable_count>=stable_required){glyph(g);accepted++;}else glyph(16);
    }else{unknown++;stable_count=0;last_candidate=15;glyph(14);}
    uint8_t*h=history[history_next];h[0]=(uint8_t)seq;h[1]=(uint8_t)(seq>>8);h[2]=mode;h[3]=(uint8_t)label;h[4]=(uint8_t)score;h[5]=shown;h[6]=(uint8_t)n;h[7]=stable_count;
    history_next=(history_next+1)&15;if(history_count<16)history_count++;
}
static void vision_request(void){
    uint8_t h[8],b,lo,hi;uint16_t c=0xffff;
    for(unsigned i=0;i<8;i++){if(!get(h+i))return;c=crc16(c,h[i]);}
    uint16_t seq=(uint16_t)h[4]|((uint16_t)h[5]<<8);unsigned length=(unsigned)h[6]|((unsigned)h[7]<<8);
    if(h[0]!=1||h[1]!=32||h[2]!=32||h[3]>1||length!=1024){vision_reply(1,seq,0,0,0,0);return;}
    unsigned range=0;for(unsigned i=0;i<1024;i++){if(!get(&b))return;frame[i]=b;if(b>16)range=1;c=crc16(c,b);}
    if(!get(&lo)||!get(&hi))return;
    if(c!=((uint16_t)lo|((uint16_t)hi<<8))){crc_errors++;vision_reply(2,seq,0,0,0,0);return;}
    if(range){vision_reply(3,seq,0,0,0,0);return;}
    vision_detection_t boxes[4];uint32_t start=CYCLE;
    unsigned n=mode?hanzi_vision_infer(frame,boxes):digit_vision_infer(frame,boxes);
    last_cycles=CYCLE-start;display_result(seq,n,boxes);
    vision_reply(0,seq,h[3],last_cycles,n,boxes);
}
static int ddr_probe(void){
    /* Run only before a model is loaded. Detect common capacity/address alias
       errors in addition to a 4 KiB walking-data test. Not an exhaustive BIST. */
    static const uint32_t offset[6]={0,0x08000000,0x10000000,0x20000000,0x30000000,0x3ffffffc};
    if(!ddr_ok()||model_ready||upload_active)return 0;
    for(unsigned i=0;i<6;i++)REG(DDR_BASE+offset[i])=0x39ac0000u+i;
    for(unsigned i=0;i<6;i++)if(REG(DDR_BASE+offset[i])!=0x39ac0000u+i)return 0;
    for(unsigned i=0;i<1024;i++)REG(DDR_BASE+0x1000u+i*4)=(1u<<(i&31))^i;
    for(unsigned i=0;i<1024;i++)if(REG(DDR_BASE+0x1000u+i*4)!=((1u<<(i&31))^i))return 0;
    return ddr_ok();
}
#ifdef WITH_LLM
typedef struct{uint8_t cmd;uint16_t seq;} emit_context;
static int emit_token(const uint8_t*p,size_t n,void*user){emit_context*e=(emit_context*)user;while(n){unsigned k=n>128?128:(unsigned)n;control_reply(e->cmd,e->seq,0x80,p,k);p+=k;n-=k;}return 1;}
#endif
static void command(uint8_t cmd,uint16_t seq,unsigned n){
    unsigned status=0,bytes=0;
    switch(cmd){
    case 1:
        if(n){status=1;break;}
        reply[0]=mode;reply[1]=threshold;reply[2]=stable_required;reply[3]=shown;
        save32(reply+4,DDR_STATUS);save32(reply+8,frames);save32(reply+12,accepted);save32(reply+16,unknown);save32(reply+20,crc_errors);save32(reply+24,last_cycles);
        save32(reply+28,model_size);save32(reply+32,model_offset);reply[36]=model_ready;reply[37]=ddr_tested;reply[38]=upload_active;
#ifdef WITH_LLM
        reply[39]=1;
#else
        reply[39]=0;
#endif
        bytes=40;break;
    case 2:if(n!=1||frame[0]>1){status=1;break;}mode=frame[0];display_reset();break;
    case 3:if(n!=3||!frame[1]||frame[1]>16){status=1;break;}threshold=frame[0];stable_required=frame[1];REG(0x10000408u)=frame[2];display_reset();break;
    case 4:
        if(n){status=1;break;}reply[0]=(uint8_t)history_count;bytes=1;
        for(unsigned i=0;i<history_count;i++){unsigned k=(history_next+16-history_count+i)&15;for(unsigned j=0;j<8;j++)reply[bytes++]=history[k][j];}break;
    case 5:if(n){status=1;break;}frames=accepted=unknown=crc_errors=0;history_count=history_next=0;break;
    case 0x10:if(n){status=1;break;}ddr_tested=(uint8_t)ddr_probe();if(!ddr_tested)status=4;break;
    case 0x20:
        if(n!=8){status=1;break;}if(!ddr_ok()||!ddr_tested){status=4;break;}
        {uint32_t size=load32(frame);if(size<256||size>DDR_BYTES-0x02000000u){status=1;break;}
        model_ready=0;upload_active=1;model_size=size;model_offset=0;model_expected_crc=load32(frame+4);model_crc=0xffffffff;last_length=0;last_offset=0;}
        break;
    case 0x21:
        if(n<5){status=1;break;}if(!ddr_ok()||!upload_active){status=4;break;}
        {uint32_t offset=load32(frame);unsigned len=n-4;
        if(offset==last_offset&&len==last_length){
            for(unsigned i=0;i<len;i++)if(*(volatile uint8_t*)(DDR_BASE+offset+i)!=frame[4+i]){status=5;break;}
        }else if(offset!=model_offset||offset>model_size||len>model_size-offset)status=5;
        else{unsigned i=0;
            for(;i<len && ((offset+i)&3);i++)*(volatile uint8_t*)(DDR_BASE+offset+i)=frame[4+i];
            for(;i+4<=len;i+=4)REG(DDR_BASE+offset+i)=load32(frame+4+i);
            for(;i<len;i++)*(volatile uint8_t*)(DDR_BASE+offset+i)=frame[4+i];
            for(i=0;i<len;i++)model_crc=crc32(model_crc,frame[4+i]);
            last_offset=offset;last_length=len;model_offset+=len;}
        save32(reply,model_offset);bytes=4;}break;
    case 0x22:
        if(n){status=1;break;}if(!ddr_ok()||!upload_active){status=4;break;}
        if(model_offset!=model_size||(model_crc^0xffffffffu)!=model_expected_crc){status=5;break;}
        /* Verify DDR readback, not just received UART bytes. */
        {uint32_t c=0xffffffff,i=0;
        for(;i+4<=model_size;i+=4){uint32_t w=REG(DDR_BASE+i);for(unsigned k=0;k<4;k++)c=crc32(c,(uint8_t)(w>>(k*8)));}
        for(;i<model_size;i++)c=crc32(c,*(volatile uint8_t*)(DDR_BASE+i));
        if((c^0xffffffffu)!=model_expected_crc){status=5;break;}}
#ifdef WITH_LLM
        {int r=llm_init_loaded((void*)DDR_BASE,model_size,DDR_BYTES);save32(reply,(uint32_t)r);bytes=4;
        if(r){status=6;break;}model_ready=1;upload_active=0;}
#else
        status=7;
#endif
        break;
    case 0x30:
        if(!n||n>512){status=1;break;}
#ifdef WITH_LLM
        if(!ddr_ok()||!model_ready){status=4;break;}
        {emit_context e={cmd,seq};glyph(16);int r=llm_chat(frame,n,emit_token,&e);glyph(r?14:15);save32(reply,(uint32_t)r);bytes=4;if(r)status=6;}
#else
        status=7;
#endif
        break;
    case 0x31:
        if(n){status=1;break;}
#ifdef WITH_LLM
        llm_reset();
#else
        status=7;
#endif
        break;
    default:status=7;break;
    }
    control_reply(cmd,seq,(uint8_t)status,reply,bytes);
}
static void control_request(void){
    uint8_t h[6],lo,hi;uint16_t c=0xffff;
    for(unsigned i=0;i<6;i++){if(!get(h+i))return;c=crc16(c,h[i]);}
    uint16_t seq=(uint16_t)h[2]|((uint16_t)h[3]<<8);unsigned n=(unsigned)h[4]|((unsigned)h[5]<<8);
    if(h[0]!=1||n>sizeof(frame)){control_reply(h[1],seq,1,0,0);return;}
    for(unsigned i=0;i<n;i++){if(!get(frame+i))return;c=crc16(c,frame[i]);}
    if(!get(&lo)||!get(&hi))return;
    if(c!=((uint16_t)lo|((uint16_t)hi<<8))){crc_errors++;control_reply(h[1],seq,2,0,0);return;}
    command(h[1],seq,n);
}
void multimodal_loop(void){
    REG(0x10000400u)=1;REG(0x10000408u)=192;display_reset();
    text("MULTIMODAL V1 DIGIT/HANZI MATRIX DDR\r\n");
    text("MODEL DIGIT MNIST28\r\nTINY GRID YOLO READY\r\n");
    for(;;){uint8_t b;if(!get(&b)||b!=0xa5)continue;if(!get(&b))continue;if(b==0x59)vision_request();else if(b==0x43)control_request();}
}
