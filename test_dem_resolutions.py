#!/usr/bin/env python3
"""
Test different DEM resolutions available from USGS 3DEP services
"""

import requests
import rasterio
import os
from pyproj import Transformer

def test_resolution_availability(bounds, test_resolutions=[1, 3, 10, 30]):
    """Test what DEM resolutions are available from USGS services"""
    
    print("=== Testing Available DEM Resolutions ===")
    print(f"Test area: {bounds}")
    
    # USGS 3DEP services to test
    services = [
        {
            'name': '3DEP Elevation ImageServer',
            'url': 'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage'
        },
        {
            'name': '1-Meter DEM ImageServer', 
            'url': 'https://elevation.nationalmap.gov/arcgis/rest/services/1DEP/ImageServer/exportImage'
        }
    ]
    
    for service in services:
        print(f"\n--- Testing {service['name']} ---")
        
        for resolution in test_resolutions:
            print(f"\nTesting {resolution}m resolution:")
            
            # Calculate appropriate size for the resolution
            # For a ~1km test area, calculate pixels needed
            area_width_deg = bounds['east'] - bounds['west']
            area_height_deg = bounds['north'] - bounds['south']
            
            # Rough conversion: 1 degree ≈ 111km at equator
            area_width_m = area_width_deg * 111000
            area_height_m = area_height_deg * 111000
            
            pixels_width = int(area_width_m / resolution)
            pixels_height = int(area_height_m / resolution)
            
            # Limit to reasonable size for testing
            pixels_width = min(2048, max(64, pixels_width))
            pixels_height = min(2048, max(64, pixels_height))
            
            params = {
                'bbox': f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}",
                'bboxSR': '4326',
                'size': f'{pixels_width},{pixels_height}',
                'imageSR': '4326',
                'format': 'tiff',
                'pixelType': 'F32',
                'f': 'image'
            }
            
            try:
                response = requests.get(service['url'], params=params, timeout=60)
                
                if response.status_code == 200 and len(response.content) > 1000:
                    # Save and analyze the result
                    filename = f"test_dem_{resolution}m_{service['name'].replace(' ', '_').lower()}.tif"
                    with open(filename, 'wb') as f:
                        f.write(response.content)
                    
                    try:
                        with rasterio.open(filename) as src:
                            # Calculate actual resolution
                            bounds_actual = src.bounds
                            width_actual = bounds_actual.right - bounds_actual.left
                            height_actual = bounds_actual.top - bounds_actual.bottom
                            
                            # Convert to meters for resolution calculation
                            if src.crs.to_string() == 'EPSG:4326':
                                # Rough conversion for WGS84
                                center_lat = (bounds_actual.top + bounds_actual.bottom) / 2
                                width_m = width_actual * 111000 * abs(center_lat / 90)  # Rough latitude adjustment
                                height_m = height_actual * 111000
                            else:
                                width_m = width_actual
                                height_m = height_actual
                            
                            actual_res_x = width_m / src.width
                            actual_res_y = height_m / src.height
                            
                            print(f"  ✅ SUCCESS: {src.width}x{src.height} pixels")
                            print(f"     Actual resolution: {actual_res_x:.1f}m x {actual_res_y:.1f}m")
                            print(f"     CRS: {src.crs}")
                            print(f"     File size: {len(response.content):,} bytes")
                            
                            # Check if this matches the requested resolution
                            if abs(actual_res_x - resolution) < resolution * 0.5:
                                print(f"     🎯 Resolution matches request (~{resolution}m)")
                            else:
                                print(f"     ⚠️  Resolution differs from request ({resolution}m)")
                                
                    except Exception as e:
                        print(f"  ❌ Error reading file: {e}")
                        
                else:
                    print(f"  ❌ Request failed: {response.status_code}")
                    if len(response.content) < 1000:
                        print(f"     Response: {response.content[:200]}")
                        
            except Exception as e:
                print(f"  ❌ Request error: {e}")

def test_specific_high_res_services():
    """Test specific high-resolution DEM services"""
    
    print("\n=== Testing Specific High-Resolution Services ===")
    
    # Test area around San Francisco
    bounds = {'west': -122.45, 'south': 37.75, 'east': -122.44, 'north': 37.76}
    
    # Known high-resolution services
    high_res_services = [
        {
            'name': 'USGS 1-Meter DEM',
            'url': 'https://elevation.nationalmap.gov/arcgis/rest/services/1DEP/ImageServer/exportImage',
            'expected_resolution': 1
        },
        {
            'name': 'USGS 3-Meter DEM (1/9 arc-second)',
            'url': 'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage',
            'expected_resolution': 3
        }
    ]
    
    for service in high_res_services:
        print(f"\n--- Testing {service['name']} ---")
        
        # Calculate size for expected resolution
        area_width_deg = bounds['east'] - bounds['west']
        area_height_deg = bounds['north'] - bounds['south']
        area_width_m = area_width_deg * 111000
        area_height_m = area_height_deg * 111000
        
        expected_res = service['expected_resolution']
        pixels_width = min(2048, int(area_width_m / expected_res))
        pixels_height = min(2048, int(area_height_m / expected_res))
        
        params = {
            'bbox': f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}",
            'bboxSR': '4326',
            'size': f'{pixels_width},{pixels_height}',
            'imageSR': '4326',
            'format': 'tiff',
            'pixelType': 'F32',
            'f': 'image'
        }
        
        try:
            response = requests.get(service['url'], params=params, timeout=90)
            
            if response.status_code == 200 and len(response.content) > 1000:
                filename = f"high_res_{expected_res}m_test.tif"
                with open(filename, 'wb') as f:
                    f.write(response.content)
                
                with rasterio.open(filename) as src:
                    print(f"  ✅ Available: {src.width}x{src.height} pixels")
                    print(f"     CRS: {src.crs}")
                    print(f"     Bounds: {src.bounds}")
                    print(f"     File size: {len(response.content):,} bytes")
                    
            else:
                print(f"  ❌ Not available or failed: {response.status_code}")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")

if __name__ == "__main__":
    # Test area around San Francisco (known to have good data coverage)
    test_bounds = {'west': -122.45, 'south': 37.75, 'east': -122.44, 'north': 37.76}
    
    print("Testing DEM resolution availability from USGS 3DEP services")
    print("=" * 60)
    
    # Test different resolutions
    test_resolution_availability(test_bounds, [1, 3, 10, 30])
    
    # Test specific high-resolution services
    test_specific_high_res_services()
    
    print("\n" + "=" * 60)
    print("SUMMARY:")
    print("- Check generated .tif files to see actual data quality")
    print("- Higher resolutions may not be available in all areas")
    print("- 1m resolution typically available in urban/developed areas")
    print("- 3m resolution has broader coverage than 1m")
    print("- 10m resolution has nationwide coverage")
    print("=" * 60)




