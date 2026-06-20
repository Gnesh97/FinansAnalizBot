# AGENTS.md

## Communication Rules

Default mode: compact.

Normal task cevaplarında maksimum 12 satır yaz.

Cevap formatı:

1. Changed:

* değişen dosyalar / kısa özet

2. Validation:

* çalıştırılan komutlar
* passed / failed

3. Result:

* exact_match true/false
* metrik değişti mi?

4. Next:

* tek cümlelik öneri

## Long Output Allowed Only When

Uzun açıklama sadece şu durumlarda yaz:

* User açıkça "detaylı rapor" isterse
* Strategy/backtest report analizi yapılacaksa
* Performance profile analizi yapılacaksa
* Validation failed olduysa ve sebep açıklanacaksa
* Kritik bug/risk varsa

## Forbidden

* Promptu tekrar etme.
* Uzun implementation essay yazma.
* Her değişikliği satır satır açıklama.
* Gereksiz arka plan bilgisi verme.
* Rapor istenmediyse uzun yorum yazma.

## Validation Reporting

Her kod değişikliğinden sonra sadece şunları bildir:

* files changed
* commands run
* exact_match true/false
* changed metrics, if any
* next action
