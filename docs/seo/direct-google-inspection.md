# Google URL Denetimi sonucunu doğrudan alma

GSC Wizard canonical alanlarını aktarmadığında Google'ın kendi APIs Explorer
ekranı kullanılabilir. Google hesabındaki onay tarayıcıda verilir. Parola,
çerez, erişim tokenı veya yetkilendirme kodu dışarı aktarılmaz.

## Google ekranında yapılacak işlem

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

Google onayı yalnız APIs Explorer oturumunu yetkilendirir; ajanın mevcut
bağlantısına Google erişimi eklemez. Açık Codex sekmesini okuma aracı
bulunmadığından bu akışta sonuç JSON'unun çalışma alanına aktarılması gerekir.
Google'ın standart Search Console URL Denetimi ekranı canonical alanını
gösterir, ancak bu ekran için belgelenmiş bir JSON indirme düğmesi varsayılmaz.

## Sonucu rapora alma

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
