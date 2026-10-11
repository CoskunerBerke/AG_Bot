"""DMC (Kaanermi) strateji paketi — kurallar: knowledge/STRATEGY_SPEC.md.

Katmanlar:
  data       : Alpaca 1 dk barları (NY saati), önbellek, yeniden örnekleme
  structure  : swing, FVG, rejection block, OTE, ATR — saf fonksiyonlar
  levels     : gün öncesi hazırlık (PDH/PDL, premarket H/L, HTF FVG, günlük bias)
  setups     : 10AM ve Lunch durum makineleri (bar bar beslenir; canlı ve backtest aynı kod)
  engine     : bir günü bar bar oynatır, setup'lardan emir niyetleri toplar, günlük limitleri uygular
  backtest   : 1 dk barlarda emir/stop/TP simülasyonu ve R istatistikleri
  live       : Alpaca üzerinde sürekli çalışan yürütücü
"""
