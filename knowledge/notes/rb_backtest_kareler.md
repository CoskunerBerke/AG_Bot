# RB (Rejection Block) Backtest — Kare Analizi

Kaynak: `knowledge/yanlışlıkla para basan strateji buldum canlı backtesting - Kaanermi (1080p).mp4` (23:11)
Transkript: `knowledge/transcript.txt`
Platform: FX Replay, **NQ1 (CME), 1 Kasım 2023 (Çarşamba)**, saat dilimi **UTC-4 (New York)**.
Replay başlangıcı: 11:01:59 NY. Hesap 50.000 $, risk/işlem 750 $ (pozisyon aracında "Amount: 750").

> Okuma notu: Kaan'ın temasında **kırmızı mum = yükselen (bullish)**, **siyah mum = düşen (bearish)**.
> "pip" = NQ'da **1 index puanı** (pozisyon aracı "Stop: 10.00" gösteriyor).
> Her kutuda TradingView dikdörtgeninin **kesikli orta çizgisi (%50)** açık.

---

## 1) Pencere bazlı gözlemler (video mm:ss → replay NY saati)

### 04:50–05:40 — Kurulum / haber
- 04:50: 5m grafik, replay 11:01:59. Fiyat 14.626, sabahtan beri güçlü yükseliş (08:30–11:00 arası ~14.420 → 14.650).
- 05:30: Ekonomik takvim: **14:00 Fed faiz kararı + 14:30 FOMC basın toplantısı**, 10:00 ISM, 08:15 ADP. 14:00'e dikey çizgi koydu. [görüldü]

### 06:10–07:35 — HTF bağlam
- 06:10 Günlük: fiyat Ekim'deki bir bölgeye tekrar geliyor; "tepki alabilir" yorumu.
- 07:00–07:20 4H: gri kutu **14.680–14.845, orta ~14.760** (24 Ekim mumları; günlük/4H order block + FVG bölgesi). Fiyat 14.626 ile kutunun hemen altında.
- 07:30 4H **OTE fib**: 1 = **14.948,75** (24 Eki tepe fitili), 0 = **14.139,25** (26 Eki dip). Seviyeler: 0.5 = 14.544, **0.62 = 14.641,25**, **0.705 = 14.710**. OTE bandı (0.62–0.705) 4H kutunun alt kısmıyla çakışıyor. [görüldü]

### 07:50–08:25 — 45m/30m order block
- 08:10 30m: OB kutusu **14.611,75 – 14.637,25**, orta ~14.624 (25 Eki 10:00 civarı düşüş içindeki küçük konsolidasyon mumu). "Akümüle zon sonrası kendini aşağı atmış" yorumu. [görüldü]
- 07:42: OB içinde ama fiyat "patır kütür" yükseldiği için **işleme girmiyor**. [söylendi]

