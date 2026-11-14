import shutil
import subprocess
from pathlib import Path
from typing import Optional


class VideoProcessingError(RuntimeError):
    pass


class VideoService:
    """Extracts 16 kHz mono WAV audio from video/audio inputs via ffmpeg."""

    def __init__(self, working_dir: Path | str) -> None:
        self.working_dir = Path(working_dir)
        self.working_dir.mkdir(parents=True, exist_ok=True)

    def extract_audio(
        self, input_path: Path | str, output_wav: Optional[Path | str] = None
    ) -> Path:
        input_path = Path(input_path)
        if not input_path.exists():
            raise VideoProcessingError(f"Input media file not found: {input_path}")

        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise VideoProcessingError("ffmpeg executable not found in PATH")

        target_wav = (
            Path(output_wav)
            if output_wav is not None
            else self.working_dir / f"{input_path.stem}.wav"
        )
        target_wav.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(target_wav),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise VideoProcessingError(
                f"ffmpeg failed with code {result.returncode}: {result.stderr.strip()}"
            )
        return target_wav
