# Kaanermi — Sıfırdan Trading Kampı (26 Video Tam Analiz ve Kural Özeti)

**Playlist:** `https://www.youtube.com/playlist?list=PLDkrnRVa54hs`  
**Analiz Tarihi:** 2026-10-15  

Bu doküman, Kaanermi'nin 26 bölümlük **Sıfırdan Trading Kampı** serisindeki tüm videoların transkriptlerinden çıkarılan mekanik kuralları, strateji modellerini, risk yönetimini ve `ag2bot` (`nasdaq_bot/dmc/`) motorundaki karşılıklarını özetler.

---

## Bölüm 1–4: Temel Altyapı, Mum Yapısı ve Yakıt (Fuel) Sistemi
- **#1 (`mXkroShFmSo`) — Day Trading & TradingView Kurulumu:**
  - Grafik saat dilimi **New York (`UTC-4` / `America/New_York`)** olarak sabitlenir.
  - Vadeli endeksler (`NQ`, `ES`, `YM`) ve korele pariteler (`XAUUSD`/`XAGUSD`, `EURUSD`/`GBPUSD`) izlenir.
- **#2 (`YNgMhlBYy8s`) — Mum Kalıpları ve Swing Noktaları:**
  - **Swing High / Swing Low (3-mum kuralı):** Ortadaki mumun iğnesi sağındaki ve solundaki mumlardan daha yüksekse **Swing High**, daha düşükse **Swing Low** olarak işaretlenir (`structure.swing_points`).
- **#3 (`Liv0Gs68W4c`) — Market Yapısı (Yakıt / Fuel Sistemi):**
  - Fiyat **likiditeden (swing noktaları / eşit tepeler-dipler) dengesizliğe (FVG)** veya **dengesizlikten likiditeye** hareket eder.
  - Önemli bir swing noktası süpürüldüğünde (sweep), market "yakıt" (fuel) toplar ve karşı taraftaki likiditeye (`Draw on Liquidity`) yönelir.
- **#4 (`qM6FKVEi2JY`) — Demo Hesapta Pratik:**
  - İşlemler yalnızca likidite alımı + yapı kırılımı (MSS) sonrasında kurgulanır.

---

## Bölüm 5–10: Killzone, MSS/BOS, FVG, Order Block ve SMT
- **#5 (`MZIHJCucPDs`) — Killzone Saatleri ve Hacim Analizi:**
  - **Asia:** `20:00–00:00` NY (genellikle akümülasyon).
  - **London:** `02:00–05:00` NY.
  - **NY AM:** `09:30–11:00` NY (ana hacim ve manipülasyon/dağıtım penceresi).
  - **NY Lunch:** `12:00–13:00` NY (öğle arası likidite biriktirme bölgesi).
  - **NY PM:** `13:30–16:00` NY.
  - **Hacim Kuralı:** Eğer NY AM seansı (`09:30–11:00` / `12:00`) düşük hacimli ve dar aralıklı (akümülasyon) geçtiyse (`am_range < median`), Lunch (`12:00–13:00`) sonrasında (`13:00+`) yüksek hacimli patlama (expansion) beklenir.
- **#6 (`-b1OugpImB4`) — MSS (Market Structure Shift) ve BOS (Break of Structure):**
  - **MSS ("McDonald's Kalıbı"):** Fiyat önemli bir likidite seviyesini (swing high/low, PDH/PDL, ORH/ORL vb.) süpürdükten sonra, o son bacağı oluşturan iç swing noktasını **mutlaka mum GÖVDESİYLE (body close)** kırmalıdır. İğne (wick) ile yapılan geçişler MSS sayılmaz, yeni bir likidite süpürmesi sayılır.
  - **BOS:** Mevcut trend yönünde yeni tepe/dip kırılımıdır (gövde kapanışı şarttır).
- **#7 (`ov9PL4d4oZg`) — FVG (Fair Value Gap) Yapıları:**
  - 3 mumluk dizide 1. mumun iğnesi ile 3. mumun iğnesi arasında kalan boşluktur (`structure.find_fvgs`).
  - Fiyat FVG'yi mum gövdesiyle ters yönde kapatmadığı (`closed_through == False`) sürece FVG geçerlidir.
- **#8 (`g-61wiSfqMc`) — İlk Geliştirmen Gereken Strateji (Temel Model):**
  - 15dk / 1s önemli likidite süpürmesi -> 1dk/3dk/5dk grafikte gövde kapanışlı **MSS + Displacement (FVG bırakan sert hareket)** -> geri çekilmede (pullback) FVG/OB üzerinden giriş -> stop süpürme iğnesinin veya FVG'nin arkası -> hedef karşı likidite (`min 3R`).
- **#9 (`HZ3_hXIVGMs`) — Order Block (OB) Yapıları:**
  - Güçlü bir displacement (sert yükseliş/düşüş) öncesindeki son ters renkli mum (veya yüksek zaman diliminde birleşik 2–3 mumluk blok).
  - Hem geri çekilmede giriş (PDA) olarak hem de kademeli kâr alma (TP) hedefi olarak kullanılır.
