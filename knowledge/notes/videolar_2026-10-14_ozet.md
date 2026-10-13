# 8 video (2026-10-14) — kodlanabilir öğrenimler (kendi özetimiz)

Kaynaklar: SIFIRDAN TRADING #9 Order Block, #11 Premium-Discount, #12 OTE, #15 BOX Setup, #21 Rejection Block;
"ADAMI DELİ EDEN NASDAQ'A AYARI VERDİK" (canlı Lunch), "Paranın Hilesini Nasıl Buldum" (Lunch işlem analizi),
"SANIRIM 16 YAŞINDA ARABA ALACAĞIM – Canlı Lunch İşlemim" (txt boş geldi; sadece kareler).
Yöntem: yeni taktikler önce **Bot B**'de (`config.b.yaml`) açılır; canlıda iyi giderse Bot A'ya taşınır.

## #21 Rejection Block
1. ✓ RB = önemli likiditeyi (dragon liquidity, FVG, OB, swing) **tek seferde, uzun ve temiz bir fitille** süpüren
   mumun fitili; kutu fitilin tepesinden dibine. Bot: `find_rbs` (süpürme şartı var).
2. **Uzun temiz fitil** şartı → `setups.dmc_rb.min_wick_frac` (fitil / mum boyu). Bot B'de deneniyor.
3. Fiyat geri gelince çoğunlukla **kutunun ortasından (C-level)** ya da fitilin tamamını almadan döner →
   `setups.dmc_rb.entry_mode: mid` (limit kutunun ortasında). Bot B'de deneniyor.
4. HTF'de (1H/4H) bir FVG'den tepki alıp saatlerce giden hareketin başladığı bölge ileride RB olarak kullanılır
   (bot: hazırlıkta 60/15 dk RB'ler).
5. Forex, vadeli, endeks hepsinde çalışır (EURUSD örneği).

## #15 BOX Setup (AMD'nin giriş kalıbı)
1. Akümülasyon (ör. 5 dk'da ~50 dk yatay) → kutunun alt/üst çizgisi çekilir.
2. Manipülasyon: kutunun bir tarafı süpürülür (ör. 09:30'da premarket akümülasyonunun tepesi).
3. Ters yöne displacement/FVG ile kırılım (dağıtım).
4. **Giriş: kutunun manipülasyon tarafındaki kenarı** (long'da kutunun dibi, short'ta tepesi), limit.
   Stop: manipülasyonun ötesi ("klasik"); hedef: low-resistance liquidity / önemli likidite (örnek 5R).
   Bot: yeni model `setups.box` (5 dk, `box_bars` 8, yükseklik ≤ 3 × ort. 5 dk mum, kırılım 12 mum içinde). Bot B.
   Not: premarket akümülasyonu + 09:30 manipülasyonu örneği botta yok (canlı veri 09:30'dan başlıyor).

## Canlı Lunch işlemleri
1. ✓ 5 dk mum lunch/NY PM dibini iğneler; **gövde seviyenin ötesinde kapanırsa süpürme değildir** (XAU örneği).
   Bot: `bl < seviye < kapanış` şartı zaten var.
2. **MSS onayı**: ilk mumda MSS yoksa girme; sonraki mumda MSS gelirken gir ("hacmi verirken dahil oldum").
   Bot: `setups.lunch.mss_confirm` (1 dk kapanış süpürme mumunun tepesini geçince giriş, 15 dk bekleme). Bot B.
3. ✓ SMT: NQ lunch dibini alıp ES almazsa güç. EURUSD'de kardeş parite (GBPUSD) kontrol edilmediği için stop.
4. Stop: MSS sırasında oluşan likidite / OB'nin ortası. Canlı NAS100 (29 Ara): stop ~6,3 puan, ~6R, fiyat
   gidince stop yeni swing dibine taşındı ("NEW SL"), hedef sabah seansı tepesi.
5. **Fake MSS**: MSS sonrası geri çekilme FVG'ye girip devam kapanışı gelmezse MSS sahtedir → ters yön.
6. **OTE akümülasyondan akümülasyona çekilince daha iyi çalışır**; OTE'ye fitil gelince hemen girme, fitilin
   kapanışını bekle ve sonraki MSS/displacement mumunda gir.
7. Hedefleri HTF'de (4H FVG, 45/30 dk OB çakışması) seç; işlemi oraya kadar tut.

## #12 OTE
1. ✓ OTE 0.62–0.705 (0.62 en az, 0.705 en çok); Kaan'ın hilesi: **bu bandın ortası** giriş. 1H'te çiziyor.
2. Fibonacci "sihirli sayı" değil, tekrar eden psikoloji/likidite davranışı. SMT ve AMD ile birleştiriyor.

## #11 Premium / Discount
1. Swing'ten swing'e çekilen aralığın 0.5'i (equilibrium): üstü premium, altı discount.
2. Fiyat **discount'a inmeden** bir PDA'dan (FVG/OB) dönerse smart money "ucuzlamadan" alıyordur → devam.
3. Kaan bunu eskiden **bias** için kullanıyordu (şu an kullanmıyor). Bot: henüz yok (bias alternatifi olarak denenebilir).

## #9 Order Block
1. OB = yüksek displacement içindeki küçük ters renkli mum(lar); 2–3 mumu birlikte alır, **ortasını** kullanır.
2. HTF'de (1H/2H) daha net; zaman dilimi değiştirerek bulunur.
3. Kaan: OB **giriş için biraz riskli, hedef (TP) için daha iyi**; TP'leri displacement içindeki OB'lere kademeli böl.
4. Lunch'taki PDA genellikle bir OB'dir. Bot: OB tespiti yok (yalnız Kaan modelinde breaker); TP adayı olarak eklenebilir.
