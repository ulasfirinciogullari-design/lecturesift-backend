# LectureSift devam kaydı

## Güncel durum — 9 Eylül 2026

Kullanıcı gece boyunca onay beklemeden ilerlemeyi ve hazır işleri canlıya
almayı istedi. Üretim Netlify + Render üzerinde; OVH makinesi geliştirme
kopyasıdır. Yerel ağır test çalıştırılmadı, yeni ücretli servis alınmadı.

- PR67 birleştirildi. `2b7f3a34fba5322452ae91361d838635ddfec477` sürümü
  Netlify'da 01:00 UTC, Render API/worker üzerinde 01:02 UTC'de yayımlandı.
  Asistan, görsel üretimi, kredi satışı ve yenileme davet ödülleri API'de
  01:06 UTC'de açıldı. Tema, mobil düzen, 13 dil ve resmi iyzico görseli korundu.
- Gerçek PostgreSQL 18.6 veritabanına 00:45 UTC'de tek işlemde dokuz eksik
  tablo eklendi. Önceki bütün satır ve şema nesneleri korundu. Ayrı API ve
  worker girişleri etkin; worker ürün tablolarına erişemiyor. Yönetilen
  yedek ve PITR mevcut. Üretim geri yüklemesi yapılmadı; gerçek dump/restore
  sözleşmesi sentetik PostgreSQL 18 CI ortamında doğrulandı.
- İki kaynak yayın yeteneği açık. API'de sohbet, görsel ve davet bayrakları
  true; worker'da false. Kalıcı kampanya sınırı API'de ayarlandı.
  Yayın erişimi ve sırlar depo dışındaki özel yayın dizininde tutuluyor.
- Son ürün ağacı 1.224 test / 3 atlama ve 34 tarayıcı kontrolü / 2 atlama
  geçti (Actions 34296969142). Gerçek model isteği metin ve geçerli JPEG
  üretti; canlı misafir asistanı Türkçe yanıt ve kayıt düğmesi döndürdü.
  Gerçek ücretli satın alma veya davet yenilemesi test için yapılmadı.
- YouTube çözülmedi: bgutil ve WPC token üretse de bot engeli sürdü.
  Aynı uygulama Render'ın izole worker işinde LS-URL-02 döndürdü.
  Invidious Companion denemesi oynatıcı isteğinde zaman aşımına uğradı;
  üretime eklenmedi. PR75'in temiz tarayıcı denemesinde gerçek izleme
  sayfası da LOGIN_REQUIRED / bot engeli döndürdü; geçici misafir çerezleri
  ve üretilen token ile gerçek indirme doğrulanamadı.
- PR75 birleştirildi: `1654e0fa093b12dc42dac52b11436ffb7f9515df`.
  Actions `34299906973`: 1.224 test / 3 atlama, 34 tarayıcı kontrolü /
  2 atlama başarılı. Netlify 01:44, Render API/worker 07:19 UTC'de
  yayımlandı. Canlı misafir isteği 07:20 UTC'de düz Türkçe yanıt üretti.
  İki Render servisinin önceki otomatik yayın ayarı 07:21 UTC'de geri
  açıldı ve doğrulandı; bekleyen bir otomatik yayın ayarı işlemi yok.
- 07:26–07:40 UTC'deki izole Render denemelerinde TV, TV Simply, iOS,
  visionOS ve web music yolları da okunabilir ses dosyası üretmedi.
  Son tanılama, web sayfasında HTTP 429 ve oynatıcı API'sinde HTTP 403
  yanıtlarını ayırdı. Bu ortamda IPv6 çıkışı yok. Kullanılabilir özel
  proxy / indirme API hesabı bilgisi soruldu; yeni servis satın alınmadı.
  Deneme kanıtları özel yayın dizininde; geçici bulut nesneleri silindi.
- Video üretimi kapalı. Görsel üretimi ve yüklenen videonun sınırlı görsel
  analizi açık; videonun sesi sohbet ekinden çözümlenmiyor.

