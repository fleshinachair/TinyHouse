# The pack's demo MIDI, played through Serum and through TinyHouse (2026-11-04)

`calibration/dev/midi_ab.py` plays each preset's own demo MIDI file (real notes, velocities, note lengths) through Serum with its whole chain and through the installed preset, both at a host tempo of 120 BPM. The paired WAVs are in `out/ab_midi/` (git-ignored: they are Serum's renders). The pack has demo MIDI for 22 of the 70 presets; Lynnic, Kisaya and Catching Flies are not among them.

Reading the table: `level` is engine minus Serum in rms dB; `centroid` is the spectral centroid (Serum / engine, Hz); the band columns are the engine's octave-band level minus Serum's (re each render's own total), 31 Hz to 8 kHz. Bands above 4 kHz sit 50 to 100 dB under the peak in most of these presets: do not chase them. The low bands (31, 62 Hz) are small absolute levels too, but a +15 to +25 dB error there is sub-bass the engine has and Serum does not.

| preset | notes | level dB | centroid Hz (Serum / engine) | 31 | 62 | 125 | 250 | 500 | 1k | 2k | 4k | 8k |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Bass - Black Out | 2 | +1.4 | 68 / 60 | +0 | -0 | -2 | -3 | -2 | +1 | +0 | -10 | -20 |
| Bass - BUNT | 4 | -0.0 | 103 / 97 | +0 | +0 | -2 | -0 | +2 | -2 | -4 | +7 | +35 |
| Bass - Gravity | 36 | -0.9 | 188 / 175 | +1 | -0 | +1 | -1 | -6 | -3 | +4 | +12 | +20 |
| Bass - Let It Go | 16 | -1.1 | 123 / 134 | -1 | +0 | +1 | +2 | -1 | -6 | -16 | -24 | -17 |
| Bass - Self Control | 16 | -1.1 | 60 / 72 | -1 | +2 | +3 | +3 | +2 | +10 | +23 | +29 | +35 |
| Keys - BUNT | 12 | +0.6 | 310 / 316 | -2 | +0 | +0 | -0 | +1 | +2 | +0 | -0 | -8 |
| Keys - Can't Find You | 45 | +1.2 | 203 / 246 | -0 | -4 | -1 | +3 | +4 | +2 | -4 | -4 | +7 |
| Keys - Loop | 8 | -2.3 | 254 / 314 | -1 | -0 | -0 | -0 | +1 | +2 | +4 | +5 | +6 |
| Keys - Nyday | 13 | +1.5 | 214 / 226 | -14 | -0 | -0 | +0 | +2 | +2 | +1 | +0 | +8 |
| Keys - Other Side | 128 | -0.2 | 331 / 350 | +6 | +0 | -0 | -0 | +1 | +3 | -4 | -11 | -10 |
| Lead - Can't Find You | 15 | -4.4 | 349 / 386 | +1 | +2 | -0 | -0 | +0 | +0 | +5 | +12 | +22 |
| Lead - I'll Be Yours | 40 | -0.8 | 480 / 561 | -9 | +1 | -1 | -0 | +0 | +1 | +3 | +4 | +4 |
| Lead - Let It Go | 9 | -4.2 | 367 / 910 | +20 | +28 | -0 | -1 | -1 | +4 | +10 | +17 | +20 |
| Lead - Loop | 4 | -2.3 | 375 / 399 | +23 | +16 | +10 | +0 | -4 | -4 | -8 | -4 | +10 |
| Lead - Nyday | 36 | +1.1 | 509 / 432 | +7 | -2 | +1 | +2 | -2 | +2 | +3 | +39 | +58 |
| Lead - Other Side | 4 | -6.9 | 478 / 525 | +5 | +8 | +13 | -0 | +1 | +2 | +3 | +4 | +1 |
| Lead - Rollin | 16 | -0.9 | 726 / 1230 | +9 | +21 | +17 | -1 | -5 | +1 | +8 | +7 | +5 |
| Lead - Self Control | 17 | -3.2 | 269 / 292 | +15 | +18 | -0 | +1 | +2 | +5 | +12 | +15 | -13 |
| Lead - Self Control Noise | 4 | +4.1 | 2646 / 2104 | +10 | +10 | +11 | +9 | +4 | -0 | -1 | -2 | -1 |
| Lead - Sleepless | 80 | -0.2 | 274 / 328 | -3 | -4 | +0 | -0 | +3 | +1 | -3 | +5 | +31 |
| Lead - Space & Time | 24 | +3.4 | 470 / 803 | -6 | -3 | -1 | -2 | +1 | +5 | +10 | +14 | +34 |
| Lead - Tonic Walter | 38 | +2.1 | 634 / 795 | -6 | -5 | -6 | -1 | +1 | +2 | +4 | -4 | -14 |

## What it shows
- **Close on real playing** (level within 1.5 dB and the bands up to 2 kHz within 6 dB): Bass - Black Out, Bass - BUNT, Keys - BUNT, Keys - Can't Find You, Lead - Sleepless.
- **Quieter in the engine by 3 dB or more:** Lead - Can't Find You (-4.4), Lead - Let It Go (-4.2), Lead - Other Side (-6.9), Lead - Self Control (-3.2). **Louder:** Lead - Self Control Noise (+4.1), Lead - Space & Time (+3.4).
- **Brightness off by more than 1.5x (centroid):** Lead - Let It Go (367 vs 910 Hz), Lead - Rollin (726 vs 1230 Hz), Lead - Space & Time (470 vs 803 Hz).
- **Sub-bass the engine has and Serum does not (31/62 Hz, +15 dB or more):** Lead - Let It Go, Lead - Loop, Lead - Rollin, Lead - Self Control.

These are the presets to look at first when working on the engine: the list comes from real playing, not from a held chord.
