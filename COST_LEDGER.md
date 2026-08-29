# LectureSift doğrulanmış maliyet defteri

Bu belge, sağlayıcı panellerindeki fatura ve kullanım kayıtlarının salt okunur
denetimidir. Kart numarası, API anahtarı, parola, fatura adresi veya başka bir
gizli bilgi içermez.

## Denetim dönemi

- Proje başlangıcı: **26 Ağustos 2026 23:16 (Europe/Istanbul)** — ilk Git
  kaydı.
- Son doğrulama: **29 Ağustos 2026**.
- Farklı para birimleri, ödeme gününün banka kuru bilinmeden birbirine
  çevrilmez. USD ve TRY toplamları bu nedenle ayrı tutulur.

## LectureSift'e doğrudan bağlanan, ödenmiş giderler

| Tarih | Sağlayıcı | Kalem | Ödenen | Kanıt / not |
|---|---|---|---:|---|
| 27.08.2026 | Netlify | Pro plan, ilk dönem | **20,00 USD** | Netlify ücretli makbuz listesi |
| 27.08.2026 | OpenAI API | Ön ödemeli API kredisi faturası | **12,00 USD** | OpenAI fatura geçmişi; vergi dahil ödeme |
| 27.08.2026 | GoDaddy | `lecturesift.com` ve Kurumsal E-posta Pro Light | **1.174,64 TRY** | GoDaddy sipariş 4172775780 |

Doğrudan proje toplamı: **32,00 USD + 1.174,64 TRY**.

GoDaddy brüt toplamının dökümü:

- `.COM` alan adı, 1 yıl: 549,99 TRY
- Kurumsal E-posta Pro Light, 1 yıl: 419,88 TRY
- KDV: 195,77 TRY
- ICANN ücreti: 9,00 TRY
- Toplam: 1.174,64 TRY

## Paylaşılan çalışma aracı

| Tarih | Sağlayıcı | Kalem | Ödenen | Muhasebe yaklaşımı |
|---|---|---|---:|---|
| 27.08.2026 | ChatGPT / Codex | Pro 20x planına geçiş | **218,35 USD** | LectureSift dışında da kullanılabilen paylaşılan araç; doğrudan altyapı toplamına eklenmez |

Proje dönemi içinde yapılan ödemelere paylaşılan ChatGPT/Codex planı da
eklenirse nakit çıkışı **250,35 USD + 1.174,64 TRY** olur. 24.08.2026 tarihli
24,00 USD ChatGPT Plus faturası proje başlangıcından önce olduğu için bu dönem
toplamına alınmadı. Temmuz 2026 tarihli 24,00 USD fatura da aynı nedenle hariçtir.

## Oluşmuş fakat henüz ödenmemiş / faturalandırılmamış

### Render

- Kesilmiş geçmiş fatura: **yok**
- Ağustos ayı bugüne kadar oluşan: **1,41 USD**
- Render'ın Ağustos ay sonu tahmini: **3,57 USD**
- Mevcut kalemler: servisler 0,53 USD, cron 0,07 USD, veri depoları 0,81 USD,
  pipeline 0,00 USD.
- Canlı kaynaklar: backend, worker, PostgreSQL, Key Value/queue ve günlük cron.
- Görülen saatlik oranlarla tam ay çalışma hızı yaklaşık **31 USD/ay**dır;
  kesin tutar yalnızca dönem sonu faturasıdır.

## Ücretsiz kota içinde kalan servisler

### Cloudflare R2

- Aktif ürün: R2 Paid (kullanım bazlı)
- Mevcut dönem maliyeti ve dönem tahmini: **0,00 USD**
- Kullanım: 135 Class A işlemi, 35 Class B işlemi, 0 GB-ay faturalı depolama
- Geçmiş fatura: yok

### Resend

- Transactional: Free, 3.000 e-posta/ay, **0 USD/ay**
- Marketing: Free, 1.000 kişi, **0 USD/ay**
- Kayıtlı ödeme yöntemi ve geçmiş fatura: yok

### GitHub

