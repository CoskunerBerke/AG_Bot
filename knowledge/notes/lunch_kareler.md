# Lunch Stratejisi (Bölüm 2), kare analizi notları

Kaynak: `knowledge/Lunch Stratejisi - Bölüm 2 - Kaanermi (1080p).mp4` + `knowledge/transcript3.txt`.
Yöntem: 02:20–14:00 arası 15 sn'de bir kare alındı, kritik anlar için ek kareler ve yakın çekimler (crop) yapıldı. Fiyatlar, ekrandaki fiyat ekseninden piksel ölçümüyle okundu (±1 tick hata payı var). Aynı ekranda görünen değerler (pozisyon aracı etiketleri) birebir yazıldı.
Etiketler: **[görüldü]** = karede gözle doğrulandı, **[söylendi]** = sadece transkriptte geçiyor, **[çıkarım]** = ikisinden türetilen yorum.

---

## 0. Grafik kurulumu (tüm kareler için geçerli)

- **Saat dilimi [görüldü]:** Her karede sağ altta `UTC-4` yazıyor. Yani x eksenindeki saatler doğrudan **New York (EDT)** saati. Video kayıt anı: Pzt 15 Eyl 2025, 16:01–16:15 NY (TR 23:01–23:15).
- **Seans kutuları [görüldü]:** Hazır bir gösterge çiziyor. Göstergenin adı ekranda görünmüyor. Kutular, etiketler ve renkler şöyle:
  - Asia (mavi): 20:00–00:00
  - London (pembe): 02:00–05:00
  - NY AM (yeşil/teal): 09:30–11:00
  - **NY Lunch (sarı): 12:00–13:00**
  - NY PM (mor): 13:30–16:00
  - Ölçüm örneği, 05:20 karesinde: 12:00 = x795, 13:00 = x875; sarı kutu x795–870 aralığında. XAU karesinde 12:00–12:55, EURUSD karesinde 12:00–13:00 olarak ölçüldü.
  - Her kutu seansın high/low'u kadar yüksek. Kutunun high ve low'undan sağa doğru yatay çizgi uzanıyor (seviye alınana kadar).
  - [çıkarım] Etiketler ("Asia, London, NY AM, NY Lunch, NY PM"), renkler ve saatler TFO'nun "ICT Killzones & Pivots" göstergesinin varsayılanlarıyla birebir aynı.
- **Lunch kutusunun içeriği [görüldü]:** Sarı kutu 12:00 mumuyla başlıyor, 12:55 mumuyla bitiyor. **13:00 mumu kutunun dışında.** SPX 10 Eyl yakın çekiminde (z_0520) 12 adet 5m mum sayıldı.
- **Saat dönüşümü:** Kaan sözlü olarak "launch 7'de açılır, 800'de kapanır" diyor [söylendi]. Bu Türkiye saati (UTC+3): TR 19:00–20:00 = NY 12:00–13:00 (EDT döneminde fark 7 saat). Kışın NY EST'ye (UTC-5) geçince fark 8 saat olur, lunch TR 20:00–21:00'e kayar. **Kodda seanslar `America/New_York` saatiyle tanımlanmalı:** Lunch 12:00–13:00, NY AM 09:30–11:00.
- **Zaman dilimi [görüldü]:** Bütün örnek grafikler 5m (30 dakikada yaklaşık 6 mum aralığı ölçüldü). Sadece NAS100 12 Eyl örneğinde 1m'e geçti (08:35).
- **İzleme listesi "Kaanermi FAV" [görüldü 07:05, 12:07]:** NAS100, SPX500, DXY, EURUSD, GBPUSD, USOIL, XAUUSD, XAGUSD, INTC, BTCUSDT, ETHUSDT.

---

## 1. Pencere pencere gözlemler

