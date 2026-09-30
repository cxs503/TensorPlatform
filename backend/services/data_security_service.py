"""
数据安全服务
提供访问策略管理、脱敏规则管理和审计日志功能
"""
import hashlib
import random
import re
import string
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from models.data_security import (
    AccessAudit,
    AccessPolicy,
    DataSecurityPolicy,
    MaskingRule,
    PolicyRule,
)
from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session


class MaskingType(str, Enum):
    """脱敏类型"""

    PHONE = "phone"
    ID_CARD = "id_card"
    BANK_CARD = "bank_card"
    EMAIL = "email"
    NAME = "name"
    ADDRESS = "address"
    CUSTOM = "custom"


class MaskingStrategy(str, Enum):
    """脱敏策略"""

    MASK = "mask"  # 掩码
    HASH = "hash"  # 哈希
    ENCRYPT = "encrypt"  # 加密
    REPLACE = "replace"  # 替换
    SHUFFLE = "shuffle"  # 打乱
    TRUNCATE = "truncate"  # 截断
    FPE = "fpe"  # 格式保留加密


class DataSecurityService:
    """数据安全服务"""

    def __init__(self, db: Session):
        self.db = db
        self._init_default_masking_rules()

    def _init_default_masking_rules(self):
        """初始化默认脱敏规则"""
        defaults = [
            {
                "name": "手机号脱敏",
                "field_pattern": ".*phone.*|.*mobile.*|.*手机.*",
                "masking_type": "phone",
                "masking_strategy": "mask",
                "parameters": {"show_first": 3, "show_last": 4},
            },
            {
                "name": "身份证脱敏",
                "field_pattern": ".*idcard.*|.*id_card.*|.*sfz.*|.*身份证.*",
                "masking_type": "id_card",
                "masking_strategy": "mask",
                "parameters": {"show_first": 3, "show_last": 4},
            },
            {
                "name": "银行卡脱敏",
                "field_pattern": ".*bankcard.*|.*bank_card.*|.*card.*|.*银行卡.*",
                "masking_type": "bank_card",
                "masking_strategy": "mask",
                "parameters": {"show_first": 4, "show_last": 4},
            },
            {
                "name": "邮箱脱敏",
                "field_pattern": ".*email.*|.*邮箱.*",
                "masking_type": "email",
                "masking_strategy": "mask",
                "parameters": {"show_first": 1, "show_last_domain": True},
            },
            {
                "name": "姓名脱敏",
                "field_pattern": ".*name.*|.*姓名.*|.*真实姓名.*",
                "masking_type": "name",
                "masking_strategy": "mask",
                "parameters": {"show_first": 1},
            },
            {
                "name": "地址脱敏",
                "field_pattern": ".*address.*|.*地址.*",
                "masking_type": "address",
                "masking_strategy": "truncate",
                "parameters": {"max_length": 10},
            },
        ]

        for default in defaults:
            existing = (
                self.db.query(MaskingRule)
                .filter(MaskingRule.name == default["name"])
                .first()
            )
            if not existing:
                rule = MaskingRule(**default, is_active=1, priority=100)
                self.db.add(rule)

        self.db.commit()

    # ========== 脱敏规则管理 ==========

    def list_masking_rules(self, is_active: int = None) -> List[Dict]:
        """列出脱敏规则"""
        query = self.db.query(MaskingRule)
        if is_active is not None:
            query = query.filter(MaskingRule.is_active == is_active)
        rules = query.order_by(MaskingRule.priority, MaskingRule.id).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "field_pattern": r.field_pattern,
                "masking_type": r.masking_type,
                "masking_strategy": r.masking_strategy,
                "parameters": r.parameters,
                "is_active": r.is_active,
                "priority": r.priority,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rules
        ]

    def create_masking_rule(self, data: Dict) -> Dict:
        """创建脱敏规则"""
        # 检查名称是否已存在
        existing = (
            self.db.query(MaskingRule)
            .filter(MaskingRule.name == data.get("name"))
            .first()
        )
        if existing:
            raise ValueError(f"规则名称 '{data.get('name')}' 已存在")

        rule = MaskingRule(
            name=data.get("name"),
            description=data.get("description"),
            field_pattern=data.get("field_pattern"),
            masking_type=data.get("masking_type", "custom"),
            masking_strategy=data.get("masking_strategy", "mask"),
            parameters=data.get("parameters", {}),
            is_active=data.get("is_active", 1),
            priority=data.get("priority", 100),
        )
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)

        return {"id": rule.id, "name": rule.name, "message": "脱敏规则创建成功"}

    def update_masking_rule(self, rule_id: int, data: Dict) -> Dict:
        """更新脱敏规则"""
        rule = self.db.query(MaskingRule).filter(MaskingRule.id == rule_id).first()
        if not rule:
            raise ValueError("规则不存在")

        for key, value in data.items():
            if hasattr(rule, key) and key != "id":
                setattr(rule, key, value)

        self.db.commit()
        return {"id": rule.id, "name": rule.name, "message": "脱敏规则更新成功"}

    def delete_masking_rule(self, rule_id: int) -> bool:
        """删除脱敏规则"""
        rule = self.db.query(MaskingRule).filter(MaskingRule.id == rule_id).first()
        if not rule:
            return False
        self.db.delete(rule)
        self.db.commit()
        return True

    def get_masking_rule(self, rule_id: int) -> Optional[Dict]:
        """获取脱敏规则"""
        rule = self.db.query(MaskingRule).filter(MaskingRule.id == rule_id).first()
        if not rule:
            return None
        return {
            "id": rule.id,
            "name": rule.name,
            "description": rule.description,
            "field_pattern": rule.field_pattern,
            "masking_type": rule.masking_type,
            "masking_strategy": rule.masking_strategy,
            "parameters": rule.parameters,
            "is_active": rule.is_active,
            "priority": rule.priority,
        }

    # ========== 数据脱敏 ==========

    def apply_masking(
        self, data: List[Dict], field_mapping: Dict[str, str] = None,
        rules: Optional[List[MaskingRule]] = None,
    ) -> List[Dict]:
        """应用脱敏规则到数据；可显式传入单条/多条已选规则。"""
        # 未指定时沿用“全部启用规则”的兼容行为；选择具体规则时由
        # 调用方传入，避免预览和全量生成的结果不一致。
        if rules is None:
            rules = (
                self.db.query(MaskingRule)
                .filter(MaskingRule.is_active == 1)
                .order_by(MaskingRule.priority)
                .all()
            )

        result = []
        for record in data:
            masked_record = {}
            for field_name, field_value in record.items():
                # 确定实际字段名
                actual_field = (
                    field_mapping.get(field_name, field_name)
                    if field_mapping
                    else field_name
                )

                # 查找匹配的规则
                matched_rule = None
                for rule in rules:
                    if re.match(rule.field_pattern, actual_field, re.IGNORECASE):
                        matched_rule = rule
                        break

                if matched_rule and field_value is not None:
                    masked_record[field_name] = self._mask_value(
                        str(field_value),
                        matched_rule.masking_type,
                        matched_rule.masking_strategy,
                        matched_rule.parameters,
                    )
                else:
                    masked_record[field_name] = field_value

            result.append(masked_record)

        return result

    def _mask_value(
        self, value: str, masking_type: str, strategy: str, params: Dict
    ) -> str:
        """执行脱敏"""
        if not value:
            return value

        params = params or {}

        # 根据类型选择特定的脱敏方法
        if masking_type == MaskingType.PHONE.value:
            return self._mask_phone(value, params)
        elif masking_type == MaskingType.ID_CARD.value:
            return self._mask_id_card(value, params)
        elif masking_type == MaskingType.BANK_CARD.value:
            return self._mask_bank_card(value, params)
        elif masking_type == MaskingType.EMAIL.value:
            return self._mask_email(value, params)
        elif masking_type == MaskingType.NAME.value:
            return self._mask_name(value, params)
        elif masking_type == MaskingType.ADDRESS.value:
            return self._mask_address(value, params)

        # 通用策略
        if strategy == MaskingStrategy.MASK.value:
            return self._mask_generic(value, params)
        elif strategy == MaskingStrategy.HASH.value:
            return self._hash_value(value, params)
        elif strategy == MaskingStrategy.ENCRYPT.value:
            return self._encrypt_value(value, params)
        elif strategy == MaskingStrategy.REPLACE.value:
            return params.get("replace_with", "***")
        elif strategy == MaskingStrategy.SHUFFLE.value:
            return self._shuffle_value(value)
        elif strategy == MaskingStrategy.TRUNCATE.value:
            return self._truncate_value(value, params)

        return value

    def _mask_phone(self, value: str, params: Dict) -> str:
        """手机号脱敏: 138****5678"""
        # 清理非数字字符
        digits = re.sub(r"\D", "", value)
        if len(digits) < 7:
            return "***" + digits[-4:] if len(digits) >= 4 else "****"

        show_first = params.get("show_first", 3)
        show_last = params.get("show_last", 4)

        first = digits[:show_first] if show_first > 0 else ""
        last = digits[-show_last:] if show_last > 0 else ""
        middle = "*" * (len(digits) - show_first - show_last)

        return first + middle + last

    def _mask_id_card(self, value: str, params: Dict) -> str:
        """身份证脱敏: 110***********1234"""
        # 清理非数字字符（身份证最后一位可能是X）
        clean = re.sub(r"[^\dXx]", "", value)
        if len(clean) < 8:
            return "***" + clean[-4:] if len(clean) >= 4 else "************"

        show_first = params.get("show_first", 3)
        show_last = params.get("show_last", 4)

        first = clean[:show_first] if show_first > 0 else ""
        last = clean[-show_last:] if show_last > 0 else ""
        middle = "*" * (len(clean) - show_first - show_last)

        return first + middle + last

    def _mask_bank_card(self, value: str, params: Dict) -> str:
        """银行卡脱敏: 6222****1234"""
        digits = re.sub(r"\D", "", value)
        if len(digits) < 8:
            return "****" + digits[-4:] if len(digits) >= 4 else "********"

        show_first = params.get("show_first", 4)
        show_last = params.get("show_last", 4)

        first = digits[:show_first] if show_first > 0 else ""
        last = digits[-show_last:] if show_last > 0 else ""
        middle = "*" * (len(digits) - show_first - show_last)

        return first + middle + last

    def _mask_email(self, value: str, params: Dict) -> str:
        """邮箱脱敏: a***@example.com"""
        if "@" not in value:
            return self._mask_generic(value, params)

        parts = value.split("@")
        username = parts[0]
        domain = parts[1]

        show_first = params.get("show_first", 1)

        if len(username) <= show_first:
            masked_username = username[0] + "***"
        else:
            masked_username = username[:show_first] + "***"

        return masked_username + "@" + domain

    def _mask_name(self, value: str, params: Dict) -> str:
        """姓名脱敏: 张**"""
        if not value:
            return value

        show_first = params.get("show_first", 1)

        if len(value) <= show_first:
            return value[0] + "*"

        return value[:show_first] + "*" * (len(value) - show_first)

    def _mask_address(self, value: str, params: Dict) -> str:
        """地址脱敏"""
        max_length = params.get("max_length", 10)
        if len(value) <= max_length:
            return value
        return value[:max_length] + "***"

    def _mask_generic(self, value: str, params: Dict) -> str:
        """通用掩码"""
        show_first = params.get("show_first", 0)
        show_last = params.get("show_last", 0)
        mask_char = params.get("mask_char", "*")

        if len(value) <= show_first + show_last:
            return mask_char * len(value)

        first = value[:show_first] if show_first > 0 else ""
        last = value[-show_last:] if show_last > 0 else ""
        middle = mask_char * (len(value) - show_first - show_last)

        return first + middle + last

    def _hash_value(self, value: str, params: Dict) -> str:
        """哈希处理"""
        salt = params.get("salt", "")
        algorithm = params.get("algorithm", "sha256")

        data = value + salt
        if algorithm == "md5":
            return hashlib.md5(data.encode()).hexdigest()
        elif algorithm == "sha1":
            return hashlib.sha1(data.encode()).hexdigest()
        else:
            return hashlib.sha256(data.encode()).hexdigest()

    def _encrypt_value(self, value: str, params: Dict) -> str:
        """简单加密（实际应使用加密库）"""
        key = params.get("key", "default_key")
        encoded = value.encode()
        key_bytes = (key * (len(encoded) // len(key) + 1))[: len(encoded)].encode()
        encrypted = bytes(a ^ b for a, b in zip(encoded, key_bytes))
        return encrypted.hex()

    def _shuffle_value(self, value: str) -> str:
        """打乱处理"""
        chars = list(value)
        random.shuffle(chars)
        return "".join(chars)

    def _truncate_value(self, value: str, params: Dict) -> str:
        """截断处理"""
        max_length = params.get("max_length", 5)
        suffix = params.get("suffix", "...")

        if len(value) <= max_length:
            return value
        return value[:max_length] + suffix

    # ========== 访问策略管理 ==========

    def list_access_policies(self, is_active: int = None) -> List[Dict]:
        """列出访问策略"""
        query = self.db.query(AccessPolicy)
        if is_active is not None:
            query = query.filter(AccessPolicy.is_active == is_active)
        policies = query.order_by(AccessPolicy.priority).all()

        return [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "resource_type": p.resource_type,
                "resource_pattern": p.resource_pattern,
                "action": p.action,
                "effect": p.effect,
                "conditions": p.conditions,
                "priority": p.priority,
                "is_active": p.is_active,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in policies
        ]

    def create_access_policy(self, data: Dict) -> Dict:
        """创建访问策略"""
        existing = (
            self.db.query(AccessPolicy)
            .filter(AccessPolicy.name == data.get("name"))
            .first()
        )
        if existing:
            raise ValueError(f"策略名称 '{data.get('name')}' 已存在")

        policy = AccessPolicy(
            name=data.get("name"),
            description=data.get("description"),
            resource_type=data.get("resource_type"),
            resource_pattern=data.get("resource_pattern"),
            action=data.get("action"),
            effect=data.get("effect", "deny"),
            conditions=data.get("conditions", {}),
            priority=data.get("priority", 100),
            is_active=data.get("is_active", 1),
        )
        self.db.add(policy)
        self.db.commit()
        self.db.refresh(policy)

        return {"id": policy.id, "name": policy.name, "message": "访问策略创建成功"}

    def update_access_policy(self, policy_id: int, data: Dict) -> Dict:
        """更新访问策略"""
        policy = (
            self.db.query(AccessPolicy).filter(AccessPolicy.id == policy_id).first()
        )
        if not policy:
            raise ValueError("策略不存在")

        for key, value in data.items():
            if hasattr(policy, key) and key != "id":
                setattr(policy, key, value)

        self.db.commit()
        return {"id": policy.id, "name": policy.name, "message": "访问策略更新成功"}

    def delete_access_policy(self, policy_id: int) -> bool:
        """删除访问策略"""
        policy = (
            self.db.query(AccessPolicy).filter(AccessPolicy.id == policy_id).first()
        )
        if not policy:
            return False
        self.db.delete(policy)
        self.db.commit()
        return True

    def check_access(
        self, user_id: int, resource_type: str, resource: str, action: str
    ) -> Dict:
        """检查访问权限"""
        policies = (
            self.db.query(AccessPolicy)
            .filter(
                AccessPolicy.is_active == 1,
                AccessPolicy.resource_type == resource_type,
                or_(AccessPolicy.action == action, AccessPolicy.action == "*"),
            )
            .order_by(AccessPolicy.priority)
            .all()
        )

        matched_policy = None
        for policy in policies:
            if re.match(policy.resource_pattern, resource, re.IGNORECASE):
                matched_policy = policy
                break

        result = {
            "allowed": False,
            "policy_matched": None,
            "reason": "No matching policy found",
        }

        if matched_policy:
            result["policy_matched"] = matched_policy.name
            result["allowed"] = matched_policy.effect == "allow"
            result[
                "reason"
            ] = f"Matched policy: {matched_policy.name} ({matched_policy.effect})"

        return result

    # ========== 审计日志 ==========

    def log_access(self, data: Dict) -> Dict:
        """记录访问日志"""
        audit = AccessAudit(
            user_id=data.get("user_id"),
            username=data.get("username"),
            resource_type=data.get("resource_type"),
            resource=data.get("resource"),
            action=data.get("action"),
            result=data.get("result", "success"),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
            request_method=data.get("request_method"),
            request_path=data.get("request_path"),
            request_params=data.get("request_params"),
            response_code=data.get("response_code"),
            duration_ms=data.get("duration_ms"),
            policy_matched=data.get("policy_matched"),
            masking_applied=data.get("masking_applied", 0),
            extra_info=data.get("extra_info"),
        )
        self.db.add(audit)
        self.db.commit()
        self.db.refresh(audit)

        return {"id": audit.id, "message": "审计日志记录成功"}

    def list_audit_logs(
        self,
        user_id: int = None,
        resource_type: str = None,
        result: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Dict:
        """查询审计日志"""
        query = self.db.query(AccessAudit)

        if user_id:
            query = query.filter(AccessAudit.user_id == user_id)
        if resource_type:
            query = query.filter(AccessAudit.resource_type == resource_type)
        if result:
            query = query.filter(AccessAudit.result == result)
        if start_time:
            query = query.filter(AccessAudit.timestamp >= start_time)
        if end_time:
            query = query.filter(AccessAudit.timestamp <= end_time)

        total = query.count()
        logs = (
            query.order_by(desc(AccessAudit.timestamp)).offset(skip).limit(limit).all()
        )

        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "items": [
                {
                    "id": log.id,
                    "user_id": log.user_id,
                    "username": log.username,
                    "resource_type": log.resource_type,
                    "resource": log.resource,
                    "action": log.action,
                    "result": log.result,
                    "ip_address": log.ip_address,
                    "request_path": log.request_path,
                    "response_code": log.response_code,
                    "duration_ms": log.duration_ms,
                    "policy_matched": log.policy_matched,
                    "masking_applied": log.masking_applied,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                }
                for log in logs
            ],
        }

    def get_audit_statistics(
        self, start_time: datetime = None, end_time: datetime = None
    ) -> Dict:
        """获取审计统计"""
        query = self.db.query(AccessAudit)

        if start_time:
            query = query.filter(AccessAudit.timestamp >= start_time)
        if end_time:
            query = query.filter(AccessAudit.timestamp <= end_time)

        logs = query.all()

        total = len(logs)
        success_count = sum(1 for l in logs if l.result == "success")
        denied_count = sum(1 for l in logs if l.result == "denied")
        failed_count = sum(1 for l in logs if l.result == "failed")

        # 按资源类型统计
        resource_stats = {}
        for log in logs:
            rt = log.resource_type or "unknown"
            resource_stats[rt] = resource_stats.get(rt, 0) + 1

        # 按用户统计
        user_stats = {}
        for log in logs:
            uid = log.user_id or 0
            user_stats[uid] = user_stats.get(uid, 0) + 1

        return {
            "total": total,
            "success": success_count,
            "denied": denied_count,
            "failed": failed_count,
            "success_rate": round(success_count / total * 100, 2) if total > 0 else 0,
            "by_resource_type": resource_stats,
            "top_users": sorted(user_stats.items(), key=lambda x: x[1], reverse=True)[
                :10
            ],
        }
