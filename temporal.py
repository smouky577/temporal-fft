"""Temporal FFT for a whole clip.

One real FFT along time, per pixel and color channel. Highpass and bandpass
keep the original frame and add the amplified motion. Lowpass keeps the
slow component itself.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import ctypes

import cv2
import numpy as np

CHUNK_SECONDS = 3.0
OVERLAP_SECONDS = 0.5


class Stopped(Exception):
    pass


def say_error(lang: str, key: str) -> str:
    table = {
        "open": ("Не мога да отворя клипа.", "Cannot open this clip."),
        "no_frames": ("Клипът няма кадри.", "The clip has no frames."),
        "too_few": ("Твърде малко кадри.", "Too few frames."),
        "no_writer": ("Не мога да създам изходния файл.", "Cannot create the output file."),
    }
    bg, en = table[key]
    return en if lang == "en" else bg


def _free_ram() -> int:
    class MemoryStatus(ctypes.Structure):
        _fields_ = (
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        )

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 8 * 1024**3
    return int(status.ullAvailPhys)


def _scaled_size(width: int, height: int, max_width: int | None) -> tuple[int, int]:
    if width < 2 or height < 2:
        return 0, 0
    if max_width is not None and width > max_width:
        new_w = max(2, int(max_width) - int(max_width) % 2)
        new_h = max(2, int(round(height * new_w / width / 2.0) * 2))
        return new_h, new_w
    return height - height % 2, width - width % 2


def _progress_line(
    action: str,
    seen: int,
    total: int,
    row: int | None,
    rows: int | None,
    fps: float,
    lang: str = "bg",
) -> str:
    words = {
        "bg": {"read": "Чете", "compute": "Смята", "row": "ред", "to": "до", "sec": "сек"},
        "en": {"read": "Reading", "compute": "Computing", "row": "row", "to": "up to", "sec": "sec"},
    }
    label = words["en" if lang == "en" else "bg"]
    name = label.get(action, action)
    if total:
        text = f"{name} {seen}/{total}"
    else:
        text = f"{name} {seen}"
    if row is not None and rows:
        text += f", {label['row']} {row}/{rows}"
    if fps > 0 and seen > 0:
        text += f" · {label['to']} {seen / fps:.1f} {label['sec']}"
    return text


def chunk_plan(frames: int, height: int, width: int, free_bytes: int) -> tuple[bool, int]:
    per = max(1, height * width * 3)
    need = per * max(frames, 1) * 6 + 2 * 1024**3
    if frames > 0 and need <= free_bytes:
        return False, frames
    room = min(max(free_bytes // 2, per * 8), 8 * 1024**3)
    chunk = max(8, int(room // per))
    if frames > 0 and chunk >= frames:
        return False, frames
    return True, chunk


def clip_needs_chunks(path: str, max_width: int | None, max_seconds: float | None) -> tuple[bool, int]:
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return False, 0
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    capture.release()
    if fps < 1 or fps > 240:
        fps = 30.0
    height, width = _scaled_size(width, height, max_width)
    if width < 2 or height < 2:
        return True, 8
    limit = None if max_seconds is None else max(4, int(max_seconds * fps))
    if count > 0 and limit is not None:
        frames = min(count, limit)
    else:
        frames = count
    return chunk_plan(frames, height, width, _free_ram())


def load_frames(path: str, max_width: int | None, max_seconds: float | None, progress=None, cancel=None, lang: str = "bg"):
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise RuntimeError(say_error(lang, "open"))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    if fps < 1 or fps > 240:
        fps = 30.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    limit = None if max_seconds is None else max(4, int(max_seconds * fps))
    frames = []
    while True:
        if cancel is not None and cancel():
            break
        if limit is not None and len(frames) >= limit:
            break
        ok, frame = capture.read()
        if not ok:
            break
        frame = _fit_width(frame, max_width)
        frames.append(frame)
        if progress is not None and (len(frames) == 1 or len(frames) % 8 == 0):
            progress(len(frames), total)
    capture.release()
    if cancel is not None and cancel():
        raise Stopped()
    if len(frames) < 4:
        raise RuntimeError(say_error(lang, "too_few"))
    return np.stack(frames).astype(np.float32), fps


def _fit_width(frame: np.ndarray, max_width: int | None) -> np.ndarray:
    height, width = frame.shape[:2]
    if max_width is not None and width > max_width:
        new_h = int(round(height * max_width / width / 2) * 2)
        new_h = max(2, new_h)
        frame = cv2.resize(frame, (max_width - max_width % 2, new_h), interpolation=cv2.INTER_AREA)
        height, width = frame.shape[:2]
    even_h = height - height % 2
    even_w = width - width % 2
    if even_h != height or even_w != width:
        frame = frame[:even_h, :even_w]
    return frame


def filter_clip(
    video: np.ndarray,
    fps: float,
    mode: str,
    cutoff: float,
    low: float,
    high: float,
    gain: float,
    colors: bool = False,
) -> np.ndarray:
    count = video.shape[0]
    spectrum = np.fft.rfft(video, axis=0)
    freqs = np.fft.rfftfreq(count, d=1.0 / fps)
    if mode == "spectrum":
        mag = np.log1p(np.abs(spectrum))
        if high > low:
            keep = (freqs >= low) & (freqs <= high)
            if np.any(keep):
                mag = mag[keep]
                kept = freqs[keep]
            else:
                mag = mag[:1]
                kept = freqs[:1]
        else:
            kept = freqs
        sample = mag[1:] if mag.shape[0] > 1 and kept[0] <= 1e-8 else mag
        ref = np.percentile(sample, 99.5) if sample.size else 1.0
        if ref <= 1e-8:
            ref = 1.0
        scale = gain if gain > 0 else 1.0
        return (np.clip(mag / ref * scale, 0, 1) * 255).astype(np.uint8)

    if mode == "highpass":
        mask = freqs >= cutoff
    elif mode == "lowpass":
        mask = freqs <= cutoff
    else:
        mask = (freqs >= low) & (freqs <= high)
    spectrum *= mask[:, None, None, None]
    result = np.fft.irfft(spectrum, n=count, axis=0)
    if mode == "lowpass":
        out = np.clip(result, 0, 255)
    elif colors:
        out = np.clip(video + gain * result, 0, 255)
    else:
        out = np.clip(128 + gain * result, 0, 255)
    return out.astype(np.uint8)


def _drop_memmap(mapped) -> None:
    if mapped is None:
        return
    try:
        mapped.flush()
    except Exception:
        pass
    handle = getattr(mapped, "_mmap", None)
    if handle is not None:
        handle.close()


def _filter_rows(src, dst, fps, mode, cutoff, low, high, gain, cancel, progress, colors: bool = False) -> None:
    count, height, width, channels = src.shape
    row_bytes = max(1, count * width * channels * 4)
    band = max(1, min(height, (64 * 1024 * 1024) // row_bytes))
    if band > 1 and band % 2:
        band -= 1
    for top in range(0, height, band):
        if cancel is not None and cancel():
            raise Stopped()
        bottom = min(height, top + band)
        slab = np.array(src[:, top:bottom], dtype=np.float32, copy=True)
        filtered = filter_clip(slab, fps, mode, cutoff, low, high, gain, colors)
        dst[:, top:bottom] = filtered
        del slab, filtered
        if progress is not None:
            progress(bottom, height)


def render_clip(
    path: str,
    dest: str,
    mode: str,
    cutoff: float,
    low: float,
    high: float,
    gain: float,
    max_width: int | None,
    max_seconds: float | None,
    gray: bool = False,
    colors: bool = False,
    progress=None,
    cancel=None,
    lang: str = "bg",
    chunk_frames: int | None = None,
    overlap_frames: int | None = None,
) -> tuple[int, float]:
    """Filter a clip in short windows and write one continuous file."""
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise RuntimeError(say_error(lang, "open"))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    if fps < 1 or fps > 240:
        fps = 30.0
    reported = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    limit = None if max_seconds is None else max(4, int(max_seconds * fps))
    total = reported if limit is None else (min(reported, limit) if reported else limit)
    if chunk_frames:
        chunk = max(4, int(chunk_frames))
    else:
        chunk = max(4, int(round(CHUNK_SECONDS * fps)))
    if mode == "spectrum":
        overlap = 0
    elif overlap_frames is not None:
        overlap = max(0, int(overlap_frames))
    else:
        overlap = max(0, int(round(OVERLAP_SECONDS * fps)))
    if overlap >= chunk // 2:
        overlap = max(0, chunk // 4)
    if mode == "spectrum":
        overlap = 0

    ok, first = capture.read()
    if not ok or first is None:
        capture.release()
        raise RuntimeError(say_error(lang, "no_frames"))
    first = _fit_width(first, max_width)
    height, width = first.shape[:2]
    pending = [first]
    ext = os.path.splitext(dest)[1].lower()
    code = "XVID" if ext == ".avi" else "mp4v"
    writer = cv2.VideoWriter(dest, cv2.VideoWriter_fourcc(*code), float(fps), (width, height))
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(say_error(lang, "no_writer"))

    work = tempfile.mkdtemp(prefix="temporal-fft-")
    written = 0
    seen = 0
    carry = None
    prev_tail = None
    stopped = False

    def say(text: str) -> None:
        if progress is not None:
            progress(text)

    def read_frame():
        if pending:
            return pending.pop(0)
        if cancel is not None and cancel():
            raise Stopped()
        got, frame = capture.read()
        if not got or frame is None:
            return None
        frame = _fit_width(frame, max_width)
        if frame.shape[0] != height or frame.shape[1] != width:
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        return frame

    def emit(frame: np.ndarray) -> None:
        nonlocal written
        if gray:
            mono = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
        writer.write(np.ascontiguousarray(frame))
        written += 1

    try:
        while not stopped:
            if cancel is not None and cancel():
                raise Stopped()
            raw_path = os.path.join(work, "in.dat")
            out_path = os.path.join(work, "out.dat")
            count = 0
            with open(raw_path, "wb") as raw:
                if carry is not None:
                    raw.write(np.ascontiguousarray(carry).tobytes())
                    count = int(carry.shape[0])
                while count < chunk:
                    if limit is not None and seen >= limit:
                        break
                    frame = read_frame()
                    if frame is None:
                        stopped = True
                        break
                    raw.write(np.ascontiguousarray(frame).tobytes())
                    count += 1
                    seen += 1
                    if seen == 1 or seen % 8 == 0:
                        say(_progress_line("read", seen, total, None, None, fps, lang))
            if count < 4:
                if prev_tail is not None:
                    for frame in prev_tail:
                        emit(np.array(frame))
                    prev_tail = None
                if written == 0:
                    raise RuntimeError(say_error(lang, "too_few"))
                break
            is_last = stopped or count < chunk or (limit is not None and seen >= limit)
            src = np.memmap(raw_path, dtype=np.uint8, mode="r", shape=(count, height, width, 3))
            out_count = count // 2 + 1 if mode == "spectrum" else count
            dst = np.memmap(out_path, dtype=np.uint8, mode="w+", shape=(out_count, height, width, 3))
            try:
                say(_progress_line("compute", seen, total, None, None, fps, lang))

                def band_progress(done_rows: int, rows: int) -> None:
                    say(_progress_line("compute", seen, total, done_rows, rows, fps, lang))

                _filter_rows(src, dst, fps, mode, cutoff, low, high, gain, cancel, band_progress, colors or gray)
                dst.flush()
                if overlap and not is_last:
                    carry = np.array(src[count - overlap : count])
                else:
                    carry = None
            finally:
                _drop_memmap(src)
                src = None
            try:
                ov = 0 if prev_tail is None else min(int(prev_tail.shape[0]), out_count)
                for index in range(ov):
                    weight = (index + 1) / (ov + 1)
                    mixed = prev_tail[index].astype(np.float32) * (1.0 - weight)
                    mixed += dst[index].astype(np.float32) * weight
                    emit(np.clip(mixed, 0, 255).astype(np.uint8))
                end = out_count if is_last or overlap <= 0 else max(ov, out_count - overlap)
                for index in range(ov, end):
                    emit(dst[index])
                if not is_last and overlap > 0:
                    prev_tail = np.array(dst[end:out_count])
                else:
                    prev_tail = None
            finally:
                _drop_memmap(dst)
                dst = None
                for leftover in (raw_path, out_path):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass
            if is_last:
                break
    except Exception:
        writer.release()
        capture.release()
        shutil.rmtree(work, ignore_errors=True)
        try:
            os.remove(dest)
        except OSError:
            pass
        raise
    writer.release()
    capture.release()
    shutil.rmtree(work, ignore_errors=True)
    if written < 1:
        raise RuntimeError(say_error(lang, "too_few"))
    return written, fps


def write_video(path: str, frames: np.ndarray, fps: float, progress=None, cancel=None, lang: str = "bg") -> None:
    count, height, width, _ = frames.shape
    ext = os.path.splitext(path)[1].lower()
    code = "XVID" if ext == ".avi" else "mp4v"
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*code), float(fps), (width, height))
    if not writer.isOpened():
        raise RuntimeError(say_error(lang, "no_writer"))
    try:
        for index, frame in enumerate(frames):
            if cancel is not None and cancel():
                raise Stopped()
            writer.write(frame)
            if progress is not None and (index == 0 or index % 8 == 0 or index + 1 == count):
                progress(index + 1, count)
    finally:
        writer.release()


def _self_test() -> None:
    count = 32
    fps = float(count)
    sine = 40 + 3 * np.sin(2 * np.pi * 2 * np.arange(count) / count)
    video = np.broadcast_to(sine[:, None, None, None], (count, 2, 2, 3)).astype(np.float32).copy()
    fast = filter_clip(video, fps, "highpass", 1.5, 0, 0, 2).astype(np.float32)
    expect = 128 + 6 * np.sin(2 * np.pi * 2 * np.arange(count) / count)
    assert np.max(np.abs(fast - expect[:, None, None, None])) < 2
    colored = filter_clip(video, fps, "highpass", 1.5, 0, 0, 2, colors=True).astype(np.float32)
    color_expect = 40 + 9 * np.sin(2 * np.pi * 2 * np.arange(count) / count)
    assert np.max(np.abs(colored - color_expect[:, None, None, None])) < 2

    slow = filter_clip(video, fps, "lowpass", 0.5, 0, 0, 1)
    assert np.all(np.abs(slow.astype(np.int16) - 40) <= 1)

    band = filter_clip(video, fps, "bandpass", 0, 1.5, 2.5, 1).astype(np.float32)
    band_expect = 128 + 3 * np.sin(2 * np.pi * 2 * np.arange(count) / count)
    assert np.max(np.abs(band - band_expect[:, None, None, None])) < 2
    band_color = filter_clip(video, fps, "bandpass", 0, 1.5, 2.5, 1, colors=True).astype(np.float32)
    band_color_expect = 40 + 6 * np.sin(2 * np.pi * 2 * np.arange(count) / count)
    assert np.max(np.abs(band_color - band_color_expect[:, None, None, None])) < 2

    spec = filter_clip(video, fps, "spectrum", 0, 0, 0, 1)
    assert spec.shape[0] == count // 2 + 1
    assert spec.max() > 200
    narrow = filter_clip(video, fps, "spectrum", 0, 1.5, 2.5, 1)
    assert 1 <= narrow.shape[0] < spec.shape[0]
    bright = filter_clip(video, fps, "spectrum", 0, 0, 0, 2)
    assert bright.mean() >= spec.mean()

    import tempfile

    folder = tempfile.mkdtemp()
    sample = os.path.join(folder, "in.mp4")
    out = os.path.join(folder, "out.mp4")
    blank = np.zeros((12, 48, 64, 3), np.uint8)
    blank[:, :, :] = (20, 40, 80)
    for index in range(12):
        blank[index, 10:30, 8 + index : 20 + index] = 220
    write_video(sample, blank, 24)
    loaded, loaded_fps = load_frames(sample, 32, None)
    assert loaded.shape[2] == 32
    assert abs(loaded_fps - 24) < 0.2
    filtered = filter_clip(loaded, loaded_fps, "highpass", 1, 0, 0, 3)
    write_video(out, filtered, loaded_fps)
    chunked, chunk_frames = chunk_plan(1500, 3840, 2160, 16 * 1024**3)
    assert chunked and 8 <= chunk_frames < 1500
    once, whole = chunk_plan(300, 480, 270, 16 * 1024**3)
    assert once is False and whole == 300

    check = cv2.VideoCapture(out)
    ok, frame = check.read()
    check.release()
    assert ok and frame is not None and frame.shape[1] == 32

    pieces = os.path.join(folder, "pieces.mp4")
    longer = np.zeros((20, 24, 32, 3), np.uint8)
    longer[:, :, :] = (30, 60, 90)
    for index in range(20):
        longer[index, 4:16, index : index + 4] = 200
    write_video(os.path.join(folder, "long.mp4"), longer, 10)
    produced, produced_fps = render_clip(
        os.path.join(folder, "long.mp4"),
        pieces,
        "highpass",
        1,
        0,
        0,
        2,
        None,
        None,
        chunk_frames=8,
        overlap_frames=2,
    )
    assert produced == 20
    assert abs(produced_fps - 10) < 0.2
    print("temporal tests: ok")


if __name__ == "__main__":
    _self_test()
