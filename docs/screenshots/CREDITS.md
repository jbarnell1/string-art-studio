# Screenshot credits

Every image in this folder, and `docs/social-preview.png`, was made on 2026-11-14 from a throwaway
local instance of String Art Studio 0.3.9 (Docker Compose on `127.0.0.1`, CPU only, Piper voice
profile on) that was deleted afterwards. Nothing comes from a real household install.

## Source photos

The demo projects use the photos that ship in `samples/`. They are AI-generated pictures of
fictional people and a dog, made for this repository (see ADR-009 in `docs/DECISIONS.md`); no
real person appears in them. The project names on screen ("Alex", "Sam", "Biscuit") are invented.

| Demo project | Source | Settings |
|---|---|---|
| Alex, 24 inch portrait | `samples/portrait-man.jpg` | Defaults, framed with "Frame the face", engines A and B |
| Sam in four colours | `samples/portrait-color.jpg` | Defaults, framed, automatic palette of four colours, engine A |
| Biscuit the spaniel | `samples/dog.jpg` | Defaults, engine A (in the project library only, not pictured) |

## Files

| File | What it shows | How it was made |
|---|---|---|
| `hero.png` | Results page (target, engine A, engine B) and build mode on a phone | Two headless Chromium captures (1440x900 and 390x844 at 2x) placed side by side with Pillow |
| `editor.png` | The editor with "Show importance" on | Headless Chromium, 1440x900 at 2x |
| `results-colour.png` | A four-colour plan beside its target | Headless Chromium, 1440x900 at 2x |
| `estimate-exports.png` | Build estimate and export panels | Headless Chromium, 1440x900 at 2x |
| `template-and-jig.png` | Files exported by the demo plan | Pages of the A4 and single-sheet template PDFs rendered with pdfium, and `guide_A.stl` from the jig ZIP rendered with a small z-buffer script; laid out with Pillow |
| `phone-build.png` | Build mode part way through the colour plan | Headless Chromium, 390x844 at 2x |
| `../social-preview.png` | Repository social preview, 1280x640 | `docs/brand/mark.png`, the sample photo and engine A's render, laid out with Pillow |

Captures were scaled down and reduced to 256 colours or fewer to keep each file small. Caption
text in the composed images is set in Segoe UI; the app itself uses IBM Plex Sans.
