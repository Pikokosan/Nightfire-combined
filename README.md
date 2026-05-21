# Nightfire Combined

This project keeps one shared NFCP protocol implementation:

- `main/nfcp.py`

Device-specific entrypoints live beside it:

- `main/master_main.py`
- `main/slave_main.py`
- `main/dummy_main.py`

Before uploading to a device, use the matching device entrypoint as `main.py`.
The device-specific files import the same shared `nfcp.py`, so packet framing,
CRC, escaping, commands, FIRE masks, and continuity response parsing stay in
one place.

`nfcp_core.py` has been removed. The master-only UART, database, discovery,
continuity scan, arm/disarm, fire, and set-group helpers now live in
`master_main.py`.