Aşağıdaki 8 Eylül kayıtları arşivdir; eski kapalı özellik veya erişim eksikliği
ifadeleri bu güncel kaydın yerine geçmez. Ayrıntılı yayın kanıtları:
`docs/PRODUCT_ACTIVATION.md` ve `docs/YOUTUBE_DOWNLOAD_SUPPORT.md`.

## Arşiv — 8 Eylül 2026 yayın öncesi durum

Kullanıcı geliştirme, uzak kontroller ve hazır tema değişikliklerinin canlı
yayınını onayladı; tekrar onay istenmiyor. Yerel kaynak sınırı nedeniyle ağır
kontroller mevcut GitHub Actions ortamında çalıştırılıyor. Aşağıdaki arşivdeki
eski onay bekleme ve test bekleme ifadeleri güncel durum değildir.

- Canlı tema: PR68–74 yayımlandı. Eğitim odaklı düzen, özgün ana sayfa görseli,
  mobil teklif kartları, 22 para birimi simgesi, Hakkımızda menüsünden Ürün
  grubunun kaldırılması ve resmi iyzico görselinin koyu zemini doğrulandı.
  Ana sürüm: `cb7f7b4f5b23195cceef882cde368f41a2f2a89c`; Netlify
  yayın zamanı `2026-09-08T23:52:47.801Z`.
- YouTube kaynak metni: PR74, “YouTube videolarını analiz et” ve
  “YouTube’dan ekle” ifadelerini 13 dile taşır. Yalnızca YouTube açıklaması,
  örnek adres ve tarayıcıda YouTube dışındaki adreslerin reddi dahildir.
  PR74 kaynak sürümü `89d21c3` üzerinde 831 test / 3 atlama ve 18 tarayıcı
  kontrolü / 2 atlama geçti (Actions `34292374187`). Açık temadaki soluk
  alan başlığı düzeltildi; geçersiz URL artık misafir oturumu açmaz. Canlı
  13 dil ana sayfası ve yayınlanan JS/CSS dosyaları eşleşti. Çalışma alanı
  çevirisi tarayıcıda uygulanır; İngilizce masaüstü/mobil yolu CI’da geçti.
  Resmi iyzico PNG dosyası canlıda bayt düzeyinde aynı kaldı.
- PR67: `391352c4c7e569f833064c54fabdfe29feed94bd` üzerinde Actions
  `34291050131` başarılı: 1.221 test / 3 atlama ve 34 tarayıcı kontrolü /
  2 atlama. Yeni YouTube metni bu sürümün üzerine uygulanıyor.
- Şema/kurtarma: tam sekiz ürün tablosunun PostgreSQL 18 sözleşmesi,
  mevcut veriyi koruyan tek işlemli geçiş, sürümlü yedek/geri yükleme,
  API/worker rol ayrımı, eşzamanlı davet/kota/kupon işlemleri ve sahipli
  veri dışa aktarımı/silme izole CI veritabanlarında geçti. Gerçek üretim
  veritabanının geçiş ve geri yükleme kanıtı henüz yok.
- Asistan: sahipli sohbet, misafir denemesi, paket kredileri/ek kredi satışı,
  resim ve sınırlı video karesi analizi kaynakta hazır. Ayrı bir eylemle
  200 kredi karşılığı görsel oluşturma, indirme, tekrar istekte çift ücret
  almama ve kısa süreli özel önbelleğin periyodik temizliği de eklendi.
  Mobil sonuç/indirme/kredi görünümü uzak ekran görüntüsünde incelendi.
  Gerçek model erişimi ve gerçek üretilmiş görsel doğrulanmadı.
- Davet: ilk abonelik ve gerçek ücretli yenilemeler, dakika veya kupon
  seçimi, ortak aylık üst sınır, bekletme/iade ve kampanya başlangıcı
  uygulanmış durumda. Ek dakika alımları yenileme ödülü kazandırmaz.
- Her iki `SCHEMA_RECOVERY_RELEASE_READY` yeteneği false. Canlı Render
  uygulamasında asistan uçları henüz yok. Bu geliştirme hesabında üretim
  veritabanı/model kimlik bilgileri, kullanılabilir SSH kimliği veya
  parolasız sudo bulunmuyor. Yeni kullanıcı onayı bu erişimi sağlamaz;
  `docs/PRODUCT_ACTIVATION.md` adımları yetkili üretim oturumunda uygulanmalı.
