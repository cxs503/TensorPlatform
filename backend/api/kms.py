"""密钥管理 API (KMS)。

当前内置服务仅用于开发演示，不能提供生产级密钥保护。除非显式配置了
真实 KMS 后端，否则拒绝所有会创建、修改或使用密钥的请求，避免把简单
的内存/XOR 实现误当作加密能力。
"""
import os

from fastapi import APIRouter, HTTPException
from services.kms_service import KeyAlgorithm, KeyUsage, get_kms_service

router = APIRouter(prefix="/kms", tags=["密钥管理"])
KMS_BACKEND_ENABLED = os.getenv("KMS_BACKEND_ENABLED", "false").lower() == "true"


def require_kms_backend() -> None:
    if not KMS_BACKEND_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="生产级 KMS 后端尚未配置，已关闭内存模拟加密",
        )


# ========== 密钥管理 ==========


@router.get("/keys")
def list_keys(user_id: str = None, status: str = None, usage: str = None):
    """列出密钥"""
    if not KMS_BACKEND_ENABLED:
        return []
    service = get_kms_service()
    return service.list_keys(user_id, status, usage)


@router.post("/keys")
def create_key(req: dict):
    """创建密钥"""
    require_kms_backend()
    service = get_kms_service()
    key = service.create_key(
        name=req.get("name"),
        algorithm=req.get("algorithm", KeyAlgorithm.AES_256),
        usage=req.get("usage", KeyUsage.ENCRYPTION),
        user_id=req.get("user_id"),
        rotation_days=req.get("rotation_days", 90),
    )
    return {
        "id": key.id,
        "name": key.name,
        "algorithm": key.algorithm,
        "usage": key.usage,
        "status": key.status,
        "rotation_days": key.rotation_days,
    }


@router.get("/keys/{key_id}")
def get_key(key_id: str):
    """获取密钥"""
    require_kms_backend()
    service = get_kms_service()
    key = service.get_key(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="密钥不存在")
    return {
        "id": key.id,
        "name": key.name,
        "algorithm": key.algorithm,
        "usage": key.usage,
        "status": key.status,
        "rotation_days": key.rotation_days,
        "created_at": key.created_at,
        "updated_at": key.updated_at,
        "last_rotated": key.last_rotated,
        "expires_at": key.expires_at,
    }


@router.put("/keys/{key_id}")
def update_key(key_id: str, req: dict):
    """更新密钥"""
    require_kms_backend()
    service = get_kms_service()
    key = service.update_key(
        key_id=key_id,
        name=req.get("name"),
        rotation_days=req.get("rotation_days"),
        status=req.get("status"),
    )
    if not key:
        raise HTTPException(status_code=404, detail="密钥不存在")
    return {"message": "更新成功"}


@router.delete("/keys/{key_id}")
def delete_key(key_id: str):
    """删除密钥"""
    require_kms_backend()
    service = get_kms_service()
    if not service.delete_key(key_id):
        raise HTTPException(status_code=404, detail="密钥不存在")
    return {"message": "删除成功"}


# ========== 密钥轮换 ==========


@router.post("/keys/{key_id}/rotate")
def rotate_key(key_id: str):
    """轮换密钥"""
    require_kms_backend()
    service = get_kms_service()
    key = service.rotate_key(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="密钥不存在")
    return {
        "message": "轮换成功",
        "version_count": len(service.key_versions.get(key_id, [])),
    }


@router.get("/keys/{key_id}/versions")
def get_key_versions(key_id: str):
    """获取密钥版本"""
    require_kms_backend()
    service = get_kms_service()
    return service.get_key_versions(key_id)


# ========== 加密解密 ==========


@router.post("/encrypt")
def encrypt(req: dict):
    """加密数据"""
    require_kms_backend()
    service = get_kms_service()
    result = service.encrypt(
        key_id=req.get("key_id"),
        plaintext=req.get("plaintext"),
        associated_data=req.get("associated_data"),
    )
    if not result:
        raise HTTPException(status_code=400, detail="加密失败")
    return result


@router.post("/decrypt")
def decrypt(req: dict):
    """解密数据"""
    require_kms_backend()
    service = get_kms_service()
    plaintext = service.decrypt(
        key_id=req.get("key_id"),
        ciphertext=req.get("ciphertext"),
        version=req.get("version"),
    )
    if plaintext is None:
        raise HTTPException(status_code=400, detail="解密失败")
    return {"plaintext": plaintext}


# ========== 签名验签 ==========


@router.post("/sign")
def sign(req: dict):
    """签名"""
    require_kms_backend()
    service = get_kms_service()
    result = service.sign(key_id=req.get("key_id"), data=req.get("data"))
    if not result:
        raise HTTPException(status_code=400, detail="签名失败")
    return result


@router.post("/verify")
def verify(req: dict):
    """验签"""
    require_kms_backend()
    service = get_kms_service()
    valid = service.verify(
        key_id=req.get("key_id"), data=req.get("data"), signature=req.get("signature")
    )
    return {"valid": valid}


# ========== 统计 ==========


@router.get("/stats")
def get_kms_stats():
    """获取KMS统计"""
    if not KMS_BACKEND_ENABLED:
        return {
            "available": False,
            "backend": None,
            "message": "生产级 KMS 后端尚未配置，内存模拟加密已关闭",
            "total_keys": 0,
            "active_keys": 0,
        }
    service = get_kms_service()
    return {"available": True, **service.get_kms_stats()}
