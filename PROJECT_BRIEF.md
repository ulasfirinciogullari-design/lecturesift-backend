# LectureSift — Master Project Brief

## Product promise

One lecture video becomes a complete, usable study pack: accurate transcript, optional translation, structured notes, summary, genuine slides, quiz, flashcards, and exportable files.

LectureSift must feel like a product, not a technical demo. The primary audience is students who should not need to understand codecs, APIs, or server behavior.

## Verified baseline

V3.2 successfully processed a 3:45 Carleton College Biology 252 video (7.3 MB) in 1:54 after the low-memory change removed the Render error-137 crash. Transcript, summary, 10 quiz questions, and 20 flashcards were produced.

The known quality failure was slide detection: the no-slide video produced 22 visual candidates, 12 presentation candidates, and 9 false final slides. V4 therefore treats zero slides as the correct result when no genuine presentation is present.

The V4 foundation passed this real-video gate on August 27, 2026. After the single-pass scanner optimization, the 224.9-second, 7.3 MB Biology 252 sample completed visual analysis in 2.63 seconds with approximately 81 MB peak RSS and returned exactly 0 slides. The classifier now also uses the maximum skin/person concentration across horizontal frame bands, which rejects classroom audiences and picture-collage title cards without retaining full frames in memory.

A second real-video gate used the 4:28 University of Manchester “Flipped Classroom for Quantitative Methods” WebM. It mixes an office interview with three full-screen or slide-dominant educational frames. After adding single-pass format-independent FFmpeg sampling, accurate frame seeking, and textured natural-scene rejection, V4 completed visual analysis in 4.25 seconds with approximately 84 MB peak RSS. It rejected all interview/office frames and preserved exactly the three genuine slide frames at 01:13, 02:42, and 04:15.

## V4.1 foundation

- Parallel audio transcription and visual scanning
- Timestamp-only visual candidates for low memory usage
- Layout, text, face/skin, persistence, and duplicate checks for slides
- One transcript when source and output languages match; original plus translated transcript otherwise
- Five summary profiles
- Interactive quiz and flashcards in the browser
- Selectable PDF, Word, and TXT downloads; the default ZIP contains only PDFs
- Human-readable errors with small support codes such as `LS-URL-02`
- Live processing timeline
- Ordered multi-video lectures, with drag-and-drop reordering in the interface
- Separate ordered audio and visual/slide source lists, including a global synchronization offset
- Video-to-MP3 conversion, combining ordered videos into one audio file
- URL-to-video download utility using direct-media discovery and provider extraction
- Direct video and supported education-page URLs, with honest warnings about provider restrictions
- Turkish and English product copy, with core labels for eleven additional languages

## Durable background execution foundation

A dedicated recovery branch now contains the production-oriented background execution architecture:

- Celery job queue with late acknowledgements and worker-loss recovery
- Render Key Value / Redis-compatible shared job state
- Dedicated Render background worker definition
- S3-compatible object storage for uploaded sources and generated outputs
- Automatic fallback to the existing in-process worker when durable infrastructure is not configured
- Output rematerialization so the existing API endpoints can serve worker-produced results
- Atomic local job-state fallback for development
- GitHub Actions pytest gate before deployment

This foundation is intentionally not merged to `main` until CI and a focused end-to-end acceptance test pass and the required paid worker/Key Value plus object-storage credentials are explicitly provisioned.

## Acceptance gates

1. The known no-slide Biology 252 sample returns exactly 0 slides. **Passed on V4 foundation.**
2. A genuine slide-based lecture keeps readable, unique presentation frames. **Passed on V4 foundation.**
3. A long lecture completes without memory spikes or oversized transcription uploads.
4. Original and translated transcripts remain separately accessible when they differ; identical-language output is not duplicated.
5. Every completed study job provides web results, selected PDF/Word/TXT files, and a ZIP; default ZIPs contain PDFs only.
6. Errors are understandable without exposing stack traces or infrastructure jargon.
7. A durable queued job survives browser disconnect and web-service restart once queue/object-storage infrastructure is activated.

## Next phases

1. Pass CI and run the durable-worker end-to-end acceptance gate.
2. Provision Render Key Value + background worker and S3/R2-compatible object storage credentials.
3. Add transcript timestamps and link notes/slides to the lecture timeline.
4. Complete every V4 interface string in all thirteen languages.
5. Add “Bu derse sor” and “Sınava hazırlan” study modes.
6. Add accounts, usage limits, billing, privacy controls, and production analytics after the core pipeline and durable execution gates pass.

## Deployment rule

`main` is the live Netlify/Render deployment. Changes must pass the automated suite and a focused local acceptance test before publishing.
