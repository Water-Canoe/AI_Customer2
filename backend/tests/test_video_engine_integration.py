from pathlib import Path


def test_content_asset_audio_formats_can_be_used_as_bgm(tmp_path: Path) -> None:
    # 背景音乐应接受内容资产层已经允许导入的全部音频格式。
    from app.video_engine.config import config
    from app.video_engine.services import video

    original_directory = config.app.get("bgm_directory")
    try:
        config.app["bgm_directory"] = str(tmp_path)
        for suffix in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"):
            audio_path = tmp_path / f"background{suffix}"
            audio_path.write_bytes(b"test")
            assert video.get_bgm_file("custom", str(audio_path)) == str(audio_path.resolve())
    finally:
        config.app["bgm_directory"] = original_directory
