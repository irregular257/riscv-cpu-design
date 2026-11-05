#include "vision_firmware.h"
#include "vision_model.h"

/* No float runtime, dynamic allocation, libc or off-board inference. */
static int8_t feature0[8*16*16],feature1[16*8*8],feature2[16*4*4],feature3[32*4*4];
static int16_t head[15*4*4];
static int8_t patch[16*3*3];
static uint8_t frame[1024];
#define REG32(a) (*(volatile uint32_t *)(a))
#define UART_DATA REG32(0x10000000u)
#define UART_STATUS REG32(0x10000004u)
#define CYCLE_LO REG32(0x10000010u)
#define DOT(off) REG32(0x10000100u+(off))
#ifndef VISION_UART_TIMEOUT_CYCLES
#define VISION_UART_TIMEOUT_CYCLES 81000000u
#endif

static int32_t dot(const int8_t *x,const int8_t *w,unsigned count,int32_t bias){
#if defined(VISION_SOFT_REFERENCE) || defined(VISION_DOT_SOFTWARE)
    for(unsigned i=0;i<count;i++)bias+=(int32_t)x[i]*(int32_t)w[i];
    return bias;
#else
    DOT(8)=(uint32_t)x;DOT(12)=(uint32_t)w;DOT(16)=count;DOT(20)=(uint32_t)bias;
    /* Publish CPU stores before the read-only DMA starts. D-cache is write-through. */
    __asm__ volatile("fence rw,rw" ::: "memory");
    DOT(0)=1;
    uint32_t status;
    do { status=DOT(4); } while(!(status&2));
    /* The model uses valid aligned RAM addresses; error is never silently accepted. */
    if(status&4){REG32(0x1000000cu)=0x200;for(;;){}}
    return (int32_t)DOT(24);
#endif
}
static int16_t requant(int32_t value,int shift,unsigned relu){
    if(relu && value<0)value=0;
    if(shift>0)value=(value+(1<<(shift-1)))>>shift;
    else if(shift<0)value<<=-shift;
    if(value>(relu?96:32767))value=relu?96:32767;
    if(value<(relu?0:-32768))value=relu?0:-32768;
    return (int16_t)value;
}
static void convolution(const int8_t *input,unsigned side,unsigned channels,
                        int8_t *output,unsigned out_channels,unsigned kernel,unsigned stride,
                        const int8_t *weights,const int32_t *bias,int shift,unsigned relu){
    unsigned out_side=(side+stride-1)/stride;
    unsigned n=channels*kernel*kernel;
    int pad=(int)kernel/2;
    for(unsigned oy=0;oy<out_side;oy++)for(unsigned ox=0;ox<out_side;ox++){
        unsigned index=0;
        for(unsigned ic=0;ic<channels;ic++)for(unsigned ky=0;ky<kernel;ky++)for(unsigned kx=0;kx<kernel;kx++){
            int iy=(int)(oy*stride+ky)-pad,ix=(int)(ox*stride+kx)-pad;
            patch[index++]=(iy>=0 && ix>=0 && iy<(int)side && ix<(int)side) ? input[(ic*side+(unsigned)iy)*side+(unsigned)ix] : 0;
        }
        for(unsigned oc=0;oc<out_channels;oc++)
            output[(oc*out_side+oy)*out_side+ox]=requant(dot(patch,weights+oc*n,n,bias[oc]),shift,relu);
    }
}
static uint16_t sigmoid(int16_t value){
    int index=(int)value+128;if(index<0)index=0;if(index>255)index=255;
    return vision_sigmoid_q15[index];
}
static unsigned min_u(unsigned a,unsigned b){return a<b?a:b;}
static unsigned max_u(unsigned a,unsigned b){return a>b?a:b;}
static int clip_int(int v,int low,int high){return v<low?low:(v>high?high:v);}
static unsigned overlap_exceeds_half(const vision_detection_t *a,const vision_detection_t *b){
    int width=(int)min_u(a->x+a->w,b->x+b->w)-(int)max_u(a->x,b->x);
    int height=(int)min_u(a->y+a->h,b->y+b->h)-(int)max_u(a->y,b->y);
    unsigned intersection=(width>0 && height>0)?(unsigned)(width*height):0;
    unsigned total=(unsigned)a->w*a->h+(unsigned)b->w*b->h-intersection;
    return 2*intersection>total;
}
unsigned vision_infer(const uint8_t pixels[1024],vision_detection_t output[4]){
    convolution((const int8_t *)pixels,32,1,feature0,8,5,2,vision_l0_weights,vision_l0_bias,VISION_L0_SHIFT,1);
    convolution(feature0,16,8,feature1,16,3,2,vision_l1_weights,vision_l1_bias,VISION_L1_SHIFT,1);
    convolution(feature1,8,16,feature2,16,3,2,vision_l2_weights,vision_l2_bias,VISION_L2_SHIFT,1);
    convolution(feature2,4,16,feature3,32,3,1,vision_l3_weights,vision_l3_bias,VISION_L3_SHIFT,1);
    for(unsigned cell=0;cell<16;cell++){
        for(unsigned ic=0;ic<32;ic++)patch[ic]=feature3[ic*16+cell];
        for(unsigned oc=0;oc<15;oc++)head[oc*16+cell]=requant(dot(patch,vision_l4_weights+oc*32,32,vision_l4_bias[oc]),VISION_L4_SHIFT,0);
    }
    vision_detection_t candidates[16];unsigned count=0;
    for(unsigned y=0;y<4;y++)for(unsigned x=0;x<4;x++){
        unsigned cell=y*4+x;
        uint16_t probability=sigmoid(head[4*16+cell]);
        if(probability<16384)continue;
        int cx=(int)x*2048+((sigmoid(head[cell])*2048+16384)>>15);
        int cy=(int)y*2048+((sigmoid(head[16+cell])*2048+16384)>>15);
        int width=(sigmoid(head[32+cell])*8192+16384)>>15;
        int height=(sigmoid(head[48+cell])*8192+16384)>>15;
        int left=clip_int((cx-width/2)>>8,0,31),top=clip_int((cy-height/2)>>8,0,31);
        int right=clip_int((cx+width/2+255)>>8,left+1,32),bottom=clip_int((cy+height/2+255)>>8,top+1,32);
        unsigned label=0;for(unsigned j=1;j<10;j++)if(head[(5+j)*16+cell]>head[5*16+cell+label*16])label=j;
        vision_detection_t box={(uint8_t)left,(uint8_t)top,(uint8_t)(right-left),(uint8_t)(bottom-top),(uint8_t)label,(uint8_t)((probability*255+16384)>>15)};
        candidates[count++]=box;
    }
    /* Stable descending objectness, then class-agnostic IoU NMS at 0.5. */
    for(unsigned i=1;i<count;i++){
        vision_detection_t box=candidates[i];unsigned j=i;
        while(j && candidates[j-1].confidence<box.confidence){candidates[j]=candidates[j-1];j--;}
        candidates[j]=box;
    }
    unsigned kept=0;
    for(unsigned i=0;i<count && kept<4;i++){
        unsigned suppress=0;
        for(unsigned j=0;j<kept;j++)if(overlap_exceeds_half(&candidates[i],&output[j]))suppress=1;
        if(!suppress)output[kept++]=candidates[i];
    }
    return kept;
}
const int16_t *vision_last_head(void){return head;}

