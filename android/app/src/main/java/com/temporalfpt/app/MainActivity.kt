package com.temporalfpt.app

import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.util.concurrent.Executors
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock
import kotlin.math.roundToInt

private val Bg = Color(0xFF10120F)
private val Panel = Color(0xFF242824)
private val Accent = Color(0xFFD6F25C)
private val Ink = Color(0xFF16180F)
private val Muted = Color(0xFF9AA194)

private val modes = listOf(
    0 to "Бързи",
    1 to "Бавни",
    2 to "Лента",
    3 to "Спектър",
)

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
        onProgress: (Float) -> Unit,
        onReady: (Int, Int, Int) -> Unit,
        onDone: (String) -> Unit,
        onError: (String) -> Unit,
    ) {
        cancel = false
        executor.execute {
            var handle = 0L
            var decoder: VideoDecoder? = null
            var encoder: Mp4Encoder? = null
            try {
                val check = NativeFilter.selfTest()
                if (check != 0) error("Проверката на сметката не мина ($check).")
                decoder = VideoDecoder(context, uri)
                val clipFps = decoder.fps
                val maxFrames = (clipFps * MAX_SECONDS).toInt().coerceIn(4, 720)
                var stored = 0
                var streak = 0
                var frameW = 0
                var frameH = 0
                while (stored < maxFrames && !cancel) {
                    when (val item = decoder.next()) {
                        is Decoded.Frame -> {
                            streak = 0
                            var frame = scaleMaxWidth(rotateArgb(item.raw, decoder.rotation), MAX_WIDTH)
                            if (handle == 0L) {
                                frameW = frame.width
                                frameH = frame.height
                                handle = NativeFilter.create(frameW, frameH, maxFrames)
                                if (handle == 0L) error("Няма достатъчно памет за този клип.")
                            } else if (frame.width != frameW || frame.height != frameH) {
                                frame = fitFrame(frame, frameW, frameH)
                            }
                            NativeFilter.push(handle, frame.pixels)
                            stored++
                            val fraction = 0.2f * stored / maxFrames
                            main.post { onProgress(fraction) }
                        }
                        Decoded.Pending -> {
                            streak++
                            if (streak > 80) error("Клипът спря да се чете.")
                        }
                        Decoded.End -> break
                    }
                }
                decoder.close()
                decoder = null
                if (cancel) {
                    main.post { onDone("Спряно.") }
                    return@execute
                }
                if (stored < 4) error("Твърде малко кадри.")
                val count = NativeFilter.process(handle, clipFps, mode, cutoff, low, high, gain) { done, total ->
                    if (total > 0) {
                        val fraction = 0.2f + 0.65f * done / total
                        main.post { onProgress(fraction) }
                    }
                    cancel
                }
                if (cancel || count == -5) {
                    main.post { onDone("Спряно.") }
                    return@execute
                }
                if (count < 0) {
                    error(
                        when (count) {
                            -2 -> "Няма достатъчно памет за този клип."
                            -3 -> "Твърде малко кадри."
                            else -> "Филтърът спря с код $count."
                        },
                    )
                }
                var accepted = false
                gate.withLock {
                    if (closed) return@withLock
                    val previous = active
                    active = handle
                    width = frameW
                    height = frameH
                    outFrames = count
                    fps = clipFps
                    ready = true
                    handle = 0L
                    accepted = true
                    if (previous != 0L) NativeFilter.destroy(previous)
                }
                if (!accepted) {
                    main.post { onDone("Спряно.") }
                    return@execute
                }
                val shown = outFrames
                main.post { onReady(shown, frameW, frameH) }
                val pixels = IntArray(frameW * frameH)
                val writer = Mp4Encoder(context, frameW, frameH, clipFps)
                encoder = writer
                writer.start()
                for (index in 0 until shown) {
                    if (cancel) break
                    gate.withLock {
                        if (active != 0L) NativeFilter.copyFrame(active, index, pixels)
                    }
                    writer.write(pixels)
                    val fraction = 0.85f + 0.15f * (index + 1) / shown
                    main.post { onProgress(fraction) }
                }
                val keep = !cancel
                writer.stop(keep)
                encoder = null
                if (!keep) {
                    main.post { onDone("Спряно.") }
                } else {
                    main.post { onDone("Готово · Movies/Temporal FFT · без звук") }
                }
            } catch (exc: Throwable) {
                main.post { onError(exc.message ?: "Грешка") }
            } finally {
                decoder?.close()
                if (encoder != null) {
                    try {
                        encoder.stop(false)
                    } catch (_: Exception) {
                    }
                }
                gate.withLock {
                    if (handle != 0L) NativeFilter.destroy(handle)
                    if (closed && active != 0L) {
                        NativeFilter.destroy(active)
                        active = 0L
                        ready = false
                    }
                }
            }
        }
    }

    fun bitmap(index: Int): Bitmap? {
        gate.withLock {
            if (!ready || active == 0L || index !in 0 until outFrames) return null
            val pixels = IntArray(width * height)
            NativeFilter.copyFrame(active, index, pixels)
            return Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
        }
    }

    fun release() {
        cancel = true
        closed = true
        gate.withLock { ready = false }
        executor.shutdown()
    }
}

