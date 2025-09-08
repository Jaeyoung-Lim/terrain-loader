"""
Configuration settings for the Terrain Data Downloader
"""

import os

class Config:
    # Server settings
    HOST = '0.0.0.0'
    PORT = 5000
    DEBUG = True
    
    # Download settings
    DOWNLOAD_DIR = os.path.join(os.getcwd(), 'downloads')
    MAX_DOWNLOAD_SIZE = 1024 * 1024 * 100  # 100MB max per download
    
    # USGS Service URLs
    USGS_3DEP_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
    USGS_NAIP_URL = "https://services.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/exportImage"
    
    # Default image sizes (pixels)
    ELEVATION_SIZE = (1024, 1024)
    IMAGERY_SIZE = (2048, 2048)
    
    # Timeout settings (seconds)
    DOWNLOAD_TIMEOUT = 120
    STATUS_CHECK_INTERVAL = 2
    
    # Coordinate system settings
    DEFAULT_CRS = 'EPSG:4326'  # WGS84
    WEB_MERCATOR_CRS = 'EPSG:3857'
    
    # File cleanup settings (hours)
    CLEANUP_AFTER_HOURS = 24

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    HOST = '127.0.0.1'  # More secure for production

# Default configuration
config = DevelopmentConfig()
