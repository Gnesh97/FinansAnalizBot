# Proje Süreci

Son güncelleme: 2026-06-18

Bu dosya `testdata/universe_scanner.py` etrafındaki strateji mimarisi, refactor kararları ve backtest çalıştırma akışının güncel durumunu özetler.

## Ana Hedef

Tek stratejiye sıkışmış `universe_scanner.py` yapısını, sonuçları bozmadan çoklu strateji test edebilecek hale getirmek.

Öncelikler:

- Mevcut `current_pullback_mean_reversion` sonucu geriye dönük uyumlu kalacak.
- Yeni stratejiler `strategy_name` ile seçilecek.
- `relative_strength_rotation` ayrı bir strateji olarak çalışacak.
- Raporlar strateji adı, strateji ailesi, RS parametreleri, benchmark karşılaştırmaları ve sembol attribution bilgilerini taşıyacak.
- Büyük refactor sırasında backtest metrikleri değişmeyecek.

## Desteklenen Stratejiler

Şu anda çalışan stratejiler:

- `current_pullback_mean_reversion`
- `relative_strength_rotation`

İskelet olarak tanınması planlanan stratejiler:

- `cross_sectional_momentum`
- `dual_momentum`
- `donchian_breakout`
- `ma_slope_trend`

Geçersiz `strategy_name` açık `ValueError` vermeli:

```text
Unknown strategy_name: X
```

## Strateji Ailesi Kararı

`strategy_name` -> `Strategy Family` mapping kararı:

| Strategy Name | Strategy Family |
| --- | --- |
| `current_pullback_mean_reversion` | `mean_reversion / pullback_in_uptrend` |
| `relative_strength_rotation` | `relative_strength / momentum` |
| `cross_sectional_momentum` | `relative_strength / momentum` |
| `dual_momentum` | `dual_momentum` |
| `donchian_breakout` | `breakout / trend_following` |
| `ma_slope_trend` | `trend_following` |

## Relative Strength Rotation Kuralları

İlk RS stratejisi daily timeframe mantığıyla eklendi.

Varsayılan parametreler:

```python
rs_lookback_days = 126
rs_top_n = 20
rs_rebalance_frequency = "weekly"
rs_score_mode = "return_over_vol"
rs_vol_lookback_days = 60
rs_trend_sma_days = 200
rs_use_market_regime = True
rs_market_regime_sma_days = 200
rs_min_price = 10
rs_max_atr_pct = 12
```

Kararlar:

- Universe filtresi: `Close >= rs_min_price`, `ATR_Pct <= rs_max_atr_pct`, `Close > SMA(rs_trend_sma_days)`.
- `ATR_Pct = 100 * ATR(20) / Close`.
- Market regime aktifse benchmark olarak `QQQ` veya `SPY` kullanılır.
- Regime bozuksa yeni pozisyon açılmaz.
- `weekly`, `monthly`, `daily` rebalance desteklenir.
- Entry/exit işlemleri lookahead yaratmamak için bir sonraki bar open ile yapılır.
- İlk versiyonda equal-weight sizing kullanılır: `target_weight = 1 / rs_top_n`.
- `evaluation_mode="single"` dışındaki modlar için RS tarafında şimdilik kısıt vardır.

## Raporlama Durumu

Ana kompakt çıktı dosyaları:

- `testdata/strategy_compact_report.csv`
- `testdata/strategy_compact_report.md`

Ek attribution çıktısı:

- `testdata/strategy_symbol_attribution_report.csv`

Compact report içinde eklenen önemli kolonlar:

- `Strategy Name`
- `Strategy Family`
- RS parametre kolonları
- `Avg Holding Bars`
- `Median Holding Bars`
- `Turnover Estimate`
- `Avg Positions Held`
- `Max Positions Held`
- `QQQ BuyHold Return (%)`
- `SPY BuyHold Return (%)`
- `EqualWeight Universe Return (%)`
- `Strategy Excess Return vs QQQ (%)`
- `Strategy Excess Return vs EqualWeight (%)`

Diagnostic kolonları esnek tutulacak. Her stratejide her kolon dolmayabilir; eksik stratejiye özel alanlar raporu patlatmamalı.

Ana diagnostic kolonları:

- `Raw Trigger Count`
- `Raw Entry Signal Count`
- `Final Entry Signal Count`
- `Trade Count`

## Refactor Kararı ve Mevcut Durum

İstenen nihai yapı:

```text
config.py
data_engine.py
indicators.py
strategies/base.py
strategies/current_pullback_mean_reversion.py
strategies/relative_strength_rotation.py
portfolio_manager.py
reports.py
diagnostics.py
runner.py
universe_scanner.py
```

Önemli karar:

- Facade/wrapper refactor yeterli kabul edilmiyor.
- Gerçek kod parçaları yeni modüllere taşınmalı.
- Yeni modüller mümkün olduğunca `universe_scanner_legacy.py` dosyasına bağımlı olmamalı.
- Refactor küçük adımlarla yapılacak.
- Her extraction sonrası davranış korunacak.

İlk gerçek extraction kapsamı:

- `testdata/indicators.py`
- `testdata/diagnostics.py`
- `testdata/strategies/relative_strength_rotation.py`

