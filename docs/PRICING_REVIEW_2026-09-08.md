# Yeni satın alma planları — 8 Eylül 2026

Durum: önizleme / yayın incelemesi. Bu belge canlı fiyatların değiştiğinin
veya garantili kârın kanıtı değildir. Mevcut siparişlerin ve aboneliklerin
satın alma koşulları korunmalıdır. Canlıya çıkmadan yeni satın alma kayıtlarının
worker yetkileri ve veritabanı yedekleme/geri yükleme sözleşmeleri doğrulanmalıdır.

## Yeni katalog

| Plan | Aylık TRY | Dakika/ay | Tek iş/dk | Kaynak/iş | Quiz / kart | Geçmiş/gün | Kuyruk |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| Lite | 299 | 400 | 120 | 12 | 10 / 20 | 30 | Standart |
| Plus | 599 | 900 | 240 | 16 | 20 / 40 | 90 | Standart |
| Pro | 1.199 | 2.000 | 360 | 24 | 30 / 60 | 365 | Öncelikli |
| Max | 2.299 | 4.000 | 600 | 24 | 30 / 60 | 730 | Öncelikli |

Hepsinde ayrıntılı özet, isteğe bağlı quiz/kart, PDF/Word/TXT ve reklamsız
kullanım var. Quiz/kart sayıları iş başına üst sınırdır, zorunlu çıktı değildir.
Öncelik kuyruk sırasını etkiler; sabit teslim süresi garantisi değildir.
Ücretsiz ve tek seferlik kredi koşulları değişmez. 180 dakika kredi 199 TL'dir.
Mevcut eski kullanıcıların hakları yeni limitlerle geriye dönük azaltılmaz.

Yıllık seçim: 10 aylık ücret, 12 aylık süre, her ay aylık kota; 12 aylık
dakika tek seferde açılmaz. USD: 8,99 / 16,99 / 32,99 / 59,99.
EUR: 8,49 / 15,99 / 30,99 / 56,99. Diğer para birimleri sabit bölgesel
fiyatlardır, canlı kur dönüşümü değildir. Gösterim para birimi, ödeme
sağlayıcısının o para biriminde tahsilat yapabildiği anlamına gelmez.

## Maliyet yöntemi ve belirsizlik

8 Eylül kontrolünde resmi kaynaklar: mini transkripsiyon için yaklaşık
0,003 USD/ses dakikası; GPT-4o-mini metin için 1 milyon giriş tokenı başına
0,15 USD, çıkış için 0,60 USD.
[OpenAI fiyatlandırma](https://developers.openai.com/api/docs/pricing),
[model fiyatı](https://developers.openai.com/api/docs/models/gpt-4o-mini).

Basit model: USD maliyet = ses dakikası × kullanılan modelin dakika bedeli
+ giriş tokenı × giriş bedeli / 1.000.000
+ çıkış tokenı × çıkış bedeli / 1.000.000.
Gerçek API token kullanımı varsa dakika tahmini yerine o esas alınır.
OCR/görsel analiz, çeviri, tekrar denemeler ve konuşmacı ayrımı ayrıca
maliyet üretir. PDF'nin ücretlendirilen dakikası ses dakikasıyla aynı
hesap değildir. Kaynak/çıktı saklama ve indirme trafiği de izlenmelidir.

Aşağıdaki tablo bir senaryodur: satış tutarı içinde **varsayımsal %20 vergi**,
brüt tutarın **varsayımsal %5 ödeme maliyeti**, tüketilen dakika başına
0,20 TL normal veya 0,35 TL stres değişken maliyeti. Bu oranlar doğrulanmış
iyzico sözleşmesi, güncel kur, vergi görüşü veya muhasebe kaydı değildir.
Sabit sunucu/veritabanı/e-posta/depolama, destek, iadeler, ücretsiz kullanıcı
maliyeti ve pazarlama henüz düşülmemiştir. Dolayısıyla aşağıdakiler net kâr değil,
bu giderleri karşılamaya kalan katkıdır.

| Plan | Aylık tam kullanım / normal | Aylık / stres | Yıllık efektif aylık / normal | Yıllık / stres |
| --- | ---: | ---: | ---: | ---: |
| Lite | 154 TL | 94 TL | 115 TL | 55 TL |
| Plus | 289 TL | 154 TL | 211 TL | 76 TL |
| Pro | 539 TL | 239 TL | 383 TL | 83 TL |
| Max | 1.001 TL | 401 TL | 701 TL | 101 TL |

Formül: katkı = brüt / 1,20 − brüt × 0,05 − kullanılan dakika × birim maliyet.
Yıllık sütununda brüt aylık fiyatın 10/12'sidir. Kur yükselmesi veya çok
yoğun görsel/konuşmacı analizi stres eşiğini aşarsa özellikle yıllık paketler
zarar edebilir; sınırsız kullanım ve garantili kârlılık iddiası yapılmamalıdır.

İşletme başa baş hesabı: aylık doğrulanmış sabit gider / kullanıcı başına
ağırlıklı ortalama katkı. Reklam onayı ve gerçek tahsilat oluşmadan reklam
geliri bu hesaba eklenmez. Kodlama araçları (Codex/ChatGPT), alan adı ve
işletme giderleri de işletme toplamına dahildir; iş başına API gideriyle
veya ön ödemeli bakiye yüklemesiyle mükerrer sayılmamalıdır.

## İşletim kuralları

- Gerçek son 30 gün API/altyapı faturaları ve iyzico mutabakatı olmadan
  yüzde yüz doğrulanmış marj gösterme. Eksik veri açıkça işaretlenmeli.
- Tam kota ve yıllık indirim senaryosunu her fiyat revizyonunda tekrar hesapla.
- Ücretsiz kullanıcıların ve başarısız/tekrar işlemlerin maliyetini de say.
- Eski planlar sona erene kadar eski satın alma koşullarını koru; bekleyen
  eski siparişi yeni düşük kotayla etkinleştirme.
- Yeni koşullar sipariş anında sunucuda sabitlenir; istemciden fiyat/hak
  listesi alınmaz. Ödeme/webhook doğrulaması fiyat değişikliğinden bağımsızdır.
- Güncel ve eski kataloğu aynı veritabanıyla kabul eden worker, backup ve
  restore kontrolleri tamamlanmadan fiyat revizyonunu üretime çıkarma.
- AdSense incelemesi ve gerekli CMP kurulumu tamamlanmadan reklamları açma.
  Ödüllü dakika, güvenilir sağlayıcı doğrulaması olmadan etkinleştirilmez.
