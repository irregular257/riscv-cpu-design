OUTPUT_ARCH(riscv)
ENTRY(_start)
/* Separate Harvard instruction/data storage; equal addresses do not alias. */
MEMORY { ROM (rx) : ORIGIN = 0, LENGTH = 64K
         RAM (rw) : ORIGIN = 0x1000, LENGTH = 56K }
SECTIONS {
  .text : { KEEP(*(.text.start)) KEEP(*(.text.trap)) *(.text .text.*) } > ROM
  .rodata : AT(0x10000 + ADDR(.rodata)) { *(.rodata .rodata.*) *(.srodata .srodata.*) } > RAM
  .data : AT(0x10000 + ADDR(.data)) { *(.data .data.*) *(.sdata .sdata.*) } > RAM
  . = ALIGN(16);
  _bss_start = .;
  .bss (NOLOAD) : { *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON) } > RAM
  . = ALIGN(16);
  _bss_end = .;
  _stack_top = 0x10000;
  __global_pointer$ = 0x1800;
  ASSERT(_bss_end <= 0xf000, "RAM overlaps reserved 4 KiB stack")
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
