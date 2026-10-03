# Statistics — SYNTHETIC toy-simulator data — illustrative prototype output, NOT cluster measurements

MTTR censored at 360 s (non-recovered episodes enter as 360 s). n = 6 per fault type.

## Mann–Whitney U (two-sided) on MTTR, per fault type

| Comparison | Fault | median A (s) | median B (s) | U | p |
|---|---|---|---|---|---|
| SAC PER s0 vs Runbook | F1 | 50 | 80 | 6.0 | 0.050 |
| SAC PER s0 vs Runbook | F2 | 70 | 60 | 21.0 | 0.655 |
| SAC PER s0 vs Runbook | F3 | 60 | 40 | 27.0 | 0.112 |
| SAC PER s0 vs Runbook | F4 | 30 | 70 | 4.5 | 0.032 |
| SAC PER s0 vs Runbook | ALL | 60 | 60 | 212.5 | 0.104 |
| SAC PER s0 vs SAC uniform s0 | F1 | 50 | 60 | 13.0 | 0.446 |
| SAC PER s0 vs SAC uniform s0 | F2 | 70 | 60 | 19.0 | 0.935 |
| SAC PER s0 vs SAC uniform s0 | F3 | 60 | 50 | 21.0 | 0.640 |
| SAC PER s0 vs SAC uniform s0 | F4 | 30 | 20 | 21.5 | 0.588 |
| SAC PER s0 vs SAC uniform s0 | ALL | 60 | 50 | 295.5 | 0.880 |
| SAC PER s0 vs K8s + HPA | F1 | 50 | 360 | 0.0 | 0.003 |
| SAC PER s0 vs K8s + HPA | F2 | 70 | 360 | 3.0 | 0.010 |
| SAC PER s0 vs K8s + HPA | F3 | 60 | 360 | 0.0 | 0.002 |
| SAC PER s0 vs K8s + HPA | F4 | 30 | 60 | 4.0 | 0.020 |
| SAC PER s0 vs K8s + HPA | ALL | 60 | 360 | 49.0 | 0.000 |
| Runbook vs K8s + HPA | F1 | 80 | 360 | 0.0 | 0.002 |
| Runbook vs K8s + HPA | F2 | 60 | 360 | 0.0 | 0.001 |
| Runbook vs K8s + HPA | F3 | 40 | 360 | 0.0 | 0.002 |
| Runbook vs K8s + HPA | F4 | 70 | 60 | 20.0 | 0.794 |
| Runbook vs K8s + HPA | ALL | 60 | 360 | 58.0 | 0.000 |

## Bootstrap 95% CI of median MTTR over the 24 fault episodes (5,000 resamples)

| Policy | median (s) | 95% CI (s) |
|---|---|---|
| K8s default | 360 | [360, 360] |
| K8s + HPA | 360 | [360, 360] |
| Runbook | 60 | [60, 60] |
| SAC uniform s0 | 50 | [40, 60] |
| SAC PER s0 | 60 | [40, 60] |
