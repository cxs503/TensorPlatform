"""
Open Digital Foundation SDK
"""
from setuptools import find_packages, setup

setup(
    name="open-foundation",
    version="1.0.0",
    description="Open Digital Foundation Python SDK",
    author="Open Digital Foundation",
    packages=find_packages(),
    install_requires=[
        "requests>=2.25.0",
    ],
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
)
