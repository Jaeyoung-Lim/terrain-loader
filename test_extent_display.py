#!/usr/bin/env python3
"""
Test the extent display logic
"""

import math

def calculate_extent_display(bounds):
    """Calculate extent display for given bounds"""
    
    # Calculate area in degrees
    width_deg = bounds['east'] - bounds['west']
    height_deg = bounds['north'] - bounds['south']
    
    # Convert to approximate kilometers
    center_lat = (bounds['north'] + bounds['south']) / 2
    lat_factor = math.cos(math.radians(center_lat))
    width_km = width_deg * 111.32 * lat_factor
    height_km = height_deg * 111.32
    
    # Format extent display (width × height)
    if width_km < 1 and height_km < 1:
        # Show in meters for small areas
        extent_display = f"{(width_km * 1000):.0f}m × {(height_km * 1000):.0f}m"
    elif width_km < 10 and height_km < 10:
        # Show in kilometers with 1 decimal for medium areas
        extent_display = f"{width_km:.1f}km × {height_km:.1f}km"
    else:
        # Show in kilometers with no decimals for large areas
        extent_display = f"{width_km:.0f}km × {height_km:.0f}km"
    
    return extent_display, width_km, height_km

if __name__ == "__main__":
    # Test cases
    test_cases = [
        {
            'name': 'Very small area (100m × 100m)',
            'bounds': {'west': -122.4501, 'south': 37.7501, 'east': -122.4499, 'north': 37.7509}
        },
        {
            'name': 'Small area (1km × 1km)',
            'bounds': {'west': -122.45, 'south': 37.75, 'east': -122.44, 'north': 37.76}
        },
        {
            'name': 'Medium area (5km × 5km)',
            'bounds': {'west': -122.475, 'south': 37.725, 'east': -122.425, 'north': 37.775}
        },
        {
            'name': 'Large area (50km × 50km)',
            'bounds': {'west': -122.7, 'south': 37.5, 'east': -122.2, 'north': 38.0}
        }
    ]
    
    print("Extent Display Test")
    print("=" * 40)
    
    for case in test_cases:
        extent_display, width_km, height_km = calculate_extent_display(case['bounds'])
        print(f"\n{case['name']}")
        print(f"  Actual size: {width_km:.2f}km × {height_km:.2f}km")
        print(f"  Display: {extent_display}")
    
    print("\n" + "=" * 40)
    print("This shows what users will see in the 'Extent:' field")
    print("- Small areas (< 1km): shown in meters")
    print("- Medium areas (< 10km): shown in km with 1 decimal")
    print("- Large areas (≥ 10km): shown in km with no decimals")




