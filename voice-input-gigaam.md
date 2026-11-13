# Voice input with GigaAM — what was built and what was learned

Session of 2026-11-13. Read this instead of re-exploring the Harness for speech input.

## Architecture inside DSH 0.2.2-rc.3

Voice input is a shipped optional bundle of four rows:

| Row | Package | Role |
|---|---|---|
| `speech-to-text` | `@deepseek-ai/dsh-experimental-speech-to-text` | service `ctx.speechToText`: provider registry, selection, `transcribe(spec, signal)` |
| `speech-to-text-sensevoice` | `...-speech-to-text-sensevoice` | bundled provider, SenseVoiceSmall (zh/en/yue/ja/ko — no Russian) |
| `api-speech-to-text` | `...-api-speech-to-text` | Host↔browser transport, 120 s / 4 MiB caps by default |
| `ui-voice-input` | `...-client-ui-voice-input` | mic button in the composer, device picker, preparation card |

Provider contract (`src/types.ts` of the service): `{ info: { id, name, location, languages, downloadSources?, setupEstimate? }, preparation?: { snapshot, subscribe, prepare, cancel }, transcribe({ audio, language }, signal) }`. Audio arrives as canonical 16 kHz mono PCM16 WAV. Phases the UI accepts: `unprepared | checking | downloading | loading | waking | standby | ready | cancelled | failed`. The mic is usable for `ready`, `standby`, `waking`.

## What this workspace installs

`gigaam-stt/` → `@local/dsh-gigaam-stt`, row `speech-to-text-gigaam`, provider id `gigaam-local`. GigaAM Multilingual (`large_ctc`, 600M, int8) via `sherpa-onnx-node` already shipped with the Harness. Model files in `~/.dsh/speech-to-text/gigaam` (591 645 642 B + 391 B tokens).

Two gotchas that cost time:

- sherpa-onnx-node expects tokens at `modelConfig.tokens`, **not** inside `modelConfig.nemoCtc`; otherwise `Failed to create offline recognizer`.
- `OfflineRecognizer.createAsync` + `decodeAsync(stream)` are genuinely async, so no worker thread is required.

## Measured on this machine (i5-class CPU, 4 threads)

| Audio | Inference | RTF |
|---|---|---|
| 12.1 s English | 1.43 s | 0.12 |
| 20.0 s Russian | 2.66 s | 0.13 |
| 2 s silence | 0.27 s | — |

Model load ~2.5 s, RSS ~1.2–1.5 GB, unloads after `idleTimeoutMs` (300 s). Output is lowercase, no punctuation: the multilingual export is charwise CTC.

## Switching variants

`variant` in `gigaam-stt/cordis.patch.yml`: `multilingual-600m` (default) or `multilingual-220m` (225 MB, ~2.5× faster, weaker). Punctuation and casing need a different model line entirely — GigaAM v3 `e2e_ctc` (320 MB, Russian-only, same NeMo-CTC path, other files). Adding it is one entry in `VARIANTS` in `gigaam-stt/index.js` plus a restart.

## Re-testing without the UI

```powershell
# ELECTRON_RUN_AS_NODE with the Desktop executable, from this directory
# decode-file.mjs converts any PCM16 WAV to 16 kHz mono and prints the transcript
```

`ffmpeg` and `zstd` exist on this machine at `C:\ProgramData\chocolatey\bin` and in a poppler tree; SAPI TTS is blocked by the file sandbox, so generate samples with ffmpeg instead.

## Re-applying after edits

The bundle is a linked directory: editing `gigaam-stt/*` needs an app restart to load the new module (the Host caches ESM). Row toggles via plugin-manager are immediate.
