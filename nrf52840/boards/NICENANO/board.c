#include "nrf.h"

#define DFU_MAGIC_UF2_RESET (0x57)

void NICENANO_board_enter_bootloader(void) {
    __disable_irq();
    NRF_POWER->GPREGRET = DFU_MAGIC_UF2_RESET;
    NVIC_SystemReset();
}
