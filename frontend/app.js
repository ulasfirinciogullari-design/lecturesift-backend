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
  subtitle: "Add video, audio, PDF, Word, PowerPoint, TXT, or Markdown and turn it into one organized study pack.",
  sourceTitle: "Add the lecture source", secure: "Secure processing", uploadTab: "Upload file", linkTab: "Use a link",
  dropTitle: "Drop a source here", dropText: "or choose from your device", fileHelp: "Your per-job upload limits are loading from your active plan.",
  audioSourceTitle: "Audio sources", audioSourceHelp: "Add audio-bearing recordings in lecture order.",
  slidesSourceTitle: "Visual / slide sources", slidesSourceHelp: "Add slide recordings in lecture order.", addSlidesVideo: "Add slide video",
  required: "Required", optional: "Optional", syncOffset: "Slide time offset", syncOffsetHelp: "Leave at 0 if both recordings started together.",
  classicMode: "Video, audio, or document", separateMode: "Separate audio and visuals", addVideos: "Add video, audio, or documents", sortHelp: "PDF, images, Word, PowerPoint, TXT, and Markdown; automatic OCR for scanned pages",
  addAudioFiles: "Add audio videos", addVisualFiles: "Add slide videos", moveUp: "Move up", moveDown: "Move down", remove: "Remove",
  urlLabel: "Video or education-page URL", urlHelp: "LectureSift tries direct media discovery and provider extraction. Login, DRM, membership, or provider restrictions can still block a download.",
  operationType: "Operation", studyPackOption: "Create a study pack", audioExportOption: "Convert video to MP3", downloadVideoOption: "Download video from URL",
  outputFormats: "Downloadable files (optional)", formatOptional: "Leave all unchecked to keep the result only in your account and on the website.",
  sameLanguageHelp: "Source and output languages match; one transcript will be created.", transcriptDisabledHelp: "Select transcript to enable translation.",
  settingsTitle: "Configure the study pack", sourceLanguage: "Source language", outputLanguage: "Output language", summaryStyle: "Summary profile",
  contentSelection: "Choose package contents", contentSelectionHelp: "Only enabled sections are prepared; quizzes and flashcards are optional.",
  presetLabel: "Ready-made package choices", presetFast: "Fast", presetBalanced: "Balanced", presetExam: "Exam", presetTranscript: "Transcript only",
  includeSummary: "Summary and smart notes", includeSummaryHelp: "Key ideas, terms, and exam focus",
  includeTranscript: "Transcript", includeTranscriptHelp: "Source text and optional translation",
  includeSlides: "Slide images", includeSlidesHelp: "Genuine presentation frames in a video", optionalOutput: "Optional",
  quizCount: "Quiz questions", cardCount: "Flashcards", translateTitle: "Translate transcript", translateHelp: "The original is preserved", analyze: "Analyze lecture",
  processEyebrow: "Live processing center", readyTitle: "Ready to analyze", readyStage: "Waiting for a video",
  readyDetail: "Add a source and choose your settings to see every processing step here.",
  stageReceive: "Receiving video", stageAudio: "Separating audio", stageTranscript: "Transcribing speech", stageVisual: "Scanning visual content",
  stageSlides: "Validating slides", stageStudy: "Structuring the lecture", stageExport: "Preparing files",
  stageSource: "Receiving the source", stageDocument: "Extracting text and running OCR", stageMp3: "Converting audio to MP3", stagePackage: "Packaging the file",
  detailMedia: "Audio, transcript, visuals, study content, and exports advance in visible stages.",
  detailDocument: "Native text and scanned pages are processed with OCR before the study pack is prepared.",
  detailMp3: "The source is uploaded, its audio is converted, and the MP3 download is packaged.",
  detailDownload: "The video is downloaded from its source and prepared as a protected file.",
  uploadingSource: "Uploading the source", uploadAccepted: "Upload complete; preparing the processing job", queuedForWorker: "Waiting in the processing queue", publishingResult: "Securing the result files",
  promiseTitle: "Your source, your preferred result", promiseText: "Select only the summary, transcript, slides, quiz, flashcards, and files you need.",
  resultEyebrow: "Study pack ready", downloadAll: "Download complete pack", tabSummary: "Summary", tabNotes: "Smart notes", tabTranscript: "Transcript",
  tabSlides: "Slides", tabCards: "Flashcards", tabFiles: "Files", translated: "Translated", original: "Original", errorTitle: "Analysis could not finish",
  summaryShort: "Quick", summaryStandard: "Standard", summaryDetailed: "Detailed", summaryExam: "Exam-focused", summaryFive: "Learn in 5 minutes",
  auto: "Auto detect", noSlides: "No genuine presentation slides were detected in this video.", noContent: "No content was generated for this section.",
  correct: "Correct", incorrect: "Incorrect", score: "Score", reveal: "Reveal answer", previous: "Previous", next: "Next", know: "I know this", repeat: "Repeat",
  download: "Download", processing: "Lecture analysis in progress", done: "Your study pack is ready", parallel_analysis: "Audio and visuals are being analyzed together",
  url_download: "Downloading the video", study_pack: "Creating smart notes and questions", exports: "Preparing selected output files",
  errorFallback: "The request could not be completed. Check the video or link and try again.", outputSelectionRequired: "Select at least one study output: summary and notes, transcript, quiz, or flashcards.",
  plansNav: "Plans", login: "Sign in", register: "Create account", logout: "Sign out",
  plansEyebrow: "Transparent usage plans", plansTitle: "Process what you need and upgrade anytime.",
  plansSubtitle: "Cards and bank transfers are completed on iyzico's secure page; matched payments activate automatically.",
  accountTitle: "Create an account or sign in", accountHelp: "Your plan, minutes, and payments are linked to this account.",
  remainingMinutes: "Processing minutes remaining", transferEyebrow: "Bank transfer details", transferTitle: "Your order is ready",
  orderReference: "Order reference", amount: "Amount", accountHolder: "Account holder", sendReceipt: "Email the receipt",
  currentPlan: "Current plan", choosePlan: "Choose plan", popular: "Popular", perMonth: "/ month", oneTime: "one time",
  pendingApproval: "Pending review", loginRequired: "Sign in before choosing a plan.", accountReady: "Account is ready."
};

