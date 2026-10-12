# Sıfırdan Trading #1–#4 — kodlanabilir öğrenimler (kendi özetimiz)

Kaynaklar: "SIFIRDAN TRADING #1 - Day Trading Nedir / TradingView Kurulumu", "#2 - Mum İşleyişi ve Swing Noktalar",
"#3 - Temel Market Yapısı (Yakıt Sistemi)", "#4 - Demo Hesapta Temel Pratik". Başlangıç serisi; çoğu temel bilgi.
Kareler: #2 9:10–17:08, #3 3:20–9:00 (EURUSD 1H/günlük çizimler, swing göstergesi ayarları).

## Kurallar
1. ✓ **Swing (fraktal):** 3 mum; ortadaki mumun tepesi sağındaki ve solundakinden yüksekse swing high, dibi
   alçaksa swing low. Bot: `structure.swing_points(k=1)` birebir bu.
2. **Dış yapı > iç yapı:** "Ortadakiler bizim için çok önemli değil, dışsal yapıdakiler önemli." Kurala uyan her
   küçük fraktal değil, çevresinde en belirgin olan swing alınır. Kaan'ın önerdiği gösterge ayarı: swing high/low
   strength = 5 (her iki yanda 5 mum). Bot: `dmc.swing_k` ile ölçüldü (bkz. kural_analizi).
3. **Swing'ler yüksek zaman diliminde daha anlamlı:** 4H, 3H, 2H, 1H, 45, 30, 15 dk.
4. ✓ **Likidite = swing = yakıt:** Smart money bir swing likiditesini alır (yakıt toplar), sonra sıradaki
   alınmamış swing'e gider. Fiyat swing'den swing'e ilerler. Alınmamış swing'ler hedeftir.
   Bot: alınmamış 1 saatlik swing'ler `DayLevels.htf_sh/htf_sl`; hedef olarak `setups.<ad>.htf_swing_tp`.
5. ✓ **Displacement:** Sıkışık/yavaş giden fiyatın ardından aniden gelen büyük, hacimli mum(lar). Bot: kırılım
   bacağında FVG oluşması (zaten var).
6. **TP = swing noktası** (#4 demo örneği): long'da hedef yukarıdaki swing, stop aşağıda.
7. **HTF kontrolü** (#1): Girişten önce 4H'e bak; yukarıda önemli bir seviye varsa daha temkinli ol.
   Bot: Lunch'taki HTF FVG vetosu bunun bir hali.
8. ✓ **Haberler (ForexFactory):** sarı önemsiz, turuncu bazen önemli, kırmızı çok önemli. Bot: kırmızı USD
   haberinin ±5 dk'sı engelli.
9. Pratik: TradingView paper trading, düşük kaldıraç (5–10); "ekran süresi" ile göz aşinalığı.

## Bilgi
- Kaan 16 yaşında, 2 yıldır trade ediyor (Şubat 2026 itibarıyla). Seri "Lunch stratejisinde bir eksik var" diyenler için.
- Pariteler: EURUSD, NAS100, GBPUSD, SPX500 (FXCM verisi).

## Ölçüm (1 yıl NQ, 2025-10 → 2026-10; taban +0.3R, PF 1.00, 392 işlem)
| Varyant | Sonuç | Karar |
|---|---|---|
| Gün içi swing gücü k=2 (dış yapı) | -12.1R, PF 0.96 | kapalı — Kaan'ın açık kuralı k=1 (3 mum) daha iyi |
| Gün içi swing gücü k=3 | -13.4R, PF 0.96 | kapalı |
| Alınmamış 1H swing'ler hedef (10AM+RB+Kaan), k=2 veya 5 | +0.3R (değişmedi) | kapalı — 3 ayda sadece 2 kez seçildi; PDH/ONH/PMH gibi seans seviyeleri zaten aynı likiditeyi kapsıyor |
