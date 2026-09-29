# Diagnostics

This folder is for focused debug probes that are useful while troubleshooting
hardware or radio behavior, but are not part of the everyday upload/build flow.

Run these from the repository root so their imports resolve correctly, for
example:

```powershell
python .\scripts\diagnostics\test_master_discover_noinfo.py --master COM21
```

Keep stable regression tests in `scripts/` using the `test_*.py` naming pattern.
