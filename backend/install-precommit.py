# Setup pre-commit hooks
"""
Pre-commit hooks 安装脚本
"""
import os
import sys


def install_precommit():
    """安装 pre-commit hooks"""
    try:
        import subprocess

        result = subprocess.run(
            ["pip", "install", "pre-commit"], capture_output=True, text=True
        )

        if result.returncode != 0:
            print("安装 pre-commit 失败:", result.stderr)
            return False

        result = subprocess.run(
            ["pre-commit", "install"], capture_output=True, text=True
        )

        if result.returncode == 0:
            print("✓ pre-commit hooks 安装成功")
            return True
        else:
            print("安装失败:", result.stderr)
            return False

    except Exception as e:
        print(f"安装出错: {e}")
        return False


if __name__ == "__main__":
    if install_precommit():
        print("\n安装完成！")
        print("运行 'pre-commit run --all-files' 来验证配置")
    else:
        print("\n安装失败，请手动运行:")
        print("  pip install pre-commit")
        print("  pre-commit install")
        sys.exit(1)