const TR = {
  eyebrow: "Yapay zekâ destekli ders çalışma alanı",
  title: "Tüm ders kaynaklarını tek düzenli çalışma paketine dönüştür.",
  subtitle: "Video, ses, PDF, Word, PowerPoint, TXT veya Markdown ekle; tek düzenli çalışma paketine dönüştür.",
  sourceTitle: "Ders kaynağını ekle", secure: "Güvenli işlem", uploadTab: "Dosya yükle", linkTab: "Bağlantı kullan",
  dropTitle: "Kaynağı buraya bırak", dropText: "veya cihazından seç", fileHelp: "Tek iş yükleme sınırların aktif planından yükleniyor.",
  audioSourceTitle: "Ses kaynakları", audioSourceHelp: "Sesli kayıtları ders sırasına göre ekle.",
  slidesSourceTitle: "Görüntü / slayt kaynakları", slidesSourceHelp: "Slayt kayıtlarını ders sırasına göre ekle.", addSlidesVideo: "Slayt videosu ekle",
  required: "Zorunlu", optional: "İsteğe bağlı", syncOffset: "Slayt zaman farkı", syncOffsetHelp: "Aynı anda başladıysa 0 bırak.",
  classicMode: "Video, ses veya belge", separateMode: "Ses ve görüntü ayrı", addVideos: "Video, ses veya belge ekle", sortHelp: "PDF, görsel, Word, PowerPoint, TXT ve Markdown; taranmış sayfalarda otomatik OCR",
  addAudioFiles: "Ses videolarını ekle", addVisualFiles: "Slayt videolarını ekle", moveUp: "Yukarı taşı", moveDown: "Aşağı taşı", remove: "Kaldır",
  urlLabel: "Video veya eğitim sayfası bağlantısı", urlHelp: "LectureSift doğrudan medya bulmayı ve sağlayıcı indirmesini dener. Giriş, DRM, üyelik veya sağlayıcı engeli olan içerikler indirilemeyebilir.",
  operationType: "İşlem türü", studyPackOption: "Ders çalışma paketi hazırla", audioExportOption: "Videoyu MP3'e çevir", downloadVideoOption: "URL'den video indir",
  outputFormats: "İndirilecek dosyalar (isteğe bağlı)", formatOptional: "Hiçbirini seçmezsen sonuç yalnızca hesabında ve sitede gösterilir.",
  sameLanguageHelp: "Kaynak ve çıktı dili aynı; tek transkript oluşturulacak.", transcriptDisabledHelp: "Çeviriyi açmak için transkripti seç.",
  settingsTitle: "Çalışma paketini ayarla", sourceLanguage: "Kaynak dili", outputLanguage: "Çıktı dili", summaryStyle: "Özet profili",
  contentSelection: "Paket içeriğini seç", contentSelectionHelp: "Yalnızca açtığın bölümler hazırlanır; quiz ve bilgi kartı zorunlu değildir.",
  presetLabel: "Hazır paket seçimleri", presetFast: "Hızlı", presetBalanced: "Dengeli", presetExam: "Sınav", presetTranscript: "Sadece transkript",
  includeSummary: "Özet ve akıllı notlar", includeSummaryHelp: "Ana fikirler, terimler ve sınav odağı",
  includeTranscript: "Transkript", includeTranscriptHelp: "Kaynak metin ve isteğe bağlı çeviri",
  includeSlides: "Slayt görselleri", includeSlidesHelp: "Video içindeki gerçek sunum kareleri", optionalOutput: "İsteğe bağlı",
  quizCount: "Quiz sorusu", cardCount: "Bilgi kartı", translateTitle: "Transkripti çevir", translateHelp: "Orijinali de korunur", analyze: "Dersi analiz et",
  processEyebrow: "Canlı işlem merkezi", readyTitle: "Analize hazır", readyStage: "Video bekleniyor",
  readyDetail: "Kaynağı ekleyip ayarlarını seçtiğinde işlem adımlarını burada canlı göreceksin.",
  stageReceive: "Video alınıyor", stageAudio: "Ses ayrıştırılıyor", stageTranscript: "Konuşma çözümleniyor", stageVisual: "Görsel içerik taranıyor",
  stageSlides: "Slaytlar doğrulanıyor", stageStudy: "Ders yapılandırılıyor", stageExport: "Çıktılar hazırlanıyor",
  stageSource: "Kaynak alınıyor", stageDocument: "Belge metni ve OCR işleniyor", stageMp3: "Ses MP3'e dönüştürülüyor", stagePackage: "Dosya paketleniyor",
  detailMedia: "Ses, transkript, görseller, ders içeriği ve çıktılar ayrı adımlarda ilerler.",
  detailDocument: "Seçilebilir metin ve taranmış sayfalar OCR ile işlenir; ardından çalışma paketi hazırlanır.",
  detailMp3: "Kaynak yüklenir, sesi dönüştürülür ve MP3 indirmesi paketlenir.",
  detailDownload: "Video kaynağından indirilir ve korumalı dosya olarak hazırlanır.",
  uploadingSource: "Kaynak yükleniyor", uploadAccepted: "Yükleme tamamlandı; işlem işi hazırlanıyor", queuedForWorker: "İşlem sırasında bekliyor", publishingResult: "Sonuç dosyaları güvenceye alınıyor",
  promiseTitle: "Kaynağın, istediğin sonuç", promiseText: "Yalnızca ihtiyacın olan özet, transkript, slayt, quiz, bilgi kartı ve dosyaları seç.",
  resultEyebrow: "Ders paketi hazır", downloadAll: "Tüm paketi indir", tabSummary: "Özet", tabNotes: "Akıllı notlar", tabTranscript: "Transkript",
  tabSlides: "Slaytlar", tabCards: "Bilgi kartları", tabFiles: "Dosyalar", translated: "Çevrilmiş", original: "Orijinal", errorTitle: "İşlem tamamlanamadı",
  summaryShort: "Hızlı", summaryStandard: "Standart", summaryDetailed: "Derinlemesine", summaryExam: "Sınav odaklı", summaryFive: "5 dakikada öğren",
  auto: "Otomatik algıla", noSlides: "Bu videoda gerçek bir sunum slaytı tespit edilmedi.", noContent: "Bu bölüm için içerik üretilemedi.",
  correct: "Doğru", incorrect: "Yanlış", score: "Skor", reveal: "Cevabı göster", previous: "Önceki", next: "Sonraki", know: "Biliyorum", repeat: "Tekrar et",
  download: "İndir", processing: "Ders analizi sürüyor", done: "Çalışma paketin hazır", parallel_analysis: "Ses ve görüntü birlikte analiz ediliyor",
  url_download: "Video bağlantıdan alınıyor", study_pack: "Akıllı notlar ve sorular hazırlanıyor", exports: "Seçilen çıktı dosyaları hazırlanıyor",
  errorFallback: "İşlem tamamlanamadı. Videoyu veya bağlantıyı kontrol edip yeniden deneyebilirsin.", outputSelectionRequired: "En az bir çalışma çıktısı seç: özet ve notlar, transkript, quiz veya bilgi kartı.",
  plansNav: "Planlar", login: "Giriş", register: "Hesap oluştur", logout: "Çıkış",
  plansEyebrow: "Şeffaf kullanım planları", plansTitle: "İhtiyacın kadar işle, istediğin zaman yükselt.",
  plansSubtitle: "Kart ve Havale/EFT, iyzico’nun güvenli sayfasında tamamlanır; eşleşen ödeme otomatik etkinleştirilir.",
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
    plus: ["Plus", "Yoğun ders dönemi ve çoklu kaynaklar için.", "1.800 dk / ay"],
    pro: ["Pro", "Uzun kayıtlar ve öncelikli işleme için.", "5.000 dk / ay"],
    max: ["Max", "En yüksek bireysel kullanım kapasitesi.", "12.000 dk / ay"],
    business: ["Business", "Ekipler, kurumlar ve özel kapasite için.", "10 kullanıcı"]
  },
  en: {
    free: ["Free", "Try LectureSift and process short lectures.", "60 min / month"],
    credit: ["Minute Pack", "Extra processing without a subscription.", "180 minutes"],
    lite: ["Lite", "For regular individual study.", "600 min / month"],
    plus: ["Plus", "For intensive study and multiple sources.", "1,800 min / month"],
    pro: ["Pro", "For long recordings and priority processing.", "5,000 min / month"],
    max: ["Max", "The highest individual processing capacity.", "12,000 min / month"],
    business: ["Business", "For teams, institutions, and custom capacity.", "10 seats"]
  }
};

const ERRORS = {
  en: {
    "LS-AI-01": "The AI usage quota is exhausted. Try again after the account quota has been renewed.",
    "LS-AI-02": "The AI service is busy. Try again in a few minutes.",
    "LS-URL-02": "The video provider blocked server-side downloading. Upload the file or use a direct MP4/WebM link.",
    "LS-URL-03": "No downloadable video was found on this page. Use a direct video link or upload the file.",
    "LS-UPLOAD-02": "The selected sources exceed the upload size allowed by your plan.",
    "LS-UPLOAD-04": "The file-processing request could not be completed. Try again, or upload a smaller copy of the document.",
    "LS-UPLOAD-05": "Video and document sources cannot be mixed in one job. Upload them separately.",
    "LS-DOC-01": "The document is empty.",
    "LS-DOC-02": "The document is larger than the allowed file size.",
    "LS-DOC-03": "The Word or PowerPoint document is damaged or cannot be opened safely.",
    "LS-DOC-04": "The PDF could not be read safely.",
    "LS-DOC-05": "Password-protected PDF files are not supported.",
    "LS-DOC-06": "The document or presentation exceeds the page limit.",
    "LS-DOC-07": "The Word document could not be read.",
    "LS-DOC-08": "The PowerPoint presentation could not be read.",
    "LS-DOC-09": "The text file could not be read.",
    "LS-DOC-10": "Add at least one document.",
    "LS-DOC-11": "This document format is not supported.",
    "LS-DOC-12": "OCR finished but no readable text was found. Try a clearer scan or select the source language.",
    "LS-DOC-13": "The documents contain too much text for one safe job. Split the source and try again.",
    "LS-OCR-01": "OCR is temporarily unavailable. Try again shortly.",
    "LS-OCR-02": "The document has too many scanned pages for one OCR job. Split it and try again.",
    "LS-OCR-03": "OCR timed out on a page. Split the document and try again.",
    "LS-OCR-04": "The scanned page or image could not be read safely.",
    "LS-VIDEO-02": "The video could not be read. It may be damaged or use an unsupported codec."
  },
  tr: {
    "LS-AI-01": "Yapay zekâ kullanım kotası doldu. Hesap kotası yenilendikten sonra tekrar dene.",
    "LS-AI-02": "Yapay zekâ hizmeti yoğun. Birkaç dakika sonra tekrar dene.",
    "LS-URL-02": "Video sağlayıcısı sunucu üzerinden indirmeyi engelledi. Dosyayı yükle veya doğrudan MP4/WebM bağlantısı kullan.",
    "LS-URL-03": "Bu sayfada indirilebilir video bulunamadı. Doğrudan video bağlantısı kullan veya dosyayı yükle.",
    "LS-UPLOAD-02": "Seçilen kaynaklar planının izin verdiği yükleme boyutunu aşıyor.",
    "LS-UPLOAD-04": "Dosya işleme isteği tamamlanamadı. Yeniden dene veya belgenin daha küçük bir kopyasını yükle.",
    "LS-UPLOAD-05": "Video ve belge kaynakları aynı işte karıştırılamaz. Ayrı ayrı yükle.",
    "LS-DOC-01": "Belge boş görünüyor.",
    "LS-DOC-02": "Belge izin verilen dosya boyutundan büyük.",
    "LS-DOC-03": "Word veya PowerPoint belgesi bozuk ya da güvenli açılamıyor.",
    "LS-DOC-04": "PDF güvenli biçimde okunamadı.",
    "LS-DOC-05": "Şifreli PDF dosyaları desteklenmiyor.",
    "LS-DOC-06": "Belge veya sunum izin verilen sayfa sınırını aşıyor.",
    "LS-DOC-07": "Word belgesi okunamadı.",
    "LS-DOC-08": "PowerPoint sunumu okunamadı.",
    "LS-DOC-09": "Metin dosyası okunamadı.",
    "LS-DOC-10": "En az bir belge ekle.",
    "LS-DOC-11": "Bu belge biçimi desteklenmiyor.",
    "LS-DOC-12": "OCR tamamlandı ancak okunabilir metin bulunamadı. Daha net bir tarama veya doğru kaynak diliyle yeniden dene.",
    "LS-DOC-13": "Belgelerin toplam metni tek bir güvenli işlem için fazla. Kaynağı bölerek yeniden dene.",
    "LS-OCR-01": "OCR hizmeti geçici olarak kullanılamıyor. Biraz sonra yeniden dene.",
    "LS-OCR-02": "Belgede tek işlem için çok fazla taranmış sayfa var. Belgeyi bölerek yeniden dene.",
    "LS-OCR-03": "Bir sayfanın OCR işlemi zaman sınırını aştı. Belgeyi bölerek yeniden dene.",
    "LS-OCR-04": "Taranmış sayfa veya görsel güvenli biçimde okunamadı.",
    "LS-VIDEO-02": "Video okunamadı. Dosya bozuk olabilir veya desteklenmeyen bir codec kullanıyor olabilir."
  }
};

