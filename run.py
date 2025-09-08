#!/usr/bin/env python3
"""
Terrain Data Downloader - Startup Script
Simple script to run the terrain data downloader application
"""

import sys
import subprocess
import os

def check_dependencies():
    """Check if required dependencies are installed"""
    try:
        import flask
        import rasterio
        import requests
        import numpy
        import pyproj
        print("✅ All dependencies found")
        return True
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("Please install dependencies with: pip install -r requirements.txt")
        return False

def check_gdal():
    """Check if GDAL is properly installed"""
    try:
        import rasterio
        from rasterio.crs import CRS
        # Test GDAL functionality
        crs = CRS.from_epsg(4326)
        print("✅ GDAL is working correctly")
        return True
    except Exception as e:
        print(f"❌ GDAL issue: {e}")
        print("Please ensure GDAL is properly installed:")
        print("  Ubuntu/Debian: sudo apt-get install gdal-bin libgdal-dev")
        print("  macOS: brew install gdal")
        print("  Windows: Download from https://www.lfd.uci.edu/~gohlke/pythonlibs/#gdal")
        return False

def main():
    print("🏔️  Terrain Data Downloader")
    print("=" * 40)
    
    # Check if we're in the right directory
    if not os.path.exists('app.py'):
        print("❌ app.py not found. Please run this script from the project directory.")
        sys.exit(1)
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    # Check GDAL
    if not check_gdal():
        sys.exit(1)
    
    # Create downloads directory
    os.makedirs('downloads', exist_ok=True)
    
    print("\n🚀 Starting server...")
    print("📍 Server will be available at: http://localhost:5000")
    print("🛑 Press Ctrl+C to stop the server")
    print("-" * 40)
    
    try:
        # Import and run the Flask app
        from app import app
        app.run(debug=True, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("\n👋 Server stopped")
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
