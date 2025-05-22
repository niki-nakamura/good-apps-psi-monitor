# good-apps-psi-monitor
PageSpeed InsightsによるCore Web Vitals監視：PageSpeed Insights (PSI) API を用いて対象サイト（例: good-apps.jp）の Core Web Vitals 指標（LCP, FID, CLS）を毎日チェックし、該当指標が「Poor（不良）」と評価された場合にSlack通知を送るソリューションです。PSIは各指標の75パーセンタイル値とともに、パフォーマンス評価を Good/Needs Improvement/Poor の3段階（API上は "FAST"/"AVERAGE"/"SLOW"）で返します
