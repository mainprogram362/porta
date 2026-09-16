"""Explicit FFmpeg planning for a small, safe batch video-encoding prototype."""

from __future__ import annotations

from runtime import managed_process

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Literal


EncoderMode = Literal["automatic", "confirm_detected", "manual"]
CodecKey = Literal["h264", "hevc", "av1"]
OutputMode = Literal["specified_directory", "alongside_source"]
EncodeMode = Literal["reencode", "keyframe_cut"]
EncodeBackendKey = Literal["software", "nvidia_nvenc", "intel_qsv", "vaapi"]
TimeBasis = Literal["seconds", "frames"]
EditKind = Literal["cut", "mosaic", "image_overlay"]
ResizeMode = Literal["fit", "stretch"]

_CODECS: dict[CodecKey, tuple[str, str]] = {
    "h264": ("libx264", "H.264 / 互換性重視"),
    "hevc": ("libx265", "H.265 / 高圧縮"),
    "av1": ("libsvtav1", "AV1 / 高圧縮・低速"),
}
_ENCODE_BACKEND_LABELS: dict[EncodeBackendKey, str] = {
    "software": "CPU（ソフトウェア）",
    "nvidia_nvenc": "NVIDIA NVENC",
    "intel_qsv": "Intel Quick Sync",
    "vaapi": "VA-API（Intel / AMD）",
}
_NVIDIA_ENCODERS: dict[CodecKey, str] = {
    "h264": "h264_nvenc",
    "hevc": "hevc_nvenc",
    "av1": "av1_nvenc",
}

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_TOOL_DIRECTORY = _PROJECT_ROOT / "integrated_backends" / "ffmpeg" / "bin"


@dataclass(frozen=True)
class FFmpegTools:
    ffmpeg: Path
    ffprobe: Path


@dataclass(frozen=True)
class VideoEncodeRequest:
    sources: tuple[Path, ...]
    output_directory: Path | None
    codec: CodecKey
    crf: int
    trim_start_frames: int = 0
    output_mode: OutputMode = "specified_directory"
    output_suffix: str = "_encoded"
    encode_mode: EncodeMode = "reencode"
    cut_start_seconds: float | None = None
    cut_end_seconds: float | None = None
    resolution: tuple[int, int] | None = None
    resize_mode: ResizeMode = "fit"
    audio_bitrate_kbps: int = 192
    target_size_mib: int | None = None
    edit_rules: tuple["VideoEditRule", ...] = ()
    encode_backend: EncodeBackendKey = "software"
    sample_seconds: int | None = None


@dataclass(frozen=True)
class VideoEditRule:
    """One user-authored edit on the original, unedited video timeline."""

    kind: EditKind
    time_basis: TimeBasis
    start: float
    end: float
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    mosaic_block_size: int = 16
    image_path: Path | None = None


@dataclass(frozen=True)
class KeyframeCut:
    """One requested removal and its safe stream-copy boundaries."""

    requested_start: float
    requested_end: float
    effective_start: float
    effective_end: float
    duration: float


@dataclass(frozen=True)
class PlannedEncode:
    source: Path
    output: Path
    temporary_output: Path
    command: tuple[str, ...]
    keyframe_cut: KeyframeCut | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class VideoEncodePlan:
    tools: FFmpegTools
    request: VideoEncodeRequest
    items: tuple[PlannedEncode, ...]


@dataclass(frozen=True)
class VideoEncodePreview:
    plan: VideoEncodePlan | None
    text: str

    @property
    def is_ready(self) -> bool:
        return self.plan is not None


def discover_tools() -> FFmpegTools:
    """Find one valid FFmpeg pair, preferring PORTA's private backend copy."""
    candidates: list[tuple[str, str | Path, str | Path]] = [
        (
            "PORTA内バックエンド",
            _BUNDLED_TOOL_DIRECTORY / "ffmpeg",
            _BUNDLED_TOOL_DIRECTORY / "ffprobe",
        )
    ]
    system_ffmpeg = shutil.which("ffmpeg")
    system_ffprobe = shutil.which("ffprobe")
    if system_ffmpeg and system_ffprobe:
        candidates.append(("パソコン本体のPATH", system_ffmpeg, system_ffprobe))

    failures: list[str] = []
    for source, ffmpeg, ffprobe in candidates:
        try:
            return validate_tools(ffmpeg, ffprobe)
        except ValueError as exc:
            failures.append(f"{source}: {exc}")

    detail = "\n".join(failures)
    raise ValueError(
        "PORTA内バックエンドとパソコン本体のどちらにも、使える "
        "ffmpeg / ffprobe の組を見つけられません。パスを手入力してください。"
        + (f"\n{detail}" if detail else "")
    )


