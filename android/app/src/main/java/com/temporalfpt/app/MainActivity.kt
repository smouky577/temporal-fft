package com.temporalfpt.app

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.TextButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.material3.LocalTextStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.io.File
import java.util.ArrayDeque
import java.util.concurrent.Executors
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock
import kotlin.math.roundToInt

private val Bg = Color(0xFF10120F)
private val Panel = Color(0xFF242824)
private val Accent = Color(0xFFD6F25C)
private val Ink = Color(0xFF16180F)
private val Muted = Color(0xFF9AA194)

private fun modeLabel(id: Int): String = when (id) {
    0 -> tr("Бързи", "Fast")
    1 -> tr("Бавни", "Slow")
    2 -> tr("Лента", "Band")
    else -> tr("Спектър", "Spectrum")
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(background = Bg, primary = Accent)) {
                FftScreen(
                    onBusy = { busy ->
                        if (busy) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    },
                )
            }
        }
    }
}

class FftSession(private val context: android.content.Context) {
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "temporal-fft").apply { isDaemon = true }
    }
    private val main = Handler(Looper.getMainLooper())
    private val gate = ReentrantLock()
    @Volatile var cancel = false
    @Volatile private var closed = false
    private var active = 0L
    private var ready = false
    private var store: FrameStore? = null
    var width = 0
    var height = 0
    var outFrames = 0
    var fps = 30f

    fun start(
        uri: Uri,
        mode: Int,
        cutoff: Float,
        low: Float,
        high: Float,
        gain: Float,
        look: Int,
        onProgress: (Float, Float) -> Unit,
        onReady: (Int, Int, Int) -> Unit,
        onDone: (String) -> Unit,
        onError: (String) -> Unit,
    ) {
        cancel = false
        executor.execute {
            var handle = 0L
            var decoder: VideoDecoder? = null
            var encoder: Mp4Encoder? = null
            var preview: FrameStore? = null
            try {
                val check = NativeFilter.selfTest()
                if (check != 0) error(tr("Проверката на сметката не мина ($check).", "The check failed ($check)."))
                dropPreview(true)
                decoder = VideoDecoder(context, uri)
                val metaFps = decoder.fps
                val tenUs = (MAX_SECONDS * 1_000_000f).toLong()
                val pending = ArrayDeque<IntArray>()
                var streak = 0
                var frameW = 0
                var frameH = 0
                var readCount = 0
                var firstUs = Long.MIN_VALUE
                var lastUs = 0L
                var pastTime = false
                var reachedEnd = false
                var ceiling = 2000
                var carryLeft = 0
                var prevTail: Array<IntArray>? = null
                fun coveredSec(): Float {
                    val span = lastUs - firstUs
                    val seconds = if (readCount > 1 && span > 0) span / 1_000_000f else readCount / metaFps
                    return seconds.coerceIn(0f, MAX_SECONDS)
                }
                fun nextPiece(): Decoded {
                    while (!cancel) {
                        when (val item = decoder!!.next()) {
                            is Decoded.Pending -> {
                                streak++
                                if (streak > 80) error(tr("Клипът спря да се чете.", "The clip stopped reading."))
                            }
                            else -> {
                                streak = 0
                                return item
                            }
                        }
                    }
                    return Decoded.End
                }
                fun takeFrame(item: Decoded.Frame): Boolean {
                    if (firstUs == Long.MIN_VALUE) firstUs = item.timeUs
                    val elapsed = item.timeUs - firstUs
                    if (readCount > 0 && elapsed >= tenUs) return false
                    if (readCount > 0 && elapsed <= 0 && readCount >= (metaFps * MAX_SECONDS).roundToInt().coerceAtLeast(4)) return false
                    var frame = scaleMaxWidth(rotateArgb(item.raw, decoder!!.rotation), MAX_WIDTH)
                    if (frameW == 0) {
                        frameW = frame.width
                        frameH = frame.height
                        val file = File(context.cacheDir, "temporal-fft-preview.raw")
                        if (file.exists()) file.delete()
                        preview = FrameStore(file, frameW, frameH)
                    } else if (frame.width != frameW || frame.height != frameH) {
                        frame = fitFrame(frame, frameW, frameH)
                    }
                    pending.add(frame.pixels)
                    readCount++
                    lastUs = item.timeUs
                    val fraction = if (elapsed > 0) 0.55f * elapsed / tenUs else 0.55f * readCount / (metaFps * MAX_SECONDS)
                    main.post { onProgress(fraction.coerceIn(0f, 0.55f), coveredSec()) }
                    return true
                }
                while (!cancel && (!(pastTime || reachedEnd) || pending.size >= 4)) {
                    if ((pastTime || reachedEnd) && pending.size < 4) break
                    val limit = chunkLimit(frameW, frameH, mode).coerceAtMost(ceiling).coerceAtLeast(8)
                    val batch = ArrayList<IntArray>()
                    var fresh = 0
                    while (batch.size < limit && !cancel) {
                        if (pending.isNotEmpty()) {
                            batch.add(pending.removeFirst())
                            if (carryLeft > 0) carryLeft-- else fresh++
                            continue
                        }
                        if (pastTime || reachedEnd) break
                        when (val item = nextPiece()) {
                            is Decoded.Frame -> if (takeFrame(item)) fresh++ else pastTime = true
                            is Decoded.End -> reachedEnd = true
                            is Decoded.Pending -> Unit
                        }
                        if (pastTime || reachedEnd) break
                    }
                    if (cancel) break
                    if (fresh == 0) break
                    if (batch.size < 4) {
                        if (preview == null || preview!!.count == 0) error(tr("Твърде малко кадри.", "Too few frames."))
                        break
                    }
                    val span = lastUs - firstUs
                    val clipFps = if (readCount > 1 && span > 0) {
                        ((readCount - 1) * 1_000_000f / span).coerceIn(1f, 240f)
                    } else {
                        metaFps
                    }
                    val processed = filterChunk(batch, frameW, frameH, clipFps, mode, cutoff, low, high, gain, look) { done, total ->
                        if (total > 0) {
                            val fraction = 0.55f + 0.25f * done / total
                            main.post { onProgress(fraction.coerceIn(0.55f, 0.8f), coveredSec()) }
                        }
                        cancel
                    }
                    handle = processed.handle
                    if (cancel || processed.code == -5) break
                    if (processed.code < 0) {
                        error(
                            when (processed.code) {
                                -2 -> tr("Няма достатъчно памет за този клип.", "Not enough memory for this clip.")
                                -3 -> tr("Твърде малко кадри.", "Too few frames.")
                                else -> tr("Филтърът спря с код ${processed.code}.", "The filter stopped with code ${processed.code}.")
                            },
                        )
                    }
                    val used = processed.used
                    if (used < batch.size) ceiling = used.coerceAtLeast(8)
                    val isLast = (pastTime || reachedEnd || fresh == 0) && used == batch.size
                    var seam = if (mode == 3) 0 else (clipFps * 0.5f).roundToInt()
                    if (seam >= used / 2) seam = used / 4
                    if (seam < 0) seam = 0
                    val saved = preview ?: error(tr("Грешка", "Error"))
                    val scratch = IntArray(frameW * frameH)
                    val tail = prevTail
                    val overlap = if (tail == null) 0 else minOf(tail.size, processed.produced)
                    val end = if (isLast || seam <= 0) processed.produced else maxOf(overlap, processed.produced - seam)
                    val keptTail = ArrayList<IntArray>()
                    for (index in 0 until processed.produced) {
                        NativeFilter.copyFrame(handle, index, scratch)
                        when {
                            tail != null && index < overlap -> {
                                val weight = (index + 1f) / (overlap + 1f)
                                saved.append(blend(tail[index], scratch, weight))
                            }
                            index < end -> saved.append(scratch)
                            else -> keptTail.add(scratch.copyOf())
                        }
                    }
                    if (handle != 0L) {
                        NativeFilter.destroy(handle)
                        handle = 0L
                    }
                    prevTail = if (keptTail.isEmpty()) null else keptTail.toTypedArray()
                    val rebuilt = ArrayDeque<IntArray>()
                    val kept = if (!isLast && seam > 0) seam else 0
                    if (kept > 0) {
                        for (index in used - kept until used) rebuilt.add(batch[index])
                    }
                    for (index in used until batch.size) rebuilt.add(batch[index])
                    pending.clear()
                    rebuilt.forEach { pending.add(it) }
                    carryLeft = kept
                    if (isLast) break
                }
                prevTail?.forEach { frame -> preview?.append(frame) }
                prevTail = null
                decoder.close()
                decoder = null
                if (cancel) {
                    main.post { onDone(tr("Спряно.", "Stopped.")) }
                    return@execute
                }
                val saved = preview
                if (saved == null || saved.count < 1) error(tr("Твърде малко кадри.", "Too few frames."))
                val span = lastUs - firstUs
                val clipFps = if (readCount > 1 && span > 0) {
                    ((readCount - 1) * 1_000_000f / span).coerceIn(1f, 240f)
                } else {
                    metaFps
                }
                var accepted = false
                gate.withLock {
                    if (closed) return@withLock
                    if (active != 0L) {
                        NativeFilter.destroy(active)
                        active = 0L
                    }
                    width = frameW
                    height = frameH
                    outFrames = saved.count
                    fps = clipFps
                    store = saved
                    preview = null
                    ready = true
                    accepted = true
                }
                if (!accepted) {
                    main.post { onDone(tr("Спряно.", "Stopped.")) }
                    return@execute
                }
                val shown = saved.count
                main.post { onReady(shown, frameW, frameH) }
                val pixels = IntArray(frameW * frameH)
                val writer = Mp4Encoder(context, frameW, frameH, clipFps)
                encoder = writer
                writer.start()
                for (index in 0 until shown) {
                    if (cancel) break
                    if (!saved.read(index, pixels)) error(tr("Грешка", "Error"))
                    writer.write(pixels)
                    val fraction = 0.8f + 0.2f * (index + 1) / shown
                    main.post { onProgress(fraction, coveredSec()) }
                }
                val keep = !cancel
                writer.stop(keep)
                encoder = null
                if (!keep) {
                    main.post { onDone(tr("Спряно.", "Stopped.")) }
                } else {
                    val seconds = coveredSec()
                    main.post {
                        onDone(
                            tr(
                                "Готово · ${"%.1f".format(seconds)} сек · Movies/Temporal FFT · без звук",
                                "Done · ${"%.1f".format(seconds)} sec · Movies/Temporal FFT · no audio",
                            ),
                        )
                    }
                }
            } catch (exc: Throwable) {
                val message = if (exc is OutOfMemoryError) {
                    tr("Няма достатъчно памет за този клип.", "Not enough memory for this clip.")
                } else {
                    exc.message ?: tr("Грешка", "Error")
                }
                main.post { onError(message) }
            } finally {
                decoder?.close()
                if (encoder != null) {
                    try {
                        encoder.stop(false)
                    } catch (_: Exception) {
                    }
                }
                val leftover = preview
                preview = null
                leftover?.close()
                leftover?.file?.delete()
                gate.withLock {
                    if (handle != 0L) NativeFilter.destroy(handle)
                    if (closed && active != 0L) {
                        NativeFilter.destroy(active)
                        active = 0L
                        ready = false
                    }
                    if (closed) dropPreview(true)
                }
            }
        }
    }

    fun bitmap(index: Int): Bitmap? {
        gate.withLock {
            val saved = store
            if (!ready || saved == null || index !in 0 until outFrames) return null
            val pixels = IntArray(width * height)
            if (!saved.read(index, pixels)) return null
            return Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
        }
    }

    fun release() {
        cancel = true
        closed = true
        gate.withLock {
            ready = false
            dropPreview(true)
        }
        executor.shutdown()
    }

    private fun dropPreview(delete: Boolean) {
        val saved = store ?: return
        store = null
        saved.close()
        if (delete) saved.file.delete()
    }

    private fun chunkLimit(width: Int, height: Int, mode: Int): Int {
        if (width < 2 || height < 2) return 24
        val frameBytes = width.toLong() * height * 4L
        val budget = Runtime.getRuntime().maxMemory() / 5
        val per = if (mode == 3) frameBytes * 2 else frameBytes
        return (budget / per.coerceAtLeast(1)).toInt().coerceIn(8, 64)
    }
}

