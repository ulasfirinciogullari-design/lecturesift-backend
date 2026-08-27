const API = "https://lecturesift-backend.onrender.com";

const LANGUAGES = {
  tr: "Türkçe", en: "English", de: "Deutsch", fr: "Français", es: "Español",
  it: "Italiano", pt: "Português", ru: "Русский", ar: "العربية", zh: "中文",
  ja: "日本語", ko: "한국어", hi: "हिन्दी"
};

const EN = {
  eyebrow: "AI-powered lecture workspace",
  title: "Everything you need to study from one lecture video.",
  subtitle: "Upload a video or paste a link. LectureSift prepares the transcript, smart notes, summary, slides, quiz, flashcards, and downloadable study files.",
  sourceTitle: "Add the lecture source", secure: "Secure processing", uploadTab: "Upload file", linkTab: "Use a link",
  dropTitle: "Drop your video here", dropText: "or choose from your device", fileHelp: "MP4, MOV, MKV, or WebM - 1 GB total",
  audioSourceTitle: "Main / audio video", audioSourceHelp: "Audio and transcript come from this video. If you do not add a separate slide video, its visuals are scanned too.",
  slidesSourceTitle: "Separate slide video", slidesSourceHelp: "Add the second synchronized recording that shows the slides.", addSlidesVideo: "Add slide video",
  required: "Required", optional: "Optional", syncOffset: "Slide time offset", syncOffsetHelp: "Leave at 0 if both recordings started together.",
  urlLabel: "Video or education-page URL", urlHelp: "Direct MP4/WebM links and supported education pages are accepted. YouTube may restrict server-side downloads.",
  settingsTitle: "Configure the study pack", sourceLanguage: "Video language", outputLanguage: "Output language", summaryStyle: "Summary profile",
  quizCount: "Quiz questions", cardCount: "Flashcards", translateTitle: "Translate transcript", translateHelp: "The original is preserved", analyze: "Analyze lecture",
  processEyebrow: "Live processing center", readyTitle: "Ready to analyze", readyStage: "Waiting for a video",
  readyDetail: "Add a source and choose your settings to see every processing step here.",
  stageReceive: "Receiving video", stageAudio: "Separating audio", stageTranscript: "Transcribing speech", stageVisual: "Scanning visual content",
  stageSlides: "Validating slides", stageStudy: "Structuring the lecture", stageExport: "Preparing files",
  promiseTitle: "A complete study pack from one video", promiseText: "Slides, original and translated transcripts, notes, summary, quiz, flashcards, and PDF/TXT files.",
  resultEyebrow: "Study pack ready", downloadAll: "Download complete pack", tabSummary: "Summary", tabNotes: "Smart notes", tabTranscript: "Transcript",
  tabSlides: "Slides", tabCards: "Flashcards", tabFiles: "Files", translated: "Translated", original: "Original", errorTitle: "Analysis could not finish",
  summaryShort: "Quick", summaryStandard: "Standard", summaryDetailed: "Detailed", summaryExam: "Exam-focused", summaryFive: "Learn in 5 minutes",
  auto: "Auto detect", noSlides: "No genuine presentation slides were detected in this video.", noContent: "No content was generated for this section.",
  correct: "Correct", incorrect: "Incorrect", score: "Score", reveal: "Reveal answer", previous: "Previous", next: "Next", know: "I know this", repeat: "Repeat",
  download: "Download", processing: "Lecture analysis in progress", done: "Your study pack is ready", parallel_analysis: "Audio and visuals are being analyzed together",
  url_download: "Downloading the video", study_pack: "Creating smart notes and questions", exports: "Preparing PDF and TXT files",
  errorFallback: "The request could not be completed. Check the video or link and try again."
};

