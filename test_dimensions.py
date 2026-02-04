#!/usr/bin/env python3
"""
Test the dimension display logic
"""

import math

def calculate_dimensions(bounds, resolution_m):
    """Calculate DEM dimensions for given bounds and resolution"""
    
    # Calculate area in degrees
    width_deg = bounds['east'] - bounds['west']
    height_deg = bounds['north'] - bounds['south']
    
    # Convert to approximate kilometers
    center_lat = (bounds['north'] + bounds['south']) / 2
    lat_factor = math.cos(math.radians(center_lat))
    width_km = width_deg * 111.32 * lat_factor
    height_km = height_deg * 111.32
    
    # Convert to meters
    width_m = width_km * 1000
    height_m = height_km * 1000
    
    # Calculate pixels
    pixels_width = math.ceil(width_m / resolution_m)
    pixels_height = math.ceil(height_m / resolution_m)
    
    return pixels_width, pixels_height

def format_dimensions(width, height):
    """Format dimensions for display"""
    return f"{width:,} × {height:,}"

if __name__ == "__main__":
    # Test cases
    test_cases = [
        {
            'name': 'Small area (1km × 1km)',
            'bounds': {'west': -122.45, 'south': 37.75, 'east': -122.44, 'north': 37.76}
        },
        {
            'name': 'Medium area (10km × 10km)',
            'bounds': {'west': -122.5, 'south': 37.7, 'east': -122.4, 'north': 37.8}
        }
    ]
    
    resolutions = [1, 3, 10, 30]
    
    print("DEM Dimension Display Test")
    print("=" * 50)
    
    for case in test_cases:
        print(f"\n{case['name']}")
        print("-" * 30)
        
        for resolution in resolutions:
            width, height = calculate_dimensions(case['bounds'], resolution)
            dimension_display = format_dimensions(width, height)
            
            print(f"{resolution:2d}m resolution: {dimension_display}")
    
    print("\n" + "=" * 50)
    print("This matches what users will see in the 'Dimensions:' field")




