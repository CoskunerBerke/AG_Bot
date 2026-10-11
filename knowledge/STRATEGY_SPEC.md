# STRATEGY_SPEC — Kaanermi / DMC metodolojisinin bot kuralları

> Kaynak: `knowledge/transcript*.txt` (bkz. README). Bu dosya transkriptlerin **kendi cümlelerimizle** makineye çevrilmiş özetidir.
> Etiketler: **[K]** = videoda açıkça söylenen kural · **[Y]** = bizim yorumumuz (videoda sezgisel/belirsiz) · **[B]** = bot/Alpaca uyarlaması.
> Kodda her parametre `config.yaml → dmc:` altında, buradaki adla bulunmalı. Spec değişmeden kod değişmez.

---

## 0. Felsefe (tüm setup'lar için geçerli)

1. **[K] Az ama temiz işlem.** Market işlem vermiyorsa zorlanmaz; "işlemi market versin". Haftalık hedef birkaç R; frekans bilinçli olarak düşük.
2. **[K] En az 3 confluence.** Bir girişte en az üç bağımsız sebep çakışmalı (ör. 10AM açılış çizgisi + OTE bölgesi + bir PD array: OB / FVG / RB / MSNR). İki confluence = "deneyimli işi", varsayılan olarak alınmaz.
3. **[K] HTF bias arkada olmalı.** Bias'a ters işlem sadece Lunch reversal'ın tanımı gereği ve güçlü sebeple.
4. **[K] Manipülasyonu anla, tersine gir.** Gün: Accumulation → Manipulation → Distribution (AMD). Seans açılışındaki ilk sert hareket çoğunlukla manipülasyondur; giriş, manipülasyonun bittiği yerden dönüşte aranır.
5. **[K] Stop dar, hedef geniş.** Stop yapısal ve küçük (NQ'da 10–20 puan); hedef karşı taraftaki likidite. Tipik R:R 5–10.
6. **[K] Mum kapanışı bekle.** Dönüş sinyali için 1 dk değil 3 dk (10AM'de ilk 5 dk) mum kapanışı beklenir; limit emir ancak bölge çok temizse ve manipülasyondan eminsen.

---

## 1. Tanımlar (kodda deterministik olmalı)

| Kavram | Bot tanımı |
|---|---|
| **Swing high/low** | [K] 3 mum fraktalı: ortadaki mumun high'ı (low'u) iki komşusundan yüksek (düşük). [K] *Dış (external)* yapıdaki belirgin swing'ler önemlidir, iç yapıdaki küçük olanlar değil. [Y] Önem skoru = fraktal genişliği (n=2…5) + sonrasında gelen displacement büyüklüğü. |
| **Displacement** | [K] Sıkışmadan sonra gelen büyük gövdeli, hacimli mum(lar). [Y] Gövde ≥ 1.5 × ATR(20) **veya** art arda ≥2 aynı yönlü mum toplam gövde ≥ 2 × ATR; ve FVG bırakması. |
| **FVG** | 3 mum boşluğu: bullish ⇔ `low[i] > high[i-2]`, bearish ⇔ `high[i] < low[i-2]`. Dolum: fiyat boşluğun tamamını kapatınca "full filled", geçersiz. |
| **IFVG** | [K] Gövde ile ters yönde kapatılan FVG; artık ters yönde destek/direnç. |
| **Order Block (OB)** | Displacement öncesi son ters renkli mum (bullish OB = yükselişten önceki son düşüş mumu). [K] OB **gövde ile** kırılırsa → **Breaker Block**. |
| **Rejection Block (RB)** | [K] Likidite süpüren bir swing mumunun **gövde ucundan** (open/close) **iğne ucuna** kadar olan bölge; giriş gövde seviyesinden, stop iğnenin ötesine. [K] RB'nin **orta noktası (C-level / %50)** stop'u daraltmak için kullanılabilir. Geçerlilik: (a) bir likiditeyi süpürmüş olmalı, (b) swing noktasında olmalı (range ortasındaki "ara" RB geçersiz), (c) iğne aşılır / gövde kapanışla geçilirse silinir. [K] **RB içinde RB** (HTF RB'nin içine düşen LTF RB) ve **FVG'ye dokunup OB ile çakışan RB** en kaliteli olandır. [K] Bir kez kullanılmış RB, orta noktası dokunulmamışsa C-level'dan tekrar kullanılabilir. |
| **MSNR** | [Y] Gövde-tabanlı destek/direnç (Malaysian SnR): ardışık mumların close/open'larının yoğunlaştığı seviyeler; fiyatın daha önce gövdeyle tepki verdiği yatay seviye. |
| **Likidite** | Swing high/low'lar; equal / relative equal high-low; önceki gün H/L (PDH/PDL); seans H/L (premarket, NY AM, Lunch); haber mumunun H/L'si ("data high/low"); wicksiz mum açılışları. [K] **Low resistance liquidity**: önünde PD array olmayan, "temiz" alınabilecek likidite → hedef olarak tercih edilir. |
| **OTE** | Manipülasyon/impulse bacağına çekilen fib: **0.62–0.79** bölgesi, 0.705 merkez; **0.5 (equilibrium)**. [K] Stop 0.79'un ötesine ("79'u coverla"). [K] Bacak 0.5'ini dengelemeden kırılırsa o bacak geçersiz, yeni bacaktan OTE çekilir. |
| **MSS / CISD** | Likidite süpürmesinden sonra karşı yöndeki son swing'in gövdeyle (displacement'la) kırılması. |
| **Premium / Discount** | Aktif range'in 0.5 üstü premium (long yok), altı discount (short yok). |
| **Accumulation** | [Y] Son N mumun range'i < k × ATR (sıkışma). Bir seansın "akümüle geçmesi" = seans range'i kendi 20 günlük ortalamasının < %60'ı. |

---

## 2. Zaman (tamamı America/New_York; DST otomatik)

| Blok | Saat (NY) | Kullanım |
|---|---|---|
| Premarket / önceki seanslar | 04:00–09:30 | [B] Asya/Londra likiditesinin hisse karşılığı: premarket H/L |
| NY Open | 09:30 | [K] **İşlem yok**, sadece izleme (09:30 manipülasyonu = "Judas") |
| **Hazırlık** | 09:30–10:00 | [K] Seviyeleri işaretle, manipülasyonu izle |
| **10AM (TNM)** | 10:00 açılış; [Y] setup penceresi 10:00–11:30 | Setup A |
| NY Lunch | 12:00–13:00 | [K] Lunch range oluşur |
| **Lunch reversal** | [K] 13:00'te başla, **en fazla 30 dk** bekle → 13:00–13:30 | Setup D |
| NY PM | 13:30–16:00 | [Y] Sadece açık pozisyon yönetimi |
| Zorunlu kapanış | [B] 15:55 | Gün içi pozisyonlar düzlenir (day trade) |

---

## 3. HTF bias prosedürü (her gün 09:25'te)

[K] Yukarıdan aşağı: W → D → 4H → 1H → 30m → 15m → 5m → 1m. Her seviyede kaydet: önemli likidite, açık FVG'ler (özellikle **dolmamış günlük gap'ler**), OB/RB, MSNR.
[Y] Bias kuralı (bot):
- Günlük ve 4H'te son geçerli MSS yönü + fiyatın dolmamış en yakın günlük FVG'ye göre konumu → `bias ∈ {long, short, neutral}`.
- Önceki günün mumu gövdeyle bearish kapandı ve altında dolmamış bullish gap'ler varsa → short eğilim (hedef o gap'ler) [K örneği].
- Neutral/yatay günlerde sadece manipülasyon tersine işlem; ek confluence şartı +1.

