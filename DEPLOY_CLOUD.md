# ☁️ Bilgisayar Kapalıyken 7/24 Bulutta Çalıştırma Rehberi

Bu sistem, **kendi bilgisayarınızı tamamen kapatsanız bile** GitHub bulut sunucularında veya bağımsız bir bulut VPS sunucusunda 7/24 kesintisiz çalışacak şekilde tasarlanmıştır.

---

## 🌟 1. Yöntem: GitHub Actions (Tamamen Ücretsiz, Sıfır Donanım, 7/24 Bulut)

Bilgisayarınıza, elektrik faturasına veya ücretli sunucuya ihtiyaç yoktur. Bot doğrudan **GitHub'ın kendi bulut altyapısında** çalışır ve tüm işlem kayıtlarını, grafiklerini ve analizlerini otomatik olarak reponuza geri yazar (`git commit & push`).

### ⏰ Otomatik Çalışma Takvimi (Zamanlayıcılar):
Projede `.github/workflows/market_bot.yml` ve `.github/workflows/forex_macro_monitor.yml` iş akışları kurulmuştur:
1. **16:15 TSİ (13:15 UTC - Açılış Öncesi)**:
   * Forex Factory ekonomik takvimini inceler (Kırmızı Klasör Kalkanı).
   * Patlayıcı momentum ve düşük lotlu hisseleri (VJET, OLOX, STDN tarzı) tarar.
2. **16:30 TSİ (13:30 UTC - Borsa Açılışı)**:
   * SMC Rejection Block ve Momentum fırsatlarını birleştirir.
   * 48 Mum Formasyonunu (Çekiç, Yutan Boğa vb.) ve Order Flow alıcı emilimini doğrular.
   * Onaylı hisselere Alpaca bracket emri iletir.
   * **4 saat boyunca canlı gözcü (watcher) olarak piyasada kalır**:
     * Portföy kârı **+%1.0** olduğunda kârı kasaya kilitler ve günü kapatır.
     * Hisse **+%0.6** kâr gördüğünde stop seviyesini başabaşa çeker ($0 risk).
     * 5 dakikalıkta 3 kırmızı barla çöküş olursa erken hasar kontrolüyle çıkar.
3. **21:30 TSİ (18:30 UTC - Seans Sonu Kapanış Dalgası)**:
   * Kapanış öncesi ikinci dalga momentum fırsatlarını değerlendirir ve kârları kilitler.
4. **7/24 Her 4 Saatte Bir**:
   * Dünyadaki makro gelişmeleri, Hürmüz Boğazı/Petrol krizlerini ve Fed açıklamalarını `reports/forexfactory_briefing.md` içine işler.

### 🔑 1 Dakikalık Kurulum (Alpaca Anahtarlarını GitHub'a Ekleme):
Botun buluttan sizin adınıza emir iletebilmesi için Alpaca anahtarlarınızı GitHub'a 1 kez tanıtmanız yeterlidir:
1. GitHub'da deponuza gidin: `https://github.com/CoskunerBerke/AG_Bot`
2. **Settings** (Ayarlar) sekmesine tıklayın.
3. Sol menüden **Secrets and variables** $\rightarrow$ **Actions** seçeneğine girin.
4. **New repository secret** butonuna basarak 2 adet gizli anahtar ekleyin:
   * **İsim:** `ALPACA_API_KEY`  
     **Değer:** (Alpaca Key ID değeriniz, örn: `PK5R6GL...`)
   * **İsim:** `ALPACA_SECRET_KEY`  
     **Değer:** (Alpaca Secret Key değeriniz, örn: `3TPmiD4...`)
5. **Artık bitti!** Bilgisayarınızı kapatabilirsiniz.

### 📱 Telefonda veya Webde Nasıl Takip Edilir?
1. GitHub'da **Actions** sekmesine gidin.
2. Çalışan veya tamamlanan seansın üzerine tıklayın.
3. **Seans Özeti ($GITHUB_STEP_SUMMARY)** sayfasında:
   * Hangi hisseye neden girildiği,
   * Ne zaman çıkılacağı (Stop, Hedef, Başabaş),
   * Forex Factory haber durumu tek ekranda okunabilir.
4. **İşlem Günlüğü & Otopsi Grafikleri:**
   * Seans bittiğinde GitHub Actions botu `reports/trade_journal.csv`, `reports/postmortems/` ve `reports/charts/*.png` dosyalarını otomatik olarak reponuza pushlar.
   * Reponuzun ana sayfasından istediğiniz an inceleyebilirsiniz!

### 🔘 Manuel Çalıştırma Butonu:
İstediğiniz zaman GitHub Actions sayfasında **🤖 NASDAQ Otonom Bulut Botu** iş akışını seçip **Run workflow** butonuna basarak tek tuşla dilediğiniz görevi (analiz, emir gönderimi, haber taraması) hemen başlatabilirsiniz.

---

## 🖥️ 2. Yöntem: 7/24 Kesintisiz Bulut Sunucu (Cloud VPS / Docker)

Eğer saniye bazlı WebSocket akışını kendi kontrolünüzde tutmak isterseniz:
* **Önerilen Sunucu:** Oracle Cloud (Always Free - Ömür boyu 4 Core ARM, 24 GB RAM tamamen ücretsiz) veya Hetzner (~3.5 €/ay).
* **Başlatma:**
  ```bash
  git clone https://github.com/CoskunerBerke/AG_Bot.git
  cd AG_Bot
  cp .env.example .env
  nano .env  # Alpaca anahtarlarınızı yazın
  docker compose up -d
  ```
