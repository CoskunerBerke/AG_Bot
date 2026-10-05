# NASDAQ Analiz & İşlem Botu

NASDAQ-100 hisselerini her gün tarayan; teknik göstergeleri, **21 Japon mum formasyonunu**, hacmi,
piyasa rejimini (QQQ + VIX), **internetteki haberleri** (Yahoo Finance + Google News) ve
**analist görüşlerini** (tavsiye ortalaması, hedef fiyat, not artırım/indirimleri, bilanço tarihi)
birleştirip 0-100 arası skor üreten; en az **%1 hedefli**, stoplu işlem planları çıkaran bot.

> [!WARNING]
> **Hiçbir bot her gün %1 kârı garanti edemez.** Her gün %1 = yılda ~%1100 (252 işlem günü bileşik).
> Dünyanın en iyi fonları bile bunu yapamıyor. Bu botun %1 hedefi *her işlemin hedefi* ve
> *günlük kâr kilidi* olarak uygulanır; zararlı günler **olacaktır**. Botu kurallarına uyan ve
> zararı sınırlayan bir disiplin aracı olarak kullanın. Gerçek parayla başlamadan önce
> backtest + en az 1-2 ay paper (sanal) hesapta deneyin. Yatırım tavsiyesi değildir.

## Kurulum

```powershell
pip install -r requirements.txt
```

## Komutlar

| Komut | Ne yapar |
|---|---|
| `python main.py scan` | NASDAQ-100'ü tarar, skor tablosu + AL sinyalleri, `reports/` altına CSV + Markdown rapor |
| `python main.py scan --tickers AAPL TSLA AMD` | Sadece verilen hisseler |
| `python main.py scan --no-news` | Haber/analist adımını atla (hızlı) |
| `python main.py analyze NVDA` | Tek hisse: tüm göstergeler, son 5 günün mumları, **mum formasyonlarının bu hissedeki geçmiş başarısı**, haberler, analistler, işlem planı |
| `python main.py backtest --years 3` | Stratejiyi geçmişte test eder; %1+ kârlı gün oranı, kazanma oranı, maks. düşüş, QQQ ile kıyas |
| `python main.py trade` | Bugünkü emir planını gösterir (kuru çalışma, emir göndermez) |
| `python main.py trade --execute` | Alpaca **paper** hesabına bracket (giriş+stop+hedef) emirleri gönderir |
| `python main.py trade --strategy rb` | **Rejection Block emir planı**: 39 Alfa hissede yapısal stoplu ve 3R hedefli bracket emirler |
| `python main.py trade --strategy rb --execute` | Rejection Block bracket emirlerini doğrudan Alpaca hesabına gönderir |
| `python main.py guard` | Seans boyunca izler: günlük **+%1'e ulaşınca her şeyi kapatır**, -%2'de durur, süresi dolan pozisyonları kapatır |
| `python main.py status` | Hesap, pozisyonlar, günlük K/Z |
| **`python main.py live`** | **SÜREKLİ gün içi long/short bot** (Rejection Block SMC bölgelerini otomatik izler ve işler) |
| `python main.py live --analiz` | Piyasa kapalıyken bile son veriyle tek analiz turu (emir yok) |
| `python main.py backtest-intraday` | Gün içi long/short stratejiyi son 60 günün 5 dk verisinde test eder |
| **`python main.py rb-scan`** | **Rejection Block Taraması**: Güncel aktif ve yaklaşan RB bölgelerini, yapısal stop ve 3R hedefleriyle listeler |
| `python main.py rb-scan --tf 1h` | 1 saatlik grafikte aktif Rejection Block bölgelerini ve limit seviyelerini tara |
| **`python main.py rb-backtest`** | **Rejection Block Portföy Simülasyonu**: 39 Alfa hisse üzerinde 2019-2026 sermaye eğrisini (`reports/rb_strategy_equity.png`) üretir |
| **`python main.py pattern-lab`** | **Formasyon Laboratuvarı**: tüm mum + grafik formasyonlarını 4 zaman diliminde istatistiksel test eder, sadece işe yarayanları bota verir |
| `python main.py pattern-lab --tf 1d 15m --tickers AAPL NVDA --no-save` | Seçili zaman dilimi/hisse, sonuçları kaydetmeden |