### 02:20–04:55: Şema (boş NAS100 alanında elle çizim)
- **02:20 [görüldü]:** Yeşil kutu ("NY") ve sarı kutu ("Lunch") dikdörtgen aracıyla elle çiziliyor. Gösterge kullanılmıyor.
- **02:50 [görüldü]:** Yeşil kutunun içinde dalgalı, testere gibi bir çizgi var: "NY akümüle". Sarı kutuda tepeden dibe sert düşüş çizgisi var: lunch'ın "hacim göstermesi" (displacement).
- **03:35 [görüldü]:** Sarı kutunun içine iki yuvarlak dikdörtgen çiziliyor: lunch içindeki FVG/OB ("içerideki PD array"). Kutu bittikten sonra lunch low'un hemen altına küçük bir iğne, oradan da yukarı bir çizgi çiziliyor. Çizgi iç PD array'lerin içinden geçip kutunun tepesine gidiyor.
- **03:50–04:20 [görüldü]:** İğne daire içine alınıyor. Sağa ikinci bir şema çiziliyor: "NY" yazısı, iki ayrı yatay high (NY high ≈24.302 ve lunch high ≈24.296), ardından aşağı hareket, lunch low'un altında iğne, sonra **iki high'ın da üstüne uzanan long çizgisi**. Hedef olarak "low resistance liquidity" (iki high) gösteriliyor.
- 04:20–04:55: Çizim yok. Konu, NY akümüle geçmese de stratejinin çalışabileceği.