- **#10 (`xTLvRa9-zPk`) — SMT (Smart Money Technique) Uyuşmazlığı:**
  - Korele iki enstrümandan (`NQ` vs `ES`, `QQQ` vs `SPY`, `GLD` vs `SLV`) biri önemli bir swing/seans likiditesini süpürürken diğerinin süpürememesidir.
  - **Giriş Kuralı:** Her zaman **likiditeyi alan (süpüren)** parite yakıtı topladığı için daha güvenlidir (stop olma ihtimali daha düşüktür) veya öğle arası (`Lunch`) stratejisinde kümülatif SMT onayı olarak kullanılır.

---

## Bölüm 11–18: Premium/Discount, OTE, Continuation, NDOG/NWOG, Box, Mitigation, FPFVG, Daily Bias
- **#11 (`YzvCG8iicyQ`) — Premium / Discount ve Displacement:**
  - Swing Low'dan Swing High'a çekilen Fibonacci'nin `0.5` seviyesi **Equilibrium (Denge)** noktasıdır. Üstü **Premium (Pahalı - Short bölgesi)**, altı **Discount (Ucuz - Long bölgesi)** olarak tanımlanır.
- **#12 (`KnoBWsDySAk`) — OTE (Optimal Trade Entry) Stratejisi:**
  - Swing'den swing'e çekilen Fibonacci'de `0.62 – 0.705 – 0.79` aralığı ("Golden Zone") doğal geri çekilme ve dönüş bölgesidir (`structure.ote`).
  - Tek başına körlemesine limit atılmaz; OTE bölgesi içindeki bir **PDA (`FVG`, `RB`, `OB`)** ile kesiştiğinde yüksek olasılıklı giriş verir.
- **#13 (`HiJJJivHlyo`) — Continuation (Devam) Trade Modeli:**
  - Fiyat likidite alıp sert kırılım (displacement + SMT) yaptıktan sonra en dibe dönmüyorsa, ara swing'den çekilen OTE (`0.62–0.79`) veya `0.5` denge noktasındaki FVG/IFVG'den trend yönünde işleme dahil olunur.
- **#14 (`P7xM_CIzM-4`) — NDOG ve NWOG Seviyeleri ve Önemi:**
  - **NDOG (New Day Opening Gap):** Önceki gün kapanışı (`16:00`/`17:00`) ile yeni gün açılışı arasındaki boşluk ve `0.5` orta noktası (Consequent Encroachment).
  - **NWOG (New Week Opening Gap):** Cuma kapanışı ile Pazar/Pazartesi açılışı arasındaki boşluk.
  - **2 Kullanım Şekli:**
    1. **Mıknatıs / Hedef (Draw on Liquidity):** Fiyat açılışta NDOG/NWOG'dan uzaklaşıp manipülasyon yaparsa, NY seansında (`09:30+`) NDOG/NWOG seviyesine mıknatıs gibi çekilir (TP hedefi).
    2. **Akümülasyon / Destek-Direnç:** Fiyat NDOG/NWOG `0.5` seviyesine oturduğunda orayı akümülasyon/retest basamağı olarak kullanır.
