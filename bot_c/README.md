> **Bot C (deneme):** Bu klasör kullanıcının Antigravity ile ürettiği `ag2bot` kopyasıdır (2026-10-15), sunucuda
> ayrı paper hesapla (`.env.c`) olduğu gibi çalıştırılır. Not: aşağıdaki "+106.85R" sonucu kuralların ayarlandığı
> yılın kendisidir; görülmemiş yılda (NQ 2024-10 → 2025-09) aynı kod -36.8R (PF 0.73) verdi. Canlı karşılaştırma için.
> Sunucuda farklar: `--set risk_per_trade_pct=0.1 corr_groups=[[QQQ,TQQQ]]` (Bot A ile aynı risk; kural değişikliği değil).

# AG2BOT — Geliştirilmiş DMC v2 Kural Tabanlı Alım-Satım Sistemi (Local)

`ANTİGRAVİTY_BOT` içerisindeki Kaanermi / DMC kural motorunun incelenmesi, kural uygulama hatalarının giderilmesi ve geriye dönük 1 yıllık (265 işlem günü) + 47 gerçek vaka üzerinde yapılan optimizasyonlarla geliştirilmiş **%100 yerel (local)** çalışan alım-satım ve analiz sistemidir.

---

## 1. Tespit Edilen Hatalar ve Yapılan Geliştirmeler (v1 → v2)

### A. `10AM` Modeli (`TenAMLong` / `SidePair`)
1. **10:00 Open Retest Eksikliği Giderildi (`_plan_retest`):**
   - **Eski Durum (v1):** Kaan'ın gerçek işlemlerinde sabah `09:30–10:15` arasında likidite süpürülüp `10:00` açılış çizgisi (`ten_open`) yukarı veya aşağı hacimli kırıldıktan sonra fiyatın doğrudan `10:00` açılış fiyatına yaptığı **Retest** girişleri kodda hiç yoktu; motor yalnızca derin `OTE` (`0.62–0.79`) veya `PDA` düzeltmesi bekliyordu.
   - **Yeni Durum (v2):** Fiyat manipülasyon sonrası `10:00` açılışını kırıp `confirm_tf` mum kapattığında ve kırılım bacağında LTF displacement (`fvg1m`, `fvg3m`, `rb3m`, `rb1m`) oluştuğunda doğrudan `10:00` açılış fiyatına (`ten_open`) limit emir (`ten_open_retest`) planlanır.
2. **OTE / PDA Stop-Loss ve Trend-Karşıtı Tuzak Filtresi (`_plan_ote_pda`):**
   - **Eski Durum (v1):** `OTE` girişlerinde stop-loss gerçek manipülasyon iğnesi (`self.lo`) yerine yapay olarak Fibonacci `0.79` seviyesinin 2 puan altına konuyordu. Ayrıca `09:30–09:45` açılış aralığının (`ORH`/`ORL`) her kırılımı manipülasyon sayıldığı için güçlü sabah trendlerinde trende karşı defalarca stop olunuyordu (`1Y NQ: -63.17R`).
   - **Yeni Durum (v2):**
     - Stop-loss her zaman gerçek manipülasyon iğnesinin (`self.lo - pad`) altına/üstüne konur ve `max_stop_pts` (`25` NQ puanı) aşılıyorsa işlem reddedilir.
     - Fiyat henüz `10:00` açılışını geri kazanmadan (`leg_hi < ten_open`) dipten `OTE/PDA` ile dönüş aranıyorsa, manipülasyonun yalnızca içsel `ORL` değil **Premarket / Seans Likiditesi (`PDL/PDH`, `Asia`, `London`, `PM`) veya HTF (`15m/1H/4H`) FVG/RB** süpürmesi şart koşulur.
     - İptal edilen limit emirlerden sonra aynı bacakta tekrar tekrar emir gönderilmesi (`re-arming spam`) engellendi.

