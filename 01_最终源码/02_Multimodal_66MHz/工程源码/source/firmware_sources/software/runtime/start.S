.option norvc
.option norelax
.section .text.start,"ax",@progbits
.globl _start
_start:
    la sp, _stack_top
    la gp, __global_pointer$
    la t0, _bss_start
    la t1, _bss_end
1:  bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:  la t0, trap_entry
    csrw mtvec, t0
    csrw mie, zero
    csrw mstatus, zero
    fence.i
    call main
3:  wfi
    j 3b
