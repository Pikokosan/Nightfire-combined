#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/mphal.h"

#if defined(NRF52840_XXAA) || defined(NRF52840_XXAB) || defined(NRF52840)
#include "nrf.h"
#else
#include "nrf.h"
#endif

#define NRF_RADIO_MAX_PAYLOAD 252
#define NRF_RADIO_DEFAULT_CHANNEL 40
#define NRF_RADIO_DEFAULT_ADDRESS 0x67676767u

#define NRF_RADIO_DEFAULT_SHORTS (RADIO_SHORTS_READY_START_Msk | RADIO_SHORTS_END_DISABLE_Msk)
// While receiving, the radio itself starts an RSSI sample when a packet's
// address matches and stops sampling when the radio disables, so the value
// belongs to that packet. (Starting it in software right after READY measured
// the empty channel before the packet arrived: always the noise floor.)
#define NRF_RADIO_RX_SHORTS (NRF_RADIO_DEFAULT_SHORTS | RADIO_SHORTS_ADDRESS_RSSISTART_Msk | \
    RADIO_SHORTS_DISABLED_RSSISTOP_Msk)

// Background receive
// ------------------
// After init() the radio listens all the time, not only inside recv(): it is
// put back into RX after every recv(), send(), channel() and noise_sample().
// Each packet ends with END -> DISABLE; the RADIO interrupt (on DISABLED)
// keeps a good packet in a small ring and re-arms RX into the next free slot
// (about 40 us with fast ramp-up). recv() hands out held packets oldest
// first and only waits when the ring is empty.
//
// The ring has NRF_RADIO_RX_SLOTS slots: the one the radio is filling plus up
// to NRF_RADIO_RX_SLOTS - 1 held packets. When all are held, a new packet is
// dropped (counted in rx_dropped) and the slot is reused.
//
// The interrupt is the only writer of rx_write, recv() the only writer of
// rx_read. Everything else that touches the radio stops listening first
// (rx_stop(): RADIO interrupt off, an in-flight packet finishes and is kept)
// and starts it again afterwards (rx_start()).
#define NRF_RADIO_RX_SLOTS 5
#define NRF_RADIO_IRQ_PRIORITY 2
#define NRF_RADIO_NO_RSSI (-32768)
// A packet whose address has been heard needs at most (1 + 252 + 2) bytes of
// 8 us each to finish. Stopping RX waits this long for it instead of cutting
// it off.
#define NRF_RADIO_RX_FINISH_US 2600

static uint8_t tx_packet[NRF_RADIO_MAX_PAYLOAD + 1];
static uint8_t rx_slots[NRF_RADIO_RX_SLOTS][NRF_RADIO_MAX_PAYLOAD + 1] __attribute__((aligned(4)));
static int16_t rx_slot_rssi[NRF_RADIO_RX_SLOTS];
// Scratch receive buffer for noise_sample(): what lands there is never kept.
static uint8_t scratch_packet[NRF_RADIO_MAX_PAYLOAD + 1];
static volatile uint8_t rx_write = 0;
static volatile uint8_t rx_read = 0;
static volatile bool rx_listening = false;
// RSSI of the last packet recv() returned. Channel checks (CCA, noise_sample)
// keep their samples to themselves.
static int last_rssi_dbm = 0;
static uint32_t current_channel = NRF_RADIO_DEFAULT_CHANNEL;
static int32_t current_tx_power = 8;
static uint32_t current_address = NRF_RADIO_DEFAULT_ADDRESS;
static uint32_t stat_tx_ok = 0;
static uint32_t stat_tx_timeout = 0;
static uint32_t stat_rx_ok = 0;
static uint32_t stat_rx_timeout = 0;
static volatile uint32_t stat_rx_crc_error = 0;
static volatile uint32_t stat_rx_bad_len = 0;
static volatile uint32_t stat_rx_dropped = 0;
static uint32_t stat_tx_busy = 0;
static uint32_t stat_cca_ok = 0;
static uint32_t stat_cca_busy = 0;

// Spins until *event is set or about `guard` polls have passed.
static bool wait_event(volatile uint32_t *event, uint32_t guard) {
    while (!*event) {
        if (guard-- == 0) {
            return false;
        }
    }
    return true;
}

