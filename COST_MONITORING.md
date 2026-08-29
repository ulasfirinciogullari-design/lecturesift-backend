# LectureSift maliyet izleme ve mutabakat modeli

Yönetim panelindeki **Maliyet** sekmesi iki ayrı toplam gösterir. Bunlar
birbirinin yerine kullanılmaz:

1. **Operasyon tahmini:** Uygulamanın ölçtüğü kullanım × tarih damgalı brüt
   liste fiyatı + yapılandırılmış sabit bütçe.
2. **Fatura/mutabakat kayıtları:** Sağlayıcı faturası, ödeme kuruluşu
   mutabakatı veya banka ekstresinden yönetici tarafından girilen gerçekleşmiş
   toplamlar.

Bu ayrım önemlidir. Ücretsiz kotalar, yuvarlama kuralları, vergiler, sözleşme
indirimleri, iade ve komisyonlar uygulama ölçümünden kesin olarak çıkarılamaz.
Panel bu nedenle tahmine hiçbir zaman “fatura” veya “muhasebe kârı” demez.

## Yüzde 100 doğruluk kuralı

Doğruluk yüzdesi sağlayıcı sayısını değil **sağlayıcı-gün kapsamını** ölçer.
Örneğin 30 günlük filtrede beş aktif sağlayıcı varsa 150 sağlayıcı-gün gerekir.
Yüzde 100 yalnızca her sağlayıcının her günü fatura/mutabakat dönemleriyle
kapsanırsa gösterilir. Kısmen örtüşen aylık bir fatura yalnızca örttüğü günler
kadar sayılır. Sabit plan tutarının `CONFIRMED` olması, tek başına fatura
mutabakatı sayılmaz.

Fatura ekranına API anahtarı, kart bilgisi, belge içeriği veya kişisel veri
girilmez. Yalnızca sağlayıcı/hizmet, dönem, para birimi, ara toplam, vergi,
açıklama ve fatura/ekstre referansı saklanır. Aynı sağlayıcı, hizmet ve dönem
yeniden girilirse kayıt güncellenir; mükerrer toplam yaratılmaz.

## Sürekli ölçülen değişken kullanım

- OpenAI yanıtındaki gerçek giriş, önbellek ve çıkış token adetleri iş ve hesap
  bağlamında ölçülür. Sağlayıcı kullanım alanı yoksa transkripsiyon için süre
  tabanlı tahmin açıkça `duration_estimate` olarak işaretlenir.
- Cloudflare R2 okuma/yazma istekleri uygulamanın gördüğü ölçüde kaydedilir.
  Depolama GB-ay, ücretsiz kota ve sağlayıcının faturalama yuvarlaması ancak R2
  faturasıyla kesinleşir.
- Her fiyat kaydında model/kaynak, ölçü birimi, fiyat kaynağı ve fiyatın geçerli
  olduğu tarih bulunur.
- İş bazlı toplamlar USD ve TCMB günlük USD satış kuruyla gösterilen TRY
  karşılığını içerir. TCMB geçici olarak alınamazsa yapılandırılmış yedek kur
  kullanılır ve kaynak ekranda açıkça belirtilir.

## Sabit bütçe yapılandırması

Bu değerler Render web servisinde tutulur. Sayısal değer bütçe/tahmin girdisidir;
karşılık gelen `*_CONFIRMED` değeri yalnızca mevcut plan tutarının kontrol
edildiğini belirtir. Gerçekleşmiş gider için ayrıca fatura kaydı gerekir.

- `LECTURESIFT_COST_RENDER_MONTHLY_USD`
- `LECTURESIFT_COST_RENDER_CONFIRMED`
- `LECTURESIFT_COST_NETLIFY_MONTHLY_USD`
- `LECTURESIFT_COST_NETLIFY_CONFIRMED`
- `LECTURESIFT_COST_RESEND_MONTHLY_USD`
- `LECTURESIFT_COST_RESEND_CONFIRMED`
- `LECTURESIFT_COST_DOMAIN_ANNUAL_USD` — yıllık tutar; bütçede aya bölünür
- `LECTURESIFT_COST_DOMAIN_CONFIRMED`
- `LECTURESIFT_COST_OTHER_MONTHLY_USD`
- `LECTURESIFT_COST_OTHER_CONFIRMED`
- `LECTURESIFT_COST_USD_TRY_FALLBACK` — yalnızca TCMB alınamadığında kullanılır

Bir plan, vergi veya indirim değiştiğinde önce tutar güncellenir, sonra kontrol
edildiyse onay bayrağı açılır. Eski faturalar silinmez; kendi dönemleriyle
mutabakat tablosunda kalır.

## Birim ekonomi

Panel seçili dönem için işlenen dakika, maliyetlendirilmiş iş, değişken
maliyet/dakika, sabit bütçe dağıtılmış maliyet/dakika ve maliyet/iş gösterir.
Ödenmiş siparişler bilinen gelire eklenir. “Komisyon öncesi katkı” ödeme
komisyonu, iade, vergi ve henüz girilmemiş faturalar düşülmeden önceki operasyon
göstergesidir; finansal tablo veya net kâr değildir.

iyzico/PayTR komisyonları iş yeri sözleşmesine, Google Ads gideri reklam
hesabına, Render/Netlify/Resend ek kullanımı ise sağlayıcı faturasına bağlıdır.
Bu kalemler ancak mutabakat kaydı girildiğinde kesin toplama dahil edilir.

## Güncel fiyat kaynakları

- OpenAI model fiyatları: <https://developers.openai.com/api/docs/models>
- Cloudflare R2: <https://developers.cloudflare.com/r2/pricing/>
- Render: <https://render.com/pricing>
- Netlify: <https://www.netlify.com/pricing/>
- Resend: <https://resend.com/pricing>

Liste fiyatı değiştiğinde oran kataloğu ve geçerlilik tarihi birlikte
güncellenmelidir. Muhasebesel kesin kaynak her zaman sağlayıcı faturası, banka
ekstresi ve ödeme kuruluşu mutabakatıdır.
