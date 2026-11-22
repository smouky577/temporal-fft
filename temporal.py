"""Temporal FFT for a whole clip.

One real FFT along time, per pixel and color channel. Highpass and bandpass
keep the original frame and add the amplified motion. Lowpass keeps the
slow component itself.
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
import ctypes

import cv2
import numpy as np


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


def _row_line(done: int, rows: int, lang: str) -> str:
    if lang == "en":
        return f"Computing row {done}/{rows}"
    return f"Смята ред {done}/{rows}"


def chunk_plan(frames: int, height: int, width: int, free_bytes: int) -> tuple[bool, int]:
    # float32 frames + complex spectrum + inverse transform + temporaries
    per = max(1, height * width * 3)
    need = per * max(frames, 1) * 20 + 2 * 1024**3
    if frames > 0 and need <= free_bytes:
        return False, frames
    room = min(max(free_bytes // 2, per * 8), 8 * 1024**3)
    chunk = max(8, int(room // per))
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
        try:
            handle.close()
        except Exception:
            pass
        try:
            mapped._mmap = None
        except Exception:
            pass


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel():
        raise Stopped()


def _band_size(count: int, height: int, width: int, channels: int, max_band: int | None) -> int:
    row_bytes = max(1, int(count) * int(width) * int(channels) * 4)
    band = max(1, min(int(height), (64 * 1024 * 1024) // row_bytes))
    if max_band is not None:
        band = max(1, min(band, int(max_band)))
    if band > 1 and band % 2:
        band -= 1
    return max(1, band)


def _freq_index(count: int, fps: float, low: float, high: float) -> np.ndarray:
    freqs = np.fft.rfftfreq(count, d=1.0 / fps)
    if high > low:
        chosen = np.flatnonzero((freqs >= low) & (freqs <= high))
        if chosen.size == 0:
            middle = (float(low) + float(high)) * 0.5
            chosen = np.array([int(np.argmin(np.abs(freqs - middle)))], dtype=np.int64)
        return np.asarray(chosen, dtype=np.int64)
    return np.arange(freqs.size, dtype=np.int64)


def _histogram_ref(point: int, hist: np.ndarray, log_lo: float, log_hi: float, min_pos: float) -> float:
    positive = int(hist.sum())
    total = int(point) + positive
    if total <= 0 or positive <= 0:
        return 1.0
    if total == 1:
        return 1.0 if min_pos <= 1e-8 else float(min_pos)
    target = 0.995 * (total - 1)
    lower = int(np.floor(target))
    upper = int(np.ceil(target))
    frac = target - lower
    if upper < point:
        return 1.0
    if lower < point:
        value = frac * float(min_pos)
        return 1.0 if value <= 1e-8 else float(value)
    rank = target - point
    width = (log_hi - log_lo) / float(hist.size)
    running = 0.0
    estimate = log_hi
    for index, count in enumerate(hist.tolist()):
        count = int(count)
        if count <= 0:
            continue
        nxt = running + count
        if rank < nxt:
            estimate = log_lo + (index + (rank - running) / count) * width
            break
        running = nxt
    ref = float(np.exp(estimate))
    if ref <= 1e-8:
        return 1.0
    return ref


def _spectrum_stored(src, dst, fps, low, high, gain, cancel, on_band, max_band) -> None:
    count, height, width, channels = src.shape
    freqs = np.fft.rfftfreq(count, d=1.0 / fps)
    keep = _freq_index(count, fps, low, high)
    kept = int(keep.size)
    drop_dc = kept > 1 and float(freqs[int(keep[0])]) <= 1e-8
    band = _band_size(count, height, width, channels, max_band)
    folder = tempfile.mkdtemp(prefix="temporal-fft-mag-")
    mag = None
    try:
        mag = np.memmap(
            os.path.join(folder, "mag.dat"),
            dtype=np.float32,
            mode="w+",
            shape=(kept, height, width, channels),
        )
        # Log bins: a linear 4096-bin grid smears the quiet tail and the
        # scaled frame drifts by more than a couple of levels.
        bins = 4096
        point_cut = 1e-8
        mag_cap = float(np.log1p(float(count) * 255.0)) + 1.0
        if mag_cap <= point_cut:
            mag_cap = point_cut * 10.0
        log_lo = float(np.log(point_cut))
        log_hi = float(np.log(mag_cap))
        hist = np.zeros(bins, dtype=np.int64)
        point = 0
        min_pos = None
        for top in range(0, height, band):
            _check_cancel(cancel)
            bottom = min(height, top + band)
            slab = np.array(src[:, top:bottom], dtype=np.float32, copy=True)
            spectrum = np.fft.rfft(slab, axis=0)
            part = np.log1p(np.abs(spectrum[keep])).astype(np.float32, copy=False)
            mag[:, top:bottom] = part
            sample = part[1:] if drop_dc else part
            if sample.size:
                point += int(np.count_nonzero(sample <= point_cut))
                positive = sample[sample > point_cut]
                if positive.size:
                    smallest = float(positive.min())
                    min_pos = smallest if min_pos is None else min(min_pos, smallest)
                    logs = np.log(np.clip(np.asarray(positive, dtype=np.float64), point_cut, mag_cap))
                    hist += np.histogram(logs, bins=bins, range=(log_lo, log_hi))[0]
            del slab, spectrum, part, sample
            if on_band is not None:
                on_band(bottom, height)
        ref = _histogram_ref(point, hist, log_lo, log_hi, 0.0 if min_pos is None else min_pos)
        scale = gain if gain > 0 else 1.0
        for top in range(0, height, band):
            _check_cancel(cancel)
            bottom = min(height, top + band)
            part = np.array(mag[:, top:bottom], dtype=np.float64, copy=True)
            dst[:, top:bottom] = (np.clip(part / ref * scale, 0.0, 1.0) * 255.0).astype(np.uint8)
            del part
            if on_band is not None:
                on_band(bottom, height)
        mag.flush()
    finally:
        _drop_memmap(mag)
        mag = None
        gc.collect()
        shutil.rmtree(folder, ignore_errors=True)


def _filter_stored(src, dst, fps, mode, cutoff, low, high, gain, colors, cancel, on_band, max_band=None) -> None:
    if mode == "spectrum":
        _spectrum_stored(src, dst, fps, low, high, gain, cancel, on_band, max_band)
        return
    count, height, width, channels = src.shape
    band = _band_size(count, height, width, channels, max_band)
    for top in range(0, height, band):
        _check_cancel(cancel)
        bottom = min(height, top + band)
        slab = np.array(src[:, top:bottom], dtype=np.float32, copy=True)
        filtered = filter_clip(slab, fps, mode, cutoff, low, high, gain, colors)
        dst[:, top:bottom] = filtered
        del slab, filtered
        if on_band is not None:
            on_band(bottom, height)


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
    """Filter every frame along time, in row strips, and write one file.

    chunk_frames and overlap_frames are accepted and ignored.
    """
    del chunk_frames, overlap_frames
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(say_error(lang, "open"))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    if fps < 1 or fps > 240:
        fps = 30.0
    reported = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    limit = None if max_seconds is None else max(4, int(max_seconds * fps))
    total = reported if limit is None else (min(reported, limit) if reported else limit)

    ok, first = capture.read()
    if not ok or first is None:
        capture.release()
        raise RuntimeError(say_error(lang, "no_frames"))
    first = _fit_width(first, max_width)
    height, width = first.shape[:2]
    ext = os.path.splitext(dest)[1].lower()
    code = "XVID" if ext == ".avi" else "mp4v"
    writer = cv2.VideoWriter(dest, cv2.VideoWriter_fourcc(*code), float(fps), (width, height))
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(say_error(lang, "no_writer"))

    work = tempfile.mkdtemp(prefix="temporal-fft-")
    src = None
    dst = None
    writer_open = True

    def say(text: str) -> None:
        if progress is not None:
            progress(text)

    try:
        raw_path = os.path.join(work, "in.dat")
        seen = 0
        pending = first
        with open(raw_path, "wb") as raw:
            while True:
                _check_cancel(cancel)
                if limit is not None and seen >= limit:
                    break
                if pending is not None:
                    frame = pending
                    pending = None
                else:
                    got, frame = capture.read()
                    if not got or frame is None:
                        break
                    frame = _fit_width(frame, max_width)
                    if frame.shape[0] != height or frame.shape[1] != width:
                        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                raw.write(np.ascontiguousarray(frame).tobytes())
                seen += 1
                if seen == 1 or seen % 8 == 0:
                    say(_progress_line("read", seen, total, None, None, fps, lang))
        if seen > 1 and seen % 8 != 0:
            say(_progress_line("read", seen, total, None, None, fps, lang))
        if seen < 4:
            raise RuntimeError(say_error(lang, "too_few"))

        src = np.memmap(raw_path, dtype=np.uint8, mode="r", shape=(seen, height, width, 3))
        out_count = int(_freq_index(seen, fps, low, high).size) if mode == "spectrum" else seen
        dst = np.memmap(
            os.path.join(work, "out.dat"),
            dtype=np.uint8,
            mode="w+",
            shape=(out_count, height, width, 3),
        )

        def on_band(done: int, rows: int) -> None:
            say(_row_line(done, rows, lang))

        _filter_stored(
            src,
            dst,
            fps,
            mode,
            cutoff,
            low,
            high,
            gain,
            bool(colors or gray),
            cancel,
            on_band,
        )
        dst.flush()
        written = 0
        for index in range(out_count):
            _check_cancel(cancel)
            frame = np.array(dst[index], copy=True)
            if gray:
                mono = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                frame = cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)
            writer.write(np.ascontiguousarray(frame))
            written += 1
        if written < 1:
            raise RuntimeError(say_error(lang, "too_few"))
        return written, fps
    except Exception:
        writer.release()
        writer_open = False
        try:
            os.remove(dest)
        except OSError:
            pass
        raise
    finally:
        if writer_open:
            writer.release()
        capture.release()
        _drop_memmap(src)
        _drop_memmap(dst)
        src = None
        dst = None
        gc.collect()
        shutil.rmtree(work, ignore_errors=True)


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

    def disk_frames(video, clip_fps, mode, cut, lo, hi, amp, use_colors=False, max_band=2):
        bench = tempfile.mkdtemp(prefix="temporal-fft-test-")
        mapped_src = mapped_dst = None
        try:
            frames, frame_h, frame_w, _ = video.shape
            mapped_src = np.memmap(
                os.path.join(bench, "src.dat"), dtype=np.uint8, mode="w+", shape=video.shape
            )
            mapped_src[:] = video
            mapped_src.flush()
            out_count = frames
            if mode == "spectrum":
                out_count = int(_freq_index(frames, clip_fps, lo, hi).size)
            mapped_dst = np.memmap(
                os.path.join(bench, "dst.dat"),
                dtype=np.uint8,
                mode="w+",
                shape=(out_count, frame_h, frame_w, 3),
            )
            _filter_stored(
                mapped_src, mapped_dst, clip_fps, mode, cut, lo, hi, amp, use_colors, None, None, max_band
            )
            mapped_dst.flush()
            return np.array(mapped_dst)
        finally:
            _drop_memmap(mapped_src)
            _drop_memmap(mapped_dst)
            gc.collect()
            shutil.rmtree(bench, ignore_errors=True)

    def same_as_ram(video, clip_fps, mode, cut, lo, hi, amp, use_colors=False):
        ram = filter_clip(video.astype(np.float32), clip_fps, mode, cut, lo, hi, amp, use_colors)
        disk = disk_frames(video, clip_fps, mode, cut, lo, hi, amp, use_colors, 2)
        assert ram.shape == disk.shape, (mode, ram.shape, disk.shape)
        delta = int(np.max(np.abs(ram.astype(np.int16) - disk.astype(np.int16))))
        assert delta <= 2, (mode, use_colors, delta)

    count = 32
    fps = 16.0
    wave = np.sin(2 * np.pi * 4 * np.arange(count) / count)
    made = np.zeros((count, 8, 6, 3), np.float32)
    level = np.zeros((8, 1, 1), np.float32)
    level[:4] = 28
    level[4:] = 4
    made[:] = 128 + level * wave[:, None, None, None]
    made += np.linspace(-6, 6, 6, dtype=np.float32)[None, None, :, None]
    made = np.clip(np.rint(made), 0, 255).astype(np.uint8)
    for mode, cut, lo, hi, amp, use_colors in (
        ("highpass", 1.0, 0.0, 0.0, 2.0, False),
        ("lowpass", 0.5, 0.0, 0.0, 1.0, False),
        ("bandpass", 0.0, 1.0, 3.0, 2.0, False),
        ("spectrum", 0.0, 0.0, 0.0, 1.0, False),
        ("spectrum", 0.0, 0.0, 0.0, 2.0, False),
        ("highpass", 1.0, 0.0, 0.0, 2.0, True),
        ("bandpass", 0.0, 1.0, 3.0, 1.0, True),
        ("spectrum", 0.0, 1.5, 2.5, 1.0, False),
    ):
        same_as_ram(made, fps, mode, cut, lo, hi, amp, use_colors)

    band_freqs = np.fft.rfftfreq(count, d=1.0 / fps)
    kept = int(np.count_nonzero((band_freqs >= 1.5) & (band_freqs <= 2.5)))
    narrow = disk_frames(made, fps, "spectrum", 0.0, 1.5, 2.5, 1.0, False, 2)
    assert narrow.shape[0] == kept and kept >= 1
    assert int(_freq_index(count, fps, 10.0, 12.0)[0]) == int(np.argmin(np.abs(band_freqs - 11.0)))
    assert disk_frames(made, fps, "spectrum", 0.0, 10.0, 12.0, 1.0).shape[0] == 1

    ram = filter_clip(made.astype(np.float32), fps, "spectrum", 0, 0, 0, 1)
    strips = np.concatenate(
        [
            filter_clip(made[:, top : top + 2].astype(np.float32), fps, "spectrum", 0, 0, 0, 1)
            for top in range(0, made.shape[1], 2)
        ],
        axis=1,
    )
    assert int(np.max(np.abs(ram.astype(np.int16) - strips.astype(np.int16)))) > 2
    assert int(ram[4, :4].max()) > int(ram[4, 4:].max()) + 20

    still = np.full((32, 8, 8, 3), 90, np.uint8)
    still = still.copy()
    still[:, 0, 0, 0] = np.clip(90 + 15 * np.sin(2 * np.pi * np.arange(32) / 32), 0, 255).astype(np.uint8)
    same_as_ram(still, 16.0, "spectrum", 0, 0, 0, 1)

    rng = np.random.default_rng(0)
    noise = rng.integers(0, 256, size=(24, 10, 8, 3), dtype=np.uint8)
    for mode, cut, lo, hi, amp in (
        ("highpass", 1.0, 0.0, 0.0, 2.0),
        ("lowpass", 0.5, 0.0, 0.0, 1.0),
        ("bandpass", 0.0, 1.0, 3.0, 1.0),
        ("spectrum", 0.0, 0.0, 0.0, 1.0),
    ):
        same_as_ram(noise, 12.0, mode, cut, lo, hi, amp)

    sooner, _ = chunk_plan(200, 270, 480, 3 * 1024**3)
    assert sooner is True

    spec_in = os.path.join(folder, "spec-in.mp4")
    spec_out = os.path.join(folder, "spec-out.mp4")
    write_video(spec_in, made, fps)
    notes = []
    produced, produced_fps = render_clip(
        spec_in,
        spec_out,
        "spectrum",
        0,
        1.5,
        2.5,
        1,
        None,
        None,
        progress=notes.append,
    )
    used = np.fft.rfftfreq(made.shape[0], d=1.0 / produced_fps)
    expect_n = int(np.count_nonzero((used >= 1.5) & (used <= 2.5)))
    assert 1 <= produced < made.shape[0] // 2 + 1
    assert produced == expect_n
    assert any(line.startswith("Чете ") for line in notes)
    assert sum(line.startswith("Смята ред ") for line in notes) >= 2
    reader = cv2.VideoCapture(spec_out)
    seen_out = 0
    while True:
        ok, _frame = reader.read()
        if not ok:
            break
        seen_out += 1
    reader.release()
    assert seen_out == produced
    print("temporal tests: ok")


if __name__ == "__main__":
    _self_test()
