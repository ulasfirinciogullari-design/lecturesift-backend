# LectureSift SEO çalışma planı

Başlangıç denetimi: 23 Eylül 2026. SEO çalışmaları bu proje üzerinden
takip edilir. Başlangıç varsayımı Türkiye'deki üniversite öğrencileri ve
Türkçe aramalardır. İlk rekabet geliştirmesi üç ana ürün sayfasının Türkçe
ve İngilizce sürümlerine uygulanır; diğer diller mevcut içeriklerini korur.

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
