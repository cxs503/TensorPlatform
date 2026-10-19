# -*- coding: utf-8 -*-
"""
认证和授权测试
"""
import pytest
from sqlalchemy import text


class TestUserModel:
    """用户模型测试"""

    def test_user_model(self, db_session):
        """测试用户模型"""
        # 插入测试数据
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('testuser', 'test@example.com', 'hashed_password', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        # 查询验证
        result = db_session.execute(
            text(
                "SELECT username, email, is_active, role FROM users WHERE username=:username"
            ),
            {"username": "testuser"},
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "testuser"
        assert row[1] == "test@example.com"
        assert row[2] == 1
        assert row[3] == "user"

    def test_user_password_hash(self, db_session):
        """测试密码哈希"""
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('password_user', 'pass@example.com', 'hashed_password_123', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT hashed_password FROM users WHERE username=:username"),
            {"username": "password_user"},
        )
        row = result.fetchone()
        assert row[0] == "hashed_password_123"

    def test_user_is_active_flag(self, db_session):
        """测试用户激活状态"""
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('active_user', 'active@example.com', 'hashed', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT is_active FROM users WHERE username=:username"),
            {"username": "active_user"},
        )
        assert result.fetchone()[0] == 1

    def test_user_is_admin_flag(self, db_session):
        """测试管理员标志"""
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('admin_user', 'admin@example.com', 'hashed', 1, 1, 'admin')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT is_admin, role FROM users WHERE username=:username"),
            {"username": "admin_user"},
        )
        row = result.fetchone()
        assert row[0] == 1
        assert row[1] == "admin"


class TestProjectModel:
    """项目模型测试"""

    def test_project_model(self, db_session):
        """测试项目模型"""
        # 插入用户
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('project_creator', 'creator@example.com', 'hashed', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT id FROM users WHERE username=:username"),
            {"username": "project_creator"},
        )
        user_id = result.fetchone()[0]

        # 插入项目
        db_session.execute(
            text(
                """
            INSERT INTO projects (name, description, project_type, tags, status, user_id)
            VALUES (:name, :description, :project_type, :tags, :status, :user_id)
        """
            ),
            {
                "name": "Test Project",
                "description": "Test Description",
                "project_type": "energy",
                "tags": '{"tag1": "value1"}',
                "status": "draft",
                "user_id": user_id,
            },
        )
        db_session.commit()

        result = db_session.execute(
            text(
                "SELECT name, description, project_type, status FROM projects WHERE name=:name"
            ),
            {"name": "Test Project"},
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "Test Project"
        assert row[1] == "Test Description"

    def test_project_with_tags(self, db_session):
        """测试带标签的项目"""
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('tag_user', 'tag@example.com', 'hashed', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT id FROM users WHERE username=:username"),
            {"username": "tag_user"},
        )
        user_id = result.fetchone()[0]

        db_session.execute(
            text(
                """
            INSERT INTO projects (name, description, project_type, tags, status, user_id)
            VALUES (:name, :description, :project_type, :tags, :status, :user_id)
        """
            ),
            {
                "name": "Tagged Project",
                "description": "With tags",
                "project_type": "energy",
                "tags": '{"city": "Beijing", "industry": "manufacturing"}',
                "status": "active",
                "user_id": user_id,
            },
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT tags FROM projects WHERE name=:name"),
            {"name": "Tagged Project"},
        )
        row = result.fetchone()
        assert row[0] is not None
        assert "Beijing" in row[0]


class TestDatasetModel:
    """数据集模型测试"""

    def test_dataset_model(self, db_session):
        """测试数据集模型"""
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('dataset_user', 'ds@example.com', 'hashed', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT id FROM users WHERE username=:username"),
            {"username": "dataset_user"},
        )
        user_id = result.fetchone()[0]

        db_session.execute(
            text(
                """
            INSERT INTO datasets (name, data_type, status, user_id)
            VALUES (:name, :data_type, :status, :user_id)
        """
            ),
            {
                "name": "Training Dataset",
                "data_type": "training",
                "status": "active",
                "user_id": user_id,
            },
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT name, data_type, status FROM datasets WHERE name=:name"),
            {"name": "Training Dataset"},
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "Training Dataset"


class TestModelModel:
    """模型模型测试"""

    def test_model_model(self, db_session):
        """测试模型模型"""
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('model_owner', 'owner@example.com', 'hashed', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT id FROM users WHERE username=:username"),
            {"username": "model_owner"},
        )
        user_id = result.fetchone()[0]

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
                "name": "Energy Model",
                "description": "Energy prediction model",
                "model_type": "regression",
                "algorithm_type": "xgboost",
                "project_id": 1,
                "dataset_id": 1,
                "hyperparameters": '{"n_estimators": 100}',
                "status": "deployed",
                "user_id": user_id,
                "owner_id": user_id,
                "version": "2.0.0",
                "metrics": '{"rmse": 0.15}',
            },
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT name, version, status FROM models WHERE name=:name"),
            {"name": "Energy Model"},
        )
        row = result.fetchone()
        assert row is not None
        assert row[1] == "2.0.0"


class TestRoleBasedAccess:
    """基于角色的访问控制测试"""

    def test_user_role(self, db_session):
        """测试用户角色"""
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('role_user', 'role@example.com', 'hashed', 1, 0, 'user')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT role FROM users WHERE username=:username"),
            {"username": "role_user"},
        )
        assert result.fetchone()[0] == "user"

    def test_admin_role(self, db_session):
        """测试管理员角色"""
        db_session.execute(
            text(
                """
            INSERT INTO users (username, email, hashed_password, is_active, is_admin, role)
            VALUES ('role_admin', 'admin@example.com', 'hashed', 1, 1, 'admin')
        """
            )
        )
        db_session.commit()

        result = db_session.execute(
            text("SELECT role, is_admin FROM users WHERE username=:username"),
            {"username": "role_admin"},
        )
        row = result.fetchone()
        assert row[0] == "admin"
        assert row[1] == 1
