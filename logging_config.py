#!/usr/bin/env python3
"""
Dynamic logging configuration for keeper bots.
Uses JSON formatting only when running on Google Cloud Platform.
"""

import os
import logging.config
import requests
import yaml
from typing import Dict, Any


def is_running_on_gcp() -> bool:
    """
    Detect if the application is running on Google Cloud Platform.
    
    Returns:
        bool: True if running on GCP, False otherwise
    """
    # Method 1: Check for Google Cloud metadata service
    try:
        response = requests.get(
            'http://metadata.google.internal/computeMetadata/v1/',
            headers={'Metadata-Flavor': 'Google'},
            timeout=1
        )
        if response.status_code == 200:
            return True
    except Exception:
        # Catch all exceptions including mocked ones in tests
        pass
    
    # Method 2: Check for common GCP environment variables
    gcp_env_vars = [
        'GOOGLE_CLOUD_PROJECT',
        'GCP_PROJECT', 
        'GCLOUD_PROJECT',
        'K_SERVICE',  # Cloud Run
        'GAE_SERVICE',  # App Engine
    ]
    
    for env_var in gcp_env_vars:
        if os.getenv(env_var):
            return True
    
    # Method 3: Check if gcloud CLI is available and authenticated
    try:
        import subprocess
        result = subprocess.run(
            ['gcloud', 'config', 'get-value', 'core/project'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    except (subprocess.SubprocessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    return False


def setup_logging(logger_name: str) -> None:
    """
    Set up logging configuration dynamically based on environment.
    
    Args:
        logger_name: Name of the logger to configure
    """
    # Load base configuration
    config_path = "log_conf.yaml"
    if not os.path.exists(config_path):
        # Fallback to basic logging if config file doesn't exist
        logging.basicConfig(
            level=logging.INFO,
            format='[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d] %(message)s'
        )
        return
    
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    
    # Modify configuration based on environment
    is_gcp = is_running_on_gcp()
    
    if is_gcp:
        # Running on GCP - use JSON formatting for errors
        print(f"Detected GCP environment - enabling JSON logging for {logger_name}")
        # Keep existing configuration (uses both json and access handlers)
    else:
        # Running locally - use only access formatting (no JSON)
        print(f"Detected local environment - using standard logging for {logger_name}")
        
        # Remove JSON handler from all bot loggers
        bot_loggers = [
            'announcer_configure_bot',
            'announcer_update_bot', 
            'oracle_update_bot',
            'statutes_update_bot',
            'recharge_start_settle_bot',
            'savings_bot',
            'surplus_start_settle_bot',
            'governance_bot'
        ]
        
        for bot_logger in bot_loggers:
            if bot_logger in config.get('loggers', {}):
                handlers = config['loggers'][bot_logger].get('handlers', [])
                # Remove 'json' handler, keep only 'access' handler
                if 'json' in handlers:
                    handlers.remove('json')
                config['loggers'][bot_logger]['handlers'] = handlers
    
    # Apply the configuration
    logging.config.dictConfig(config)