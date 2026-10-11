"""
数据库种子数据脚本 — 初始化演示/管理数据
包含真实工业场景的种子数据（除尘器、生产过程、能耗、碳排放、光伏）
"""
import sys, os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.config import SessionLocal, engine, Base
from models.core import (
    User, UserRole, Project, ProjectStatus, Dataset,
    Model as AIModel, ModelStatus, ModelVersion,
    EmissionFactor, CarbonEmission, EmissionScope,
    PhotovoltaicSystem, PhotovoltaicData,
    EnergyConsumption,
    DustCollectorData, SinteringData,
)

import hashlib


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def seed_demo_data():
    db = SessionLocal()
    try:
        print("Creating tables...")
        Base.metadata.create_all(bind=engine)
        print("Tables created/verified.")

        # 1. 管理员用户
        admin = db.query(User).filter_by(username="admin").first()
        if not admin:
            admin = User(username="admin", email="admin@foundation.com",
                         hashed_password=hash_password("CHANGE_ME_ADMIN"),
                         full_name="系统管理员", role=UserRole.ADMIN, is_active=True)
            db.add(admin); db.flush()
            print(f"  Created admin user (id={admin.id})")
        else:
            print(f"  Admin user exists (id={admin.id})")

        # 2. 演示用户
        demo = db.query(User).filter_by(username="demo").first()
        if not demo:
            demo = User(username="demo", email="demo@foundation.com",
                        hashed_password=hash_password("demo123"),
                        full_name="演示工程师", role=UserRole.ENGINEER, is_active=True)
            db.add(demo); db.flush()
            print(f"  Created demo user (id={demo.id})")
        else:
            print(f"  Demo user exists (id={demo.id})")

        # 3. 排放因子
        factors = {
            "coal": (2.64, "kgCO2/kg", "IPCC"),
            "natural_gas": (2.16, "kgCO2/m3", "IPCC"),
            "electricity_grid": (0.581, "kgCO2/kWh", "中国区域电网基准线"),
            "diesel": (3.16, "kgCO2/kg", "IPCC"),
        }
        for name, (val, unit, src) in factors.items():
            if not db.query(EmissionFactor).filter_by(name=name).first():
                db.add(EmissionFactor(name=name, factor_value=val, unit=unit, source=src))
                print(f"  Created emission factor '{name}' ({val} {unit})")
        db.flush()

        # ── 4. 工业场景种子数据 ──
        now = datetime.utcnow()

        # 4a. 除尘器数据 (50 条, entity=duster-01)
        if db.query(DustCollectorData).count() == 0:
            dust_samples = []
            for i in range(50):
                t = now - timedelta(hours=i)
                dust_samples.append(DustCollectorData(
                    entity_id="duster-01",
                    production_load=80.0 + (i % 5) * 5.0,
                    dust_concentration=max(1, 12.5 - i * 0.1),
                    filter_pressure_diff=1200.0 + (i % 10) * 50.0,
                    temperature=85.0 + (i % 3) * 2.0,
                    fan_speed=1450.0 + (i % 4) * 30.0,
                    energy_consumption_kwh=45.0 + (i % 5) * 2.5,
                    recorded_at=t,
                ))
            db.add_all(dust_samples)
            print(f"  Created {len(dust_samples)} dust collector data samples")

        # 4b. 生产过程数据 (50 条, entity=sinter-01)
        if db.query(SinteringData).count() == 0:
            sinter_samples = []
            for i in range(50):
                t = now - timedelta(hours=i)
                sinter_samples.append(SinteringData(
                    entity_id="sinter-01",
                    machine_speed=2.5 + (i % 10) * 0.1,
                    bed_thickness=550.0 + (i % 5) * 5.0,
                    permeability=15.0 - (i % 8) * 0.5,
                    pressure1=-12000.0 + (i % 3) * 100,
                    pressure2=-11000.0 + (i % 3) * 100,
                    temp15=350.0 + (i % 20) * 5.0,
                    temp16=380.0 + (i % 20) * 5.0,
                    temp19=420.0 + (i % 20) * 5.0,
                    end_temp=280.0 + (i % 10) * 3.0,
                    carbon_ratio=4.5 + (i % 4) * 0.1,
                    cooling_slope=0.85 + (i % 5) * 0.02,
                    feo_content=8.5 + (i % 4) * 0.3,
                    recorded_at=t,
                ))
            db.add_all(sinter_samples)
            print(f"  Created {len(sinter_samples)} sintering data samples")

        # 4c. 生产设备生产过程数据 (50 条, entity=blast-01)
        if db.query(SinteringData).filter(SinteringData.entity_id == "blast-01").count() == 0:
            blast_samples = []
            for i in range(50):
                t = now - timedelta(hours=i * 2)
                blast_samples.append(SinteringData(
                    entity_id="blast-01",
                    machine_speed=2.2 + (i % 8) * 0.08,
                    bed_thickness=580.0 + (i % 4) * 4.0,
                    permeability=14.0 - (i % 6) * 0.4,
                    pressure1=-11800.0,
                    pressure2=-10800.0,
                    temp15=340.0 + (i % 15) * 4.0,
                    temp16=370.0 + (i % 15) * 4.0,
                    temp19=410.0 + (i % 15) * 4.0,
                    end_temp=275.0 + (i % 8) * 2.5,
                    carbon_ratio=4.3 + (i % 3) * 0.12,
                    cooling_slope=0.88,
                    feo_content=8.2 + (i % 3) * 0.25,
                    recorded_at=t,
                ))
            db.add_all(blast_samples)
            print(f"  Created {len(blast_samples)} blast furnace sintering data samples")

        # 4d. 能耗数据 (100 条，4 种工序 x 3 种能源)
        if db.query(EnergyConsumption).count() == 0:
            energy_samples = []
            processes = ["生产过程", "炼铁", "炼钢", "轧钢"]
            energy_types = [
                ("electricity", "kWh", 0.65),
                ("gas", "m3", 0.08),
                ("coal", "kg", 1.2),
            ]
            for i, proc in enumerate(processes):
                base = [50000, 200000, 80000, 60000][i]
                for j, (etype, unit, price) in enumerate(energy_types):
                    for k in range(8):
                        t = now - timedelta(hours=k * 3 + i * 24)
                        energy_samples.append(EnergyConsumption(
                            entity_id=f"process-{proc}",
                            entity_type=proc,
                            energy_type=etype,
                            consumption=base * (0.85 + 0.15 * (k % 3)),
                            unit=unit,
                            cost_yuan=base * price * (0.85 + 0.15 * (k % 3)),
                            recorded_at=t,
                        ))
            # 追加 4 条额外记录
            for i in range(4):
                t = now - timedelta(hours=i)
                energy_samples.append(EnergyConsumption(
                    entity_id="duster-01",
                    entity_type="除尘",
                    energy_type="electricity",
                    consumption=45.0 + i * 2.5,
                    unit="kWh",
                    cost_yuan=(45.0 + i * 2.5) * 0.65,
                    recorded_at=t,
                ))
            db.add_all(energy_samples)
            print(f"  Created {len(energy_samples)} energy consumption records")

        # 4e. 碳排放数据 (40 条)
        if db.query(CarbonEmission).count() == 0:
            carbon_samples = []
            sources = [("生产过程", 120), ("焦化", 80), ("炼铁", 450), ("炼钢", 50), ("轧钢", 100)]
            for src, base in sources:
                for i in range(8):
                    t = now - timedelta(days=i * 3)
                    carbon_samples.append(CarbonEmission(
                        entity_id=f"process-{src}",
                        entity_type=src,
                        scope=EmissionScope.SCOPE1 if src != "轧钢" else EmissionScope.SCOPE2,
                        source=src,
                        emission_amount=base * (0.9 + 0.1 * (i % 3)),
                        unit="tCO2e",
                        recorded_at=t,
                    ))
            db.add_all(carbon_samples)
            print(f"  Created {len(carbon_samples)} carbon emission records")

        # 4f. 光伏系统 + 数据
        if db.query(PhotovoltaicSystem).count() == 0:
            pv = PhotovoltaicSystem(
                name="厂房屋顶光伏1号", capacity_kwp=500.0,
                location="轧钢厂屋顶", status="running",
            )
            db.add(pv); db.flush()
            print(f"  Created photovoltaic system (id={pv.id})")
            pv_samples = []
            for i in range(48):
                t = now - timedelta(hours=i)
                hour = t.hour
                irrad = max(0, 800 * (0.5 + 0.5 * (6 <= hour <= 18)))
                power = round(irrad * 0.18 * pv.capacity_kwp / 1000, 1)
                pv_samples.append(PhotovoltaicData(
                    system_id=pv.id,
                    power_kw=power,
                    energy_kwh=round(power * 0.25, 2),
                    irradiance_wm2=round(irrad, 1),
                    temperature_c=round(25 + (hour - 12) * 1.5, 1),
                    recorded_at=t,
                ))
            db.add_all(pv_samples)
            print(f"  Created {len(pv_samples)} photovoltaic data records")

        db.commit()
        print("\n✓ Seed completed successfully!")

    except Exception as e:
        db.rollback()
        print(f"✗ Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo_data()
