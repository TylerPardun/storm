import time

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from ui.studio.writer import FrameWriter


def _frames(n, w=320, h=180):
    for i in range(n):
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(20 + 20 * i, 40, 60))
        yield img


def _run(writer, frames, timeout=20):
    result = {}
    writer.finished.connect(lambda ok, msg: result.update(ok=ok, msg=msg))
    pending = list(frames)

    def push():
        while pending:
            if not writer.write(pending[0]):
                return
            pending.pop(0)
        writer.finish()
    writer.ready.connect(push)
    push()
    end = time.monotonic() + timeout
    while "ok" not in result and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    return result


def test_png_frames_are_numbered_files(tmp_path):
    out = tmp_path / "clip"
    result = _run(FrameWriter(out, "png", (320, 180), 24), _frames(3))
    assert result["ok"] and sorted(p.name for p in out.iterdir()) == ["clip_00000.png", "clip_00001.png", "clip_00002.png"]


def test_gif_is_an_animation(tmp_path):
    from PIL import Image
    out = tmp_path / "clip.gif"
    result = _run(FrameWriter(out, "gif", (320, 180), 10), _frames(4))
    assert result["ok"]
    gif = Image.open(out)
    assert gif.n_frames == 4 and gif.size == (320, 180)


def test_mp4_is_written_with_qts_own_encoder(tmp_path):
    out = tmp_path / "clip.mp4"
    result = _run(FrameWriter(out, "mp4", (320, 180), 24), _frames(12))
    assert result["ok"], result
    assert out.stat().st_size > 1000 and out.read_bytes()[4:8] == b"ftyp"
