const API = "https://lecturesift-backend.onrender.com";
const LOCALE_DATA = window.LECTURESIFT_LOCALE_DATA || {
  countries: [], currencies: ["TRY", "USD", "EUR", "GBP"], currencyForCountry: {},
};
const ZERO_DECIMAL_CURRENCIES = new Set(["JPY", "KRW"]);

const LANGUAGES = {
  tr: "Türkçe", en: "English", de: "Deutsch", fr: "Français", es: "Español",
  it: "Italiano", pt: "Português", ru: "Русский", ar: "العربية", zh: "中文",
  ja: "日本語", ko: "한국어", hi: "हिन्दी"
};

const EN = {
  eyebrow: "AI-powered lecture workspace",
  title: "Turn every lecture source into one organized study pack.",
  subtitle: "Add ordered videos, use separate audio and slide recordings, convert video to MP3, or download a supported video URL.",
  sourceTitle: "Add the lecture source", secure: "Secure processing", uploadTab: "Upload file", linkTab: "Use a link",
  dropTitle: "Drop your video here", dropText: "or choose from your device", fileHelp: "MP4, MOV, MKV, or WebM - 1 GB total",
  audioSourceTitle: "Audio sources", audioSourceHelp: "Add audio-bearing recordings in lecture order.",
  slidesSourceTitle: "Visual / slide sources", slidesSourceHelp: "Add slide recordings in lecture order.", addSlidesVideo: "Add slide video",
  required: "Required", optional: "Optional", syncOffset: "Slide time offset", syncOffsetHelp: "Leave at 0 if both recordings started together.",
  classicMode: "Video and audio together", separateMode: "Separate audio and visuals", addVideos: "Add one or more videos", sortHelp: "Reorder files after adding them",
  addAudioFiles: "Add audio videos", addVisualFiles: "Add slide videos", moveUp: "Move up", moveDown: "Move down", remove: "Remove",
  urlLabel: "Video or education-page URL", urlHelp: "LectureSift tries direct media discovery and provider extraction. Login, DRM, membership, or provider restrictions can still block a download.",
  operationType: "Operation", studyPackOption: "Create a study pack", audioExportOption: "Convert video to MP3", downloadVideoOption: "Download video from URL",
  outputFormats: "Output formats", sameLanguageHelp: "Source and output languages match; one transcript will be created.",
  settingsTitle: "Configure the study pack", sourceLanguage: "Video language", outputLanguage: "Output language", summaryStyle: "Summary profile",
  quizCount: "Quiz questions", cardCount: "Flashcards", translateTitle: "Translate transcript", translateHelp: "The original is preserved", analyze: "Analyze lecture",
  processEyebrow: "Live processing center", readyTitle: "Ready to analyze", readyStage: "Waiting for a video",
  readyDetail: "Add a source and choose your settings to see every processing step here.",
  stageReceive: "Receiving video", stageAudio: "Separating audio", stageTranscript: "Transcribing speech", stageVisual: "Scanning visual content",
  stageSlides: "Validating slides", stageStudy: "Structuring the lecture", stageExport: "Preparing files",
  promiseTitle: "One organized result from every source", promiseText: "Choose PDF, Word, or TXT. PDF-only is the default ZIP package.",
  resultEyebrow: "Study pack ready", downloadAll: "Download complete pack", tabSummary: "Summary", tabNotes: "Smart notes", tabTranscript: "Transcript",
  tabSlides: "Slides", tabCards: "Flashcards", tabFiles: "Files", translated: "Translated", original: "Original", errorTitle: "Analysis could not finish",
  summaryShort: "Quick", summaryStandard: "Standard", summaryDetailed: "Detailed", summaryExam: "Exam-focused", summaryFive: "Learn in 5 minutes",
  auto: "Auto detect", noSlides: "No genuine presentation slides were detected in this video.", noContent: "No content was generated for this section.",
  correct: "Correct", incorrect: "Incorrect", score: "Score", reveal: "Reveal answer", previous: "Previous", next: "Next", know: "I know this", repeat: "Repeat",
  download: "Download", processing: "Lecture analysis in progress", done: "Your study pack is ready", parallel_analysis: "Audio and visuals are being analyzed together",
  url_download: "Downloading the video", study_pack: "Creating smart notes and questions", exports: "Preparing selected output files",
  errorFallback: "The request could not be completed. Check the video or link and try again.",
  plansNav: "Plans", login: "Sign in", register: "Create account", logout: "Sign out",
  plansEyebrow: "Transparent usage plans", plansTitle: "Process what you need and upgrade anytime.",
  plansSubtitle: "Bank transfers activate after payment review. Cards and automatic renewal will be added when PayTR is ready.",
  accountTitle: "Create an account or sign in", accountHelp: "Your plan, minutes, and payments are linked to this account.",
  remainingMinutes: "Processing minutes remaining", transferEyebrow: "Bank transfer details", transferTitle: "Your order is ready",
  orderReference: "Order reference", amount: "Amount", accountHolder: "Account holder", sendReceipt: "Email the receipt",
  currentPlan: "Current plan", choosePlan: "Choose plan", popular: "Popular", perMonth: "/ month", oneTime: "one time",
  pendingApproval: "Pending review", loginRequired: "Sign in before choosing a plan.", accountReady: "Account is ready."
};