const TR = {
  eyebrow: "Yapay zekâ destekli ders çalışma alanı",
  title: "Bir ders videosundan çalışmaya hazır her şey.",
  subtitle: "Videoyu yükle veya bağlantıyı yapıştır. LectureSift transkript, akıllı notlar, özet, slaytlar, quiz ve bilgi kartlarını tek pakette hazırlar.",
  sourceTitle: "Ders kaynağını ekle", secure: "Güvenli işlem", uploadTab: "Dosya yükle", linkTab: "Bağlantı kullan",
  dropTitle: "Videoyu buraya bırak", dropText: "veya cihazından seç", fileHelp: "MP4, MOV, MKV veya WebM - toplam en fazla 1 GB",
  audioSourceTitle: "Ana / ses videosu", audioSourceHelp: "Ses ve transkript buradan alınır. Ayrı slayt videosu eklemezsen görüntüler de bu videodan taranır.",
  slidesSourceTitle: "Ayrı slayt videosu", slidesSourceHelp: "Eş zamanlı kaydedilen, slaytların geçtiği ikinci videoyu buraya ekle.", addSlidesVideo: "Slayt videosu ekle",
  required: "Zorunlu", optional: "İsteğe bağlı", syncOffset: "Slayt zaman farkı", syncOffsetHelp: "Aynı anda başladıysa 0 bırak.",
  urlLabel: "Video veya eğitim sayfası bağlantısı", urlHelp: "Doğrudan MP4/WebM bağlantıları ve desteklenen eğitim sayfaları kabul edilir. YouTube bazı sunucularda indirmeyi kısıtlayabilir.",
  settingsTitle: "Çalışma paketini ayarla", sourceLanguage: "Video dili", outputLanguage: "Çıktı dili", summaryStyle: "Özet profili",
  quizCount: "Quiz sorusu", cardCount: "Bilgi kartı", translateTitle: "Transkripti çevir", translateHelp: "Orijinali de korunur", analyze: "Dersi analiz et",
  processEyebrow: "Canlı işlem merkezi", readyTitle: "Analize hazır", readyStage: "Video bekleniyor",
  readyDetail: "Kaynağı ekleyip ayarlarını seçtiğinde işlem adımlarını burada canlı göreceksin.",
  stageReceive: "Video alınıyor", stageAudio: "Ses ayrıştırılıyor", stageTranscript: "Konuşma çözümleniyor", stageVisual: "Görsel içerik taranıyor",
  stageSlides: "Slaytlar doğrulanıyor", stageStudy: "Ders yapılandırılıyor", stageExport: "Çıktılar hazırlanıyor",
  promiseTitle: "Tek videodan tam çalışma paketi", promiseText: "Slaytlar, iki dilde transkript, notlar, özet, quiz, bilgi kartları ve PDF/TXT dosyaları.",
  resultEyebrow: "Ders paketi hazır", downloadAll: "Tüm paketi indir", tabSummary: "Özet", tabNotes: "Akıllı notlar", tabTranscript: "Transkript",
  tabSlides: "Slaytlar", tabCards: "Bilgi kartları", tabFiles: "Dosyalar", translated: "Çevrilmiş", original: "Orijinal", errorTitle: "İşlem tamamlanamadı",
  summaryShort: "Hızlı", summaryStandard: "Standart", summaryDetailed: "Derinlemesine", summaryExam: "Sınav odaklı", summaryFive: "5 dakikada öğren",
  auto: "Otomatik algıla", noSlides: "Bu videoda gerçek bir sunum slaytı tespit edilmedi.", noContent: "Bu bölüm için içerik üretilemedi.",
  correct: "Doğru", incorrect: "Yanlış", score: "Skor", reveal: "Cevabı göster", previous: "Önceki", next: "Sonraki", know: "Biliyorum", repeat: "Tekrar et",
  download: "İndir", processing: "Ders analizi sürüyor", done: "Çalışma paketin hazır", parallel_analysis: "Ses ve görüntü birlikte analiz ediliyor",
  url_download: "Video bağlantıdan alınıyor", study_pack: "Akıllı notlar ve sorular hazırlanıyor", exports: "PDF ve TXT dosyaları hazırlanıyor",
  errorFallback: "İşlem tamamlanamadı. Videoyu veya bağlantıyı kontrol edip yeniden deneyebilirsin."
};

