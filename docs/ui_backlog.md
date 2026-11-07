# UI Backlog

## Battery settings

- Add a master Settings screen for module battery configuration.
- Include pack type selection, starting with 2S and future higher-voltage packs.
- Include ADC divider calibration so displayed module voltage can match a meter.
- Keep calibration per module type or firmware target so dummy and field builds can use different hardware safely.

## Refactor follow-up order

1. Module state model
2. Script-fire UI state
3. Shared module firmware helpers (done: `module_link.py`, `module_script.py`, `module_lcd.py`)
4. Bench regression scripts