---

## 4. Setup A — 10AM (TNM) modeli  [ana setup]

**Hazırlık [K]:** 09:30'da seviyeler çizilir; 09:30–10:00 arası işlem yok.

**10AM çizgisi [K]:** 10:00 1 dk mumunun **açılış fiyatı** yatay çizgi (`ten_open`). Bu çizgi gün içi "draw on liquidity" (DOL) olarak kullanılır.

**Wicksiz açılış ("devil mark") [K]:** 10:00 mumu bir tarafı iğnesiz açılır ve o yönde giderse → o yöndeki hareket **manipülasyondur**, fiyatın `ten_open`'a geri dönmesi beklenir.

**İki giriş tipi [K]:**
1. **Manipülasyon dönüşü:** Fiyat `ten_open`'dan uzaklaşan manipülasyon bacağını tamamlar, bir PD array'de (RB / OB / FVG / MSNR / Kaanermi modeli) dönüş verir → `ten_open` yönüne giriş; TP1 = `ten_open`, TP2 = arkadaki likidite.
2. **Kırılım-retest:** Fiyat `ten_open`'ı **displacement ile** kırar → retest'inde (OTE + PD array çakışması) kırılım yönünde giriş.

**Onay [K]:** 10:00'dan sonraki **ilk 5 dk mumun kapanması** beklenir (ilk manipülasyonu görmek için). Dönüş sinyali 3 dk kapanışla teyit edilir.