def validate_tools(ffmpeg_value: str | Path, ffprobe_value: str | Path) -> FFmpegTools:
    """Accept only executable FFmpeg/ffprobe paths that answer ``-version``."""
    ffmpeg = Path(ffmpeg_value).expanduser().resolve(strict=False)
    ffprobe = Path(ffprobe_value).expanduser().resolve(strict=False)
    for label, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        if not path.is_file() or not path.stat().st_mode & 0o111:
            raise ValueError(f"{label} の実行ファイルを確認できません: {path}")
        try:
            result = managed_process.run(
                [str(path), "-version"], capture_output=True, text=True, check=False, timeout=8,
                label='FFmpeg・動画情報の確認',
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"{label} を実行できません: {path}") from exc
        if result.returncode != 0:
            raise ValueError(f"{label} は -version に正常応答しません: {path}")
    return FFmpegTools(ffmpeg=ffmpeg, ffprobe=ffprobe)


def validate_encode_backend(tools: FFmpegTools, request: VideoEncodeRequest) -> None:
    """Prove a selected hardware encoder can run without touching user files."""
    if request.encode_backend == "software":
        return
    if request.encode_backend != "nvidia_nvenc":
        raise ValueError("このGPU方式はまだ実装していません。CPUまたはNVIDIA NVENCを選んでください。")
    if request.encode_mode != "reencode":
        raise ValueError("高速キーフレームカットは再エンコードしないため、GPU方式を使えません。")
    encoder = _NVIDIA_ENCODERS[request.codec]
    try:
        listed = managed_process.run(
            [str(tools.ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
            label='FFmpeg・動画情報の確認',
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("FFmpegのNVIDIA対応を確認できません。") from exc
    if listed.returncode != 0 or encoder not in listed.stdout:
        raise ValueError(f"このFFmpegには {encoder} が含まれていません。CPU方式を選んでください。")

    # A listed encoder alone is insufficient: the driver, GPU generation, and
    # permissions can still reject it. This encodes one generated frame only;
    # it reads no user file and creates no output file.
    command = (
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=1", "-frames:v", "1",
        "-an", "-c:v", encoder, "-f", "null", "-",
    )
    try:
        tested = managed_process.run(
            command, capture_output=True, text=True, check=False, timeout=20,
            label='FFmpeg・動画情報の確認',
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("NVIDIA NVENCの実機テストを実行できません。") from exc
    if tested.returncode != 0:
        detail = tested.stderr.strip() or tested.stdout.strip()
        raise ValueError(
            "NVIDIA NVENCの実機テストに失敗しました。GPUドライバ・GPU対応・利用権限を確認してください。"
            + (f"\n{detail[-1500:]}" if detail else "")
        )


def build_encode_preview(tools: FFmpegTools, request: VideoEncodeRequest) -> VideoEncodePreview:
    """Build every command before starting FFmpeg or creating any output."""
    try:
        plan = build_encode_plan(tools, request)
    except (OSError, ValueError) as exc:
        return VideoEncodePreview(None, "実行できません。次を確認してください。\n" + str(exc))
    lines = [
        "操作: 動画エンコード（プロトタイプ）",
        f"対象: {len(plan.items)} 件",
        (
            "方式: 高速キーフレームカット（映像・音声の再エンコードなし）"
            if request.encode_mode == "keyframe_cut"
            else f"映像: {_CODECS[request.codec][1]} / CRF {request.crf}"
        ),
        (
            "編集: キーフレーム境界へ調整した範囲削除"
            if request.encode_mode == "keyframe_cut"
            else f"編集ルール: {len(request.edit_rules)}件"
        ),
        f"実行装置: {_ENCODE_BACKEND_LABELS[request.encode_backend]}",
        (
            "解像度: 元のまま"
            if request.resolution is None or request.encode_mode == "keyframe_cut"
            else f"解像度: {request.resolution[0]}×{request.resolution[1]}（{'縦横比を維持' if request.resize_mode == 'fit' else '強制変形'}）"
        ),
        (
            "サイズ指定: なし"
            if request.target_size_mib is None or request.encode_mode == "keyframe_cut"
            else f"サイズ指定: 1動画あたり約 {request.target_size_mib} MiB"
        ),
        f"出力名末尾: {request.output_suffix or '_encoded'}",
        (
            f"出力先: {request.output_directory} にまとめて保存"
            if request.output_mode == "specified_directory"
            else "出力先: 各動画と同じフォルダへ保存"
        ),
        "出力: MP4、新しいファイルのみ作成。元ファイルは削除しません。",
        "変換中は【PORTA変換中・未確認】付きの名前で保存し、成功時だけ完成名へ変更します。",
        "",
        "変更予定:",
    ]
    if request.sample_seconds is not None:
        lines.append(f"試し変換: 編集後の先頭{request.sample_seconds}秒まで。通常出力とは別の名前で保存します。")
    if request.encode_mode == "reencode" and request.edit_rules:
        lines.insert(-1, "編集内容: " + " / ".join(_edit_rule_summary(rule) for rule in request.edit_rules))
    for index, item in enumerate(plan.items, 1):
        lines.append(f"{index}. {item.source}\n   → {item.output}")
        if item.keyframe_cut is not None:
            cut = item.keyframe_cut
            lines.extend(
                (
                    f"   指定削除: {_seconds_text(cut.requested_start)}〜{_seconds_text(cut.requested_end)}",
                    f"   実際削除: {_seconds_text(cut.effective_start)}〜{_seconds_text(cut.effective_end)}",
                    f"   開始側: 指定より {_seconds_text(cut.requested_start - cut.effective_start)} 早く削除",
                    f"   終了側: 指定より {_seconds_text(cut.effective_end - cut.requested_end)} 遅く削除",
                )
            )
        lines.extend(f"   注意: {note}" for note in item.notes)
    return VideoEncodePreview(plan, "\n".join(lines))


def build_encode_plan(tools: FFmpegTools, request: VideoEncodeRequest) -> VideoEncodePlan:
    """Validate inputs and create non-overwriting FFmpeg commands."""
    if not request.sources:
        raise ValueError("動画ファイルを1件以上追加し、対象にチェックしてください。")
    if request.sample_seconds is not None:
        if type(request.sample_seconds) is not int or not 1 <= request.sample_seconds <= 60:
            raise ValueError("試し変換は1〜60秒で指定してください。")
        if request.encode_mode != "reencode" or request.target_size_mib is not None:
            raise ValueError("試し変換は再エンコード・サイズ指定なしで使用してください。")
    if request.output_mode not in {"specified_directory", "alongside_source"}:
        raise ValueError("未対応の出力方式です。")
    if request.encode_mode not in {"reencode", "keyframe_cut"}:
        raise ValueError("未対応のエンコード方式です。")
    if request.encode_backend not in _ENCODE_BACKEND_LABELS:
        raise ValueError("未対応のエンコード実行装置です。")
    if request.output_mode == "specified_directory" and request.output_directory is None:
        raise ValueError("まとめて出力する場合は、出力先フォルダを入力してください。")
    if request.output_mode == "specified_directory" and not request.output_directory.is_dir():
        raise ValueError("出力先フォルダが存在しません。")
    if request.codec not in _CODECS:
        raise ValueError("未対応の映像コーデックです。")
    if not 0 <= request.crf <= 63:
        raise ValueError("品質（CRF）は 0〜63 で指定してください。")
    if request.encode_backend == "nvidia_nvenc" and request.crf > 51:
        raise ValueError("NVIDIA NVENCの品質（CQ）は 0〜51 で指定してください。")
    if request.trim_start_frames < 0:
        raise ValueError("先頭から削除するフレーム数は0以上にしてください。")
    if request.resize_mode not in {"fit", "stretch"}:
        raise ValueError("未対応の解像度変更方式です。")
    if request.resolution is not None and (
        request.resolution[0] <= 0 or request.resolution[1] <= 0
    ):
        raise ValueError("解像度は縦横とも1以上で指定してください。")
    if request.resolution is not None and request.codec in {"h264", "hevc"} and (
        request.resolution[0] % 2 or request.resolution[1] % 2
    ):
        raise ValueError("H.264/H.265の出力解像度は縦横とも偶数にしてください。")
    if not 32 <= request.audio_bitrate_kbps <= 512:
        raise ValueError("音声ビットレートは32〜512 kbpsで指定してください。")
    if request.target_size_mib is not None and request.target_size_mib <= 0:
        raise ValueError("目標サイズは1 MiB以上で指定してください。")
    _validate_edit_rules(request.edit_rules, encode_mode=request.encode_mode)
    if request.encode_mode == "keyframe_cut":
        if request.trim_start_frames:
            raise ValueError("高速キーフレームカットでは、先頭フレームカットを併用できません。")
        if request.cut_start_seconds is None or request.cut_end_seconds is None:
            raise ValueError("高速キーフレームカットでは、削除開始・終了秒を指定してください。")
        if request.cut_start_seconds < 0 or request.cut_end_seconds <= request.cut_start_seconds:
            raise ValueError("削除終了秒は、削除開始秒より後にしてください。")
        if request.edit_rules:
            raise ValueError("高速キーフレームカットでは、通常の編集ルールを併用できません。")
        if request.resolution is not None or request.target_size_mib is not None:
            raise ValueError("高速キーフレームカットでは、解像度・サイズ指定を使えません。")
    validate_encode_backend(tools, request)
    suffix = request.output_suffix.strip() or "_encoded"
    if request.sample_seconds is not None:
        suffix += f"_sample_{request.encode_backend}"
    if Path(suffix).name != suffix or suffix in {".", ".."}:
        raise ValueError("出力名末尾にはフォルダ区切りを含めず、名前の末尾だけを入力してください。")

    planned: list[PlannedEncode] = []
    occupied: set[Path] = set()
    for source in request.sources:
        if not source.is_file():
            raise ValueError(f"通常ファイルではない対象があります: {source}")
        output_directory = (
            request.output_directory
            if request.output_mode == "specified_directory"
            else source.parent
        )
        assert output_directory is not None
        output = _unique_output_path(output_directory, source.stem, suffix, occupied)
        temporary_output = _temporary_output_path(output)
        if temporary_output.exists():
            raise ValueError(f"前回の未確認出力が残っています。確認または削除してから再実行してください: {temporary_output}")
        occupied.add(output)
        if request.encode_mode == "keyframe_cut":
            duration, keyframes = _probe_duration_and_keyframes(tools.ffprobe, source)
            cut = _resolve_keyframe_cut(
                requested_start=request.cut_start_seconds,
                requested_end=request.cut_end_seconds,
                duration=duration,
                keyframes=keyframes,
            )
            planned.append(PlannedEncode(source=source, output=output, temporary_output=temporary_output, command=(), keyframe_cut=cut))
            continue
        fps, has_audio = _probe_video_basics(tools.ffprobe, source)
        duration = _probe_duration(tools.ffprobe, source) if _request_needs_duration(request) else None
        command, notes = _build_command(
            tools.ffmpeg,
            source,
            temporary_output,
            request,
            fps=fps,
            has_audio=has_audio,
            duration=duration,
        )
        planned.append(PlannedEncode(source=source, output=output, temporary_output=temporary_output, command=command, notes=notes))
    return VideoEncodePlan(tools=tools, request=request, items=tuple(planned))


def _unique_output_path(directory: Path, stem: str, suffix: str, occupied: set[Path]) -> Path:
    base = directory / f"{stem}{suffix}.mp4"
    if base not in occupied and not base.exists():
        return base
    index = 1
    while True:
        candidate = directory / f"{stem}{suffix} ({index}).mp4"
        if candidate not in occupied and not candidate.exists():
            return candidate
        index += 1


def _temporary_output_path(output: Path) -> Path:
    """Keep incomplete outputs visible without occupying their final filename."""
    return output.with_name(f"{output.stem}【PORTA変換中・未確認】{output.suffix}")


def _probe_duration_and_keyframes(ffprobe: Path, source: Path) -> tuple[float, tuple[float, ...]]:
    """Read only video keyframes, needed to make stream-copy cuts explicit."""
    try:
        result = managed_process.run(
            [
                str(ffprobe), "-v", "error", "-skip_frame", "nokey", "-select_streams", "v:0",
                "-show_frames", "-show_entries", "format=duration:frame=best_effort_timestamp_time,key_frame",
                "-of", "json", str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
            label='FFmpeg・動画情報の確認',
        )
        data = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError(f"キーフレーム情報を読めません: {source}") from exc
    format_data = data.get("format") if isinstance(data, dict) else None
    try:
        duration = float(format_data.get("duration")) if isinstance(format_data, dict) else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    frames = data.get("frames") if isinstance(data, dict) else None
    keyframes = (
        tuple(
            float(frame["best_effort_timestamp_time"])
            for frame in frames
            if isinstance(frame, dict)
            and str(frame.get("key_frame")) == "1"
            and frame.get("best_effort_timestamp_time") is not None
        )
        if isinstance(frames, list)
        else ()
    )
    if duration <= 0 or not keyframes:
        raise ValueError(f"キーフレームまたは動画の長さを確認できません: {source}")
    return duration, keyframes


def _resolve_keyframe_cut(
    *, requested_start: float, requested_end: float, duration: float, keyframes: tuple[float, ...]
) -> KeyframeCut:
    """Expand a requested removal so the requested interval can never remain."""
    if requested_start >= duration:
        raise ValueError("削除開始秒が動画の長さ以後です。")
    end = min(requested_end, duration)
    before = [time for time in keyframes if time <= requested_start]
    after = [time for time in keyframes if time >= end]
    effective_start = max(before) if before else 0.0
    effective_end = min(after) if after else duration
    if effective_end <= effective_start:
        raise ValueError("キーフレーム境界を使うと動画全体が削除されるため実行できません。")
    return KeyframeCut(requested_start, end, effective_start, effective_end, duration)


def _seconds_text(value: float) -> str:
    """Use seconds in previews; frame counts are deliberately not primary UI."""
    return f"{value:.3f}秒"


def keyframe_cut_concat_text(item: PlannedEncode) -> str:
    """Create an ffconcat manifest retaining the two stream-copy segments."""
    if item.keyframe_cut is None:
        raise ValueError("キーフレームカットではない計画です。")
    cut = item.keyframe_cut
    source = str(item.source).replace("\\", "\\\\").replace("'", "\\'")
    lines = ["ffconcat version 1.0"]
    if cut.effective_start > 0:
        lines.extend((f"file '{source}'", "inpoint 0", f"outpoint {cut.effective_start:.9f}"))
    if cut.effective_end < cut.duration:
        lines.extend((f"file '{source}'", f"inpoint {cut.effective_end:.9f}"))
    if len(lines) == 1:
        raise ValueError("キーフレームカット後に残る映像がありません。")
    return "\n".join(lines) + "\n"


def build_keyframe_cut_command(
    tools: FFmpegTools, item: PlannedEncode, concat_manifest: Path
) -> tuple[str, ...]:
    """Build a no-reencode concat command after the temporary manifest exists."""
    if item.keyframe_cut is None:
        raise ValueError("キーフレームカットではない計画です。")
    return (
        str(tools.ffmpeg), "-hide_banner", "-nostdin", "-n", "-f", "concat", "-safe", "0",
        "-i", str(concat_manifest), "-map", "0", "-map_metadata", "0", "-c", "copy", str(item.temporary_output),
    )


def _probe_video_basics(ffprobe: Path, source: Path) -> tuple[float, bool]:
    """Read only the first video stream's average frame rate and audio presence."""
    try:
        result = managed_process.run(
            [
                str(ffprobe), "-v", "error", "-show_entries", "stream=codec_type,avg_frame_rate",
                "-of", "json", str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            label='FFmpeg・動画情報の確認',
        )
        data = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError(f"動画情報を読めません: {source}") from exc
    streams = data.get("streams") if isinstance(data, dict) else None
    if not isinstance(streams, list):
        raise ValueError(f"映像ストリームを確認できません: {source}")
    video = next((stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"), None)
    if not isinstance(video, dict):
        raise ValueError(f"映像ストリームを確認できません: {source}")
    fps = _parse_frame_rate(str(video.get("avg_frame_rate") or ""))
    has_audio = any(isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams)
    return fps, has_audio


def _probe_duration(ffprobe: Path, source: Path) -> float:
    """Read duration only when a rule or size calculation actually needs it."""
    try:
        result = managed_process.run(
            [str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            label='FFmpeg・動画情報の確認',
        )
        data = json.loads(result.stdout) if result.returncode == 0 else {}
        raw = data.get("format", {}).get("duration") if isinstance(data, dict) else None
        duration = float(raw)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError):
        raise ValueError(f"動画の長さを確認できません: {source}") from None
    if duration <= 0:
        raise ValueError(f"動画の長さを確認できません: {source}")
    return duration


def _request_needs_duration(request: VideoEncodeRequest) -> bool:
    return bool(request.edit_rules) or bool(request.trim_start_frames) or request.target_size_mib is not None


def _validate_edit_rules(rules: tuple[VideoEditRule, ...], *, encode_mode: EncodeMode) -> None:
    for number, rule in enumerate(rules, start=1):
        if rule.kind not in {"cut", "mosaic", "image_overlay"}:
            raise ValueError(f"編集ルール{number}: 未対応の操作です。")
        if rule.time_basis not in {"seconds", "frames"}:
            raise ValueError(f"編集ルール{number}: 時間基準を指定してください。")
        if rule.start < 0 or rule.end <= rule.start:
            raise ValueError(f"編集ルール{number}: 終了は開始より後にしてください。")
        if rule.kind == "mosaic":
            if rule.width <= 0 or rule.height <= 0 or rule.x < 0 or rule.y < 0:
                raise ValueError(f"編集ルール{number}: モザイクの位置と幅・高さを指定してください。")
            if rule.mosaic_block_size < 2:
                raise ValueError(f"編集ルール{number}: モザイクの粗さは2以上にしてください。")
        if rule.kind == "image_overlay":
            if rule.image_path is None or not rule.image_path.is_file():
                raise ValueError(f"編集ルール{number}: 重ねる画像ファイルを確認できません。")
            if rule.x < 0 or rule.y < 0:
                raise ValueError(f"編集ルール{number}: 画像の位置は0以上にしてください。")
    if encode_mode == "keyframe_cut" and rules:
        raise ValueError("高速キーフレームカットでは編集ルールを使えません。")


def _parse_frame_rate(value: str) -> float:
    try:
        numerator, denominator = value.split("/", 1)
        rate = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        raise ValueError("フレームレートを確認できない動画です。先頭フレーム削除は使えません。") from None
    if rate <= 0:
        raise ValueError("フレームレートを確認できない動画です。先頭フレーム削除は使えません。")
    return rate


def _build_command(
    ffmpeg: Path,
    source: Path,
    output: Path,
    request: VideoEncodeRequest,
    *,
    fps: float,
    has_audio: bool,
    duration: float | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    encoder, _label = _CODECS[request.codec]
    uses_nvenc = request.encode_backend == "nvidia_nvenc"
    if uses_nvenc:
        encoder = _NVIDIA_ENCODERS[request.codec]
    command: list[str] = [str(ffmpeg), "-hide_banner", "-nostdin", "-n", "-i", str(source)]
    normalized_rules, notes = _normalized_edit_rules(
        request.edit_rules, fps=fps, duration=duration, trim_start_frames=request.trim_start_frames
    )
    image_inputs = [rule for rule in normalized_rules if rule.kind == "image_overlay"]
    for rule in image_inputs:
        assert rule.image_path is not None
        command.extend(["-loop", "1", "-i", str(rule.image_path)])
    filter_text, video_map, audio_map = _build_edit_filter(
        normalized_rules,
        has_audio=has_audio,
        duration=duration,
        resolution=request.resolution,
        resize_mode=request.resize_mode,
        image_rules=image_inputs,
    )
    if filter_text:
        command.extend(["-filter_complex", filter_text, "-map", video_map])
        if audio_map is not None:
            command.extend(["-map", audio_map])
    else:
        command.extend(["-map", "0:v:0", "-map", "0:a?"])
    command.extend(["-map_metadata", "0", "-c:v", encoder])
    if request.target_size_mib is None:
        if uses_nvenc:
            command.extend(["-rc", "vbr", "-cq", str(request.crf)])
        else:
            command.extend(["-crf", str(request.crf)])
    else:
        assert duration is not None
        video_bitrate = _target_video_bitrate(
            request.target_size_mib,
            _edited_duration(normalized_rules, duration),
            request.audio_bitrate_kbps,
        )
        command.extend(["-b:v", str(video_bitrate)])
    if uses_nvenc:
        command.extend(["-preset", "p4"])
    elif request.codec in {"h264", "hevc"}:
        command.extend(["-preset", "medium"])
    if has_audio:
        command.extend(["-c:a", "aac", "-b:a", f"{request.audio_bitrate_kbps}k"])
    if request.sample_seconds is not None:
        command.extend(["-t", str(request.sample_seconds)])
    command.append(str(output))
    return tuple(command), notes


def _normalized_edit_rules(
    rules: tuple[VideoEditRule, ...], *, fps: float, duration: float | None, trim_start_frames: int
) -> tuple[tuple[VideoEditRule, ...], tuple[str, ...]]:
    """Convert all rules to original-timeline seconds and clamp only their end."""
    converted: list[VideoEditRule] = []
    notes: list[str] = []
    if trim_start_frames:
        converted.append(VideoEditRule("cut", "seconds", 0.0, trim_start_frames / fps))
    for number, rule in enumerate(rules, start=1):
        factor = 1 / fps if rule.time_basis == "frames" else 1.0
        if rule.time_basis == "frames":
            notes.append(f"編集ルール{number}のフレーム指定は平均フレームレートから秒へ換算しました。")
        start = rule.start * factor
        end = rule.end * factor
        if duration is not None:
            if start >= duration:
                notes.append(f"編集ルール{number}は動画末尾以後のため適用しません。")
                continue
            if end > duration:
                end = duration
                notes.append(f"編集ルール{number}の終了を動画末尾へ調整しました。")
        converted.append(
            VideoEditRule(
                rule.kind, "seconds", start, end, rule.x, rule.y, rule.width, rule.height,
                rule.mosaic_block_size, rule.image_path,
            )
        )
    return tuple(converted), tuple(notes)


def _build_edit_filter(
    rules: tuple[VideoEditRule, ...], *, has_audio: bool, duration: float | None,
    resolution: tuple[int, int] | None, resize_mode: ResizeMode, image_rules: list[VideoEditRule],
) -> tuple[str, str, str | None]:
    """Compose visual edits first, then remove cut ranges on the original timeline."""
    visual_rules = [rule for rule in rules if rule.kind in {"mosaic", "image_overlay"}]
    cut_ranges = _merged_cut_ranges(rule for rule in rules if rule.kind == "cut")
    needs_filter = bool(visual_rules or cut_ranges or resolution is not None)
    if not needs_filter:
        return "", "0:v:0", None
    filters: list[str] = []
    current = "0:v:0"
    label_counter = 0
    image_input_index = 1
    for rule in visual_rules:
        label_counter += 1
        output_label = f"v{label_counter}"
        if rule.kind == "mosaic":
            crop = f"crop={rule.width}:{rule.height}:{rule.x}:{rule.y}"
            down_width = max(1, rule.width // rule.mosaic_block_size)
            down_height = max(1, rule.height // rule.mosaic_block_size)
            filters.append(
                f"[{current}]split=2[{output_label}base][{output_label}crop]"
                f";[{output_label}crop]{crop},scale={down_width}:{down_height}:flags=neighbor,"
                f"scale={rule.width}:{rule.height}:flags=neighbor[{output_label}mosaic]"
                f";[{output_label}base][{output_label}mosaic]overlay={rule.x}:{rule.y}:"
                f"enable='between(t,{rule.start:.9f},{rule.end:.9f})'[{output_label}]"
            )
        else:
            filters.append(
                f"[{current}][{image_input_index}:v]overlay={rule.x}:{rule.y}:"
                f"enable='between(t,{rule.start:.9f},{rule.end:.9f})'[{output_label}]"
            )
            image_input_index += 1
        current = output_label
    if resolution is not None:
        label_counter += 1
        output_label = f"v{label_counter}"
        width, height = resolution
        if resize_mode == "fit":
            scale = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
            pad = f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            filters.append(f"[{current}]{scale},{pad}[{output_label}]")
        else:
            filters.append(f"[{current}]scale={width}:{height}[{output_label}]")
        current = output_label
    if not cut_ranges:
        return ";".join(filters), f"[{current}]", "0:a?" if has_audio else None
    if duration is None:
        raise ValueError("範囲削除には動画の長さが必要です。")
    kept = _kept_ranges(cut_ranges, duration)
    if not kept:
        raise ValueError("編集後に残る映像がありません。")
    video_labels: list[str] = []
    audio_labels: list[str] = []
    video_inputs = [current] if len(kept) == 1 else [f"precutv{index}" for index in range(len(kept))]
    if len(video_inputs) > 1:
        filters.append(
            f"[{current}]split={len(video_inputs)}" + "".join(f"[{label}]" for label in video_inputs)
        )
    for index, (start, end) in enumerate(kept):
        video_label = f"cutv{index}"
        video_labels.append(video_label)
        filters.append(f"[{video_inputs[index]}]trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS[{video_label}]")
        if has_audio:
            audio_label = f"cuta{index}"
            audio_labels.append(audio_label)
            filters.append(f"[0:a:0]atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS[{audio_label}]")
    if len(video_labels) == 1:
        return ";".join(filters), f"[{video_labels[0]}]", f"[{audio_labels[0]}]" if audio_labels else None
    filters.append("".join(f"[{label}]" for label in video_labels) + f"concat=n={len(video_labels)}:v=1:a=0[vout]")
    if has_audio:
        filters.append("".join(f"[{label}]" for label in audio_labels) + f"concat=n={len(audio_labels)}:v=0:a=1[aout]")
    return ";".join(filters), "[vout]", "[aout]" if has_audio else None


def _merged_cut_ranges(rules: Iterable[VideoEditRule]) -> tuple[tuple[float, float], ...]:
    ranges = sorted((rule.start, rule.end) for rule in rules)
    merged: list[tuple[float, float]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _kept_ranges(cut_ranges: tuple[tuple[float, float], ...], duration: float) -> tuple[tuple[float, float], ...]:
    kept: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in cut_ranges:
        start = max(0.0, start)
        end = min(duration, end)
        if start > cursor:
            kept.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        kept.append((cursor, duration))
    return tuple((start, end) for start, end in kept if end > start)


def _target_video_bitrate(target_mib: int, duration: float, audio_kbps: int) -> int:
    """Leave a small container margin; the target is an estimate, not a cap."""
    total_bits = target_mib * 1024 * 1024 * 8
    video_bitrate = int(total_bits * 0.96 / duration - audio_kbps * 1000)
    if video_bitrate < 100_000:
        raise ValueError("目標サイズが短すぎるか小さすぎます。音声だけで大半を使ってしまいます。")
    return video_bitrate


def _edited_duration(rules: tuple[VideoEditRule, ...], duration: float) -> float:
    """Use post-cut duration for a per-video size estimate."""
    removed = sum(end - start for start, end in _merged_cut_ranges(rule for rule in rules if rule.kind == "cut"))
    result = duration - removed
    if result <= 0:
        raise ValueError("編集後に残る動画の長さがないため、サイズ指定を計算できません。")
    return result


def _edit_rule_summary(rule: VideoEditRule) -> str:
    unit = "f" if rule.time_basis == "frames" else "秒"
    names = {"cut": "削除", "mosaic": "モザイク", "image_overlay": "画像重ね"}
    return f"{names[rule.kind]} {rule.start:g}〜{rule.end:g}{unit}"