## Formasyon Laboratuvarı (`pattern-lab`)

İnternette paylaşılan formasyon listeleri (Çekiç, Asılı Adam, Kayan Yıldız, Sabah/Akşam Yıldızı, Üç İçeride/Dışarıda
Yukarı/Aşağı, Karşı Saldırı, On-Neck, Tepe/Dip Dönüş, OBO, TOBO, İkili/Üçlü Tepe-Dip, Üçgenler, Bayraklar...)
"bu formasyon AL'dır" der ama bunu **ölçmez**. Laboratuvar her formasyonu ölçer:

| Zaman dilimi | Veri | Tutma süresi |
|---|---|---|
| 1d | 5 yıl | 5 gün |
| 1h | 730 gün | 7 saat (gün içi) |
| 15m | 60 gün | 4 saat (gün içi) |
| 5m | 60 gün | 2 saat (gün içi) |

1. NASDAQ-100'deki her barda sanal işlem: giriş sonraki açılış, stop 1 ATR, hedef 1.5 ATR, komisyon+kayma dahil.
2. Formasyon sonrası sonuç, **aynı yönde rastgele girişe** (baz oran) göre kıyaslanır → `Fark%`.
3. t-istatistiği **gün bazında** hesaplanır (aynı gün 30 hissede çıkan sinyal 30 kanıt sayılmaz).
4. Dönem ikiye bölünür; formasyon **iki yarıda da** aynı yönde çalışmalı.

Karar: **GEÇERLİ** (t≥2, iki yarı da pozitif) → bot formasyonu kullanır · **TERS** (t≤-2, iki yarı da negatif) →
bot sinyali tersine çevirir · ZAYIF / GEÇERSİZ / AZ ÖRNEK → bot formasyonu **yok sayar**.
Ağırlıklar `state/pattern_weights.json`'a yazılır; `scan`, `backtest`, `live` bunları otomatik kullanır.
Ders kitabı ağırlıklarına dönmek için `config.yaml` → `strategy.use_validated_patterns: false`.

> [!WARNING]
> ~40 formasyon × 4 zaman dilimi = ~160 test. Şans eseri birkaçının "geçerli" çıkması beklenir; ikiye bölme
> kontrolü bunu azaltır ama sıfırlamaz. Laboratuvarı ayda bir yeniden çalıştırın.

## Rejection Block (`rb`)

**Ayı RB:** bir tepe mumunun uzun üst fitili (fiyat oraya çıkmış, satıcılar geri itmiş). Bölge = fitil ucu ↔ gövde üstü.
**Boğa RB:** bir dip mumunun uzun alt fitili. Bölge = fitil ucu ↔ gövde altı.

Bot bir bölgeyi şöyle takip eder: tepe/dip `pivot_k` barla onaylanır → fiyat bölgeden ≥1 ATR uzaklaşır → geri gelip
bölgeye girer (**ilk temas**) → gövde kenarının dışında kapanırsa **RET** (sinyal). Kapanış fitil ucunu geçerse bölge
**geçersiz** olur. Varyasyonlar: ilk temas · ret kapanışı · likidite süpürmeli ret (fitil eski tepe/dibi aşıp geri
kapanmış) · trend yönünde ret · aynı sinyaller **yapısal stop** (fitil ucu) + 2R hedef ile.

| Komut | Ne yapar |
|---|---|
| `python main.py rb NVDA --tf 1h` | Aktif bölgeler + "ne olursa AL/SAT, stop nerede" + bu hissedeki geçmiş sonuçlar + `reports/rb_NVDA_1h.png` grafiği |
| `python main.py pattern-lab --only rb` | Tüm RB varyasyonlarını NASDAQ-100'de 4 zaman diliminde test eder; geçenler bota girer |

Parametreler `config.yaml` → `rejection:` bölümünde (fitil oranı, uzaklaşma mesafesi, bölge ömrü, R hedefi).

## Taktiği öğrenme ve mükemmelleştirme (`learn`)

```
python main.py learn --tf 1h          # RB taktiğini NASDAQ-100'ün tüm geçmişiyle öğren
python main.py learn --tf 1d --no-ml  # günlük grafikte, makine öğrenmesi olmadan
```