@Composable
private fun FftScreen(onBusy: (Boolean) -> Unit) {
    val context = LocalContext.current
    val session = remember { FftSession(context.applicationContext) }
    DisposableEffect(session) {
        onDispose { session.release() }
    }
    var source by remember { mutableStateOf<Uri?>(null) }
    var sourceName by remember { mutableStateOf("Няма избран клип") }
    var mode by remember { mutableIntStateOf(0) }
    var cutoff by remember { mutableFloatStateOf(2f) }
    var low by remember { mutableFloatStateOf(0.5f) }
    var high by remember { mutableFloatStateOf(3f) }
    var gain by remember { mutableFloatStateOf(3f) }
    var busy by remember { mutableStateOf(false) }
    var progress by remember { mutableFloatStateOf(0f) }
    var status by remember { mutableStateOf("До 480 px и 10 секунди. Звукът не се пренася.") }
    var preview by remember { mutableStateOf<Bitmap?>(null) }
    var frames by remember { mutableIntStateOf(0) }
    var frameIndex by remember { mutableIntStateOf(0) }
    var resultW by remember { mutableIntStateOf(1) }
    var resultH by remember { mutableIntStateOf(1) }

    val openVideo = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            source = uri
            sourceName = uri.lastPathSegment ?: "Клип"
            status = "Клипът е избран."
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Bg)
            .verticalScroll(rememberScrollState())
            .padding(18.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text("TEMPORAL", color = Accent, fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Text("FFT", color = Color.White, fontSize = 22.sp, modifier = Modifier.padding(bottom = 4.dp))
        Text(sourceName, color = Muted, fontSize = 13.sp)
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(if (resultW > 0 && resultH > 0) resultW / resultH.toFloat() else 16f / 9f)
                .background(Color(0xFF070807)),
            contentAlignment = Alignment.Center,
        ) {
            val image = preview
            if (image != null && !image.isRecycled) {
                Image(
                    bitmap = image.asImageBitmap(),
                    contentDescription = null,
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit,
                )
            } else {
                Text("Резултатът се показва тук", color = Muted, fontSize = 14.sp)
            }
        }
        if (frames > 1) {
            Slider(
                value = frameIndex.toFloat(),
                onValueChange = {
                    val next = it.roundToInt().coerceIn(0, frames - 1)
                    frameIndex = next
                    preview = session.bitmap(next)
                },
                valueRange = 0f..(frames - 1).toFloat(),
            )
            Text("Кадър ${frameIndex + 1} / $frames", color = Muted, fontSize = 12.sp)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            modes.forEach { (id, label) ->
                val selected = mode == id
                Button(
                    onClick = { if (!busy) mode = id },
                    enabled = !busy,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (selected) Accent else Panel,
                        contentColor = if (selected) Ink else Color.White,
                    ),
                    shape = RoundedCornerShape(8.dp),
                    contentPadding = ButtonDefaults.ContentPadding,
                ) {
                    Text(label, fontSize = 13.sp)
                }
            }
        }
        if (mode == 0 || mode == 1) {
            SliderRow("Граница", cutoff, 0.2f, 12f) { cutoff = it }
            Text("${"%.1f".format(cutoff)} Hz", color = Color.White, fontSize = 13.sp)
        }
        if (mode == 2) {
            SliderRow("Долна", low, 0.1f, 12f) { low = it.coerceAtMost(high) }
            Text("${"%.1f".format(low)} Hz", color = Color.White, fontSize = 13.sp)
            SliderRow("Горна", high, 0.2f, 16f) { high = it.coerceAtLeast(low) }
            Text("${"%.1f".format(high)} Hz", color = Color.White, fontSize = 13.sp)
        }
        if (mode == 0 || mode == 2) {
            SliderRow("Усилване", gain, 0.5f, 8f) { gain = it }
            Text("${"%.1f".format(gain)}×", color = Color.White, fontSize = 13.sp)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { openVideo.launch("video/*") },
                enabled = !busy,
                colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = Ink),
                shape = RoundedCornerShape(8.dp),
            ) { Text("Отвори клип") }
            Button(
                onClick = {
                    val uri = source ?: return@Button
                    busy = true
                    onBusy(true)
                    progress = 0f
                    frames = 0
                    frameIndex = 0
                    preview = null
                    status = "Чете клипа…"
                    session.start(uri, mode, cutoff, low, high, gain, { fraction ->
                        progress = fraction
                        status = "Обработка ${(fraction * 100).roundToInt()}%"
                    }, { count, w, h ->
                        frames = count
                        resultW = w
                        resultH = h
                        frameIndex = 0
                        preview = session.bitmap(0)
                        status = "Записва видеото…"
                    }, { message ->
                        busy = false
                        onBusy(false)
                        progress = 1f
                        status = message
                    }, { message ->
                        busy = false
                        onBusy(false)
                        status = message
                    })
                },
                enabled = !busy && source != null,
                colors = ButtonDefaults.buttonColors(containerColor = Panel, contentColor = Color.White),
                shape = RoundedCornerShape(8.dp),
            ) { Text("Обработи") }
            if (busy) {
                Button(
                    onClick = { session.cancel = true },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFFF4D3A), contentColor = Color.White),
                    shape = RoundedCornerShape(8.dp),
                ) { Text("Отказ") }
            }
        }
        Text(status, color = Muted, fontSize = 13.sp)
    }
}

@Composable
private fun SliderRow(label: String, value: Float, start: Float, end: Float, onChange: (Float) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = Muted, modifier = Modifier.padding(end = 8.dp), fontSize = 14.sp)
        Slider(
            value = value,
            onValueChange = onChange,
            valueRange = start..end,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}
