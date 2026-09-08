# Yeni satın alma planları — 8 Eylül 2026

## Asistan kredisi ilavesi — taslak

Güncellenen paket fiyatlarına aylık Lite 500, Plus 1.500, Pro 4.000, Max 10.000
asistan kredisi dahil edilir. Önceki satın almalar kendi koşullarını korur.
Bu ilave için aynı gün revize edilen temel paket fiyatları tekrar artırılmadı;
asistan kullanımı aşağıdaki katkı hesabından ayrıca düşülmelidir.

Ek paketler: 1.000 kredi 149 TL / 3,99 USD; 3.000 kredi 349 TL / 9,99 USD;
10.000 kredi 999 TL / 29,99 USD. Diğer 20 para biriminde sabit bölgesel
fiyatlar ayrı katalogda tutulur. Kullanılabilir tahsilat para birimleri ödeme
sağlayıcısına bağlıdır; dil para birimini değiştirmez. Ek krediler 365 gün,
pakete dahil krediler ilgili aylık hak dönemi boyunca geçerlidir.

8 Eylül resmî model belgesinde GPT-5.6 Luna standart fiyatı milyon giriş
tokenı için 0,20 USD, çıkış için 1,20 USD'dir. Kredi başına 1.000 ağırlıklı
token (çıkış ağırlığı 6) bu fiyatla en fazla yaklaşık 0,0002 USD normal model
maliyeti oluşturur; yukarı yuvarlama ve önbellek indirimleri maliyeti azaltabilir.
1.000 / 3.000 / 10.000 kredi tam kullanımında model maliyeti yaklaşık
0,20 / 0,60 / 2 USD olur. Mesaj geçmişi ve görsel tokenları da tüketimdir.
[Resmî model ve fiyat](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

Paketlere dahil kredilerin aylık tam model gideri 0,10 / 0,30 / 0,80 / 2 USD'dir.
Yerel katkıdan bu tutar × gerçekleşen USD/TRY maliyet kuru çıkarılmalıdır.
Özellikle yıllık Max stres senaryosu düşük katkı bırakabilir. Vergi, ödeme
komisyonu, sunucu, başarısız istekler, destek, iadeler ve ücretsiz denemeler
düşülmeden bu fark net kâr değildir. Gerçek marj ölçümü sonrası yalnız yeni
satın almalara uygulanacak yeni sürümlü fiyat/limit güncellemesi yapılmalıdır.
Kredi satışı ve kişisel asistan, şema/kurtarma ve sağlayıcı kontrolü tamamlanana
kadar kapalıdır; önizleme fiyatı tahsilat veya kâr gerçekleştiğini göstermez.

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

### Referans ödülü senaryosu — henüz canlı değil

Önerilen edinim ödülü, yeni ve doğrulanmış hesabın ilk uygun ücretli
aboneliğine bağlıdır. Davet eden 60 dakika veya sonraki TRY aylık abonelikte
%10 indirim (en fazla 50 TL) seçer; davet edilen 30 dakika kazanır. Kupon
tek kullanımlık, 90 gün geçerli ve diğer indirimlerle/yıllık fiyatla birleşmez.
Davet eden başına UTC takvim ayında en fazla beş ödüllü davet vardır; kota
çifte uygulanır. Sırf kayıt, ücretsiz/test paketleri ve ek kredi alımı ödül
üretmez. En az 14 günlük bekleme ve yönetici ödeme mutabakatı gerekir.

Yukarıdaki **varsayımsal** 0,35 TL/dakika stres bedeliyle, dakika seçeneğinde
çiftin 90 dakikası 31,50 TL değişken maliyet getirir. Kupon seçeneğinde en fazla
50 TL gelir indirimi ve 30 dakika için 10,50 TL değişken gider vardır; bunlar
muhasebede aynı gider türü değildir. Kupon kullanılmazsa nominal tutarı
gerçekleşmiş gider/gelir kaybı olarak yazmamak gerekir. Yıllık alımdaki tek
seferlik edinim maliyeti 12 ay boyunca tekrar oluşuyormuş gibi sayılmamalıdır.

Bunlar doğrulanmış kâr veya bütçe garantisi değildir. Gerçek iş maliyeti,
iade/itiraz, ücretler ve ödül kullanım oranı izlenmeli; kullanım pahalılaşırsa
yeni davetler için program durdurulabilmelidir. Kazanılmış koşullar sonradan
sessizce değiştirilmemelidir. Önizleme kodu şema/yedekleme geçişi tamamlanana
kadar programı kapalı tutar; bu belge canlıda ödül dağıtıldığını göstermez.

### Katalog ve ölçüm kontrolleri

8 Eylül devam taslağı: kullanıcı, davet ilişkisinin sonraki abonelik paketleri
ve yenilemelerde de ödül oluşturmasını seçti; ek dakika alımları hariç.
İlk alışveriş koşulları korunuyor. Yenileme için uygulama taslağı davetçiye
30 dakika veya %5/en fazla 25 TL kupon; davet edilene tekrar hoş geldin bonusu
yok. İlk ve sonraki ödüller aynı aylık beş işlem kotasını paylaşır; aynı davet
edilen kişi için ayda en fazla bir yenileme ödülü ayrılır. Yıllık paketin aylık
kota açılışı yeni ödeme sayılmaz. Sürümlü defter, eski hakların korunması ve
yeni şema/kurtarma kanıtları `deploy/REFERRAL_RELEASE_GATES.md` kapsamındadır.
Kullanıcının dil/para birimi düzeltmesiyle yeni kuponlar 22 katalog para biriminde
sabit bölgesel tavanlara sahip; 13 arayüz dili ile para birimi seçimi bağımsızdır.
TL cinsinden yukarıdaki tavanlar diğer para birimlerine doğrudan aynı sayıyla
taşınmaz. Sürümlü politika tavanları Lite bölgesel fiyat oranlarından sabitlenir;
canlı kur değildir. Kupon seçilen para biriminde verilir ve aynı para biriminde
kullanılır. Mevcut ödeme seçeneğinin desteklemediği para biriminde yeni kupon
verilmez; dakika seçeneği kullanılabilir. Eski TRY kuponlarının koşulları korunur.
Bu değişiklik de kapalı önizleme kodudur; canlı kampanya veya doğrulanmış kâr
değildir. Üstteki ilk alışveriş hesaplarına ek ayrı bir aylık kota yaratmaz.

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

## Önizleme doğrulaması

82c9a47 yalnız `codex/product-theme-refresh` dalına gönderildi; PR #67 taslak,
ana dal değişmedi. Netlify önizlemesi mevcut canlı API'yi kullanır: tema
incelenebilir, ancak yeni fiyatların canlıya geçtiği veya güvenli bir ödeme
test ortamı olduğu varsayılmamalıdır.

İlk uzak test çalışmasında 908 test geçti, 5 test başarısız oldu. Üç misafir
hesabı uyumluluğu hatası, güncellenen kota test verisi ve yeni satın alma
tablosunun eski şema sözleşmesiyle doğrulanması ele alındı. Yeni şema/recovery
sözleşmeleri ayrı sürümlerdir; eski yedek sözleşmeleri değiştirilmedi. Yeni
yönetici plan atamaları da haklarını saklar, eski yönetici atamaları korunur.

Yerel hedefli testler geçse bile gerçek PostgreSQL 18 doğrulaması ve bütün
uzak testler yeni önizleme sürümünde geçmeden üretime geçiş yapılmamalıdır.
Canlı sağlık kontrolü, gerçek kart tahsilatının, tüm dosya türlerinin uçtan
uca işlendiğinin veya reklam gelirinin doğrulandığı anlamına gelmez.
