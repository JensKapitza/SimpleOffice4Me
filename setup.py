"""Legacy editable-install compatibility for older pip/setuptools.

Modern builds use ``pyproject.toml``. Ubuntu 22.04 can fall back to
``setup.py develop`` when its effective build backend does not expose PEP 660.
Keep the compatibility metadata aligned with ``pyproject.toml`` so this path
never installs an ``UNKNOWN`` distribution or skips runtime dependencies.
"""

from pathlib import Path
import runpy

from setuptools import setup

# Also enforce the policy in isolated PEP 517 and legacy editable builds.
runpy.run_path(str(Path(__file__).with_name("simpleoffice_runtime_support.py")))["require_supported_runtime"]()


RUNTIME_DEPENDENCIES = [
    "Flask>=3.0,<4",
    "argon2-cffi>=23.1,<26",
    "beautifulsoup4>=4.12,<5",
    "defusedxml>=0.7,<1",
    "Pillow>=12.2,<13",
    "reportlab>=4.0,<6",
    "pypdf>=5.0,<7",
    "waitress>=3.0,<4",
    "cryptography>=48.0.1,<51",
    "dnspython>=2.6,<3",
    "watchdog>=6,<7",
    "tzdata>=2024.1",
]


setup(
    name="simpleoffice4me",
    version="2.0.0",
    description="Self-hosted, file-based document management",
    # Syntax floor only; vendor security policy is checked above.
    python_requires=">=3.10",
    classifiers=[f"Programming Language :: Python :: 3.{minor}" for minor in range(11, 15)],
    packages=["app", "app.library", "app.v2", "app.v2.adapters", "app.s3_overlay", "tools"],
    py_modules=[
        "simpleoffice_runtime_support",
        "simpleoffice_recovery_cli",
        "simpleoffice_version",
        "simpleoffice_mini_core",
        "simpleoffice_mini_runtime",
        "simpleoffice_mini_services",
        "simpleoffice_network_boot",
        "simpleoffice_network_boot_dhcp",
        "simpleoffice_network_gateway",
        "simpleoffice_network_gateway_runtime",
        "simpleoffice_gateway_rule_content",
        "simpleoffice_mini_control",
        "simpleoffice_firewall",
        "simpleoffice_firewall_agent",
        "simpleoffice_connection_relay",
        "simpleoffice_https_connect_tunnel",
        "simpleoffice_media_renderer",
        "simpleoffice_media_upnp",
        "simpleoffice_service_lifecycle",
        "simpleoffice_sip_runtime",
    ],
    install_requires=RUNTIME_DEPENDENCIES,
    extras_require={
        "web-export": ["playwright==1.55.0"],
        "security": ["pip-audit>=2.7,<3"],
        "quality": ["ruff==0.16.10"],
        "ocr": [
            "rapidocr==3.9.2",
            "onnxruntime==1.24.3; python_version == '3.10'",
            "onnxruntime==1.30.0; python_version >= '3.11'",
        ],
        "sftp": ["paramiko>=3.5,<6"],
        "banking": ["fints>=4.2,<6"],
        "erasure": ["zfec>=1.6.0.0,<2"],
    },
    entry_points={
        "console_scripts": [
            "simpleoffice-sftp=app.sftp_server:serve",
            "simpleoffice-v2-recovery=simpleoffice_recovery_cli:main",
        ]
    },
)
