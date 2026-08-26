from dataclasses import dataclass


@dataclass
class LectureSiftError(Exception):
    code: str
    user_message: str
    technical_message: str = ""
    status_code: int = 400

    def __str__(self) -> str:
        return self.technical_message or self.user_message

    def public(self) -> dict:
        return {"code": self.code, "message": self.user_message}


def normalize_error(exc: Exception) -> LectureSiftError:
    if isinstance(exc, LectureSiftError):
        return exc

    raw = str(exc)
    low = raw.lower()

    if "insufficient_quota" in low or "exceeded your current quota" in low:
        return LectureSiftError(
            "LS-AI-01",
            "Yapay zekâ kullanım kotası dolmuş. Hesap kotası yenilendiğinde işlem tekrar denenebilir.",
            raw,
            503,
        )
    if "429" in low or "rate limit" in low:
        return LectureSiftError(
            "LS-AI-02",
            "Yapay zekâ servisi şu anda yoğun. Birkaç dakika sonra yeniden deneyebilirsin.",
            raw,
            503,
        )
    if "blocked server-side" in low or "not a bot" in low or "sign in" in low:
        return LectureSiftError(
            "LS-URL-02",
            "Video sağlayıcısı sunucudan indirmeyi engelledi. Videoyu cihazından yükleyebilir veya doğrudan MP4/WebM bağlantısı kullanabilirsin.",
            raw,
            422,
        )
    if "no downloadable video" in low or "could not be downloaded" in low:
        return LectureSiftError(
            "LS-URL-03",
            "Bu sayfada indirilebilir bir video bulunamadı. Doğrudan video bağlantısı kullan veya dosyayı cihazından yükle.",
            raw,
            422,
        )
    if "video could not be opened" in low or "video açılamadı" in low:
        return LectureSiftError(
            "LS-VIDEO-02",
            "Video okunamadı. Dosya bozuk, eksik veya desteklenmeyen bir kodlayıcı kullanıyor olabilir.",
            raw,
            422,
        )
    if "audio extraction" in low or "output file does not contain any stream" in low:
        return LectureSiftError(
            "LS-AUDIO-01",
            "Videonun ses parçası okunamadı. Görsel analiz yapılabilir ancak transkript oluşturulamaz.",
            raw,
            422,
        )
    if "larger than" in low or "size limit" in low:
        return LectureSiftError(
            "LS-UPLOAD-02",
            "Video izin verilen dosya boyutunu aşıyor.",
            raw,
            413,
        )
    return LectureSiftError(
        "LS-SYSTEM-01",
        "İşlem beklenmeyen bir nedenle tamamlanamadı. Videoyu veya bağlantıyı kontrol edip yeniden deneyebilirsin.",
        raw,
        500,
    )
