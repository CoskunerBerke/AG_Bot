"""Order Flow (Emir Akışı), Hacim Deltası ve Kurumsal Likidite Emilimi (Absorption) Analizi.

Bu modül:
1. 1 dakikalık ve 5 dakikalık barlarda Kümülatif Hacim Deltasını (CVD) hesaplar.
2. Rejection Block seviyelerine yaklaşıldığında pasif/agresif alıcı baskısını ölçer.
3. Fiyat bloğa çok yaklaştığında (örneğin 10-15 cent / %0.30 kala) kurumsal alıcıların
   tahtayı süpürdüğünü (Absorption) tespit ederse, limit emri fiyata yaklaştırarak
   (Marketable Smart Entry) emrin kaçmasını engeller.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("order_flow")


def compute_volume_delta(df_1m: pd.DataFrame) -> pd.DataFrame:
    """1 dakikalık OHLCV verisinden Bar İçi Alıcı/Satıcı Hacim Deltasını tahmin eder.
    
    Yaklaşım (Lee-Ready & Tick Direction Bar Proxy):
    - Kapanış > Açılış: Alıcı baskın bar (Buy Volume = Volume * (Close - Low) / (High - Low))
    - Kapanış < Açılış: Satıcı baskın bar (Sell Volume = Volume * (High - Close) / (High - Low))
    - Delta = Buy Volume - Sell Volume
    - CVD = Delta'nın kümülatif toplamı
    """
    d = df_1m.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)

    o, h, l, c, v = d["Open"], d["High"], d["Low"], d["Close"], d["Volume"]
    rng = (h - l).replace(0, np.nan)
    
    # Mum içi alıcı/satıcı güç ağırlığı
    buy_ratio = ((c - l) / rng).fillna(0.5).clip(0.0, 1.0)
    sell_ratio = ((h - c) / rng).fillna(0.5).clip(0.0, 1.0)
    
    d["buy_vol"] = v * buy_ratio
    d["sell_vol"] = v * sell_ratio
    d["delta"] = d["buy_vol"] - d["sell_vol"]
    d["cvd"] = d["delta"].cumsum()
    d["vol_sma20"] = v.rolling(20, min_periods=5).mean()
    d["rel_vol"] = (v / d["vol_sma20"]).fillna(1.0)
    return d


def detect_absorption(df_1m: pd.DataFrame, level: float, side: str = "LONG", window: int = 10) -> dict[str, Any]:
    """Belirli bir fiyat seviyesi etrafında kurumsal likidite emilimini (Absorption) analiz eder.
    
    Özellikler:
    - Fiyat o seviyeye indiğinde hacim ortalamanın üzerinde mi?
    - Delta pozitif mi (satış baskısına rağmen alıcılar tahtayı tutuyor mu)?
    - CVD yukarı dönüyor mu?
    """
    if len(df_1m) < window:
        return {"absorption": False, "delta": 0.0, "rel_vol": 1.0, "reason": "yetersiz veri"}
        
    d = compute_volume_delta(df_1m)
    recent = d.iloc[-window:]
    
    total_vol = recent["Volume"].sum()
    net_delta = recent["delta"].sum()
    delta_ratio = net_delta / total_vol if total_vol > 0 else 0.0
    avg_rel_vol = recent["rel_vol"].mean()
    
    # Long için: Net delta güçlü pozitif veya son barlarda delta yukarı dönüyor
    # Short için: Net delta güçlü negatif
    if side == "LONG":
        is_bull_delta = delta_ratio >= 0.15 or (recent["delta"].iloc[-3:].sum() > 0 and avg_rel_vol >= 1.15)
        absorption = bool(is_bull_delta and avg_rel_vol >= 1.10)
    else:
        is_bear_delta = delta_ratio <= -0.15 or (recent["delta"].iloc[-3:].sum() < 0 and avg_rel_vol >= 1.15)
        absorption = bool(is_bear_delta and avg_rel_vol >= 1.10)
        
    return {
        "absorption": absorption,
        "delta": float(net_delta),
        "delta_ratio": float(delta_ratio),
        "avg_rel_vol": float(avg_rel_vol),
        "last_close": float(recent["Close"].iloc[-1]),
        "level": float(level),
        "side": side,
    }


def should_smart_enter(
    current_price: float,
    limit_target: float,
    df_1m: pd.DataFrame | None,
    side: str = "LONG",
    max_slippage_pct: float = 0.35
) -> tuple[bool, float, str]:
    """Fiyat Rejection Block'a çok yaklaştığında (örneğin 10-15 cent kala)
    Order Flow teyidi ile erken akıllı giriş yapılıp yapılmayacağına karar verir.
    
    Dönüş: (Giriş Yapılsın mı?, Önerilen Giriş Fiyatı, Gerekçe)
    """
    diff_pct = (abs(current_price - limit_target) / limit_target) * 100
    
    # Zaten fiyata eşit veya daha iyi fiyattaysa doğrudan limit gir
    if side == "LONG" and current_price <= limit_target:
        return True, current_price, "Hedef limit seviyesinde veya altında"
    if side == "SHORT" and current_price >= limit_target:
        return True, current_price, "Hedef limit seviyesinde veya üstünde"
        
    # Fiyat hedef limitin çok uzağındaysa bekle
    if diff_pct > max_slippage_pct:
        return False, limit_target, f"Fiyat hedefin %{diff_pct:.2f} uzağında (> %{max_slippage_pct:.2f})"
        
    # Fiyat hedefin çok yakınında (örneğin 10-15 cent / %0.30 içinde)
    # 1 dakikalık Order Flow'u kontrol et
    if df_1m is not None and not df_1m.empty and len(df_1m) >= 5:
        ab = detect_absorption(df_1m, limit_target, side=side)
        if ab["absorption"]:
            return True, current_price, (
                f"Kurumsal Soğurma Teyit Edildi (Delta: {ab['delta']:+,.0f}, "
                f"Hacim: {ab['avg_rel_vol']:.1f}x) -> Kaçırmamak için ${current_price:.2f}'den akıllı giriş"
            )
            
    # Hacim verisi yoksa ama fark %0.15'ten az ise (birkaç cent)
    if diff_pct <= 0.15:
        return True, current_price, f"Fiyat 1-2 kademe ({diff_pct:.2f}%) mesafede -> Akıllı tolerans girişi"
        
    return False, limit_target, f"Bekleniyor (Fark: %{diff_pct:.2f})"