- Gerçek YouTube denemesi: hem bgutil hem gerçek misafir tarayıcısı (WPC)
  PO token üretti; ikisi de aynı genel örnekte `LS-URL-02 / bot_challenge`
  döndürdü. Tarayıcının başlamaması düzeltildi; indirme sorunu çözülmüş
  değildir. Üretim ağı üzerinde başarılı indirme kanıtı yok.
- Video üretimi henüz uygulanmadı. Sora/Videos API 24 Eylül 2026'da
  kapanıyor; yeni sağlayıcıda hesap erişimi, fiyat, sahipli asenkron iş ve
  kredi kurtarma akışı doğrulanmadan ücretli video sunulmuyor.

## Arşiv: önceki devir incelemesi

8 Eylül 2026; kaynak tabanı: `f21b45b587d1d293c4a40cc0b0b93aa32b5b0c88`.
Bu kayıt sınırlı kaynak incelemesidir; test başarısı veya canlı kabul raporu değildir.

## Tamamlanan devir

- `vps-8c7c7a48` / `lecturesift-dev` doğrulandı. Görev kopyası:
  `/home/lecturesift-dev/.codex/worktrees/58cb/lecturesift-backend`.
- Sığ geçmiş tamamlandıktan sonra `ca83768` atalık kontrolü geçti;
  yalnız görev kopyası fast-forward ile `f21b45b` sürümüne ilerletildi.
- Ana checkout `/home/lecturesift-dev/code/lecturesift-backend` temiz ve
  `4f04fc4` sürümünde korundu. AGENTS.md okundu.
- Aktarım bundle'ı geçerli; dört dosyanın hashleri ve izinleri devir
  manifestiyle eşleşti. Eski sohbetler bu kayda birebir taşınmış sayılmaz.

## Yayın ve doğrulama sınırları

- Bu sürümün kamuya açık push işlemi için açık, sürüme özel onay bekleniyor.
  Onaydan önce push, CI tetikleme, PR birleştirme veya dağıtım yapılmayacak.
- `f21b45b` için CI sonucu yok (devir bilgisi); bu incelemede CI sorgulanmadı
  veya çalıştırılmadı. Önceki 936 başarılı / 3 atlanan test bu sürüme ait değil.
- Windows'ta ve üretimle ilişkili bu VPS'de ağır test çalıştırılmayacak.
  Mevcut GitHub Actions tanımları onay sonrası kullanılacak.
- Üretim, sırlar, canlı veri, ödeme ve reklam aktivasyonu kapsam dışı.
  `/opt/lecturesift`, PostgreSQL, Redis, Caddy ve yönetici hesabına dokunulmadı.
- Devirde PR #67 taslak; eski Netlify önizlemesi yeni sürüm kanıtı değil.
  Netlify frontend / Render backend-worker-Instagram ve OVH geçiş durumu
  yeniden doğrulanmayı bekliyor; bu incelemede canlı servislere gidilmedi.

## Tek takip listesi

Her satırdaki kaynak/test adı yalnız mevcut uygulama veya test tanımına işaret
eder. Aşağıdaki ürün kalemlerinin hiçbiri bu turda canlıda doğrulanmadı.