static void radio_disable(void) {
    NRF_RADIO->TASKS_DISABLE = 1;
    wait_event(&NRF_RADIO->EVENTS_DISABLED, 1000000);
    NRF_RADIO->EVENTS_DISABLED = 0;
}

static void radio_config(uint32_t channel, int32_t tx_power, uint32_t address) {
    radio_disable();

    if (channel > 100) {
        channel = NRF_RADIO_DEFAULT_CHANNEL;
    }

    current_channel = channel;
    current_tx_power = tx_power;
    current_address = address;

    NRF_RADIO->POWER = 1;
    NRF_RADIO->MODE = RADIO_MODE_MODE_Nrf_1Mbit << RADIO_MODE_MODE_Pos;
    NRF_RADIO->FREQUENCY = channel;

    switch (tx_power) {
        case 8:
            NRF_RADIO->TXPOWER = RADIO_TXPOWER_TXPOWER_Pos8dBm << RADIO_TXPOWER_TXPOWER_Pos;
            break;
        case 4:
            NRF_RADIO->TXPOWER = RADIO_TXPOWER_TXPOWER_Pos4dBm << RADIO_TXPOWER_TXPOWER_Pos;
            break;
        case 0:
            NRF_RADIO->TXPOWER = RADIO_TXPOWER_TXPOWER_0dBm << RADIO_TXPOWER_TXPOWER_Pos;
            break;
        case -4:
            NRF_RADIO->TXPOWER = RADIO_TXPOWER_TXPOWER_Neg4dBm << RADIO_TXPOWER_TXPOWER_Pos;
            break;
        default:
            NRF_RADIO->TXPOWER = RADIO_TXPOWER_TXPOWER_Pos8dBm << RADIO_TXPOWER_TXPOWER_Pos;
            break;
    }

    NRF_RADIO->PREFIX0 = (address >> 24) & 0xFF;
    NRF_RADIO->BASE0 = address << 8;
    NRF_RADIO->TXADDRESS = 0;
    NRF_RADIO->RXADDRESSES = 1;

    NRF_RADIO->PCNF0 =
        (8 << RADIO_PCNF0_LFLEN_Pos) |
        (0 << RADIO_PCNF0_S0LEN_Pos) |
        (0 << RADIO_PCNF0_S1LEN_Pos);

    NRF_RADIO->PCNF1 =
        (NRF_RADIO_MAX_PAYLOAD << RADIO_PCNF1_MAXLEN_Pos) |
        (0 << RADIO_PCNF1_STATLEN_Pos) |
        (3 << RADIO_PCNF1_BALEN_Pos) |
        (RADIO_PCNF1_ENDIAN_Little << RADIO_PCNF1_ENDIAN_Pos) |
        (RADIO_PCNF1_WHITEEN_Enabled << RADIO_PCNF1_WHITEEN_Pos);

    NRF_RADIO->DATAWHITEIV = channel & 0x3F;
    NRF_RADIO->CRCCNF =
        (RADIO_CRCCNF_LEN_Two << RADIO_CRCCNF_LEN_Pos) |
        (RADIO_CRCCNF_SKIPADDR_Skip << RADIO_CRCCNF_SKIPADDR_Pos);
    NRF_RADIO->CRCINIT = 0xFFFF;
    NRF_RADIO->CRCPOLY = 0x11021;

    NRF_RADIO->MODECNF0 = RADIO_MODECNF0_RU_Fast << RADIO_MODECNF0_RU_Pos;
    NRF_RADIO->SHORTS = NRF_RADIO_DEFAULT_SHORTS;
}

// Keeps the packet that just ended (if any) in the ring. Runs in the RADIO
// interrupt, or with that interrupt off, after DISABLED.
static void rx_take_packet(void) {
    if (!NRF_RADIO->EVENTS_END) {
        return;
    }
    uint8_t slot = rx_write;
    if (!NRF_RADIO->CRCSTATUS) {
        stat_rx_crc_error++;
        return;
    }
    if (rx_slots[slot][0] > NRF_RADIO_MAX_PAYLOAD) {
        stat_rx_bad_len++;
        return;
    }
    rx_slot_rssi[slot] = NRF_RADIO->EVENTS_RSSIEND ? -(int16_t)NRF_RADIO->RSSISAMPLE : NRF_RADIO_NO_RSSI;
    uint8_t next = (uint8_t)((slot + 1) % NRF_RADIO_RX_SLOTS);
    if (next == rx_read) {
        stat_rx_dropped++;
        return;
    }
    __DMB();
    rx_write = next;
}

