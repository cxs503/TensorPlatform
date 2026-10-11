# -*- coding: utf-8 -*-
"""
数据库测试
"""
import pytest
from sqlalchemy import text


class TestDatabaseConnection:
    """数据库连接测试"""

    def test_database_connection(self, db_session):
        """测试数据库连接"""
        result = db_session.execute(text("SELECT 1"))
        assert result.scalar() == 1

    def test_database_version(self, db_session):
        """测试数据库版本"""
        result = db_session.execute(text("SELECT sqlite_version()"))
        version = result.scalar()
        assert version is not None
        assert len(version) > 0


class TestDatabaseTables:
    """数据库表测试"""

    def test_users_table_exists(self, db_session):
        """测试 users 表是否存在"""
        result = db_session.execute(
            text(
                """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='users'
        """
            )
        )
        assert result.fetchone() is not None

    def test_projects_table_exists(self, db_session):
        """测试 projects 表是否存在"""
        result = db_session.execute(
            text(
                """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='projects'
        """
            )
        )
        assert result.fetchone() is not None

    def test_datasets_table_exists(self, db_session):
        """测试 datasets 表是否存在"""
        result = db_session.execute(
            text(
                """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='datasets'
        """
            )
        )
        assert result.fetchone() is not None

    def test_models_table_exists(self, db_session):
        """测试 models 表是否存在"""
        result = db_session.execute(
            text(
                """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='models'
        """
            )
        )
        assert result.fetchone() is not None


class TestDataOperations:
    """数据操作测试"""

    def test_insert_and_select_user(self, db_session):
        """测试用户插入和查询"""
        # 使用数据库 fixture 的 transaction 便于清理
        # 插入测试数据 (使用当前时间戳确保唯一性)
        import time

        from sqlalchemy import text

        timestamp = int(time.time())
        db_session.execute(
            text(
                f"""
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('testuser_{timestamp}', 'test@example.com', 'hashed_password', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        # 查询验证
        result = db_session.execute(
            text("SELECT id, username, email FROM users WHERE username=:username"),
            {"username": f"testuser_{timestamp}"},
        )
        row = result.fetchone()
        assert row is not None
        assert row[1] == f"testuser_{timestamp}"
        assert row[2] == "test@example.com"

    def test_insert_and_select_project(self, db_session):
        """测试项目插入和查询"""
        # 先插入用户
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('project_user', 'project@example.com', 'hashed_password', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        # 获取用户 ID
        result = db_session.execute(
            text("SELECT id FROM users WHERE username=:username"),
            {"username": "project_user"},
        )
        user_id = result.fetchone()[0]

        # 插入项目
        db_session.execute(
            text(
                """
            INSERT INTO projects (name, description, project_type, status, user_id)
            VALUES (:name, :description, :project_type, :status, :user_id)
        """
            ),
            {
                "name": "Test Project",
                "description": "Test Description",
                "project_type": "energy",
                "status": "draft",
                "user_id": user_id,
            },
        )
        db_session.commit()

        # 查询验证
        result = db_session.execute(
            text("SELECT name, project_type FROM projects WHERE name=:name"),
            {"name": "Test Project"},
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "Test Project"
        assert row[1] == "energy"


class TestCRUDOperations:
    """CRUD 操作测试"""

    def test_update_user(self, db_session):
        """测试更新用户"""
        # 插入测试用户
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('update_user', 'old@example.com', 'hashed_password', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        # 更新邮箱
        db_session.execute(
            text(
                """
            UPDATE users SET email=:new_email WHERE username=:username
        """
            ),
            {"new_email": "new@example.com", "username": "update_user"},
        )
        db_session.commit()

        # 验证更新
        result = db_session.execute(
            text("SELECT email FROM users WHERE username=:username"),
            {"username": "update_user"},
        )
        row = result.fetchone()
        assert row[0] == "new@example.com"

    def test_delete_user(self, db_session):
        """测试删除用户"""
        # 插入测试用户
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('delete_user', 'delete@example.com', 'hashed_password', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        # 删除用户
        db_session.execute(
            text(
                """
            DELETE FROM users WHERE username=:username
        """
            ),
            {"username": "delete_user"},
        )
        db_session.commit()

        # 验证删除
        result = db_session.execute(
            text("SELECT COUNT(*) FROM users WHERE username=:username"),
            {"username": "delete_user"},
        )
        count = result.fetchone()[0]
        assert count == 0


class TestModelOperations:
    """模型相关测试"""

    def test_model_crud(self, db_session):
        """测试模型的 CRUD 操作"""
        # 插入用户和项目
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('model_user', 'model@example.com', 'hashed_password', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT id FROM users WHERE username=:username"),
            {"username": "model_user"},
        )
        user_id = result.fetchone()[0]

        # 插入项目
        db_session.execute(
            text(
                """
            INSERT INTO projects (name, description, project_type, status, user_id)
            VALUES ('Model Project', 'Test', 'energy', 'active', :user_id)
        """
            ),
            {"user_id": user_id},
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT id FROM projects WHERE name=:name"), {"name": "Model Project"}
        )
        project_id = result.fetchone()[0]

        # 插入数据集
        db_session.execute(
            text(
                """
            INSERT INTO datasets (name, data_type, status, user_id)
            VALUES ('Model Dataset', 'training', 'active', :user_id)
        """
            ),
            {"user_id": user_id},
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT id FROM datasets WHERE name=:name"), {"name": "Model Dataset"}
        )
        dataset_id = result.fetchone()[0]

        # 插入模型
        db_session.execute(
            text(
                """
            INSERT INTO models (name, description, model_type, algorithm_type,
                              project_id, dataset_id, hyperparameters, status, user_id, owner_id, version, metrics)
            VALUES (:name, :description, :model_type, :algorithm_type,
                   :project_id, :dataset_id, :hyperparameters, :status, :user_id, :owner_id, :version, :metrics)
        """
            ),
            {
                "name": "Test Model",
                "description": "A test model",
                "model_type": "deep_learning",
                "algorithm_type": "lstm",
                "project_id": project_id,
                "dataset_id": dataset_id,
                "hyperparameters": '{"learning_rate": 0.001}',
                "status": "training",
                "user_id": user_id,
                "owner_id": user_id,
                "version": "1.0.0",
                "metrics": '{"accuracy": 0.95}',
            },
        )
        db_session.commit()

        # 查询验证
        result = db_session.execute(
            text("SELECT name, version, status FROM models WHERE name=:name"),
            {"name": "Test Model"},
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "Test Model"
        assert row[1] == "1.0.0"
