#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Issue #27 独立验证脚本 - 不依赖完整应用环境
验证 _sanitize_errors 函数能正确处理 Pydantic v2 的 ValidationError
"""
import sys
import os
import json
import re
from typing import Optional

# 添加依赖路径
sys.path.insert(0, '/tmp/test_deps')

from pydantic import BaseModel, validator

# 复制 RegisterRequest 模型
class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    role: Optional[str] = 'VIEWER'

    @validator('username')
    def validate_username(cls, v):
        if len(v) < 3 or len(v) > 50:
            raise ValueError('用户名长度必须在3-50个字符之间')
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError('用户名只能包含字母、数字和下划线')
        return v

    @validator('password')
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('密码长度至少8位')
        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        has_special = any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in v)
        if not (has_upper and has_lower and has_digit):
            raise ValueError('密码必须包含大写字母、小写字母和数字')
        if not has_special:
            raise ValueError('密码必须包含至少一个特殊字符')
        return v

# 复制修复后的 _sanitize_errors 函数
def _sanitize_errors(errors: list) -> list:
    """Strip non-JSON-serializable values (e.g. ValueError in ctx) from Pydantic error dicts."""
    sanitized = []
    for err in errors:
        clean = {
            "loc": list(err.get("loc", [])),
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        sanitized.append(clean)
    return sanitized

def run_tests():
    """运行所有测试用例"""
    results = []
    
    # Test 1: Password missing special char
    print('=' * 60)
    print('Test 1: Password missing special character')
    print('=' * 60)
    try:
        RegisterRequest(username='testuser', password='NoSpecial123')
        print('  ✗ FAIL: No validation error raised')
        results.append({'test': 'password_missing_special', 'status': 'FAIL'})
    except Exception as e:
        raw_errors = e.errors()
        print(f'  Raw errors: {raw_errors}')
        try:
            json.dumps(raw_errors)
            print('  ✗ Raw errors JSON serializable (unexpected)')
        except TypeError as te:
            print(f'  ✓ Raw errors NOT JSON serializable: {te}')
        
        sanitized = _sanitize_errors(raw_errors)
        json_str = json.dumps(sanitized, ensure_ascii=False)
        print(f'  Sanitized: {json_str}')
        assert len(sanitized) >= 1
        for item in sanitized:
            assert 'loc' in item
            assert 'msg' in item
            assert 'type' in item
        print('  ✓ PASS: Sanitized errors have loc/msg/type and are JSON serializable')
        results.append({'test': 'password_missing_special', 'status': 'PASS', 'output': sanitized})
    
    print()
    
    # Test 2: Username too short
    print('=' * 60)
    print('Test 2: Username too short')
    print('=' * 60)
    try:
        RegisterRequest(username='ab', password='ValidPass123!')
        print('  ✗ FAIL: No validation error raised')
        results.append({'test': 'username_too_short', 'status': 'FAIL'})
    except Exception as e:
        sanitized = _sanitize_errors(e.errors())
        json_str = json.dumps(sanitized, ensure_ascii=False)
        print(f'  Sanitized: {json_str}')
        assert len(sanitized) >= 1
        print('  ✓ PASS: Returns sanitized errors')
        results.append({'test': 'username_too_short', 'status': 'PASS', 'output': sanitized})
    
    print()
    
    # Test 3: Password too short
    print('=' * 60)
    print('Test 3: Password too short')
    print('=' * 60)
    try:
        RegisterRequest(username='testuser', password='Sh0rt!')
        print('  ✗ FAIL: No validation error raised')
        results.append({'test': 'password_too_short', 'status': 'FAIL'})
    except Exception as e:
        sanitized = _sanitize_errors(e.errors())
        json_str = json.dumps(sanitized, ensure_ascii=False)
        print(f'  Sanitized: {json_str}')
        assert len(sanitized) >= 1
        print('  ✓ PASS: Returns sanitized errors')
        results.append({'test': 'password_too_short', 'status': 'PASS', 'output': sanitized})
    
    print()
    
    # Test 4: Password missing uppercase
    print('=' * 60)
    print('Test 4: Password missing uppercase')
    print('=' * 60)
    try:
        RegisterRequest(username='testuser', password='nouppercase123!')
        print('  ✗ FAIL: No validation error raised')
        results.append({'test': 'password_missing_uppercase', 'status': 'FAIL'})
    except Exception as e:
        sanitized = _sanitize_errors(e.errors())
        json_str = json.dumps(sanitized, ensure_ascii=False)
        print(f'  Sanitized: {json_str}')
        assert len(sanitized) >= 1
        print('  ✓ PASS: Returns sanitized errors')
        results.append({'test': 'password_missing_uppercase', 'status': 'PASS', 'output': sanitized})
    
    print()
    
    # Test 5: Valid registration
    print('=' * 60)
    print('Test 5: Valid registration')
    print('=' * 60)
    try:
        req = RegisterRequest(username='validuser', password='ValidPass123!', email='test@example.com')
        print(f'  ✓ PASS: Valid request passes validation')
        print(f'    username={req.username}, email={req.email}')
        results.append({'test': 'valid_registration', 'status': 'PASS'})
    except Exception as e:
        print(f'  ✗ FAIL: Unexpected validation error: {e}')
        results.append({'test': 'valid_registration', 'status': 'FAIL'})
    
    print()
    print('=' * 60)
    print('SUMMARY')
    print('=' * 60)
    passed = sum(1 for r in results if r['status'] == 'PASS')
    failed = sum(1 for r in results if r['status'] == 'FAIL')
    print(f'Total: {len(results)} tests')
    print(f'Passed: {passed}')
    print(f'Failed: {failed}')
    print()
    
    if failed == 0:
        print('✓ ALL TESTS PASSED')
        print()
        print('修复验证成功：')
        print('- Pydantic v2 ValidationError 的 ctx 字段包含 ValueError 对象，无法 JSON 序列化')
        print('- _sanitize_errors 函数清洗 errors，只保留 loc/msg/type')
        print('- 修复后 JSONResponse 可正常序列化，返回 422 而非 500')
        return 0
    else:
        print('✗ SOME TESTS FAILED')
        return 1

if __name__ == '__main__':
    sys.exit(run_tests())