// Starts receiving into the free slot. The radio must be DISABLED.
static void rx_arm(void) {
    NRF_RADIO->EVENTS_READY = 0;
    NRF_RADIO->EVENTS_ADDRESS = 0;
    NRF_RADIO->EVENTS_END = 0;
    NRF_RADIO->EVENTS_RSSIEND = 0;
    NRF_RADIO->EVENTS_DISABLED = 0;
    (void)NRF_RADIO->EVENTS_DISABLED;
    NRF_RADIO->PACKETPTR = (uint32_t)rx_slots[rx_write];
    NRF_RADIO->TASKS_RXEN = 1;
}

void RADIO_IRQHandler(void) {
    if (!NRF_RADIO->EVENTS_DISABLED) {
        return;
    }
    if (rx_listening) {
        rx_take_packet();
        rx_arm();
    } else {
        NRF_RADIO->EVENTS_DISABLED = 0;
        (void)NRF_RADIO->EVENTS_DISABLED;
    }
}

static void rx_start(void) {
    if (rx_listening) {
        return;
    }
    if ((NRF_RADIO->STATE & RADIO_STATE_STATE_Msk) != RADIO_STATE_STATE_Disabled) {
        radio_disable();
    }
    NRF_RADIO->SHORTS = NRF_RADIO_RX_SHORTS;
    NRF_RADIO->INTENCLR = 0xFFFFFFFFu;
    NRF_RADIO->INTENSET = RADIO_INTENSET_DISABLED_Msk;
    NVIC_SetPriority(RADIO_IRQn, NRF_RADIO_IRQ_PRIORITY);
    NVIC_ClearPendingIRQ(RADIO_IRQn);
    rx_listening = true;
    rx_arm();
    NVIC_EnableIRQ(RADIO_IRQn);
}

// Stops listening. A packet that is on the air finishes first, and a
// finished packet the interrupt has not handled yet is kept.
static void rx_stop(void) {
    if (!rx_listening) {
        return;
    }
    NVIC_DisableIRQ(RADIO_IRQn);
    if (NRF_RADIO->EVENTS_ADDRESS && !NRF_RADIO->EVENTS_END) {
        // One poll takes at least one 16 MHz peripheral read (62.5 ns), so
        // this bound is at least NRF_RADIO_RX_FINISH_US. (Not ticks_us: its
        // 30 us RTC steps and IRQ dependence are not needed here.)
        wait_event(&NRF_RADIO->EVENTS_END, NRF_RADIO_RX_FINISH_US * 16u);
    }
    rx_listening = false;
    radio_disable();
    rx_take_packet();
    NRF_RADIO->INTENCLR = 0xFFFFFFFFu;
    NRF_RADIO->EVENTS_ADDRESS = 0;
    NRF_RADIO->EVENTS_END = 0;
    NRF_RADIO->EVENTS_RSSIEND = 0;
    NRF_RADIO->EVENTS_READY = 0;
    NRF_RADIO->SHORTS = NRF_RADIO_DEFAULT_SHORTS;
    NVIC_ClearPendingIRQ(RADIO_IRQn);
}

// Drops held packets. Call with listening stopped.
static void rx_clear(void) {
    rx_read = rx_write;
}