const LEGACY = {
  de: ["Vorlesungsvideo oder Link", "Datei hochladen", "Video-Link verwenden", "Video analysieren", "Videosprache", "Ausgabesprache", "Zusammenfassungsprofil", "Quizfragen", "Lernkarten", "Zusammenfassung", "Notizen", "Transkript", "Folien", "Alle Dateien herunterladen"],
  fr: ["Vidéo de cours ou lien", "Téléverser un fichier", "Utiliser un lien", "Analyser le cours", "Langue source", "Langue de sortie", "Profil du résumé", "Questions", "Cartes mémoire", "Résumé", "Notes", "Transcription", "Diapositives", "Télécharger tous les fichiers"],
  es: ["Video de clase o enlace", "Subir archivo", "Usar enlace", "Analizar la clase", "Idioma de origen", "Idioma de salida", "Perfil del resumen", "Preguntas", "Tarjetas", "Resumen", "Apuntes", "Transcripción", "Diapositivas", "Descargar todos los archivos"],
  it: ["Video lezione o link", "Carica file", "Usa un link", "Analizza lezione", "Lingua sorgente", "Lingua output", "Profilo riassunto", "Quiz", "Flashcard", "Riassunto", "Appunti", "Trascrizione", "Slide", "Scarica tutti i file"],
  pt: ["Vídeo da aula ou link", "Enviar arquivo", "Usar link", "Analisar aula", "Idioma de origem", "Idioma de saída", "Perfil do resumo", "Perguntas", "Flashcards", "Resumo", "Notas", "Transcrição", "Slides", "Baixar todos os arquivos"],
  ru: ["Видео лекции или ссылка", "Загрузить файл", "Использовать ссылку", "Анализировать лекцию", "Язык видео", "Язык результата", "Профиль конспекта", "Вопросы", "Карточки", "Конспект", "Заметки", "Транскрипт", "Слайды", "Скачать все файлы"],
  ar: ["فيديو المحاضرة أو الرابط", "رفع ملف", "استخدام رابط", "تحليل المحاضرة", "لغة الفيديو", "لغة الإخراج", "نمط الملخص", "أسئلة", "بطاقات", "الملخص", "الملاحظات", "النص", "الشرائح", "تنزيل كل الملفات"],
  zh: ["课程视频或链接", "上传文件", "使用链接", "分析课程", "视频语言", "输出语言", "摘要模式", "测验题", "闪卡", "摘要", "笔记", "文字稿", "幻灯片", "下载全部文件"],
  ja: ["講義動画またはリンク", "ファイルをアップロード", "リンクを使用", "講義を分析", "動画の言語", "出力言語", "要約プロファイル", "クイズ", "カード", "要約", "ノート", "文字起こし", "スライド", "全ファイルをダウンロード"],
  ko: ["강의 영상 또는 링크", "파일 업로드", "링크 사용", "강의 분석", "영상 언어", "출력 언어", "요약 프로필", "퀴즈", "플래시카드", "요약", "노트", "전사", "슬라이드", "전체 파일 다운로드"],
  hi: ["लेक्चर वीडियो या लिंक", "फ़ाइल अपलोड करें", "लिंक का उपयोग करें", "लेक्चर का विश्लेषण", "वीडियो भाषा", "आउटपुट भाषा", "सारांश प्रोफ़ाइल", "क्विज़", "फ्लैशकार्ड", "सारांश", "नोट्स", "ट्रांसक्रिप्ट", "स्लाइड", "सभी फ़ाइलें डाउनलोड करें"]
};

const ERRORS = {
  en: {
    "LS-AI-01": "The AI usage quota is exhausted. Try again after the account quota has been renewed.",
    "LS-AI-02": "The AI service is busy. Try again in a few minutes.",
    "LS-URL-02": "The video provider blocked server-side downloading. Upload the file or use a direct MP4/WebM link.",
    "LS-URL-03": "No downloadable video was found on this page. Use a direct video link or upload the file.",
    "LS-UPLOAD-02": "The video is larger than the allowed file size.",
    "LS-VIDEO-02": "The video could not be read. It may be damaged or use an unsupported codec."
  },
  tr: {}
};

const $ = (id) => document.getElementById(id);
const uiLanguage = $("uiLanguage"), sourceLanguage = $("sourceLanguage"), outputLanguage = $("outputLanguage");
const summaryStyle = $("summaryStyle"), videoFile = $("videoFile"), slidesVideoFile = $("slidesVideoFile"), videoUrl = $("videoUrl");
let currentLanguage = localStorage.getItem("lecturesift-ui") || "tr";
let sourceMode = "upload", selectedVideo = null, selectedSlidesVideo = null, jobId = null, timerStarted = null, timerHandle = null, pollHandle = null;
let latestResult = null, cardIndex = 0, cardRevealed = false, quizScore = 0, quizAnswered = 0;

