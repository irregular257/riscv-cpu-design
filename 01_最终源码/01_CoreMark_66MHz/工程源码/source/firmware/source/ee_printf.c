/* Minimal platform-only formatter. CoreMark algorithm sources remain unchanged. */
#include "coremark.h"
#include <stdarg.h>
/* The remote console can truncate long receive bursts. Keep each physical
 * line within 48 bytes, including CR/LF, and let the platform leave a quiet
 * interval between lines. This only affects reporting outside CoreMark's
 * start_time/stop_time interval; the official workload is unchanged.
 * Override the two hooks only in the host transport regression. */
#ifndef COREMARK_UART_WRITE
#define COREMARK_UART_WRITE(c) (*(volatile ee_u32 *)0x10000000=(unsigned char)(c))
#endif
#ifndef COREMARK_UART_GAP
#define COREMARK_UART_GAP() portable_uart_gap()
#endif
static unsigned uart_column;
static void end_uart_line(void){
 COREMARK_UART_WRITE('\r');COREMARK_UART_WRITE('\n');
 uart_column=0;COREMARK_UART_GAP();
}
static void putc_uart(char c){
 if(c=='\n'){end_uart_line();return;}
 if(uart_column==46)end_uart_line();
 COREMARK_UART_WRITE(c);uart_column++;
}
static void number(ee_u32 n,unsigned base,int width,char pad){
 char b[32];int i=0;do{b[i++]="0123456789abcdef"[n%base];n/=base;}while(n);
 while(width-- > i)putc_uart(pad);while(i)putc_uart(b[--i]);
}
int ee_printf(const char *f,...){
 va_list a;va_start(a,f);
 while(*f){
  if(*f!='%'){putc_uart(*f++);continue;}f++;
  char pad=' ';int width=0,precision=6;
  if(*f=='0'){pad='0';f++;}
  while(*f>='0'&&*f<='9')width=width*10+*f++-'0';
  if(*f=='.'){f++;precision=0;while(*f>='0'&&*f<='9')precision=precision*10+*f++-'0';}
  while(*f=='l')f++;
  switch(*f++){
   case 's':{const char *s=va_arg(a,const char *);while(*s)putc_uart(*s++);break;}
   case 'c':putc_uart((char)va_arg(a,int));break;
   case 'd':{ee_s32 n=va_arg(a,int);if(n<0){putc_uart('-');number(0-(ee_u32)n,10,width,pad);}else number(n,10,width,pad);break;}
   case 'u':number(va_arg(a,unsigned),10,width,pad);break;
   case 'x':number(va_arg(a,unsigned),16,width,pad);break;
   case 'f':{double d=va_arg(a,double);if(d<0){putc_uart('-');d=-d;}ee_u32 n=(ee_u32)d;number(n,10,0,' ');putc_uart('.');d-=n;for(int i=0;i<precision;i++){d*=10;n=(ee_u32)d;putc_uart('0'+n);d-=n;}break;}
   case '%':putc_uart('%');break;
   default:putc_uart('?');break;
  }
 }
 va_end(a);return 0;
}
void *memcpy(void *d,const void *s,size_t n){unsigned char *a=d;const unsigned char *b=s;while(n--)*a++=*b++;return d;}
void *memset(void *d,int c,size_t n){unsigned char *a=d;while(n--)*a++=c;return d;}