// Clear-channel check while listening: the receiver that listens for
// packets also samples the channel. A packet on the air counts as busy.
static bool channel_is_clear(uint32_t cca_ms, int32_t busy_dbm) {
    if (cca_ms == 0) {
        return true;
    }

    rx_start();
    uint32_t start = mp_hal_ticks_ms();
    while ((mp_hal_ticks_ms() - start) < cca_ms) {
        if (NRF_RADIO->EVENTS_ADDRESS && !NRF_RADIO->EVENTS_END) {
            stat_cca_busy++;
            return false;
        }
        if ((NRF_RADIO->STATE & RADIO_STATE_STATE_Msk) != RADIO_STATE_STATE_Rx) {
            // Re-arming after a packet: about 40 us.
            continue;
        }
        NRF_RADIO->EVENTS_RSSIEND = 0;
        NRF_RADIO->TASKS_RSSISTART = 1;
        if (!wait_event(&NRF_RADIO->EVENTS_RSSIEND, 10000)) {
            continue;
        }

        int32_t dbm = -(int32_t)NRF_RADIO->RSSISAMPLE;
        if (dbm >= busy_dbm) {
            stat_cca_busy++;
            return false;
        }
    }

    stat_cca_ok++;
    return true;
}

static mp_obj_t nrf_radio_init(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum {
        ARG_channel,
        ARG_tx_power,
        ARG_address,
    };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_channel, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = NRF_RADIO_DEFAULT_CHANNEL} },
        { MP_QSTR_tx_power, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 8} },
        { MP_QSTR_address, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = NRF_RADIO_DEFAULT_ADDRESS} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    rx_stop();
    radio_config(
        (uint32_t)args[ARG_channel].u_int,
        (int32_t)args[ARG_tx_power].u_int,
        (uint32_t)args[ARG_address].u_int
    );
    rx_clear();
    rx_start();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(nrf_radio_init_obj, 0, nrf_radio_init);

static mp_obj_t nrf_radio_send(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum {
        ARG_payload,
        ARG_cca_ms,
        ARG_busy_dbm,
        ARG_backoff_ms,
    };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_payload, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_cca_ms, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 0} },
        { MP_QSTR_busy_dbm, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = -85} },
        { MP_QSTR_backoff_ms, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 0} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    mp_buffer_info_t payload;
    mp_get_buffer_raise(args[ARG_payload].u_obj, &payload, MP_BUFFER_READ);
    if (payload.len > NRF_RADIO_MAX_PAYLOAD) {
        mp_raise_ValueError(MP_ERROR_TEXT("payload too long"));
    }

    uint32_t cca_ms = (uint32_t)args[ARG_cca_ms].u_int;
    int32_t busy_dbm = (int32_t)args[ARG_busy_dbm].u_int;
    uint32_t backoff_ms = (uint32_t)args[ARG_backoff_ms].u_int;

    if (!channel_is_clear(cca_ms, busy_dbm)) {
        stat_tx_busy++;
        if (backoff_ms > 0) {
            mp_hal_delay_ms((mp_hal_ticks_us() % backoff_ms) + 1);
        }
        return mp_const_false;
    }

    rx_stop();
    tx_packet[0] = payload.len;
    memcpy(&tx_packet[1], payload.buf, payload.len);
    NRF_RADIO->PACKETPTR = (uint32_t)tx_packet;
    NRF_RADIO->EVENTS_READY = 0;
    NRF_RADIO->EVENTS_END = 0;
    NRF_RADIO->EVENTS_DISABLED = 0;
    NRF_RADIO->TASKS_TXEN = 1;

    if (!wait_event(&NRF_RADIO->EVENTS_DISABLED, 1000000)) {
        radio_disable();
        stat_tx_timeout++;
        rx_start();
        mp_raise_OSError(MP_ETIMEDOUT);
    }
    NRF_RADIO->EVENTS_DISABLED = 0;
    NRF_RADIO->EVENTS_END = 0;
    stat_tx_ok++;
    rx_start();
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(nrf_radio_send_obj, 1, nrf_radio_send);

