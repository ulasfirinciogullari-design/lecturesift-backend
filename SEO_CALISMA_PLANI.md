# LectureSift SEO çalışma planı

Başlangıç denetimi: 23 Eylül 2026. SEO çalışmaları bu proje üzerinden
takip edilir. Başlangıç varsayımı Türkiye'deki üniversite öğrencileri ve
Türkçe aramalardır. İlk rekabet geliştirmesi üç ana ürün sayfasının Türkçe
ve İngilizce sürümlerine uygulanır; diğer diller mevcut içeriklerini korur.

## 9 Ekim 2026 sayfa açıklamalarının iyileştirilmesi

Canlı denetimde Hakkımızda ve İletişim açıklamaları yalnız sayfa adını
tekrar ediyordu; örneğin İngilizce Hakkımızda açıklaması “About LectureSift.”
idi. Bu iki sayfanın Türkçe kaynak açıklaması ve 13 dildeki karşılıkları,
mevcut gövde içeriğini özetleyecek şekilde düzenlendi. Hakkımızda ürünün
öğrenme materyalleri üretme amacını ve AI çıktılarının kontrolünü;
İletişim ise teknik destek, hesap, ödeme/iptal/iade ve gizlilik konularıyla
form/e-posta kanallarını anlatıyor. Yeni özellik, yanıt süresi veya sonuç
garantisi eklenmedi.

Bu bir açıklama iyileştirmesidir; indekslenmemenin kanıtlanmış nedeni
veya indeksleme çözümü olarak sunulmaz. Mevcut üretim akışı açıklamaları
statik olarak çevirip ilk HTML, Open Graph ve Twitter alanlarına taşır.
Canonical, robots, sayfa gövdesi ve
site haritası tarihleri değiştirilmedi.

Kaynak değişikliği güncel ana sürümden ayrı dalda hazırlanıyor. Mevcut
GitHub Actions kontrolleri çalıştırılacak; yerel test/derleme yapılmadı.
Bu aşamada canlı etkinleşme veya Google'ın yeni açıklamaları kullanması
doğrulanmış değildir. Aşağıdaki denetim sonuçları bu metin değişikliğinden
önceki gözlemlerdir.

## 9 Ekim 2026 tam site haritası URL Denetimi

İlk 30 adreslik inceleme, kullanıcının devam talebiyle **canlı site
haritasındaki 183 adresin tamamına** genişletildi. Harita 00:57 TSİ'de
okundu. Aynı oturumdaki 16 güncel harita adresinin sonucu tekrar
kullanıldı, kalan 167 adres Google URL Denetimi ile okundu. Son denetim
01:01:32 TSİ'de tamamlandı; tüm sonuçlar 00:45–01:01 TSİ aralığından.
Hata veya atlanan URL yok. Eski/özel adresler dahil oturumdaki toplam
URL Denetimi isteği 199; aşağıdaki sayılar yalnız 183 harita adresine aittir.

| Google'ın bildirdiği durum | Haritadaki URL sayısı |
| --- | ---: |
| Gönderildi ve indekslendi | **152** |
| Google tarafından bilinmiyor | **21** |
| Keşfedildi, şu anda indekslenmiş değil | **7** |
| Uygun canonical etiketli alternatif sayfa | **3** |
| Toplam | **183** |

[183 adreslik CSV dökümü](docs/seo/sitemap-url-inspection-2026-10-09.csv)
ve [ayrıntılı JSON kanıtı](docs/seo/sitemap-url-inspection-2026-10-09.json)
her adresin durumunu, denetim zamanını ve son Google taramasını içerir.
CSV'deki canlı HTTP/canonical alanları indeks dışı 31 adres için
doldurulmuştur; diğer satırlardaki boş alanlar canlı hata anlamına gelmez.
Google'ın seçtiği canonical alanı araçta sunulmadığından açıkça
`NOT_EXPOSED_BY_GSC_WIZARD` olarak işaretlidir; tahminle doldurulmadı.

İndeks dışı **31 adresin tamamı** canlıda ayrıca kontrol edildi: 29'u
GSC Wizard'ın uzak sayfa denetimiyle, Portekizce iki adres sınırlı HTTP
istekleriyle. Hepsi **200, self-canonical ve noindex olmadan** yanıt
veriyor. Bu harita kapsamında Google'ın robots/noindex/404/sunucu hatası
nedeniyle dışladığı bir kayıt bulunmadı. Bu gözlem geçmişteki bütün
tarama koşullarının sorunsuz olduğunu kanıtlamaz.

Üç alternatif canonical kaydı `/quiz-flashcards`, `/pt/quiz-flashcards`
ve `/pt/contact`. Son taramaları 29–30 Ağustos UTC; üç adresin de
canonical/yönlendirme düzeltmesi 7 Eylül'deki `2176d74` sürümünde mevcut.
Portekizce iki eski `.html` adresi bugün temiz karşılıklarına 301 veriyor.
Google'ın kesin canonical hedefleri mevcut aracın sınırlaması nedeniyle
hâlâ doğrulanmamış durumda.

Keşfedilip taranmayan 7 adres: `/en/distance-sales`, `/fr/contact`,
`/es/refund`, `/it/quiz-flashcards`, `/ru/features`, `/ar/quiz-flashcards`
ve `/hi/lecture-video-summary`. Bu 7 ve Google tarafından bilinmeyen
21 adresin hiçbirinde son tarama tarihi yok. Yeni yayınlanan
`/pdf-note-check` ile `/en/pdf-note-check` bilinmeyen 21 adresin içinde;
yeni sayfalar bu denetim sırasında indekslenmiş sayılmadı.