const TR = {
  eyebrow: "Yapay zekâ destekli ders çalışma alanı",
  title: "Tüm ders kaynaklarını tek düzenli çalışma paketine dönüştür.",
  subtitle: "Sıralı videolar ekle, ses ve slayt kayıtlarını ayır, videoyu MP3'e çevir ya da desteklenen video bağlantısını indir.",
  sourceTitle: "Ders kaynağını ekle", secure: "Güvenli işlem", uploadTab: "Dosya yükle", linkTab: "Bağlantı kullan",
  dropTitle: "Videoyu buraya bırak", dropText: "veya cihazından seç", fileHelp: "MP4, MOV, MKV veya WebM - toplam en fazla 1 GB",
  audioSourceTitle: "Ses kaynakları", audioSourceHelp: "Sesli kayıtları ders sırasına göre ekle.",
  slidesSourceTitle: "Görüntü / slayt kaynakları", slidesSourceHelp: "Slayt kayıtlarını ders sırasına göre ekle.", addSlidesVideo: "Slayt videosu ekle",
  required: "Zorunlu", optional: "İsteğe bağlı", syncOffset: "Slayt zaman farkı", syncOffsetHelp: "Aynı anda başladıysa 0 bırak.",
  classicMode: "Video ve ses birlikte", separateMode: "Ses ve görüntü ayrı", addVideos: "Bir veya birden fazla video ekle", sortHelp: "Dosyalar eklenince sıralarını değiştirebilirsin",
  addAudioFiles: "Ses videolarını ekle", addVisualFiles: "Slayt videolarını ekle", moveUp: "Yukarı taşı", moveDown: "Aşağı taşı", remove: "Kaldır",
  urlLabel: "Video veya eğitim sayfası bağlantısı", urlHelp: "LectureSift doğrudan medya bulmayı ve sağlayıcı indirmesini dener. Giriş, DRM, üyelik veya sağlayıcı engeli olan içerikler indirilemeyebilir.",
  operationType: "İşlem türü", studyPackOption: "Ders çalışma paketi hazırla", audioExportOption: "Videoyu MP3'e çevir", downloadVideoOption: "URL'den video indir",
  outputFormats: "Çıktı biçimleri", sameLanguageHelp: "Kaynak ve çıktı dili aynı; tek transkript oluşturulacak.",
  settingsTitle: "Çalışma paketini ayarla", sourceLanguage: "Video dili", outputLanguage: "Çıktı dili", summaryStyle: "Özet profili",
  quizCount: "Quiz sorusu", cardCount: "Bilgi kartı", translateTitle: "Transkripti çevir", translateHelp: "Orijinali de korunur", analyze: "Dersi analiz et",
  processEyebrow: "Canlı işlem merkezi", readyTitle: "Analize hazır", readyStage: "Video bekleniyor",
  readyDetail: "Kaynağı ekleyip ayarlarını seçtiğinde işlem adımlarını burada canlı göreceksin.",
  stageReceive: "Video alınıyor", stageAudio: "Ses ayrıştırılıyor", stageTranscript: "Konuşma çözümleniyor", stageVisual: "Görsel içerik taranıyor",
  stageSlides: "Slaytlar doğrulanıyor", stageStudy: "Ders yapılandırılıyor", stageExport: "Çıktılar hazırlanıyor",
  promiseTitle: "Tüm kaynaklardan tek düzenli sonuç", promiseText: "PDF, Word veya TXT seçebilirsin. Varsayılan ZIP yalnızca PDF içerir.",
  resultEyebrow: "Ders paketi hazır", downloadAll: "Tüm paketi indir", tabSummary: "Özet", tabNotes: "Akıllı notlar", tabTranscript: "Transkript",
  tabSlides: "Slaytlar", tabCards: "Bilgi kartları", tabFiles: "Dosyalar", translated: "Çevrilmiş", original: "Orijinal", errorTitle: "İşlem tamamlanamadı",
  summaryShort: "Hızlı", summaryStandard: "Standart", summaryDetailed: "Derinlemesine", summaryExam: "Sınav odaklı", summaryFive: "5 dakikada öğren",
  auto: "Otomatik algıla", noSlides: "Bu videoda gerçek bir sunum slaytı tespit edilmedi.", noContent: "Bu bölüm için içerik üretilemedi.",
  correct: "Doğru", incorrect: "Yanlış", score: "Skor", reveal: "Cevabı göster", previous: "Önceki", next: "Sonraki", know: "Biliyorum", repeat: "Tekrar et",
  download: "İndir", processing: "Ders analizi sürüyor", done: "Çalışma paketin hazır", parallel_analysis: "Ses ve görüntü birlikte analiz ediliyor",
  url_download: "Video bağlantıdan alınıyor", study_pack: "Akıllı notlar ve sorular hazırlanıyor", exports: "Seçilen çıktı dosyaları hazırlanıyor",
  errorFallback: "İşlem tamamlanamadı. Videoyu veya bağlantıyı kontrol edip yeniden deneyebilirsin.",
  plansNav: "Planlar", login: "Giriş", register: "Hesap oluştur", logout: "Çıkış",
  plansEyebrow: "Şeffaf kullanım planları", plansTitle: "İhtiyacın kadar işle, istediğin zaman yükselt.",
  plansSubtitle: "Havale ödemeleri ödeme kontrolünden sonra etkinleştirilir. PayTR açıldığında kart ve otomatik yenileme eklenecek.",
  accountTitle: "Hesabını oluştur veya giriş yap", accountHelp: "Planın, dakikaların ve ödemelerin bu hesaba bağlanır.",
  remainingMinutes: "Kalan işlem dakikası", transferEyebrow: "Havale bilgileri", transferTitle: "Siparişin oluşturuldu",
  orderReference: "Sipariş referansı", amount: "Tutar", accountHolder: "Hesap sahibi", sendReceipt: "Dekontu e-postayla gönder",
  currentPlan: "Mevcut plan", choosePlan: "Planı seç", popular: "Popüler", perMonth: "/ ay", oneTime: "tek ödeme",
  pendingApproval: "Kontrol bekliyor", loginRequired: "Plan seçmeden önce giriş yap.", accountReady: "Hesabın hazır."
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

const PLAN_COPY = {
  tr: {
    free: ["Ücretsiz", "LectureSift'i denemek ve kısa dersleri işlemek için.", "60 dk / ay"],
    credit: ["Dakika Paketi", "Abonelik olmadan ek işlem hakkı.", "180 dakika"],
    lite: ["Lite", "Düzenli bireysel ders çalışması için.", "600 dk / ay"],
    plus: ["Plus", "Yoğun ders dönemi ve çoklu kaynaklar için.", "2.400 dk / ay"],
    pro: ["Pro", "Uzun kayıtlar ve öncelikli işleme için.", "6.000 dk / ay"],
    max: ["Max", "En yüksek bireysel kullanım kapasitesi.", "15.000 dk / ay"],
    business: ["Business", "Ekipler, kurumlar ve özel kapasite için.", "10 kullanıcı"]
  },
  en: {
    free: ["Free", "Try LectureSift and process short lectures.", "60 min / month"],
    credit: ["Minute Pack", "Extra processing without a subscription.", "180 minutes"],
    lite: ["Lite", "For regular individual study.", "600 min / month"],
    plus: ["Plus", "For intensive study and multiple sources.", "2,400 min / month"],
    pro: ["Pro", "For long recordings and priority processing.", "6,000 min / month"],
    max: ["Max", "The highest individual processing capacity.", "15,000 min / month"],
    business: ["Business", "For teams, institutions, and custom capacity.", "10 seats"]
  }
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
  tr: {
    "LS-AI-01": "Yapay zekâ kullanım kotası doldu. Hesap kotası yenilendikten sonra tekrar dene.",
    "LS-AI-02": "Yapay zekâ hizmeti yoğun. Birkaç dakika sonra tekrar dene.",
    "LS-URL-02": "Video sağlayıcısı sunucu üzerinden indirmeyi engelledi. Dosyayı yükle veya doğrudan MP4/WebM bağlantısı kullan.",
    "LS-URL-03": "Bu sayfada indirilebilir video bulunamadı. Doğrudan video bağlantısı kullan veya dosyayı yükle.",
    "LS-UPLOAD-02": "Video izin verilen dosya boyutundan büyük.",
    "LS-VIDEO-02": "Video okunamadı. Dosya bozuk olabilir veya desteklenmeyen bir codec kullanıyor olabilir."
  }
};

const $ = (id) => document.getElementById(id);
const uiLanguage = $("uiLanguage"), sourceLanguage = $("sourceLanguage"), outputLanguage = $("outputLanguage");
const summaryStyle = $("summaryStyle"), videoUrl = $("videoUrl"), jobType = $("jobType");
let currentLanguage = localStorage.getItem("lecturesift-ui") || "tr";
let sourceMode = "upload", sourceLayout = "classic", classicVideos = [], audioVideos = [], visualVideos = [];
let jobId = null, timerStarted = null, timerHandle = null, pollHandle = null;
let latestResult = null, cardIndex = 0, cardRevealed = false, quizScore = 0, quizAnswered = 0;
let billingToken = localStorage.getItem("lecturesift-billing-token") || "";
let billingAccount = null, billingCatalog = null;
let billingCurrency = localStorage.getItem("lecturesift-currency") || "";

function stringsFor(language) {
  if (language === "tr") return TR;
  if (language === "en") return EN;
  const exact = window.LectureSiftI18n?.exact;
  return Object.fromEntries(Object.entries(TR).map(([key, value]) => [key, exact?.(value) || value]));
}
function t(key) { return stringsFor(currentLanguage)[key] || EN[key] || key; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[char]); }
function formatBytes(bytes) { if (!bytes) return "0 B"; const units = ["B", "KB", "MB", "GB"]; const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3); return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`; }
function formatTime(seconds) { const value = Math.max(0, Math.round(seconds || 0)); return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`; }