| Öncelik / konu | Kaynakta görülen | CI / kalan kabul işi |
| --- | --- | --- |
| P0 — Referans güvenliği | `lecturesift/referrals.py`: `SCHEMA_RECOVERY_RELEASE_READY = False`; kapalı. `deploy/REFERRAL_RELEASE_GATES.md` kuralları ve açılış koşulları tanımlı. | `tests/test_referrals.py` tanımları var, yeni sürüm sonucu bekliyor. Sürümlü cutover v4 / recovery v3, PostgreSQL 18 eşzamanlı kota-kupon-ödül, rol, yedek/geri yükleme ve veri koruma kanıtları olmadan açma. |
| P0 — Referans koşulları | İlk uygun ücretli abonelik; davetçiye 60 dk VEYA %10/en fazla 50 TRY kupon, davet edilene 30 dk; ayda 5 eşleşme, 14 gün ve yönetici mutabakatı. Ücretsiz/test/ek kredi hariç. | Gerçek eşzamanlılık ve iade/itiraz riskleri ayrıca doğrulanacak. Otomatik chargeback geri alımı yok; kişi başı kota site bütçesi değildir. |
| P0 — Fiyat/ödeme | `billing_service.py`, `billing.py`, `payments.py`; satın alma koşulları ve yeni katalog. `docs/PRICING_REVIEW_2026-09-08.md` varsayımları açıklar. | `test_pricing_revision.py`, `test_purchase_terms_schema_transition.py`, `test_iyzico.py`, `test_payments.py` sonuçları bekliyor. Eski haklar, worker yetkileri, kurtarma sözleşmesi ve sağlayıcı mutabakatı ayrıca kabul edilecek. Kişisel IBAN hareketi webhook kanıtı sayılmaz. |
| P0 — Ana hesap/yönetici | `rollout_routes.py` ADMIN_ADMIN kontrolü; `rollout_service.py` yapılandırmaya bağlı korumalı e-posta kontrolü; `test_rollout.py` korumalı hesap testi var. | `ulasfirinciogullari@gmail.com` hesabının etkin yapılandırmada korunduğu bu turda doğrulanmadı. Silme/kapama yolları ve bağımsız yönetici girişinin kapsamı incelenecek. Sır dosyası okunmayacak. |
| P1 — Tema ve quiz | `theme.css`, `site-shell.js`, `app.js`, `home-demo.js`; sıcak tema, menü, demo ve quiz değişiklikleri mevcut. | `test_quiz_runtime.py`, `test_home_demo_runtime.py`, `test_public_menu_runtime.py` ve tarayıcı testleri bekliyor. Seçim, tek puanlama, tekrar deneme, odak, açık/koyu ve mobil görünüm kabul edilecek. |
| P1 — Tarayıcı kontrolleri | `.github/workflows/test.yml`, `tests/browser/`: tek Chromium işçisi, masaüstü/mobil × açık/koyu; Arapça RTL; sentetik sonuçlar ve dış ağ engeli tanımlı. | Tanım var, çalıştığı henüz kanıtlanmadı. 13 dilin tüm sayfa/oturum kombinasyonları bu üç smoke senaryosuyla tamamlanmış sayılmaz. |
| P1 — Dil ve oturum | `i18n.js`, `page-i18n.js`, `auth.js`, `site-shell.js` inceleme başlangıçları. | Tüm sayfalarda dil, giriş/çıkış durumu, menü ve hassas metin tutarlılığı için kapsam matrisi çıkarılacak; 13 dilde demo kapsamı ile genel çeviri kalitesi ayrı denetlenecek. |
| P1 — Hesap | `billing_service.py` bağlantı/kod doğrulaması, altı haneli kod üretimi, profil ve parola işlevleri; `frontend/account.html`. | Süre/tek kullanım/hata akışları, e-posta teslimi ve mobil sekmeli hesap uçtan uca kabul bekliyor. SMS istenmiyor. |
| P1 — Yönetim ve destek | `rollout_routes.py`, `rollout_service.py`, `frontend/admin.html`: yönetim ve destek yanıt işlevleri mevcut. | Plan/ek kredi/sipariş/ödeme yetkileri ve panelden e-posta yanıtının gerçek teslimi ayrı denetlenecek; kapsamlı admin tamamlandı varsayılmayacak. |
| P1 — Medya güvenilirliği ve hız | `documents.py`, `slides.py`, `media.py`, `pipeline.py`; `test_material_formats.py`, `test_documents_and_costs.py`, `test_speed_optimizations.py` test başlangıçları. | PDF/slayt/video/ses/OCR için sentetik dosya matrisi, başarısızlık ve süre ölçümleri uygun uzak ortamda bekliyor. Sağlık yanıtı kabul kanıtı değil. |
| P1 — Çıktı tercihleri | `pipeline.py` zaman damgası ve konuşmacı seçenekleri; `test_transcript_timeline.py`. | Varsayılan ayrıntılı özet, isteğe bağlı quiz/kart, seçeneklerin arayüzden çıktıya korunması denetlenecek. Konuşmacı etiketleri parça kapsamlı; kesin kişi kimliği varsayılmayacak. |
| P1 — Geçmiş ve ZIP | Tarayıcı testinde sentetik eski sonucu açma; `test_study_pack_audio_export.py` sesin ZIP üyeliği, sahiplik ve temizlik testleri mevcut. | Yeni sürüm CI sonucu ve gerçek depolama/indirme kabulü bekliyor; mevcut tüm çıktıların açıldığı varsayılmayacak. |
| P1 — Limit ve maliyet | `resource_limits.py`, `costs.py`, fiyat incelemesi. | Dakika/kaynak/iş limitlerinin tüm ekranlarda açıklığı ve gerçek maliyet kaydı karşılaştırılacak. Fatura, kur, ödeme gideri ve iadeler olmadan kâr doğrulandı denmeyecek. |
| P1 — Reklam/SEO/hukuk | `SEO_AND_ADS_READINESS.md`, `display-ads.js`, hukuki HTML sayfaları; reklam ve SEO test tanımları mevcut. | Devirde AdSense pub-7608481350058806 durumu Hazırlanıyor; burada güncellenmedi. Onay, CMP/rıza ve ücretli reklamsız hakları doğrulanmadan açma. Hukuki sayfaların varlığı içerik kabulü değildir. |
| P2 — Instagram | `instagram.py`, `daily_social.py`, `social_routes.py`; `test_instagram.py`, `test_instagram_publisher_gate.py`. | Mevcut uygulama ve yayın kapıları ayrıntılı incelenecek; canlı entegrasyon/yayın başarısı doğrulanmadı. Bu görevde paylaşım veya reklam başlatılmadı. |