- **#15 (`3HaTKyYHbHM`) — BOX Setup Stratejisi:**
  - AMD (Accumulation - Manipulation - Distribution) kalıbının kutu versiyonu: Dar akümülasyon kutusunun bir tarafı manipüle edilip diğer tarafı gövdeyle kırıldığında, kutunun manipüle edilen kenarına (ör. akümülasyon low'una) retestte giriş aranır. *(Not: 2 yıllık NQ backtestinde tek başına düşük WR verdiği için `config.yaml` içinde devre dışı bırakılmıştır).*
- **#16 (`tSFo8xV7EIM`) — Mitigation Block Stratejisi:**
  - Başarısız tepe/dip (failure swing) sonrası gövde kapanışlı MSS oluştuğunda, kırılan iç swing bölgesi (veya onunla kesişen FVG) retest edildiğinde giriş verir.
  - Ayrıca 1 saatlik / 15 dakikalık **Rejection Block'un `0.5` orta noktası (C-level)** işaretlenir; fiyat burayı test edip LTF'de akümüle olduktan sonra sert dağıtım (distribution) yapar.
- **#17 (`bWuvbNgTpk4`) — First Presented FVG (FPFVG):**
  - `09:30` NY açılışından sonra (`09:30–10:00` arasında) **oluşan ilk FVG** günün referans dengesizliğidir. Fiyat bu FVG'nin içinde akümüle olup veya onu ters yönde kırdıktan (IFVG) sonra sabah trendini başlatır.
- **#18 (`GadcZXkRQ7s`) — Daily Bias Nedir? Nasıl Belirlenir? (CRT & HTF Akışı):**
  - Market yalnızca **Likiditeden -> FVG'ye** veya **FVG'den -> Likiditeye** gider.
  - **CRT (Candle Range Theory):** Günlük (Daily), 4 Saatlik (4H) veya 1 Saatlik (1H) mumun tepesi/dibi süpürülüp (özellikle karşıdaki bir HTF FVG'nin `0.5` orta noktasına değerek) mum gövdesi tekrar aralığın içinde kapanırsa, günlük yön (bias) o mumun karşı ucuna döner.

---

## Bölüm 19–26: RDRB, Tüm FVG'ler, Rejection Block, LRLR, PO3/AMD, Turtle Soup ve Matematiksel Risk Yönetimi
- **#19 (`9DF9fHf5v4c`) — RDRB (Redelivered Rebalanced) Nedir?**
  - Görünürde boşluk kalmamış (1. ve 3. mum iğnelerinin birbirine değdiği/kesiştiği) ancak 1 saatlik / 15 dakikalık yapıda FVG gibi çalışan dengelenmiş fiyat alanıdır.
  - Doğrudan kör limit emir atılmaz; fiyat RDRB'den yakıt aldıktan sonra 1 dakikalıkta **MSS + Displacement** onayıyla girilir veya **TP hedefi** olarak kullanılır.
- **#20 (`ptJ42O9Q9oU`) — Tüm FVG Türleri (FVG, IFVG, BPR):**
  - **Normal FVG:** Fiyat boşluğa döner, tepki alır (`0.5` CE seviyesi veya yakın kenar).
  - **IFVG (Inversion FVG):** Çalışmayan / güçlü gövdeyle kırılan FVG artık ters yönde destek/direnç olarak çalışır.
  - **BPR (Balanced Price Range):** Yükseliş ve düşüş FVG'lerinin üst üste bindiği yüksek güçlü dengesizlik kesişimidir.
- **#21 (`RJaHLp-kYI0`) — Rejection Block (RB) Stratejisi:**
  - Önemli bir swing likiditesini veya PD Array'i (FVG/OB) **uzun ve temiz bir iğneyle (wick)** tek seferde süpüren mumun **iğne ucundan gövde kapanışına kadar olan bölgesi** Rejection Block'tur (`structure.find_rbs`).
  - Fiyat bir sonraki gelişinde en uçtaki iğneyi tekrar alamadan RB'nin gövde kenarından veya **`0.5` orta noktasından (C-level)** döner (`dmc_rb` modeli).
- **#22 (`fXxCna6Sa2s`) — Low Resistance Liquidity Run (LRLR) & HRLR:**
  - **HRLR (High Resistance Liquidity):** Ana likiditeyi süpürmüş, arkasında güçlü koruma olan en tepe/en dip nokta.
  - **LRLR (Low Resistance Liquidity):** Fiyatın ana hedefe giderken yol üzerinde bıraktığı nizami, birbirine yakın iç swing tepeleri/dipleridir. Tek mumda kolayca temizlenir; **kademeli TP (`TP1`, `TP2`)** hedefleri olarak kullanılır.
- **#23 (`SxsjOsUdRDU`) — Power Of 3 (PO3 / AMD - Accumulation, Manipulation, Distribution):**
  - Tüm gün içi kurulumların (`10AM`, `Lunch`, `DMC RB`, `Kaan`) temel çatısıdır:
    1. **Accumulation:** Fiyatın dar aralıkta hacimsiz sıkışması (`09:30–10:00` Açılış Aralığı veya `12:00–13:00` Lunch).
    2. **Manipulation (Judas Swing):** Akümülasyonun tepesini/dibini (`ORH`/`ORL`, `PDH`/`PDL`, `ONH`/`ONL`) süpürüp yanlış yöne gidiyormuş izlenimi vermesi (`10:00–10:35` veya `13:00–13:30`).
    3. **Distribution:** Gövde kapanışlı MSS/CISD sonrası gerçek yönde sert dağıtım.
- **#24 (`CvDrT6xp9dA`) — Turtle Soup Stratejisi:**
  - **5 Zorunlu Unsur:** `Draw on Liquidity` (mıknatıs hedef) + `PD Array` (FVG/RB) + `Likidite Süpürmesi` + `Premium/Discount (0.5 üstü/altı)` + `PO3/AMD` (ve varsa `SMT`).
  - Stop, en uzaktaki swing ucuna değil, giriş yapılan **PDA'nın (FVG/RB) hemen arkasına** konularak yüksek R:R (`3R–5R`) elde edilir.
- **#25 (`G_ZTXyxF5CY`) — Final: Grafik Pratiği ve Disiplin:**
  - Her strateji kalıbının (`MSS`, `FVG`, `OB`, `SMT`, `OTE`, `RB`, `PO3`, `Turtle Soup`) birlikte nasıl okunduğunun özeti ve Mark Douglas (*Trading in the Zone*) psikoloji disiplini.
- **#26 (`gaopVXWXBtc`) — Matematiksel Risk Yönetimi:**
  - **Sabit Dolar Riski Kuralı:** Stop mesafesi dar da olsa geniş de olsa her işlemde riske edilen dolar/yüzde miktarı **birebir sabit** tutulur (`size_position` fonksiyonumuz tam olarak bunu uygular).
  - Maksimum drawdown limiti `6` ila `8` eşit parçaya bölünür; haftada sadece yüksek kaliteli 2–3 işlem bile (`3R–4R`) tüm stopları kapatıp hesabı kâra geçirir. Üst üste 2 stop alındığında o gün ekran kapatılır (`halt_after_stops: 2`).
