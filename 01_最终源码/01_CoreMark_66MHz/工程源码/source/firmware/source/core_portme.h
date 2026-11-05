#ifndef CORE_PORTME_H
#define CORE_PORTME_H
#include <stddef.h>
#define HAS_FLOAT 1
#define HAS_TIME_H 0
#define HAS_STDIO 0
#define HAS_PRINTF 0
#define USE_CLOCK 0
#define COMPILER_VERSION "GCC " __VERSION__
#ifndef COMPILER_FLAGS
#define COMPILER_FLAGS "-O2 -march=rv32i -mabi=ilp32 -msmall-data-limit=0 -ffreestanding -fno-builtin -fno-common -ffunction-sections -fdata-sections"
#endif
#define MEM_LOCATION "Static RAM, actual advanced_soc 2-way I/D cache, memory:core 1:1"
typedef signed short ee_s16;
typedef unsigned short ee_u16;
typedef signed int ee_s32;
typedef unsigned int ee_u32;
typedef unsigned char ee_u8;
typedef double ee_f32;
typedef unsigned int ee_ptr_int;
typedef size_t ee_size_t;
#define align_mem(x) ((void *)(4+(((ee_ptr_int)(x)-1)&~3)))
#define CORETIMETYPE unsigned long long
typedef unsigned long long CORE_TICKS;
#define SEED_METHOD SEED_VOLATILE
#define MEM_METHOD MEM_STATIC
#define MULTITHREAD 1
#define MAIN_HAS_NOARGC 1
#define MAIN_HAS_NORETURN 0
#ifndef ITERATIONS
#define ITERATIONS 1
#endif
#ifndef CPU_HZ
#define CPU_HZ 100000000
#endif
#ifndef COREMARK_UART_GAP_MS
#define COREMARK_UART_GAP_MS 0
#endif
extern ee_u32 default_num_contexts;
typedef struct {ee_u8 portable_id;} core_portable;
void portable_init(core_portable *,int *,char *[]);
void portable_fini(core_portable *);
void portable_uart_gap(void);
int ee_printf(const char *,...);
#endif
