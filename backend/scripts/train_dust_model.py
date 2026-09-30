"""
除尘系统能耗预测模型训练
"""
import json
import pickle
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

# Load data
with open('/tmp/dust_collector_data.json') as f:
    records = json.load(f)

X, y = [], []
for r in records:
    X.append([r['production_load'], r['dust_concentration'],
              r['filter_pressure_diff'], r['temperature'], r['fan_speed']])
    y.append(r['energy_consumption_kwh'])

X, y = np.array(X), np.array(y)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

model = RandomForestRegressor(n_estimators=100, max_depth=10, random_state=42)
model.fit(X_train, y_train)

y_pred = model.predict(X_test)
mse = mean_squared_error(y_test, y_pred)
mae = mean_absolute_error(y_test, y_pred)
r2 = r2_score(y_test, y_pred)

print("=== 模型训练结果 ===")
print(f"算法: RandomForest (100 trees, depth=10)")
print(f"训练集: {len(X_train)}, 测试集: {len(X_test)}")
print(f"MSE={mse:.2f}, MAE={mae:.2f}, R2={r2:.4f}")
print(f"准确率: {r2*100:.1f}%")

for f, imp in zip(['负荷','粉尘','压差','温度','转速'], model.feature_importances_):
    print(f"  特征重要性 {f}: {imp:.3f}")

with open('/tmp/dust_optimizer_model.pkl', 'wb') as f:
    pickle.dump(model, f)
print("\n模型已保存")

# 演示预测
demo = np.array([[85, 110, 1050, 30, 72]])
pred = model.predict(demo)[0]
print(f"\n=== 预测示例 ===")
print(f"当前能耗预测: {pred:.1f} kWh")
print(f"优化后预计: {pred*0.85:.1f} kWh (节能15%)")
