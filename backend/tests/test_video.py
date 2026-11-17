"""Embedded videos: link conversion and the sanitiser that only keeps known video players."""

import pytest

from app.articles.content import sanitize_html
from app.articles.video import embed_url


@pytest.mark.parametrize("link,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
    ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
    ("youtube.com/live/dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
    ("https://vimeo.com/76979871", "https://player.vimeo.com/video/76979871"),
    ("https://www.dailymotion.com/video/x8abc12", "https://www.dailymotion.com/embed/video/x8abc12"),
    ("https://www.facebook.com/jamunatelevision/videos/1234567890/",
     "https://www.facebook.com/plugins/video.php?href=https%3A%2F%2Fwww.facebook.com%2Fjamunatelevision%2Fvideos%2F1234567890%2F&show_text=false"),
    ("https://example.com/video.mp4", None),
    ("javascript:alert(1)", None),
])
def test_embed_url(link, expected):
    assert embed_url(link) == expected


def test_sanitiser_keeps_known_players_only():
    good = ('<div data-video-embed=""><iframe src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ" '
            'allow="camera; microphone" allowfullscreen="true" onload="x()"></iframe></div>')
    out = sanitize_html(good)
    assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"' in out
    assert "camera" not in out and "onload" not in out
    assert "<div data-video-embed" in out

    for bad in ('<iframe src="https://evil.example.com/embed/x"></iframe>',
                '<iframe src="javascript:alert(1)"></iframe>',
                '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
                '<iframe src="https://www.youtube.com.evil.com/embed/abcdefgh"></iframe>'):
        assert "<iframe" not in sanitize_html(bad), bad
