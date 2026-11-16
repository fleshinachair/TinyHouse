# Working on TinyHouse together

Read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (the rule: translate from Serum's own settings, no per-preset fits) and [docs/ROADMAP.md](docs/ROADMAP.md) (what is left) first.
[docs/TRANSLATION_RULES.md](docs/TRANSLATION_RULES.md) holds every fact measured on Serum so far.

## Taking a piece of work
- Pick one roadmap row and open an issue saying so, so two people do not build the same thing. Rows touch mostly separate files: effects rack and buses (`fx.h`, `fxmap.py`),
  voice filters (`voice.h`), LFOs (`map_lfos` in `deephouse2move.py` and the engine), new packs (`import_pack.py`).
- `deephouse2move.py` and `schwung-tablor/src/dsp/fx.h` are shared by almost everything: keep changes there small and merge often.

## Branches and pull requests
- One branch per piece of work, merged to `main` by pull request. `main` always builds and passes the native tests.
- Small commits that do one thing. Put the measured evidence in the message (before and after numbers from the same tool).
- Keep the files' line endings: `fx.h` and several engine files are CRLF. Edit them in a way that preserves them (a binary-safe replace, not a text-mode rewrite).

## What a change must show
1. **The rule.** A new translation rule comes from a Serum probe (`calibration/probe_*.py`) and is written into `docs/TRANSLATION_RULES.md` with what was measured. A fix that helps one preset and
   does not transfer to a new pack is not a fix.
2. **The numbers.** Before and after on the same measurement: `calibration/dev/midi_ab.py` (the pack's demo MIDI), `calibration/dev/reverb_engine_only.py` (reverb tails), the stage and effect
   audits, or the chain report. Judge by many presets: random unison phases give about +-3 dB per preset.
3. **The tests.** `bash schwung-tablor/scripts/zig-build.sh native`, then run `schwung-tablor/build/native_engine_test.exe`, `native_fx_test.exe` and `native_dsp_test.exe`. No Move is needed for this.
4. **Ear reports where possible.** Only people with a Move can audition. Report it in the issue as: preset, what you hear, which side (Serum or Move). Treat it as data to check against a measurement.

## What you bring yourself
- Serum 2 (the full plugin) and your own packs, for the reference renders. `converter/serum_paths.py` finds them; the `SERUM_ROOT` and `SERUM_VST` environment variables override.
- Never commit Serum presets, packs, wavetables, samples or renders, nor the effect fingerprints (`calibration/fx_fingerprints*.json`): they are measurements of a pack's own effects and are rebuilt
  by `calibration/fx_fingerprint.py` and `import_pack.py`. The `.gitignore` already covers `out/`, `out_*/` and `calibration/refs/`.
- Do not install on a shared Move without a rollback: `deploy_all.sh` makes one.

## Installing on a Move
`deploy_all.sh` installs a whole bank and the module. Never overwrite the live `dsp.so` in place (the host maps it): `schwung-tablor/scripts/deploy.sh` uploads under a temporary name and renames.
