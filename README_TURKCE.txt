LECTURESIFT BACKEND V1 — TÜRKÇE

BU PAKET NE YAPIYOR?
Gerçek video işleyen ilk prototiptir.

Video yüklenince:
1) Videodan belirli aralıklarla görüntüler alır.
2) Birbirine benzeyen tekrar karelerini temizlemeye çalışır.
3) Kalan kareleri "slides" klasörüne koyar.
4) Videonun sesini 20 dakikalık küçük parçalara böler.
5) OpenAI gpt-4o-mini-transcribe ile konuşmayı yazıya çevirir.
6) Sonucu ZIP olarak indirir:
   - transcript.txt
   - slides klasörü
   - slides.json

BU HENÜZ SON ÜRÜN DEĞİLDİR.
İlk hedef: gerçek video -> gerçek transkript + gerçek slayt çıktısını doğrulamak.

KURULUMDA SANA GEREKECEKLER
A) GitHub hesabı
B) Render hesabı
C) OpenAI API hesabı ve API anahtarı

OPENAI ANAHTARINI KİMSEYLE PAYLAŞMA.
Bana da mesaj olarak göndermene gerek yok. Render'ın "Environment" alanına kendin yapıştıracaksın.

RENDER AYARLARI
Bu proje Docker ile hazırlanmıştır çünkü FFmpeg gerekiyor.
Render, Dockerfile bulunan projeleri Docker Web Service olarak çalıştırabilir.

Backend yayına girdikten sonra adres yaklaşık şöyle olur:
https://lecturesift-api.onrender.com

Adresin sonuna:
 /health
ekleyince:
{"ok":true,"openai_key":true}
görmelisin.

SONRA
Backend adresini bana yaz.
Ben mevcut Netlify index.html dosyanı bu backend'e bağlayan yeni sürümü hazırlayacağım.

NOT
Render ücretsiz servisleri boşta kaldığında uyuyabilir; ilk istek daha yavaş başlayabilir.
Gerçek müşteri aşamasında ücretli sunucuya geçilir.