private data class ChunkProduct(val handle: Long, val used: Int, val produced: Int, val code: Int)

private fun filterChunk(
    frames: List<IntArray>,
    width: Int,
    height: Int,
    fps: Float,
    mode: Int,
    cutoff: Float,
    low: Float,
    high: Float,
    gain: Float,
    look: Int,
    progress: (Int, Int) -> Boolean,
): ChunkProduct {
    var count = frames.size.coerceAtMost(2000)
    while (count >= 4) {
        val handle = NativeFilter.create(width, height, count)
        if (handle == 0L) {
            count /= 2
            continue
        }
        var pushed = 0
        var failed = false
        while (pushed < count) {
            if (!NativeFilter.push(handle, frames[pushed])) {
                failed = true
                break
            }
            pushed++
        }
        if (failed) {
            NativeFilter.destroy(handle)
            count = pushed / 2
            continue
        }
        val produced = NativeFilter.process(handle, fps, mode, cutoff, low, high, gain, look, progress)
        if (produced == -2) {
            NativeFilter.destroy(handle)
            count /= 2
            continue
        }
        if (produced < 0) {
            NativeFilter.destroy(handle)
            return ChunkProduct(0L, 0, 0, produced)
        }
        return ChunkProduct(handle, count, produced, produced)
    }
    return ChunkProduct(0L, 0, 0, -2)
}

