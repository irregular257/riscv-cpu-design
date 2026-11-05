#include <stdint.h>
#ifdef WITH_VISION
#include "multimodal.h"
#endif
#define REG(a) (*(volatile uint32_t *)(a))
static volatile uint32_t software_irqs,timer_irqs,sync_traps,expected_cause;
static volatile uint32_t cache_words[192] __attribute__((aligned(256)));
static void put(uint8_t b){while(!(REG(0x10000004)&1u)){}REG(0x10000000)=b;}
static void text(const char*s){while(*s)put((uint8_t)*s++);}
static void hex(uint32_t x){static const char d[]="0123456789abcdef";for(int i=28;i>=0;i-=4)put(d[(x>>i)&15]);}
static uint32_t csr_mcause(void){uint32_t x;__asm__ volatile("csrr %0,mcause":"=r"(x));return x;}
static void failed(const char*s){REG(0x1000000c)=0x100;text("FAIL ");text(s);text("\r\n");for(;;){}}
void trap_dispatch(void){
  uint32_t cause=csr_mcause(),pc;
  if(cause==0x80000003u){REG(0x02000000)=0;software_irqs++;return;}
  if(cause==0x80000007u){REG(0x02004004)=0xffffffffu;REG(0x02004000)=0xffffffffu;timer_irqs++;return;}
  if(!(cause&0x80000000u) && cause==expected_cause){
    sync_traps++;__asm__ volatile("csrr %0,mepc":"=r"(pc));pc+=4;
    __asm__ volatile("csrw mepc,%0"::"r"(pc));return;
  }
  text("UNEXPECTED TRAP cause=");hex(cause);text(" pc=");
  __asm__ volatile("csrr %0,mepc":"=r"(pc));hex(pc);text("\r\n");failed("trap");
}
static void check_privileged(void){
  uint32_t misa;__asm__ volatile("csrr %0,misa":"=r"(misa));
  if(misa!=0x40000100u)failed("misa");
  expected_cause=11;__asm__ volatile("ecall");
  expected_cause=3;__asm__ volatile("ebreak");
  expected_cause=2;__asm__ volatile(".word 0xffffffff");
  expected_cause=0xffffffffu;
  if(sync_traps!=3)failed("synchronous trap/MRET");
  __asm__ volatile("csrw mie,%0"::"r"(8u));
  __asm__ volatile("csrsi mstatus,8");REG(0x02000000)=1;
  for(unsigned i=0;i<10000 && software_irqs==0;i++)__asm__ volatile("nop");
  if(software_irqs!=1)failed("software IRQ");
  uint32_t now=REG(0x0200bff8),hi=REG(0x0200bffc);
  uint32_t deadline=now+5000u;if(deadline<now)hi++;
  REG(0x02004004)=0xffffffffu;REG(0x02004000)=deadline;REG(0x02004004)=hi;
  __asm__ volatile("csrw mie,%0"::"r"(128u));
  for(unsigned i=0;i<20000 && timer_irqs==0;i++)__asm__ volatile("nop");
  if(timer_irqs!=1)failed("timer IRQ");
  __asm__ volatile("csrci mstatus,8");__asm__ volatile("csrw mie,zero");
  text("MACHINE PASS traps=3 software_irq=1 timer_irq=1\r\n");
}
static void check_cache_gpio(void){
  cache_words[0]=0x12345678;cache_words[64]=0x89abcdef;cache_words[128]=0x31415926;
  for(unsigned i=0;i<8;i++){
    if(cache_words[0]!=0x12345678 || cache_words[64]!=0x89abcdef || cache_words[128]!=0x31415926)failed("cache conflict");
  }
  volatile uint8_t*b=(volatile uint8_t*)&cache_words[0];b[1]=0xa5;
  if(cache_words[0]!=0x1234a578u)failed("cache byte store");
  __asm__ volatile("fence iorw,iorw\n fence.i":::"memory");
  REG(0x10000208)=15;REG(0x10000204)=5;REG(0x1000021c)=2;REG(0x10000220)=1;
  if(REG(0x10000204)!=6 || REG(0x10000208)!=15)failed("GPIO");
  REG(0x10000208)=0;
  text("CACHE/GPIO PASS ihit=");hex(REG(0x10000300));text(" imiss=");hex(REG(0x10000304));
  text(" dhit=");hex(REG(0x10000310));text(" dmiss=");hex(REG(0x10000314));text("\r\n");
}
int main(void){
  text("ADVANCED RV32I BOOT\r\n");check_privileged();check_cache_gpio();
  REG(0x1000000c)=1;text("ADVANCED SELFTEST PASS\r\n");
#ifdef WITH_VISION
  multimodal_loop();
#else
  for(;;){if(REG(0x10000004)&2u)put((uint8_t)REG(0x10000000));}
#endif
}
