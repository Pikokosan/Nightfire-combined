#include <stdbool.h>

#include "nrf.h"
#include "nrf52840_bitfields.h"

#define DFU_MAGIC_UF2_RESET (0x57)

static bool NICENANO_uicr_regout0_needs_3v0(void) {
    uint32_t regout0 = NRF_UICR->REGOUT0;
    uint32_t vout = (regout0 & UICR_REGOUT0_VOUT_Msk) >> UICR_REGOUT0_VOUT_Pos;

    return (
        regout0 == 0xFFFFFFFFUL ||
        vout == UICR_REGOUT0_VOUT_DEFAULT
    );
}

void NICENANO_board_early_init(void) {
    if (!NICENANO_uicr_regout0_needs_3v0()) {
        return;
    }

    while (NRF_NVMC->READY != NVMC_READY_READY_Ready) {
    }

    NRF_NVMC->CONFIG = NVMC_CONFIG_WEN_Wen;
    while (NRF_NVMC->READY != NVMC_READY_READY_Ready) {
    }

    NRF_UICR->REGOUT0 =
        (UICR_REGOUT0_VOUT_3V0 & UICR_REGOUT0_VOUT_Msk) << UICR_REGOUT0_VOUT_Pos;
    while (NRF_NVMC->READY != NVMC_READY_READY_Ready) {
    }

    NRF_NVMC->CONFIG = NVMC_CONFIG_WEN_Ren;
    while (NRF_NVMC->READY != NVMC_READY_READY_Ready) {
    }

    NVIC_SystemReset();
}

void NICENANO_board_enter_bootloader(void) {
    __disable_irq();
    NRF_POWER->GPREGRET = DFU_MAGIC_UF2_RESET;
    NVIC_SystemReset();
}
