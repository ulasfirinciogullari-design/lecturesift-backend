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