function stringsFor(language) {
  if (language === "tr") return TR;
  if (language === "en") return EN;
  const values = LEGACY[language] || [];
  return {
    ...EN,
    sourceTitle: values[0] || EN.sourceTitle, uploadTab: values[1] || EN.uploadTab, linkTab: values[2] || EN.linkTab,
    analyze: values[3] || EN.analyze, sourceLanguage: values[4] || EN.sourceLanguage, outputLanguage: values[5] || EN.outputLanguage,
    summaryStyle: values[6] || EN.summaryStyle, quizCount: values[7] || EN.quizCount, cardCount: values[8] || EN.cardCount,
    tabSummary: values[9] || EN.tabSummary, tabNotes: values[10] || EN.tabNotes, tabTranscript: values[11] || EN.tabTranscript,
    tabSlides: values[12] || EN.tabSlides, downloadAll: values[13] || EN.downloadAll
  };
}
function t(key) { return stringsFor(currentLanguage)[key] || EN[key] || key; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[char]); }
function formatBytes(bytes) { if (!bytes) return "0 B"; const units = ["B", "KB", "MB", "GB"]; const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3); return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`; }
function formatTime(seconds) { const value = Math.max(0, Math.round(seconds || 0)); return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`; }

function applyLanguage() {
  const strings = stringsFor(currentLanguage);
  document.documentElement.lang = currentLanguage;
  document.documentElement.dir = currentLanguage === "ar" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach(node => { const key = node.dataset.i18n; node.textContent = strings[key] || EN[key] || node.textContent; });
  summaryStyle.innerHTML = [
    ["short", t("summaryShort")], ["standard", t("summaryStandard")], ["detailed", t("summaryDetailed")], ["exam", t("summaryExam")], ["five_minute", t("summaryFive")]
  ].map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join("");
  summaryStyle.value = "standard";
  localStorage.setItem("lecturesift-ui", currentLanguage);
}

Object.entries(LANGUAGES).forEach(([code, label]) => {
  uiLanguage.add(new Option(label, code));
  sourceLanguage.add(new Option(label, code));
  outputLanguage.add(new Option(label, code));
});
sourceLanguage.add(new Option(TR.auto, "auto"), 0);
uiLanguage.value = currentLanguage;
sourceLanguage.value = "auto";
outputLanguage.value = currentLanguage in LANGUAGES ? currentLanguage : "tr";
uiLanguage.addEventListener("change", () => { currentLanguage = uiLanguage.value; applyLanguage(); if (!latestResult) outputLanguage.value = currentLanguage; });
applyLanguage();

function setSourceMode(mode) {
  sourceMode = mode;
  const upload = mode === "upload";
  $("uploadTab").classList.toggle("active", upload); $("uploadTab").setAttribute("aria-selected", upload);
  $("linkTab").classList.toggle("active", !upload); $("linkTab").setAttribute("aria-selected", !upload);
  $("uploadPanel").hidden = !upload; $("uploadPanel").classList.toggle("active", upload);
  $("linkPanel").hidden = upload; $("linkPanel").classList.toggle("active", !upload);
}
$("uploadTab").onclick = () => setSourceMode("upload");
$("linkTab").onclick = () => setSourceMode("link");

function chooseFile(file) {
  if (!file) return;
  selectedVideo = file;
  $("dropZone").hidden = true; $("selectedFile").hidden = false;
  $("selectedFileName").textContent = file.name; $("selectedFileSize").textContent = formatBytes(file.size);
}
videoFile.onchange = () => chooseFile(videoFile.files[0]);
$("removeFile").onclick = () => { selectedVideo = null; videoFile.value = ""; $("dropZone").hidden = false; $("selectedFile").hidden = true; };
["dragenter", "dragover"].forEach(event => $("dropZone").addEventListener(event, e => { e.preventDefault(); $("dropZone").classList.add("dragging"); }));
["dragleave", "drop"].forEach(event => $("dropZone").addEventListener(event, e => { e.preventDefault(); $("dropZone").classList.remove("dragging"); }));
$("dropZone").addEventListener("drop", event => chooseFile(event.dataTransfer.files[0]));