## Sonraki sıra

1. Kaynak incelemesini P0 ödeme, kurtarma ve hesap korumasında derinleştir;
   somut eksikleri bu kayda ekle, kanıt olmayan kalemleri tamamlandı işaretleme.
2. Yayına aday kesin sürümü ve değişiklik kapsamını incelemeye hazır hale getir.
   Açık sürüm onayı alınmadan kamuya gönderme veya CI tetikleme.
3. Onay sonrası mevcut uzak CI sonuçlarını ilgili SHA ile kaydet; canlı
   kabul ve aktivasyon kapılarını ayrıca değerlendir.

## 8 Eylül — devam geliştirmesi

Kullanıcı bu görevde geliştirmeye devam edilmesini istedi. Ardından davetin
sonraki abonelik alımları/yenilemelerde de kazandırmasını seçti; ek dakika
alımları hariç kaldı. 13 dil ve kişiye göre para birimi birlikte korunacak.
Aşağıdaki değişiklikler yalnız çalışma kopyasındaki kaynakta hazırdır; tablodaki
ilk inceleme bulgularının bu konulara ilişkin güncellemesidir.

- Ana hesap artık boş koruma yapılandırmasında da korunuyor. Kendi hesabını
  kapatma, yönetici kapatma/toplu kapatma ve e-posta değiştirerek korumayı
  kaldırma yolları engellendi. Profil/parola işlemleri ve bağımsız ADMIN_ADMIN
  yetkisi korunuyor; normal kullanıcıya yönetici yetkisi verilmedi.
- E-posta değişikliğinin yanlış kod sayacı hata sonrası kalıcı; beş yanlış
  denemede istek geçersiz. Eşzamanlı yeniden gönderim/doğrulama kilitleri ve
  başarısız gönderimin sonraki isteği silmemesi için düzeltmeler eklendi.
- Yeni abonelik, dönem sonunda iptal edilecek eski aboneliğin haklarını
  gölgelemesini engelliyor. Onaylarda kullanıcı/sipariş kilit sırası ortak.
  Hesap kapanışı bekleyen havaleyi de iptal eder; sonradan ulaşan gerçek para
  için sağlayıcı mutabakatı/iade değerlendirmesi ayrıca gerekir.
