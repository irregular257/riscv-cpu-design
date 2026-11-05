#include <stddef.h>
/* GCC may lower structure assignments to memcpy even with -ffreestanding.
   These routines have no heap, syscalls or OS dependencies. */
void *memcpy(void *dst,const void *src,size_t n){unsigned char*d=dst;const unsigned char*s=src;while(n--)*d++=*s++;return dst;}
void *memset(void *dst,int c,size_t n){unsigned char*d=dst;while(n--)*d++=(unsigned char)c;return dst;}
static int bare_errno;
int *__errno(void){return &bare_errno;}
