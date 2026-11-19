# Kids game speech clips

Pre-generated, compressed speech for the matching, numbers and syllables
games, served from the local app under `static/audio/kids/games/<game>/<lang>/`.
The games play these clips first; if a clip is missing or cannot be played,
the browser voice reads the same text as a fallback. Clips never leave the
device and no speech is sent to network voices for pre-recorded lines.

- Piper text-to-speech: https://github.com/OHF-Voice/piper1-gpl (GPL-3.0-or-later)
- German voice: Thorsten, medium, based on Thorsten-Voice; voice dataset CC0:
  https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE/thorsten/medium
- English voice: LibriTTS-R, medium, speaker 0; dataset CC BY 4.0:
  https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/libritts_r/medium
  and https://www.openslr.org/141/

The audio files are generated synthetic speech, not recordings of the dataset
speakers. The English dataset attribution and license are retained here.
To regenerate, install `piper-tts==1.8.0`, `ffmpeg` with `libmp3lame`, `node`,
download the matching Piper models and configs, then run
`python -m tools.generate_kids_game_audio` with the four model arguments.
`--check` verifies every inventory clip without Piper, `--dump-keys` prints
the clip inventory as JSON. Clip texts come from the games themselves
(matching/numbers config modules, syllables Node dump), so added words are
covered automatically after regeneration.
