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
