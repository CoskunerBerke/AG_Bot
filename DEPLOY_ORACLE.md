# DMC botunu Oracle Cloud (Always Free) sunucusunda 7/24 çalıştırma

Bot (`python main.py dmc-run`) Docker içinde sürekli çalışır. Piyasa kapalıyken uyur, açılışta kendini hazırlar,
her dakika kapanan QQQ barıyla Kaanermi 10AM + Lunch kurallarını uygular, 15:55 ET'de pozisyonları kapatır.
Hesap: Alpaca **paper**. Kurallar: [knowledge/STRATEGY_SPEC.md](knowledge/STRATEGY_SPEC.md).

## 1. Sunucuyu aç (Oracle konsolu — bir kerelik)
1. https://cloud.oracle.com → hesap aç (kart doğrulaması istenir, Always Free kaynaklar ücretsizdir).
2. **Compute → Instances → Create instance**
   - Image: **Canonical Ubuntu 24.04**
   - Shape: **VM.Standard.A1.Flex** (1 OCPU / 6 GB) — "out of capacity" derse **VM.Standard.E2.1.Micro** (AMD, Always Free)
   - **Add SSH keys → Generate a key pair for me → Save private key** (bu dosyayı sakla)
3. Oluşunca **Public IP address**'i not et. Gelen port açmaya gerek yok (bot sadece dışarı bağlanır).

> Not: Oracle, 7 gün boyunca CPU kullanımı çok düşük kalan Always Free sunucuları geri alabiliyor. Bot az CPU kullanır;
> bunu önlemek için hesabı **Pay As You Go**'ya yükselt (Always Free limitler içinde kaldıkça ücret çıkmaz).

## 2. Bağlan ve kur
Windows PowerShell'de (anahtar dosyasının yolunu kendi dosyanla değiştir):
```
ssh -i C:\Users\berke\Downloads\ssh-key.key ubuntu@<PUBLIC_IP>
```
Sunucuda:
```
curl -fsSL https://raw.githubusercontent.com/CoskunerBerke/AG_Bot/main/deploy/oracle_setup.sh | bash
nano ~/AG_Bot/.env        # ALPACA_API_KEY ve ALPACA_API_SECRET'i yaz (paper anahtarları), Ctrl+O, Enter, Ctrl+X
curl -fsSL https://raw.githubusercontent.com/CoskunerBerke/AG_Bot/main/deploy/oracle_setup.sh | bash
```
İkinci çalıştırma Docker imajını derler ve botu başlatır. Sunucu yeniden başlasa da bot kendiliğinden kalkar
(`restart: unless-stopped`).

## 3. Günlük kullanım (sunucuda)
| İş | Komut |
|---|---|
| Canlı log | `sudo docker compose -f ~/AG_Bot/docker-compose.yml logs -f` |
| Bugünün dosya logu | `tail -f ~/AG_Bot/logs/dmc_live.log` |
| İşlem günlüğü | `cat ~/AG_Bot/state/dmc_journal.json` |
| Güncelle (GitHub'daki son kod) | aynı `curl ... oracle_setup.sh \| bash` komutu |
| Durdur / başlat | `cd ~/AG_Bot && sudo docker compose stop` / `sudo docker compose up -d` |
| Bir günü yeniden oynat | `cd ~/AG_Bot && sudo docker compose run --rm nasdaq-bot python main.py dmc-scan --date 2026-10-12` |

## Güvenlik
- `.env` sadece sunucuda durur (chmod 600), repoya girmez.
- Aynı Alpaca hesabında başka bir botu (eski `start_live.bat`, `watch`, `smart-trade`) **çalıştırma** — DMC pozisyonlarına müdahale eder.
  `config.yaml → legacy_enabled: false` bu komutları zaten engeller.

## İkinci bot (Bot B: 10AM sadece izlenir)

Bot A (`.env`) tüm modellerle işlem yapar; Bot B aynı kod ve ayarlarla ama `watch_setups: [ten_am]` ile çalışır
(10AM işlemleri açılmaz, sadece loga "İZLEME" olarak yazılır). İki bot ayrı **Alpaca paper hesabı** kullanmalıdır,
yoksa aynı semboldeki pozisyonlar birbirine karışır.

1. Alpaca panelinde sol üstteki hesap menüsünden yeni bir **paper** hesap aç, o hesap için API anahtarı üret.
2. Sunucuda `nano ~/AG_Bot/.env.b`:
   ```
   ALPACA_API_KEY=...
   ALPACA_API_SECRET=...
   ALPACA_BASE_URL=https://paper-api.alpaca.markets
   ```
3. `curl -fsSL https://raw.githubusercontent.com/CoskunerBerke/AG_Bot/main/deploy/oracle_setup.sh | bash`
   → `.env.b` varsa Bot B de başlar (`nasdaq_trading_bot_b`, log: `logs/dmc_live_b.log`, durum: `state/b/`).
4. Yerel panelde görmek için bilgisayardaki `.env`'e `ALPACA_B_API_KEY=` ve `ALPACA_B_API_SECRET=` ekle.

## Bakımsız çalışma (Claude olmadan)

Botlar sunucuda kendi başına çalışır: Docker açılışta başlar, konteynerler çökerse yeniden başlar, her seans
sonunda **günlük rapor** `~/AG_Bot/reports/gunluk_<a|b>_<gün>.txt` dosyasına yazılır (Telegram ayarlıysa telefona da gelir).

**Telegram'dan rapor ve hata uyarısı almak (bir kez, 3 dk):**
1. Telegram'da `@BotFather` → `/newbot` → isim ver → verdiği **token**'ı kopyala.
2. Yeni botuna herhangi bir mesaj at (ör. "merhaba").
3. Tarayıcıda `https://api.telegram.org/bot<TOKEN>/getUpdates` aç → `"chat":{"id":123456789` → bu sayı **chat id**.
4. Sunucuda `nano ~/AG_Bot/.env` ve `nano ~/AG_Bot/.env.b` dosyalarının ikisinin de sonuna ekle:
   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   ```
5. `curl -fsSL https://raw.githubusercontent.com/CoskunerBerke/AG_Bot/main/deploy/oracle_setup.sh | bash`

**Sık işler (sunucuda):**
- Durum: `sudo docker ps` (iki satır "Up" olmalı) · Son raporlar: `ls ~/AG_Bot/reports/ && cat ~/AG_Bot/reports/gunluk_a_*.txt | tail -n 20`
- Canlı log: `tail -f ~/AG_Bot/logs/dmc_live.log` (Bot B: `dmc_live_b.log`) · Yeniden başlat: kurulum komutunu tekrar çalıştır.
- Botları durdur: `cd ~/AG_Bot && sudo docker compose --profile b stop` · Başlat: kurulum komutu.
- **Bot B'deki bir taktiği Bot A'ya taşımak:** GitHub'da `config.b.yaml`'daki satırın ayarını `config.yaml`'da aynı
  yere yaz (ör. `dmc_rb` içine `min_wick_frac: 0.5`), `config.b.yaml`'dan sil, kaydet (commit) → kurulum komutu.

**Oracle'ın boşta VM'yi geri alma riski:** Always Free hesaplarda 7 gün boyunca CPU/ağ/bellek kullanımı %20'nin
altında kalan VM'ler geri alınabilir; bu bot çok az kaynak kullanır. Oracle panelinde hesabı **Pay As You Go**'ya
yükseltmek bunu engeller; ücretsiz kotada kaldıkça ücret çıkmaz.
