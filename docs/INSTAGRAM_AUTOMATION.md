# LectureSift Instagram yayın akışı

`@lecturesift` için tek zamanlanmış yayıncı Render cron görevidir. Her gün
17:00 UTC'de bir özgün çalışma tekniği paylaşır. `MIXED` ayarı altı günün
dördünde Reel, ikisinde API uyumlu 4:5 fotoğraf üretir. Reels üç kısa sahneden oluşur:
merak uyandıran giriş, uygulanabilir adımlar ve son adım. Her Reel, ilgili
konuya özel önceden hazırlanmış İngilizce seslendirme ve LectureSift için
üretilmiş özgün, düşük seviyeli enstrümantal müzik içerir; Türkçe özet ekranda
kalır. Sesler depoda tutulduğu için yayın anında harici ses servisine ihtiyaç
duyulmaz. MP4 dosyası Instagram'ın istediği 48 kHz AAC ses akışıyla üretilir.
Açıklamanın ilk
satırında konuya uygun arama ifadesi, devamında gerçek öğrenme önerileri ve
konuyla ilişkili etiketler bulunur. Ürün adı, öneriden sonra gelir.
Yirmi dört konu tamamlandığında aynı içerik tekrar paylaşılmaz; görev hata
verir ve yeni editoryal seri eklenmesi gerekir.

Hesap bağlantısı `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_ACCOUNT_ID` ve
`INSTAGRAM_APP_SECRET` ile yapılır. Token'ın `instagram_business_basic` ve
`instagram_business_content_publish` izinlerine sahip olması, hesabın
profesyonel hesap olması gerekir. Bu değerler yalnızca Render'ın gizli ortam
değişkenlerinde tutulur. OVH `lecturesift-instagram.timer` kapalı kalmalıdır;
aynı gün iki yayıncının çalışması önlenir.

Yayıncı şu sırayı izler:

1. API'nin gerçekten `@lecturesift` hesabına bağlı olduğunu doğrular.
2. Bugünün işaretinin önceki gönderilerde bulunmadığını kontrol eder.
3. Reel gününde herkese açık MP4 adresini doğrular; video bozuksa gönderi
   kapsayıcısı açmaz.
4. Medya kapsayıcısını oluşturur, işlenmesini bekler ve yayımlar.
5. Günlük işareti açıklamada saklar; sonraki çalıştırma aynı gönderiyi atlar.

`GET /instagram/daily/plan?day=YYYY-MM-DD` içeriği ve medya bağlantılarını,
`GET /instagram/daily/status?day=YYYY-MM-DD` yayın durumunu gösterir.
`GET /instagram/health` hesabı doğrular. GitHub'daki günlük üretim kontrolü
18:30 UTC'de yayın işaretini arar; eksikse iş başarısız olur.

Eski dokuz parçalı “launch grid” görselleri gerektiğinde elle kullanılabilir,
fakat günlük akışı geciktirmez. Eski “yakında açılıyoruz” metinleri otomatik
olarak paylaşılmaz.

Keşfet görünürlüğü garanti edilemez. Açıklamalarda alakasız popüler sözcükler
ve yoğun etiket yığınları kullanılmaz. Gönderi performansı Instagram içgörülerinde
erişim, izlenme süresi, kaydetme ve paylaşma üzerinden düzenli incelenmeli;
zayıf konular bu dosyadaki editoryal döngüden çıkarılmalıdır.

## Stüdyo Reels, karusel ve otomatik cevaplar

Render cron görevi artık 15 dakikada bir çalışır. Her çalışmada önce zamanı gelen paylaşım
yapılır, sonra yeni yorum ve DM'ler cevaplanır. Paylaşım gün ve saat dilimi başına bir kez olur
(açıklama işareti ve depodaki kayıt), bu yüzden sık çalışma çift gönderi üretmez.

- `INSTAGRAM_STUDIO_ENABLED=true` ve `ELEVENLABS_API_KEY` ile `ELEVENLABS_VOICE_ID` tanımlıysa
  günün dersi (`lecturesift/studio_social.py`) 20:00'de ElevenLabs seslendirmeli 1080x1920 Reel,
  13:00'te aynı dersin 5 sayfalık 4:5 karuseli olarak paylaşılır (`INSTAGRAM_REEL_HOUR`,
  `INSTAGRAM_CAROUSEL_HOUR`, Europe/Istanbul). Soru `gpt-4o-mini` ile yazılır, ikinci bağımsız bir
  çağrı cevabı doğrulamazsa elle kontrol edilmiş yedek derslerden biri kullanılır. ElevenLabs
  anahtarı yoksa eski günlük Reel akışı devam eder.
- `INSTAGRAM_REPLIES_ENABLED=true` iken `lecturesift/instagram_replies.py` son 14 günün
  gönderilerindeki yorumlara ve son 24 saatteki DM'lere yazanın dilinde kısa cevap verir. Spam,
  link, hakaret ve hassas konular atlanır; ödeme ve hesap sorunları iletişim sayfasına yönlendirilir.
  Günlük üst sınır `INSTAGRAM_REPLIES_DAILY_LIMIT` (120), kişi başı günde 5 cevap. Token'ın ayrıca
  `instagram_business_manage_comments` ve `instagram_business_manage_messages` izinlerine sahip
  olması gerekir; izin yoksa cevaplar atlanır, paylaşım sürer.
- Üretilen dosyalar `social/instagram/studio/<gün>/` altında durur ve
  `GET /instagram/studio/<gün>/<dosya>` ile Instagram'a sunulur.

Denetim komutları:

```bash
python -m lecturesift.instagram_replies dry-run              # cevapları yazar, göndermez
python -m lecturesift.studio_social prepare YYYY-MM-DD       # üretir, paylaşmaz
python -m lecturesift.reel_studio preview /tmp/onizleme      # örnek ders, anahtarsız sessiz önizleme
```

OVH `lecturesift-instagram.timer` kapalı kalmaya devam eder. OVH'nin `instagram.env` rolü OpenAI ve
depolama anahtarlarını bilerek içermez; yayıncı ileride OVH'ye taşınırsa bu anahtarların role
eklenmesi ayrıca kararlaştırılmalıdır.