function chooseSlidesFile(file) {
  if (!file) return;
  selectedSlidesVideo = file;
  $("slidesDropZone").hidden = true; $("selectedSlidesFile").hidden = false; $("syncControl").hidden = false;
  $("selectedSlidesFileName").textContent = file.name; $("selectedSlidesFileSize").textContent = formatBytes(file.size);
}
slidesVideoFile.onchange = () => chooseSlidesFile(slidesVideoFile.files[0]);
$("removeSlidesFile").onclick = () => {
  selectedSlidesVideo = null; slidesVideoFile.value = ""; $("slidesOffset").value = "0";
  $("slidesDropZone").hidden = false; $("selectedSlidesFile").hidden = true; $("syncControl").hidden = true;
};
["dragenter", "dragover"].forEach(event => $("slidesDropZone").addEventListener(event, e => { e.preventDefault(); $("slidesDropZone").classList.add("dragging"); }));
["dragleave", "drop"].forEach(event => $("slidesDropZone").addEventListener(event, e => { e.preventDefault(); $("slidesDropZone").classList.remove("dragging"); }));
$("slidesDropZone").addEventListener("drop", event => chooseSlidesFile(event.dataTransfer.files[0]));

function startTimer() {
  timerStarted = Date.now(); clearInterval(timerHandle);
  timerHandle = setInterval(() => { $("elapsedTime").textContent = formatTime((Date.now() - timerStarted) / 1000); }, 1000);
}
function resetStages() { document.querySelectorAll(".stage-list li").forEach(item => { item.className = ""; item.querySelector("b").textContent = "--"; }); }
function setItemState(stage, state) { const item = document.querySelector(`[data-stage="${stage}"]`); if (!item) return; item.className = state; item.querySelector("b").textContent = state === "done" ? "OK" : state === "active" ? "•••" : "--"; }
function updateProgress(percent, label, detail = "") {
  const value = Math.max(0, Math.min(100, Math.round(percent || 0)));
  $("progressPercent").textContent = `${value}%`; $("progressRing").style.setProperty("--progress", `${value * 3.6}deg`);
  $("currentStage").textContent = label; $("stageDetail").textContent = detail || t("processing");
}
function updateJobView(job) {
  $("processTitle").textContent = job.status === "done" ? t("done") : t("processing");
  updateProgress(job.percent, t(job.stage) || t("processing"), t("parallel_analysis"));
  resetStages(); setItemState("url_download", "done");
  const audio = job.tasks?.audio?.percent || 0, visual = job.tasks?.visual?.percent || 0;
  if (audio >= 100) { setItemState("audio", "done"); setItemState("transcription", "done"); }
  else if (audio >= 35) { setItemState("audio", "done"); setItemState("transcription", "active"); }
  else if (job.status === "working") setItemState("audio", "active");
  if (visual >= 100) { setItemState("scene_scan", "done"); setItemState("slide_validation", "done"); }
  else if (visual >= 55) { setItemState("scene_scan", "done"); setItemState("slide_validation", "active"); }
  else if (job.status === "working") setItemState("scene_scan", "active");
  if (job.percent >= 90) { setItemState("study_pack", "done"); setItemState("exports", job.status === "done" ? "done" : "active"); }
  else if (job.percent >= 70) setItemState("study_pack", "active");
  if (job.status === "done") document.querySelectorAll(".stage-list li").forEach(item => { item.className = "done"; item.querySelector("b").textContent = "OK"; });
}

function showError(message, code = "LS-SYSTEM-01") {
  const translated = (ERRORS[currentLanguage] || ERRORS.en)[code] || message || t("errorFallback");
  $("errorMessage").textContent = translated; $("errorCode").textContent = `Hata kodu: ${code}`; $("errorBox").hidden = false;
  $("analyzeButton").disabled = false; clearTimeout(pollHandle); clearInterval(timerHandle);
}
$("closeError").onclick = () => { $("errorBox").hidden = true; };
async function responseError(response) {
  const text = await response.text();
  try { const body = JSON.parse(text); const detail = body.detail || body; return {message: detail.message || body.message, code: detail.code || body.code}; }
  catch { return {message: text, code: "LS-SYSTEM-01"}; }
}

function formData() {
  const data = new FormData();
  data.append("source_language", sourceLanguage.value); data.append("output_language", outputLanguage.value);
  data.append("summary_style", summaryStyle.value); data.append("quiz_count", $("quizCount").value); data.append("flashcard_count", $("cardCount").value);
  data.append("translate_transcript", $("translateTranscript").checked ? "true" : "false");
  data.append("slides_offset_seconds", selectedSlidesVideo ? ($("slidesOffset").value || "0") : "0");
  return data;
}

