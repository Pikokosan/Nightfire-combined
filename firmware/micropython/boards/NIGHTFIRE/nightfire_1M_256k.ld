/*
    Nightfire nRF52840 (nice!nano) layout.

    Same as the port's nrf52840_1M_256k.ld, but without its 256 KiB ROMFS
    partition (unused here; the flash driver accepts size 0). That space goes
    to the littlefs filesystem (FS_SIZE in mpconfigboard.mk), so a module can
    hold its running firmware and a verified new copy during an OTA update.

    Flash: bootloader head 4K | app 380K | filesystem 512K | bootloader 128K
*/

_flash_size = 1M;
_ram_size   = 256K;
_micropy_hw_romfs_part0_size = 0;

/* produce a link error if there is not this amount of RAM for these sections */
_stack_size = 8K;
_minimum_heap_size = 128K;