- Plan: GitHub Free, **0 USD/ay**
- Ağustos brüt ölçülen Actions kullanımı: 4,81 USD
- Dahil kullanım indirimi: 4,81 USD
- Faturalandırılabilir toplam: **0,00 USD**
- LectureSift deposunun brüt payı: 4,48 USD; bu tutarın tamamı ücretsiz kota
  içinde.

### Google Ads / Ad Manager

- Etkin Google hesabında seçilebilir Google Ads hesabı bulunmadı.
- Doğrulanmış reklam harcaması: **0**.
- Reklam hesabı açılıp kampanya başlatılana kadar maliyet oluşmaz.

## Kullanıma bağlı durum

### OpenAI API

- Ağustos organizasyon kullanımı: **2,03 USD**
- Son 7 gün, seçili API projesi kullanımı: **1,39 USD**
- Kalan API kredi bakiyesi: **7,97 USD**
- API projesinin adı `youtube-factory-backend` olduğu için LectureSift'e özel
  maliyet ayrımı kusursuz değildir. LectureSift için ayrı OpenAI projesi ve
  yalnız ona ait anahtar kullanılmalıdır.

### iyzico / PayTR

- Başarılı ödeme veya kesilmiş komisyon faturası bu denetimde doğrulanamadı.
- Bilinen 1 TRY kart denemeleri hata ile reddedildi; reddedilen işlem gelir veya
  komisyon olarak kaydedilmez.
- Kesin komisyon/iade tutarı için iyzico üye işyeri mutabakat ekranına giriş
  yapılması gerekir. PayTR canlı işlem kullanılmıyorsa gideri sıfırdır.

## Aylık çalışma hızı ve riskler

- Netlify: 20 USD/ay; mevcut dönemde 3.000 kredinin 1.522,4'ü iki günde
  kullanılmış. 101 production deploy, 1.515 kredi tüketti. Deployların
  birleştirilmesi gerekir; aksi halde kota dönem bitmeden tükenebilir.
- Render: mevcut canlı kaynaklarla yaklaşık 31 USD/ay; ilk fatura henüz
  kesilmedi.
- OpenAI API, Cloudflare R2 ve ödeme kuruluşları: kullanıma bağlı.
- Resend ve GitHub: mevcut kullanımda 0 USD.
- GoDaddy: alan adı ve e-posta yıllık; yenileme tutarı ilk yıl fiyatından farklı
  olabilir.
- ChatGPT/Codex: paylaşılan işletme aracı; proje kârlılığına dahil edilecekse
  aylık kullanım oranına göre paylaştırılmalıdır.

## Mutabakat kuralları

1. `Ödendi` toplamına yalnız sağlayıcı faturası veya ücretli makbuz girer.
2. Henüz kesilmemiş kullanım ayrı gösterilir.
3. Ücretsiz kota içindeki brüt kullanım gider sayılmaz.
4. Başarısız kart denemesi gider veya gelir değildir.
5. USD ve TRY, banka ekstresindeki işlem kuru olmadan tek sayıda birleştirilmez.
6. Her yeni fatura, sağlayıcı + hizmet + dönem anahtarıyla tek kez kaydedilir.
7. API kredisi satın alımı nakit çıkışıdır; kredi tüketimi aynı ödemenin ikinci
   kez gider yazılmasına yol açmamalıdır.

## Kalan doğrulama işleri

- iyzico üye işyeri mutabakatı ve varsa işlem komisyonlarını kontrol etmek.
- Render ilk fatura kesildiğinde 1,41 USD tahakkuku gerçek tutarla değiştirmek.
- Netlify bir sonraki dönemde ek kredi satın alınırsa ayrı fatura olarak eklemek.
- GoDaddy yenileme tarihinden önce alan adı ile e-posta yenileme fiyatını
  doğrulamak.
- ChatGPT/Codex gideri için proje kullanım yüzdesi belirlemek.
- LectureSift adına ayrı OpenAI API projesi/anahtarı açarak maliyet atfını
  kesinleştirmek.