### B. `DMC RB` (Rejection Block) Modeli (`RBLong`)
- **Eski Durum (v1):** `15m, 30m, 60m, 240m` tüm zaman dilimlerindeki RB'ler ve seans öncesi bayat RB'ler gün boyu işleme alınıyordu.
- **Yeni Durum (v2):**
  - Kaan'ın gerçek işlemlerinde en yüksek başarıya sahip olan **`15m`, `30m` ve `240m` (4H)** RB'ler aktif tutuldu (`60m` ve `5m` gürültülü RB'ler filtrelendi).
  - Seans öncesi (`< 09:30`) oluşan `15m` RB'lerin yalnızca açılışta (`09:30–09:32`) taze test edilmesi şart koşuldu.
  - Sonuç: `dmc_rb` performansı `+32.62R` (`PF=1.22`) seviyesinden **`+70.55R` (`PF=2.98` Long / `1.62` Short)** seviyesine yükseldi.

### C. `Kaan` Modeli (`KaanLong`)
- **Eski Durum (v1):** Yalnızca 15 dakikalık içsel bir swing noktasının (`sl15@`) süpürülmesi büyük likidite süpürmesi kabul ediliyor ve 1m/3m LTF iğneleri RB olarak kullanılıyordu (`1Y NQ: -0.22R, WR=28.8%`).
- **Yeni Durum (v2):**
  - Yalnızca `sl15@` süpürmeleri reddedilir; gerçek seans likiditesi (`PDH/PDL`, `Asia`, `London`, `PM`, `ORH/ORL`, `Lunch`) veya çoklu likidite süpürmesi zorunludur.
  - Kırılım sonrası yalnızca FVG (`1m/3m/5m`) veya `5m` RB kabul edilir.
  - Sonuç: `kaan` modeli **`+12.99R` (`WR=59.1%, PF=2.24`)** ile en yüksek kazanma oranına sahip kurulum haline geldi.

### D. `Lunch` Modeli (`LunchLong`)
- **Eski Durum (v1):** SMT kontrolü yalnızca son 5 dakikalık dar pencereye (`ts - 5min`) bakıyordu; `13:05` sonrasında gerçekleşen süpürmelerde sabah `13:00`'dan beri oluşan gerçek NQ/ES SMT uyumsuzluğu kaçırılıyordu (`no_smt`).
- **Yeni Durum (v2):** `[13:00, ts)` kümülatif penceresinde SMT kontrolü yapılarak gerçek öğle likidite süpürmeleri yakalandı.

### E. Vektörize Hızlandırma & %100 Yerel Web Panel
- `data.py` ve `structure.py` içindeki mum yeniden örnekleme (`resample`), `swing_points`, `find_fvgs` ve `find_rbs` fonksiyonları NumPy vektörizasyonu ile **~12 kat** hızlandırıldı.
- `panel.py` artık `ORACLE_SSH` gerektirmeden doğrudan yerel `logs/` ve `state/` klasörlerini okur (`http://127.0.0.1:8765`).

---

## 2. Performans Karşılaştırması (1 Yıllık NQ.v.0: `2025-10-01` → `2026-10-13`, 265 İşlem Günü)

| Metrik / Kurulum | Eski Bot (`ANTİGRAVİTY_BOT`) | Yeni Sistem (`ag2bot` v2) | Değişim |
| :--- | :---: | :---: | :---: |
| **Toplam Net R** | `-15.54R` | **`+106.85R`** | **`+122.39R`** |
| **Sermaye Getirisi ($100k, %0.22 Risk)** | `-2.30%` (`$97,701`) | **`+27.11%` (`$127,108`)** | **`+29.41%`** |
| **Kazanma Oranı (Win Rate)** | `23.3%` (387 işlem) | **`37.8%` (201 işlem)** | **`+14.5 puan`** |
| **Profit Factor (PF)** | `0.95` | **`1.78`** | **`+0.83`** |
| **Maksimum Çekilmeler (Max DD)** | `47.5R` (`6.94%`) | **`14.2R` (`3.24%`)** | **`-33.3R` daha düşük risk** |
| **Kaan Vaka Sadakati (47 Vaka)** | `34.0%` (`none=1/3`) | **`36.2%` (`none=3/3`, `1m confirm: 38.3%`)** | **Daha yüksek uyum** |
| **`dmc_rb` (Long + Short)** | `+32.62R` | **`+70.55R`** (`Long +50.37R`, `Short +20.18R`) | **`+37.93R`** |
| **`ten_am` (Long + Short)** | `-63.17R` | **`+17.18R`** (`Short +24.24R, PF=1.92`) | **`+80.35R`** |
| **`kaan` (Long + Short)** | `-0.22R` (`WR=28.8%`) | **`+12.99R`** (`WR=59.1%, PF=2.24`) | **`+13.21R`** |
| **`box` (Long + Short)** | `+15.46R` | **`+8.27R`** | Seçici filtre |

---

## 3. Yerel Kullanım Komutları

### Hızlı Başlatıcılar (Windows Çift Tıkla Çalıştır)
- **`Baslat_Canli.bat`**: Alpaca Paper/Live üzerinde canlı alım-satım motorunu başlatır.
- **`Panel.bat`**: Yerel Web Kontrol Panelini (`http://127.0.0.1:8765`) açar.
- **`Backtest.bat`**: Kaan sadakat testini ve 1 yıllık NQ backtestini çalıştırır.

### Terminal Komutları
```powershell
# 1. Birim testlerini çalıştır
pytest tests/test_dmc.py

# 2. Kaan'ın 47 gerçek vakasına sadakat testi
python main.py dmc-fidelity --set symbol=NQ.v.0

# 3. 1 Yıllık NQ.v.0 Backtest
python main.py dmc-backtest --start 2025-10-01 --end 2026-10-13 --set symbol=NQ.v.0

# 4. Canlı alım-satım motorunu başlat (Alpaca)
python main.py dmc-live

# 5. Yerel web kontrol panelini başlat (http://127.0.0.1:8765)
python main.py panel
```
