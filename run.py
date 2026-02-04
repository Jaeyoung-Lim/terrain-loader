#!/usr/bin/env python3
"""
Terrain Data Downloader - Startup Script
Simple script to run the terrain data downloader application
"""

import sys
import subprocess
import os

def check_dependencies():
    """Check if required dependencies are installed with compatible versions"""
    try:
        import flask
        import requests
        import numpy
        import pyproj
        
        # Check numpy version BEFORE importing rasterio
        # rasterio 1.3.x requires numpy<2.0
        numpy_major = int(numpy.__version__.split('.')[0])
        if numpy_major >= 2:
            print(f"❌ NumPy version conflict: numpy {numpy.__version__} is installed")
            print("   rasterio 1.3.x requires numpy<2.0")
            print("")
            print("Fix with: pip install 'numpy==1.26.4' --force-reinstall")
            print("Or reinstall all deps: pip install -r requirements.txt --force-reinstall")
            return False
        
        import rasterio
        print(f"✅ All dependencies found (numpy {numpy.__version__})")
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
