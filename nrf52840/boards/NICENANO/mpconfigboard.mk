MCU_SERIES = m4
MCU_VARIANT = nrf52
MCU_SUB_VARIANT = nrf52840
SOFTDEV_VERSION = 6.1.1
LD_FILES += boards/nrf52840_1M_256k.ld

NRF_DEFINES += -DNRF52840_XXAA

MICROPY_VFS_LFS2 = 1

# 256 KiB littlefs (default 64 KiB) so over-the-air updates can hold the
# running field image and a verified new copy (~38 KiB each) at once.
# Changing this moves the filesystem: reflash wipes it, reinstall files after.
FS_SIZE = 0x40000