private fun blend(from: IntArray, to: IntArray, weight: Float): IntArray {
    val out = IntArray(from.size)
    val stay = 1f - weight
    for (index in from.indices) {
        val left = from[index]
        val right = to[index]
        val red = (((left shr 16) and 255) * stay + ((right shr 16) and 255) * weight).roundToInt().coerceIn(0, 255)
        val green = (((left shr 8) and 255) * stay + ((right shr 8) and 255) * weight).roundToInt().coerceIn(0, 255)
        val blue = ((left and 255) * stay + (right and 255) * weight).roundToInt().coerceIn(0, 255)
        out[index] = (0xFF shl 24) or (red shl 16) or (green shl 8) or blue
    }
    return out
}

@Composable
private fun FftScreen(onBusy: (Boolean) -> Unit) {
    val context = LocalContext.current
    val session = remember { FftSession(context.applicationContext) }
    DisposableEffect(session) {
        onDispose { session.release() }
    }
    var about by remember { mutableStateOf(false) }
    var source by remember { mutableStateOf<Uri?>(null) }
    var sourceName by remember { mutableStateOf("") }
    var mode by remember { mutableIntStateOf(0) }
    var cutoff by remember { mutableFloatStateOf(2f) }
    var low by remember { mutableFloatStateOf(0.5f) }
    var high by remember { mutableFloatStateOf(3f) }
    var gain by remember { mutableFloatStateOf(3f) }
    var look by remember { mutableIntStateOf(0) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var preview by remember { mutableStateOf<Bitmap?>(null) }
    var frames by remember { mutableIntStateOf(0) }
    var frameIndex by remember { mutableIntStateOf(0) }
    var clipFps by remember { mutableFloatStateOf(30f) }
    var playing by remember { mutableStateOf(false) }

    fun showFrame(index: Int) {
        val safe = index.coerceIn(0, (frames - 1).coerceAtLeast(0))
        val next = session.bitmap(safe) ?: return
        frameIndex = safe
        preview = next
    }

    val openVideo = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            source = uri
            sourceName = uri.lastPathSegment ?: tr("Клип", "Clip")
            status = tr("Клипът е избран.", "Clip selected.")
        }
    }

    LaunchedEffect(playing, frames, clipFps) {
        if (!playing || frames < 2) return@LaunchedEffect
        val frameDelay = (1000f / clipFps.coerceIn(1f, 60f)).toLong().coerceAtLeast(16L)
        while (playing) {
            delay(frameDelay)
            showFrame(if (frameIndex >= frames - 1) 0 else frameIndex + 1)
        }
    }

    val scroll = rememberScrollState()
    Column(modifier = Modifier.fillMaxSize().background(Bg)) {
        Column(modifier = Modifier.padding(start = 16.dp, top = 4.dp, end = 16.dp, bottom = 4.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "Temporal FFT",
                    color = Accent,
                    fontWeight = FontWeight.Bold,
                    fontSize = 20.sp,
                    maxLines = 1,
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    LanguageFlag()
                    Text(
                        "by Dr. Baysinger",
                        color = Muted,
                        fontSize = 11.sp,
                        maxLines = 1,
                        modifier = Modifier
                            .padding(start = 6.dp)
                            .clickable { about = true },
                    )
                }
            }
            Text(
                tr("бързи   ·   бавни   ·   лента   ·   спектър", "fast   ·   slow   ·   band   ·   spectrum"),
                color = Muted,
                fontSize = 12.sp,
                lineHeight = 16.sp,
            )
        }
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .background(Color.Black),
            contentAlignment = Alignment.Center,
        ) {
            val image = preview
            if (image != null && !image.isRecycled) {
                Image(
                    bitmap = image.asImageBitmap(),
                    contentDescription = "Резултат",
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit,
                )
            } else {
                Text(
                    tr("Отвори клип\nРезултатът остава тук", "Open a clip\nThe result stays here"),
                    color = Muted,
                    textAlign = TextAlign.Center,
                )
            }
        }
        if (frames > 0) {
            Column(modifier = Modifier.fillMaxWidth().padding(start = 16.dp, top = 6.dp, end = 16.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(clock(frameIndex, clipFps), color = Muted, fontSize = 12.sp)
                    Slider(
                        value = frameIndex.toFloat(),
                        onValueChange = { showFrame(it.roundToInt()) },
                        valueRange = 0f..(frames - 1).coerceAtLeast(0).toFloat(),
                        enabled = frames > 1 && !busy,
                        modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
                    )
                    Text(clock(frames, clipFps), color = Muted, fontSize = 12.sp)
                }
                ActionButton(
                    if (playing) tr("Спри", "Stop") else tr("Пусни", "Play"),
                    if (playing) Accent else Panel,
                    if (playing) Ink else Color.White,
                    frames > 1 && !busy,
                    Modifier.fillMaxWidth(),
                ) { playing = !playing }
            }
        }
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(scroll)
                .padding(start = 16.dp, top = 8.dp, end = 16.dp, bottom = 14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ActionButton(tr("Отвори", "Open"), Accent, Ink, !busy, Modifier.weight(1f)) {
                    openVideo.launch("video/*")
                }
                ActionButton(tr("Обработи", "Process"), Panel, Color.White, !busy && source != null, Modifier.weight(1f)) {
                    val uri = source ?: return@ActionButton
                    busy = true
                    onBusy(true)
                    frames = 0
                    frameIndex = 0
                    playing = false
                    preview = null
                    status = tr("Чете клипа…", "Reading the clip…")
                    session.start(uri, mode, cutoff, low, high, gain, look, { fraction, seconds ->
                        status = tr(
                            "Обработка ${(fraction * 100).roundToInt()}% · ${"%.1f".format(seconds)} сек",
                            "Processing ${(fraction * 100).roundToInt()}% · ${"%.1f".format(seconds)} sec",
                        )
                    }, { count, _, _ ->
                        frames = count
                        clipFps = session.fps
                        playing = false
                        showFrame(0)
                        status = tr("Записва видеото…", "Writing the video…")
                    }, { message ->
                        busy = false
                        onBusy(false)
                        status = message
                    }, { message ->
                        busy = false
                        onBusy(false)
                        status = message
                    })
                }
                ActionButton(tr("Отказ", "Cancel"), Color(0xFFFF4D3A), Color.White, busy, Modifier.weight(1f)) {
                    session.cancel = true
                }
            }
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(0, 1, 2, 3).forEach { id ->
                    val label = modeLabel(id)
                    val selected = mode == id
                    ActionButton(
                        label,
                        if (selected) Accent else Panel,
                        if (selected) Ink else Color.White,
                        !busy,
                        Modifier.weight(1f),
                    ) { mode = id }
                }
            }
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ActionButton(
                    if (look == 2) tr("Черно-бяло ВКЛ", "Black & white ON") else tr("Черно-бяло", "Black & white"),
                    if (look == 2) Accent else Panel,
                    if (look == 2) Ink else Color.White,
                    !busy,
                    Modifier.weight(1f),
                ) { look = if (look == 2) 0 else 2 }
                ActionButton(
                    if (look == 1) tr("Цветове ВКЛ", "Colors ON") else tr("Цветове", "Colors"),
                    if (look == 1) Accent else Panel,
                    if (look == 1) Ink else Color.White,
                    !busy,
                    Modifier.weight(1f),
                ) { look = if (look == 1) 0 else 1 }
            }
            if (mode == 0 || mode == 1) {
                LabeledSlider(tr("Граница", "Cutoff"), cutoff, 0.2f..12f, "${"%.1f".format(cutoff)} Hz") { cutoff = it }
            }
            if (mode == 2 || mode == 3) {
                LabeledSlider(tr("Долна", "Low"), low, 0.1f..12f, "${"%.1f".format(low)} Hz") { low = it.coerceAtMost(high) }
                LabeledSlider(tr("Горна", "High"), high, 0.2f..16f, "${"%.1f".format(high)} Hz") { high = it.coerceAtLeast(low) }
            }
            if (mode == 0 || mode == 2 || mode == 3) {
                LabeledSlider(tr("Усилване", "Gain"), gain, 0.5f..8f, "${"%.1f".format(gain)}×") { gain = it }
            }
            val shownStatus = status.ifEmpty {
                tr("До 480 px и 10 секунди. Звукът не се пренася.", "Up to 480 px and 10 seconds. Audio is not copied.")
            }
            val shownName = sourceName.ifEmpty { tr("Няма избран клип", "No clip selected") }
            Text(if (source == null) shownStatus else "$shownName    $shownStatus", color = Muted, fontSize = 12.sp)
        }
        if (about) {
            AboutDialog { about = false }
        }
    }
}