// Returns the oldest held packet, or waits up to timeout ms (0: forever) for
// one. CRC errors no longer end the wait early; they are counted and the
// radio keeps listening.
static mp_obj_t nrf_radio_recv(size_t n_args, const mp_obj_t *args) {
    uint32_t timeout_ms = 0;
    if (n_args > 0) {
        timeout_ms = mp_obj_get_int(args[0]);
    }

    rx_start();
    uint32_t start = mp_hal_ticks_ms();
    for (;;) {
        uint8_t slot = rx_read;
        if (slot != rx_write) {
            __DMB();
            mp_obj_t packet = mp_obj_new_bytes(&rx_slots[slot][1], rx_slots[slot][0]);
            if (rx_slot_rssi[slot] != NRF_RADIO_NO_RSSI) {
                last_rssi_dbm = rx_slot_rssi[slot];
            }
            __DMB();
            rx_read = (uint8_t)((slot + 1) % NRF_RADIO_RX_SLOTS);
            stat_rx_ok++;
            return packet;
        }
        if (timeout_ms && mp_hal_ticks_ms() - start >= timeout_ms) {
            stat_rx_timeout++;
            return mp_const_none;
        }
    }
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(nrf_radio_recv_obj, 0, 1, nrf_radio_recv);

static mp_obj_t nrf_radio_last_rssi(void) {
    return mp_obj_new_int(last_rssi_dbm);
}
static MP_DEFINE_CONST_FUN_OBJ_0(nrf_radio_last_rssi_obj, nrf_radio_last_rssi);

static mp_obj_t nrf_radio_stats(void) {
    mp_obj_t result = mp_obj_new_dict(16);
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_tx_ok), mp_obj_new_int_from_uint(stat_tx_ok));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_tx_timeout), mp_obj_new_int_from_uint(stat_tx_timeout));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_tx_busy), mp_obj_new_int_from_uint(stat_tx_busy));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_rx_ok), mp_obj_new_int_from_uint(stat_rx_ok));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_rx_timeout), mp_obj_new_int_from_uint(stat_rx_timeout));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_rx_crc_error), mp_obj_new_int_from_uint(stat_rx_crc_error));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_rx_bad_len), mp_obj_new_int_from_uint(stat_rx_bad_len));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_rx_dropped), mp_obj_new_int_from_uint(stat_rx_dropped));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_rx_held), mp_obj_new_int(
        (rx_write + NRF_RADIO_RX_SLOTS - rx_read) % NRF_RADIO_RX_SLOTS));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_cca_ok), mp_obj_new_int_from_uint(stat_cca_ok));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_cca_busy), mp_obj_new_int_from_uint(stat_cca_busy));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_last_rssi), mp_obj_new_int(last_rssi_dbm));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_channel), mp_obj_new_int_from_uint(current_channel));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_tx_power), mp_obj_new_int(current_tx_power));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_address), mp_obj_new_int_from_uint(current_address));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_max_payload), mp_obj_new_int(NRF_RADIO_MAX_PAYLOAD));
    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_0(nrf_radio_stats_obj, nrf_radio_stats);

