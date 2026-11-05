OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { ROM (rx) : ORIGIN = 0, LENGTH = 64K
         RAM (rw) : ORIGIN = 0x8000, LENGTH = 32K }
SECTIONS {
 .text : { KEEP(*(.text.start)) *(.text .text.*) } > ROM
 .rodata : { *(.rodata .rodata.* .srodata .srodata.*) } > RAM
 .data : { *(.data .data.* .sdata .sdata.*) } > RAM
 .bss (NOLOAD) : { . = ALIGN(4); _bss_start = .; *(.bss .bss.* .sbss .sbss.*) *(COMMON) . = ALIGN(4); _bss_end = .; } > RAM
 _stack_top = ORIGIN(RAM)+LENGTH(RAM);
 ASSERT(_bss_end < _stack_top-8192, "Insufficient stack reserve")
}
