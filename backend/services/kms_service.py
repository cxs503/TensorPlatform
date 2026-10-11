"""
密钥管理服务 (KMS)
提供密钥生命周期管理、密钥轮换、加密解密服务
"""
import hashlib
import hmac
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class KeyStatus(str, Enum):
    """密钥状态"""

    ENABLED = "enabled"
    DISABLED = "disabled"
    PENDING_ROTATION = "pending_rotation"
    EXPIRED = "expired"


class KeyUsage(str, Enum):
    """密钥用途"""

    ENCRYPTION = "encryption"
    SIGNING = "signing"
    HASHING = "hashing"


class KeyAlgorithm(str, Enum):
    """密钥算法"""

    AES_256 = "AES-256-GCM"
    RSA_2048 = "RSA-2048"
    RSA_4096 = "RSA-4096"
    HMAC_SHA256 = "HMAC-SHA256"


class Key:
    """密钥"""

    def __init__(
        self,
        name: str,
        algorithm: str,
        usage: str,
        user_id: str = None,
        rotation_days: int = 90,
    ):
        self.id = f"key_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.algorithm = algorithm
        self.usage = usage
        self.user_id = user_id
        self.rotation_days = rotation_days
        self.status = KeyStatus.ENABLED
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.expires_at = None
        self.last_rotated = None
        self.key_material = self._generate_key_material()

    def _generate_key_material(self) -> str:
        """生成密钥材料"""
        return hashlib.sha256(
            f"{self.id}{datetime.now().isoformat()}".encode()
        ).hexdigest()


class KeyVersion:
    """密钥版本"""

    def __init__(self, key_id: str, version: int, key_material: str):
        self.id = f"keyver_{uuid.uuid4().hex[:12]}"
        self.key_id = key_id
        self.version = version
        self.key_material = key_material
        self.created_at = datetime.now().isoformat()
        self.status = KeyStatus.ENABLED


class EncryptionRecord:
    """加密记录"""

    def __init__(
        self,
        key_id: str,
        version: int,
        algorithm: str,
        encrypted_data: str,
        iv: str = None,
    ):
        self.id = f"enc_{uuid.uuid4().hex[:12]}"
        self.key_id = key_id
        self.version = version
        self.algorithm = algorithm
        self.encrypted_data = encrypted_data
        self.iv = iv
        self.created_at = datetime.now().isoformat()