static mp_obj_t nrf_radio_clear_stats(void) {
    stat_tx_ok = 0;
    stat_tx_timeout = 0;
    stat_rx_ok = 0;
    stat_rx_timeout = 0;
    stat_rx_crc_error = 0;
    stat_rx_bad_len = 0;
    stat_rx_dropped = 0;
    stat_tx_busy = 0;
    stat_cca_ok = 0;
    stat_cca_busy = 0;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(nrf_radio_clear_stats_obj, nrf_radio_clear_stats);

static mp_obj_t nrf_radio_channel(mp_obj_t channel_obj) {
    uint32_t channel = mp_obj_get_int(channel_obj);
    if (channel > 100) {
        mp_raise_ValueError(MP_ERROR_TEXT("channel must be 0..100"));
    }
    bool was_listening = rx_listening;
    rx_stop();
    rx_clear();
    current_channel = channel;
    NRF_RADIO->FREQUENCY = channel;
    NRF_RADIO->DATAWHITEIV = channel & 0x3F;
    if (was_listening) {
        rx_start();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(nrf_radio_channel_obj, nrf_radio_channel);


static mp_obj_t nrf_radio_device_id32(void) {
    uint32_t id0 = NRF_FICR->DEVICEID[0];
    uint32_t id1 = NRF_FICR->DEVICEID[1];

    if (id0 != 0 && id0 != 0xFFFFFFFFu) {
        return mp_obj_new_int_from_uint(id0);
    }

    return mp_obj_new_int_from_uint(id0 ^ id1);
}
static MP_DEFINE_CONST_FUN_OBJ_0(nrf_radio_device_id32_obj, nrf_radio_device_id32);

static mp_obj_t nrf_radio_device_id(void) {
    mp_obj_t tuple[2] = {
        mp_obj_new_int_from_uint(NRF_FICR->DEVICEID[0]),
        mp_obj_new_int_from_uint(NRF_FICR->DEVICEID[1]),
    };
    return mp_obj_new_tuple(2, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(nrf_radio_device_id_obj, nrf_radio_device_id);

static mp_obj_t nrf_radio_noise_sample(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum {
        ARG_channel,
        ARG_duration_ms,
        ARG_interval_ms,
        ARG_busy_dbm,
    };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_channel, MP_ARG_REQUIRED | MP_ARG_INT, {.u_int = NRF_RADIO_DEFAULT_CHANNEL} },
        { MP_QSTR_duration_ms, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 100} },
        { MP_QSTR_interval_ms, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 2} },
        { MP_QSTR_busy_dbm, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = -85} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    uint32_t sample_channel = (uint32_t)args[ARG_channel].u_int;
    uint32_t duration_ms = (uint32_t)args[ARG_duration_ms].u_int;
    uint32_t interval_ms = (uint32_t)args[ARG_interval_ms].u_int;
    int32_t busy_dbm = (int32_t)args[ARG_busy_dbm].u_int;

    if (sample_channel > 100) {
        mp_raise_ValueError(MP_ERROR_TEXT("channel must be 0..100"));
    }
    if (duration_ms < 1) {
        duration_ms = 1;
    }
    if (interval_ms < 1) {
        interval_ms = 1;
    }

    uint32_t previous_channel = current_channel;
    int32_t previous_tx_power = current_tx_power;
    uint32_t previous_address = current_address;

    // Held packets stay held; listening resumes on the previous channel.
    bool was_listening = rx_listening;
    rx_stop();
    radio_config(sample_channel, current_tx_power, current_address);
    // END -> START: a packet on the sampled channel does not stop the receiver
    // (it lands in the scratch buffer and is thrown away).
    NRF_RADIO->SHORTS = RADIO_SHORTS_READY_START_Msk | RADIO_SHORTS_END_START_Msk;
    NRF_RADIO->PACKETPTR = (uint32_t)scratch_packet;
    NRF_RADIO->EVENTS_READY = 0;
    NRF_RADIO->EVENTS_RSSIEND = 0;
    NRF_RADIO->EVENTS_DISABLED = 0;
    NRF_RADIO->TASKS_RXEN = 1;

    if (!wait_event(&NRF_RADIO->EVENTS_READY, 1000000)) {
        radio_disable();
        radio_config(previous_channel, previous_tx_power, previous_address);
        if (was_listening) {
            rx_start();
        }
        mp_raise_OSError(MP_ETIMEDOUT);
    }

    int min_dbm = 0;
    int max_dbm = 0;
    int sum_dbm = 0;
    int count = 0;
    int busy_count = 0;

    uint32_t start = mp_hal_ticks_ms();
    while ((mp_hal_ticks_ms() - start) < duration_ms || count == 0) {
        NRF_RADIO->EVENTS_RSSIEND = 0;
        NRF_RADIO->TASKS_RSSISTART = 1;

        if (!wait_event(&NRF_RADIO->EVENTS_RSSIEND, 1000000)) {
            break;
        }

        int dbm = -(int)NRF_RADIO->RSSISAMPLE;
        if (count == 0 || dbm < min_dbm) {
            min_dbm = dbm;
        }
        if (count == 0 || dbm > max_dbm) {
            max_dbm = dbm;
        }
        sum_dbm += dbm;
        count++;
        if (dbm >= busy_dbm) {
            busy_count++;
        }

        mp_hal_delay_ms(interval_ms);
    }

    radio_disable();
    radio_config(previous_channel, previous_tx_power, previous_address);
    if (was_listening) {
        rx_start();
    }

    mp_obj_t result = mp_obj_new_dict(6);
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_channel), mp_obj_new_int(sample_channel));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_count), mp_obj_new_int(count));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_avg), mp_obj_new_int(count ? (sum_dbm / count) : 0));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_min), mp_obj_new_int(count ? min_dbm : 0));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_max), mp_obj_new_int(count ? max_dbm : 0));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_busy_count), mp_obj_new_int(busy_count));
    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(nrf_radio_noise_sample_obj, 1, nrf_radio_noise_sample);

