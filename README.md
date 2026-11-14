# Temporal FFT

Филтър по времето за всеки пиксел и всеки цвят. Бързото движение, бавното, една честотна лента или самият спектър.

Има версия за телефон и версия за Windows. Клипът и мястото на записа се избират от прозорец.

## Телефон

Свали **Temporal-FFT-1.6.apk** от [Releases](https://github.com/smouky577/temporal-fft/releases) и го инсталирай. Ако Android попита, разреши инсталация от този файл.

Кодът за телефона е в папката `android`.

Телефонът смалява клипа до 480 px и обработва първите 10 секунди. Ако кадрите не се събират наведнъж, минава ги на парчета и продължава до 10-ата секунда. Долу пише колко секунди реално са влезли.

## Windows

Свали **Temporal-FFT-Windows-1.6.zip** от [Releases](https://github.com/smouky577/temporal-fft/releases). Цъкни върху името на zip файла. Той се сваля цял.

Не отваряй `temporal_app.py` и `temporal.py` в сайта и не копирай текста в нов файл. Така файловете само се показват, не се свалят.

Разархивирай zip-а. В папката има:

- `Temporal FFT.bat` — пуска програмата
- `temporal_app.py` — прозорецът
- `temporal.py` — самият филтър

Кликни два пъти върху `Temporal FFT.bat`. Трите файла трябва да останат в една папка.

Батникът стартира `C:\Python314\pythonw.exe` с `temporal_app.py`. В този Python вече са нужните библиотеки: OpenCV, NumPy и Pillow.

Ако прозорецът не се появи, отвори папката в командния ред и пусни:

```bat
C:\Python314\python.exe temporal_app.py
```

Така на екрана се вижда какво липсва.

## Режими

- **Бързи** — пази картината и усилва движението над границата в херцове
- **Бавни** — оставя само бавното, с реалните цветове
- **Лента** — пази картината и усилва честотите между долната и горната граница
- **Спектър** — кадрите са честоти: първите са бавни, последните са бързи. Долна и горна граница избират кои честоти влизат, а усилването прави слабите по-светли

На Windows бутонът **Размер** показва ширината и дължината. По подразбиране са 480 px и 10 секунди. **Оригинал** и **Цял клип** махат тези граници. Ако оригиналът не се събира в паметта, клипът се минава на парчета и се сглобява в един файл.

**Черно-бяло** и **Цветове** се включват поотделно. Ако и двата са изключени, движението се рисува върху сиво.

Звукът не се пренася.

---

# Temporal FFT (English)

A time filter for every pixel and every color channel. Fast motion, slow motion, one frequency band, or the spectrum itself.

There is a phone app and a Windows app. The clip and the save location are chosen in the window.

## Phone

Download **Temporal-FFT-1.6.apk** from [Releases](https://github.com/smouky577/temporal-fft/releases) and install it. Allow installation from this file if Android asks.

The phone source is in the `android` folder.

The phone scales the clip to 480 px and processes the first 10 seconds. If the frames do not fit at once, it handles them in pieces and continues until the 10th second. The line at the bottom shows how many seconds actually went in.

## Windows

Download **Temporal-FFT-Windows-1.6.zip** from [Releases](https://github.com/smouky577/temporal-fft/releases). Click the zip file name. The whole archive downloads.

Do not open `temporal_app.py` or `temporal.py` on the site and do not paste them into a new file. That only shows the text.

Unzip the archive. The folder contains:

- `Temporal FFT.bat` — starts the program
- `temporal_app.py` — the window
- `temporal.py` — the filter

Double-click `Temporal FFT.bat`. Keep the three files in the same folder.

The batch file starts `C:\Python314\pythonw.exe` with `temporal_app.py`. That Python already has OpenCV, NumPy, and Pillow.

If the window does not open, open the folder in a command prompt and run:

```bat
C:\Python314\python.exe temporal_app.py
```

Any missing library is printed there.

## Modes

- **Fast** — keeps the picture and amplifies motion above the cutoff in hertz
- **Slow** — keeps only the slow part, in the real colors
- **Band** — keeps the picture and amplifies frequencies between the low and high limits
- **Spectrum** — frames are frequencies: the first are slow, the last are fast. Low and high choose which frequencies appear, and gain makes the weak ones brighter

On Windows the **Size** button shows width and length. The defaults are 480 px and 10 seconds. **Original size** and **Whole clip** remove those limits. If the original clip does not fit in memory, it is processed in pieces and joined into one file.

**Black and white** and **Colors** turn on separately. With both off, the motion is drawn on gray.

Audio is not copied.
