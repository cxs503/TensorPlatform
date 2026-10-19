#!/usr/bin/env python3
"""Retrain models 13 (XGBoost) and 14 (LightGBM) directly in backend container."""
import sys, time, numpy as np
sys.path.insert(0, '/app')
from services.data_loader import get_training_data
from services.real_training_service import get_training_service
from database.config import get_db

db = next(get_db())
X, y, feature_names = get_training_data(db, dataset_id=7, scenario='generic')
print(f'Data: X={X.shape}, y={y.shape}')

svc = get_training_service()

# Train XGBoost (model 13)
jid_xgb = svc.create_job({
    'name': 'Temp-XGBoost', 'task_type': 'regression',
    'model_type': 'xgboost', 'model_id': 13, 'dataset_id': 7,
    'n_estimators': 100, 'max_depth': 6, 'learning_rate': 0.1,
    'normalize': True,
})
svc.start_training(jid_xgb, {'X': X.tolist(), 'y': y.tolist()})

# Train LightGBM (model 14)
jid_lgb = svc.create_job({
    'name': 'Temp-LightGBM', 'task_type': 'regression',
    'model_type': 'lightgbm', 'model_id': 14, 'dataset_id': 7,
    'n_estimators': 100, 'max_depth': 6, 'learning_rate': 0.1,
    'normalize': True,
})
svc.start_training(jid_lgb, {'X': X.tolist(), 'y': y.tolist()})

for jid, name in [(jid_xgb, 'XGBoost'), (jid_lgb, 'LightGBM')]:
    for _ in range(60):
        st = svc.get_job_status(jid)
        if st['status'] in ('completed', 'failed'):
            print(f'{name}: {st["status"]}, metrics={st.get("best_metrics", {})}')
            break
        time.sleep(1)

db.close()
print('DONE')
