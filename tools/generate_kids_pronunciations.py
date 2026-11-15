#!/usr/bin/env python3
"""Generate offline English/German Kids vocabulary and complete question audio.

Requires ``piper-tts`` (1.8.x), ``ffmpeg`` with libmp3lame, and the compatible
Piper model/config files. Model weights are intentionally not stored here.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOCABULARY = ROOT / "static" / "data" / "kids_language_vocabulary.json"
OUTPUT = ROOT / "static" / "audio" / "kids" / "language"
QUOTED_WORD = re.compile(r'[“„"]([^“”„"]+)[””“"]')
WAV_CACHE: dict[tuple[int, str, int | None], bytes] = {}


def wav_bytes(voice, text: str, *, speaker_id: int | None = None) -> bytes:
    from piper.config import SynthesisConfig

    key = (id(voice), text, speaker_id)
    if key in WAV_CACHE:
        return WAV_CACHE[key]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize_wav(
            text,
            wav_file,
            syn_config=SynthesisConfig(speaker_id=speaker_id, length_scale=0.94),
        )
    rendered = buffer.getvalue()
    WAV_CACHE[key] = rendered
    return rendered


def encode_mp3(wav_data: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kids-pronunciation-") as temporary:
        wav_path = Path(temporary) / "speech.wav"
        wav_path.write_bytes(wav_data)
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(wav_path), "-ac", "1", "-ar", "22050",
                "-codec:a", "libmp3lame", "-q:a", "5", str(destination),
            ],
            check=True,
        )


def generate_word(voice, text: str, destination: Path, *, speaker_id: int | None = None) -> None:
    encode_mp3(wav_bytes(voice, text, speaker_id=speaker_id), destination)


def phrase_segments(text: str, main_voice, quoted_voice, *, main_speaker: int | None = None, quote_speaker: int | None = None):
    segments = []
    cursor = 0
    for match in QUOTED_WORD.finditer(text):
        prefix = text[cursor:match.start()].strip()
        if re.search(r"\w", prefix):
            segments.append(wav_bytes(main_voice, prefix, speaker_id=main_speaker))
        segments.append(wav_bytes(quoted_voice, match.group(1), speaker_id=quote_speaker))
        cursor = match.end()
    suffix = text[cursor:].strip()
    if re.search(r"\w", suffix):
        segments.append(wav_bytes(main_voice, suffix, speaker_id=main_speaker))
    return segments or [wav_bytes(main_voice, text, speaker_id=main_speaker)]


def generate_phrase(segments: list[bytes], destination: Path) -> None:
    import numpy as np

    combined = io.BytesIO()
    sample_rate = 22050
    silence = b"\0\0" * int(sample_rate * 0.09)
    with wave.open(combined, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        for index, segment in enumerate(segments):
            with wave.open(io.BytesIO(segment), "rb") as source:
                params = source.getparams()
                samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
                if params.nchannels > 1:
                    samples = samples.reshape(-1, params.nchannels).astype(np.int32).mean(axis=1)
                if params.framerate != sample_rate:
                    duration = len(samples) / params.framerate
                    target_count = round(duration * sample_rate)
                    source_times = np.arange(len(samples), dtype=np.float64) / params.framerate
                    target_times = np.arange(target_count, dtype=np.float64) / sample_rate
                    samples = np.interp(target_times, source_times, samples).astype("<i2")
                target.writeframes(samples.tobytes())
            if index + 1 < len(segments):
                target.writeframes(silence)
    encode_mp3(combined.getvalue(), destination)


def quoted_segments(text: str, main_voice, quoted_voice, *, main_speaker: int | None = None, quote_speaker: int | None = None):
    return phrase_segments(
        text, main_voice, quoted_voice,
        main_speaker=main_speaker, quote_speaker=quote_speaker,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--english-model", type=Path, required=True)
    parser.add_argument("--english-config", type=Path, required=True)
    parser.add_argument("--german-model", type=Path, required=True)
    parser.add_argument("--german-config", type=Path, required=True)
    args = parser.parse_args()

    from piper.voice import PiperVoice

    vocabulary = json.loads(VOCABULARY.read_text(encoding="utf-8"))
    if len(vocabulary) < 100:
        raise SystemExit("The Kids vocabulary must contain at least 100 entries.")
    english_voice = PiperVoice.load(args.english_model, config_path=args.english_config)
    german_voice = PiperVoice.load(args.german_model, config_path=args.german_config)

    for index, item in enumerate(vocabulary, 1):
        word_id = item["id"]
        generate_word(english_voice, item["english"], OUTPUT / "en" / f"{word_id}.mp3", speaker_id=0)
        generate_word(german_voice, item["german"], OUTPUT / "de" / f"{word_id}.mp3")

        prompts = (
            ("english", "de", "early", item["english_early_de"], german_voice, english_voice, None, 0),
            ("english", "en", "early", item["english_early_en"], english_voice, english_voice, 0, 0),
            ("english", "de", "full", item["english_question_de"], german_voice, english_voice, None, 0),
            ("english", "en", "full", item["english_question_en"], english_voice, english_voice, 0, 0),
            ("german", "de", "full", item["german_question_de"], german_voice, german_voice, None, None),
            ("german", "en", "full", item["german_question_en"], english_voice, german_voice, 0, None),
        )
        for subject, ui_language, round_type, prompt, main_voice, quoted_voice, main_speaker, quoted_speaker in prompts:
            audio = quoted_segments(
                prompt, main_voice, quoted_voice,
                main_speaker=main_speaker, quote_speaker=quoted_speaker,
            )
            generate_phrase(
                audio,
                OUTPUT / "prompts" / subject / ui_language / round_type / f"{word_id}.mp3",
            )
        if index % 10 == 0 or index == len(vocabulary):
            print(f"Generated audio for {index}/{len(vocabulary)} vocabulary entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
