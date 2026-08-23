"""View-once detection helpers."""

from userbot.detect import is_view_once, view_once_kind


def test_is_view_once():
    assert is_view_once(has_media=True, ttl_seconds=5) is True
    assert is_view_once(has_media=True, ttl_seconds=None) is False   # normal media
    assert is_view_once(has_media=False, ttl_seconds=5) is False     # no media
    assert is_view_once(has_media=False, ttl_seconds=None) is False


def test_view_once_kind():
    assert view_once_kind(is_photo=True, is_voice=False, is_video=False) == "photo"
    assert view_once_kind(is_photo=False, is_voice=True, is_video=False) == "voice"
    assert view_once_kind(is_photo=False, is_voice=False, is_video=True) == "video"
    assert view_once_kind(is_photo=False, is_voice=False, is_video=False) == "media"
