"""
批量导入除尘系统训练数据
"""
import json
from database.config import SessionLocal
from models.core import EnergyConsumption
from sqlalchemy import func

with open('/tmp/dust_collector_data.json') as f:
    records = json.load(f)

db = SessionLocal()
count = 0
for r in records:
    record = EnergyConsumption(
        entity_id="dust_collector_01",
        entity_type="dust_collector",
        energy_type="electricity",
        consumption=r["energy_consumption_kwh"],
        unit="kWh",
        recorded_at=r["timestamp"]
    )
    db.add(record)
    count += 1
    if count % 100 == 0:
        db.commit()
        print(f"  Inserted {count}/{len(records)}...")

db.commit()
total = db.query(func.count(EnergyConsumption.id)).filter(
    EnergyConsumption.entity_id == "dust_collector_01"
).scalar()
avg = db.query(func.avg(EnergyConsumption.consumption)).filter(
    EnergyConsumption.entity_id == "dust_collector_01"
).scalar()
print(f"Done! {total} records, avg: {avg:.1f} kWh")
db.close()
