# Google URL Denetimi sonucunu doğrudan alma

GSC Wizard canonical alanlarını aktarmadığında Google Search Console'un kendi
URL Denetimi ekranı veya APIs Explorer kullanılabilir. Google hesabındaki
giriş ve onay Google'ın kendi ekranında yapılır. Parola, çerez, erişim tokenı
veya yetkilendirme kodu dışarı aktarılmaz.

## Search Console ekranından denetim

### Mevcut Chrome ve SSH köprüsü

9 Ekim'de kullanıcının başka sohbette hazırladığı, Windows Chrome'a bağlı
mevcut SSH köprüsü bu sohbetten de fiilen doğrulandı. TinyFish gerektirmeden
LectureSift paneli ve Google'ın indeks kayıtları okunabildi. Bu erişim
ambient sekme adresinden çıkarılmadı: hedef sekmeye bağlı DOM yanıtı alındı.

Bu ortamda mevcut yardımcı
`~/.config/lecturesift-browser/control.py` üzerinden `status`, `tabs`,
`command` ve bekleyen komutlar için `result ID` kullanılıyor. Yardımcı kendi
yerel yetkilendirmesini kullanır; token, Google çerezi veya profil dosyası
kopyalanmaz, çıktı veya depoya eklenmez. `connected=true` yalnız bağlantı
durumudur; Google mülküne erişim her seferinde sayfada ayrıca doğrulanır.

Sekmeler hedef kimlikleriyle ayrılır. Diğer sohbetin açık sayfası
değiştirilmeden bu göreve ait tek Search Console sekmesi kullanıldı.
`Target.createTarget` yardımcıda desteklenmediği için köprünün izin listesi
değiştirilmedi ve süreç yeniden başlatılmadı; mevcut sayfadaki normal
`window.open(..., '_blank', 'noopener')` işlemiyle ayrı sekme açıldı ve yeni
hedef kimliği sekme listesinden doğrulandı. Komut beklemede dönerse aynı
eylem tekrar gönderilmez; kendi komut kimliğiyle sonucu alınır. İş sonunda
yalnız göreve ait sekme kapatılır, diğer sohbetin sekmesi ve köprüsü korunur.

Bağlantı kullanıcının Chrome ve SSH/Powershell oturumuna bağlıdır; bilgisayar
kapanınca çalışacağı veya yeni sohbetlere kendiliğinden bağlanacağı
varsayılmaz. Bu, yerel test/derleme veya tarayıcı test matrisi çalıştırmaz;
istenen sağlayıcı ekranının mevcut kullanıcı oturumuyla denetlenmesidir.
Google CAPTCHA isterse istek otomatik tekrarlanmaz veya başka oturumla
aşılmaya çalışılmaz. Chrome'a geçiş kullanıcının TinyFish bağımlılığını
kaldırma talebiyle yapıldı; bu geçişte yeni indeksleme isteği gönderilmedi.

Kullanıcı daha sonra doğrulamayı tamamladığını bildirdiğinde, önce mevcut
isteğin kabul ekranını oku. “Dizine eklenmesi istendi”/“TEKRAR İSTEK GÖNDER”
görünüyorsa aynı URL için tekrar gönderme. 9 Ekim 03:33 TSİ'de Türkçe PDF
rehberindeki manuel isteğin kabulü bu şekilde doğrulandı; ardından henüz
gönderilmemiş İngilizce rehberin tek isteği 03:35 TSİ'de kabul edildi.
[Bu oturumun sonuçları](pdf-indexing-requests-2026-10-09.json) önceki salt
okuma oturumundan ayrıdır. İnsan doğrulaması otomatikleştirilmez; yeni bir
CAPTCHA çıkarsa kullanıcıya bırakılır.

### Alternatif: kayıtlı TinyFish profili

