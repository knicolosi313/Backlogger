# Chrome Web Store artwork

| File | Use |
| --- | --- |
| `store-icon-128.png` | Store listing icon: transparent 128 x 128 PNG with 96 x 96 artwork. |
| `promo-small-440x280.png` | Small promotional tile: 440 x 280 PNG. |
| `promo-marquee-1400x560.png` | Optional marquee promotional banner: 1400 x 560 PNG. |
| `../../apps/extension/public/icon/*.png` | Packaged extension icons at 16, 32, 48, and 128 pixels. |

Upload the store files to their matching fields in the developer dashboard.
The extension icons are included in the manifest and production build.
Promotional images are illustrations, not screenshots. Capture actual extension
screenshots separately at 1280 x 800 or 640 x 400; do not submit the old design
mockups as if they show the implemented product.

## Screenshots ready to upload

The `screenshots/` directory contains five **1280 x 800, 24-bit RGB PNGs without
alpha**: recording, drafts, story details, analytics, and settings. Each shows
the actual extension React components with synthetic demonstration data, with
an explanatory caption beside the interface. No real account or recording is
used. The screenshot harness is separate from the shipped extension; it mocks
browser storage and API responses and blocks external API calls.

Regenerate all five from the project root after installing extension packages:

```powershell
node scripts/store-screenshots/capture.mjs
```

This uses a separate headless Chrome profile under `tmp/store-screenshots`,
not your signed-in browser. It requires Chrome at its standard Windows install
path. The script builds the actual UI with Vite, captures each view, then checks
dimensions and converts PNG encoding to RGB without alpha. Visually inspect
regenerated captures before uploading. Demo metrics are illustrative and are
identified as demonstration data in the screenshots.

The artwork follows Backlogger's existing dark navy/violet palette and layers
mark. The icon exports reuse the code-based logo; the promotional illustrations
were generated with the built-in imagegen tool. Generated source images and
their prompts are retained in `sources/` and `PROMPTS.md`. Final store PNGs are
downsampled to exact upload dimensions without changing their design.

Re-export icons from the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-extension-icons.ps1
```

Export a promotional source at its upload size:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/export-store-image.ps1 -Source docs/store-assets/sources/promo-small.png -Destination docs/store-assets/promo-small-440x280.png -Width 440 -Height 280
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/export-store-image.ps1 -Source docs/store-assets/sources/promo-marquee.png -Destination docs/store-assets/promo-marquee-1400x560.png -Width 1400 -Height 560
```

Dimension reference: [Chrome Web Store listing images](https://developer.chrome.com/docs/webstore/images).