**Confluence [K]:** `ten_open` + OTE (0.62–0.79) + en az bir PD array (OB/FVG/RB/MSNR) ⇒ ≥3. Manipülasyon bacağı bir **FVG'ye dokunmadıysa manipülasyon geçersiz** sayılır [K].

**Geçersizlik [K]:**
- `ten_open` birden fazla kez iki yönlü kırıldıysa (kararsızlık/chop) → o gün 10AM'e bakmayı bırak.
- Fiyat `ten_open`'a hiç dönmeden uzaklaşıp gidiyorsa → işlem yok (böyle günler olur).
- 10:00'da önemli haber varsa reaksiyonu izle; sürekli devam eden konuşma tipi haber (Fed konuşması vb.) → o gün pas.

**Limit emir [K]:** Sadece (a) manipülasyon net, (b) `ten_open` + OTE + PD array birebir çakışıyorsa. [Y] Bot varsayılanı: limit emir **yalnız** confluence ≥4'te; aksi halde 3 dk kapanış sonrası market/limit-at-close.

---

## 5. Setup B — Kaanermi modeli (breaker + OTE)

[K] Sıra:
1. **Önemli likidite alınır** (belirgin swing low/high süpürülür) → fiyata "yakıt" girer.
2. Süpürme bölgesinde bir **OB** oluşur; fiyat bu OB'yi **gövdeyle** kırar → **breaker block**.
3. Kırılım yukarıdaki bir **FVG**'ye dokunur (bir hedef alınır).
4. Fiyat breaker'a geri çekilir:
   - **Doğrudan giriş:** breaker block'tan (stop breaker'ın ötesi).
   - **Akümülasyon olursa:** akümülasyonun altını (üstünü) manipüle edip impulse bacağının **OTE**'sine iner → OTE'den giriş.
5. **Hedef:** en uç karşı likidite ("market maker TP'sini en tepeye koyar").
[K] Breaker'a ikinci dokunuşta pozisyona ekleme yapılabilir ("kontrat ekle"). [B] Bot: v1'de ekleme yok.

---

## 6. Setup C — Rejection Block (DMC) girişi