const $ = (id) => document.getElementById(id);
const uiLanguage = $("uiLanguage"), sourceLanguage = $("sourceLanguage"), outputLanguage = $("outputLanguage");
const summaryStyle = $("summaryStyle"), videoUrl = $("videoUrl"), jobType = $("jobType");
let currentLanguage = window.LectureSiftI18n?.language || localStorage.getItem("lecturesift-ui") || "tr";
let sourceMode = "upload", sourceLayout = "classic", classicVideos = [], audioVideos = [], visualVideos = [];
let jobId = null, timerStarted = null, timerHandle = null, pollHandle = null;
let latestResult = null, cardIndex = 0, cardRevealed = false, quizScore = 0, quizAnswered = 0;
let billingToken = localStorage.getItem("lecturesift-billing-token") || "";
let billingAccount = null, billingCatalog = null;
let billingCurrency = localStorage.getItem("lecturesift-currency") || "";
let requestedJobLoaded = false;
let activeProgressProfile = "";

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
  const selectedSummaryStyle = summaryStyle.value || "standard";
  document.documentElement.lang = currentLanguage;
  document.documentElement.dir = currentLanguage === "ar" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach(node => {
    const key = node.dataset.i18n;
    node.textContent = strings[key] || EN[key] || node.textContent;
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach(node => {
    const key = node.dataset.i18nAriaLabel;
    node.setAttribute("aria-label", strings[key] || EN[key] || node.getAttribute("aria-label") || "");
  });
  const central = window.LectureSiftI18n;
  summaryStyle.innerHTML = [
    ["short", central?.t("summary.short", t("summaryShort")) || t("summaryShort")],
    ["standard", central?.t("summary.standard", t("summaryStandard")) || t("summaryStandard")],
    ["detailed", central?.t("summary.detailed", t("summaryDetailed")) || t("summaryDetailed")],
    ["exam", central?.t("summary.exam", t("summaryExam")) || t("summaryExam")],
    ["five_minute", central?.t("summary.fiveMinute", t("summaryFive")) || t("summaryFive")]
  ].map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join("");
  summaryStyle.value = [...summaryStyle.options].some(option => option.value === selectedSummaryStyle) ? selectedSummaryStyle : "standard";
  const autoOption = [...sourceLanguage.options].find(option => option.value === "auto");
  if (autoOption) autoOption.textContent = t("auto");
  localStorage.setItem("lecturesift-ui", currentLanguage);
  ["classic", "audio", "visual"].forEach(renderFileList);
  syncContentChoices(); updateOperationUI();
  if (billingCatalog) renderPlans();
  renderBillingAccount();
  updateSourceLimitHelp();
}

uiLanguage.replaceChildren();
sourceLanguage.replaceChildren();
outputLanguage.replaceChildren();
Object.entries(LANGUAGES).forEach(([code, label]) => {
  uiLanguage.add(new Option(label, code));
  sourceLanguage.add(new Option(label, code));
  outputLanguage.add(new Option(label, code));
});
sourceLanguage.add(new Option(TR.auto, "auto"), 0);
uiLanguage.value = currentLanguage;
sourceLanguage.value = "auto";
outputLanguage.value = currentLanguage in LANGUAGES ? currentLanguage : "tr";
uiLanguage.addEventListener("change", () => {
  currentLanguage = uiLanguage.value;
  localStorage.setItem("lecturesift-ui", currentLanguage);
  const nextPath = window.LectureSiftI18n?.localizedPath?.(currentLanguage);
  if (nextPath && nextPath !== location.pathname) {
    location.assign(`${nextPath}${location.search}${location.hash}`);
    return;
  }
  applyLanguage();
  if (!latestResult) outputLanguage.value = currentLanguage;
  syncTranslationChoice();
});
const PLAN_ORDER = ["free", "credit", "lite", "plus", "pro", "max", "business"];
const PLAN_FALLBACK = {
  free: ["free", 60, 10, 20, ["pdf"], ["short", "standard"], "standard", false],
  credit: ["one_time", 180, 20, 40, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "standard", false],
  lite: ["subscription", 600, 20, 40, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "standard", false],
  plus: ["subscription", 1800, 30, 60, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "standard", true],
  pro: ["subscription", 5000, 30, 60, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "priority", false],
  max: ["subscription", 12000, 30, 60, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "priority", false],
  business: ["quote", null, null, null, ["pdf", "docx", "txt"], ["short", "standard", "detailed", "exam", "five_minute"], "priority", false],
};
const FALLBACK_PRICES = {
  TRY: [0,19900,27900,44900,99900,199900,null], USD: [0,499,699,999,2499,4999,null],
  EUR: [0,499,649,949,2399,4799,null], GBP: [0,399,599,849,2099,4199,null],
  CAD: [0,699,949,1349,3399,6799,null], AUD: [0,799,1099,1549,3799,7599,null],
  NZD: [0,899,1199,1699,4199,8399,null], JPY: [0,750,1050,1500,3750,7500,null],
  KRW: [0,6900,9500,13900,34900,69900,null], CNY: [0,3500,4900,6900,17500,34900,null],
  INR: [0,39900,54900,79900,199900,399900,null], BRL: [0,2499,3499,4999,12499,24999,null],
  MXN: [0,9900,13900,19900,49900,99900,null], CHF: [0,449,599,849,2199,4399,null],
  SEK: [0,5299,7299,10499,25999,51999,null], NOK: [0,5499,7699,10999,27499,54999,null],
  DKK: [0,3499,4499,6699,16999,33999,null], PLN: [0,1999,2699,3999,9999,19999,null],
  AED: [0,1899,2599,3699,9199,18399,null], SAR: [0,1899,2599,3799,9399,18799,null],
  SGD: [0,699,949,1349,3399,6799,null], HKD: [0,3899,5499,7799,19499,38999,null],
};
const PLAN_SOURCE_LIMITS = {
  free: {max_files_per_job:3, max_media_upload_mb:100, max_document_upload_mb:25, max_minutes_per_job:30, max_document_pages:50, max_ocr_pages:20},
  credit: {max_files_per_job:8, max_media_upload_mb:500, max_document_upload_mb:50, max_minutes_per_job:180, max_document_pages:150, max_ocr_pages:50},
  lite: {max_files_per_job:12, max_media_upload_mb:750, max_document_upload_mb:75, max_minutes_per_job:180, max_document_pages:250, max_ocr_pages:75},
  plus: {max_files_per_job:16, max_media_upload_mb:1024, max_document_upload_mb:100, max_minutes_per_job:300, max_document_pages:350, max_ocr_pages:100},
  pro: {max_files_per_job:24, max_media_upload_mb:1024, max_document_upload_mb:100, max_minutes_per_job:600, max_document_pages:500, max_ocr_pages:150},
  max: {max_files_per_job:24, max_media_upload_mb:1024, max_document_upload_mb:100, max_minutes_per_job:900, max_document_pages:500, max_ocr_pages:150},
  business: {max_files_per_job:24, max_media_upload_mb:1024, max_document_upload_mb:100, max_minutes_per_job:1440, max_document_pages:500, max_ocr_pages:150},
};

function fallbackCatalog(currency) {
  const selected = FALLBACK_PRICES[currency] ? currency : "TRY";
  return {selected_currency:selected, supported_currencies:Object.keys(FALLBACK_PRICES), plans:PLAN_ORDER.map((code, index) => {
    const [kind, minutes, quiz, cards, formats, summaries, priority, featured] = PLAN_FALLBACK[code];
    const amount = FALLBACK_PRICES[selected][index];
    return {code, kind, minutes, priority, featured, display_price:amount == null ? null : {currency:selected, amount_minor:amount}, entitlements:{minutes, quiz_questions:quiz, flashcards:cards, export_formats:formats, summary_profiles:summaries, priority, limits:PLAN_SOURCE_LIMITS[code], download_enabled:code !== "free"}};
  })};
}

function planCopy(code) {
  const fallback = PLAN_COPY.tr[code] || [code, "", ""];
  if (currentLanguage === "tr") return fallback;
  if (currentLanguage === "en") return PLAN_COPY.en[code] || fallback;
  const central = window.LectureSiftI18n;
  const amount = {free:60, credit:180, lite:600, plus:1800, pro:5000, max:12000, business:10}[code];
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
  updateSourceLimitHelp();
  if (!loggedIn) return;
  if ($("accountEmail")) $("accountEmail").textContent = billingAccount.user.email;
  if ($("accountPlan")) $("accountPlan").textContent = `${t("currentPlan")}: ${planCopy(billingAccount.plan.code)[0]}`;
  if ($("accountRemaining")) $("accountRemaining").textContent = billingAccount.remaining_minutes == null ? "∞" : billingAccount.remaining_minutes.toLocaleString(currentLanguage);
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
      <ul class="plan-features"><li>${escapeHtml(copy[2])}</li><li>${entitlements.quiz_questions ?? "∞"} ${escapeHtml(local("plans.quizShort", "quiz sorusu"))}</li><li>${entitlements.flashcards ?? "∞"} ${escapeHtml(local("plans.cardsShort", "bilgi kartı"))}</li><li>${escapeHtml((entitlements.summary_profiles || []).map(profile => local(summaryNames[profile], profile)).join(", "))} ${escapeHtml(local("plans.summaryShort", "özet"))}</li><li>${escapeHtml(entitlements.download_enabled === false ? local("plans.previewOnly", "Sitede önizleme · dosya indirme yok") : (entitlements.export_formats || []).join(", ").toUpperCase())}</li><li>${escapeHtml(local(plan.priority === "priority" ? "priority.priority" : "priority.standard", plan.priority === "priority" ? "Öncelikli" : "Standart"))} ${escapeHtml(local("plans.processingSuffix", "işleme"))}</li></ul>
      <button class="plan-action" type="button" data-plan="${escapeHtml(code)}" ${current || code === "free" || code === "business" ? "disabled" : ""}>${escapeHtml(buttonLabel)}</button>
    </article>`;
  }).join("");
  document.querySelectorAll(".plan-action[data-plan]").forEach(button => button.onclick = () => openPlanCheckout(button.dataset.plan));
}

function openPlanCheckout(planCode) {
  if (!billingToken) { location.href = `/login.html?next=${encodeURIComponent("/plans.html")}`; return; }
  const plan = billingCatalog.plans.find(item => item.code === planCode);
  const interval = plan?.kind === "one_time" ? "one_time" : "monthly";
  const query = new URLSearchParams({plan: planCode, interval});
  location.href = `/plans.html?${query}`;
}

async function loadBilling() {
  try {
    const selected = detectedCurrency();
    const remoteCatalog = await fetch(`${API}/billing/plans?currency=${encodeURIComponent(selected)}`, {cache:"no-store"}).then(response => response.json());
    billingCatalog = normalizeBillingCatalog(remoteCatalog, selected);
    billingCurrency = selected;
    localStorage.setItem("lecturesift-currency", billingCurrency);
    if ($("billingCurrency")) $("billingCurrency").value = billingCurrency;
    renderPlans(); await refreshBillingAccount(); await restoreRequestedJob();
  } catch {
    billingCatalog = fallbackCatalog(detectedCurrency());
    billingCurrency = billingCatalog.selected_currency;
    if ($("billingCurrency")) $("billingCurrency").value = billingCurrency;
    renderPlans();
    await refreshBillingAccount(); await restoreRequestedJob();
  }
}

async function restoreRequestedJob() {
  if (requestedJobLoaded || !billingToken || !billingAccount) return;
  const requested = new URLSearchParams(location.search).get("job") || "";
  if (!/^[A-Za-z0-9-]{8,80}$/.test(requested)) return;
  requestedJobLoaded = true;
  jobId = requested;
  try {
    const response = await fetch(`${API}/jobs/${encodeURIComponent(jobId)}`, {cache:"no-store", headers:{Authorization:`Bearer ${billingToken}`}});
    if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
    const job = await response.json();
    updateJobView(job);
    if (job.status === "done") await loadResult();
    else if (job.status === "error") showError(job.error, job.error_code);
    else { startTimer(); pollJob(); }
  } catch (error) {
    showError(error.message, "LS-NETWORK-01");
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
const DOCUMENT_EXTENSIONS = new Set(["pdf", "docx", "pptx", "txt", "md", "png", "jpg", "jpeg", "webp", "tif", "tiff"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "mov", "mkv", "webm", "mpeg", "mpg", "m4v"]);
const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "m4a", "aac", "flac", "ogg", "oga", "opus", "wma", "aiff", "aif", "mka"]);
const MEDIA_EXTENSIONS = new Set([...VIDEO_EXTENSIONS, ...AUDIO_EXTENSIONS]);
const DEFAULT_SOURCE_LIMITS = {max_files_per_job:3, max_media_upload_mb:100, max_document_upload_mb:25};
function activeSourceLimits() {
  return billingAccount?.job_entitlements?.limits || billingAccount?.plan?.entitlements?.limits || DEFAULT_SOURCE_LIMITS;
}
function updateSourceLimitHelp() {
  const node = $("sourceLimitHelp");
  if (!node) return;
  const limits = activeSourceLimits();
  const central = window.LectureSiftI18n;
  const locale = central?.locale || (currentLanguage === "tr" ? "tr-TR" : navigator.language);
  const number = value => Number(value || 0).toLocaleString(locale);
  const prefix = central?.t("upload.activePlanLimits", "Aktif plan · tek işte") || "Aktif plan · tek işte";
  const media = central?.t("plans.mediaShort", "medya") || "medya";
  const documentLabel = central?.t("plans.documentShort", "belge") || "belge";
  const sources = central?.t("plans.filesPerJobShort", "kaynak / iş") || "kaynak / iş";
  node.textContent = `${prefix}: ${number(limits.max_media_upload_mb)} MB ${media} · ${number(limits.max_document_upload_mb)} MB ${documentLabel} · ${number(limits.max_files_per_job)} ${sources}`;
}
function uploadLimitMessage(documentMode, limitMb) {
  const central = window.LectureSiftI18n;
  const base = central?.t("upload.planSizeLimit", "Bu seçim planının tek iş yükleme sınırını aşıyor.") || "Bu seçim planının tek iş yükleme sınırını aşıyor.";
  const kind = central?.t(documentMode ? "plans.documentShort" : "plans.mediaShort", documentMode ? "belge" : "medya") || (documentMode ? "belge" : "medya");
  return `${base} ${Number(limitMb).toLocaleString(central?.locale || navigator.language)} MB ${kind}.`;
}
function selectedSourceFiles(role) {
  return role === "classic" ? classicVideos : [...audioVideos, ...visualVideos];
}
const PROGRESS_PROFILES = {
  media: [
    ["source", "stageSource"], ["audio", "stageAudio"], ["transcription", "stageTranscript"],
    ["scene_scan", "stageVisual"], ["slide_validation", "stageSlides"], ["study_pack", "stageStudy"], ["exports", "stageExport"],
  ],
  document: [["source", "stageSource"], ["document_preflight", "stageDocument"], ["document_extraction", "stageDocument"], ["study_pack", "stageStudy"], ["exports", "stageExport"]],
  audio_export: [["source", "stageSource"], ["audio_extract", "stageMp3"], ["exports", "stagePackage"]],
  download_video: [["source", "stageSource"], ["worker_download", "url_download"], ["exports", "stagePackage"]],
};
function fileExtension(file) { return String(file?.name || "").split(".").pop().toLowerCase(); }
function isDocumentFile(file) { return DOCUMENT_EXTENSIONS.has(fileExtension(file)); }
function selectedDocumentJob() { return sourceLayout === "classic" && classicVideos.length > 0 && classicVideos.every(isDocumentFile); }
function progressProfileFor(job = null) {
  const type = job?.options?.job_type || jobType.value;
  if (type === "audio_export" || type === "download_video") return type;
  const documentJob = String(job?.source_type || "").startsWith("document") || job?.source_layout === "documents" || (!job && selectedDocumentJob());
  return documentJob ? "document" : "media";
}
function configureProgressProfile(job = null, force = false) {
  const profile = progressProfileFor(job), signature = `${profile}:${currentLanguage}`;
  if (!force && activeProgressProfile === signature) return profile;
  activeProgressProfile = signature;
  $("stageList").innerHTML = PROGRESS_PROFILES[profile].map(([stage, label]) =>
    `<li data-stage="${stage}"><i></i><span>${escapeHtml(t(label))}</span><b>--</b></li>`
  ).join("");
  return profile;
}
function addFiles(role, incoming) {
  const current = filesFor(role), known = new Set(current.map(file => `${file.name}:${file.size}:${file.lastModified}`));
  for (const file of incoming) {
    const extension = fileExtension(file);
    const allowed = role === "classic"
      ? (DOCUMENT_EXTENSIONS.has(extension) || MEDIA_EXTENSIONS.has(extension))
      : role === "visual" ? VIDEO_EXTENSIONS.has(extension) : MEDIA_EXTENSIONS.has(extension);
    if (!allowed) { showError("Bu dosya biçimi desteklenmiyor.", "LS-UPLOAD-01"); continue; }
    if (role === "classic" && current.length && current.some(isDocumentFile) !== isDocumentFile(file)) {
      showError("Video ve belgeyi aynı işte karıştırma; ayrı ayrı yükle.", "LS-UPLOAD-05");
      continue;
    }
    const limits = activeSourceLimits();
    const selected = selectedSourceFiles(role);
    if (selected.length >= Number(limits.max_files_per_job || DEFAULT_SOURCE_LIMITS.max_files_per_job)) {
      showError(window.LectureSiftI18n?.t("upload.planFileLimit", "Planının tek işte kaynak sayısı sınırına ulaştın."), "LS-UPLOAD-04");
      continue;
    }
    const uploadLimitMb = isDocumentFile(file)
      ? Number(limits.max_document_upload_mb || DEFAULT_SOURCE_LIMITS.max_document_upload_mb)
      : Number(limits.max_media_upload_mb || DEFAULT_SOURCE_LIMITS.max_media_upload_mb);
    const projectedBytes = selected.reduce((sum, item) => sum + Number(item.size || 0), 0) + Number(file.size || 0);
    if (projectedBytes > uploadLimitMb * 1024 * 1024) {
      showError(uploadLimitMessage(isDocumentFile(file), uploadLimitMb), "LS-UPLOAD-02");
      continue;
    }
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (!known.has(key)) { current.push(file); known.add(key); }
  }
  renderFileList(role);
  configureProgressProfile(null, true);
}
function renderFileList(role) {
  const files = filesFor(role), list = $(`${role}FileList`);
  list.innerHTML = files.map((file, index) => {
    const documentFile = isDocumentFile(file);
    const kind = fileExtension(file).toUpperCase();
    const readyLabel = documentFile ? "Belge analizi hazır" : "Medya kaynağı hazır";
    const localizedReadyLabel = window.LectureSiftI18n?.exact?.(readyLabel) || readyLabel;
    return `
    <div class="source-file-row ${documentFile ? "document-file" : ""}" draggable="true" data-role="${role}" data-index="${index}">
      <b>${index + 1}</b><div><strong>${escapeHtml(file.name)}${documentFile ? `<span class="file-kind">${escapeHtml(kind)}</span>` : ""}</strong><small>${formatBytes(file.size)} · ${escapeHtml(localizedReadyLabel)}</small></div>
      <span class="file-order-actions">
        <button type="button" data-action="up" title="${escapeHtml(t("moveUp"))}" ${index === 0 ? "disabled" : ""}>↑</button>
        <button type="button" data-action="down" title="${escapeHtml(t("moveDown"))}" ${index === files.length - 1 ? "disabled" : ""}>↓</button>
        <button type="button" data-action="remove" title="${escapeHtml(t("remove"))}">×</button>
      </span>
    </div>`;
  }).join("");
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

const PACK_PRESETS = {
  fast: {summary:true, transcript:true, slides:false, quiz:false, cards:false, timestamps:false, speakers:false, style:"short", quizCount:"5", cardCount:"10", formats:["pdf"]},
  balanced: {summary:true, transcript:true, slides:true, quiz:true, cards:true, timestamps:false, speakers:false, style:"standard", quizCount:"10", cardCount:"20", formats:["pdf"]},
  exam: {summary:true, transcript:true, slides:true, quiz:true, cards:true, timestamps:false, speakers:false, style:"exam", quizCount:"20", cardCount:"40", formats:["pdf"]},
  transcript: {summary:false, transcript:true, slides:false, quiz:false, cards:false, timestamps:false, speakers:false, style:"standard", quizCount:"5", cardCount:"10", formats:[]},
};

function selectedFormats() {
  return [$("formatPdf"), $("formatDocx"), $("formatTxt")].filter(input => input.checked).map(input => input.value);
}

function syncTranslationChoice() {
  const same = sourceLanguage.value !== "auto" && sourceLanguage.value === outputLanguage.value;
  const transcriptDisabled = !$("includeTranscript").checked;
  $("translateTranscript").disabled = same || transcriptDisabled;
  if (same || transcriptDisabled) $("translateTranscript").checked = false;
  $("translateControl").classList.toggle("is-disabled", same || transcriptDisabled);
  $("translateHelp").textContent = transcriptDisabled ? t("transcriptDisabledHelp") : same ? t("sameLanguageHelp") : t("translateHelp");
}

function syncTranscriptEnhancements() {
  const transcriptEnabled = $("includeTranscript").checked;
  const timestampsEnabled = transcriptEnabled && $("transcriptTimestamps").checked;
  $("transcriptTimestamps").disabled = !transcriptEnabled;
  $("transcriptTimestampsControl").classList.toggle("is-disabled", !transcriptEnabled);
  $("transcriptEnhancements").classList.toggle("is-disabled", !transcriptEnabled);
  if (!transcriptEnabled) $("transcriptTimestamps").checked = false;
  $("speakerDetection").disabled = !timestampsEnabled;
  $("speakerDetectionControl").classList.toggle("is-disabled", !timestampsEnabled);
  if (!timestampsEnabled) $("speakerDetection").checked = false;
  $("speakerDetectionRequirement").hidden = !transcriptEnabled || timestampsEnabled;
}
sourceLanguage.addEventListener("change", syncTranslationChoice);
outputLanguage.addEventListener("change", syncTranslationChoice);

function activePresetName() {
  const state = {
    summary:$("includeSummary").checked, transcript:$("includeTranscript").checked, slides:$("includeSlides").checked,
    quiz:$("includeQuiz").checked, cards:$("includeCards").checked, style:summaryStyle.value,
    timestamps:$("transcriptTimestamps").checked, speakers:$("speakerDetection").checked,
    quizCount:$("quizCount").value, cardCount:$("cardCount").value, formats:selectedFormats(),
  };
  return Object.entries(PACK_PRESETS).find(([, preset]) =>
    ["summary", "transcript", "slides", "quiz", "cards", "timestamps", "speakers", "style", "quizCount", "cardCount"].every(key => state[key] === preset[key])
    && state.formats.join(",") === preset.formats.join(",")
  )?.[0] || "";
}

function syncContentChoices() {
  $("quizCount").disabled = !$("includeQuiz").checked;
  $("cardCount").disabled = !$("includeCards").checked;
  summaryStyle.disabled = !$("includeSummary").checked;
  $("quizCount").closest(".content-choice").classList.toggle("is-disabled", !$("includeQuiz").checked);
  $("cardCount").closest(".content-choice").classList.toggle("is-disabled", !$("includeCards").checked);
  syncTranslationChoice();
  syncTranscriptEnhancements();
  const activePreset = activePresetName();
  document.querySelectorAll("[data-pack-preset]").forEach(button => {
    const active = button.dataset.packPreset === activePreset;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function applyPackPreset(name) {
  const preset = PACK_PRESETS[name];
  if (!preset) return;
  $("includeSummary").checked = preset.summary; $("includeTranscript").checked = preset.transcript;
  $("includeSlides").checked = preset.slides; $("includeQuiz").checked = preset.quiz; $("includeCards").checked = preset.cards;
  $("transcriptTimestamps").checked = preset.timestamps; $("speakerDetection").checked = preset.speakers;
  summaryStyle.value = preset.style; $("quizCount").value = preset.quizCount; $("cardCount").value = preset.cardCount;
  [$("formatPdf"), $("formatDocx"), $("formatTxt")].forEach(input => { input.checked = preset.formats.includes(input.value); });
  $("translateTranscript").checked = preset.transcript && !(sourceLanguage.value !== "auto" && sourceLanguage.value === outputLanguage.value);
  syncContentChoices();
}

document.querySelectorAll("[data-pack-preset]").forEach(button => button.addEventListener("click", () => applyPackPreset(button.dataset.packPreset)));
[$("includeSummary"), $("includeTranscript"), $("includeSlides"), $("includeQuiz"), $("includeCards"), $("quizCount"), $("cardCount"), summaryStyle, $("formatPdf"), $("formatDocx"), $("formatTxt"), $("transcriptTimestamps"), $("speakerDetection")]
  .forEach(control => control.addEventListener("change", syncContentChoices));

function updateOperationUI() {
  const type = jobType.value, study = type === "study_pack";
  $("studySettings").hidden = !study; $("formatChoices").hidden = !study;
  $("recordingModeTabs").hidden = type !== "study_pack";
  if (type === "audio_export") setSourceLayout("classic");
  if (type === "download_video") setSourceMode("link");
  $("analyzeButton").querySelector("span").textContent = type === "audio_export" ? t("audioExportOption") : type === "download_video" ? t("downloadVideoOption") : t("analyze");
  configureProgressProfile(null, true);
}
jobType.addEventListener("change", updateOperationUI);
applyLanguage();

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
function jobPhaseLabel(job, profile) {
  const labels = {
    queued: "queuedForWorker", queued_worker: "queuedForWorker", worker_download: "url_download",
    worker_publish: "publishingResult", document_preflight: "stageDocument", document_extraction: "stageDocument", document_ocr: "stageDocument", audio_extract: "stageMp3",
    transcription: "stageTranscript", transcript_translation: "stageTranscript", parallel_analysis: profile === "document" ? "stageDocument" : "processing",
    study_pack: "study_pack", exports: "exports", done: "done",
  };
  return t(labels[job.stage] || job.stage || "processing");
}
function profileDetail(profile) {
  return t({media:"detailMedia", document:"detailDocument", audio_export:"detailMp3", download_video:"detailDownload"}[profile]);
}
function updateJobView(job) {
  const profile = configureProgressProfile(job);
  $("processTitle").textContent = job.status === "done" ? t("done") : t("processing");
  const ocrDetail = job.stage === "document_ocr"
    ? `OCR · ${Number(job.ocr_pages_completed || 0)}/${Number(job.ocr_pages_total || 0)} ${window.LectureSiftI18n?.exact?.("taranmış sayfa") || "taranmış sayfa"}`
    : profileDetail(profile);
  updateProgress(job.percent, jobPhaseLabel(job, profile), ocrDetail);
  resetStages();
  if (Number(job.percent || 0) >= 8 || ["working", "processing", "done"].includes(job.status)) setItemState("source", "done");
  else setItemState("source", "active");
  if (profile === "document") {
    if (job.percent >= 62) setItemState("document_extraction", "done");
    else if (job.percent >= 8) setItemState("document_extraction", "active");
    if (job.percent >= 90) { setItemState("study_pack", "done"); setItemState("exports", job.status === "done" ? "done" : "active"); }
    else if (job.percent >= 62) setItemState("study_pack", "active");
  } else if (profile === "audio_export") {
    if (job.status === "done") { setItemState("audio_extract", "done"); setItemState("exports", "done"); }
    else if (job.stage === "exports") { setItemState("audio_extract", "done"); setItemState("exports", "active"); }
    else if (job.percent >= 35) setItemState("audio_extract", "active");
  } else if (profile === "download_video") {
    if (["worker_publish", "exports", "done"].includes(job.stage) || job.status === "done") { setItemState("worker_download", "done"); setItemState("exports", job.status === "done" ? "done" : "active"); }
    else setItemState("worker_download", "active");
  } else {
  const audio = job.tasks?.audio?.percent || 0, visual = job.tasks?.visual?.percent || 0;
  if (audio >= 100) { setItemState("audio", "done"); setItemState("transcription", "done"); }
  else if (audio >= 35) { setItemState("audio", "done"); setItemState("transcription", "active"); }
  else if (job.status === "working") setItemState("audio", "active");
  if (visual >= 100) { setItemState("scene_scan", "done"); setItemState("slide_validation", "done"); }
  else if (visual >= 55) { setItemState("scene_scan", "done"); setItemState("slide_validation", "active"); }
  else if (job.status === "working") setItemState("scene_scan", "active");
  if (job.percent >= 90) { setItemState("study_pack", "done"); setItemState("exports", job.status === "done" ? "done" : "active"); }
  else if (job.percent >= 70) setItemState("study_pack", "active");
  }
  if (job.status === "done") document.querySelectorAll(".stage-list li").forEach(item => { item.className = "done"; item.querySelector("b").textContent = "OK"; });
}

function showError(message, code = "LS-SYSTEM-01") {
  const known = ERRORS.tr[code];
  const centralKey = ({"LS-AI-03":"error.ai03","LS-AI-04":"error.ai04","LS-AI-05":"error.ai05","LS-AI-06":"error.ai06","LS-AI-07":"error.ai07","LS-AI-08":"error.ai08"})[code];
  const translated = code === "LS-UPLOAD-02" && message
    ? message
    : centralKey
    ? window.LectureSiftI18n?.t(centralKey, message)
    : known
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
  data.append("summary_style", summaryStyle.value);
  data.append("quiz_count", $("includeQuiz").checked ? $("quizCount").value : "0");
  data.append("flashcard_count", $("includeCards").checked ? $("cardCount").value : "0");
  data.append("include_summary", $("includeSummary").checked ? "true" : "false");
  data.append("include_transcript", $("includeTranscript").checked ? "true" : "false");
  data.append("include_slides", $("includeSlides").checked ? "true" : "false");
  data.append("translate_transcript", $("includeTranscript").checked && $("translateTranscript").checked ? "true" : "false");
  const timestampsEnabled = $("includeTranscript").checked && $("transcriptTimestamps").checked;
  data.append("transcript_timestamps", timestampsEnabled ? "true" : "false");
  data.append("speaker_detection", timestampsEnabled && $("speakerDetection").checked ? "true" : "false");
  data.append("slides_offset_seconds", sourceLayout === "separate" ? ($("slidesOffset").value || "0") : "0");
  data.append("source_layout", sourceLayout); data.append("job_type", jobType.value);
  data.append("output_formats", selectedFormats().join(",") || "none");
  return data;
}

$("analyzeButton").onclick = async () => {
  $("errorBox").hidden = true;
  if (jobType.value === "study_pack" && ![$("includeSummary"), $("includeTranscript"), $("includeQuiz"), $("includeCards")].some(input => input.checked)) {
    showError(t("outputSelectionRequired"), "LS-OUTPUT-01");
    return;
  }
  if (!billingToken || !billingAccount) {
    try {
      const access = await window.LectureSiftGuestTrial?.ensureAccess?.();
      if (access?.token && access?.account) {
        billingToken = access.token;
        billingAccount = access.account;
        renderBillingAccount();
      }
    } catch (error) {
      showError(error.message || t("loginRequired"), error.code || "LS-BILL-01");
      return;
    }
  }
  if (!billingToken || !billingAccount) {
    showError(t("loginRequired"), "LS-BILL-01");
    return;
  }
  const uploadFiles = sourceLayout === "separate" ? [...audioVideos, ...visualVideos] : classicVideos;
  if (sourceMode === "upload" && sourceLayout === "classic" && !classicVideos.length) { $("classicFiles").click(); return; }
  if (sourceMode === "upload" && sourceLayout === "separate" && (!audioVideos.length || !visualVideos.length)) {
    showError("Ses ve görüntü ayrı modunda her iki listeye de en az bir video ekle.", "LS-UPLOAD-03"); return;
  }
  const documentUpload = sourceLayout === "classic" && uploadFiles.length && uploadFiles.every(isDocumentFile);
  const sourceLimits = activeSourceLimits();
  const uploadLimitMb = Number(documentUpload ? sourceLimits.max_document_upload_mb : sourceLimits.max_media_upload_mb);
  if (sourceMode === "upload" && uploadFiles.reduce((total, file) => total + file.size, 0) > uploadLimitMb * 1024 ** 2) {
    showError(uploadLimitMessage(documentUpload, uploadLimitMb), "LS-UPLOAD-02"); return;
  }
  if (documentUpload && jobType.value !== "study_pack") { showError("Belge kaynakları çalışma paketi işleminde kullanılabilir.", "LS-UPLOAD-05"); return; }
  if (sourceMode === "link" && !videoUrl.value.trim()) { showError(TR.urlLabel, "LS-URL-01"); return; }
  $("analyzeButton").disabled = true; $("results").hidden = true; latestResult = null; jobId = null; configureProgressProfile(null, true); resetStages(); startTimer();
  setItemState("source", "active");
  updateProgress(2, sourceMode === "link" ? t("url_download") : t("uploadingSource"), profileDetail(progressProfileFor()));
  const data = formData();
  if (sourceMode === "link") {
    data.append("video_url", videoUrl.value.trim());
    try {
      const response = await fetch(`${API}/jobs/url`, {method: "POST", body: data, headers:{Authorization:`Bearer ${billingToken}`}});
      if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
      jobId = (await response.json()).job_id;
      pollJob();
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
  const uploadStartedAt = performance.now();
  request.upload.onprogress = event => {
    if (!event.lengthComputable) return;
    const elapsedSeconds = Math.max(.25, (performance.now() - uploadStartedAt) / 1000);
    const bytesPerSecond = event.loaded / elapsedSeconds;
    const remainingSeconds = bytesPerSecond > 0 ? Math.max(0, (event.total - event.loaded) / bytesPerSecond) : 0;
    const speed = `${(bytesPerSecond / 1024 ** 2).toFixed(1)} MB/s`;
    const remaining = remainingSeconds >= 1 ? ` · ~${Math.ceil(remainingSeconds)} sn` : "";
    updateProgress(
      Math.min(7, event.loaded / event.total * 7),
      t("uploadingSource"),
      `${(event.loaded / 1024 ** 2).toFixed(1)} / ${(event.total / 1024 ** 2).toFixed(1)} MB · ${speed}${remaining}`,
    );
  };
  request.upload.onload = () => updateProgress(7, t("uploadAccepted"), profileDetail(progressProfileFor()));
  request.onload = async () => {
    if (request.status < 300) {
      const created = JSON.parse(request.responseText);
      jobId = created.job_id;
      if (created.ocr_required) {
        const pageCount = Number(created.ocr_pages || 0);
        updateProgress(8, t("stageDocument"), `${pageCount} ${window.LectureSiftI18n?.exact?.("taranmış sayfa") || "taranmış sayfa"}`);
      }
      pollJob();
    }
    else {
      try {
        const body = JSON.parse(request.responseText);
        const detail = body.detail || body;
        showError(detail.message || ERRORS.tr["LS-UPLOAD-04"], detail.code || "LS-UPLOAD-04");
      } catch {
        showError(request.responseText || "Sunucu dosya yükleme isteğine yanıt veremedi.", "LS-NETWORK-01");
      }
    }
  };
  request.onerror = () => showError("", "LS-NETWORK-01"); request.send(data);
};

async function pollJob() {
  try {
    const response = await fetch(`${API}/jobs/${jobId}`, {cache: "no-store", headers:{Authorization:`Bearer ${billingToken}`}});
    if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
    const job = await response.json();
    updateJobView(job);
    if (job.status === "processing" || job.status === "done") {
      window.LectureSiftGuestTrial?.markUsed?.(jobId);
    }
    if (job.status === "done") { clearInterval(timerHandle); await loadResult(); await refreshBillingAccount(); return; }
    if (job.status === "error") { showError(job.error, job.error_code); return; }
    pollHandle = setTimeout(pollJob, 1300);
  } catch (error) { pollHandle = setTimeout(pollJob, 2500); }
}

async function loadResult() {
  try {
    const response = await fetch(`${API}/jobs/${jobId}/result`, {cache: "no-store", headers:{Authorization:`Bearer ${billingToken}`}});
    if (!response.ok) { const error = await responseError(response); showError(error.message, error.code); return; }
    latestResult = await response.json();
    renderResult(latestResult);
    $("analyzeButton").disabled = false;
    window.LectureSiftGuestTrial?.markUsed?.(jobId);
  } catch (error) { showError(error.message, "LS-NETWORK-01"); }
}

function activateResultPane(paneName, {focus = false} = {}) {
  const tabs = [...document.querySelectorAll(".result-tab")];
  const availableTabs = tabs.filter(tab => !tab.hidden);
  const target = availableTabs.find(tab => tab.dataset.pane === paneName) || availableTabs[0];
  if (!target) return;
  tabs.forEach(tab => {
    const selected = tab === target;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  document.querySelectorAll(".result-pane").forEach(pane => {
    const selected = pane.id === `pane-${target.dataset.pane}`;
    pane.classList.toggle("active", selected);
    pane.hidden = !selected;
  });
  if (focus) target.focus({preventScroll:true});
}

function setupResultTabs() {
  document.querySelectorAll(".result-tab").forEach(button => {
    button.addEventListener("click", () => activateResultPane(button.dataset.pane));
    button.addEventListener("keydown", event => {
      const tabs = [...document.querySelectorAll(".result-tab:not([hidden])")];
      const index = tabs.indexOf(button);
      if (index < 0) return;
      const direction = {ArrowRight:1, ArrowDown:1, ArrowLeft:-1, ArrowUp:-1}[event.key];
      let nextIndex = direction === undefined ? index : (index + direction + tabs.length) % tabs.length;
      if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = tabs.length - 1;
      else if (direction === undefined) return;
      event.preventDefault();
      activateResultPane(tabs[nextIndex].dataset.pane, {focus:true});
    });
  });
}

function renderResult(data) {
  $("resultHeading").textContent = data.title || "LectureSift";
  const utilityResult = data.job_type && data.job_type !== "study_pack";
  const options = data.options || {};
  const selected = {
    summary: options.include_summary !== false,
    transcript: options.include_transcript !== false,
    slides: options.include_slides !== false,
    quiz: Number(options.quiz_count ?? data.quiz?.length ?? 0) > 0,
    cards: Number(options.flashcard_count ?? data.flashcards?.length ?? 0) > 0,
    files: Boolean(data.artifacts?.length),
  };
  const preciseTranscriptMode = ["provider_segments", "speaker_segments"].includes(data.transcript_timestamps_mode);
  const timestampLabel = preciseTranscriptMode
    ? window.LectureSiftI18n?.t("transcript.preciseTimestamps", "Hassas zaman damgaları")
    : data.transcript_timestamps_mode === "chunk_estimate"
      ? window.LectureSiftI18n?.t("transcript.estimatedTimestamps", "Bölüm başlangıç zamanları")
      : window.LectureSiftI18n?.t("transcript.noTimestamps", "Zaman damgası kapalı");
  const resultDetails = [];
  if (selected.slides) resultDetails.push(`${data.slides?.length || 0} ${t("tabSlides")}`);
  if (selected.quiz) resultDetails.push(`${data.quiz?.length || 0} Quiz`);
  if (selected.cards) resultDetails.push(`${data.flashcards?.length || 0} ${t("tabCards")}`);
  if (selected.transcript) resultDetails.push(timestampLabel);
  if (selected.transcript && (data.transcript_segments || []).some(segment => segment.speaker_label || segment.speaker_id || segment.speaker)) {
    resultDetails.push(window.LectureSiftI18n?.t("transcript.speakerLegend", "Konuşmacılar") || "Konuşmacılar");
  }
  $("resultMeta").textContent = utilityResult ? `${data.artifacts?.length || 0} ${t("tabFiles")}` : resultDetails.join(" · ");
  const canDownload = data.download_enabled !== false;
  const unlockText = window.LectureSiftI18n?.t("plans.unlockDownload", "Dosyaları indirmek için paket seç") || "Dosyaları indirmek için paket seç";
  $("downloadAll").hidden = !selected.files;
  $("downloadAll").href = canDownload ? "#" : (window.LectureSiftI18n?.localizedPath?.(currentLanguage, "/plans.html") || "/plans.html");
  $("downloadAll").textContent = canDownload ? t("downloadAll") : unlockText;
  $("downloadAll").onclick = canDownload ? (event => { event.preventDefault(); downloadProtected(`/jobs/${jobId}/download`, "LectureSift_Paketi.zip"); }) : null;
  $("summaryContent").textContent = data.summary || t("noContent");
  $("keyPoints").innerHTML = (data.key_points || []).map(point => `<div class="key-item"><i>✦</i><span>${escapeHtml(point)}</span></div>`).join("");
  const terms = (data.important_terms || []).map(item => `<div class="note-item"><h3>${escapeHtml(item.term)}</h3><p>${escapeHtml(item.definition)}</p></div>`).join("");
  const notes = (data.notes || []).map(item => `<div class="note-item"><h3>${escapeHtml(item.heading)}</h3><p>${escapeHtml(item.content)}</p><ul>${(item.bullets || []).map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul></div>`).join("");
  const exam = data.exam_focus?.length ? `<div class="note-item"><h3>${escapeHtml(t("summaryExam"))}</h3><ul>${data.exam_focus.map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul></div>` : "";
  $("notesContent").innerHTML = terms + notes + exam || `<div class="empty-state">${escapeHtml(t("noContent"))}</div>`;
  renderTranscript(!(data.transcript_segments || []).length);
  $("slidesContent").innerHTML = data.slides?.length ? data.slides.map(slide => `<figure class="slide-card"><img loading="lazy" data-protected-src="/jobs/${jobId}/slide/${encodeURIComponent(slide.file)}" alt="Slide ${escapeHtml(slide.timestamp)}"><figcaption>${escapeHtml(slide.timestamp || `${slide.second}s`)}</figcaption></figure>`).join("") : `<div class="empty-state">${escapeHtml(t("noSlides"))}</div>`;
  document.querySelectorAll("img[data-protected-src]").forEach(async image => {
    try {
      const response = await protectedFetch(image.dataset.protectedSrc);
      const objectUrl = URL.createObjectURL(await response.blob());
      image.onload = () => URL.revokeObjectURL(objectUrl);
      image.src = objectUrl;
    } catch { image.alt = t("noSlides"); }
  });
  renderQuiz(data.quiz || []); renderExamPrep(data); renderCards(); renderFiles(data.artifacts || [], canDownload);
  $("lessonQuestionForm").reset(); $("lessonAnswer").hidden = true; $("lessonAnswer").replaceChildren();
  const visiblePanes = utilityResult
    ? {files:true}
    : {
        summary:selected.summary, notes:selected.summary, transcript:selected.transcript, slides:selected.slides,
        quiz:selected.quiz, exam:selected.summary || selected.quiz, cards:selected.cards,
        ask:selected.summary || selected.transcript, files:selected.files,
      };
  document.querySelectorAll(".result-tab").forEach(button => { button.hidden = !visiblePanes[button.dataset.pane]; });
  activateResultPane(utilityResult ? "files" : Object.keys(visiblePanes).find(name => visiblePanes[name]));
  $("results").hidden = false; $("results").scrollIntoView({behavior: "smooth", block: "start"});
}

function transcriptUiText(key, fallback) {
  return window.LectureSiftI18n?.t(key, fallback) || fallback;
}

function transcriptTemplate(key, fallback, values) {
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    transcriptUiText(key, fallback),
  );
}

function transcriptTimestamp(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total % 3600 / 60);
  const remainder = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function transcriptSpeakerLabel(value) {
  const speaker = String(value || "").trim();
  if (!speaker) return "";
  if (/^[A-Z0-9_-]{1,12}$/i.test(speaker)) {
    return `${transcriptUiText("transcript.speakerPrefix", "Konuşmacı")} ${speaker}`;
  }
  return speaker;
}

function normalizedTranscriptSegments(rawSegments) {
  const speakerMetadata = new Map(
    (latestResult?.transcript_speaker_metadata?.speakers || [])
      .filter(speaker => speaker?.speaker_id)
      .map(speaker => [String(speaker.speaker_id), speaker]),
  );
  const segments = (rawSegments || []).map((segment, sourceIndex) => ({
    sourceIndex,
    start: Math.max(0, Number(segment.start) || 0),
    end: Number(segment.end),
    timestamp: String(segment.timestamp || ""),
    speakerKey: String(segment.speaker_id || segment.speaker || segment.speaker_label || "").trim(),
    speaker: transcriptSpeakerLabel(
      speakerMetadata.get(String(segment.speaker_id || ""))?.label
      || segment.speaker_label
      || segment.speaker
      || segment.speaker_id,
    ),
    speakerUncertain: Boolean(segment.speaker_uncertain),
    text: String(segment.text || "").trim(),
    precision: String(segment.precision || ""),
  })).filter(segment => segment.text).sort((left, right) => left.start - right.start || left.sourceIndex - right.sourceIndex);
  return segments.map((segment, index) => {
    const nextStart = segments[index + 1]?.start;
    const end = Number.isFinite(segment.end) && segment.end > segment.start
      ? segment.end
      : Number.isFinite(nextStart) && nextStart > segment.start ? nextStart : segment.start + 1;
    return {...segment, index, end, timestamp:segment.timestamp || transcriptTimestamp(segment.start)};
  });
}

function transcriptSpeakerPalette(segments) {
  const hues = [205, 269, 155, 28, 337, 184, 52, 304];
  const entries = [];
  const hueByKey = new Map();
  segments.forEach(segment => {
    if (!segment.speakerKey || hueByKey.has(segment.speakerKey)) return;
    const hue = hues[entries.length % hues.length];
    hueByKey.set(segment.speakerKey, hue);
    entries.push({key:segment.speakerKey, label:segment.speaker, hue});
  });
  return {entries, hueFor:segment => hueByKey.get(segment.speakerKey) ?? 212};
}

function activateTranscriptSegment(index, {focusRow = false} = {}) {
  document.querySelectorAll("[data-transcript-segment]").forEach(control => {
    const active = Number(control.dataset.transcriptSegment) === index;
    control.classList.toggle("active", active);
    if (active) control.setAttribute("aria-current", "true");
    else control.removeAttribute("aria-current");
  });
  if (!focusRow) return;
  const row = document.querySelector(`.transcript-segment[data-transcript-segment="${index}"]`);
  if (!row) return;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  row.scrollIntoView({behavior:reducedMotion ? "auto" : "smooth", block:"nearest"});
  row.focus({preventScroll:true});
}

function renderTranscriptTimeline(segments) {
  const timeline = $("transcriptTimeline"), waveform = $("transcriptWaveform"), legend = $("speakerLegend");
  if (!segments.length) {
    timeline.hidden = true;
    waveform.replaceChildren();
    legend.replaceChildren();
    return;
  }
  const palette = transcriptSpeakerPalette(segments);
  const duration = Math.max(1, ...segments.map(segment => segment.end));
  const timelineMinimumWidth = Math.max(1, segments.length * 30);
  const slots = segments.map(segment => {
    const left = Math.min(99.6, Math.max(0, segment.start / duration * 100));
    const width = Math.max(.4, Math.min(100 - left, (segment.end - segment.start) / duration * 100));
    const height = 34 + Math.min(54, (segment.text.length % 9) * 6);
    const label = segment.speaker
      ? transcriptTemplate("transcript.segmentSpeakerAction", "{time} zamanındaki {speaker} bölümünü seç", {time:segment.timestamp, speaker:segment.speaker})
      : transcriptTemplate("transcript.segmentAction", "{time} zamanındaki bölümü seç", {time:segment.timestamp});
    return `<span class="transcript-wave-slot" role="listitem" style="--segment-left:${left}%;--segment-width:${width}%;--speaker-hue:${palette.hueFor(segment)}"><button type="button" class="transcript-wave-segment" data-transcript-segment="${segment.index}" style="--wave-height:${height}%" aria-label="${escapeHtml(label)}" aria-controls="transcript-segment-${segment.index}" title="${escapeHtml(label)}"></button></span>`;
  }).join("");
  waveform.innerHTML = `<div class="transcript-wave-track" style="--timeline-min-width:${timelineMinimumWidth}px">${slots}</div>`;
  legend.innerHTML = palette.entries.map(speaker => `<span role="listitem" style="--speaker-hue:${speaker.hue}"><i aria-hidden="true"></i>${escapeHtml(speaker.label)}</span>`).join("");
  legend.closest(".speaker-legend-shell").hidden = !palette.entries.length;
  const mode = latestResult?.transcript_timestamps_mode;
  $("transcriptPrecisionBadge").textContent = ["provider_segments", "speaker_segments"].includes(mode)
    ? transcriptUiText("transcript.preciseTimestamps", "Hassas zaman damgaları")
    : mode === "chunk_estimate"
      ? transcriptUiText("transcript.estimatedTimestamps", "Bölüm başlangıç zamanları")
      : transcriptUiText("transcript.noTimestamps", "Zaman damgası kapalı");
  timeline.hidden = false;
  waveform.querySelectorAll(".transcript-wave-segment").forEach(button => {
    button.addEventListener("click", () => activateTranscriptSegment(Number(button.dataset.transcriptSegment), {focusRow:true}));
  });
}

function renderOriginalTranscript(segments) {
  const content = $("transcriptContent");
  if (!segments.length) {
    content.textContent = latestResult.transcript_original || t("noContent");
    renderTranscriptTimeline([]);
    return;
  }
  const palette = transcriptSpeakerPalette(segments);
  content.innerHTML = `<ol class="transcript-segment-list">${segments.map(segment => {
    const label = segment.speaker
      ? transcriptTemplate("transcript.segmentSpeakerAction", "{time} zamanındaki {speaker} bölümünü seç", {time:segment.timestamp, speaker:segment.speaker})
      : transcriptTemplate("transcript.segmentAction", "{time} zamanındaki bölümü seç", {time:segment.timestamp});
    const speaker = segment.speaker ? `<span class="transcript-speaker"><i aria-hidden="true"></i>${escapeHtml(segment.speaker)}</span>` : "";
    const accessibleLabel = `${label}. ${segment.text}`;
    return `<li><button id="transcript-segment-${segment.index}" type="button" class="transcript-segment" data-transcript-segment="${segment.index}" style="--speaker-hue:${palette.hueFor(segment)}" aria-label="${escapeHtml(accessibleLabel)}"><span class="transcript-segment-meta"><time datetime="PT${Math.round(segment.start)}S">${escapeHtml(segment.timestamp)}</time>${speaker}</span><span class="transcript-segment-text" dir="auto">${escapeHtml(segment.text)}</span></button></li>`;
  }).join("")}</ol>`;
  renderTranscriptTimeline(segments);
  content.querySelectorAll(".transcript-segment").forEach(button => {
    button.addEventListener("click", () => activateTranscriptSegment(Number(button.dataset.transcriptSegment)));
  });
}

function renderTranscript(translated) {
  if (!latestResult) return;
  const hasTranslation = Boolean(latestResult.transcript_translated?.trim());
  const segments = normalizedTranscriptSegments(latestResult.transcript_segments);
  document.querySelector(".transcript-tools").hidden = !hasTranslation;
  translated = hasTranslation && translated;
  $("showTranslated").classList.toggle("active", translated);
  $("showOriginal").classList.toggle("active", !translated);
  $("showTranslated").setAttribute("aria-pressed", String(translated));
  $("showOriginal").setAttribute("aria-pressed", String(!translated));
  $("transcriptTranslationNotice").hidden = !translated || !segments.length;
  if (translated) {
    $("transcriptTimeline").hidden = true;
    $("transcriptContent").replaceChildren();
    const paragraph = document.createElement("p");
    paragraph.className = "transcript-plain";
    paragraph.dir = "auto";
    paragraph.textContent = latestResult.transcript_translated || latestResult.transcript_original || t("noContent");
    $("transcriptContent").append(paragraph);
    return;
  }
  renderOriginalTranscript(segments);
  if (segments.length) activateTranscriptSegment(0);
}

$("lessonQuestionForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (!jobId || !latestResult) return;
  const question = $("lessonQuestion").value.trim();
  if (question.length < 3) return;
  const button = $("lessonQuestionButton");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = window.LectureSiftI18n?.t("ask.thinking", "Yanıt hazırlanıyor…") || "Yanıt hazırlanıyor…";
  try {
    const body = await billingRequest(`/jobs/${encodeURIComponent(jobId)}/ask`, {
      method:"POST", body:JSON.stringify({question}),
    });
    const citations = (body.citations || []).map(item => `<li><strong>${escapeHtml(item.timestamp || "00:00:00")}</strong>${item.speaker ? ` · ${escapeHtml(item.speaker)}` : ""}<span>${escapeHtml(item.excerpt || "")}</span></li>`).join("");
    $("lessonAnswer").innerHTML = `<h4>${escapeHtml(window.LectureSiftI18n?.t("ask.answer", "Yanıt") || "Yanıt")}</h4><p>${escapeHtml(body.answer)}</p>${citations ? `<h4>${escapeHtml(window.LectureSiftI18n?.t("ask.sources", "Ders içindeki dayanaklar") || "Ders içindeki dayanaklar")}</h4><ol>${citations}</ol>` : ""}`;
    $("lessonAnswer").hidden = false;
  } catch (error) {
    showError(error.message, error.code || "LS-AI-07");
  } finally {
    button.disabled = false; button.textContent = original;
  }
});
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
    $("quizStatus").textContent = `${t("score")}: ${quizScore}/${items.length} · ${quizAnswered}/${items.length}`; updateExamReadiness(items.length);
  });
}

function updateExamReadiness(total = latestResult?.quiz?.length || 0) {
  const answered = Math.min(quizAnswered, total);
  const score = answered ? Math.round(quizScore / answered * 100) : 0;
  $("examReadiness").textContent = answered ? `%${score} · ${answered}/${total}` : window.LectureSiftI18n?.t("exam.notStarted", "Henüz deneme çözülmedi") || "Henüz deneme çözülmedi";
  $("examRecommendation").textContent = !answered
    ? (window.LectureSiftI18n?.t("exam.startHelp", "Önce önemli konuları gözden geçir, ardından karışık denemeyi başlat.") || "Önce önemli konuları gözden geçir, ardından karışık denemeyi başlat.")
    : score >= 80
      ? (window.LectureSiftI18n?.t("exam.strong", "Bu derste güçlü görünüyorsun; yanlışlarını son kez gözden geçir.") || "Bu derste güçlü görünüyorsun; yanlışlarını son kez gözden geçir.")
      : (window.LectureSiftI18n?.t("exam.review", "Sınav odaklarını ve yanlış yanıt açıklamalarını tekrar et.") || "Sınav odaklarını ve yanlış yanıt açıklamalarını tekrar et.");
}

function renderExamPrep(data) {
  const focus = data.exam_focus || [];
  $("examFocusList").innerHTML = focus.length ? focus.map((item, index) => `<div class="note-item"><h3>${index + 1}</h3><p>${escapeHtml(item)}</p></div>`).join("") : `<div class="empty-state">${escapeHtml(window.LectureSiftI18n?.t("exam.noFocus", "Bu ders için ayrı sınav odağı oluşmadı; quiz ve bilgi kartlarını kullanabilirsin.") || "Bu ders için ayrı sınav odağı oluşmadı; quiz ve bilgi kartlarını kullanabilirsin.")}</div>`;
  $("startExamButton").disabled = !(data.quiz || []).length;
  updateExamReadiness((data.quiz || []).length);
}

function shuffledExamQuestions(items) {
  return [...items].sort(() => Math.random() - .5).map(item => {
    const options = (item.options || []).map((value, index) => ({value, correct:index === Number(item.answer_index)})).sort(() => Math.random() - .5);
    return {...item, options:options.map(option => option.value), answer_index:options.findIndex(option => option.correct)};
  });
}

$("startExamButton").addEventListener("click", () => {
  const items = latestResult?.quiz || [];
  if (!items.length) return;
  renderQuiz(shuffledExamQuestions(items));
  activateResultPane("quiz", {focus:true});
  $("pane-quiz").scrollIntoView({behavior:"smooth", block:"start"});
});

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

function renderFiles(files, canDownload = true) {
  const plansPath = window.LectureSiftI18n?.localizedPath?.(currentLanguage, "/plans.html") || "/plans.html";
  const unlockText = window.LectureSiftI18n?.t("plans.unlockDownload", "Dosyaları indirmek için paket seç") || "Dosyaları indirmek için paket seç";
  $("filesContent").innerHTML = files.map(file => `<div class="file-item"><div><strong>${escapeHtml(file.label)}</strong><small>${escapeHtml(file.format)} · ${formatBytes(file.size_bytes)}</small></div><a href="${canDownload ? "#" : plansPath}" ${canDownload ? `data-artifact="${encodeURIComponent(file.file)}" data-filename="${escapeHtml(file.file)}"` : ""}>${escapeHtml(canDownload ? t("download") : unlockText)}</a></div>`).join("") || `<div class="empty-state">${escapeHtml(t("noContent"))}</div>`;
  document.querySelectorAll("a[data-artifact]").forEach(anchor => anchor.onclick = event => {
    event.preventDefault();
    downloadProtected(`/jobs/${jobId}/artifact/${anchor.dataset.artifact}`, anchor.dataset.filename);
  });
}

setupResultTabs();
