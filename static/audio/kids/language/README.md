# Kids language pronunciation audio

The 100 English and 100 German word clips and the language-game questions are
pre-generated, compressed audio served from the local app. The service worker
caches a clip after it is played, so previously used words and questions remain
available offline. Questions use German, English, or joined bilingual speech
segments so target vocabulary keeps its language pronunciation. If a clip
cannot be played, the browser voice is used as a fallback.

- Piper text-to-speech: https://github.com/OHF-Voice/piper1-gpl (GPL-3.0-or-later)
- German voice: Thorsten, medium, based on Thorsten-Voice; voice dataset CC0:
  https://huggingface.co/rhasspy/piper-voices/tree/main/de/de_DE/thorsten/medium
- English voice: LibriTTS-R, medium, speaker 0; dataset CC BY 4.0:
  https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/libritts_r/medium
  and https://www.openslr.org/141/

The audio files are generated synthetic speech, not recordings of the dataset
speakers. The English dataset attribution and license are retained here.
To regenerate, install `piper-tts==1.8.0`, `ffmpeg` with `libmp3lame`, download
the matching Piper models and configs, then run
`python tools/generate_kids_pronunciations.py` with the four model arguments.