$("analyzeButton").onclick = async () => {
  $("errorBox").hidden = true;
  if (sourceMode === "upload" && !selectedVideo) { videoFile.click(); return; }
  if (sourceMode === "link" && !videoUrl.value.trim()) { showError(TR.urlLabel, "LS-URL-01"); return; }
  $("analyzeButton").disabled = true; $("results").hidden = true; latestResult = null; jobId = null; resetStages(); startTimer();
  updateProgress(2, sourceMode === "link" ? t("url_download") : t("processing"));
  const data = formData();
  if (sourceMode === "link") {
    data.append("video_url", videoUrl.value.trim());
    try {
      const response = await fetch(`${API}/jobs/url`, {method: "POST", body: data});
      if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
      jobId = (await response.json()).job_id; pollJob();
    } catch (error) { showError(error.message, "LS-NETWORK-01"); }
    return;
  }
  data.append("file", selectedVideo);
  if (selectedSlidesVideo) data.append("slides_file", selectedSlidesVideo);
  const request = new XMLHttpRequest(); request.open("POST", `${API}/jobs`);
  request.upload.onprogress = event => { if (event.lengthComputable) updateProgress(Math.min(7, event.loaded / event.total * 7), t("processing")); };
  request.onload = async () => {
    if (request.status < 300) { jobId = JSON.parse(request.responseText).job_id; pollJob(); }
    else { try { const body = JSON.parse(request.responseText); showError(body.detail?.message, body.detail?.code); } catch { showError(request.responseText); } }
  };
  request.onerror = () => showError("", "LS-NETWORK-01"); request.send(data);
};

async function pollJob() {
  try {
    const response = await fetch(`${API}/jobs/${jobId}`, {cache: "no-store"});
    if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
    const job = await response.json(); updateJobView(job);
    if (job.status === "done") { clearInterval(timerHandle); await loadResult(); return; }
    if (job.status === "error") { showError(job.error, job.error_code); return; }
    pollHandle = setTimeout(pollJob, 1300);
  } catch (error) { pollHandle = setTimeout(pollJob, 2500); }
}

async function loadResult() {
  try {
    const response = await fetch(`${API}/jobs/${jobId}/result`, {cache: "no-store"});
    if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
    latestResult = await response.json(); renderResult(latestResult); $("analyzeButton").disabled = false;
  } catch (error) { showError(error.message, "LS-NETWORK-01"); }
}

function renderResult(data) {
  $("resultHeading").textContent = data.title || "LectureSift";
  $("resultMeta").textContent = `${data.slides?.length || 0} ${t("tabSlides")} · ${data.quiz?.length || 0} Quiz · ${data.flashcards?.length || 0} ${t("tabCards")}`;
  $("downloadAll").href = `${API}/jobs/${jobId}/download`;
  $("summaryContent").textContent = data.summary || t("noContent");
  $("keyPoints").innerHTML = (data.key_points || []).map(point => `<div class="key-item"><i>✦</i><span>${escapeHtml(point)}</span></div>`).join("");
  const terms = (data.important_terms || []).map(item => `<div class="note-item"><h3>${escapeHtml(item.term)}</h3><p>${escapeHtml(item.definition)}</p></div>`).join("");
  const notes = (data.notes || []).map(item => `<div class="note-item"><h3>${escapeHtml(item.heading)}</h3><p>${escapeHtml(item.content)}</p><ul>${(item.bullets || []).map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul></div>`).join("");
  const exam = data.exam_focus?.length ? `<div class="note-item"><h3>${escapeHtml(t("summaryExam"))}</h3><ul>${data.exam_focus.map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul></div>` : "";
  $("notesContent").innerHTML = terms + notes + exam || `<div class="empty-state">${escapeHtml(t("noContent"))}</div>`;
  renderTranscript(true);
  $("slidesContent").innerHTML = data.slides?.length ? data.slides.map(slide => `<figure class="slide-card"><img loading="lazy" src="${API}/jobs/${jobId}/slide/${encodeURIComponent(slide.file)}" alt="Slide ${escapeHtml(slide.timestamp)}"><figcaption>${escapeHtml(slide.timestamp || `${slide.second}s`)}</figcaption></figure>`).join("") : `<div class="empty-state">${escapeHtml(t("noSlides"))}</div>`;
  renderQuiz(data.quiz || []); renderCards(); renderFiles(data.artifacts || []);
  $("results").hidden = false; $("results").scrollIntoView({behavior: "smooth", block: "start"});
}

