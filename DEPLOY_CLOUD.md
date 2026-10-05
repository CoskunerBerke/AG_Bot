# NASDAQ Botu Bulutta (Cloud) ve GitHub'da Çalıştırma Rehberi

Bu botu kendi bilgisayarınızı hiç açık bırakmadan, 7/24 veya borsa saatlerinde bulutta çalıştırmanın **2 kanıtlanmış yöntemi** vardır:

---

## 1. Yöntem: GitHub Actions (Tamamen Ücretsiz & Sıfır Kurulum)

Bu yöntemde bilgisayarınıza veya sunucuya ihtiyaç yoktur. Bot doğrudan GitHub'ın kendi bulut altyapısında çalışır.

### Nasıl Çalışır?
Projede `.github/workflows/market_bot.yml` dosyası hazırdır:
* **Otomatik Çalışma**: Hafta içi her gün borsa açılışından 10 dakika önce (16:20 TSİ) otomatik olarak GitHub sunucusunda uyanır.
* **Ne Yapar**: 39 Alfa hissede Rejection Block taraması yapar, emirleri hazırlar, eğer Alpaca anahtarlarınızı girdiyseniz emirleri otomatik gönderir.
* **Raporları İndirme**: Oluşturulan `.md`, `.csv` ve sermaye eğrisi `.png` grafikleri GitHub Actions sayfasında **Artifact** olarak kaydedilir; telefonunuzdan bile indirip görebilirsiniz.
* **Manuel Buton**: GitHub'da **Actions** sekmesine gidip dilediğiniz zaman tek tuşla `rb-scan` veya `rb-backtest` çalıştırabilirsiniz.

### Kurulum Adımları:
1. **Projeyi GitHub'a Yükleyin**:
   ```powershell
   git init
   git add .
   git commit -m "feat: Nasdaq Rejection Block botu"
   git branch -M main
   git remote add origin https://github.com/KULLANICI_ADINIZ/REPO_ADINIZ.git
   git push -u origin main
   ```
2. **API Anahtarlarını Ekleyin (Opsiyonel - Otomatik Emir İçin)**:
   * GitHub reponuzda **Settings** → **Secrets and variables** → **Actions** sayfasına gidin.
   * **New repository secret** butonuna basarak şunları ekleyin:
     * `ALPACA_API_KEY`: Alpaca API anahtarınız
     * `ALPACA_SECRET_KEY`: Alpaca Secret anahtarınız
   *(Not: `.gitignore` dosyamız `.env` dosyanızı GitHub'a yüklemeyi otomatik olarak engeller; API anahtarlarınız asla açıkta kalmaz).*

---

## 2. Yöntem: 7/24 Kesintisiz Bulut Sunucu (Cloud VPS - Profesyonel Canlı Bot)

Eğer seans boyunca (16:30 - 23:00 TSİ) her 5 dakikada bir piyasayı tarayıp anlık emir açıp kapatan **`python main.py live`** motorunun 7/24 kesintisiz çalışmasını istiyorsanız bir bulut sunucu (VPS) en ideal çözümdür.

### Önerilen Sunucular:
* **Oracle Cloud (Always Free)**: Ömür boyu **tamamen ücretsiz** 4 Core ARM işlemci, 24 GB RAM ve 200 GB disk verir.
* **Hetzner Cloud**: Aylık ~3.5 € (Avrupa'nın en hızlı ve ucuz sunucusu).
* **DigitalOcean / AWS EC2**: Aylık $4-5 veya 1 yıl ücretsiz AWS Free Tier.

### VPS'te Tek Komutla Çalıştırma (Docker ile):
Sunucunuza (Ubuntu Linux) SSH ile bağlandıktan sonra:

```bash
# 1. Depoyu klonlayın
git clone https://github.com/KULLANICI_ADINIZ/REPO_ADINIZ.git
cd REPO_ADINIZ

# 2. .env dosyanızı oluşturun
cp .env.example .env
nano .env  # Alpaca anahtarlarınızı yazın (Ctrl+O Enter, Ctrl+X)

# 3. Docker ile arka planda kesintisiz başlatın
docker compose up -d
```

### Neden VPS + Docker?
* **Otomatik Yeniden Başlatma (`restart: unless-stopped`)**: Sunucu yeniden başlasa veya internet gitse bile bot otomatik olarak kaldığı yerden devam eder.
* **Kalıcı Veri (Volume Mount)**: İşlem kayıtları (`reports/journal.csv`), günlük durumlar ve geçmiş veri arşivi sunucu diskinde güvenle saklanır.
* **Sıfır Donanım Yükü**: Bilgisayarınızı kapatabilir, tatile çıkabilirsiniz; bot borsa açıldığında otomatik analiz yapıp emirleri yönetir.
