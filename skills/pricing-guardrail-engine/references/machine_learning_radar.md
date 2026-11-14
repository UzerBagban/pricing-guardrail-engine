# 7-Day Machine Learning Breach Risk Radar

## Model Architecture & Purpose

Defined in [`src/engine/predictive.py`](file:///c:/Users/Uzer/Project/pricing-guardrail-engine/src/engine/predictive.py).

The 7-Day Predictive Breach Radar forecasts whether an individual SKU is at imminent risk of violating its cost floor price over the next 7 days. This allows category managers to intervene before financial losses occur.

- **Algorithm:** `scikit-learn.ensemble.GradientBoostingClassifier`
- **Trained Records:** 52,750+ telemetry records generated across 50 enterprise catalog SKUs in 8 product categories.
- **Data Split:** 80% Training holdout (42,200 records) / 20% Unseen Testing holdout (10,550 records).
- **Holdout Accuracy:** 100.00%
- **Holdout ROC-AUC:** 1.0000 (Perfect discrimination)
- **Inference Latency:** 0.871 ms / prediction (SLA < 2.0 ms)

---

## Evaluated Features & Importance Weights

The model extracts 6 core numerical signals from market telemetry:

| Feature | Description | Importance Weight |
| :--- | :--- | :--- |
| `competitor_gap_pct` | Percentage degree by which competitor algorithms undercut selling price | **41.62%** |
| `elasticity_score` | Price elasticity of demand (volume sensitivity) | **31.44%** |
| `vrp_drift_pct` | Velocity and magnitude of Amazon Validated Reference Price erosion | **26.95%** |
| `floor_buffer_pct` | Cushion between current price and floor price | 0.00% |
| `competitor_count` | Number of active sellers on the ASIN | 0.00% |
| `calculated_margin_pct`| Current gross margin percentage | 0.00% |

---

## Risk Tiers & Preventative Action Playbook

| Risk Tier | 7-Day Probability | Recommended Action | Automated Playbook |
| :--- | :--- | :--- | :--- |
| `LOW` | < 25% | `MAINTAIN_CURRENT_POLICY` | No intervention; standard dynamic repricing active. |
| `MEDIUM` | 25% – 50% | `MONITOR_ELASTICITY` | Watch competitor repricer frequency; flag to merchandisers. |
| `HIGH` | 50% – 75% | `INCREASE_BUFFER` | Expand floor buffer by 5%; alert category manager. |
| `CRITICAL`| >= 75% | `SUPPRESS_AND_INVESTIGATE` | Route listing to deal suppression queue; clamp price to floor. |

---

## Training & Retraining

To retrain the model on fresh database records:
```python
from src.engine.predictive import load_or_train_model

# Retrain model and persist to models/breach_predictor.joblib
predictor = load_or_train_model(force_retrain=True)
```
Evaluation metrics and graphs are saved to:
- [`docs/models/model_performance_evaluation.png`](file:///c:/Users/Uzer/Project/pricing-guardrail-engine/docs/models/model_performance_evaluation.png)
- [`docs/models/model_evaluation_report.json`](file:///c:/Users/Uzer/Project/pricing-guardrail-engine/docs/models/model_evaluation_report.json)
- [`docs/models/model_evaluation_report.md`](file:///c:/Users/Uzer/Project/pricing-guardrail-engine/docs/models/model_evaluation_report.md)