static mp_obj_t nrf_radio_write_ws2811(mp_obj_t pin_obj, mp_obj_t data_obj, mp_obj_t timing_obj) {
    mp_int_t pin = mp_obj_get_int(pin_obj);
    if (pin < 0 || pin > 47) {
        mp_raise_ValueError(MP_ERROR_TEXT("GPIO must be 0..47"));
    }
    mp_buffer_info_t data;
    mp_get_buffer_raise(data_obj, &data, MP_BUFFER_READ);
    if (!data.len || data.len > 48 || data.len % 3) {
        mp_raise_ValueError(MP_ERROR_TEXT("expected 1..16 RGB pixels"));
    }
    mp_obj_t *timing;
    mp_obj_get_array_fixed_n(timing_obj, 4, &timing);
    uint32_t cycles[4];
    for (size_t index = 0; index < 4; index++) {
        mp_int_t ns = mp_obj_get_int(timing[index]);
        if (ns < 100 || ns > 5000) {
            mp_raise_ValueError(MP_ERROR_TEXT("timings must be 100..5000 ns"));
        }
        cycles[index] = ((uint32_t)ns * (SystemCoreClock / 1000000u) + 999u) / 1000u;
    }
    NRF_GPIO_Type *gpio = pin < 32 ? NRF_P0 : NRF_P1;
    uint32_t mask = 1u << (pin & 31);
    gpio->OUTCLR = mask;
    gpio->DIRSET = mask;
    uint32_t previous_trace = CoreDebug->DEMCR;
    uint32_t previous_counter = DWT->CTRL;
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    mp_uint_t atomic_state = MICROPY_BEGIN_ATOMIC_SECTION();
    const uint8_t *bytes = data.buf;
    for (size_t index = 0; index < data.len; index++) {
        for (uint32_t bit = 0x80; bit; bit >>= 1) {
            size_t slot = (bytes[index] & bit) ? 2 : 0;
            uint32_t started = DWT->CYCCNT;
            gpio->OUTSET = mask;
            while ((uint32_t)(DWT->CYCCNT - started) < cycles[slot]) {
            }
            gpio->OUTCLR = mask;
            while ((uint32_t)(DWT->CYCCNT - started) < cycles[slot] + cycles[slot + 1]) {
            }
        }
    }
    MICROPY_END_ATOMIC_SECTION(atomic_state);
    DWT->CTRL = previous_counter;
    CoreDebug->DEMCR = previous_trace;
    mp_hal_delay_us(300);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(nrf_radio_write_ws2811_obj, nrf_radio_write_ws2811);

static const mp_rom_map_elem_t nrf_radio_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_nrf_radio) },
    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&nrf_radio_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_send), MP_ROM_PTR(&nrf_radio_send_obj) },
    { MP_ROM_QSTR(MP_QSTR_recv), MP_ROM_PTR(&nrf_radio_recv_obj) },
    { MP_ROM_QSTR(MP_QSTR_last_rssi), MP_ROM_PTR(&nrf_radio_last_rssi_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&nrf_radio_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_clear_stats), MP_ROM_PTR(&nrf_radio_clear_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_channel), MP_ROM_PTR(&nrf_radio_channel_obj) },
    { MP_ROM_QSTR(MP_QSTR_device_id32), MP_ROM_PTR(&nrf_radio_device_id32_obj) },
    { MP_ROM_QSTR(MP_QSTR_device_id), MP_ROM_PTR(&nrf_radio_device_id_obj) },
    { MP_ROM_QSTR(MP_QSTR_noise_sample), MP_ROM_PTR(&nrf_radio_noise_sample_obj) },
    { MP_ROM_QSTR(MP_QSTR_write_ws2811), MP_ROM_PTR(&nrf_radio_write_ws2811_obj) },
};
static MP_DEFINE_CONST_DICT(nrf_radio_module_globals, nrf_radio_module_globals_table);

const mp_obj_module_t nrf_radio_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&nrf_radio_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_nrf_radio, nrf_radio_user_cmodule);