### 05:00–06:55: SPX500, Çar 10 Eyl 2025, 5m (ana kazanan örnek, pozisyon aracı var)
- **Bağlam [görüldü]:** NY AM kutusu geniş. 10:00–12:00 arası fiyat ≈6.536–6.552 bandında testere gibi gidiyor (Kaan'ın deyişiyle "kocaman bir akümle alan"). 11:00–11:55 arası yükseliş var; **11:55 swing high ≈6.547,2**.
- **Lunch (12:00–12:55) [görüldü]:** high ≈6.544,9 (12:15 civarı fitil), low ≈6.532,4 (12:50 civarı). Lunch boyunca düşüş var ("hacim gösteriyor").
- **İğne [görüldü, z_0520 yakın çekim]:** 12:55 ve 13:00 mumları güçlü yeşil, fiyatı lunch high'a geri taşıyor. **13:05 mumunun fitili ≈6.545,4'e çıkıyor (lunch high'ın ~0,5 puan üstü). Mum siyah, kapanışı ≈6.543,8 (lunch high'ın altında, aralığın içinde).** Kaan'ın "5 dakikalık iğne" dediği bu.
- **Pozisyon aracı (short) [görüldü]:**
  - Giriş **6.543,75**. Aracın sol kenarı **13:10**'da, yani giriş iğne mumundan sonraki mumun açılışı.
  - Hedef **6.535,70** ("Hedef: 8,05 (0,12%)").
  - 05:35'teki ilk hali: stop 8,05 puan (≈6.551,98), R:R 1.
  - 05:50'de: stop **6.547,18** (3,43 puan) = 11:55 swing high. "Risk/Ödül Oranı: 2,35". Kaan'ın sözü: "önemli bir şey vardır, şuraya atabilirsin".
  - **06:35'teki son hali: stop 6.545,43 (1,68 puan, %0,03) = iğnenin tepesi. "Risk/Ödül Oranı: 4,79".**
  - Gerekçe [söylendi]: fiyat oraya gelirse strateji zaten çalışmamış demektir, o yüzden stopu yakına koy ("stopuna yakın atabilirsin").
- **Sonuç [görüldü]:** 13:10'dan sonra fiyat düşüyor, hedef ~13:30'da doluyor. NY PM'de lunch low'un da altına (~6.526) iniyor.
- Hedef 6.535,70 belirgin bir seviyeye denk gelmiyor. Lunch low 6.532,4'ün ~3 puan üstünde, lunch aralığının yaklaşık %75'inde kalıyor.

### 07:05–07:50: SPX500, Cum 12 Eyl 2025, 5m (bias long, "lunch çalışmamış" örneği)
- **[görüldü]** NY AM high ≈6.594,2, low ≈6.580,8. Lunch high ≈6.591,0, low ≈6.582,2. Lunch boyunca yükseliş.
- **07:33 [görüldü]:** İmleç 13:10–13:15 mumunda. Fiyat lunch high'ın biraz üstüne çıkmış (≈6.591,5–6.592), ama belirgin bir iğne yok. Kaan da "İğneyi aslında göremiyoruz" diyor [söylendi].
- **[görüldü]** Sonrasında 13:15–13:30 arası 5m FVG'ye (~6.588–6.589) geri çekilme var. Ardından NY PM'de NY AM high (6.594,2) alınıyor ve fiyat 6.597,5'e kadar çıkıyor. 15:45'te sert bir düşüşle NY AM low'un ve lunch low'un altına iniyor.
- **[söylendi]** Bias long, "Nazdaq'ta da longtu". Asıl şart "New York'un highı veya low'u alınması"; mükemmel bir sweep gerekmiyor. Fiyat FVG'ye çekilip "yoluna devam ediyor". Yani lunch dönüşü "büyük R:R olarak çalışmamış".

### 08:00–08:50: NAS100, Cum 12 Eyl 2025, 5m → 1m ("lunch iğnelenmezse" alternatifi)
- **[görüldü]** NY AM güçlü yükseliş (high ≈24.095). Lunch low ≈24.052 (12:05), lunch high ≈24.101 (12:55 fitili).
- **[görüldü]** Lunch sonrası 13:00–13:30 yatay. Kaan 12:45 dibinden sağa **≈24.076**'da yatay bir çizgi çekiyor (akümülasyon alanının dibi).
- **[görüldü, z_0805]** **13:35 5m mumu çizginin altına fitil atıyor (≈24.071) ve üstünde kapanıyor.** Lunch low (24.052) alınmıyor.
- **08:35 [görüldü]:** 1m'e geçiyor. Çizgi 12:43'ten 13:43'e uzanıyor. 13:38–13:42 arası 1m mumları çizgiyi süpürüyor. Ardından ~13:45–13:50'de 1m swing high'ın yukarı kırılması (market structure shift) geliyor [söylendi: "bir dakikalıkta market structure shift"].
- **[görüldü]** Sonra fiyat lunch high'ı ve NY AM high'ı alıp ≈24.124'e (NY PM high) gidiyor. Bu örnekte pozisyon aracı çizilmedi.

### 09:00–10:55: XAUUSD, Cum 12 Eyl 2025, 5m (yüksek zaman dilimi FVG filtresi, başarısız iğne)
- **[görüldü]** Lunch güçlü bir yükseliş (≈3.643,6'dan lunch high ≈3.649,78'e). Lunch içinde küçük yeşil bir dikdörtgen var: 5m FVG (≈3.648,3–3.648,8).
- **[görüldü]** Büyük sarı dikdörtgen = **15m FVG** (NY AM düşüşünden, ~10:35'ten sağa uzatılmış), aralığı **≈3.650,1–3.651,7**. Alt sınırı lunch high'ın sadece ~0,3 üstünde.
- **Mum mum [görüldü, z_1020]:**
  - 13:00: fitil ≈3.650,0 (lunch high'ın üstü), kapanış ≈3.649,57 (içeride). Tanıma uyan **1. iğne**.
  - 13:05: doji, fitil yine lunch high'ın üstünde.
  - 13:10: kapanış ≈3.650,0, **lunch high'ın üstünde**. 1. iğneye stop iğnenin tepesine konmuşsa stop olurdu.
  - 13:15: 15m FVG'ye giriyor (≈3.650,6).
  - **13:20: fitil ≈3.650,86 (FVG'nin içinde), siyah mum, kapanış ≈3.649,59 (lunch high'ın altında). 2. iğne.**
  - **13:25: sert düşüş mumu (kapanış ≈3.647,8)**, 5m FVG'nin altına iniyor. NY PM boyunca düşüş sürüyor, 16:00'da ≈3.643.
- **[söylendi]** Kaan'ın anlatımı: 5m'de iğnelendi ama "shorta dönmedi". 15/30/45 dakikalığa geçince 15m FVG görülüyor ve fiyat "yakıtını" oradan alıyor. Sözü: "5 dakikalıkları iğnelendi deyip dalamazsın". FVG, order block, breaker block, OTE gibi bütün PD array'ler hesaba katılmalı.

### 10:55–11:15: Kaan'ın X sayfası
- Eski paylaşımları gösteriyor (ör. "Lunchermi 7RR"). Kural bilgisi yok.

### 11:15–11:35: EURUSD, 12 Eyl ve 10 Eyl 2025, 5m
- **12 Eyl [görüldü]:** London'dan sonra (05:00) NY AM sonuna kadar dalgalı, akümülasyon. Lunch başında ≈1.1710'a dip (12:05 civarı), sonra lunch boyunca ≈1.1735'e yükseliş, NY PM'de ≈1.1745. Kaan'ın sözü: "Londra'nın sonundan beri market aküle", lunch'ta hacim geliyor [söylendi].
- **10 Eyl [görüldü]:** NY AM yükseliş, lunch düşüş (1.1716'dan 1.1702'ye), NY PM'de önce 1.1712'ye tepki, sonra ≈1.1693'e düşüş.

### 11:35–12:30: GBPUSD, Cum 12 Eyl 2025, 5m (low resistance liquidity)
- **[görüldü]** Lunch başında ≈1.3541'e dip, sonra lunch boyunca ≈1.3568'e yükseliş. NY PM'de NY AM high (≈1.3570) alınıyor.
- **11:58 [görüldü]:** Kaan NY AM high'ın üstüne hayali bir high çizip London high (≈1.3574) seviyesine kadar yatay bir çizgi uzatıyor. Anlatılan [söylendi]: önemli bir likidite varsa hedef oraya kadar beklenebilir.

### 12:30–14:00: BTCUSDT, 11–12 Eyl 2025, 5m + enstrüman listesi
- **12 Eyl [görüldü]:** London'dan NY AM sonuna kadar 114.800–115.500 arasında yatay (akümülasyon). Lunch'ta yükseliş başlıyor, NY PM'de 116.500'e sert çıkış.
- **11 Eyl [görüldü]:** Lunch'tan sonra belirgin bir hareket yok. Kaan "burada çalışmamış" diyor [söylendi].
- Enstrümanlar [söylendi]: Nasdaq, S&P 500, EURUSD, GBPUSD, USDCAD, XAUUSD, XAGUSD, bazen BTC.

---

## 2. Kodlanabilir kurallar

Bütün saatler `America/New_York`. Sinyal zaman dilimi 5m.

1. **Seans tanımları [görüldü]:** Lunch = 12:00 ≤ bar_open < 13:00 (12:00…12:55 arası 12 adet 5m bar). `lunchHigh = max(high)`, `lunchLow = min(low)`. NY AM = 09:30–11:00 (`nyHigh`, `nyLow`). London = 02:00–05:00. Seviye hesabı 13:00 barı açılınca kesinleşir.
2. **İşlem penceresi [söylendi + görüldü]:** Sinyal mumu 13:00 ≤ bar_open ≤ 13:25 olmalı (Kaan'ın sözü: "maksimum 30 dakika beklersin"). Giriş en geç 13:30 barında. Görülen örnekler: SPX iğnesi 13:05 (giriş 13:10), XAU iğnesi 13:00 ve 13:20, NAS alternatif kurulumu 13:35. Penceresi 13:30 ve 13:45 olarak iki ayrı parametreyle test edin.
3. **İğne (sweep) tanımı [görüldü]:**
   - Short: `high > lunchHigh` VE `close < lunchHigh`
   - Long: `low < lunchLow` VE `close > lunchLow`
   - SPX örneğinde aşım ≈0,5 puandı ve iğne mumu ters renkliydi (close < open). Renk şartını parametre yapın, varsayılanı kapalı olsun.
4. **Sweep seviyesi alternatifi [söylendi]:** "New York'un highı veya low'u alınması" da yeterli sayılıyor. Seviye listesi: birincil `lunchHigh/lunchLow`, ikincil `nyHigh/nyLow`. Kural 3'teki iğne şartı bu seviyelere de uygulanır.
5. **Bias filtresi [söylendi; SPX 12 Eyl'de görüldü]:** Yön, yüksek zaman dilimi bias'ına uymalı. Bias long ise sadece lunch LOW iğnesinden long, bias short ise sadece lunch HIGH iğnesinden short. SPX 12 Eyl'de bias long iken lunch high'ın üstüne çıkan fiyat sadece 5m FVG'ye kadar geri çekilip yukarı devam etti. Bias tanımı videoda yok, bkz. Belirsiz.
6. **Giriş [görüldü + söylendi]:** İğne mumu kapandıktan sonra **bir sonraki 5m mumun açılışında** market emir. Kaan'ın sözü: "ertesi hemen mumda... işleme dahil oluyorsun". SPX'te iğne 13:05, giriş 13:10 açılışı (6.543,75).
7. **Stop [görüldü]:** Varsayılan **iğne mumunun ucu** (short için high, long için low) + `buffer` (görselde ≈0; spread/1 tick önerilir). SPX: 6.545,43 / 1,68 puan.
   - Alternatif stop: iğnenin ötesindeki en yakın 3-mum swing high/low (SPX: 6.547,18, R:R 2,35). Parametre olsun.
8. **Hedefler [söylendi + görüldü]:**
   - **TP1:** İğne ile karşı lunch ucu arasındaki ilk dolmamış 5m FVG/OB ("içerideki PD array"). Şemada 03:35'te görüldü. SPX 12 Eyl'deki geri çekilme de 5m FVG'de bitti.
   - **TP2:** Bias'a göre karşı lunch ucu. High iğnesinde `lunchLow`, low iğnesinde `lunchHigh` [söylendi 13:07]. SPX 10 Eyl'deki gerçek hedef 6.535,70 (lunch aralığının ~%75'i, R:R 4,79), yani lunch low'un biraz önünde tutulmuş.
   - **TP3 (low resistance liquidity):** Karşı taraftaki `nyHigh/nyLow`. Daha ötede önemli likidite varsa London high/low [görüldü: 04:05 şeması, GBPUSD 11:58].
9. **Hedef tarafında PD array şartı [söylendi 10:55]:** İğnenin hedef tarafında (long için üstte, short için altta) en az bir PD array (FVG/OB) olmalı. Yoksa sinyal zayıf: skor düşürün veya filtreyle eleyin.
10. **Yüksek zaman dilimi PD array veto filtresi [XAU'da görüldü + söylendi]:** İğne seviyesinin ötesinde (short için `lunchHigh` üstünde, long için `lunchLow` altında) `k × ATR(5m,14)` mesafe içinde **dolmamış bir 15m/30m/45m FVG** varsa:
    - a) O iğneyi alma.
    - b) Fiyatın o FVG'ye girip iğne şartını (close lunch seviyesinin içinde) yeniden sağlamasını bekle.
    - XAU örneği: 1. iğne (13:00) stop oldu. FVG'ye giren 2. iğne (13:20) kazandı. Başlangıç değeri olarak k ≈ 1 önerilir.
11. **Geçersizlik [söylendi 05:53–06:23]:** Fiyat iğnenin ucunu geri alırsa "strateji çalışmamış" demektir: stop olunur, aynı yönde yeniden giriş yapılmaz. Tek istisna Kural 10b, yani yüksek zaman dilimi FVG'si içinden yeni bir iğne gelmesi.
12. **Alternatif kurulum: lunch ucu iğnelenmezse [NAS100 12 Eyl'de görüldü]:**
    - 13:00–13:30 arası oluşan iç akümülasyon alanının 3-mum swing low/high'ı (transcript5'teki tanım: ortadaki mumun ucu iki komşusundan daha uçta) 5m'de fitille süpürülüp geri içeride kapanırsa, 1m'e geç.
    - 1m'de son swing high (long için) **kapanışla** kırılırsa (market structure shift), MSS mumundan sonraki mumda gir.
    - Stop süpürme fitilinin ucu. Hedef lunch high ve NY AM high.
13. **NY akümülasyonu [söylendi]:** Zorunlu değil, artı puan ("akümüle geçerse bizim için artı ama... geçmese de olur"). Kod önerisi: skor olarak kullanın. Örneğin `NY AM range / ATR(D,14) < eşik` veya NY AM ile London aralıklarının büyük ölçüde örtüşmesi. Görsel karşılıkları: SPX 10 Eyl'de 10:00–12:00 arası testere, BTC ve EURUSD 12 Eyl'de London–NY AM arası yatay piyasa.
14. **Enstrümanlar:** Grafikte görülenler SPX500, NAS100, XAUUSD, EURUSD, GBPUSD, BTCUSDT [görüldü]. Sözlü listede bunlara ek olarak USDCAD ve XAGUSD var [söylendi].

---

## 3. Belirsiz kalanlar

- **HTF bias nasıl belirleniyor?** Hangi zaman dilimi, hangi kural (gün yönü mü, Daily FVG mi, NAS–SPX uyumu mu)? Videoda tanım yok. Sadece SPX 12 Eyl'de "Nazdaq'ta da longtu" diyor.
- **"New York'un high/low'u" hangi seviye?** NY AM kutusu mu, yoksa göstergedeki "NY Lunch" kutusu mu? SPX 12 Eyl'de 13:10'da alınan seviye lunch high'dı; NY AM high ancak ~13:45'te alındı.
- **Lunch içindeki iğne sayılıyor mu?** Lunch'ın 12:00–12:10 arasında yaptığı dip/tepe (GBPUSD ve EURUSD 12 Eyl'de low 12:05'te oluştu, ardından lunch boyunca yükseliş) setup sayılıyor mu? Şemada iğne kutu bittikten sonra çizildi, SPX örneği de 13:05.
- **Mum rengi ve kapanış şartının kesinliği:** Elimizde temiz bir tek örnek var (SPX 13:05). XAU'daki 13:00 iğnesi tanıma uyuyor ama kaybetti.
- **Hedef 6.535,70 neye göre seçildi?** Belirgin bir seviyeyle örtüşmüyor. 8,05 puan değeri ilk çizimden kalmış olabilir. TP için kesin kural yok; sadece "lunch'ın karşı ucu" sözü var.
- **"İçerideki PD array'i atıyorsun":** İç PD array giriş bölgesi mi, hedef mi? Şemada hedef gibi duruyor. Limit emirle iç FVG'den giriş varyantı da test edilmeli.
- **Pencere süresi:** 30 dakika mı, 45 dakika mı? NAS alternatif kurulumu 13:35–13:38'de oldu.
- **Stop tamponu:** Tick sayısı söylenmedi; görselde ≈0.
- **İşlem yönetimi:** Kısmi kâr, breakeven ve zaman stopu konuşulmadı.
- **"Lunch hacim gösteriyor":** Displacement için ölçüt (mum gövdesi/ATR gibi) tanımsız.
- **1m MSS tanımı:** Fitil kırılımı mı, kapanış kırılımı mı gösterilmedi. Kuralda kapanış varsayıldı.
- **XAU'daki "FVG varmış ama buraya gelmemiş" sözü:** Lunch içindeki 5m FVG'yi kastediyor gibi; fiyat oraya geri çekilmeden yukarı gitti. Tam olarak net değil.
- **Gösterge adı:** Ekranda görünmüyor. TFO tahmini saat, renk ve etiket uyumuna dayanıyor.
- **Hacim filtresi:** Gerçek hacim kullanılmıyor. "Hacim" sözcüğü fiyat hareketinin büyüklüğü anlamında geçiyor.
