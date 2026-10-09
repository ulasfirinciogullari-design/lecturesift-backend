# LectureSift SEO çalışma planı

Başlangıç denetimi: 23 Eylül 2026. SEO çalışmaları bu proje üzerinden
takip edilir. Başlangıç varsayımı Türkiye'deki üniversite öğrencileri ve
Türkçe aramalardır. İlk rekabet geliştirmesi üç ana ürün sayfasının Türkçe
ve İngilizce sürümlerine uygulanır; diğer diller mevcut içeriklerini korur.

## 9 Ekim 2026 üç öncelikli URL indekslendi; ölçüm tanımları tamamlandı

**03:49 TSİ takip yenilemesi:** Kabul edilmiş yedi isteğin hedefleri bir kez
yeniden denetlendi. Bu işlem yeni indeksleme isteği değildir. Üç URL artık
indeksli; aynı sonuçlar **03:52–03:54 TSİ** arasında Google'ın kendi indeks
kayıtlarında da doğrulandı:

| URL | Güncel indeks durumu | Google'ın seçtiği canonical |
| --- | --- | --- |
| `/quiz-flashcards` | İndeksli | `https://lecturesift.com/quiz-flashcards` |
| `/pt/contact` | İndeksli | `https://lecturesift.com/pt/contact` |
| `/en/quiz-flashcards` | İndeksli | `https://lecturesift.com/en/quiz-flashcards` |

Üç kayıtta kullanıcı canonical'ı da aynı temiz adres; Google'ın seçimi
“İncelenen URL”. Böylece Türkçe quiz ve Portekizce iletişim için eski
`.html` seçiminin güncellendiği doğrulandı. Üçünde de Google bir geçerli
BreadcrumbList öğesi gösteriyor. Eski denetim bağlantısı Türkçe quiz'in
Ağustos kaydını göstermeye devam ettiğinden güncel sonuç yeni URL Denetimi
başlatılarak alındı; eski raporlar güncel sonuç yerine kullanılmadı.

Kalan `/pt/quiz-flashcards` eski taramaya dayanan alternatif sayfa;
`/pdf-note-check` keşfedilmiş; `/en/lecture-video-summary` ve
`/en/pdf-note-check` son sağlayıcı yanıtında Google tarafından bilinmiyor.
Önceki keşfedildi/bilinmiyor kayıtları korunur;
bu dalgalanma kabul edilmiş istekleri geçersiz kılmaz veya yeni bir kaynak
kusurunu kanıtlamaz. Dört adrese tekrar istek gönderilmedi.

31 URL'lik takip grubunda artık **3 indeksli / 28 indeks dışı** kayıt var;
yalnız yedi öncelikli URL bu oturumda yenilendi. Kalan 24 kaydın zamanı
eski, bu sayılar tüm sitenin indeks sayısı değildir. Takip ve e-posta
özeti etkin. Sitemap `isPending=false`, 183 adres, 0 hata/0 uyarı;
son indirme `2026-10-08T22:17:48.588Z`. `contents[].indexed=0` değeri,
Google'ın [kullanımdan kaldırdığı alan](https://developers.google.com/webmaster-tools/v1/sitemaps)
olduğu için indeks sayısı olarak yorumlanmadı. Harita tekrar gönderilmedi.
[Takip sonuçları ve native canonical kanıtları](docs/seo/indexing-followup-2026-10-09.json).

