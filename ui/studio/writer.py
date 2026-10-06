"""Write a movie's frames: MP4 (H.264, through Qt Multimedia's own FFmpeg --
no extra dependency), an animated GIF (Pillow), or numbered PNG files.

Frames are QImages, all the same size, handed over with write(); the MP4
encoder can push back (Qt's QVideoFrameInput), so write() returns False when
it should be called again with the same frame after `ready` fires. finish()
completes the file; `finished(ok, message)` reports the result.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QSize, QUrl, pyqtSignal
from PyQt6.QtGui import QImage

from core.studio import GIF_MAX_WIDTH


class FrameWriter(QObject):
    ready = pyqtSignal()                 # MP4: can take another frame
    finished = pyqtSignal(bool, str)     # ok, message

    def __init__(self, path: Path, fmt: str, size: tuple[int, int], fps: int, parent=None):
        super().__init__(parent)
        self.path, self.fmt, self.size, self.fps = Path(path), fmt, size, fps
        self.count = 0
        self._gif_frames = []
        self._recorder = None
        if fmt == "mp4":
            self._start_mp4()
        elif fmt == "png":
            self.path.mkdir(parents=True, exist_ok=True)

    # ---- MP4 ---------------------------------------------------------------
    def _start_mp4(self) -> None:
        from PyQt6 import QtMultimedia as M
        self._M = M
        w, h = self.size
        self._session = M.QMediaCaptureSession(self)
        self._input = M.QVideoFrameInput(self)
        self._session.setVideoFrameInput(self._input)
        self._recorder = M.QMediaRecorder(self)
        self._session.setRecorder(self._recorder)
        fmt = M.QMediaFormat(M.QMediaFormat.FileFormat.MPEG4)
        fmt.setVideoCodec(M.QMediaFormat.VideoCodec.H264)
        self._recorder.setMediaFormat(fmt)
        self._recorder.setVideoResolution(QSize(w, h))
        self._recorder.setVideoFrameRate(self.fps)
        self._recorder.setQuality(M.QMediaRecorder.Quality.VeryHighQuality)
        self._recorder.setOutputLocation(QUrl.fromLocalFile(str(self.path)))
        self._errors: list[str] = []
        self._recorder.errorOccurred.connect(lambda _e, text: self._errors.append(text))
        self._recorder.recorderStateChanged.connect(self._on_state)
        self._input.readyToSendVideoFrame.connect(self.ready.emit)
        self._stopping = False
        self._recorder.record()

    def _on_state(self, state) -> None:
        if state == self._M.QMediaRecorder.RecorderState.StoppedState and self._stopping:
            ok = not self._errors and self.path.exists() and self.path.stat().st_size > 0
            self.finished.emit(ok, f"{self.count} frames → {self.path.name}" if ok
                               else "; ".join(self._errors) or "the video could not be written")

    # ---- frames ----------------------------------------------------------------
    def write(self, image: QImage) -> bool:
        """Add the next frame; False (MP4 only): try again after `ready`."""
        if self.fmt == "mp4":
            M = self._M
            frame = M.QVideoFrame(image.convertToFormat(QImage.Format.Format_RGBX8888))
            frame.setStartTime(int(self.count * 1e6 / self.fps))
            frame.setEndTime(int((self.count + 1) * 1e6 / self.fps))
            if not self._input.sendVideoFrame(frame):
                return False
        elif self.fmt == "gif":
            from PIL import Image
            img = image.convertToFormat(QImage.Format.Format_RGB888)
            pil = Image.frombuffer("RGB", (img.width(), img.height()),
                                   bytes(img.constBits().asarray(img.sizeInBytes())), "raw", "RGB",
                                   img.bytesPerLine(), 1)
            if pil.width > GIF_MAX_WIDTH:
                pil = pil.resize((GIF_MAX_WIDTH, round(pil.height * GIF_MAX_WIDTH / pil.width)), Image.LANCZOS)
            self._gif_frames.append(pil.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
        else:
            image.save(str(self.path / f"{self.path.name}_{self.count:05d}.png"), "PNG")
        self.count += 1
        return True

    def finish(self) -> None:
        if self.fmt == "mp4":
            self._stopping = True
            self._recorder.stop()
            return
        if self.fmt == "gif":
            if not self._gif_frames:
                self.finished.emit(False, "no frames")
                return
            first, rest = self._gif_frames[0], self._gif_frames[1:]
            first.save(self.path, save_all=True, append_images=rest, loop=0,
                       duration=round(1000 / self.fps), optimize=False, disposal=1)
            self._gif_frames = []
            self.finished.emit(True, f"{self.count} frames → {self.path.name}")
            return
        self.finished.emit(True, f"{self.count} PNG frames in {self.path.name}/")

    def cancel(self) -> None:
        self._gif_frames = []
        if self.fmt == "mp4" and self._recorder is not None:
            self._stopping = False
            self._recorder.stop()
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