Bu üç dosyada gerçek kod parçaları taşındı. Ancak proje tamamen modüler hale gelmiş değil.

Bilinen mevcut teknik borç:

- `testdata/universe_scanner.py` hâlâ public facade olarak `universe_scanner_legacy.py` üzerinden API yüzeyini taşıyor.
- `testdata/runner.py`, `testdata/config.py`, `testdata/data_engine.py`, `testdata/portfolio_manager.py`, `testdata/reports.py`, `testdata/strategies/base.py` ve `testdata/strategies/current_pullback_mean_reversion.py` içinde legacy import bağımlılıkları hâlâ var.
- Bunlar sonraki küçük extraction adımlarında kaldırılmalı.

## Public Entrypoint Kararı

Public API aynı kalmalı:

- `run_universe_scanner`
- `run_strategy_experiments`

Programatik kullanımda dışarıdan verilen `experiments` listesi aynen çalışmalı.

Karar:

- `experiments is None` ise default experiment listesi kullanılabilir.
- `experiments == []` ise default experiment listesi kullanılabilir.
- Non-empty `experiments` listesi verilmişse default listeye düşülmeyecek.

Son düzeltme:

- `run_strategy_experiments(..., experiments=[...])` artık verilen listeyi koruyor.
- `python testdata/universe_scanner.py` doğrudan çalıştırıldığında validation için beklenen üç experiment geçiliyor.

## Güncel Validation Experiment Seti

`testdata/universe_scanner.py` içindeki direct main şu üç experiment'i çalıştıracak şekilde ayarlandı:

```text
compat_current_strategy
rs_m63_sma100
rs_m63_no_regime
```

Beklenen strateji eşleşmeleri:

| Experiment Name | Strategy Name |
| --- | --- |
| `compat_current_strategy` | `current_pullback_mean_reversion` |
| `rs_m63_sma100` | `relative_strength_rotation` |
| `rs_m63_no_regime` | `relative_strength_rotation` |

## Backtest Nasıl Çalıştırılır?

Doğrudan validation setini çalıştırmak için:

```powershell
python testdata\universe_scanner.py
```

Programatik kullanım:

```python
from testdata.universe_scanner import run_strategy_experiments

experiments = [
    {"name": "compat_current_strategy", "strategy_name": "current_pullback_mean_reversion"},
    {"name": "rs_m63_sma100", "strategy_name": "relative_strength_rotation", "rs_lookback_days": 63, "rs_trend_sma_days": 100},
    {"name": "rs_m63_no_regime", "strategy_name": "relative_strength_rotation", "rs_lookback_days": 63, "rs_use_market_regime": False},
]

report = run_strategy_experiments(
    "testdata/hisseler",
    experiments=experiments,
    run_profile="fast",
    quick_mode=True,
    compact_report=True,
    write_experiment_detail_files=False,
)
```

## Son Doğrulamalar

Son kontrol çıktısı:

```text
['compat_current_strategy', 'rs_m63_sma100', 'rs_m63_no_regime']
['current_pullback_mean_reversion', 'relative_strength_rotation', 'relative_strength_rotation']
```

Küçük sembol setiyle gerçek runner çağrısında compact report içinde şu satırlar görüldü:

```text
rs_m63_sma100      relative_strength_rotation
rs_m63_no_regime  relative_strength_rotation
compat_current_strategy current_pullback_mean_reversion
```

Syntax kontrolü:

```powershell
python -m py_compile testdata\universe_scanner.py testdata\universe_scanner_legacy.py
```

Bu kontrol geçti.

## Sonraki Güvenli Adımlar

Yeni strateji eklemeden önce önerilen refactor sırası:

1. `strategies/current_pullback_mean_reversion.py` içindeki legacy import kaldırılıp gerçek pullback sinyal kodu taşınacak.
2. `strategies/base.py` içindeki `generate_strategy_signals` gerçek dispatcher olacak.
3. `runner.py` içindeki `run_strategy_experiments` gerçek runner olarak ayrılacak.
4. `reports.py` ve `diagnostics.py` rapor/diagnostic yardımcılarını tamamen devralacak.
5. `data_engine.py` veri okuma, RTH bar hazırlama ve cache akışını devralacak.
6. `portfolio_manager.py` portfolio/backtest sınıflarını legacy bağımlılığı olmadan taşıyacak.
7. Her adımda aynı experiment setiyle metrik parity kontrolü yapılacak.

Korunması gereken metrikler:

- `Net Getiri`
- `Max Drawdown`
- `Trade Count`
- `Profit Factor`
- `Expectancy R`
- `Worst R`
- `QQQ BuyHold Return`
- `EqualWeight Universe Return`

## Dikkat Edilecek Noktalar

- Yeni özellik ekleme ile refactor ayrı tutulmalı.
- Backtest sonuçlarını etkileyebilecek hesaplama sırası değiştirilmemeli.
- Generated CSV/MD rapor dosyaları commit kapsamına alınmadan önce ayrıca kontrol edilmeli.
- Büyük tek seferlik refactor yerine küçük extraction + validation döngüsü izlenmeli.
