# Kaanermi (DMC) İşlem Botu

Kaan'ın (Kaanermi) YouTube ve Telegram'da anlattığı modelleri Alpaca **paper** hesabında uygular:
**10AM modeli**, **Rejection Block (DMC)** ve **NY Lunch reversal**. Kaan NQ vadeli işlem yapar; Alpaca'da vadeli
olmadığı için canlıda karşılıkları **QQQ** (NQ) ve **SPY** (ES) kullanılır. Stoplar Kaan'ın puanlarından (10–25 NQ
puanı) her enstrümanın 1 dk mum boyuna oranlanarak hesaplanır.

## Kaynaklar
- `knowledge/` — transkriptler (repoda yok), video kare notları (`notes/`), Kaan'ın tarihli işlemleri (`fixtures/`)
- `knowledge/fixtures/kaan_telegram.yaml` — Telegram "Bing-Bongs" işlem özetlerinden çıkarılan 46 gerçek işlem

## Komutlar
```
python main.py dmc-fidelity --set symbol=NQ.v.0   # botun kararları vs Kaan'ın gerçek işlemleri (NQ verisiyle)
python main.py dmc-backtest --start 2024-11-01    # geçmiş test
python main.py dmc-scan --date 2026-10-07         # bir günü bot gözüyle yeniden oynat
python main.py dmc-run [--dry-run]                # canlı yürütücü (QQQ + SPY)
python main.py tg-login | tg-sync                 # Kaan'ın Telegram işlem özetlerini topla
python main.py status                             # hesap durumu
```

## Yapı
| Modül | Görev |
|---|---|
| `nasdaq_bot/dmc/structure.py` | swing, FVG, Rejection Block, OTE, CISD, STDV |
| `nasdaq_bot/dmc/levels.py` | gün seviyeleri: PDH/PDL, premarket, Asya/Londra, TDO, gece yarısı, HTF FVG/RB |
| `nasdaq_bot/dmc/setups.py` | 10AM (retest + OTE/PDA), RB, Lunch, evrensel model — long kodu, short aynalanarak |
| `nasdaq_bot/dmc/engine.py` | gün motoru: tatil/haber filtreleri, günlük limitler |
| `nasdaq_bot/dmc/backtest.py` | 1 dk barlarda muhafazakâr dolum simülasyonu |
| `nasdaq_bot/dmc/live.py` | Alpaca yürütücü: bracket TP1/TP2, BE, 15:55 kapanış, çoklu sembol |
| `nasdaq_bot/dmc/fidelity.py` | Kaan uyum testi |

Sunucu kurulumu: [DEPLOY_ORACLE.md](DEPLOY_ORACLE.md). Yatırım tavsiyesi değildir; yalnızca paper hesapta test içindir.
