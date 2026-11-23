# Nightfire Simulator branding

Option 2, the integrated lime-green `SIMULATOR` badge, is the selected identity.

The `final` directory contains:

- the original square logo master;
- reusable PNG icon sizes from 16 through 1024 pixels;
- a multi-resolution Windows `.ico` file;
- the original 16:9 splash master;
- 1280×720, 1920×1080, and 2560×1440 splash images.

Regenerate the package with:

```powershell
./scripts/generate_simulator_brand_assets.ps1 `
  -IconSource <selected-logo.png> `
  -SplashSource <splash-master.png>
```