**Ölçümde uygulanabilir eksik kapatıldı:** LectureSift GA4 mülkünde mevcut
`content_action` parametreleri için `target_path` ve `link_placement`
olay kapsamlı özel boyutları oluşturuldu. Google panelindeki kayıtlar,
ayrı GA4 Admin API okumasıyla doğrulandı. İlki yalnız izinli sabit hedef
yollarını, ikincisi `header/footer/content` değerlerini raporlar. Yeni olay,
kişisel veri veya izleme kodu eklenmedi; izin/dönüşüm ayarları değiştirilmedi.
Geçmiş veri geriye dönük dolmaz; [Google'ın belirttiği 24–48 saatlik](https://support.google.com/analytics/answer/14240153)
raporlama süresi ve gerçek ziyaretçi etkileşimleri beklenir. İç kullanım,
atıf, gözlenmeyen dört eylem türü ve ödeme karşılaştırması hâlâ açık.
[Tanımlar ve doğrulama kaydı](docs/seo/ga4-content-dimensions-2026-10-09.json).

Canonical, dil alternatifleri, rehber keşfi ve beş olay türünün kaynak
bağlantıları ayrıca incelendi; kesin bir yeni kaynak kusuru saptanmadı.
Yerel test/derleme veya sentetik Analytics olayı çalıştırılmadı. Göreve ait
Chrome sekmesi kapatıldı; diğer sohbetin sekmesi korundu. Aşağıdaki
bekleme/kabul sayıları önceki oturumların tarihsel kayıtlarıdır.

## 9 Ekim 2026 PDF rehberlerinin indeksleme istekleri tamamlandı

Kullanıcının Google doğrulamasını tamamladığını bildirmesinin ardından,
**03:33 TSİ'de** Türkçe `/pdf-note-check` kaydında “Dizine eklenmesi
istendi” onayı ve “TEKRAR İSTEK GÖNDER” düğmesi görüldü. Bu manuel isteği
kullanıcı tamamladı; ajan sonucu doğruladı ve tekrar göndermedi.

Kalan `/en/pdf-note-check` için aynı Chrome oturumunda ilk kez tek istek
gönderildi. Google uygunluk kontrolünden sonra **03:35 TSİ'de** “Dizine
eklenmesi istendi” onayı ve öncelikli tarama sırasına eklenme mesajı
gösterdi. Böylece önceki beş URL ile birlikte **yedi öncelikli URL'nin
tamamı için kabul edilmiş indeksleme isteği** var. PDF rehberlerinin
istek gönderme işi kapandı; CAPTCHA otomatik çözülmedi veya aşılmadı.

İngilizce rehberin güncel indeks kaydı artık “Keşfedildi - şu anda dizine
eklenmiş değil” ve sitemap kaydı mevcut. Türkçe rehber onay anında hâlâ
Google tarafından bilinmiyor. İkisinde de son tarama ve canonical alanları
`Yok`. İsteklerin kabulü indekslenme, tarama veya canonical seçiminin
tamamlandığı anlamına gelmez; Google'ın işlemesi bekleniyor.

[İsteklerin göndericileri, zamanları ve kabul kanıtları](docs/seo/pdf-indexing-requests-2026-10-09.json)
ayrı kaydedildi. Aşağıdaki beş istek/sıfır yeni istek/CAPTCHA engeli
bölümleri önceki oturumların tarihsel kayıtlarıdır; güncel toplam yedidir.
Yerel test veya derleme çalıştırılmadı.

## 9 Ekim 2026 TinyFish olmadan Chrome erişimi doğrulandı

Kullanıcının isteğiyle mevcut Windows Chrome/SSH köprüsü bu sohbetten
kullanıldı. Yeni hizmet, Google oturum aktarımı veya köprü yapılandırma
değişikliği gerekmedi. Diğer sohbetin sayfası korunarak bu göreve ait ayrı
sekmede `sc-domain:lecturesift.com` paneli ve gerçek URL Denetimi kayıtları
okundu. Böylece canonical denetimi TinyFish'e bağlı kalmıyor. Erişim,
kullanıcının Chrome ve SSH bağlantısı açık olduğu sürece kullanılabilir;
ambient sekme bilgisi erişim kanıtı olarak alınmadı.

| URL | Google indeks kaydı | Google'ın seçtiği canonical | Ham son tarama |
| --- | --- | --- | --- |
| `/` | İndeksli | `https://lecturesift.com/` | `1 Eki 2026 21:09:40` |
| `/document-summary` | İndeksli | `https://lecturesift.com/document-summary` | `5 Eki 2026 15:38:26` |
| `/lecture-video-summary` | İndeksli | `https://lecturesift.com/lecture-video-summary` | `1 Eki 2026 12:19:39` |
| `/pdf-note-check` | Google tarafından bilinmiyor | `Yok` | `Yok` |
| `/en/pdf-note-check` | Google tarafından bilinmiyor | `Yok` | `Yok` |

İndeksli üç kayıtta Google'ın alanı “İncelenen URL”; hedef adres buradan
çözümlendi. Üçünün kullanıcı canonical'ı da aynı temiz URL. Tarama izni ve
indeksleme izni `Evet`, sayfa getirme `Başarılı`. Zaman dilimi görünmediği
için son tarama saatleri dönüştürülmedi. Önceden okunan Türkçe quiz kaydıyla
birlikte ana sayfa/üç ürün için native Google alanlarının denetimi tamamlandı.

İki PDF rehberinde eksik canonical aracı uygulamadan kaynaklanmıyor:
Google'ın kendi indeks kaydı da `Yok` gösteriyor. Bu gözlem, önceki Türkçe
PDF canlı testinin indeks kaydı olarak okunamadığı belirsizliğini kapatır.
CAPTCHA çıkan indeksleme isteği Chrome'da tekrar gönderilmedi; bu oturumda
yeni indeksleme isteği sayısı sıfır. Kabul edilmiş toplam sayı hâlâ beş.

[Beş native Google kaydı](docs/seo/chrome-google-inspection-2026-10-09.json)
ve [tekrar kullanılabilir erişim yöntemi](docs/seo/direct-google-inspection.md)
kaydedildi. Göreve ait sekme iş sonunda kapatıldı; diğer sohbetin sekmesi
korundu. Yerel test veya derleme çalıştırılmadı.

**03:23 TSİ yayın sonrası mobil ölçüm:** Aynı Chrome bağlantısından Google'ın
uzak PageSpeed raporu alındı: [ana sayfa 99 puan](https://pagespeed.web.dev/analysis/https-lecturesift-com/rlitij1087?form_factor=mobile),
FCP 1,3 sn, LCP 1,6 sn, TBT 10 ms, CLS 0, Speed Index 2,7 sn. SEO,
erişilebilirlik ve iyi uygulamalar 100. Saha verisi yine yok. Bu tekil
laboratuvar örneği; önceki puanla farkın tamamı tek görsel değişikliğine
bağlanmaz ve saha Core Web Vitals sonucu sayılmaz. [Ölçüm kaydı](docs/seo/mobile-performance-2026-10-09.json)
önceki raporları ve yayın sonrasındaki raporu ayrı tutar. PDF ürünü için
ikinci ölçüm yapılmadı. Yeni TinyFish çalışması veya yerel Lighthouse yok.

## 9 Ekim 2026 kalan işler ve güncel durum

Öncelikli dört indeks dışı adresin canlı yanıtı ve keşif yolları yeniden
incelendi: `/en/quiz-flashcards`, `/en/lecture-video-summary`,
`/pdf-note-check`, `/en/pdf-note-check`. Dördü de 200, tek self-canonical
ve noindex olmadan yanıt veriyor; 183 adreslik site haritasında ve ilgili
dilde dahili bağlantılarda bulunuyor. Yeni bir kaynak kusuru saptanmadı.
[Canlı yanıtlar ve bağlantı kanıtı](docs/seo/priority-discovery-2026-10-09.json).

Google'ın kendi ekranında İngilizce quiz ve video sayfaları **keşfedilmiş,
henüz indekslenmemiş** görünüyor. Son tarama ve iki canonical alanı `Yok`;
bu değerler canlı HTML ile doldurulmadı. İki URL için birer indeksleme
isteği kabul edildi. Önceki üç canonical hedefiyle birlikte **toplam beş
kabul edilmiş istek** var; Google'ın yeniden taraması veya indekslemesi
tamamlanmış sayılmıyor.

Türkçe PDF not kontrol isteğinde Google reCAPTCHA gösterdi. İşlem burada
durduruldu; İngilizce PDF rehberi için yeni istek başlatılmadı. Türkçe
sayfanın son ekranı canlı testte erişilebilir/indekslenebilir olduğunu
gösteriyor; bu ekran indekslenme, son Google taraması veya Google'ın seçtiği
canonical kanıtı değildir. Tarayıcı aracının bunları indeks sonucu gibi
yorumlayan özeti kabul edilmedi. [Alan bazında kayıt ve düzeltme](docs/seo/priority-indexing-2026-10-09.json).
CAPTCHA aşılmadı; hesap değiştirme veya tekrar gönderim yapılmadı.

**Gezinme yolu doğrulaması kapatıldı:** 03:01 TSİ'de üç canlı HTML okuması
TR kütüphane, EN Cornell rehberi ve AR belge ürününde doğru dilde ana
sayfa/ara basamak zincirlerini ve `WebPage.breadcrumb` bağlantılarını
doğruladı. Kaynak düzeltmesi ve önceki uzak CI kanıtına bu canlı sonuçlar
eklendi. [Tam JSON-LD zincirleri](docs/seo/breadcrumb-live-verification-2026-10-09.json).
Google zengin sonuç uygunluğu bu okumayla doğrulanmış sayılmaz.

**Ölçüm kısmen doğrulandı:** 8 Eylül–5 Ekim arasındaki 28 günde üretim
alan adında 5 `content_action` olayı ve 2 kullanıcı görüldü; tamamı
`open_workspace` (`/` üzerinden 4, `/en/` üzerinden 1). Diğer dört tür
(`open_registration`, `view_plans`, `read_related`, `download_resource`)
henüz gözlenmedi; yoklukları arıza kanıtı değildir. GSC 3 tıklama/133
gösterim, GA4 `google / organic` 67 oturum/4 aktif kullanıcı bildirdi.
Metrik ve rapor saat dilimi farkları nedeniyle bu sayılar eşdeğer değildir.
İç kullanım, atıf ve gerçek işlem karşılaştırması açık kalır. [Toplu ölçüm
kaydı ve sınırları](docs/seo/measurement-review-2026-10-09.json).
Canlı veriye yapay olay gönderilmedi; hesap ayarları değiştirilmedi.

**Mobil hız başlangıcı alındı:** PageSpeed API'sinin 429 kotası ve GSC
Wizard'ın eksik CrUX yapılandırması ayrı kaydedildi. Google'ın resmî web
formu üzerinden, sunucuda çalışan Lighthouse 13.5/Moto G Power/Slow 4G
raporları alındı:

| Sayfa | Performans | FCP | LCP | TBT | CLS |
| --- | --- | --- | --- | --- | --- |
| [Ana sayfa raporu](https://pagespeed.web.dev/analysis/https-lecturesift-com/bvz6reajb5?form_factor=mobile) | 71 | 1,6 sn | 8,5 sn | 110 ms | 0 |
| [PDF ürün raporu](https://pagespeed.web.dev/analysis/https-lecturesift-com-document-summary/hesyg6ouky?form_factor=mobile) | 98 | 1,5 sn | 2,3 sn | 50 ms | 0 |

İki raporda erişilebilirlik, iyi uygulamalar ve SEO 100. Bunlar tekil
laboratuvar sonuçları; saha Core Web Vitals sonucu değil. CrUX iki URL
için veri sunmadı; ana sayfa raporu origin düzeyinde de veri olmadığını
gösterdi. [Ölçüm kaydı ve sınırları](docs/seo/mobile-performance-2026-10-09.json).

Ana sayfanın raporu 1.730.450 baytlık çalışma masası PNG'sini işaret etti.
Ekranda 400–480 px gösterilen bu görsel için responsive WebP düzeltmesi
[PR121](https://github.com/ulasfirinciogullari-design/lecturesift-backend/pull/121)
ile aynı çalışma sırasında ana dala alındı. Mevcut Netlify Image CDN
400/480/800/960 px kaynakları sunuyor; PNG yedeği, oran, lazy yükleme ve
dekoratif boş alt metni korunuyor. Bu uygulama korunur; aynı görsel için
ikinci bir teslim yöntemi eklenmez. PR121'in kaynak commit'i `ece07d8`
[uzak CI'da](https://github.com/ulasfirinciogullari-design/lecturesift-backend/actions/runs/37862556517)
1.530 uygulama ve 239 tarayıcı kontrolünü geçti (3/5 atlandı).
03:12 TSİ'de TR/EN ana sayfanın canlı HTML'inde yeni WebP kaynakları ve
tek `assistant.css?v=4` bağlantısı görüldü. Gerçek 960 px/q80 görsel
uç noktası 200 `image/webp`, 29.104 bayt döndürdü; kaynak PNG'den yaklaşık
%98,3 küçük. Böylece görsel teslim düzeltmesinin canlı olduğu doğrulandı.
Üstteki iki hız raporu bu değişikliğin öncesini ölçer; 03:23 TSİ yayın
sonrası ölçüm yukarıdaki bölümde ayrı kaydedilmiştir. Sıralama artışı
iddia edilmez. PR121'in ayrı laboratuvar raporları farklı
puanlar kaydetti; tekil ölçümlerdeki fark bir iyileşme oranı olarak
kullanılmaz. Tema betiği ilk boyamadaki tema seçimini yaptığı için rastgele
ertelenmedi; kalan CSS/JS önerileri bu değişiklikle çözülmüş sayılmaz.

31 URL'lik GSC Wizard takibi etkin. Saklanan son toplu kontrol
9 Ekim 01:46 TSİ'den; bu eski sayılar yeni isteklerin sonucu gibi
sunulmaz. Yerel test veya derleme çalıştırılmadı.

## 9 Ekim 2026 Google'ın seçtiği canonical hedefleri doğrulandı

Kullanıcının TinyFish profilinde Google oturumunu kaydetmesinin ardından,
**02:40 TSİ sonrasında** `sc-domain:lecturesift.com` mülküne fiilen erişildi.
GSC Wizard ve Windsor'ın sunmadığı canonical alanları, Google Search
Console'un kendi URL Denetimi ekranından okundu. Bu yöntemle canonical
bilgisine erişim engeli giderildi; aracı sağlayıcıların çıktı şeması
değiştirilmiş veya Google'ın indeks durumu düzelmiş sayılmıyor.

| Denetlenen temiz adres | Google'ın seçtiği canonical |
| --- | --- |
| `/quiz-flashcards` | `https://lecturesift.com/quiz-flashcards.html` |
| `/pt/quiz-flashcards` | `https://lecturesift.com/pt/quiz-flashcards.html` |
| `/pt/contact` | `https://lecturesift.com/pt/contact.html` |

Google üç kayıtta da seçimin beyan edilen canonical ile aynı olduğunu
gösteriyor; `.html` adresleri aynı Google indeks kaydındaki kullanıcı
canonical alanından okundu. Bunlar bugünkü canlı HTML'den tahmin edilmedi.
Üçü de uygun canonical etiketli alternatif sayfa nedeniyle indeks dışında.
Tarama kayıtları 29–30 Ağustos'tan; 7 Eylül'deki temiz URL/301 düzeltmesinden
önce. Tarayıcı ekranının saat dilimi görünmediğinden saatler UTC veya TSİ'ye
çevrilmeden kanıt dosyasında ham gösterimleriyle korundu.

**02:41 TSİ canlı kontrolü:** Üç temiz adres 200, self-canonical ve noindex
olmadan yanıt veriyor; üç eski `.html` adresi temiz karşılıklarına 301
yönleniyor. Yanlış yönde yeni bir yönlendirme saptanmadı. Uygulama kodunda
bu nedenle ek canonical değişikliği yapılmadı.

Üç temiz adres için Google'ın kendi ekranından birer indeksleme isteği
gönderildi ve üçünün de “Dizine eklenmesi istendi” onayı alındı.
Kota, CAPTCHA veya uygunluk reddi görülmedi. Kabul, URL'nin öncelikli tarama
kuyruğuna alındığını gösterir; yeniden tarama veya indekslenme tamamlandı
anlamına gelmez. Tekrar gönderim yapılmadı.

[Google ekranı gözlemleri ve canlı kanıtlar](docs/seo/google-selected-canonicals-2026-10-09.json)
alanları, canonical çözümleme dayanağını ve istek sonuçlarını ayrı tutar.
Bu kayıtlar ham Google API JSON'u değildir; API içe aktarıcısına verilmedi.

Site haritası canlıda 200 ve geçerli XML; 183 benzersiz URL ile üç hedefi
içeriyor. Search Console sitemap kaydı 0 hata/0 uyarı, `isPending=true`;
önceki gönderim tekrar edilmedi. URL Denetimi'ndeki “Geçici işleme hatası”
etiketi, Google'ın [açıklamasına](https://support.google.com/webmasters/answer/9012289?hl=en)
göre raporlama sisteminin sitemap bilgisini getirmesiyle ilgilidir;
canlı sitemap dosyasının bozuk olduğunun kanıtı değildir.

Yerel test veya derleme çalıştırılmadı. Aşağıdaki erişim engeli kayıtları,
Google oturumu kaydedilmeden önceki tarihsel durumu anlatır; bu bölüm
onların canonical hedeflerinin bilinmediğine ilişkin sonucunu günceller.

## 9 Ekim 2026 sağlayıcıdan bağımsız Google okuma yolu

**Doğrudan tarayıcı denemesi:** Kullanıcının ek manuel adım olmadan devam
talebi üzerine TinyFish'in varsayılan mevcut profilinde tek, sınırlı ve
salt okunur deneme yapıldı. Hedef `sc-domain:lecturesift.com` mülkünde
`https://lecturesift.com/quiz-flashcards` denetimiydi. Tarayıcı Google'ın
`accounts.google.com/v3/signin/identifier` giriş ekranına yönlendirildi;
e-posta/telefon girişi isteniyordu. Denetim paneline girilmedi ve canonical
değeri alınmadı. [Tamamlanan deneme](https://agent.tinyfish.ai/runs/a8fddba5-259c-434e-b4e9-49e5efa7debb)
3 adım ve 11 saniye sürdü. Yeni giriş, parola/kasa kullanımı, OAuth onayı,
çerez aktarımı veya hesap değişikliği yapılmadı. Açık Codex sekmesi bu
denemede kontrol edilmedi.

PR #117'nin birleşme sonrası [Actions çalışması](https://github.com/ulasfirinciogullari-design/lecturesift-backend/actions/runs/37857587930)
da başarılı tamamlandı: 1.456 Python testi (3 atlandı), 223 tarayıcı
senaryosu (5 atlandı); uzak derlemede 183 indekslenebilir sayfa.
Bu sonuç Google erişimi veya canonical doğrulaması sayılmıyor.

**02:10 TSİ ek erişim araştırması:** Yerel özellik listesinde
`browser_use`, `browser_use_external`, `browser_use_full_cdp_access`,
`computer_use` ve `in_app_browser` zaten açık. Buna karşın mevcut sohbetin
araç kataloğunda yerel tarayıcı okuma/tıklama aracı yok. Bu nedenle
"Browser kapalı" teşhisi konmadı ve kullanıcıdan tekrar etkinleştirme
istenmedi. [Resmî Browser belgesinde](https://learn.chatgpt.com/docs/browser)
eksik araçları bu sohbete kullanıcı işlemi olmadan bağlayan desteklenmiş
bir yöntem doğrulanamadı.

İzlenen proje kaynaklarında hazır Search Console yetkilendirmesi,
URL Inspection çağrısı veya zamanlanmış ham GSC dışa aktarımı bulunmadı.
Mevcut Ads/AdSense entegrasyonları Search Console izni sağlamıyor.
[GSC Wizard'ın kamuya açık deposu](https://github.com/jbobbink/seo-gsc-wizard)
ise istemci/skill paketi; Google yanıtını işleyen sunucu kodunu içermiyor.
Bu depoyu değiştirerek uzak canonical çıktısını düzeltmek mümkün görünmüyor.
Gizli kimlik bilgisi veya tarayıcı oturumu çıkarılmadı; yeni yetkilendirme,
yerel test/derleme veya sonuç vermeyen yeni uygulama değişikliği yapılmadı.
Google'ın gerçek canonical hedefleri hâlâ doğrulanmamış durumda.

GSC Wizard desteğini beklemeden Google'ın kendi URL Denetimi API'sini
kullanmak için [native Google onayı ve JSON aktarım yolu](docs/seo/direct-google-inspection.md)
hazırlandı. Kullanıcı Google izin adımını yapabileceğini bildirdi;
bu, iznin verildiği veya gerçek API sonucunun alındığı anlamına gelmez.
Google'ın `webmasters.readonly` onayı resmi APIs Explorer ekranında verilir.
Bu onay ajanın mevcut bağlantısına otomatik erişim eklemez; kullanıcıdan
yalnız API'nin JSON yanıt gövdesi alınır, token/çerez/parola alınmaz.

Öncelikli üç alternatif adres ve indeksli ana sayfa için istek gövdeleri
hazırlandı. Yerel JSON yardımcı programı Google'ın canonical alanlarını
ve alanın mevcut olup olmadığını koruyacak şekilde eklendi. Bu program
Google'a bağlanmaz ve GSC Wizard özetinden canonical tahmin etmez.
Gerçek Google yanıtı gelene kadar canonical hedefleri doğrulanmamış kalır.
Kaynak incelemesi tamamlandı; yerel test/derleme çalıştırılmadı. Yardımcı
programın testleri mevcut GitHub Actions akışında doğrulanacak.

Kullanıcının tarayıcıdan devam talebi üzerine araç erişimi yeniden kontrol
edildi: açık Codex sekmesini okuyup tıklayacak araç yok. Ayrı TinyFish
tarayıcı araçları mevcut, ancak profil listesindeki hiçbir profilde Google
oturumu kayıtlı değil. Ayrı giriş yolu kullanıcıya sunuldu; oturum açıldığı
veya Search Console'a erişildiği henüz doğrulanmadı.

Kullanıcı sonrasında ek bir manuel adım yapmadan devam edilmesini istedi;
yeni giriş veya hesap bağlantısı başlatılmadı. Mevcut Windsor.ai bağlantısı
ayrıca okundu: `searchconsole` altında `sc-domain:lecturesift.com` bağlı,
ancak `get_fields`/hesaba özel `get_options` URL Denetimi veya canonical
alanı sunmuyor; `list_actions` sonucu da boş. Bu nedenle Windsor üzerinden
de gerçek Google canonical yanıtı alınamadı.

Yardımcı program ve belgeler [PR #117](https://github.com/ulasfirinciogullari-design/lecturesift-backend/pull/117)
ile yayımlandı. Uzak [Actions çalışması](https://github.com/ulasfirinciogullari-design/lecturesift-backend/actions/runs/37857102265)
**02:06 TSİ’de başarılı tamamlandı**: 1.456 Python testi (3 atlandı),
223 tarayıcı senaryosu (5 atlandı) geçti. PR #117 ana dala birleştirildi:
`cd9d5fce4017ae44ceda8d4b5b7d68ffad91afd3`. Bu yardımcı program ve belge
değişikliğidir; canlı Google erişimi veya indeksleme düzeltmesi değildir.
Gerçek Google yanıtı alınmadı ve hiçbir canonical hedefi doğrulanmış olarak
işaretlenmedi. Destek e-posta konusu tekrar okundu; henüz yalnız gönderilen
mesaj var. Yerel test/derleme çalıştırılmadı.

## 9 Ekim 2026 Google canonical alanı teşhisi

Kullanıcının eksik alanı çözme talebi üzerine `/quiz-flashcards` ve
indeksli ana sayfa kontrol örneği yeniden okundu. Ana sayfa `PASS` ve
`Submitted and indexed` döndürdüğü halde, iki örneğin hem metin hem
yapılandırılmış yanıtında `googleCanonical` ve `userCanonical` yok.
GSC Wizard'ın araç çıktı şeması da bu alanları sunmuyor. Bu gözlem,
Google'ın ham yanıtının boş olduğunu kanıtlamaz; ham yanıt erişilebilir
değil. Google'ın seçimi canlı HTML'den tahmin edilmedi.

Sağlayıcının resmî belgesine göre MCP ve REST aynı araç hattını kullanır;
belgelenmiş bir raw/full seçeneği bulunmadı. Google'ın resmî yanıt şeması
iki canonical alanını destekler, ancak indeks dışı bir sayfada Google'ın
`googleCanonical` alanını vermemesi de mümkündür. Çözüm, alanı sağlayıcı
yanıtında korumak ve Google'ın döndürmemesi ile aracın sunmamasını ayırmaktır.

[İki örnekli teşhis ve destek isteği](docs/seo/canonical-field-diagnostic-2026-10-09.json)
hazırlandı. Kullanıcının açık gönderim talimatıyla destek mesajı
**9 Ekim 01:54 TSİ'de support@gscwizard.com adresine gönderildi**;
Gmail yanıtındaki `SENT` durumu doğrulandı. Sağlayıcının yanıtı bekleniyor.
Sorun çözülmüş olarak işaretlenmedi; yeni bir yerel test/derleme çalıştırılmadı.

## 9 Ekim 2026 GSC URL takibi

**01:41 TSİ:** Tam site haritası denetiminde indeks dışında bulunan
**31 URL**, GSC Wizard'ın mevcut URL takip özelliğine alındı. Kayıtlar
`isActive=true`; sağlayıcının varsayılanı olan `emailDigestEnabled=true`
değeri korunuyor. Düzenli kontrol, GSC Wizard uygulamasının mevcut cron
özelliği üzerinden yürütülür; yeni bir Codex zamanlanmış otomasyonu
oluşturulmadı. Bu ayarlar, e-posta özetinin gönderildiğini kanıtlamaz;
alıcı ve gönderim zamanı mevcut araçta görünmüyor.

**01:46 TSİ:** 31 adresin başlangıç denetimi tamamlandı; bekleyen veya
hatalı kayıt yok. Bu takip kümesinin tamamı hâlâ indeks dışında:
**18 Google tarafından bilinmiyor, 10 keşfedilmiş fakat indekslenmemiş,
3 uygun canonical etiketli alternatif**. Bu sayılar ilk 183 URL'lik
denetimin tarihsel 152/31 sonucunu değiştirmez; yalnız seçili 31 adresin
sonraki görüntüsüdür. Bazı neden etiketleri değişmiş, indekslenen yeni
adres görülmemiştir. [Takip başlangıç kanıtı](docs/seo/indexing-tracker-2026-10-09.json)
ayarları, her URL'nin son kontrolünü ve durumunu içerir. Sağlayıcının
bildirdiği günlük kota sayacı 231/2.000; bu sayı sadece bu 31 çağrıya
atfedilmez.

Bu takip Google'a yeniden indeksleme veya yeniden tarama isteği göndermez.
Sonraki sonuçlar GSC Wizard takip geçmişinde tutulur. Açık takip konuları,
site haritasının yeniden okunması ve üç alternatif URL'nin yeniden taranması,
Google'ın canonical seçiminin temiz adreslere geçmesi ve indeks durumudur.
Mevcut `.html` canonical hedefleri yukarıdaki doğrudan Google denetiminde
doğrulandı.

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
Canonical, robots, sayfa gövdesi ve site haritası tarihleri değiştirilmedi.

Kaynak değişikliği `df39541` ana sürümünden ayrı
`codex/seo-metadata-descriptions` dalında hazırlandı ve
[PR #115](https://github.com/ulasfirinciogullari-design/lecturesift-backend/pull/115)
açıldı. PR sürümü `0a4a98a`; GitHub Actions
[37852750562](https://github.com/ulasfirinciogullari-design/lecturesift-backend/actions/runs/37852750562)
01:26 TSİ'de başarıyla tamamlandı: **1.434 Python testi geçti, 3 atlandı;
223 tarayıcı senaryosu geçti, 5 atlandı**. Uzak derleme 183 indekslenebilir
sayfa üretti. Netlify önizleme yayını başarılı; bu görevden önizleme
HTML'ini okuma denemesi 401 verdiği için içeriği ayrıca doğrulanamadı.
Kullanıcının tamamını uygulama talebiyle PR **01:32 TSİ'de ana dala
birleştirildi**: `b80df35072b53c2b5c0756932f778086cbdb1a2a`.
Yayın sonrasında **26 canlı HTML** doğrudan okundu: yeni description,
Open Graph ve Twitter açıklamalarının tamamı 13 dildeki kaynakla birebir
eşleşiyor; tümü 200 ve self-canonical. GSC Wizard'ın uzak canlı denetimi
de bu 26 sayfayı indekslenebilir ve noindex olmadan doğruladı. Türkçe ve
Portekizce quiz sayfaları ayrıca 200/self-canonical/indekslenebilir;
üç eski `.html` adresi temiz karşılıklarına 301 veriyor.
[Canlı yayın kanıtı](docs/seo/live-deployment-2026-10-09.json) kaydedildi.

Ana dalın [Frontend delivery kontrolü](https://github.com/ulasfirinciogullari-design/lecturesift-backend/actions/runs/37854110748)
ve [site yedekleme işi](https://github.com/ulasfirinciogullari-design/lecturesift-backend/actions/runs/37854110595)
başarılı. Birleşme sonrası [Actions testleri](https://github.com/ulasfirinciogullari-design/lecturesift-backend/actions/runs/37854110632)
de **01:37 TSİ'de başarıyla tamamlandı**; hem `pytest` hem
`browser-smoke` başarılı. Kaynak değişikliği, uzak CI ve canlı etkinleşme
ayrı ayrı doğrulandı. Yerel test/derleme çalıştırılmadı.

Google'ın 01:32 TSİ'deki sitemap kaydı hâlâ `isPending=true`, 0 hata ve
0 uyarı; aynı harita yeniden gönderilmedi. Mevcut araçlarda Google'a
bireysel yeniden indeksleme isteği gönderen veya Google-selected canonical
alanını açan bir eylem yok. Site tarafındaki yayının tamamlanması,
Google'ın yeniden taramayı veya indekslemeyi tamamladığı anlamına gelmez.
Aşağıdaki denetim sonuçları bu metin değişikliğinden önceki gözlemlerdir.

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

Bu ilk incelemenin sonraki kontrolü, yeniden gönderilen site haritasının
okunma tarihini ve üç ürün sayfasının son tarama/indeks durumunu tekrar
karşılaştırmak, Türkçe quiz için Google'ın seçtiği canonical adresi
tamamlamaktı. O aşamada yeni zamanlanmış otomasyon oluşturulmadı.
Güncel takip kapsamı, yukarıdaki [31 URL'lik GSC URL takibi](#9-ekim-2026-gsc-url-takibi)
bölümünde tüm indeks dışı harita adreslerine genişletildi.

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

## Öncelik sırası — 9 Ekim 2026 değerlendirmesi

| Öncelik | İş | Durum ve kalan ölçüt |
| --- | --- | --- |
| P0 | Ölçümün güvenilirliğini doğrula | Üretimde `content_action/open_workspace` alınıyor. `target_path` ve `link_placement` özel boyutları oluşturulup UI/API ile doğrulandı; yeni verinin raporlanması bekleniyor. Diğer dört tür, iç kullanım/atıf ve gerçek ödeme karşılaştırması açık; yapay olay veya müşteri kaydı kullanılmadı. |
| P0 | Gezinme yolu düzeltmesini doğrula | Tamamlandı: kaynak/önceki uzak CI ve 9 Ekim TR/EN/AR canlı JSON-LD zincirleri doğrulandı. |
| P1 | İlk üç ürün sayfasını geliştir | İlk paket tamamlandı: altı TR/EN sürümü PR107 ile yayımlandı; örnekler, adımlar, sınırlar ve ilgili rehberler canlı doğrulandı. Yeni gelişim ölçülmüş sorgulara göre seçilecek. |
| P1 | Google URL Denetimi ve canonical | 183 URL'nin ilk indeks denetimi ve yedi öncelikli istek tamam. Son kontrolde TR/EN quiz ile PT iletişim indeksli ve temiz canonical; native Google UI ile doğrulandı. Dört öncelikli adres bekliyor. Sitemap işlendi, 0 hata/uyarı. 31 URL'lik takipte 3 indeksli/28 indeks dışı; yalnız yedi kayıt yeni. Tüm 183 adresin canonical alanı alınmış değildir. |
| P1 | Mobil hız başlangıcını ölç | Tamamlandı: başlangıçta ana sayfa 71, PDF ürünü 98; 03:23 TSİ yayın sonrası tekil ana sayfa ölçümü 99/LCP 1,6 sn. Saha verisi yok. PR121'in responsive görsel düzeltmesi uzak CI'da ve 03:12 TSİ canlı TR/EN HTML/görsel yanıtında doğrulandı. |
| P2 | İlk rehber kümesini genişlet | İlk paket tamamlandı: Cornell, aktif hatırlama ve PDF not kontrol rehberleri/ürün bağlantıları yayımlandı. Yeni konu seçimi sorgu verisiyle yapılacak; yalnız gerçekten yazılmış dil sürümleri yayımlanacak. |

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
başarı/başarısızlık hükmü verilmez. 7 Ekim hedefinin veri incelemesi
9 Ekim'de, tamamlanmış 8 Eylül–5 Ekim penceresiyle yapıldı. Sonraki tam
28 günlük karşılaştırma penceresi 6 Ekim–2 Kasım; veri kesinleştikten sonra
okunmalı. İndeks durumu mevcut 31 URL'lik GSC Wizard takibinden izlenir;
bu belge yeni bir zamanlanmış Codex otomasyonu oluşturmaz.

## Başvuru kaynakları

- [Google SEO başlangıç rehberi](https://developers.google.com/search/docs/fundamentals/seo-starter-guide)
- [Google gezinme yolu yapılandırılmış verisi](https://developers.google.com/search/docs/appearance/structured-data/breadcrumb)
- [Google site haritası ve lastmod yönergeleri](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)
- Önceki SEO / reklam gözlemleri: [SEO_AND_ADS_READINESS.md](SEO_AND_ADS_READINESS.md).
  O dosyanın geçmiş indeks sayıları güncel veri yerine kullanılmaz.
