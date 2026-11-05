#include "coremark.h"
#if VALIDATION_RUN
volatile ee_s32 seed1_volatile=0x3415,seed2_volatile=0x3415;
#else
volatile ee_s32 seed1_volatile=0,seed2_volatile=0;
#endif
volatile ee_s32 seed3_volatile=0x66,seed4_volatile=ITERATIONS,seed5_volatile=0;
ee_u32 default_num_contexts=1;
static CORE_TICKS start_ticks,stop_ticks;
static CORE_TICKS cycles(void){
 volatile ee_u32 *lo=(volatile ee_u32 *)0x10000010,*hi=(volatile ee_u32 *)0x10000014;
 ee_u32 a,b,l;
 do {a=*hi;l=*lo;b=*hi;}while(a!=b);
 return ((CORE_TICKS)a<<32)|l;
}
void portable_uart_gap(void){
#if COREMARK_UART_GAP_MS > 0
 /* Unsigned subtraction also works when the 64-bit counter rolls over. */
 CORE_TICKS begin=cycles();
 const CORE_TICKS interval=((CORE_TICKS)CPU_HZ*COREMARK_UART_GAP_MS)/1000;
 while(cycles()-begin<interval){}
#endif
}
/* Markers bound simulation-only event counters; actual ticks use the same
 * real SoC 64-bit MMIO clock as the physical port. No algorithm is modified. */
void start_time(void){*(volatile ee_u32 *)0x1000000c=0xc001;start_ticks=cycles();}
void stop_time(void){stop_ticks=cycles();*(volatile ee_u32 *)0x1000000c=0xc002;}
CORE_TICKS get_time(void){return stop_ticks-start_ticks;}
secs_ret time_in_secs(CORE_TICKS t){return (double)t/(double)CPU_HZ;}
void portable_init(core_portable *p,int *argc,char *argv[]){
 (void)argc;(void)argv;p->portable_id=1;
 ee_printf("ADVANCED COREMARK START iterations=%u clock_hz=%u\n",(ee_u32)ITERATIONS,(ee_u32)CPU_HZ);
}
void portable_fini(core_portable *p){p->portable_id=0;}
