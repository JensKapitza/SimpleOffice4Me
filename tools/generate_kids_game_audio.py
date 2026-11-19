#!/usr/bin/env python3
"""Generate offline speech clips for the Kids games (matching, numbers, syllables).

Requires ``piper-tts`` (1.8.x), ``ffmpeg`` with libmp3lame, ``node`` (for the
syllables inventory dump) and the compatible Piper model/config files. Model
weights are intentionally not stored here. Voices match
static/audio/kids/language/README.md: German Thorsten medium, English
LibriTTS-R medium speaker 0.

Clip inventory is derived from the games themselves: matching/numbers items
come from the app config modules, syllables words come from a Node dump of
kids_syllables.js, fixed sentences live in FIXED below. ``--check`` verifies
that every inventory clip exists as a valid MP3 without needing Piper.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "static" / "audio" / "kids" / "games"
DUMP_SCRIPT = ROOT / "tools" / "dump_kids_syllables_inventory.js"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NUMBER_WORDS = {
    1: ("eins", "one"), 2: ("zwei", "two"), 3: ("drei", "three"),
    4: ("vier", "four"), 5: ("fünf", "five"), 6: ("sechs", "six"),
    7: ("sieben", "seven"), 8: ("acht", "eight"), 9: ("neun", "nine"),
    10: ("zehn", "ten"),
}

MATCHING_FIXED = {
    "praise_right": ("Richtig!", "Correct!"),
    "look_here": ("Schau, hier ist:", "Look, here is:"),
    "try_again": ("Fast! Versuch es noch einmal.", "Almost! Try again."),
    "done_great": ("Das hast du toll gemacht!", "You did great!"),
    "nice_playing": ("Gut gespielt!", "Nice playing!"),
}

NUMBERS_FIXED = {
    "how_many": ("Wie viele", "How many"),
    "see_you": ("siehst du?", "can you see?"),
    "tap_on": ("Tippe auf", "Tap"),
    "where_more": ("Wo sind mehr", "Where are more"),
    "where_less": ("Wo sind weniger", "Where are fewer"),
    "tap_it": ("Tipp drauf.", "Tap it."),
    "praise_1": ("Super!", "Great!"),
    "praise_2": ("Genau richtig!", "Exactly right!"),
    "praise_3": ("Toll gemacht!", "Well done!"),
    "praise_4": ("Prima, weiter so!", "Awesome, keep going!"),
    "try_again": ("Versuch es nochmal. Du schaffst das!", "Try again. You can do it!"),
    "amazing": ("Spitzenmäßig!", "Amazing!"),
    "well_done": ("Gut gemacht!", "Well done!"),
    "nice_playing": ("Schön mitgemacht!", "Nice playing!"),
}

SYLLABLES_FIXED = {
    "listen_well": ("Höre gut zu:", "Listen carefully:"),
    "tap_each_syllable": (
        "Tippe für jede Silbe einmal auf die Hände!",
        "Tap the hands once for every syllable!",
    ),
    "praise_right": ("Richtig!", "Right!"),
    "praise_great": ("Richtig! Super gemacht!", "That is right! Well done!"),
    "how_many_syllables": ("Wie viele Silben hörst du?", "How many syllables can you hear?"),
    "what_rhymes": ("Was reimt sich auf", "What rhymes with"),
    "look_solution": ("Schau, hier ist die Lösung.", "Look, here is the answer."),
    "done_great": ("Das hast du toll gemacht!", "You did great!"),
    "nice_playing": ("Gut gespielt!", "Nice playing!"),
    "has_1": ("hat eine Silbe!", "has 1 syllable!"),
    "has_2": ("hat 2 Silben!", "has 2 syllables!"),
    "has_3": ("hat 3 Silben!", "has 3 syllables!"),
    "has_4": ("hat 4 Silben!", "has 4 syllables!"),
    "try_1_of_5": ("Versuch 1 von 5. Probier es weiter!", "Try 1 of 5. Keep going!"),
    "try_2_of_5": ("Versuch 2 von 5. Probier es weiter!", "Try 2 of 5. Keep going!"),
    "try_3_of_5": ("Versuch 3 von 5. Probier es weiter!", "Try 3 of 5. Keep going!"),
    "try_4_of_5": ("Versuch 4 von 5. Probier es weiter!", "Try 4 of 5. Keep going!"),
    "clap_more_1": ("Gut! Noch einmal klatschen!", "Good! Clap 1 more time!"),
    "clap_more_2": ("Gut! Noch zweimal klatschen!", "Good! Clap 2 more times!"),
    "clap_more_3": ("Gut! Noch dreimal klatschen!", "Good! Clap 3 more times!"),
}


def slugify_js_mirror(word: str) -> str:
    """Mirror of slugify() in static/js/kids_syllables.js (German/English words)."""
    import re as _re

    slug = str(word or "").lower()
    for pair in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        slug = slug.replace(*pair)
    slug = _re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    return slug or "clip"


def matching_inventory() -> dict[str, dict[str, str]]:
    """Clip texts per language; keys derive from explicit config audio keys."""
    from app.kids_matching import MATCHING_CATEGORIES

    clips: dict[str, dict[str, str]] = {"de": {}, "en": {}}
    for key, (german, english) in MATCHING_FIXED.items():
        clips["de"][key] = german
        clips["en"][key] = english
    for category, items in MATCHING_CATEGORIES.items():
        for item in items:
            clips["de"][item["word_audio"]] = item["de"]
            clips["en"][item["word_audio"]] = item["en"]
            prompt_key = item["prompt_audio"]
            if category == "sounds":
                clips["de"][prompt_key] = f"Welches Tier macht {item['sound_de']}?"
                clips["en"][prompt_key] = f"Which animal says {item['sound_en']}?"
            else:
                clips["de"][prompt_key] = f"Finde: {item['de']}!"
                clips["en"][prompt_key] = f"Find: {item['en']}!"
    return clips


def numbers_inventory() -> dict[str, dict[str, str]]:
    from app.kids_numbers import NUMBERS_ITEMS

    clips: dict[str, dict[str, str]] = {"de": {}, "en": {}}
    for key, (german, english) in NUMBERS_FIXED.items():
        clips["de"][key] = german
        clips["en"][key] = english
    for number, (german, english) in NUMBER_WORDS.items():
        clips["de"][f"num_{number}"] = german
        clips["en"][f"num_{number}"] = english
    for item in NUMBERS_ITEMS:
        clips["de"][item["audio"]] = item["de"]
        clips["en"][item["audio"]] = item["en"]
        clips["de"][item["audio_plural"]] = item["de_plural"]
        clips["en"][item["audio_plural"]] = item["en_plural"]
    return clips


def syllables_dump() -> dict:
    proc = subprocess.run(
        ["node", str(DUMP_SCRIPT)], check=True, capture_output=True, text=True,
    )
    return json.loads(proc.stdout)


def syllables_inventory(dump: dict | None = None) -> dict[str, dict[str, str]]:
    """Clip texts per language; words/parts/rhymes come from the Node dump."""
    dump = dump if dump is not None else syllables_dump()
    clips: dict[str, dict[str, str]] = {"de": {}, "en": {}}
    for key, (german, english) in SYLLABLES_FIXED.items():
        clips["de"][key] = german
        clips["en"][key] = english

    def add(lang: str, key: str, text: str) -> None:
        prior = clips[lang].get(key)
        if prior is not None and prior.casefold() != text.casefold():
            raise SystemExit(f"clip key collision: syllables/{lang}/{key}: {prior!r} vs {text!r}")
        clips[lang].setdefault(key, text)

    for lang in ("de", "en"):
        for entry in dump["words"][lang]:
            add(lang, f"w_{slugify_js_mirror(entry['word'])}", entry["word"])
            for part in entry["parts"]:
                add(lang, f"p_{slugify_js_mirror(part)}", part)
        for entry in dump["rhymes"][lang]:
            others = [entry["target"], entry["rhyme"], *entry["others"]]
            for choice in others:
                add(lang, f"w_{slugify_js_mirror(choice['word'])}", choice["word"])
            rhyme_key = "rhyme_{}_{}".format(
                slugify_js_mirror(entry["target"]["word"]),
                slugify_js_mirror(entry["rhyme"]["word"]),
            )
            if lang == "de":
                add(lang, rhyme_key, f"{entry['target']['word']} und {entry['rhyme']['word']} reimen sich!")
            else:
                add(lang, rhyme_key, f"{entry['target']['word']} and {entry['rhyme']['word']} rhyme!")
    return clips


def inventory() -> dict[str, dict[str, dict[str, str]]]:
    return {
        "matching": matching_inventory(),
        "numbers": numbers_inventory(),
        "syllables": syllables_inventory(),
    }


def clip_path(game: str, lang: str, key: str) -> Path:
    return OUTPUT / game / lang / f"{key}.mp3"


def is_valid_mp3(path: Path) -> bool:
    try:
        header = path.read_bytes()[:4]
    except OSError:
        return False
    return header[:3] == b"ID3" or (len(header) == 4 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)


def check() -> int:
    missing: list[str] = []
    total = 0
    for game, langs in inventory().items():
        for lang, clips in langs.items():
            for key in clips:
                total += 1
                if not is_valid_mp3(clip_path(game, lang, key)):
                    missing.append(str(clip_path(game, lang, key).relative_to(ROOT)))
    if missing:
        print(f"{len(missing)} clips missing or invalid:")
        for item in sorted(missing)[:20]:
            print(f"  {item}")
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more")
        return 1
    print(f"OK: {total} clips present and valid")
    return 0


def dump_keys() -> int:
    data = {
        game: {lang: sorted(clips) for lang, clips in langs.items()}
        for game, langs in inventory().items()
    }
    print(json.dumps(data, indent=1, sort_keys=True))
    return 0


def generate(english_model, english_config, german_model, german_config) -> int:
    from piper.voice import PiperVoice

    from tools.generate_kids_pronunciations import generate_word

    english_voice = PiperVoice.load(english_model, config_path=english_config)
    german_voice = PiperVoice.load(german_model, config_path=german_config)
    count = 0
    for game, langs in inventory().items():
        for key in sorted(langs["de"]):
            generate_word(german_voice, langs["de"][key], clip_path(game, "de", key))
            count += 1
        for key in sorted(langs["en"]):
            generate_word(english_voice, langs["en"][key], clip_path(game, "en", key), speaker_id=0)
            count += 1
        print(f"Generated {game}: {len(langs['de']) + len(langs['en'])} clips")
    print(f"Generated {count} clips total")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify clips without Piper")
    parser.add_argument("--dump-keys", action="store_true", help="print inventory clip keys as JSON")
    parser.add_argument("--english-model", type=Path)
    parser.add_argument("--english-config", type=Path)
    parser.add_argument("--german-model", type=Path)
    parser.add_argument("--german-config", type=Path)
    args = parser.parse_args(argv)
    if args.check:
        return check()
    if args.dump_keys:
        return dump_keys()
    missing = [name for name in ("english_model", "english_config", "german_model", "german_config")
               if getattr(args, name) is None]
    if missing:
        parser.error(f"missing required model arguments: {', '.join('--' + m.replace('_', '-') for m in missing)}")
    return generate(args.english_model, args.english_config, args.german_model, args.german_config)


if __name__ == "__main__":
    raise SystemExit(main())
