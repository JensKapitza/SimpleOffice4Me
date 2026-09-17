"""Read-only executable inventory; does not probe hardware or install packages."""
import platform
import shutil


def audio_dependencies(service, settings):
    """Describe mandatory stream tools and conditional announcement features.

    Availability means an executable is on PATH, not that its codecs, server,
    permissions or hardware work. Keep this separate from runtime health.
    """
    rows = []

    def add(programs, required, purpose):
        rows.append({"programs": programs, "required": required,
                     "available": any(shutil.which(name) for name in programs),
                     "purpose": purpose, "scope": "executable-only"})

    if service in {"sender", "receiver"}:
        add(["ffmpeg"], True, "Audio kodieren bzw. empfangen")
    if service == "sender":
        if settings.get("backend", "pulse") == "pulse":
            add(["pactl"], False, "PulseAudio-Geräte automatisch suchen")
    elif service == "receiver":
        windows = platform.system() == "Windows"
        if settings.get("speaker_devices"):
            add(["ffplay" if windows else "paplay"], True, "Empfang über Lautsprecher wiedergeben")
        if settings.get("virtual_microphone") and not windows:
            if not settings.get("speaker_devices"):
                add(["paplay"], True, "Empfang an den virtuellen Audioausgang weitergeben")
            add(["pactl"], True, "Virtuelles Mikrofon erstellen")
        elif not windows:
            add(["pactl"], False, "Lautsprecher automatisch suchen")
    elif service == "output":
        add(["ffmpeg"], False, "Durchsagen an gebundene RTP/Opus-Empfänger senden")
        add(["pactl"], False, "Lokale PulseAudio-Ausgänge automatisch suchen")
        add(["paplay"], False, "Ansagen auf automatisch erkannten Ausgängen wiedergeben")
        add(["pw-play", "aplay", "ffplay"], False, "Ansagen auf manuell eingetragenen lokalen Ausgängen wiedergeben")
        add(["piper"], False, "Sprachansagen erzeugen; zusätzlich wird ein vorhandenes Sprachmodell benötigt")
    else:
        raise ValueError("Unbekannter Audio-Dienst")
    return rows