- İlk davet ödülü korunarak ayrı yenileme defteri eklendi. Taslak yenileme
  ödülü 30 dakika VEYA %5 kupon; ilk ve sonraki alışverişler ortak aylık beş
  ödül sınırında. Davet edilen başına ayda en fazla bir yenileme ödülü var;
  30 dakikalık hoş geldin ödülü tekrar verilmez. Yıllık kota yenilenmesi
  ödeme sayılmaz. Ret/sınır kararları sonraki ay tekrarla yeniden açılmaz.
- Kupon tavanları 22 para birimi için sürümlü sabit bölgesel değerlerdir;
  canlı kur değildir. Dil/para birimi bağımsızdır. Eski TRY kuponları korunur;
  yeni kupon seçilen para biriminde sabitlenir. Kullanılabilir ödeme kanalı
  olmayan para biriminde yeni kupon verilemez, dakika seçimi kullanılabilir.
- Davet ekranına ilk/yenileme geçmişi, değişken tutarlı ödül seçenekleri ve
  kupon para birimi seçimi eklendi. 13 dilde kapalı/kabul edilmemiş davet
  durumu açıklanıyor. Hesap sekmelerinin RTL klavye yönü düzeltildi.
- Davet kartlarındaki eski sabit mavi/koyu renkler sıcak temanın açık/koyu
  renklerine bağlandı; küçük yazılar ve dokunma alanları büyütüldü.
- Kurtarma belgelerinin güncel v3 dosya adları ve bağımlılık denetimi düzeltildi.

Doğrulama: Python dosyaları içe aktarılmadan sözdizimi olarak okundu; farklarda
boşluk hatası yok. Regresyon testleri yazıldı, çalıştırılmadı. Bu hesapta Node
komutu bulunmadığından JavaScript sözdizimi kontrolü de uzak CI bekliyor;
bağımlılık kurulmadı. Önceki test sayıları yeni çalışmaya mal edilmedi.

Referans kaynak yeteneği hâlâ `False`. Yeni beş tablonun sürümlü şema, rol,
geri yükleme, gerçek PostgreSQL eşzamanlılığı ve eski ödemelerin kampanya
başlangıcına göre kapsamı açık yayın kapılarıdır; canlı dağıtım yok.

## 8 Eylül — onaylı önizleme ve CI takibi

Kullanıcı `7734528` kapsamının taslak PR #67'ye gönderilmesini, uzak CI ve
otomatik önizlemeyi onayladı; yetkili rutin düzeltmeler için tekrar onay
istenmemesini belirtti. Yukarıdaki onay bekleyen geliştirme kayıtları bu
güncellemeyle aşılmıştır; canlı aktivasyon kapıları devam eder.

GitHub bağlantısıyla oluşturulan `e62b3db` sürümünün kaynak ağacı
`ff45da59e000354bf7435cb35048c5a61faf6125`, onaylanan yerel sürümle birebir
aynıdır. Netlify bu sürümün önizlemesini başarıyla yayımladı. GitHub Actions
34271910101 çalışması, iş düzeyinde kullanılamayan `runner.temp` ifadesi
nedeniyle testler başlamadan reddedildi. Tarayıcı dizini artık ayrılan işçide
bir adımla belirleniyor; düzeltmenin uzak test sonuçları bekleniyor.

PR taslak, davet yeteneği kapalıdır. Önizleme üretim API'sini kullandığından
yalnız görünüm incelemesine uygundur; gerçek ödeme kabul kanıtı değildir.

`041c389` için 12 tarayıcı senaryosu geçti. Python CI'da 1.130 test geçti,
3 test atlandı; kalan tek hata, ana sayfa testinin yeni davet çeviri kaynağını
örnek ortama vermemesiydi. Test girdisi düzeltildi; yeni sürüm sonucu bekleniyor.

Kullanıcı daha sonra `LS-URL-02` indirme hatasının çözülmesini ve bağlantı
alanının yalnız YouTube kabul etmesini istedi. Kaynakta YouTube doğrulaması,
13 dilde açık alan açıklaması, Deno/EJS bileşenleri ve indirme regresyonları
eklendi. Uzak CI uygulama imajını da doğrulayacak. Gerçek başarısız video
bağlantısı verilmedi; canlı indirme çözümü doğrulandı denmiyor. Ayrıntılar:
`docs/YOUTUBE_DOWNLOAD_SUPPORT.md`.
