from pathlib import Path
from dataclasses import replace
import pytest
from media import video_encode


def test_sample_is_limited_and_has_a_distinct_output_name(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"sample input")
    monkeypatch.setattr(video_encode, "_probe_video_basics", lambda *_: (30.0, True))
    tools = video_encode.FFmpegTools(Path("/ffmpeg"), Path("/ffprobe"))
    normal = video_encode.VideoEncodeRequest((source,), tmp_path, "hevc", 20)
    full = video_encode.build_encode_plan(tools, normal)
    sample = video_encode.build_encode_plan(tools, replace(normal, sample_seconds=15))
    command = sample.items[0].command
    assert command[command.index("-t") + 1] == "15"
    assert sample.items[0].output != full.items[0].output
    assert "sample_software" in sample.items[0].output.name
    assert "-t" not in full.items[0].command
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("options", [{"sample_seconds": 0}, {"sample_seconds": True},
                                    {"sample_seconds": 15, "target_size_mib": 10},
                                    {"sample_seconds": 15, "encode_mode": "keyframe_cut"}])
def test_incompatible_sample_settings_are_rejected_before_start(tmp_path, options):
    source = tmp_path / "clip.mp4"
    request = video_encode.VideoEncodeRequest((source,), tmp_path, "hevc", 20, **options)
    with pytest.raises(ValueError, match="試し変換"):
        video_encode.build_encode_plan(video_encode.FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")), request)