9 Ekim 2026'da kullanıcının kaydettiği TinyFish tarayıcı profiliyle Search
Console mülküne erişim doğrulandı. Profil yönetimi için kalıcı giriş noktası
[TinyFish Profiles](https://agent.tinyfish.ai/profiles) sayfasıdır. Bu çalışmada
araçtan alınan iki geçici devir bağlantısının süresi doldu; nedeni doğrulanmadı.

1. Kullanıcının Google'ın kendi giriş ekranında oturum açıp kaydettiği
   TinyFish profilini kullan. Codex tarayıcısındaki çerez veya kimlik
   bilgilerini başka bir tarayıcıya kopyalama.
2. Her oturumda çağrılabilir tarayıcı okuma/kontrol aracını ve
   `sc-domain:lecturesift.com` mülküne fiilî erişimi doğrula. Açık sekme adresi
   veya kayıtlı profilin varlığı erişim kanıtı değildir.
3. Search Console'da hedef URL'yi denetle; indeks durumunu, son tarama
   zamanını, kullanıcı tarafından beyan edilen canonical'ı ve Google'ın
   seçtiği canonical'ın ekranda gösterilen değerini birlikte kaydet.

### Alanları yorumlama

Google'ın seçimi **İncelenen URL** ise denetlenen hedef URL'yi kullan.
Google'ın seçimi **Kullanıcı tarafından beyan edilen standart URL ile aynı**
ise adresi aynı Google indeks kaydındaki beyan edilen canonical alanından
çözümle. Bugünkü canlı HTML etiketinden çıkarma: eski tarama kaydı `.html`
adresini gösterirken canlı sayfa temiz adresi gösterebilir.

[9 Ekim gözlemleri](google-selected-canonicals-2026-10-09.json), ekranın
gösterdiği değeri, çözümleme dayanağını ve canlı kontrolleri ayrı tutar.
Bu gözlemler ham Google API JSON'u değildir ve aşağıdaki API içe aktarıcısına
verilmez. Ekrandan gönderilen indeksleme isteğinin kabulü de yeniden taramanın
veya indekslemenin tamamlandığı anlamına gelmez.

## Alternatif: APIs Explorer ile ham JSON alma

1. [Google index.inspect sayfasını](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect)
   açıp **Try it!** panelini kullan.
2. İstek gövdesine aşağıdaki JSON'u gir.
3. **Credentials** altında **Google OAuth 2.0** seçili, **API key** kapalı
   olsun. **Show scopes** altında yalnız
   `https://www.googleapis.com/auth/webmasters.readonly` seç.
4. **Execute** düğmesine bas; LectureSift mülküne erişimi olan Google
   hesabını seçip Google'ın kendi izin penceresini onayla.
5. Başarılı `200 application/json` sonucunun yalnız JSON yanıt gövdesini
   kaydet. İstek başlıkları veya oluşturulan curl komutu sonuç dosyası değildir.

```json
{
  "siteUrl": "sc-domain:lecturesift.com",
  "inspectionUrl": "https://lecturesift.com/quiz-flashcards"
}
```

[Dört öncelikli isteğin gövdeleri](direct-google-inspection-requests.json)
Türkçe quiz, Portekizce quiz/iletişim ve indeksli ana sayfa kontrolünü içerir.
Her gövde ayrı bir URL Denetimi isteğidir. 401/403 veya başka API hata
yanıtı canonical sonucu olarak kullanılmaz. Hata kodu paylaşılabilir;
kimlik bilgisi paylaşmak gerekmez.

Google onayı yalnız APIs Explorer oturumunu yetkilendirir; başka bir ajan
bağlantısına Google erişimi eklemez. Bu akışta gerçek API yanıtının JSON
gövdesi çalışma alanına aktarılır. Search Console URL Denetimi ekranı için
belgelenmiş bir JSON indirme düğmesi varsayılmaz; ekran gözlemleri API yanıtı
biçimine dönüştürülmez.

## API sonucunu rapora alma

`scripts/import_gsc_url_inspection.py` yalnız sağlanan JSON dosyasını işler.
Kimlik bilgisi kullanmaz ve ağa bağlanmaz. Örnek kullanım:

```sh
python3 scripts/import_gsc_url_inspection.py \
  --input /path/to/google-quiz-response.json \
  --site-url sc-domain:lecturesift.com \
  --inspection-url https://lecturesift.com/quiz-flashcards \
  --output docs/seo/google-quiz-canonical-observation.json
```

Girdi UTF-8 olabilir; Windows editörlerinin ekleyebildiği BOM da kabul edilir.
Çıktı dosyası yeni olmalıdır. İçe alma zamanı, Google'ın son tarama zamanı
yerine geçmez. İstek URL'si kullanıcı tarafından sağlanan bağlamdır; Google'ın
yanıtının o URL'ye ait olduğu yalnız dosya biçiminden doğrulanamaz.

Rapor `googleCanonical`, `userCanonical` ve `inspectionResultLink`
alanlarının mevcut olup olmadığını değerlerinden ayrı kaydeder. Google
alanı vermediyse adres uydurulmaz veya canlı sayfanın canonical etiketiyle
doldurulmaz. Düz GSC Wizard özeti ham Google yanıtı olarak kabul edilmez.
Bu akış yeniden tarama/indeksleme isteği göndermez.

Google'ın şemasına göre indeks dışı sayfalarda `googleCanonical` alanı
bulunmayabilir. Alanın Google tarafından verilmemesi ile bir aracı uygulamanın
alanı göstermemesi ayrı durumlardır. Desteklenen şemada bulunması, her URL
için bir canonical değeri alınacağını garanti etmez.

## Doğrulama ve kaynaklar

Yardımcı programın örnek veri kontrolleri mevcut GitHub Actions akışında
çalıştırılır. Yerel test veya derleme çalıştırılmaz. Hazır bir program veya
başarılı CI, gerçek Google yetkilendirmesi ya da canonical sonucu değildir.

- [Google URL Denetimi API yöntemi](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect)
- [Google yanıt şeması](https://developers.google.com/webmaster-tools/v1/urlInspection.index/UrlInspectionResult)
- [APIs Explorer Google onayı ve scope seçimi](https://developers.google.com/explorer-help/authorization-and-authentication)
- [Search Console'da Google-selected canonical alanı](https://support.google.com/webmasters/answer/9012289?hl=en)
