# Kural analizi (2026-10-11) — Kaan'ın gerçek işlemleri ve 1 yıllık NQ backtest'i

Ölçütler:
- **Uyum:** Kaan'ın 44 gerçek işlemiyle karşılaştırma (aynı yön / aynı model).
- **Backtest:** 2025-10 → 2026-10 NQ, 1 dk bar. Kayma 0.15 bps, dolum kuralları muhafazakâr.

## Giriş kuralları (uyum)
| Kural | Aynı yön | Aynı model | Genel | Karar |
|---|---|---|---|---|
| Mevcut | 17/44 | 5/44 | %38 | — |
| RB üst/alt zaman dilimi kesişmesi zorunlu | 12/44 | 5/44 | %28 | kapalı (düşürdü) |
| Kaanermi breaker modeli 09:30–11:30 | 17/44 | 4/44 | %38 | kapalı (fark yok) |
| Kaanermi + fresh MSNR temas tetiği | 17/44 | 4/44 | %38 | kapalı (fark yok) |
| 10AM: MSNR seviyesi PDA olarak | 16/44 | 5/44 | %36 | kapalı (düşürdü) |
| 10AM: STDV −1/−2/−2.5 manipülasyon geçerliliği | 17/44 | 5/44 | %38 | kapalı (fark yok) |

## Yönetim ve SMT (backtest, 387 işlem)
| Kural | Toplam R | PF | Karar |
|---|---|---|---|
| Mevcut | −6.8 | 0.98 | — |
| 1R'de BE | −12.2 | 0.95 | kapalı |
| Sıkışma çıkışı (10 dk içinde 0.5R yoksa) | −11.0 | 0.96 | kapalı |
| TP1 sonrası 5 dk swing takibi | −7.1 | 0.98 | kapalı (fark yok) |
| TP1 = pozisyonun 1/3'ü | −8.8 | 0.97 | kapalı |
| **Lunch'ta SMT şartı (NQ↔ES, canlıda QQQ↔SPY)** | **−4.2** | **0.99** | **açık** |

Not: Bütün kurallar kodda duruyor ve `config.yaml`'dan açılabilir. Yeni video ve Telegram işlemi geldikçe yeniden ölçülecek.