function renderTranscript(translated) {
  if (!latestResult) return;
  $("showTranslated").classList.toggle("active", translated); $("showOriginal").classList.toggle("active", !translated);
  const value = translated ? (latestResult.transcript_translated || latestResult.transcript_original) : latestResult.transcript_original;
  $("transcriptContent").textContent = value || t("noContent");
}
$("showTranslated").onclick = () => renderTranscript(true); $("showOriginal").onclick = () => renderTranscript(false);

function renderQuiz(items) {
  quizScore = 0; quizAnswered = 0; $("quizStatus").textContent = `${t("score")}: 0/${items.length}`;
  $("quizContent").innerHTML = items.map((item, index) => `<div class="quiz-item" data-question="${index}"><h3>${index + 1}. ${escapeHtml(item.question)}</h3><div class="quiz-options">${(item.options || []).map((option, optionIndex) => `<button class="quiz-option" data-option="${optionIndex}">${String.fromCharCode(65 + optionIndex)}. ${escapeHtml(option)}</button>`).join("")}</div><p class="quiz-explanation">${escapeHtml(item.explanation)}</p></div>`).join("") || `<div class="empty-state">${escapeHtml(t("noContent"))}</div>`;
  document.querySelectorAll(".quiz-option").forEach(button => button.onclick = () => {
    const shell = button.closest(".quiz-item"); if (shell.classList.contains("answered")) return;
    const question = items[Number(shell.dataset.question)], selected = Number(button.dataset.option), correct = Number(question.answer_index);
    shell.classList.add("answered"); quizAnswered += 1;
    shell.querySelectorAll(".quiz-option").forEach(option => { option.disabled = true; if (Number(option.dataset.option) === correct) option.classList.add("correct"); });
    if (selected === correct) quizScore += 1; else button.classList.add("wrong");
    $("quizStatus").textContent = `${t("score")}: ${quizScore}/${items.length} · ${quizAnswered}/${items.length}`;
  });
}

function renderCards() {
  const cards = latestResult?.flashcards || [];
  if (!cards.length) { $("cardContent").innerHTML = `<div class="empty-state">${escapeHtml(t("noContent"))}</div>`; return; }
  cardIndex = Math.max(0, Math.min(cardIndex, cards.length - 1)); const item = cards[cardIndex];
  $("cardContent").innerHTML = `<div class="flash-shell"><div class="flash-count">${cardIndex + 1} / ${cards.length}</div><div class="flash-card" id="activeFlash"><div>${cardRevealed ? `<p>${escapeHtml(item.back)}</p>` : `<strong>${escapeHtml(item.front)}</strong><small>${escapeHtml(t("reveal"))}</small>`}</div></div><div class="flash-actions"><button id="previousCard">← ${escapeHtml(t("previous"))}</button><button id="repeatCard">${escapeHtml(t("repeat"))}</button><button id="knowCard" class="know">${escapeHtml(t("know"))}</button><button id="nextCard">${escapeHtml(t("next"))} →</button></div></div>`;
  $("activeFlash").onclick = () => { cardRevealed = !cardRevealed; renderCards(); };
  $("previousCard").onclick = () => { cardIndex = (cardIndex - 1 + cards.length) % cards.length; cardRevealed = false; renderCards(); };
  $("nextCard").onclick = $("knowCard").onclick = () => { cardIndex = (cardIndex + 1) % cards.length; cardRevealed = false; renderCards(); };
  $("repeatCard").onclick = () => { cardRevealed = false; renderCards(); };
}

function renderFiles(files) {
  $("filesContent").innerHTML = files.map(file => `<div class="file-item"><div><strong>${escapeHtml(file.label)}</strong><small>${escapeHtml(file.format)} · ${formatBytes(file.size_bytes)}</small></div><a href="${API}/jobs/${jobId}/artifact/${encodeURIComponent(file.file)}">${escapeHtml(t("download"))}</a></div>`).join("") || `<div class="empty-state">${escapeHtml(t("noContent"))}</div>`;
}

document.querySelectorAll(".result-tab").forEach(button => button.onclick = () => {
  document.querySelectorAll(".result-tab").forEach(item => item.classList.remove("active"));
  document.querySelectorAll(".result-pane").forEach(item => item.classList.remove("active"));
  button.classList.add("active"); $(`pane-${button.dataset.pane}`).classList.add("active");
});
