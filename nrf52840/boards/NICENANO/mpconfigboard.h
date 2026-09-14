/*
 * Local nice!nano test board profile for Nightfire nRF52840 prototypes.
 */

#define MICROPY_HW_BOARD_NAME       "nice!nano"
#define MICROPY_HW_MCU_NAME         "NRF52840"

#define MICROPY_BOARD_ENTER_BOOTLOADER(nargs, args) NICENANO_board_enter_bootloader()

#define MICROPY_HW_ENABLE_USBDEV    (1)
#define MICROPY_HW_USB_CDC          (1)

#define MICROPY_PY_MACHINE_UART     (1)
#define MICROPY_PY_MACHINE_HW_PWM   (1)
#define MICROPY_PY_MACHINE_RTCOUNTER (1)
#define MICROPY_PY_MACHINE_I2C      (1)
#define MICROPY_PY_MACHINE_ADC      (1)
#define MICROPY_PY_MACHINE_TEMP     (1)

#define MICROPY_HW_ENABLE_RNG       (1)

#define MICROPY_HW_HAS_LED          (1)
#define MICROPY_HW_LED_COUNT        (1)
#define MICROPY_HW_LED_PULLUP       (0)
#define MICROPY_HW_LED1             (15)

// Keep the REPL on USB CDC only. Enabling UART REPL at the same time can make
// the nRF USB CDC console stop responding on this port.
#define MICROPY_HW_ENABLE_UART_REPL (0)

// UART config is still available for application use.
#define MICROPY_HW_UART1_RX         (8)
#define MICROPY_HW_UART1_TX         (6)
#define MICROPY_HW_UART1_HWFC       (0)

#define MICROPY_HW_SPI0_NAME        "SPI0"
#define MICROPY_HW_SPI0_SCK         (47)
#define MICROPY_HW_SPI0_MOSI        (45)
#define MICROPY_HW_SPI0_MISO        (46)

#define MICROPY_HW_PWM0_NAME        "PWM0"
#define MICROPY_HW_PWM1_NAME        "PWM1"
#define MICROPY_HW_PWM2_NAME        "PWM2"

#define HELP_TEXT_BOARD_LED         "1"

// Match the nice!nano UF2 bootloader USB identity.
#define MICROPY_HW_USB_VID          (0x239A)
#define MICROPY_HW_USB_PID          (0x00B3)
#define MICROPY_HW_USB_CDC_1200BPS_TOUCH (1)

void NICENANO_board_enter_bootloader(void);