@Composable
private fun ActionButton(
    text: String,
    background: Color,
    foreground: Color,
    enabled: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.height(30.dp),
        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = background,
            contentColor = foreground,
            disabledContainerColor = Panel,
            disabledContentColor = Muted,
        ),
        shape = RoundedCornerShape(6.dp),
    ) {
        Text(text, fontSize = 13.sp, maxLines = 1, textAlign = TextAlign.Center)
    }
}

@Composable
private fun LabeledSlider(
    title: String,
    value: Float,
    range: ClosedFloatingPointRange<Float>,
    caption: String,
    onChange: (Float) -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(title, color = Muted)
            Text(caption, color = Color.White, fontSize = 13.sp, textAlign = TextAlign.End)
        }
        Slider(
            value = value.coerceIn(range.start, range.endInclusive),
            onValueChange = onChange,
            valueRange = range,
            modifier = Modifier
                .padding(horizontal = sliderSideInset())
                .pullUp(10.dp),
        )
    }
}

@Composable
private fun sliderSideInset(): Dp {
    val prefix = rememberTextMeasurer().measure(text = "Из", style = LocalTextStyle.current).size.width
    return with(LocalDensity.current) { prefix.toDp() }
}

private fun clock(frame: Int, fps: Float): String {
    val total = (frame / fps.coerceAtLeast(1f)).toInt().coerceAtLeast(0)
    return "%d:%02d".format(total / 60, total % 60)
}