class KMService:
    """密钥管理服务"""

    def __init__(self):
        self.keys: Dict[str, Key] = {}
        self.key_versions: Dict[str, List[KeyVersion]] = {}
        self.encryption_records: Dict[str, EncryptionRecord] = {}

    # ========== 密钥管理 ==========

    def create_key(
        self,
        name: str,
        algorithm: str,
        usage: str,
        user_id: str = None,
        rotation_days: int = 90,
    ) -> Key:
        """创建密钥"""
        key = Key(name, algorithm, usage, user_id, rotation_days)
        self.keys[key.id] = key
        self.key_versions[key.id] = []

        # 创建初始版本
        self._create_key_version(key)

        return key

    def _create_key_version(self, key: Key):
        """创建密钥版本"""
        version_num = len(self.key_versions[key.id]) + 1
        version = KeyVersion(
            key_id=key.id, version=version_num, key_material=key.key_material
        )
        self.key_versions[key.id].append(version)

    def get_key(self, key_id: str) -> Optional[Key]:
        """获取密钥"""
        return self.keys.get(key_id)

    def list_keys(
        self, user_id: str = None, status: str = None, usage: str = None
    ) -> List[Dict]:
        """列出密钥"""
        keys = self.keys.values()
        if user_id:
            keys = [k for k in keys if k.user_id == user_id]
        if status:
            keys = [k for k in keys if k.status == status]
        if usage:
            keys = [k for k in keys if k.usage == usage]

        return [
            {
                "id": k.id,
                "name": k.name,
                "algorithm": k.algorithm,
                "usage": k.usage,
                "status": k.status,
                "rotation_days": k.rotation_days,
                "version_count": len(self.key_versions.get(k.id, [])),
                "last_rotated": k.last_rotated,
                "created_at": k.created_at,
                "expires_at": k.expires_at,
            }
            for k in keys
        ]

    def update_key(
        self,
        key_id: str,
        name: str = None,
        rotation_days: int = None,
        status: str = None,
    ) -> Optional[Key]:
        """更新密钥"""
        key = self.keys.get(key_id)
        if not key:
            return None

        if name:
            key.name = name
        if rotation_days:
            key.rotation_days = rotation_days
        if status:
            key.status = status

        key.updated_at = datetime.now().isoformat()
        return key

    def delete_key(self, key_id: str) -> bool:
        """删除密钥"""
        if key_id in self.keys:
            del self.keys[key_id]
            if key_id in self.key_versions:
                del self.key_versions[key_id]
            return True
        return False

    # ========== 密钥轮换 ==========

    def rotate_key(self, key_id: str) -> Optional[Key]:
        """轮换密钥"""
        key = self.keys.get(key_id)
        if not key:
            return None

        # 生成新密钥材料
        key.key_material = hashlib.sha256(
            f"{key.id}{datetime.now().isoformat()}".encode()
        ).hexdigest()

        # 创建新版本
        self._create_key_version(key)

        # 更新密钥
        key.last_rotated = datetime.now().isoformat()
        key.updated_at = datetime.now().isoformat()

        # 设置旧版本为pending rotation
        versions = self.key_versions.get(key_id, [])
        for v in versions[:-1]:
            v.status = KeyStatus.PENDING_ROTATION

        return key

    def get_key_versions(self, key_id: str) -> List[Dict]:
        """获取密钥版本"""
        versions = self.key_versions.get(key_id, [])
        return [
            {
                "id": v.id,
                "version": v.version,
                "status": v.status,
                "created_at": v.created_at,
            }
            for v in versions
        ]

    # ========== 加密解密 ==========

    def encrypt(
        self, key_id: str, plaintext: str, associated_data: str = None
    ) -> Optional[Dict]:
        """加密数据"""
        key = self.keys.get(key_id)
        if not key or key.status != KeyStatus.ENABLED:
            return None

        # 获取最新版本
        versions = self.key_versions.get(key_id, [])
        if not versions:
            return None
        latest_version = versions[-1]

        # 简单模拟加密（实际应使用加密库）
        encrypted = self._simulate_encrypt(
            plaintext, latest_version.key_material, key.algorithm
        )

        # 记录加密
        record = EncryptionRecord(
            key_id=key_id,
            version=latest_version.version,
            algorithm=key.algorithm,
            encrypted_data=encrypted,
        )
        self.encryption_records[record.id] = record

        return {
            "ciphertext": encrypted,
            "key_id": key_id,
            "version": latest_version.version,
            "algorithm": key.algorithm,
            "encryption_id": record.id,
        }

    def decrypt(
        self, key_id: str, ciphertext: str, version: int = None
    ) -> Optional[str]:
        """解密数据"""
        key = self.keys.get(key_id)
        if not key or key.status != KeyStatus.ENABLED:
            return None

        # 获取指定版本或最新版本
        versions = self.key_versions.get(key_id, [])
        if not versions:
            return None

        if version:
            target_version = next((v for v in versions if v.version == version), None)
        else:
            target_version = versions[-1]

        if not target_version:
            return None

        # 解密
        plaintext = self._simulate_decrypt(
            ciphertext, target_version.key_material, key.algorithm
        )

        return plaintext

    def _simulate_encrypt(
        self, plaintext: str, key_material: str, algorithm: str
    ) -> str:
        """模拟加密"""
        # 简单XOR加密模拟
        key_bytes = bytes.fromhex(key_material[:32])
        text_bytes = plaintext.encode()
        encrypted = bytes(
            a ^ b
            for a, b in zip(
                text_bytes,
                (key_bytes * (len(text_bytes) // len(key_bytes) + 1))[
                    : len(text_bytes)
                ],
            )
        )
        return encrypted.hex()

    def _simulate_decrypt(
        self, ciphertext: str, key_material: str, algorithm: str
    ) -> str:
        """模拟解密"""
        key_bytes = bytes.fromhex(key_material[:32])
        encrypted_bytes = bytes.fromhex(ciphertext)
        decrypted = bytes(
            a ^ b
            for a, b in zip(
                encrypted_bytes,
                (key_bytes * (len(encrypted_bytes) // len(key_bytes) + 1))[
                    : len(encrypted_bytes)
                ],
            )
        )
        return decrypted.decode()

    # ========== 签名验签 ==========

    def sign(self, key_id: str, data: str) -> Optional[Dict]:
        """签名"""
        key = self.keys.get(key_id)
        if not key or key.status != KeyStatus.ENABLED or key.usage != KeyUsage.SIGNING:
            return None

        versions = self.key_versions.get(key_id, [])
        if not versions:
            return None
        latest_version = versions[-1]

        # 生成签名
        signature = hmac.new(
            latest_version.key_material.encode(), data.encode(), hashlib.sha256
        ).hexdigest()

        return {
            "signature": signature,
            "key_id": key_id,
            "version": latest_version.version,
            "algorithm": key.algorithm,
        }

    def verify(self, key_id: str, data: str, signature: str) -> bool:
        """验签"""
        key = self.keys.get(key_id)
        if not key or key.usage != KeyUsage.SIGNING:
            return False

        versions = self.key_versions.get(key_id, [])
        if not versions:
            return False

        # 尝试所有版本
        for version in versions:
            expected = hmac.new(
                version.key_material.encode(), data.encode(), hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(expected, signature):
                return True

        return False

    # ========== 统计 ==========

    def get_kms_stats(self) -> Dict:
        """获取KMS统计"""
        total_keys = len(self.keys)
        enabled_keys = sum(
            1 for k in self.keys.values() if k.status == KeyStatus.ENABLED
        )
        pending_rotation = sum(
            1 for k in self.keys.values() if k.status == KeyStatus.PENDING_ROTATION
        )

        total_versions = sum(len(v) for v in self.key_versions.values())

        return {
            "total_keys": total_keys,
            "enabled_keys": enabled_keys,
            "pending_rotation": pending_rotation,
            "total_versions": total_versions,
            "encryption_records": len(self.encryption_records),
        }


# 全局实例
_kms_service: Optional[KMService] = None


def get_kms_service() -> KMService:
    """获取KMS服务"""
    global _kms_service
    if _kms_service is None:
        _kms_service = KMService()
    return _kms_service
