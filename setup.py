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
        "httpx>=0.27.0",
        "tqdm>=4.66.2",
        "backoff>=2.2.1",
        "jinja2>=3.1.3",
        "pyyaml>=6.0",  # For config file support
        "rank-torrent-name>=1.0.0",  # For quality source detection
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "pytest-mock>=3.10.0",
            "pytest-xdist>=3.5.0",
            "mypy>=1.3.0",
            "ruff>=0.0.270",
            "vulture>=2.7",
            "requests>=2.31.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "emby-dedupe=emby_dedupe.cli.main:main",
        ],
    },
    author="Troy Kelly",
    author_email="troy@troykelly.com",
    description="A tool for deduplicating media items in Emby libraries",
    keywords="emby, deduplication, media, server",
    url="https://github.com/troykelly/emby-dedupe",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "Topic :: Multimedia :: Video",
        "Topic :: Utilities",
        "Environment :: Console",
    ],
    python_requires=">=3.12",
)