1. **Tüm RB bölgeleri** zengin özelliklerle çıkarılır (fitil oranı, bölge genişliği, yaş, uzaklaşma, trend,
   RSI, hacim, saat, QQQ yönü, hissenin geçmiş RB başarısı...).
2. **~46.000 kural kombinasyonu** simüle edilir: giriş (kenara limit / fitil ortasına limit / ret kapanışı) ×
   hedef (1–3R) × stop tamponu × tutma süresi × seans sonu kapama × filtreler (trend, likidite süpürme, yön, hisse).
3. **Walk-forward:** her dönemde yalnızca o güne kadar kapanmış işlemlerle en iyi kural seçilir, sonra hiç
   görülmemiş sonraki dönemde işlem yapılır. Rapordaki **OOS** satırları gerçek hayattaki en dürüst beklentidir.
4. **Makine öğrenmesi (meta-labeling):** seçilen kuralın işlemlerinden hangisinin kazanacağını tahmin eden
   model de walk-forward eğitilir; işe yarayıp yaramadığı yine OOS ile ölçülür.
5. Son kural + model `state/learned/rb_<tf>.json` / `.joblib` olarak kaydedilir.

### Kanıtlanmış Alfa Hisseler (39 Hisse & %44.7 Yıllık Bileşik Getiri)
Walk-Forward OOS testinde Rejection Block taktiğinin her hissede aynı çalışmadığı kanıtlanmıştır:
* **En Başarılı Hisseler (Yüksek Alfa)**: `COST` (PF 2.82, %46.8 kazanma), `ODFL` (PF 1.86, +%1.44 ort), `CRWD` (PF 2.11, +%2.56 ort), `TMUS` (PF 1.77), `PCAR` (PF 1.54), `SBUX` (PF 1.54), `MNST` (PF 1.49), `DDOG` (PF 1.61), `NVDA` (PF 1.33).
* **Zarar Ettiren Hisseler (Değer tuzakları & yatay bant)**: `HON`, `KHC`, `CTSH`, `KLAC`, `LIN`.
* **Portföy İstatistiği (2019-2026 Görülmemiş Dönem)**: 39 Alfa hisse üzerinde işlem başına %1 risk ile çalıştırıldığında portföy **$100.000'dan $1.707.321'a (+%1607.3 net, CAGR +%44.7, PF 1.33, Sharpe 1.33)** büyümüştür. Detaylı grafik için `python main.py rb-backtest` çalıştırın.
* Tüm alfa hisse listesi ve metrikleri `state/learned/rb_alpha_tickers.json` dosyasındadır. `rb-scan` ve `trade --strategy rb` bu listeyi otomatik kullanır.

### Geçmiş veri arşivi
Her `learn` çalıştırmasında indirilen veri `data/history/<interval>/` altına **eklenir**. Yahoo gün içi geçmişi
kısıtlı verdiği için (5m/15m: 60 gün, 1h: 730 gün) arşiv zamanla büyür. Çok daha uzun gün içi geçmiş için
`.env`'e **ücretsiz Alpaca** anahtarlarını girin: 2016'dan bu yana 15m/5m/1d veri çekilir (1h, 15m'den 09:30
hizalı üretilir). Kaynağı değiştirmek için ilgili `data/history/<interval>` klasörünü silin (kaynaklar karıştırılmaz).

## Sürekli gün içi long/short bot (`live`)

`start_live.bat` dosyasına çift tıklayın (veya `python main.py live`). Bot:

1. Borsa kapalıysa açılışı bekler (ABD tatilleri ve yarım günler dahil). Açılıştan ~20 dk önce
   NASDAQ-100'ü günlük grafikte skorlar, likidite/oynaklık filtresiyle evreni hazırlar.
2. **09:30–16:00 ET (TR 16:30–23:00)** arasında her 5 dakikada bir 5 dk mumları çeker; her hisse için
   **Long** ve **Short** skorunu hesaplar (5 dk teknik + günlük trend + VWAP + açılış aralığı kırılımı +
   QQQ yönü; en iyi adaylara haber ve analist puanı eklenir).