Çok dilli kaynak incelemesinde genel sayfaların çeviri katalogları ve
dil bağlantılarında yeni kusur bulunmadı. Kısa başlık/açıklama, kelime
sayısı ve önerilen Article alanlarına ilişkin araç uyarıları tek başına
indekslenmeme nedeni sayılmadı. Özellikle Japonca/Çince sayfaların düşük
kelime sayısından içerik kalitesi sonucu çıkarılmadı. Google'ın neden
henüz taramadığı mevcut verilerle kesinleştirilemiyor.

Önceki **00:48:32 TSİ site haritası gönderimi kabul edildi**; tam inceleme
sırasında tekrar gönderim yapılmadı. Yeni kaynak hatası saptanmadığından
uygulama kodu değiştirilmedi. `lastmod` tarihleri yapay olarak
yenilenmedi, indekslenebilir sayfalara `noindex` eklenmedi. Rapor ve
kanıtlar çalışma alanına kaydedildi; yerel test/derleme veya yeni CI
çalıştırılmadı. Sonraki anlamlı değişiklik Google'ın haritayı yeniden
okuması veya bu adreslerden birini ilk kez/yeniden taraması olacak;
aynı kaydı birkaç dakika arayla tekrar sorgulamak yeni tarama başlatmaz.

Dayanaklar: [Google URL Denetimi açıklaması](https://support.google.com/webmasters/answer/9012289?hl=en),
[sayfa indeksleme durumları](https://support.google.com/webmasters/answer/7440203?hl=en)
ve [site haritası / lastmod yönergeleri](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).

## 9 Ekim 2026 ilk 30 adreslik Google URL Denetimi

İlk bağlantıdaki boş mülk listesi, kullanıcının Search Console erişimli
Google hesabını GSC Wizard'a eklemesiyle giderildi. Mevcut
`sc-domain:lecturesift.com` mülkü sahip yetkisiyle okunabiliyor; yeni
Search Console mülkü oluşturulmadı.

9 Ekim **00:45–00:47 TSİ** arasında **30 farklı URL** Google URL Denetimi
ile okundu. Türkçe quiz için ayrıntılı tekrar dahil 31 istek yapıldı;
tüm istekler başarılı oldu. Bu, öncelikli sayfalar ve eski/özel/hatalı
adreslerden oluşan bir örneklemdir; tüm sitenin indeks sayısı değildir.
[URL bazında sonuçlar, son taramalar ve canlı denetim verileri](docs/seo/url-inspection-2026-10-09.json)
ayrı kaydedildi. Dosyadaki API zamanları UTC, buradaki saatler TSİ'dir.

| Google'ın bildirdiği durum | URL sayısı | Yorum |
| --- | ---: | --- |
| Submitted and indexed | 18 | 13 güncel herkese açık adres ve eski taramaya dayanan 5 eski/özel adres. |
| URL is unknown to Google | 4 | 2 önemli İngilizce ürün sayfası, eski İngilizce quiz `.html` adresi ve hatalı iç içe adres. |
| Page with redirect | 5 | Temiz adreslere taşınmış eski `.html` adresleri; beklenen dışlama. |
| Excluded by ‘noindex’ tag | 2 | İngilizce çalışma alanının temiz ve `.html` adresleri; beklenen dışlama. |
| Alternate page with proper canonical tag | 1 | Türkçe `/quiz-flashcards`; eski tarama kaydı ayrıca incelendi. |

Ana sayfa, PDF, video, quiz, Cornell ve aktif hatırlama sayfalarının TR/EN
sürümlerinden oluşan 12 öncelikli adresin **9'u indeksli**, 3'ü indeks
dışı. Cornell ve aktif hatırlama rehberlerinin iki dili de indeksli.

| Öncelikli indeks dışı adres | Google sonucu ve son tarama | Canlı durum ve değerlendirme |
| --- | --- | --- |
| `/quiz-flashcards` | Uygun canonical etiketli alternatif; 30 Ağustos 00:42 TSİ (29 Ağustos 21:42 UTC). | 200, self-canonical, indekslemeye izinli. Eski `/quiz-flashcards.html` Google'da indeksli; canlıda temiz adrese 301 veriyor. |
| `/en/lecture-video-summary` | Google tarafından bilinmiyor; son tarama yok. | 200, self-canonical, site haritasında ve İngilizce ana sayfa/özellikler/Cornell/kütüphane bağlantılarında mevcut. Eski `.html` sürümü Google'da indeksli, canlıda temiz adrese 301 veriyor. |
| `/en/quiz-flashcards` | Google tarafından bilinmiyor; son tarama yok. | 200, self-canonical, site haritasında ve İngilizce ana sayfa/özellikler/aktif hatırlama/kütüphane bağlantılarında mevcut. Eski `.html` sürümü de Google tarafından bilinmiyor. |

Kaynak geçmişi eski quiz sonucunu açıklayan somut kanıt sunuyor:
29 Ağustos sürümü `/quiz-flashcards.html` canonical'ı üretiyordu.
[7 Eylül `2176d74` değişikliği](https://github.com/ulasfirinciogullari-design/lecturesift-backend/commit/2176d7494788aa08555cf55c1262784f5119ffce)
canonical, site haritası ve dahili bağlantıları temiz adreslere çevirdi,
`.html` için 301 ekledi. Google'ın quiz kaydının son taraması bundan önce.
Bu, eski kaydın etkisini destekler; Google'ın bugün seçtiği adresin
doğrudan okunması yerine geçmez. İngilizce sayfaların neden henüz
taranmadığı mevcut verilerden kesinleştirilemez.

Google'ın **seçtiği canonical adres** bu denetimde henüz doğrulanamadı:
GSC Wizard'ın tekli/toplu denetim ve geçmiş araçları `googleCanonical`
ve `userCanonical` alanlarını aktarmıyor. Bu alanların araçta bulunmaması,
Google'ın boş canonical döndürdüğü anlamına gelmez. [Google API şeması](https://developers.google.com/webmaster-tools/v1/urlInspection.index/UrlInspectionResult)
bu alanları destekliyor; canlı HTML beyanı ayrı bir veridir. Bu görevde
çağrılabilir oturumlu tarayıcı aracı da yok. Kullanıcı işlemleri kendisinin
yapması yerine ajanın sürdürmesini istedi; manuel ekran okuma kullanıcıya
bekleyen görev olarak bırakılmadı. Kesin canonical hedefi mevcut araç
kısıtı nedeniyle doğrulanmamış olarak tutuluyor.

`/fr/workspace.html`, `/lecture-video-summary.html`, `/fr/refund.html`,
`/quiz-flashcards.html` ve `/en/lecture-video-summary.html` eski Ağustos
taramalarıyla hâlâ indeksli görünüyor. Canlıda ilki `noindex,follow`,
diğer dördü 301 taşıyor. Düzeltmelerin Google tarafından yeniden
işlendiği henüz söylenemez; robots.txt ile taramalarını engellememek gerekir.

### Yapılan işlem ve kalan takip

00:48 TSİ canlı site haritası 183 URL içeriyordu; 00:40'taki 181 adrese
başka yayınla `/pdf-note-check` ve `/en/pdf-note-check` eklenmişti.
Bu görev bu sayfaları oluşturmadı. Haritada eski `.html` adresi yok;
üç öncelikli indeks dışı ürün adresi mevcut.

Site haritası **9 Ekim 00:48:32 TSİ** tarihinde Search Console'a yeniden
gönderildi; yanıt `accepted=true`, `confirmed=true`, `isPending=true`.
Google'ın dosyayı yeniden okuması bekleniyor. Bu, URL başına “Dizine
eklenmesini iste” veya “Düzeltmeyi doğrula” işlemi değildir ve indeksleme
başarısı sayılmaz. Önceki son okuma 4 Ekim 12:11 TSİ idi; eski sitemap
yanıtındaki `indexed=0`, URL Denetimi sonuçlarının yerine kullanılmadı.

Sorunlu üç ürün sayfasının uzak canlı denetiminde erişim, robots veya
canonical engeli bulunmadı. Aracın `Article.datePublished` alanını
“required” sayan uyarısı, [Google'ın Article belgesindeki](https://developers.google.com/search/docs/appearance/structured-data/article)
önerilen alan tanımıyla aynı değildir; indekslenmeme sebebi diye sunulmadı,
uydurma yayın tarihi eklenmedi. Yeni bir kaynak hatası kanıtlanmadığından
uygulama kodu değiştirilmedi; rapor ve kanıt dosyası güncellendi.
Yerel test/derleme veya yeni CI çalıştırılmadı.

Sonraki kontrol: yeniden gönderilen site haritasının okunma tarihini ve
üç ürün sayfasının son tarama/indeks durumunu tekrar karşılaştırmak;
Türkçe quiz için Google'ın seçtiği canonical adresi tamamlamak.
Yeni zamanlanmış otomasyon oluşturulmadı.

**00:51 TSİ takip kontrolü:** Quiz URL Denetimi aynı alternatif sayfa
durumunu ve 29 Ağustos 21:42 UTC son taramasını döndürdü. Site haritasının
00:48:32 TSİ gönderimi hâlâ beklemede; son indirilme tarihi hâlâ 4 Ekim.
Aynı harita tekrar tekrar gönderilmedi. Mevcut araçlar doğrudan
“Dizine eklenmesini iste” eylemini veya Google'ın seçtiği canonical
alanını sunmuyor. Kaynakta doğru self-canonical ve kalıcı yönlendirme
zaten bulunduğundan yeni bir kod değişikliği gerekmedi.

### Aynı oturumdaki ilk canlı doğrulama — 00:40 TSİ

9 Ekim **00:40 TSİ** (8 Ekim 21:40 UTC) sırasında 23 sınırlı, salt okunur
HTTP isteği yapıldı. Aşağıdaki 12 sayfanın her biri 200 döndürdü; ilk
HTML'de tek ve kendisini gösteren canonical vardı, `noindex` yoktu.
Adresler `https://lecturesift.com` alan adına göredir; bu tablo Google'ın
indeksindeki canonical seçimini değil, **sitenin bildirdiği** adresi kaydeder.

| Türkçe adres / canonical | İngilizce adres / canonical | HTTP |
| --- | --- | --- |
| `/` | `/en/` | Her ikisi 200 |
| `/document-summary` | `/en/document-summary` | Her ikisi 200 |
| `/lecture-video-summary` | `/en/lecture-video-summary` | Her ikisi 200 |
| `/quiz-flashcards` | `/en/quiz-flashcards` | Her ikisi 200 |
| `/cornell-notes` | `/en/cornell-notes` | Her ikisi 200 |
| `/active-recall` | `/en/active-recall` | Her ikisi 200 |

Önceki kaynak düzeltmelerinin canlı davranışı ayrıca doğrulandı:

- `/features.html`, `/en/plans.html`, `/ko/privacy.html` temiz adreslerine
  301 veriyor; `www` ürün adresi ana alan adına 301 veriyor.
- Üretim Netlify alt alan adındaki `/document-summary?seo_probe=canonical`
  aynı yol ve sorguyla `https://lecturesift.com` adresine 301 veriyor.
- `/en/en/features` ve denetim için seçilen var olmayan İngilizce adres
  gerçek 404 döndürüyor; ikisinde de `noindex,nofollow` var.
- `/en/workspace` ve `/fr/workspace.html` 200 ve `noindex,follow`
  döndürüyor. Çalışma alanının indeks dışı bırakılması kasıtlıdır;
  bu işareti kaldırmak bir SEO düzeltmesi değildir.
- `robots.txt` 200, `Allow: /` ve doğru site haritası adresini veriyor.
  Canlı `sitemap.xml` 200 ve 181 adres içeriyor; bu indeks sayısı değildir.

`6bc4a46` kaynak sürümünün canonical, hreflang, site haritası ve
yönlendirme üretimi salt okunur incelendi. [Google canonical yönergeleri](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)
site sinyalleri ile Google'ın seçimini birbirinden ayırır.

## 8–9 Ekim 2026 Search Console incelemesi

Windsor üzerinden `sc-domain:lecturesift.com` yeniden okundu. 8 Ekim
20:56:53 UTC veri görüntüsünde site haritası **181 adres, 0 hata, 0 uyarı**
bildiriyor; son okuma 4 Ekim 09:11 UTC. Bu, indekslenen sayfa sayısı değildir.
8 Eylül–5 Ekim kesinleşmiş 28 günlük site toplamı **133 gösterim, 3 tıklama,
%2,26 CTR**. Görünür sorgu satırları anonimleştirilen sorguları içermediği
için bu toplamın yerine kullanılamaz. Bu küçük örneklem rekabette başarı
veya yayın değişikliklerinin etkisini kanıtlamaz.

Canlı isteklerde doğrulananlar:

- `/features.html`, `/en/plans.html`, `/ko/privacy.html` temiz adreslerine
  301 ile gidiyor. Geçmiş raporlarda `.html` gösterimi bulunması bugünkü
  yönlendirme hatasının kanıtı değil.
- `/en/workspace` ve `/fr/workspace.html` ilk HTML'de `noindex,follow`
  taşıyor; robots.txt bunların taranmasını engellemiyor.
- `www` ana alan adına yönleniyor. Buna karşılık üretimin Netlify alt
  alan adı kendi adresinde 200 veriyor. Kaynak düzeltmesi bu belirli alan
  adını, yolu ve sorguyu koruyarak `lecturesift.com` adresine 301 ile taşır.
- `/en/en/features` yanlış iç içe adreste İngilizce sayfayı 200 ile
  sunuyor. Kaynak düzeltmesi geniş dil rewrite kurallarını kaldırır;
  yalnız gerçek ortak dosyalar ve açık noindex uygulama sayfaları için
  tam adresli kurallar üretir. Hatalı iç içe adresler 404 olur; eksik bir
  çeviri de sessizce Türkçe sayfaya dönüşmez.
- Herkese açık sayfalardaki büyük resim/arama önizlemesi izinleri ilk
  HTML'e eklenir; rehberler dahil 181 adres için JavaScript gerekmez.
  Bu izinler Google'ın o görünümü kullanacağına dair garanti değildir.

Kaynak kontrolleri mevcut GitHub Actions üzerinden yürütülür. Yerel
derleme/test yapılmaz. Kaynak değişikliği, CI başarısı ve canlı etkinleşme
ayrı ayrı doğrulanmalıdır.

Netlify destek kaydı #1128504'e gelen yanıt, 23 Eylül aralıklı 500
yanıtlarını sağlayıcının yaklaşık bir saatlik kısmi kesintisiyle eşleştirip
çözüldüğünü bildiriyor. 8 Ekim ana sürümünün
[Frontend delivery kontrolü başarılı](https://github.com/ulasfirinciogullari-design/lecturesift-backend/actions/runs/37710583709).
Bugünkü sınırlı okumalarda da 500 görülmedi; bunlar kesintisiz hizmet
garantisi değildir. Aşağıdaki 23 Eylül hata kayıtları tarihsel kanıttır.

Search Console'da doğrudan yapılan bir ayar değişikliği veya “Düzeltmeyi
doğrula” işlemi yoktur: mevcut Windsor bağlantısının yazma eylemi listesi
boş; alanları performans ve site haritasıyla sınırlı. URL Denetimi için
GSC Wizard önerildi, bağlantısı henüz doğrulanmadı. Bu erişimle öncelikle
TR/EN ürün sayfaları, Cornell/aktif hatırlama rehberleri, eski `.html`
adresleri ve çalışma alanının son tarama, indekslenme nedeni ve Google'ın
seçtiği canonical bilgisi okunmalı. Sonuç olmadan “tüm indeksleme
sorunları çözüldü” veya “yeniden indeksleme istendi” denmemelidir.

Teknik dayanaklar: [Netlify adres kuralları](https://docs.netlify.com/manage/routing/redirects/redirect-options/),
[Google robots önizleme yönergeleri](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag).

## 23 Eylül rekabet geliştirmesi

Kullanıcının uygulama talebi üzerine ilk paket aşağıdaki işleri kapsar:

- PDF, video/ses ve quiz sayfaları için altı özgün içerik sürümü: arama
  niyetini açıklayan başlıklar, kaynak–sonuç örnekleri, kullanım adımları,
  ürünün gerçek sınırları, sorular ve ilgili rehberlere bağlantılar.
- Örnek cevaplar hesaplanabilir ve kaynağı sayfada görünür. Öğretim örneği
  oldukları açıkça belirtilir; gerçek bir işlem veya müşteri sonucu gibi
  sunulmaz. Cevap açma ve bağlantılar JavaScript olmadan da çalışır.
- Rehberlerden ilgili ürün sayfalarına geri bağlantılar eklenir. Mevcut
  adresler korunur; aynı sorgu için yeni kopya sayfalar üretilmez.
- Değişen altı ürün sürümü ve on rehber sürümünün güncelleme tarihleri
  site haritasıyla eşleştirilir. Ürün sayfasındaki tarih, ilk HTML ve
  tarayıcı sonrasındaki Article verisinde aynı kalır.
- Üretim dışındaki adreslerde GA4 ve reklam dönüşüm ölçümü başlatılmaz.
  Kaynak kodda önceden alan adı kısıtı yoktu; bu, önizleme trafiğinin
  ölçüme karışabilmesine izin veriyordu. Geçmiş 88 oturumun sebebinin
  önizleme kullanımı olduğu **kanıtlanmış değildir**.
- Mevcut GitHub Actions akışında dil, tarih, örnek/FAQ ayrımı, dahili
  bağlantılar, mobil taşma ve üretim dışı ölçüm kontrolleri yapılır.
  Yerel derleme veya tarayıcı testi çalıştırılmaz.

### Rakip sayfalarından çıkan uygulama önceliği

23 Eylül 2026'da rakiplerin kendi ürün sayfaları incelendi. Aşağıdaki
gözlemler sayfa sunumuyla ilgilidir; bağımsız kalite, trafik, sıralama veya
fiyat üstünlüğü ölçümü değildir. Rakiplerin hız, kullanıcı sayısı ve başarı
iddiaları LectureSift iddiasına dönüştürülmez.

| Kaynak | Görülen yaklaşım | LectureSift'te uygulanan karşılık |
| --- | --- | --- |
| [Knowt PDF Summarizer](https://knowt.com/ai-pdf-summarizer) | PDF arama niyetine özel sayfa, görünür yükleme başlangıcı ve kullanım örnekleri. | PDF başlığı, dosya yükleme çağrısı ve kaynağı gösterilen çözümlü örnek. |
| [Mindgrasp Lecture Summarizer](https://www.mindgrasp.ai/ai-summarizer/lecture) | Kaynak yükleme, analiz ve sonuç aşamalarını anlatan ürün sayfası. | Video/ses iş akışı, transkript–özet ayrımı, zaman damgası ve kayıt kalitesi açıklaması. |
| [StudyFetch Quizzes](https://www.studyfetch.com/features/quizzes) | Kaynaktan soru üretme, cevaba geri bildirim ve açıklama üzerine kurulan sunum. | Kaynağı ve cevap gerekçesi görünür quiz örneği; bilgi kartı farkı ve aktif hatırlama rehberine bağlantı. |

Sonraki değerlendirme, bu altı adresin gösterim/tıklamalarını başlangıçla
karşılaştırır. Bu kaynak paketi sıralama veya trafik artışının kanıtı değildir;
yayın ve Google'ın yeniden taraması ayrıca izlenir.

## Ölçülmüş başlangıç

### Yayın sonrası erişim sorunu — 23 Eylül 2026

İkinci SEO paketi [#108](https://github.com/ulasfirinciogullari-design/lecturesift-backend/pull/108)
ile birleştirildi. İlk Netlify yayını 181 sayfayı oluşturdu ve gizli bilgi
taramasını geçti; dosyaları etkinleştiren `updateSiteDeploy` isteği HTTP 500
ile kesildi. Aynı kaynak ağacını kullanan `2c8843e` yeniden denemesi
[22:52:46 UTC'de yayımlandı](https://app.netlify.com/projects/clever-horse-22b1a8/deploys/6ab4582758c32c0008d375a6).
Bu sürümün GitHub Actions kontrolleri 1.397 uygulama ve 163 tarayıcı testi
geçti. Canlıda iki Cornell rehberi, iki şablon, ürün bağlantıları ve 181
adreslik site haritası doğrulandı. Türkçe/İngilizce dil dosyası 620.983
bayttan 106.505 bayta indi; bu yaklaşık %82,8 küçülmedir, PageSpeed puanı değildir.

Yayın başarısından ayrı olarak, kamuya açık dosyalarda aralıklı 500/503
yanıtları gözlendi. 23:18 UTC örneklemesinde `/favicon.svg` özel alan adında
200, Netlify alt alan adında 500; İngilizce dil dosyası özel alan adında 500
döndürdü. Hata yanıtları `Server: Netlify` ve sağlayıcı istek kimliği taşıyor.
Sorun hem HTTP/1.1 hem HTTP/2 isteklerinde görüldü. Tekrar edilen başarılı
istekler önceki hatayı ortadan kaldırmış sayılmaz.

- Dil dosyası hata kimliği: `01M388ZVSZWC3Y4KMNQN7WHC18`.
- Netlify alt alan adı favicon hata kimliği: `01M388ZVYK3FNBKGZ0FY1F40M6`.
- CSP nonce eklentisinin üretim kapsamı `/about`; `.js`, `.svg` ve `.txt`
  dosyaları ayrıca dışlanıyor ve `onError: bypass` kullanılıyor. Bu yüzden
  statik dosya hatalarını bu eklentiye bağlayan bir kanıt yok.
- Netlify durum sayfasında açık olay bulunmaması, bu alan adı ve bağlantı
  noktası için sorunsuz erişim kanıtı değildir. Sağlayıcının iç hata nedeni
  yalnız istemci yanıtlarından kesinleştirilemez.

`Frontend delivery` GitHub Actions kontrolü, ana sürüm güncellemelerinde
ve ilgili tanılama PR'larında mevcut canlı siteyi ayrı bir ağdan okur.
İki alan adında iki kısa tur yapar; sayfaları, site haritasını, şablonu ve
HTML'de bulunan dil dosyasını kontrol eder. İlk başarısız yanıt korunur;
sonraki başarıyla gizlenmez. Ham yanıt gövdeleri veya kimlik bilgileri
kaydedilmez. JSON kanıtı istek kimlikleri ve zamanlarla yedi gün saklanır.
Bu kontrol yeni sürümün yayımlandığının veya tüm kullanıcıların kesintisiz
eriştiğinin kanıtı değildir; gözlem anındaki canlı yayını raporlar.
Zamanlanmış yeni bir otomasyon veya sağlayıcı değişikliği yapılmaz.

### Yayın ve ikinci geliştirme

İlk paket [#107](https://github.com/ulasfirinciogullari-design/lecturesift-backend/pull/107)
ile ana sürüme alındı. 23 Eylül 22:06 UTC canlı kontrolünde altı ürün
sayfasındaki içerik, canonical, dil bağlantıları, FAQ ve tarihler doğrulandı.
GitHub Actions'ta 1.386 uygulama ve 151 tarayıcı testi geçti; ana sürümün
iki kontrolü de başarılıydı. Aşağıdaki ilk teknik denetim tarihsel kayıttır.

İkinci paket, bu denetimde bulunan bir ölçüm açığını kapatır: çalışma
rehberleri analytics izin listesinde değildi ve izin/ölçüm dosyalarını
yüklemiyordu. Rehberler artık küçük izin ve ölçüm dosyalarını doğrudan
yüklüyor; büyük uygulama ve çeviri katalogları eklenmiyor. Kullanıcı
istatistik veya reklam izni vermemişse ölçüm yapılandırması da istenmiyor.

`content_action` olayı, izin veren üretim ziyaretçilerinin bilinen bağlantı
tıklamalarını ayırır: `open_workspace`, `open_registration`, `view_plans`,
`read_related`, `download_resource`. İçerik kimliği/dili, hedefin sabit yolu
ve bağlantının yeri gönderilir. Bağlantı yazısı, kullanıcının dosya adı, sorgu ve parça
değerleri bu olayın parametrelerine alınmaz. Bu olayın `page_location`
alanı da sorgusuzdur. Genel sayfa görüntülemesinin mevcut kampanya atfı
korunur. Bunlar tıklama niyetleridir: tamamlanmış yükleme, kayıt, indirme
başarısı veya satış kanıtı değildir. Yeni reklam dönüşümü tanımlanmaz.

Temel rapor, bağlı aracın hazır `page_path`, `event_name`, `content_type`,
`content_id` ve `event_count` alanlarıyla okunabilir. Sabit tıklama türü
`content_type` alanına da yazılır; bu ayrım için özel boyut oluşturmak
gerekmez. `target_path` veya `link_placement` gibi ek parametreleri ayrı
raporlamak ise GA4'te özel boyut tanımı gerektirir; bu tanımlar oluşturulmuş
sayılmaz. [GA4 hazır alanları](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema)
ve [özel boyut açıklaması](https://support.google.com/analytics/answer/14240153)
bu ayrımı tanımlar. Olayların canlı veri akışına düşmesi, gerçek ve izin
vermiş ziyaretçi etkileşimlerinden sonra ayrıca doğrulanacaktır.

Herkese açık 13 dildeki sayfalarda tüm dillerin 620.983 baytlık çalışma
zamanı sözlüğü yerine Türkçe kaynak anahtarları, İngilizce yedek ve seçili
dili içeren dosya oluşturulur. Dosya adı içerik özeti taşıdığı için güncel
çeviriler eski önbelleğe takılmaz. Dil seçimi yine yeni adrese gider; özel
uygulama sayfaları mevcut tam sözlüğü kullanır. Bu değişiklik dosya
boyutunu azaltmayı hedefler; PageSpeed puanı veya saha hız kazanımı değildir.

Yeni içerik `/cornell-notes` ve `/en/cornell-notes`: Cornell'in birincil
açıklamasına bağlantı, özgün yüzde değişim dersi, doldurulmuş soru/not/özet,
cevabı açılan uygulama sorusu ve iki düzenlenebilir TXT şablonu içerir.
Kütüphane ile video sayfaları bu rehbere, rehber video ve belge sayfalarına
bağlanır. Yalnız yazılmış iki dil indekslenir; site haritası 181 URL olur.
“Cornell not tutma / Cornell notes template” ürün sayfalarının mevcut
özetleme niyetinden ayrı bir içerik konusudur; arama hacmi ölçülmemiştir.

### 23 Eylül ek veri doğrulaması

Windsor'ın Search Console site haritası tablosu `/sitemap.xml` için
son gönderimi 28 Ağustos 18:40 UTC, son okumayı 20 Eylül 11:20 UTC,
hata ve uyarıyı sıfır bildiriyor. `submitted=531` eski sağlayıcı kaydıdır;
canlı haritadaki 179 adres veya indeks sayısı yerine kullanılamaz.
Bu okuma yeni yayından öncedir. Bağlı aracın sunduğu alanlar içinde URL
Inspection yoktur; güncel indeks/kullanılan canonical bilgisi doğrulanmadı.

24 Ağustos–20 Eylül GA4 alan adı kırılımında yalnız `lecturesift.com`
döndü: Organic Search 88 oturum/4 aktif kullanıcı, Direct 32/6,
Unassigned 1/1. Bu sonuç geçmiş ölçüm farkının önizleme trafiğinden
kaynaklandığı varsayımını desteklemiyor. Kullanıcılar kanallar arasında
örtüşebileceği için aktif kullanıcı satırları toplanmaz; atıf ve iç kullanım
hâlâ açıklanmış değildir. Yeni yayın sonrası değişimi bu dönemden çıkaramayız.

Kaynak: [Cornell Üniversitesi not tutma yöntemi](https://lsc.cornell.edu/notes.html).

Search Console ve GA4 verileri 23 Eylül'de bağlı Windsor üzerinden okundu.
İstenen dönem **24 Ağustos–20 Eylül 2026**; Search Console günlük yanıtının
ilk satırı 27 Ağustos, son satırı 20 Eylül. Eksik günlere değer uydurulmadı;
henüz kesinleşmemiş veriler istenmedi.

| Ölçüm | Başlangıç | Yorum |
| --- | ---: | --- |
| Search Console gösterim | 68 | Günlük site toplamlarından hesaplandı. |
| Google arama tıklaması | 4 | Ana sayfa 3, Hakkımızda 1. |
| Tıklanma oranı | %5,88 | 4 / 68; günlük yüzdelerin ortalaması değil. |
| Canlı site haritasındaki URL | 179 | Yayınlanan adres sayısıdır; indekslenen sayfa sayısı değildir. |
| GA4 Organic Search oturumu | 88 | 4 aktif kullanıcı; Search Console tıklamasıyla eşdeğer değildir. |
| GA4 organik purchase olayı | 1 | Ölçüm olayıdır; doğrulanmış ödeme/gelir kanıtı değildir. |

Search Console sorgu dökümünde yalnız dört sorgu, toplam altı gösterim ve
sıfır tıklama döndü. Bu döküm site toplamını açıklamıyor; gizlenen sorgular
ve rapor toplama farkları nedeniyle buradan marka / marka dışı payı veya
anahtar kelime talebi çıkarılmamalı. Sayfa bazında gösterimler de site
toplamına eklenmemeli. Ürün sayfalarında bu dönemde tıklama yok.

GA4'te organik kaynak `google / organic`: 88 session_start, 300 page_view,
28 begin_checkout, 22 add_payment_info ve 1 purchase olayı döndü.
Search Console ile farkın nedeni henüz doğrulanmadı. Yeniden gelen oturumlar,
atıf, iç kullanım, rıza ve etiket davranışı araştırılmadan bu sayılar yeni
müşteri veya satış başarısı olarak sunulmaz. Güncel bağlantı yalnız veri
okuma erişimini doğrular; signed-in tarayıcı paneline erişim anlamına gelmez.

## Canlı teknik denetim

Sınırlı, salt okunur HTTP örneklemesi yapıldı; tüm site taraması yapılmadı.

- `/`, `/en/`, `/document-summary`, `/en/document-summary`, `/study-guides`
  ve `/en/active-recall` 200 yanıt verdi. Kontrol edilen sayfaların ana
  adres etiketi kendisini gösteriyor; HTML'de başlık ve açıklama hazır.
- `/document-summary.html` isteği temiz `/document-summary` adresine ulaştı.
  Kaynak yönlendirme kuralı 301; bu örneklemede ara yanıt kodu kaydedilmedi.
- `robots.txt` erişime açık ve doğru site haritasını gösteriyor.
- `/en/workspace.html` ve `/hi/workspace.html` açık `noindex,follow` taşıyor.
  Geçmiş performans raporundaki workspace gösterimleri bugünkü indeksleme
  durumunu kanıtlamıyor; güncel URL Inspection sonucu ayrıca gerekli.
- Deneme amaçlı var olmayan bir adres gerçek 404 döndürdü.
- Canlı İngilizce ürün ve rehber sayfalarının BreadcrumbList başlangıcı
  Türkçe ana sayfayı gösteriyor; rehberlerde görünür kütüphane basamağı
  yapılandırılmış veride eksik.

## İlk kaynak düzeltmesi

Bu değişiklik, bütün dillerde gezinme yolunun başlangıcını aynı dildeki ana
sayfaya bağlar. Rehber sayfaları kendi dillerindeki çalışma rehberleri
kütüphanesini ara basamak olarak bildirir. WebPage kaydı ilgili gezinme
yoluna bağlanır. Hem yayıma hazırlanan HTML hem JavaScript sonrasındaki
metadata için koruma kontrolleri eklenir.

Kaynak değişikliği, GitHub Actions sonucu, önizleme ve canlı yayın ayrı
aşamalardır. Yerel derleme veya test çalıştırılmaz. Bu dosyadaki canlı
denetim, değişiklik öncesindeki durumu kaydeder; düzeltmenin yayına
alındığını veya Google tarafından işlendiğini iddia etmez.

## Öncelik sırası

| Öncelik | İş | Tamamlanma ölçütü |
| --- | --- | --- |
| P0 | Ölçümün güvenilirliğini doğrula | GA4 iç kullanım/atıf/etiket kapsamı açıklanır; purchase olayı ödeme kaydıyla özel olarak karşılaştırılır; kişisel veriler rapora eklenmez. |
| P0 | Gezinme yolu düzeltmesini doğrula | Mevcut GitHub Actions SEO ve tarayıcı kontrolleri geçer; yayın sonrasında TR/EN ve bir başka dilde kamuya açık HTML tekrar okunur. |
| P1 | Ana ürün sayfalarını sorgu niyetine göre geliştir | Aşağıdaki üç sayfada gerçek örnek, anlaşılır kullanım adımları, sınırlar ve ilgili rehber bağlantısı bulunur; mevcut içerik önce gözden geçirilir. |
| P1 | Google'ın önemli sayfaları nasıl gördüğünü denetle | Ana sayfa ve üç ürün sayfası için güncel URL Inspection: indeks durumu, Google'ın seçtiği ana adres ve son tarama tarihi kaydedilir. |
| P1 | Mobil hız başlangıcını ölç | Ana sayfa ve bir ürün sayfasında uzak PageSpeed raporu alınır; saha verisi yoksa bu açıkça belirtilir. Yerel tarayıcı matrisi çalıştırılmaz. |
| P2 | İçerik kümelerini genişlet | Her yeni rehber özgün örnek, kontrol edilebilir cevaplar ve ilgili ürün/rehber bağlantıları içerir; yalnız gerçekten yazılmış dil sürümleri yayınlanır. |

### İlk içerik ve sorgu eşlemesi

Bunlar ürünle uyumlu **aday arama niyetleridir**; ölçülmüş arama hacmi
veya sıralama fırsatı olarak sunulmaz. Aynı niyete birden çok zayıf sayfa
açmak yerine mevcut ana sayfa güçlendirilir.

| Sayfa | Aday sorgular | İçerik geliştirme odağı |
| --- | --- | --- |
| `/document-summary` | PDF özetleme, PDF'den ders notu çıkarma | Kısa bir belgeden kaynakla karşılaştırılabilir özet örneği; taranmış PDF sınırları; not kontrol rehberine bağlantı. |
| `/lecture-video-summary` | ders videosu özetleme, ses kaydını yazıya çevirme | Transkript ve özet farkı; yüklenen kayıt üzerinden örnek; desteklenen iş akışı ve belirsiz terimleri kontrol etme. |
| `/quiz-flashcards` | PDF'den quiz oluşturma, ders notundan bilgi kartı | Aynı kaynak için bir soru, açıklamalı cevap ve kısa bilgi kartı; aktif hatırlama rehberine bağlantı. |
| `/study-guides` | ders çalışma yöntemleri, örnek ders notu | Mevcut örnek ders, not kontrolü ve aktif hatırlama rehberlerini birbirine ve uygun ürün sayfasına bağlama. |

Yeni özellik, işlem hızı, başarı oranı veya ücretsiz kullanım sınırı
uydurulmaz. Başlık ve açıklama gerçek içeriği anlatır; tarihler yalnız
anlamlı içerik değiştiğinde güncellenir.

## Takip düzeni

Her değerlendirmede aynı kapsam ve karşılaştırılabilir tamamlanmış 28 günlük
pencereler kullanılır. Türkiye / diğer ülkeler, Türkçe / İngilizce açılış
sayfaları ve mümkün olduğunda marka / marka dışı sorgular ayrı incelenir.
Gizlenen sorguların payı bilinmiyorsa marka dışı oran kesinmiş gibi yazılmaz.

Başlıca ölçümler: ürün sayfalarının gösterimleri ve tıklamaları, sorgu
kapsamı, önemli sayfaların indekslenmesi ve ölçümü doğrulanmış organik
kayıt/ürün kullanımı. Düşük hacimde birkaç gösterim veya tıklamayla kesin
başarı/başarısızlık hükmü verilmez. Sonraki veri inceleme hedefi 7 Ekim 2026;
bu bir çalışma planıdır, zamanlanmış otomasyon oluşturulmamıştır.

## Başvuru kaynakları

- [Google SEO başlangıç rehberi](https://developers.google.com/search/docs/fundamentals/seo-starter-guide)
- [Google gezinme yolu yapılandırılmış verisi](https://developers.google.com/search/docs/appearance/structured-data/breadcrumb)
- [Google site haritası ve lastmod yönergeleri](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)
- Önceki SEO / reklam gözlemleri: [SEO_AND_ADS_READINESS.md](SEO_AND_ADS_READINESS.md).
  O dosyanın geçmiş indeks sayıları güncel veri yerine kullanılmaz.