[K]
- HTF'de (1H/15m) geçerli RB işaretlenir (bkz. tanım). LTF'de (5m/3m/1m) aynı bölgede RB/FVG/MSNR çakışması aranır.
- Giriş: RB gövde seviyesi (veya içindeki LTF RB'nin gövdesi). Stop: iğne ötesi; RB büyükse orta nokta (C-level) ötesi.
- **RB manipülasyonu:** fiyat RB'ye iğneyle girip yeni bir high/low yapar (turtle soup) ve hemen geri dönerse → sonraki 1 dk mum kapanışında giriş.
- RB seçildiyse **fiyat hemen dönmeli**; RB içinde uzun akümülasyon kötü işaret → stop'u BE'ye çek ya da çık.
- [K] Kaan'ın favorisi: 4H RB + FVG tap + OB çakışması.
- [B] Setup C, tek başına değil **A/B/D içinde PD array** olarak ya da HTF bias yönünde bağımsız setup olarak çalışır (bağımsız mod `dmc.rb_standalone: false` varsayılan).

---

## 7. Setup D — NY Lunch reversal

[K] Faktörler (önem sırasıyla): (1) HTF bias, (2) Lunch'ın kendi yönü/hacmi, (3) NY AM'in nasıl geçtiği, (4) low resistance liquidity, (5) **5 dk iğne**, (6) önemli PD array'ler.
- **Mantık:** Lunch (12:00–13:00) range'i oluşur. Lunch high'ı ya da low'u (veya NY AM high/low'u) **5 dk mumda iğneyle süpürülür** → **bir sonraki 5 dk mumda ters yöne** giriş (reversal).
- NY AM akümüle (yatay) geçtiyse hacim Lunch/PM'e saklanır → artı puan, şart değil.
- **Stop:** süpürme iğnesinin hemen ötesi, **dar**. [K] Gerekçe: fiyat iğnenin ötesine geçtiyse strateji zaten çalışmamıştır; geniş stop sadece kaybı büyütür.
- **Hedef:** bias'a göre lunch range'in karşı ucu, ötesinde NY AM high/low veya low resistance liquidity; arada önemli PD array varsa TP1 orada.
- **Filtre [K]:** Süpürmenin karşı tarafında 15m/30m/45m'de yakın bir PD array (ör. 15m FVG) varsa 5 dk iğneye güvenme → pas.
- **İğne yoksa [K]:** Lunch süpürmesi net değilse genel yapı kullanılır: akümülasyon likiditesi alınmış + 1 dk'da MSS → giriş (bu durumda confluence ≥3 şartı).
- Pencere: 13:00–13:30; 13:30'a kadar tetik yoksa o gün Lunch iptal.

---

## 8. Risk ve işlem yönetimi

| Kural | Değer |
|---|---|
| Stop | [K] Yapısal (RB iğnesi / OTE 0.79 ötesi / süpürme iğnesi). NQ'da 10–20 puan tipik, ~37 puan "fazla". [B] QQQ'ya oran: **%0.03–0.07**, üst sınır `dmc.max_stop_pct = 0.12%`; yapısal stop bunu aşıyorsa işlem alınmaz. |
| Pozisyon büyüklüğü | [B] Mevcut `risk.size_position` (sabit %R). Varsayılan R = hesap özvarlığının %0.35'i. |
| TP1 | [K] En güvenli yakın likidite (iç high/low, `ten_open`, data high/low). Pozisyonun %50'si. |
| TP2 | [K] Ana hedef: karşı taraftaki büyük likidite. Kalan %50. |
| Breakeven | [K] (a) Lehte displacement + iç high/low kırılınca, ya da (b) TP1 dolunca stop → giriş fiyatı. Riskli girişlerde engulfing görülünce erken BE. |
| Min R:R | [Y] TP2 ≥ 3R değilse işlem yok. |
| Günlük limit | [Y] Günde en fazla 2 işlem; 1 tam stop sonrası o setup kapanır, 2 stop sonrası gün kapanır. |
| Zaman stopu | [B] 15:55'te tüm gün içi pozisyonlar kapanır. |

---

## 9. Gün filtreleri (işlem açmadan önce)