function applyLanguage() {
  const strings = stringsFor(currentLanguage);
  document.documentElement.lang = currentLanguage;
  document.documentElement.dir = currentLanguage === "ar" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach(node => {
    const key = node.dataset.i18n;
    node.textContent = strings[key] || EN[key] || node.textContent;
  });
  const central = window.LectureSiftI18n;
  summaryStyle.innerHTML = [
    ["short", central?.t("summary.short", t("summaryShort")) || t("summaryShort")],
    ["standard", central?.t("summary.standard", t("summaryStandard")) || t("summaryStandard")],
    ["detailed", central?.t("summary.detailed", t("summaryDetailed")) || t("summaryDetailed")],
    ["exam", central?.t("summary.exam", t("summaryExam")) || t("summaryExam")],
    ["five_minute", central?.t("summary.fiveMinute", t("summaryFive")) || t("summaryFive")]
  ].map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join("");
  summaryStyle.value = "standard";
  const autoOption = [...sourceLanguage.options].find(option => option.value === "auto");
  if (autoOption) autoOption.textContent = t("auto");
  localStorage.setItem("lecturesift-ui", currentLanguage);
  ["classic", "audio", "visual"].forEach(renderFileList);
  syncTranslationChoice(); updateOperationUI();
  if (billingCatalog) renderPlans();
  renderBillingAccount();
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
uiLanguage.addEventListener("change", () => { currentLanguage = uiLanguage.value; applyLanguage(); if (!latestResult) outputLanguage.value = currentLanguage; syncTranslationChoice(); });
applyLanguage();

const PLAN_ORDER = ["free", "credit", "lite", "plus", "pro", "max", "business"];
const PLAN_FALLBACK = {
  free: ["free", 60, 10, 20, ["pdf"], ["short", "standard"], "standard", false],
  credit: ["one_time", 180, 20, 40, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "standard", false],
  lite: ["subscription", 600, 20, 40, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "standard", false],
  plus: ["subscription", 2400, 30, 60, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "standard", true],
  pro: ["subscription", 6000, 30, 60, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "priority", false],
  max: ["subscription", 15000, 30, 60, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "priority", false],
  business: ["quote", null, null, null, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "priority", false],
};
const FALLBACK_PRICES = {
  TRY: [0,19900,34900,69900,129900,249900,null], USD: [0,500,900,1800,3300,6300,null],
  EUR: [0,500,900,1700,3100,5900,null], GBP: [0,400,800,1500,2700,5200,null],
  CAD: [0,700,1200,2500,4500,8500,null], AUD: [0,800,1400,2800,5200,9900,null],
  NZD: [0,900,1600,3100,5700,10900,null], JPY: [0,800,1400,2800,5000,9500,null],
  KRW: [0,7000,13000,25000,47000,89000,null], CNY: [0,3600,6500,12900,23900,45900,null],
  INR: [0,39900,74900,149900,279900,529900,null], BRL: [0,2500,4500,8900,16900,31900,null],
  MXN: [0,9900,17900,34900,64900,124900,null], CHF: [0,500,800,1600,3000,5700,null],
  SEK: [0,5500,9900,19900,36900,69900,null], NOK: [0,5900,10900,21900,39900,76900,null],
  DKK: [0,3500,6500,12900,22900,44900,null], PLN: [0,2000,3600,7200,13200,25200,null],
  AED: [0,1900,3300,6600,12100,23100,null], SAR: [0,1900,3400,6800,12400,23600,null],
  SGD: [0,700,1200,2400,4500,8500,null], HKD: [0,3900,7000,14000,26000,49000,null],
};

function fallbackCatalog(currency) {
  const selected = FALLBACK_PRICES[currency] ? currency : "TRY";
  return {selected_currency:selected, supported_currencies:Object.keys(FALLBACK_PRICES), plans:PLAN_ORDER.map((code, index) => {
    const [kind, minutes, quiz, cards, formats, summaries, priority, featured] = PLAN_FALLBACK[code];
    const amount = FALLBACK_PRICES[selected][index];
    return {code, kind, minutes, priority, featured, display_price:amount == null ? null : {currency:selected, amount_minor:amount}, entitlements:{minutes, quiz_questions:quiz, flashcards:cards, export_formats:formats, summary_profiles:summaries, priority}};
  })};
}

function planCopy(code) {
  const fallback = PLAN_COPY.tr[code] || [code, "", ""];
  if (currentLanguage === "tr") return fallback;
  if (currentLanguage === "en") return PLAN_COPY.en[code] || fallback;
  const central = window.LectureSiftI18n;
  const amount = {free:60, credit:180, lite:600, plus:2400, pro:6000, max:15000, business:10}[code];
  const units = code === "business"
    ? central?.t("plans.userUnit", "kullanıcı")
    : code === "credit"
      ? central?.t("plans.minuteUnit", "dakika")
      : central?.t("plans.minutesPerMonth", "dk / ay");
  return [
    central?.t(`plan.${code}`, fallback[0]) || fallback[0],
    central?.t(`plan.${code}.description`, fallback[1]) || fallback[1],
    `${Number(amount).toLocaleString(central?.locale || navigator.language)} ${units}`,
  ];
}

function detectedCurrency() {
  if (LOCALE_DATA.currencies.includes(billingCurrency)) return billingCurrency;
  const savedCountry = localStorage.getItem("lecturesift-country");
  const region = (navigator.language.split("-")[1] || "").toUpperCase();
  return LOCALE_DATA.currencyForCountry[(savedCountry || region).toUpperCase()] || "USD";
}

function formatPrice(amountMinor, currency = detectedCurrency()) {
  const divisor = ZERO_DECIMAL_CURRENCIES.has(currency) ? 1 : 100;
  return new Intl.NumberFormat(currentLanguage === "tr" ? "tr-TR" : navigator.language, {
    style: "currency", currency, maximumFractionDigits: divisor === 1 ? 0 : 2
  }).format((amountMinor || 0) / divisor);
}

function currencyLabel(code) {
  try {
    const parts = new Intl.NumberFormat(navigator.language, {style:"currency", currency:code}).formatToParts(0);
    return `${code} ${parts.find(part => part.type === "currency")?.value || code}`;
  } catch { return code; }
}

function populateBillingCurrencies() {
  if (!$("billingCurrency")) return;
  const selected = detectedCurrency();
  $("billingCurrency").replaceChildren(...LOCALE_DATA.currencies.map(code => new Option(currencyLabel(code), code)));
  $("billingCurrency").value = selected;
}

function normalizeBillingCatalog(remote, selected) {
  const fallback = fallbackCatalog(selected);
  const remotePlans = new Map((remote?.plans || []).map(plan => [plan.code, plan]));
  return {
    ...(remote || {}), selected_currency:selected, supported_currencies:LOCALE_DATA.currencies,
    plans:fallback.plans.map(fallbackPlan => {
      const plan = remotePlans.get(fallbackPlan.code) || {};
      const remotePrice = plan.display_price;
      return {
        ...fallbackPlan, ...plan,
        display_price:remotePrice?.currency === selected ? remotePrice : fallbackPlan.display_price,
        entitlements:{...fallbackPlan.entitlements, ...(plan.entitlements || {})},
      };
    }),
  };
}

function renderBillingAccount() {
  const loggedIn = Boolean(billingAccount && billingToken);
  if ($("authForm")) $("authForm").hidden = loggedIn;
  if ($("accountStatus")) $("accountStatus").hidden = !loggedIn;
  if ($("accountButton")) {
    $("accountButton").textContent = loggedIn ? (billingAccount.user.first_name || billingAccount.user.email) : t("login");
    $("accountButton").href = loggedIn ? "/account.html" : "/login.html";
  }
  if (!loggedIn) return;
  $("accountEmail").textContent = billingAccount.user.email;
  $("accountPlan").textContent = `${t("currentPlan")}: ${planCopy(billingAccount.plan.code)[0]}`;
  $("accountRemaining").textContent = billingAccount.remaining_minutes == null ? "∞" : billingAccount.remaining_minutes.toLocaleString(currentLanguage);
}

async function billingRequest(path, options = {}) {
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  if (billingToken) headers.Authorization = `Bearer ${billingToken}`;
  const response = await fetch(`${API}${path}`, {...options, headers});
  if (!response.ok) {
    const error = await responseError(response);
    throw Object.assign(new Error(error.message), {code: error.code});
  }
  return response.json();
}

async function protectedFetch(path) {
  const response = await fetch(`${API}${path}`, {
    cache: "no-store",
    headers: {Authorization: `Bearer ${billingToken}`},
  });
  if (!response.ok) {
    const error = await responseError(response);
    throw Object.assign(new Error(error.message), {code: error.code});
  }
  return response;
}

async function downloadProtected(path, filename) {
  try {
    const response = await protectedFetch(path);
    const objectUrl = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = objectUrl; anchor.download = filename || "LectureSift";
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (error) { showError(error.message, error.code || "LS-NETWORK-01"); }
}

async function refreshBillingAccount() {
  if (!billingToken) { billingAccount = null; renderBillingAccount(); renderPlans(); return; }
  try {
    const body = await billingRequest("/billing/me");
    billingAccount = body.account;
  } catch {
    billingToken = ""; billingAccount = null; localStorage.removeItem("lecturesift-billing-token");
  }
  renderBillingAccount(); renderPlans();
}

function renderPlans() {
  if (!billingCatalog || !$("plansGrid")) return;
  const plans = new Map(billingCatalog.plans.map(plan => [plan.code, plan]));
  $("plansGrid").innerHTML = PLAN_ORDER.map(code => {
    const plan = plans.get(code); if (!plan) return "";
    const copy = planCopy(code), current = billingAccount?.plan?.code === code;
    const fallback = PLAN_FALLBACK[code];
    const entitlements = plan.entitlements || {
      quiz_questions: plan.quiz_questions ?? fallback[2],
      flashcards: plan.flashcards ?? fallback[3],
      summary_profiles: plan.summary_profiles || fallback[5],
      export_formats: plan.export_formats || fallback[4],
    };
    const priceInfo = plan.display_price || plan.manual_price;
    const price = priceInfo ? formatPrice(priceInfo.amount_minor, priceInfo.currency || billingCatalog.selected_currency) : (code === "free" ? formatPrice(0, billingCatalog.selected_currency) : (window.LectureSiftI18n?.t("plans.quote", "Teklif") || "Teklif"));
    const suffix = plan.kind === "subscription" ? t("perMonth") : (plan.kind === "one_time" ? t("oneTime") : "");
    const buttonLabel = current ? t("currentPlan") : (code === "business" ? (window.LectureSiftI18n?.t("plans.contact", "Bize ulaş") || "Bize ulaş") : t("choosePlan"));
    const summaryNames = {short:"summary.short",standard:"summary.standard",detailed:"summary.detailed",exam:"summary.exam",five_minute:"summary.fiveMinute"};
    const local = (key, fallback) => window.LectureSiftI18n?.t(key, fallback) || fallback;
    return `<article class="plan-card ${plan.featured ? "featured" : ""}">
      ${plan.featured ? `<span class="plan-badge">${escapeHtml(t("popular"))}</span>` : ""}
      <h3>${escapeHtml(copy[0])}</h3><p>${escapeHtml(copy[1])}</p>
      <div class="plan-price">${escapeHtml(price)} <small>${escapeHtml(suffix)}</small></div>
      <ul class="plan-features"><li>${escapeHtml(copy[2])}</li><li>${entitlements.quiz_questions ?? "∞"} ${escapeHtml(local("plans.quizShort", "quiz sorusu"))}</li><li>${entitlements.flashcards ?? "∞"} ${escapeHtml(local("plans.cardsShort", "bilgi kartı"))}</li><li>${escapeHtml((entitlements.summary_profiles || []).map(profile => local(summaryNames[profile], profile)).join(", "))} ${escapeHtml(local("plans.summaryShort", "özet"))}</li><li>${escapeHtml((entitlements.export_formats || []).join(", ").toUpperCase())}</li><li>${escapeHtml(local(plan.priority === "priority" ? "priority.priority" : "priority.standard", plan.priority === "priority" ? "Öncelikli" : "Standart"))} ${escapeHtml(local("plans.processingSuffix", "işleme"))}</li></ul>
      <button class="plan-action" type="button" data-plan="${escapeHtml(code)}" ${current || code === "free" || code === "business" ? "disabled" : ""}>${escapeHtml(buttonLabel)}</button>
    </article>`;
  }).join("");
  document.querySelectorAll(".plan-action[data-plan]").forEach(button => button.onclick = () => createTransferOrder(button.dataset.plan));
}

async function createTransferOrder(planCode) {
  if (!billingToken) { location.href = `/login.html?next=${encodeURIComponent("/#plans")}`; return; }
  if (billingCurrency !== "TRY") { showError(window.LectureSiftI18n?.t("plans.globalPending", "Global kart ödemeleri PayTR etkinleştiğinde açılacak. Şimdilik havale için TRY seçebilirsin.") || "Global kart ödemeleri PayTR etkinleştiğinde açılacak. Şimdilik havale için TRY seçebilirsin.", "LS-BILL-20"); return; }
  const plan = billingCatalog.plans.find(item => item.code === planCode);
  const interval = plan?.kind === "one_time" ? "one_time" : "monthly";
  try {
    const body = await billingRequest("/billing/manual-transfer/orders", {method:"POST", body:JSON.stringify({plan_code:planCode, interval})});
    const order = body.order;
    $("transferReference").textContent = order.reference;
    $("transferAmount").textContent = formatPrice(order.amount_minor, order.currency || "TRY");
    $("transferIban").textContent = order.bank.iban.replace(/(.{4})/g, "$1 ").trim();
    $("transferHolder").textContent = order.bank.account_holder;
    $("transferInstruction").textContent = order.instruction;
    $("transferStatus").textContent = t("pendingApproval");
    $("transferSupport").href = `mailto:${encodeURIComponent(order.support_email)}?subject=${encodeURIComponent(`LectureSift ${order.reference}`)}`;
    $("transferPanel").hidden = false;
    $("transferPanel").scrollIntoView({behavior:"smooth", block:"center"});
  } catch (error) { showError(error.message, error.code || "LS-BILL-13"); }
}

async function loadBilling() {
  try {
    const selected = detectedCurrency();
    const remoteCatalog = await fetch(`${API}/billing/plans?currency=${encodeURIComponent(selected)}`, {cache:"no-store"}).then(response => response.json());
    billingCatalog = normalizeBillingCatalog(remoteCatalog, selected);
    billingCurrency = selected;
    localStorage.setItem("lecturesift-currency", billingCurrency);
    if ($("billingCurrency")) $("billingCurrency").value = billingCurrency;
    renderPlans(); await refreshBillingAccount();
  } catch {
    billingCatalog = fallbackCatalog(detectedCurrency());
    billingCurrency = billingCatalog.selected_currency;
    if ($("billingCurrency")) $("billingCurrency").value = billingCurrency;
    renderPlans();
    await refreshBillingAccount();
  }
}

if ($("billingCurrency")) $("billingCurrency").addEventListener("change", () => {
  billingCurrency = $("billingCurrency").value;
  localStorage.setItem("lecturesift-currency", billingCurrency);
  loadBilling();
});
populateBillingCurrencies();
if ($("logoutButton")) $("logoutButton").onclick = () => { billingToken = ""; billingAccount = null; localStorage.removeItem("lecturesift-billing-token"); renderBillingAccount(); renderPlans(); };
loadBilling();

function setSourceMode(mode) {
  sourceMode = mode;
  const upload = mode === "upload";
  $("uploadTab").classList.toggle("active", upload); $("uploadTab").setAttribute("aria-selected", upload);
  $("linkTab").classList.toggle("active", !upload); $("linkTab").setAttribute("aria-selected", !upload);
  $("uploadPanel").hidden = !upload; $("uploadPanel").classList.toggle("active", upload);
  $("linkPanel").hidden = upload; $("linkPanel").classList.toggle("active", !upload);
}
$("uploadTab").onclick = () => { if (jobType.value === "download_video") jobType.value = "study_pack"; setSourceMode("upload"); updateOperationUI(); };
$("linkTab").onclick = () => setSourceMode("link");

function filesFor(role) {
  if (role === "classic") return classicVideos;
  if (role === "audio") return audioVideos;
  return visualVideos;
}
function addFiles(role, incoming) {
  const current = filesFor(role), known = new Set(current.map(file => `${file.name}:${file.size}:${file.lastModified}`));
  for (const file of incoming) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (!known.has(key)) { current.push(file); known.add(key); }
  }
  renderFileList(role);
}
function renderFileList(role) {
  const files = filesFor(role), list = $(`${role}FileList`);
  list.innerHTML = files.map((file, index) => `
    <div class="source-file-row" draggable="true" data-role="${role}" data-index="${index}">
      <b>${index + 1}</b><div><strong>${escapeHtml(file.name)}</strong><small>${formatBytes(file.size)}</small></div>
      <span class="file-order-actions">
        <button type="button" data-action="up" title="${escapeHtml(t("moveUp"))}" ${index === 0 ? "disabled" : ""}>↑</button>
        <button type="button" data-action="down" title="${escapeHtml(t("moveDown"))}" ${index === files.length - 1 ? "disabled" : ""}>↓</button>
        <button type="button" data-action="remove" title="${escapeHtml(t("remove"))}">×</button>
      </span>
    </div>`).join("");
  list.querySelectorAll(".source-file-row").forEach(row => {
    row.addEventListener("dragstart", event => event.dataTransfer.setData("text/plain", `${role}:${row.dataset.index}`));
    row.addEventListener("dragover", event => event.preventDefault());
    row.addEventListener("drop", event => {
      event.preventDefault(); const [dragRole, dragIndex] = event.dataTransfer.getData("text/plain").split(":");
      if (dragRole !== role) return;
      const reordered = filesFor(role), [moved] = reordered.splice(Number(dragIndex), 1);
      reordered.splice(Number(row.dataset.index), 0, moved); renderFileList(role);
    });
  });
}
document.addEventListener("click", event => {
  const button = event.target.closest(".file-order-actions button"); if (!button) return;
  const row = button.closest(".source-file-row"), role = row.dataset.role, index = Number(row.dataset.index), files = filesFor(role);
  if (button.dataset.action === "remove") files.splice(index, 1);
  if (button.dataset.action === "up" && index > 0) [files[index - 1], files[index]] = [files[index], files[index - 1]];
  if (button.dataset.action === "down" && index < files.length - 1) [files[index + 1], files[index]] = [files[index], files[index + 1]];
  renderFileList(role);
});

function setupPicker(inputId, zoneId, role) {
  const input = $(inputId), zone = $(zoneId);
  input.onchange = () => { addFiles(role, Array.from(input.files || [])); input.value = ""; };
  ["dragenter", "dragover"].forEach(name => zone.addEventListener(name, event => { event.preventDefault(); zone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(name => zone.addEventListener(name, event => { event.preventDefault(); zone.classList.remove("dragging"); }));
  zone.addEventListener("drop", event => addFiles(role, Array.from(event.dataTransfer.files || [])));
}
setupPicker("classicFiles", "classicDropZone", "classic");
setupPicker("audioFiles", "audioDropZone", "audio");
setupPicker("visualFiles", "visualDropZone", "visual");

function setSourceLayout(layout) {
  sourceLayout = layout;
  const classic = layout === "classic";
  $("classicMode").classList.toggle("active", classic); $("separateMode").classList.toggle("active", !classic);
  $("classicSources").hidden = !classic; $("separateSources").hidden = classic;
}
$("classicMode").onclick = () => setSourceLayout("classic");
$("separateMode").onclick = () => setSourceLayout("separate");

function syncTranslationChoice() {
  const same = sourceLanguage.value !== "auto" && sourceLanguage.value === outputLanguage.value;
  $("translateTranscript").disabled = same;
  if (same) $("translateTranscript").checked = false;
  $("translateHelp").textContent = same ? t("sameLanguageHelp") : t("translateHelp");
}
sourceLanguage.addEventListener("change", syncTranslationChoice);
outputLanguage.addEventListener("change", syncTranslationChoice);

function updateOperationUI() {
  const type = jobType.value, study = type === "study_pack";
  $("studySettings").hidden = !study; $("formatChoices").hidden = !study;
  $("recordingModeTabs").hidden = type !== "study_pack";
  if (type === "audio_export") setSourceLayout("classic");
  if (type === "download_video") setSourceMode("link");
  $("analyzeButton").querySelector("span").textContent = type === "audio_export" ? t("audioExportOption") : type === "download_video" ? t("downloadVideoOption") : t("analyze");
}
jobType.addEventListener("change", updateOperationUI);
syncTranslationChoice(); updateOperationUI();

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
  const known = ERRORS.tr[code];
  const translated = known
    ? (currentLanguage === "tr" ? known : currentLanguage === "en" ? ERRORS.en[code] : window.LectureSiftI18n?.exact(known))
    : message || t("errorFallback");
  $("errorMessage").textContent = translated || message || t("errorFallback");
  $("errorCode").textContent = `${window.LectureSiftI18n?.exact("Hata kodu") || "Hata kodu"}: ${code}`; $("errorBox").hidden = false;
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
  data.append("slides_offset_seconds", sourceLayout === "separate" ? ($("slidesOffset").value || "0") : "0");
  data.append("source_layout", sourceLayout); data.append("job_type", jobType.value);
  const formats = [$("formatPdf"), $("formatDocx"), $("formatTxt")].filter(input => input.checked).map(input => input.value);
  if (jobType.value === "study_pack" && !formats.length) { $("formatPdf").checked = true; formats.push("pdf"); }
  data.append("output_formats", formats.join(",") || "pdf");
  return data;
}

$("analyzeButton").onclick = async () => {
  $("errorBox").hidden = true;
  if (!billingToken || !billingAccount) { $("plans").scrollIntoView({behavior:"smooth"}); showError(t("loginRequired"), "LS-BILL-01"); return; }
  const uploadFiles = sourceLayout === "separate" ? [...audioVideos, ...visualVideos] : classicVideos;
  if (sourceMode === "upload" && sourceLayout === "classic" && !classicVideos.length) { $("classicFiles").click(); return; }
  if (sourceMode === "upload" && sourceLayout === "separate" && (!audioVideos.length || !visualVideos.length)) {
    showError("Ses ve görüntü ayrı modunda her iki listeye de en az bir video ekle.", "LS-UPLOAD-03"); return;
  }
  if (sourceMode === "upload" && uploadFiles.reduce((total, file) => total + file.size, 0) > 1024 ** 3) {
    showError("Dosyaların toplam boyutu 1 GB sınırını aşıyor.", "LS-UPLOAD-02"); return;
  }
  if (sourceMode === "link" && !videoUrl.value.trim()) { showError(TR.urlLabel, "LS-URL-01"); return; }
  $("analyzeButton").disabled = true; $("results").hidden = true; latestResult = null; jobId = null; resetStages(); startTimer();
  updateProgress(2, sourceMode === "link" ? t("url_download") : t("processing"));
  const data = formData();
  if (sourceMode === "link") {
    data.append("video_url", videoUrl.value.trim());
    try {
      const response = await fetch(`${API}/jobs/url`, {method: "POST", body: data, headers:{Authorization:`Bearer ${billingToken}`}});
      if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
      jobId = (await response.json()).job_id; pollJob();
    } catch (error) { showError(error.message, "LS-NETWORK-01"); }
    return;
  }
  if (sourceLayout === "separate") {
    audioVideos.forEach(file => data.append("audio_files", file));
    visualVideos.forEach(file => data.append("visual_files", file));
  } else {
    classicVideos.forEach(file => data.append("files", file));
  }
  const request = new XMLHttpRequest(); request.open("POST", `${API}/jobs`);
  request.setRequestHeader("Authorization", `Bearer ${billingToken}`);
  request.upload.onprogress = event => { if (event.lengthComputable) updateProgress(Math.min(7, event.loaded / event.total * 7), t("processing")); };
  request.onload = async () => {
    if (request.status < 300) { jobId = JSON.parse(request.responseText).job_id; pollJob(); }
    else { try { const body = JSON.parse(request.responseText); showError(body.detail?.message, body.detail?.code); } catch { showError(request.responseText); } }
  };
  request.onerror = () => showError("", "LS-NETWORK-01"); request.send(data);
};

async function pollJob() {
  try {
    const response = await fetch(`${API}/jobs/${jobId}`, {cache: "no-store", headers:{Authorization:`Bearer ${billingToken}`}});
    if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
    const job = await response.json(); updateJobView(job);
    if (job.status === "done") { clearInterval(timerHandle); await loadResult(); await refreshBillingAccount(); return; }
    if (job.status === "error") { showError(job.error, job.error_code); return; }
    pollHandle = setTimeout(pollJob, 1300);
  } catch (error) { pollHandle = setTimeout(pollJob, 2500); }
}

async function loadResult() {
  try {
    const response = await fetch(`${API}/jobs/${jobId}/result`, {cache: "no-store", headers:{Authorization:`Bearer ${billingToken}`}});
    if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
    latestResult = await response.json(); renderResult(latestResult); $("analyzeButton").disabled = false;
  } catch (error) { showError(error.message, "LS-NETWORK-01"); }
}

function renderResult(data) {
  $("resultHeading").textContent = data.title || "LectureSift";
  const utilityResult = data.job_type && data.job_type !== "study_pack";
  $("resultMeta").textContent = utilityResult ? `${data.artifacts?.length || 0} ${t("tabFiles")}` : `${data.slides?.length || 0} ${t("tabSlides")} · ${data.quiz?.length || 0} Quiz · ${data.flashcards?.length || 0} ${t("tabCards")}`;
  $("downloadAll").href = "#";
  $("downloadAll").onclick = event => { event.preventDefault(); downloadProtected(`/jobs/${jobId}/download`, "LectureSift_Paketi.zip"); };
  $("summaryContent").textContent = data.summary || t("noContent");
  $("keyPoints").innerHTML = (data.key_points || []).map(point => `<div class="key-item"><i>✦</i><span>${escapeHtml(point)}</span></div>`).join("");
  const terms = (data.important_terms || []).map(item => `<div class="note-item"><h3>${escapeHtml(item.term)}</h3><p>${escapeHtml(item.definition)}</p></div>`).join("");
  const notes = (data.notes || []).map(item => `<div class="note-item"><h3>${escapeHtml(item.heading)}</h3><p>${escapeHtml(item.content)}</p><ul>${(item.bullets || []).map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul></div>`).join("");
  const exam = data.exam_focus?.length ? `<div class="note-item"><h3>${escapeHtml(t("summaryExam"))}</h3><ul>${data.exam_focus.map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul></div>` : "";
  $("notesContent").innerHTML = terms + notes + exam || `<div class="empty-state">${escapeHtml(t("noContent"))}</div>`;
  renderTranscript(true);
  $("slidesContent").innerHTML = data.slides?.length ? data.slides.map(slide => `<figure class="slide-card"><img loading="lazy" data-protected-src="/jobs/${jobId}/slide/${encodeURIComponent(slide.file)}" alt="Slide ${escapeHtml(slide.timestamp)}"><figcaption>${escapeHtml(slide.timestamp || `${slide.second}s`)}</figcaption></figure>`).join("") : `<div class="empty-state">${escapeHtml(t("noSlides"))}</div>`;
  document.querySelectorAll("img[data-protected-src]").forEach(async image => {
    try {
      const response = await protectedFetch(image.dataset.protectedSrc);
      const objectUrl = URL.createObjectURL(await response.blob());
      image.onload = () => URL.revokeObjectURL(objectUrl);
      image.src = objectUrl;
    } catch { image.alt = t("noSlides"); }
  });
  renderQuiz(data.quiz || []); renderCards(); renderFiles(data.artifacts || []);
  document.querySelectorAll(".result-tab").forEach(button => { button.hidden = utilityResult && button.dataset.pane !== "files"; button.classList.toggle("active", utilityResult ? button.dataset.pane === "files" : button.dataset.pane === "summary"); });
  document.querySelectorAll(".result-pane").forEach(pane => pane.classList.remove("active"));
  $(utilityResult ? "pane-files" : "pane-summary").classList.add("active");
  $("results").hidden = false; $("results").scrollIntoView({behavior: "smooth", block: "start"});
}

function renderTranscript(translated) {
  if (!latestResult) return;
  const hasTranslation = Boolean(latestResult.transcript_translated?.trim());
  document.querySelector(".transcript-tools").hidden = !hasTranslation;
  translated = hasTranslation && translated;
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
  $("filesContent").innerHTML = files.map(file => `<div class="file-item"><div><strong>${escapeHtml(file.label)}</strong><small>${escapeHtml(file.format)} · ${formatBytes(file.size_bytes)}</small></div><a href="#" data-artifact="${encodeURIComponent(file.file)}" data-filename="${escapeHtml(file.file)}">${escapeHtml(t("download"))}</a></div>`).join("") || `<div class="empty-state">${escapeHtml(t("noContent"))}</div>`;
  document.querySelectorAll("a[data-artifact]").forEach(anchor => anchor.onclick = event => {
    event.preventDefault();
    downloadProtected(`/jobs/${jobId}/artifact/${anchor.dataset.artifact}`, anchor.dataset.filename);
  });
}

document.querySelectorAll(".result-tab").forEach(button => button.onclick = () => {
  document.querySelectorAll(".result-tab").forEach(item => item.classList.remove("active"));
  document.querySelectorAll(".result-pane").forEach(item => item.classList.remove("active"));
  button.classList.add("active"); $(`pane-${button.dataset.pane}`).classList.add("active");
});
