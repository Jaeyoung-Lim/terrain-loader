#!/usr/bin/env python3
"""
Test script to verify the terrain downloader setup
"""

import sys
import os

def test_imports():
    """Test that all required modules can be imported"""
    print("Testing imports...")
    
    try:
        import flask
        print("✅ Flask imported successfully")
    except ImportError:
        print("❌ Flask import failed")
        return False
        
    try:
        import rasterio
        print("✅ Rasterio imported successfully")
    except ImportError:
        print("❌ Rasterio import failed")
        return False
        
    try:
        import requests
        print("✅ Requests imported successfully")
    except ImportError:
        print("❌ Requests import failed")
        return False
        
    try:
        import numpy
        print("✅ NumPy imported successfully")
    except ImportError:
        print("❌ NumPy import failed")
        return False
        
    try:
        import pyproj
        print("✅ PyProj imported successfully")
    except ImportError:
        print("❌ PyProj import failed")
        return False
        
    return True

def test_gdal():
    """Test GDAL functionality through rasterio"""
    print("\nTesting GDAL functionality...")
    
    try:
        import rasterio
        from rasterio.crs import CRS
        from rasterio.warp import calculate_default_transform
        
        # Test CRS creation
        crs_4326 = CRS.from_epsg(4326)
        crs_3857 = CRS.from_epsg(3857)
        print("✅ CRS creation works")
        
        # Test coordinate transformation calculation
        transform, width, height = calculate_default_transform(
            crs_4326, crs_3857, 100, 100, -180, -85, 180, 85
        )
        print("✅ Coordinate transformation works")
        
        return True
        
    except Exception as e:
        print(f"❌ GDAL test failed: {e}")
        return False

def test_utm_calculation():
    """Test UTM zone calculation"""
    print("\nTesting UTM zone calculation...")
    
    try:
        from app import TerrainDownloader
        
        downloader = TerrainDownloader()
        
        # Test various coordinates
        test_coords = [
            (-105.0, 40.0),  # Colorado, USA
            (2.0, 48.0),     # Paris, France
            (139.0, 35.0),   # Tokyo, Japan
            (-70.0, -33.0),  # Santiago, Chile
        ]
        
        for lon, lat in test_coords:
            zone, hemisphere = downloader.get_utm_zone(lon, lat)
            crs = downloader.get_utm_crs(lon, lat)
            print(f"✅ {lon}, {lat} -> Zone {zone} ({hemisphere}) -> {crs}")
        
        return True
        
    except Exception as e:
        print(f"❌ UTM calculation test failed: {e}")
        return False

def test_file_structure():
    """Test that required files exist"""
    print("\nTesting file structure...")
    
    required_files = [
        'app.py',
        'requirements.txt',
        'templates/index.html',
        'README.md'
    ]
    
    for file_path in required_files:
        if os.path.exists(file_path):
            print(f"✅ {file_path} exists")
        else:
            print(f"❌ {file_path} missing")
            return False
    
    return True

def main():
    print("🧪 Terrain Downloader Setup Test")
    print("=" * 40)
    
    tests = [
        ("File Structure", test_file_structure),
        ("Python Imports", test_imports),
        ("GDAL Functionality", test_gdal),
        ("UTM Calculation", test_utm_calculation),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n📋 {test_name}")
        print("-" * 20)
        result = test_func()
        results.append((test_name, result))
    
    print("\n" + "=" * 40)
    print("📊 Test Results:")
    
    all_passed = True
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {test_name}: {status}")
        if not result:
            all_passed = False
    
    if all_passed:
        print("\n🎉 All tests passed! The setup is ready to use.")
        print("Run 'python run.py' to start the server.")
    else:
        print("\n⚠️  Some tests failed. Please check the requirements and setup.")
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
