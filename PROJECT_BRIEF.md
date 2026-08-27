# LectureSift — Master Project Brief

## Product promise

One lecture video becomes a complete, usable study pack: accurate transcript, optional translation, structured notes, summary, genuine slides, quiz, flashcards, and exportable files.

LectureSift must feel like a product, not a technical demo. The primary audience is students who should not need to understand codecs, APIs, or server behavior.

## Verified baseline

V3.2 successfully processed a 3:45 Carleton College Biology 252 video (7.3 MB) in 1:54 after the low-memory change removed the Render error-137 crash. Transcript, summary, 10 quiz questions, and 20 flashcards were produced.

The known quality failure was slide detection: the no-slide video produced 22 visual candidates, 12 presentation candidates, and 9 false final slides. V4 therefore treats zero slides as the correct result when no genuine presentation is present.

The V4 foundation passed this real-video gate on August 27, 2026. After the single-pass scanner optimization, the 224.9-second, 7.3 MB Biology 252 sample completed visual analysis in 2.63 seconds with approximately 81 MB peak RSS and returned exactly 0 slides. The classifier now also uses the maximum skin/person concentration across horizontal frame bands, which rejects classroom audiences and picture-collage title cards without retaining full frames in memory.

A second real-video gate used the 4:28 University of Manchester “Flipped Classroom for Quantitative Methods” WebM. It mixes an office interview with three full-screen or slide-dominant educational frames. After adding single-pass format-independent FFmpeg sampling, accurate frame seeking, and textured natural-scene rejection, V4 completed visual analysis in 4.25 seconds with approximately 84 MB peak RSS. It rejected all interview/office frames and preserved exactly the three genuine slide frames at 01:13, 02:42, and 04:15.

## V4 foundation

- Parallel audio transcription and visual scanning
- Timestamp-only visual candidates for low memory usage
- Layout, text, face/skin, persistence, and duplicate checks for slides
- Original plus translated transcript
- Five summary profiles
- Interactive quiz and flashcards in the browser
- Individual PDF/TXT downloads plus complete ZIP
- Human-readable errors with small support codes such as `LS-URL-02`
- Live processing timeline
- Optional dual-source mode: transcription from the primary/audio recording and slide extraction from a synchronized second recording
- Direct video and supported education-page URLs, with honest warnings about provider restrictions
- Turkish and English product copy, with core labels for eleven additional languages

## Acceptance gates

1. The known no-slide Biology 252 sample returns exactly 0 slides. **Passed on V4 foundation.**
2. A genuine slide-based lecture keeps readable, unique presentation frames. **Passed on V4 foundation.**
3. A long lecture completes without memory spikes or oversized transcription uploads.
4. Original and translated transcripts remain separately accessible.
5. Every completed job provides web results, individual PDF/TXT files, and a ZIP.
6. Errors are understandable without exposing stack traces or infrastructure jargon.

## Next phases

1. Calibrate V4 on the real no-slide sample and a genuine slide-heavy lecture.
2. Add transcript timestamps and link notes/slides to the lecture timeline.
3. Complete every V4 interface string in all thirteen languages.
4. Add “Bu derse sor” and “Sınava hazırlan” study modes.
5. Add accounts, usage limits, billing, privacy controls, and production analytics only after the core pipeline passes the quality gates.

## Deployment rule

`main` is the live Netlify/Render deployment. Changes must pass the automated suite and a focused local acceptance test before publishing.
