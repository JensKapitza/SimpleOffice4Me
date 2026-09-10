"""Legacy editable-install compatibility for older pip/setuptools.

Modern builds use ``pyproject.toml``. Ubuntu 22.04 can fall back to
``setup.py develop`` when its effective build backend does not expose PEP 660.
Keep the compatibility metadata aligned with ``pyproject.toml`` so this path
never installs an ``UNKNOWN`` distribution or skips runtime dependencies.
"""

from setuptools import setup


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
    "watchdog>=6,<7",
    "tzdata>=2024.1",
]


setup(
    name="simpleoffice4me",
    version="1.0.0",
    description="Self-hosted, file-based document management",
    python_requires=">=3.10",
    packages=["app", "app.library"],
    py_modules=[
        "simpleoffice_version",
        "simpleoffice_mini_core",
        "simpleoffice_mini_runtime",
        "simpleoffice_mini_services",
        "simpleoffice_network_boot",
        "simpleoffice_network_boot_dhcp",
        "simpleoffice_network_gateway",
        "simpleoffice_network_gateway_runtime",
    ],
    install_requires=RUNTIME_DEPENDENCIES,
    extras_require={
        "security": ["pip-audit>=2.7,<3"],
        "sftp": ["paramiko>=3.5,<6"],
    },
    entry_points={
        "console_scripts": [
            "simpleoffice-sftp=app.sftp_server:serve",
        ]
    },
)