- **[K] Haberler (ForexFactory, USD):** kırmızı = yüksek etki; turuncu = içeriğine bak; sarı = önemsiz. 08:30 kırmızı haber varsa güne erken bak (data high/low hedefi). 10:00'da kırmızı haber varsa Setup A sadece reaksiyon netleşince. Süreklilik gösteren konuşma tipi olaylarda (FOMC basın toplantısı, Fed başkanı konuşması) o pencere pas.
- **[K] ABD tatili** (ör. Labor Day) → hiç işlem yok. Yarım gün → işlem yok [Y].
- **[K] Pazartesi, haber yoksa** → akümülatif olma eğilimi; confluence şartı +1 [Y].
- **[K] Cuma** → haftalık mum kapanışı; salı/çarşamba genelde haftanın high/low'u yapılır. Cuma işlem isteği düşük: [Y] sadece confluence ≥4.
- **[K] Yaz (özellikle Ağustos)** price action zayıf → [Y] günlük maks işlem 1.

---

## 10. Bot uyarlaması [B]

- **Enstrüman:** Kaan NQ/NAS100 (vadeli/CFD) işlem yapıyor. Alpaca vadeli desteklemediği için proxy = **QQQ** (NASDAQ-100 ETF). İsteğe bağlı ikinci enstrüman: SPY (Kaan Lunch'ı S&P'de de gösteriyor). Tekil momentum hisseleri bu metodolojinin parçası değil.
- **Puan → yüzde:** NQ ≈ 30.000 seviyesinde 10 puan ≈ %0.033. Tüm puan değerleri `pct = points / NQ_ref` ile QQQ'ya çevrilir; kodda puan değil yüzde kullanılır.
- **Veri:** Alpaca 1 dk bar (geçmiş için SIP-15dk-gecikmeli, canlı için IEX). 3/5/15/30/45/60/240 dk barlar 1 dk'dan resample edilir (sınır: NY saatine hizalı, `label='left', closed='left'`). Hiçbir sinyal **kapanmamış mum** kullanmaz.
- **Emir tipi:** Limit giriş + bracket (stop + TP). TP1/TP2 için pozisyon iki bracket'a bölünür; BE = stop bacağının `replace` edilmesi.
- **Short:** QQQ short'u gerekiyor (setup'ların yarısı short). Alpaca paper'da `shorting_enabled` kontrol edilmeli.

---

## 11. `config.yaml → dmc:` varsayılanları

```yaml
dmc:
  symbols: [QQQ]
  tz: America/New_York
  nq_ref_price: 30000          # puan→% dönüşümü için
  min_confluence: 3
  limit_entry_min_confluence: 4
  ote: {low: 0.62, mid: 0.705, high: 0.79, eq: 0.5}
  max_stop_pct: 0.0012         # %0.12
  min_rr_tp2: 3.0
  tp1_fraction: 0.5
  max_trades_per_day: 2
  flat_time: "15:55"
  setups:
    ten_am:   {enabled: true,  prep_start: "09:30", open: "10:00", window_end: "11:30", confirm_tf: 5}
    kaanermi: {enabled: true}
    rb_standalone: {enabled: false}
    lunch:    {enabled: true,  range_start: "12:00", range_end: "13:00", window_end: "13:30", sweep_tf: 5}
  filters:
    skip_us_holidays: true
    red_news_block_minutes: 5   # haber anından önce/sonra
    monday_no_news_extra_confluence: 1
    friday_min_confluence: 4
    august_max_trades: 1
```

---

## 12. Açık noktalar (sahibi karar verecek)

1. Sadece QQQ mı, QQQ + SPY mi?
2. Risk/işlem %0.35 kalsın mı (Kaan sabit $ risk kullanıyor)?
3. Mevcut momentum/RB tarayıcısı tamamen kapatılsın mı, yoksa ayrı bir "eski strateji" modu olarak mı kalsın?
4. Setup A dışındaki (B/C/D) setup'lar ilk sürümde açık mı olsun, yoksa önce sadece 10AM + Lunch mı?
