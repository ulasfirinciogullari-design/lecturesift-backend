LECTURESIFT V4 — TÜRKÇE KISA REHBER

LectureSift bir ders videosundan şunları üretir:
- Orijinal ve isteğe bağlı çevrilmiş transkript
- Yapılandırılmış ders notları ve özet
- Gerçek sunum slaytları
- Quiz ve bilgi kartları
- Ayrı PDF/TXT dosyaları ve tümünü içeren ZIP paketi

ÇİFT KAYNAK MODU
Ana/ses videosu transkript ve yapay zekâ içerikleri için kullanılır. İsteğe bağlı
ikinci slayt videosu eklendiğinde görsel tarama yalnızca bu eş zamanlı kayıtta
yapılır. İki video aynı anda başladıysa zaman farkı 0 bırakılır; başlangıç farkı
varsa saniye cinsinden düzeltme uygulanabilir. İki dosyanın toplam yükleme sınırı
1 GB'dır.

CANLI ADRESLER
Arayüz: https://clever-horse-22b1a8.netlify.app/
Backend: https://lecturesift-backend.onrender.com/

V4'TEKİ ANA DEĞİŞİKLİKLER
- Ses ve görüntü paralel işlenir.
- Uzun derslerin sesi 20 dakikalık güvenli parçalara ayrılır.
- Slayt motoru bellekte tam kare saklamaz; yalnızca zaman damgalarını tutar.
- İnsan, sınıf ve sıradan sahneleri slayt sanmamak için yerleşim, metin,
  yüz/ten, kalıcılık ve tekrar kontrolleri birlikte kullanılır.
- Sonuçlar web arayüzünde açılır; PDF/TXT dosyaları tek tek veya ZIP olarak indirilir.
- Hatalar kullanıcıya kısa bir açıklama ve LS-... destek koduyla gösterilir.

GÜVENLİ YAYIN KURALI
main dalı canlı Netlify ve Render sürümüdür. Değişiklikler canlıya alınmadan önce
otomatik testlerden ve ilgili yerel kabul testinden geçmelidir.

İLK KABUL TESTİ
Carleton College Biology 252 videosunda gerçek slayt bulunmadığı için beklenen
sonuç tam olarak 0 slayttır. Ardından gerçek slayt içeren ikinci bir dersle kayıp
slayt testi yapılmalıdır.

27 AĞUSTOS 2026 SONUCU
224,9 saniyelik 7,3 MB gerçek Biology 252 videosu tek geçişli V4 slayt motorunda
2,63 saniyede tarandı. Tepe bellek yaklaşık 81 MB oldu ve sonuç tam olarak 0
slayt çıktı.
V3.2'deki 9 yanlış slayt böylece bu kabul örneğinde tamamen elendi.

İKİNCİ GERÇEK VİDEO TESTİ
University of Manchester'ın 4:28'lik WebM eğitim videosu; ofis röportajı ile üç
tam ekran veya slayt ağırlıklı eğitim karesini birlikte içeriyordu. V4 görsel
analizi 4,25 saniyede ve yaklaşık 84 MB tepe bellekle tamamladı. Tüm konuşmacı/
ofis kareleri elendi; 01:13, 02:42 ve 04:15'teki üç gerçek slayt korundu.

NOT
Render ücretsiz servisleri boşta kaldığında uyuyabilir; ilk istek daha yavaş
başlayabilir. OPENAI_API_KEY yalnızca Render Environment alanında saklanmalıdır.

PAYTR TEST KURULUMU
Render'da PAYTR_MERCHANT_ID, PAYTR_MERCHANT_KEY ve PAYTR_MERCHANT_SALT gizli
ortam değişkenleri tanımlanana kadar kartlı ödeme kapalı kalır. Test sırasında
PAYTR_TEST_MODE=true kullanılmalıdır. PayTR Bildirim URL adresi:
https://lecturesift-backend.onrender.com/billing/paytr/callback
Bu adres kullanıcı sayfası değildir; PayTR'ın imzalı ödeme sonucunu gönderdiği
sunucu adresidir. Anahtarlar koda, GitHub'a veya uygulama kayıtlarına yazılmaz.

YÖNETİCİ ERİŞİMİ
Render'daki BILLING_ADMIN_EMAILS değerine doğrulanmış sahip e-posta adresleri
virgülle ayrılarak yazılır. Bu hesaplar normal giriş yaptıktan sonra yönetici
panelini otomatik açabilir. BILLING_ADMIN_TOKEN yalnızca acil durum yedeğidir;
koda veya tarayıcıya kalıcı olarak kaydedilmez.
