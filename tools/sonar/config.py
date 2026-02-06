#!/usr/bin/env python3
"""
Shared SonarQube configuration for scripts.
Loads configuration from sonar-project.properties file.
"""
import os


def _load_sonar_config():
    config = {}
    try:
        with open('sonar-project.properties', 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    config[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return config


_config = _load_sonar_config()

# SonarQube server configuration
SONAR_URL = os.getenv("SONAR_URL") or _config.get("sonar.host.url", "http://sonarqube.sonarcube.orb.local")
SONAR_TOKEN = os.getenv("SONAR_TOKEN") or _config.get("sonar.token", "")
PROJECT_KEY = os.getenv("SONAR_PROJECT_KEY") or _config.get("sonar.projectKey", "emby-dedupe")