### 08:30–10:35 — RB #1 (SHORT, "kaçmış" sayıldı) — 15m
- 08:30–08:52 15m: Swing-high mumu **10:45 15m siyah (bearish) mum**. RB kutusu **gövde üstü (open) 14.642,75 → fitil tepesi 14.650**, kısa kesikli orta çizgi. Gövde üstüne beyaz yatay çizgi (14.642,75). Aynı anda 15m **FVG kutusu ~14.545–14.560** (orta ~14.553). [görüldü]
- RB 30m OB'nin (14.611–14.637) hemen üstünde, 4H OTE 0.62 (14.641) seviyesine dokunuyor. [görüldü]
- 09:00–09:20 (5m/1m): 10:45 tepesinden sonra fiyat 10:50–11:00 arası RB altında akümüle oldu, **11:03'te 14.642 civarına ikinci kez geldi**, sonra 14.600'e sert düştü. [görüldü]
- 08:57–09:11: 5m'de "reaction yok" diyor; çünkü fiyat RB altında akümüle oldu ve FVG'ye hemen gitmedi → fiyat geri çekilme ister. [söylendi]
- 09:50–10:10 Pozisyon aracı (3m): **Entry 14.642,75** (RB gövde kenarı), **Stop 14.652,75 = 10,00 puan** (fitil tepesi 14.650'nin 2,75 üstü), ilk TP 14.595, sonra **TP 14.586,50 (56,25 puan)**, **R:R 5,63**, "Closed P&L 56.25". [görüldü]
- 10:00–10:04: "Hadi 10 pip atalım ya... 10 pip ideal." TP = 3m FVG → "5,5–6 R". [söylendi]
- 10:20–10:30: **MSNR çizgisi 14.575** (09:40–09:55 tepeleri; kırılmış direnç). TP bunun ~11,5 puan üstünde. Fiyat 11:24'te ~14.583'e indi, TP doldu. [görüldü]
- Bu işlemi **saymıyor** (hızlı ilerletti, gerçek zamanlı girmedi). [söylendi]

### 11:20–12:05 — RB #2 (LONG, "kaçmış" sayıldı) — 15m
- 11:40 15m: **11:30 15m kırmızı (bullish) mum**: gövde altı (open) **14.590,75**, alt fitil ~**14.576**. Bu fitil **11:15/11:22 dibini (~14.583) süpürdü** ve **MSNR 14.575'e değdi**. Gövde altına beyaz çizgi 14.590,75 (kutu çizmedi). [görüldü]
- 11:47–11:59 1m pozisyon: **Entry 14.591**, **Stop 14.585 = 6,00 puan** (fitil dibi 14.576'nın üstünde → stop RB'nin **yaklaşık ortasında**; RB orta ≈ 14.583,4), **TP 14.613 (22 puan)**, **R:R 3,67**. Fiyat 11:51 retest → 11:55 TP. [görüldü]
- TP = kesikli çizgi ~14.613–14.614 (10:50–11:00 iç dipleri / 5m küçük zon alt kenarı; 30m OB alt kenarı 14.611,75'e yakın). [görüldü]
- "3 arar neredeyse 4 arar" — yine kaçmış sayıyor. [söylendi]

### 12:10–14:35 — RB #3 (SHORT, **gerçekten girildi**, STOP oldu) — 5m RB, 1m giriş
- 12:30–12:50 5m: **11:55 5m kırmızı (bullish) swing-high mumu**: gövde üstü (close) **14.611** → fitil tepesi **14.620,5**. Kutu 14.611–14.620,5, **orta ~14.615,75** (kesikli). Üstte beyaz **MSNR ~14.619,5** (11:00 dipleri). [görüldü]
- 12:48–13:09: Fitilin içi de RB, ama hemen girmiyor; "birazcık geri çekilimle beklemem gerekiyor" — gerekçe: **aşağıda FVG var** ve bölge **MSNR'a** denk geliyor. [söylendi]
- 13:10–13:50 1m: alttaki **FVG kutusu ~14.601–14.607** (beyaz çizgi 14.604,5, 11:48–11:52 bacağı). Fiyat 12:02'de FVG'ye indi (daire içine aldı), sonra 14.611'e geri döndü. "Akümüle alanını manipüle etmiştir." 13:12: "OTE bölgesinde" (fib çizimi görünmedi). [görüldü+söylendi]
- 14:10: ilk pozisyon taslağı Entry 14.611, Stop 14.635 (geniş), sonra daraltılıyor. "Stop olma ihtimalimiz yüksek çünkü rejection bloğumuz çok yüksek." [görüldü+söylendi]
- 14:17–14:42 final: **Entry 14.611** (RB gövde kenarı), **Stop 14.616,25 = 5,25 puan** (RB ortası 14.615,75'in ~0,5 üstü), **TP 14.584,25 (26,75 puan)**, **R:R 5,1**. Giriş ~12:07 NY. [görüldü]
- 15:10–15:30: 12:09'da 1m fitil ~14.616,5'e çıktı → **stop (-5,25)**. Sonra fiyat 12:19'da **14.584'e indi = TP'ye gitti**. [görüldü]
- 15:22–15:32: Normalde stop **10 pip**; **kocaman RB + internal yapı** varsa stop RB'nin ötesinde olmalı (özet). Stop'u **14.620,25'e (9,25 puan, RB fitil tepesinin hemen üstü)** sürükleyip gösterdi → "Closed P&L +26,75, R:R 2,89". [görüldü+söylendi]
- 22:50 (replay 13:13): aynı kutu tekrar test edildi (~14.619'a kadar) ve sert düşüş geldi — "en başta belirlediğimiz... nasıl çalıştı". [görüldü+söylendi]

### 15:40–18:45 — RB #4 (LONG, **RB içinde RB**, TP1 alındı)
- 15:50–16:10 1m: **Dış (HTF) RB** = 11:36 swing-low fitili, kutu **~14.576,3–14.584**, orta ~14.580. Bu dip 11:22 dibini (~14.583) süpürdü, MSNR 14.575'in hemen üstünde. [görüldü]
- 16:00: fiyat 12:20'de dış RB üst kenarına ~14.582,5 ile değip 14.602'ye fırladı. "RB içindeki RB'yi çizelim." [görüldü+söylendi]
- 16:20–16:30 1m: **İç (LTF) RB** = 12:19 1m mumunun alt fitili, **gövde altı 14.585,25 → fitil 14.582,50**, orta ~14.583,9. Beyaz çizgi 14.585,25. [görüldü]
- 16:31–16:40: Emir **limit** olarak konuyor ("Buraya emrimi koyuyorum"), fiyat 12:25–12:28'de geri gelip doldurdu. **Entry 14.585,25**, **Stop 14.582,50 = 2,75 puan** (iç RB fitil dibinin tam altı) — "2.75 stop". [görüldü+söylendi]
- 17:05–17:20: **TP1** = 12:22 lokal tepe (~14.603,5; çizgi 14.605,25 — daireyle işaretli), **TP2 = 14.615,25** (üstteki short RB orta çizgisinin hemen altı; "buradan tekrar tepki alabilir"). Pozisyon aracı: **Target 30,00, R:R 10,91**. [görüldü]
- 17:31–17:36: TP1 alındı, "7 arar" (≈ (14.604,5−14.585,25)/2,75 = 7R). [görüldü+söylendi]
- 17:20: "Riskli noktadayız, fiyat buraya iki kez gelip tepki aldı." [söylendi]
- 17:56–18:30 **BE+ tartışması**: fiyat bölgeye geri gelince "stopu artıya çekeriz"; seviye: **3m/5m'de yükseliş sırasında oluşan küçük RB ~14.589,5** (entry +4,25). Gerekçe: 5–10 dk'dır işlemdeyiz + oradaki LTF RB'den tepki beklenir. [görüldü+söylendi]
- 18:36–18:40: TP2'ye (14.615,25) gitmedi; tepe 14.612 (12:34), sonra tüm RB'leri kırıp 14.566'ya düştü. Sonuç: **−1R (RB#3) + 7R = 6R**. [görüldü+söylendi]

### 18:50–22:45 — RB #5 (LONG, BE'ye çekildi, TP)
- 19:20–19:30 5m: **12:40 5m kırmızı (bullish) mumunun alt fitili**: gövde altı **14.575,25** → fitil dibi **14.562,25**, orta ~**14.568,75**. RB üst kenarı **MSNR 14.575** ile aynı; altında 15m FVG/OB **14.545–14.560**. [görüldü]
- 19:23–19:52: Özet: korkutan şey yakındaki order bloğunun (fiyatı çekebilir) ve MSNR'ın varlığı; "long için tek olan şey rejection blounun olması"; bias biraz ters. [söylendi]
- 20:10–20:27 30s/5m: fiyat 13:03'te 14.575'e tam dokundu, tepki. [görüldü]
- 21:08–21:39 pozisyon: **Entry 14.575,25**, ilk stop 14.567,25 (8 puan, "8 mi koysak?"), final **Stop 14.568,25 = 7,00 puan** (RB ortası ~14.568,75'in ~0,5 altı), **TP 14.609,50 (34,25 puan)**, **R:R 4,89**. TP = üstteki short RB alt kenarının (14.611) ~1,5 altı. [görüldü]
- 21:49–21:59: 13:00–13:06 fiyat RB üstünde birkaç mum **yatay/akümüle** kaldı → "RB seçiyorsam **anında** dönmesi gerekiyor; bu kadar akümüle olmalar iyi değil." [görüldü+söylendi]
- 22:04–22:25: fiyat 14.594'e (~+19 puan, ~2,7R) çıkınca **BE çizgisi ("b.e.") ~14.576,5** (entry +~1,25). "Geri çekilirse seni stop eder → breakeven." [görüldü+söylendi]
- 22:28–22:30: **TP 14.609,50 doldu** (13:13), tepe ~14.619 = short RB içinde. 22:50: short RB'den tekrar sert tepki (13:32'de 14.586). [görüldü]
- Not: Bu işlem için "girmedim" diyor (22:46) — sonuca saymıyor. [söylendi]

---

## 2) Kodlanabilir kurallar

**Tanımlar**
1. **RB (rejection block) kutusu** = swing ekstrem mumunun **fitil kısmı**. Bearish RB (tepe): `top = high`, `bottom = max(open, close)`. Bullish RB (dip): `bottom = low`, `top = min(open, close)`. `mid = (top+bottom)/2` (kesikli orta çizgi). [görüldü — 5 kutunun hepsi]
2. RB'yi çıkaran mum **lokal swing high/low** olmalı (en az 1–2 mum solda/sağda daha düşük tepe / daha yüksek dip). [görüldü]
3. RB TF'si: 15m veya 5m (RB#1, #2: 15m; #3, #5: 5m); dış/iç RB'de 1m. **Giriş 1m'de**. TF taraması 15→7→5→3→1. [görüldü+söylendi]
4. **RB-in-RB**: HTF RB (dış) kenarına dokunup oluşan LTF (1m) RB (iç). Giriş iç RB'nin gövde kenarı, stop iç RB fitili ötesi → çok yüksek R:R (RB#4: 2,75 pt stop, 7–10,9R). [görüldü]

**Geçerlilik / filtre**
5. **Sweep şartı (tercih)**: RB fitili önceki bir swing dip/tepeyi süpürmüş olmalı (RB#2 ve dış RB#4: 11:22 dibi ~14.583 süpürüldü; RB#3: 11:00 dipleri/MSNR 14.619,5'e değdi). [görüldü]
6. **Konfluens (en az biri)**: RB, bir HTF seviyeye denk gelmeli → MSNR yatay seviye (kırılmış direnç/destek), HTF OB/FVG kutusu, 4H OTE bandı (0.62–0.705). RB#1 = 30m OB üstü + OTE 0.62; RB#2/#5 = MSNR 14.575; RB#3 = 30m OB + MSNR 14.619,5. [görüldü]
7. **Anında tepki şartı**: RB'ye dokunuştan sonra fiyat hemen uzaklaşmalı. Dokunuş sonrası N mum (öneri: 1m'de ≥4–5 mum) RB kenarında yatay/akümüle kalırsa → **geçersiz say ya da stopu BE'ye çek**. [söylendi 21:52–22:00; görüldü RB#5'te 13:00–13:06]
8. RB oluştuktan sonra fiyat RB altında/üstünde **uzun akümüle olursa ve yakındaki FVG anında doldurulmazsa** o TF'de RB "reaction" sayılmaz; geri çekilme beklenir. [söylendi 08:57–09:11]
9. RB'nin hemen önünde (işlem yönünde) **dolmamış FVG + MSNR** varsa → hemen girme; fiyatın önce o FVG'ye inip (manipülasyon) RB kenarına **geri dönmesini** bekle, sonra gir (RB#3). [söylendi+görüldü]
10. HTF OB içinde fiyat **impulsif** (büyük gövdeli ardışık mumlar) ilerliyorsa giriş yok. [söylendi 07:42]
11. Yüksek etkili haber (FOMC 14:00/14:30) işaretlenir; tüm girişler **11:03–13:04 NY** arasında, haberden önce kapandı. Öneri filtresi: haberden ≥60 dk önce yeni giriş yok. [görüldü saatler; filtre = çıkarım]

**Giriş**
12. **Limit emir RB'nin gövde kenarında** (`short: bottom`, `long: top`); RB oluştuktan sonra fiyat uzaklaşıp **retest** ettiğinde dolar. 5 örneğin 5'inde böyle. [görüldü]

**Stop**
13. **Varsayılan stop = 10 puan** ("normalde 10 pip"), RB fitil ekstreminin ötesinde kalacak şekilde (RB#1: entry+10 = fitil+2,75). [görüldü+söylendi]
14. **Büyük RB / internal yapı**: stop **RB fitil ekstreminin hemen ötesi** (+0,25 pt). RB#3 düzeltmesi: 14.620,25 (9,25 pt) → işlem kazanırdı. [görüldü 15:26]
15. Agresif varyant: stop **RB orta çizgisinin ~0,5 pt ötesi** (RB#3: 5,25 pt, RB#5: 7 pt, RB#2: ~6 pt). RB#3'te bu varyant stop oldu ve Kaan sonradan genişletti → **kodda kural 13/14 öncelikli**, orta-stop yalnızca test parametresi. [görüldü]
16. RB-in-RB'de stop = **iç RB fitil ekstremi** (RB#4: 2,75 pt). [görüldü]
17. Önerilen formül: `stop_dist = max(10, |entry − RB_extreme| + 0.25)`; RB-in-RB için `|entry − inner_extreme|` (+tick). [çıkarım, 13+14+16'dan]

**Hedefler**
18. **TP1** = işlem yönündeki **ilk swing tepe/dip** (likidite; RB#4: 12:22 tepe ~14.604,5). [görüldü]
19. **TP2 / tek TP** = karşı taraftaki **en yakın RB/OB/FVG kutusunun ön kenarı veya orta çizgisinin ~0,5–1,5 pt önü** (RB#4 TP2 14.615,25 < short RB orta 14.615,75; RB#5 TP 14.609,5 < short RB alt 14.611; RB#2 TP 14.613 ≈ 30m OB alt/iç dip; RB#3 TP 14.584,25 < long RB alanı). RB#1'de TP = 3m FVG, MSNR 14.575'in üstü. [görüldü]
20. Gözlenen R:R: 5,63 / 3,67 / 5,1 / 10,91 (TP1 7R) / 4,89 → **min R:R filtresi ≈ 3**. [görüldü; eşik = çıkarım]

**Breakeven / yönetim**
21. **BE tetikleyici A (zaman + tekrar test)**: TP1 alındıktan sonra fiyat giriş bölgesine geri geliyorsa ve işlem 5–10 dk'dır açıksa → stop'u kâra, hareket sırasında oluşan **LTF (3m/5m) RB seviyesine** çek (RB#4: ~14.589,5 = entry+4,25). [söylendi+görüldü]
22. **BE tetikleyici B (akümülasyon)**: girişten sonra fiyat RB kenarında akümüle olup sonra ≥~2,5R ilerlediyse → stop = **entry + ~1 pt** (RB#5 "b.e." 14.576,5). [görüldü+söylendi]
23. TP1'de kısmi kapama (RB#4: TP1 = 7R olarak sayıldı, kalan TP2'ye bırakıldı). [görüldü]

**İptal (invalidation)**
24. Fiyat RB'yi **gövde kapanışıyla** tamamen geçerse (RB#4 sonrası 12:37–12:41: dış+iç RB kırıldı) RB ölü; yeni RB aranır. [görüldü; "gövde kapanışı" eşiği = çıkarım]
25. RB oluştuktan sonra retest yapmadan TP seviyesine ulaşılırsa emir iptal. [çıkarım]

---

## 3) Belirsiz kalanlar
- **"C level"** ifadesi bu videoda geçmiyor; yalnızca her kutuda %50 kesikli orta çizgi var. C level = bu orta çizgi mi, doğrulanamadı.
- Swing tanımı için mum sayısı (fractal 2/3/5?) söylenmedi; başta "fraktal indikatörümü kaldırıyorum" diyor (04:38), yani başka zaman fractal indikatörü kullanıyor olabilir.
- RB'nin hangi TF'de "ana" sayılacağı net değil (15m mi 5m mi). RB#3 5m'de çizildi, 1m'de işlendi; RB#1/#2 15m'de.
- Stop kuralı tutarsız: 10 pt sabit / fitil ötesi / RB ortası arasında gidip geliyor. RB#3 kaybı sonrası "fitil ötesi" doğru gösterildi, ama RB#5'te yine ortayı kullandı (21:37: "order bloğu ... ortaya koyacağım"; ifade tam anlaşılmıyor).
- RB#1 TP'si "3m FVG" deniyor ama 3m FVG kutusu karede net görünmedi (TP 14.586,50).
- RB#2'de stop 14.585 RB ortasına (~14.583,4) tam denk değil (~1,6 pt fark); 6 pt sabit de olabilir.
- 13:12'de "OTE bölgesinde" diyor ama LTF OTE fib çizimi ekranda yok.
- BE+ seviyesinde (RB#4, ~14.589,5) stop gerçekten taşındı mı belirsiz: "stopu artıya çekmediğimizi varsayalım" diyor; sonucu 6R olarak sayıyor.
- "Bias birazcık ters" (RB#5): HTF bias'a karşı RB işlemine izin var mı? Kaan "deneyelim" diyor; kural olarak alınmamalı.
- Akümülasyon için kesin mum/süre eşiği yok (kural 7'deki 4–5 mum önerisi çıkarım).
- Haber öncesi kesim saati söylenmedi; videoda işlemler 13:15'te bitti.
