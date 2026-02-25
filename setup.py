from setuptools import setup, find_packages

setup(
    name="emby-dedupe",
    version="2.0.0",
    packages=find_packages(),
    package_data={
        "emby_dedupe": ["templates/*.html", "static/css/*.css"],
    },
    include_package_data=True,
    install_requires=[
        "httpx>=0.28.1",
        "tqdm>=4.67.3",
        "backoff>=2.2.1",
        "jinja2>=3.1.6",
        "pyyaml>=6.0.3",  # For config file support
        "rank-torrent-name>=1.10.0",  # For quality source detection
        "python-dotenv>=1.0.0",  # Load .env files automatically
        "typer>=0.24.0",
    ],
    extras_require={
        "dev": [
            "pytest>=9.0.2",
            "pytest-cov>=7.0.0",
            "pytest-mock>=3.15.1",
            "pytest-xdist>=3.8.0",
            "mypy>=1.19.1",
            "ruff>=0.15.0",
            "vulture>=2.14",
            "requests>=2.32.5",
        ],
    },
    entry_points={
        "console_scripts": [
            "emby-dedupe=emby_dedupe.cli.app:main",
        ],
    },
    author="",
    author_email="",
    description="A tool for deduplicating media items in Emby libraries",
    keywords="emby, deduplication, media, server",
    url="",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Multimedia :: Video",
        "Topic :: Utilities",
        "Environment :: Console",
    ],
    python_requires=">=3.12",
)