#ifndef VISION_SOFT_REFERENCE
static void put(uint8_t b){while(!(UART_STATUS&1)){}UART_DATA=b;}
static void text(const char *s){while(*s)put((uint8_t)*s++);}
static int get_timeout(uint8_t *b){
    uint32_t start=CYCLE_LO;
    while(!(UART_STATUS&2))if((uint32_t)(CYCLE_LO-start)>VISION_UART_TIMEOUT_CYCLES)return 0;
    *b=(uint8_t)UART_DATA;return 1;
}
static uint16_t crc_step(uint16_t crc,uint8_t b){
    crc^=(uint16_t)b<<8;
    for(unsigned i=0;i<8;i++)crc=(uint16_t)((crc&0x8000)?(crc<<1)^0x1021:crc<<1);
    return crc;
}
static void send_crc(uint8_t b,uint16_t *crc){put(b);*crc=crc_step(*crc,b);}
static void response(unsigned status,uint16_t seq,unsigned flags,uint32_t cycles,unsigned count,vision_detection_t *boxes){
    uint16_t crc=0xffff;put(0x5a);put(0x59);
    send_crc(1,&crc);send_crc((uint8_t)status,&crc);send_crc((uint8_t)seq,&crc);send_crc((uint8_t)(seq>>8),&crc);
    send_crc((uint8_t)count,&crc);send_crc((uint8_t)flags,&crc);
    for(unsigned i=0;i<4;i++)send_crc((uint8_t)(cycles>>(8*i)),&crc);
    for(unsigned i=0;i<count;i++){
        send_crc(boxes[i].x,&crc);send_crc(boxes[i].y,&crc);send_crc(boxes[i].w,&crc);send_crc(boxes[i].h,&crc);
        send_crc(boxes[i].label,&crc);send_crc(boxes[i].confidence,&crc);send_crc(0,&crc);send_crc(0,&crc);
    }
    if(flags&1)for(unsigned cell=0;cell<16;cell++)for(unsigned channel=0;channel<15;channel++){
        uint16_t raw=(uint16_t)head[channel*16+cell];send_crc((uint8_t)raw,&crc);send_crc((uint8_t)(raw>>8),&crc);
    }
    put((uint8_t)crc);put((uint8_t)(crc>>8));
}
void vision_loop(void){
    text("MODEL DIGIT MNIST28\r\n");
    text("TINY GRID YOLO READY\r\n");
    for(;;){
        uint8_t b,header[8];
        if(!get_timeout(&b) || b!=0xa5)continue;
        if(!get_timeout(&b) || b!=0x59)continue;
        uint16_t crc=0xffff;unsigned complete=1;
        for(unsigned i=0;i<8;i++){if(!get_timeout(&header[i])){complete=0;break;}crc=crc_step(crc,header[i]);}
        if(!complete)continue;
        uint16_t seq=(uint16_t)header[4]|((uint16_t)header[5]<<8);
        unsigned length=(unsigned)header[6]|((unsigned)header[7]<<8);
        if(header[0]!=1 || header[1]!=32 || header[2]!=32 || length!=1024 || header[3]>1){response(1,seq,0,0,0,0);continue;}
        unsigned range_error=0;
        for(unsigned i=0;i<1024;i++){
            if(!get_timeout(&b)){complete=0;break;}
            frame[i]=b;if(b>16)range_error=1;crc=crc_step(crc,b);
        }
        uint8_t low,high;
        if(!complete || !get_timeout(&low) || !get_timeout(&high))continue;
        if(crc!=((uint16_t)low|((uint16_t)high<<8))){response(2,seq,0,0,0,0);continue;}
        if(range_error){response(3,seq,0,0,0,0);continue;}
        vision_detection_t boxes[4];uint32_t start=CYCLE_LO;
        unsigned n=vision_infer(frame,boxes);
        response(0,seq,header[3],CYCLE_LO-start,n,boxes);
    }
}
#else
void vision_loop(void){}
#endif
