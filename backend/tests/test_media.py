from app.services.media import MediaService


def test_output_extension_uses_ts_for_mpegts() -> None:
    assert MediaService.output_extension("mpegts") == "ts"


def test_burn_encode_args_use_webm_compatible_codecs() -> None:
    args = MediaService._burn_encode_args("webm")
    assert "-c:v" in args and "libvpx-vp9" in args
    assert "-c:a" in args and "libopus" in args
