"""
数据库种子数据脚本 — 初始化管理账号

2026-10-16 架构收敛：移除除尘/生产过程/能耗/碳排放/光伏等行业演示数据，
平台回归 domain-neutral 共性底座；行业数据由使用方按需自行准备。
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.config import SessionLocal, engine, Base
from models.core import User, UserRole

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