private const val ABOUT_EN = "Donate to Dr. Baysinger. He promises to spend it on hookers and beer."
private const val ABOUT_BG = "Дарете на Dr. Baysinger. Той обещава да ги изхарчи за курви и бело."
private const val REVOLUT = "revolut.me/georgi09qc"
private val BTC = listOf(
    "bc1qr84akpfry7sjs6759wwq722fyert9apcarpst6",
    "bc1p6hp5q20u9t9upsjt7425wgqdrs0cr8wnnlf9atu24z92pzsnq85q62uc8e",
)

@Composable
private fun AboutDialog(onClose: () -> Unit) {
    val context = LocalContext.current
    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Panel,
        titleContentColor = Color.White,
        textContentColor = Color.White,
        confirmButton = {
            TextButton(onClick = onClose) { Text("OK", color = Accent) }
        },
        title = { Text("About", fontWeight = FontWeight.Bold) },
        text = {
            Column(
                modifier = Modifier
                    .heightIn(max = 420.dp)
                    .verticalScroll(rememberScrollState()),
            ) {
                Text(ABOUT_EN)
                Text(ABOUT_BG, modifier = Modifier.padding(top = 12.dp))
                Text("Revolut", fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 20.dp))
                CopyLine(REVOLUT, context)
                Text("BTC address", fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 16.dp))
                BTC.forEach { CopyLine(it, context) }
            }
        },
    )
}

@Composable
private fun CopyLine(value: String, context: Context) {
    Text(
        value,
        color = Accent,
        modifier = Modifier
            .fillMaxWidth()
            .clickable {
                val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                clipboard.setPrimaryClip(ClipData.newPlainText("address", value))
                Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
            }
            .padding(vertical = 6.dp),
    )
}

private fun Modifier.pullUp(by: Dp): Modifier = layout { measurable, constraints ->
    val placeable = measurable.measure(constraints)
    val lift = by.roundToPx().coerceAtMost(placeable.height)
    layout(placeable.width, placeable.height - lift) {
        placeable.place(0, -lift)
    }
}
