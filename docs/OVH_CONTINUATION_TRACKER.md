# LectureSift tek devam kaydı

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
başlangıcına göre kapsamı açık yayın kapılarıdır. Kamuya push/CI/otomatik
önizleme için kesin sürüm kapsamlı onay henüz alınmadı; canlı dağıtım yok.
