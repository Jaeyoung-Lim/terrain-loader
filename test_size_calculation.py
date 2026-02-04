#!/usr/bin/env python3
"""
Test the size calculation logic to verify accuracy
"""

import math

def calculate_dem_size(bounds, resolution_m):
    """Calculate DEM size for given bounds and resolution"""
    
    # Calculate area in degrees
    width_deg = bounds['east'] - bounds['west']
    height_deg = bounds['north'] - bounds['south']
    
    # Convert to approximate kilometers
    center_lat = (bounds['north'] + bounds['south']) / 2
    lat_factor = math.cos(math.radians(center_lat))
    width_km = width_deg * 111.32 * lat_factor
    height_km = height_deg * 111.32
    area_km2 = width_km * height_km
    
    # Convert to meters
    width_m = width_km * 1000
    height_m = height_km * 1000
    
    # Calculate pixels
    pixels_width = math.ceil(width_m / resolution_m)
    pixels_height = math.ceil(height_m / resolution_m)
    total_pixels = pixels_width * pixels_height
    
    # Estimate file size (4 bytes per F32 pixel + compression)
    raw_size_bytes = total_pixels * 4
    compressed_size_bytes = raw_size_bytes * 0.4  # 40% compression
    file_size_mb = compressed_size_bytes / (1024 * 1024)
    
    return {
        'area_km2': area_km2,
        'width_km': width_km,
        'height_km': height_km,
        'pixels_width': pixels_width,
        'pixels_height': pixels_height,
        'total_pixels': total_pixels,
        'file_size_mb': file_size_mb
    }

def format_size(size_mb):
    """Format file size for display"""
    if size_mb < 1:
        return f"{size_mb * 1024:.0f} KB"
    elif size_mb < 1024:
        return f"{size_mb:.1f} MB"
    else:
        return f"{size_mb / 1024:.2f} GB"

def format_area(area_km2):
    """Format area for display"""
    if area_km2 < 0.01:
        return f"{area_km2 * 1000000:.0f} m²"
    elif area_km2 < 1:
        return f"{area_km2 * 100:.1f} hectares"
    else:
        return f"{area_km2:.2f} km²"

if __name__ == "__main__":
    # Test cases
    test_cases = [
        {
            'name': 'Small urban area (1km × 1km)',
            'bounds': {'west': -122.45, 'south': 37.75, 'east': -122.44, 'north': 37.76}
        },
        {
            'name': 'Medium area (10km × 10km)',
            'bounds': {'west': -122.5, 'south': 37.7, 'east': -122.4, 'north': 37.8}
        },
        {
            'name': 'Large area (50km × 50km)',
            'bounds': {'west': -122.7, 'south': 37.5, 'east': -122.2, 'north': 38.0}
        }
    ]
    
    resolutions = [1, 3, 10, 30]
    
    print("DEM Size Estimation Test")
    print("=" * 80)
    
    for case in test_cases:
        print(f"\n{case['name']}")
        print("-" * 50)
        
        for resolution in resolutions:
            result = calculate_dem_size(case['bounds'], resolution)
            
            print(f"{resolution:2d}m resolution:")
            print(f"  Area: {format_area(result['area_km2'])}")
            print(f"  Dimensions: {result['width_km']:.1f}km × {result['height_km']:.1f}km")
            print(f"  Pixels: {result['pixels_width']:,} × {result['pixels_height']:,} = {result['total_pixels']:,}")
            print(f"  Est. file size: {format_size(result['file_size_mb'])}")
            print()
    
    print("=" * 80)
    print("Notes:")
    print("- Estimates assume F32 data type (4 bytes per pixel)")
    print("- Compression factor of 40% applied (typical for TIFF LZW)")
    print("- Actual sizes may vary based on data complexity and compression")
    print("- Geographic calculations are approximate")