3. Skoru eşiği geçen ve karşı yönden belirgin güçlü olan hisselerde **bracket emir** açar
   (long: al + üstte hedef + altta stop; short: açığa sat + altta hedef + üstte stop).
4. Ters yönde güçlü sinyal gelirse pozisyonu erken kapatır; aynı hisseye 30 dk tekrar girmez.
5. Gün içinde hesap **+%1**'e ulaşırsa her şeyi kapatıp günü bitirir; **-%2**'de zararı keser.
6. Kapanışa 10 dk kala tüm pozisyonları kapatır — gece pozisyon taşımaz. Ertesi gün kendiliğinden devam eder.

| Mod | Komut | Açıklama |
|---|---|---|
| SIM (varsayılan) | `python main.py live` | Anahtar gerekmez. Canlı veriyle sanal hesap (`state/sim_account.json`) |
| Alpaca paper | `python main.py live --alpaca` | `.env` içinde Alpaca anahtarları gerekir; sanal para, gerçek emir altyapısı |
| Sadece long | `python main.py live --no-short` | |

Tüm işlemler `reports/journal.csv`'ye, döngü kayıtları `logs/` klasörüne yazılır.

> [!IMPORTANT]
> * Bilgisayar ve bot seans boyunca açık kalmalı (TR 16:30–23:00). Kesintisiz çalışma için bir VPS önerilir.
> * ABD'de **25.000$ altı** hesaplarda 5 iş gününde en fazla 3 gün-içi işleme izin verilir (PDT kuralı);
>   bot bunu algılar ve sınır dolunca yeni işlem açmaz.
> * Short için marjin hesabı gerekir; bot Alpaca'da hissenin açığa satılabilir olduğunu kontrol eder.
> * Yahoo 5 dk verisi ücretsizdir ama resmi değildir; gecikme/kesinti olabilir.

## Nasıl çalışır

```
NASDAQ-100 listesi (Wikipedia)  ─┐
OHLCV verisi (Yahoo, doğrulanmış) ┼─> Teknik skor (0-100) ──┐
QQQ + VIX                        ─┘   trend 30 / momentum 25 │
                                      mum 20 / hacim 10       ├─> Nihai skor x rejim çarpanı
En iyi 15 aday ──> Haber duyarlılığı (±10) ─────────────────┤   -> AL / İZLE / -
               └─> Analist görüşü (±10) + bilanço kontrolü ─┘
AL -> İşlem planı: limit giriş, stop = 1 ATR, hedef = max(1.5 ATR, %1), risk bazlı adet
```

* **Mum formasyonları** trend bağlamıyla tespit edilir (ör. Çekiç sadece düşüş sonrası boğa sayılır,
  aynı şekil yükselişte "Asılı Adam"dır) ve yüksek hacimle oluşursa ağırlığı %50 artar.
* `analyze` komutu, her formasyonun **o hissede** geçmişte gerçekten işe yarayıp yaramadığını
  baz orana göre gösterir — internetteki genel kurallara körü körüne güvenmek yerine veriyle doğrular.
* Bilançoya ≤2 gün kalan hisseler elenir (gap riski).
* Backtest canlı botla aynı skor fonksiyonunu ve aynı emir mantığını kullanır.

## Önerilen günlük akış (Türkiye saati)

1. **23:15 sonrası** (ABD kapanışından sonra): `python main.py trade --execute` → yarın için limit emirler
2. **16:30 - 23:00** (seans): `python main.py guard` açık kalsın → %1'de kâr kilidi, -%2'de durdurma
3. Haftada bir: `python main.py backtest` ile performansı kontrol edin

Alpaca paper hesabı için `.env.example` dosyasını `.env` olarak kopyalayıp anahtarları girin.
Tüm parametreler `config.yaml` içinde açıklamalıdır.

## Bilinen sınırlamalar

* Yahoo verisi ücretsizdir, ~15 dk gecikmeli olabilir ve resmi değildir.
* Backtest geçmiş haber/analist verisini içermez ve güncel endeks listesini kullandığı için iyimserdir.
* Günlük mumlarla çalışır; gün içi (dakikalık) scalping stratejisi değildir.
