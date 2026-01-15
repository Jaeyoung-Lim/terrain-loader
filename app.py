from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import requests
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.crs import CRS
import numpy as np
import os
import tempfile
import zipfile
import threading
import time
from datetime import datetime
import uuid
import json
from pyproj import Transformer
from scipy.spatial.distance import cdist
import logging

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global dictionary to track download progress
download_progress = {}

class TerrainDownloader:
    def __init__(self):
        self.base_dir = os.path.join(os.getcwd(), 'downloads')
        os.makedirs(self.base_dir, exist_ok=True)
        self.pixel_size = 10.0  # Fixed 10-meter resolution for perfect alignment
    
    
    def get_utm_zone(self, lon, lat):
        """Calculate UTM zone from longitude and latitude"""
        zone = int((lon + 180) / 6) + 1
        hemisphere = 'north' if lat >= 0 else 'south'
        return zone, hemisphere
    
    def get_utm_crs(self, lon, lat):
        """Get UTM CRS string for given coordinates"""
        zone, hemisphere = self.get_utm_zone(lon, lat)
        if hemisphere == 'north':
            return f'EPSG:{32600 + zone}'
        else:
            return f'EPSG:{32700 + zone}'
    
    def create_perfect_reference_grid(self, bounds, target_crs):
        """Create a reference coordinate grid for perfect alignment"""
        try:
            # Convert bounds to target CRS
            transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
            xmin, ymin = transformer.transform(bounds['west'], bounds['south'])
            xmax, ymax = transformer.transform(bounds['east'], bounds['north'])
            
            # Calculate dimensions based on fixed pixel size
            utm_width = abs(xmax - xmin)
            utm_height = abs(ymax - ymin)
            
            # Calculate natural pixel dimensions
            natural_width = int(utm_width / self.pixel_size)
            natural_height = int(utm_height / self.pixel_size)
            
            # Use adaptive minimum constraints to avoid massive expansion for small areas
            # For very small areas, use a smaller minimum to preserve user intent
            min_pixels = 16 if (natural_width < 16 or natural_height < 16) else 32
            
            # Apply constraints: minimum adaptive, maximum 4096 pixels
            width = max(min_pixels, min(4096, natural_width))
            height = max(min_pixels, min(4096, natural_height))
            
            # Log if constraints are applied
            if width != natural_width or height != natural_height:
                logger.info(f"Applied size constraints: {natural_width}x{natural_height} → {width}x{height}")
                expansion_factor = (width * height) / (natural_width * natural_height) if natural_width > 0 and natural_height > 0 else 1
                if expansion_factor > 2:
                    logger.warning(f"Area expansion: {expansion_factor:.1f}x due to minimum size constraints")
                    logger.info(f"Consider selecting a larger area to avoid expansion")
            
            # Snap to exact pixel boundaries to eliminate sub-pixel misalignment
            xmin_snapped = np.floor(xmin / self.pixel_size) * self.pixel_size
            ymin_snapped = np.floor(ymin / self.pixel_size) * self.pixel_size
            xmax_snapped = xmin_snapped + (width * self.pixel_size)
            ymax_snapped = ymin_snapped + (height * self.pixel_size)
            
            # Create reference transform
            reference_transform = rasterio.transform.from_bounds(
                xmin_snapped, ymin_snapped, xmax_snapped, ymax_snapped, width, height
            )
            
            logger.info(f"Perfect reference grid: {width}x{height} at {self.pixel_size}m resolution")
            
            return {
                'width': width,
                'height': height,
                'transform': reference_transform,
                'crs': target_crs,
                'bounds': (xmin_snapped, ymin_snapped, xmax_snapped, ymax_snapped)
            }
            
        except Exception as e:
            logger.error(f"Error creating reference grid: {str(e)}")
            return None
    
    def align_to_perfect_grid(self, input_path, reference_grid, is_elevation=True):
        """Align a raster to the perfect reference grid with gap filling"""
        try:
            output_path = input_path.replace('.tif', '_perfect_aligned.tif')
            
            with rasterio.open(input_path) as src:
                # Set up output profile to match reference grid exactly
                profile = {
                    'driver': 'GTiff',
                    'width': reference_grid['width'],
                    'height': reference_grid['height'],
                    'count': src.count,
                    'dtype': src.dtypes[0],
                    'crs': reference_grid['crs'],
                    'transform': reference_grid['transform'],
                    'compress': 'lzw'
                }
                
                # Reproject to exact reference grid
                with rasterio.open(output_path, 'w', **profile) as dst:
                    for i in range(1, src.count + 1):
                        reproject(
                            source=rasterio.band(src, i),
                            destination=rasterio.band(dst, i),
                            src_transform=src.transform,
                            src_crs=src.crs,
                            dst_transform=reference_grid['transform'],
                            dst_crs=reference_grid['crs'],
                            resampling=Resampling.bilinear
                        )
                
                # Note: Data gaps will be handled by multi-tile downloading in the main process
                
                logger.info(f"Aligned {'elevation' if is_elevation else 'imagery'} to perfect grid")
                return output_path
                
        except Exception as e:
            logger.error(f"Error aligning to perfect grid: {str(e)}")
            return input_path
    
    def detect_missing_data_regions(self, file_path, is_elevation):
        """Detect regions with missing data that need additional downloads"""
        try:
            missing_regions = []
            
            with rasterio.open(file_path) as src:
                if is_elevation:
                    data = src.read(1)
                    nodata = src.nodata if src.nodata is not None else -9999
                    
                    # Enhanced missing data detection for elevation
                    missing_mask = self._create_comprehensive_elevation_mask(data, nodata)
                    
                else:
                    data = src.read()
                    # Use the enhanced imagery gap detection method
                    missing_mask = self._detect_imagery_gaps(data, src)
                
                # Find contiguous regions of missing data
                if np.any(missing_mask):
                    missing_regions = self._identify_missing_regions(missing_mask, src.transform, src.crs)
                    
                    total_pixels = missing_mask.size
                    missing_pixels = np.sum(missing_mask)
                    missing_percentage = (missing_pixels / total_pixels) * 100
                    
                    logger.info(f"Found {len(missing_regions)} regions with missing data ({missing_percentage:.2f}% of total)")
                    
                    # If we still have significant missing data, try alternative tiling strategy
                    if missing_percentage > 5.0:  # More than 5% missing
                        logger.info("Significant missing data detected, will try grid-based tiling approach")
                        grid_regions = self._create_grid_based_regions(src.bounds, src.crs)
                        missing_regions.extend(grid_regions)
                    
                return missing_regions
                
        except Exception as e:
            logger.error(f"Error detecting missing data regions: {str(e)}")
            return []
    
    def _create_comprehensive_elevation_mask(self, data, nodata_value):
        """Create comprehensive mask for all types of elevation data gaps"""
        # Start with basic no-data detection
        if nodata_value is None or (isinstance(nodata_value, float) and np.isnan(nodata_value)):
            missing_mask = np.isnan(data)
        else:
            missing_mask = (data == nodata_value) | np.isnan(data)
        
        # Common no-data values used by different elevation services
        common_nodata_values = [-9999, -32768, -32767, 9999, 32767, -3.4028235e+38]
        for nd_val in common_nodata_values:
            missing_mask |= (data == nd_val)
        
        # INTELLIGENT ZERO DETECTION: Check if zeros are likely no-data regions
        zero_mask = (data == 0)
        zero_count = np.sum(zero_mask)
        total_pixels = data.size
        zero_percentage = (zero_count / total_pixels) * 100
        
        # If >5% of pixels are zero, they're likely no-data regions (not valid sea level)
        if zero_percentage > 5.0:
            logger.info(f"Found {zero_percentage:.1f}% zero pixels - treating as no-data regions")
            missing_mask |= zero_mask
        elif zero_count > 0:
            # For smaller amounts of zeros, check if they form large contiguous regions
            # Large contiguous zero regions are likely no-data, scattered zeros might be valid
            try:
                from scipy import ndimage
                labeled_zeros, num_regions = ndimage.label(zero_mask)
                
                large_region_threshold = max(100, total_pixels * 0.01)  # 1% of total or 100 pixels
                for region_id in range(1, num_regions + 1):
                    region_mask = labeled_zeros == region_id
                    region_size = np.sum(region_mask)
                    
                    if region_size > large_region_threshold:
                        logger.info(f"Found large zero region ({region_size} pixels) - treating as no-data")
                        missing_mask |= region_mask
                        
            except ImportError:
                # Fallback without scipy: if we have substantial zeros, treat them as missing
                if zero_count > max(100, total_pixels * 0.02):  # 2% threshold without scipy
                    logger.info(f"Found {zero_count} zero pixels - treating as no-data (no scipy)")
                    missing_mask |= zero_mask
        
        # Check for unrealistic elevation values - be more conservative
        missing_mask |= (data < -1000)    # Well below Dead Sea level (-430m)
        missing_mask |= (data > 10000)    # Well above Everest (8849m)
        
        # Only check for suspicious flat regions in very large datasets and be more conservative
        if data.size > 10000:  # Only for large datasets
            unique_vals, counts = np.unique(data[~missing_mask], return_counts=True)
            if len(unique_vals) > 0:
                # Much more conservative threshold - only if value appears in >30% of pixels
                max_count_threshold = data.size * 0.3
                suspicious_values = unique_vals[counts > max_count_threshold]
                for sus_val in suspicious_values:
                    # Be very conservative - only mark clearly invalid values as suspicious
                    # Don't mark any values in reasonable elevation range as suspicious
                    if sus_val < -500 or sus_val > 8000:  # Only extreme values
                        missing_mask |= (data == sus_val)
        
        # Much more conservative outlier detection - only for extreme cases
        if np.any(~missing_mask):
            valid_data = data[~missing_mask]
            if len(valid_data) > 100:  # Need more data points for reliable statistics
                # Use wider percentile range for more conservative detection
                q1, q3 = np.percentile(valid_data, [5, 95])  # Use 5th/95th percentiles
                iqr = q3 - q1
                if iqr > 100:  # Only apply if there's significant variation (100m)
                    # Much more conservative outlier bounds
                    lower_bound = q1 - 10 * iqr  # Very conservative outlier detection
                    upper_bound = q3 + 10 * iqr
                    outliers = (data < lower_bound) | (data > upper_bound)
                    missing_mask |= outliers & ~missing_mask  # Only new outliers
        
        return missing_mask
    
    def _create_grid_based_regions(self, bounds, crs):
        """Create a grid of regions to ensure complete coverage"""
        # Convert bounds to WGS84 for consistent processing
        if crs != CRS.from_epsg(4326):
            transformer = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
            west, south = transformer.transform(bounds.left, bounds.bottom)
            east, north = transformer.transform(bounds.right, bounds.top)
        else:
            west, south, east, north = bounds.left, bounds.bottom, bounds.right, bounds.top
        
        # Create a 2x2 or 3x3 grid depending on area size
        width = east - west
        height = north - south
        
        # Determine grid size based on area
        if width > 0.1 or height > 0.1:  # Large area
            grid_size = 3
        else:
            grid_size = 2
        
        grid_regions = []
        step_x = width / grid_size
        step_y = height / grid_size
        
        for i in range(grid_size):
            for j in range(grid_size):
                region_west = west + i * step_x
                region_east = west + (i + 1) * step_x
                region_south = south + j * step_y
                region_north = south + (j + 1) * step_y
                
                # Add overlap between tiles
                overlap = min(step_x, step_y) * 0.1
                
                grid_regions.append({
                    'west': region_west - overlap,
                    'south': region_south - overlap,
                    'east': region_east + overlap,
                    'north': region_north + overlap,
                    'pixel_count': 1000  # Estimated
                })
        
        logger.info(f"Created {len(grid_regions)} grid-based regions for comprehensive coverage")
        return grid_regions
    
    def _final_elevation_validation(self, elevation_path, job_id):
        """Final validation and gap filling for elevation data"""
        try:
            # Check for any remaining gaps
            remaining_regions = self.detect_missing_data_regions(elevation_path, True)
            
            if not remaining_regions:
                logger.info("Final elevation validation: No gaps detected")
                return elevation_path
            
            # If small gaps remain (< 2% of data), apply interpolation as final fallback
            with rasterio.open(elevation_path) as src:
                data = src.read(1)
                missing_mask = self._create_comprehensive_elevation_mask(data, src.nodata)
                missing_percentage = (np.sum(missing_mask) / missing_mask.size) * 100
                
                if missing_percentage < 2.0:  # Small gaps only
                    logger.info(f"Applying final interpolation to {missing_percentage:.2f}% remaining gaps")
                    
                    # Create final output path
                    final_path = elevation_path.replace('.tif', '_final.tif')
                    
                    # Apply conservative interpolation
                    filled_data = self._conservative_gap_filling(data, missing_mask)
                    
                    # Write result
                    profile = src.profile.copy()
                    with rasterio.open(final_path, 'w', **profile) as dst:
                        dst.write(filled_data, 1)
                    
                    logger.info("Applied final interpolation to remaining small gaps")
                    return final_path
                else:
                    logger.warning(f"Still {missing_percentage:.2f}% gaps remaining - may need manual review")
                    return elevation_path
                    
        except Exception as e:
            logger.error(f"Error in final elevation validation: {e}")
            return elevation_path
    
    def _conservative_gap_filling(self, data, missing_mask):
        """Conservative gap filling for small remaining gaps"""
        filled_data = data.copy()
        
        if not np.any(missing_mask):
            return filled_data
        
        # Only fill small isolated gaps
        from scipy import ndimage
        
        try:
            # Label connected components
            labeled_gaps, num_gaps = ndimage.label(missing_mask)
            
            for gap_id in range(1, num_gaps + 1):
                gap_mask = labeled_gaps == gap_id
                gap_size = np.sum(gap_mask)
                
                # Only fill small gaps (< 100 pixels)
                if gap_size < 100:
                    gap_coords = np.column_stack(np.where(gap_mask))
                    
                    # Use nearby valid pixels for interpolation
                    for row, col in gap_coords:
                        # Look in expanding windows for valid data
                        for window_size in [3, 5, 7, 11]:
                            half_window = window_size // 2
                            row_start = max(0, row - half_window)
                            row_end = min(data.shape[0], row + half_window + 1)
                            col_start = max(0, col - half_window)
                            col_end = min(data.shape[1], col + half_window + 1)
                            
                            window_data = filled_data[row_start:row_end, col_start:col_end]
                            window_missing = missing_mask[row_start:row_end, col_start:col_end]
                            
                            valid_in_window = window_data[~window_missing]
                            
                            if len(valid_in_window) >= 3:
                                # Use median for robustness
                                filled_data[row, col] = np.median(valid_in_window)
                                break
                else:
                    logger.warning(f"Large gap of {gap_size} pixels remains unfilled")
                    
        except ImportError:
            # Fallback without scipy
            gap_coords = np.column_stack(np.where(missing_mask))
            for row, col in gap_coords:
                # Simple neighborhood average
                neighbors = []
                for dr in [-1, 0, 1]:
                    for dc in [-1, 0, 1]:
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = row + dr, col + dc
                        if (0 <= nr < data.shape[0] and 0 <= nc < data.shape[1] and 
                            not missing_mask[nr, nc]):
                            neighbors.append(filled_data[nr, nc])
                
                if neighbors:
                    filled_data[row, col] = np.median(neighbors)
        
        return filled_data
    
    def _identify_missing_regions(self, missing_mask, transform, crs):
        """Identify contiguous regions of missing data and convert to geographic bounds"""
        from scipy import ndimage
        
        try:
            # Label connected components of missing data
            labeled_array, num_features = ndimage.label(missing_mask)
            
            missing_regions = []
            
            for label in range(1, num_features + 1):
                # Find bounding box of this missing region
                region_mask = labeled_array == label
                rows, cols = np.where(region_mask)
                
                if len(rows) == 0:
                    continue
                    
                # Get min/max pixel coordinates
                min_row, max_row = rows.min(), rows.max()
                min_col, max_col = cols.min(), cols.max()
                
                # Convert pixel coordinates to geographic coordinates
                # Top-left corner
                x_min, y_max = rasterio.transform.xy(transform, min_row, min_col)
                # Bottom-right corner  
                x_max, y_min = rasterio.transform.xy(transform, max_row + 1, max_col + 1)
                
                # Convert to WGS84 if needed
                if crs != CRS.from_epsg(4326):
                    transformer = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
                    x_min, y_min = transformer.transform(x_min, y_min)
                    x_max, y_max = transformer.transform(x_max, y_max)
                
                # Add buffer to ensure overlap
                buffer = 0.001  # ~100m buffer
                region_bounds = {
                    'west': x_min - buffer,
                    'south': y_min - buffer, 
                    'east': x_max + buffer,
                    'north': y_max + buffer,
                    'pixel_count': len(rows)
                }
                
                missing_regions.append(region_bounds)
            
            # Sort by pixel count (largest gaps first)
            missing_regions.sort(key=lambda x: x['pixel_count'], reverse=True)
            
            return missing_regions
            
        except ImportError:
            logger.warning("SciPy not available for region identification, using simple bounding box")
            return self._simple_missing_region_detection(missing_mask, transform, crs)
        except Exception as e:
            logger.error(f"Error identifying missing regions: {e}")
            return []
    
    def _simple_missing_region_detection(self, missing_mask, transform, crs):
        """Simple fallback method for missing region detection without SciPy"""
        if not np.any(missing_mask):
            return []
        
        # Find overall bounding box of all missing data
        rows, cols = np.where(missing_mask)
        
        if len(rows) == 0:
            return []
        
        min_row, max_row = rows.min(), rows.max()
        min_col, max_col = cols.min(), cols.max()
        
        # Convert to geographic coordinates
        x_min, y_max = rasterio.transform.xy(transform, min_row, min_col)
        x_max, y_min = rasterio.transform.xy(transform, max_row + 1, max_col + 1)
        
        # Convert to WGS84 if needed
        if crs != CRS.from_epsg(4326):
            transformer = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
            x_min, y_min = transformer.transform(x_min, y_min)
            x_max, y_max = transformer.transform(x_max, y_max)
        
        buffer = 0.001
        return [{
            'west': x_min - buffer,
            'south': y_min - buffer,
            'east': x_max + buffer, 
            'north': y_max + buffer,
            'pixel_count': len(rows)
        }]
    
    def download_additional_tiles(self, missing_regions, job_id, is_elevation=True):
        """Download additional tiles to fill missing regions"""
        downloaded_tiles = []
        
        # Increase limit for elevation data since it's critical for complete DEMs
        max_tiles = 10 if is_elevation else 5
        
        for i, region in enumerate(missing_regions[:max_tiles]):
            try:
                logger.info(f"Downloading additional tile {i+1} for missing region")
                
                if is_elevation:
                    tile_path = self.download_elevation_data(region, f"{job_id}_tile_{i}")
                else:
                    tile_path = self.download_imagery_data(region, f"{job_id}_tile_{i}")
                
                if tile_path and os.path.exists(tile_path):
                    downloaded_tiles.append(tile_path)
                    logger.info(f"Successfully downloaded tile {i+1}")
                else:
                    logger.warning(f"Failed to download tile {i+1}")
                    
            except Exception as e:
                logger.warning(f"Error downloading tile {i+1}: {e}")
                continue
        
        return downloaded_tiles
    
    def merge_tiles_with_main(self, main_file, additional_tiles, output_path):
        """Merge additional tiles with the main file to fill gaps"""
        try:
            if not additional_tiles:
                # No additional tiles, just copy main file
                import shutil
                shutil.copy2(main_file, output_path)
                return output_path
            
            logger.info(f"Merging {len(additional_tiles)} additional tiles with main file")
            
            # Read main file
            with rasterio.open(main_file) as main_src:
                main_data = main_src.read()
                main_transform = main_src.transform
                main_crs = main_src.crs
                main_bounds = main_src.bounds
                
                # Create output with same properties as main
                profile = main_src.profile.copy()
                
                # Start with main data
                merged_data = main_data.copy()
                
                # Process each additional tile
                for tile_path in additional_tiles:
                    try:
                        with rasterio.open(tile_path) as tile_src:
                            # Reproject tile to match main file
                            tile_data_reprojected = np.empty_like(merged_data)
                            
                            reproject(
                                source=rasterio.band(tile_src, list(range(1, tile_src.count + 1))),
                                destination=tile_data_reprojected,
                                src_transform=tile_src.transform,
                                src_crs=tile_src.crs,
                                dst_transform=main_transform,
                                dst_crs=main_crs,
                                resampling=Resampling.bilinear
                            )
                            
                            # Merge: use tile data where main data is missing
                            if len(merged_data.shape) == 2 or merged_data.shape[0] == 1:
                                # Elevation data (single band)
                                main_band = merged_data[0] if len(merged_data.shape) == 3 else merged_data
                                tile_band = tile_data_reprojected[0] if len(tile_data_reprojected.shape) == 3 else tile_data_reprojected
                                
                                # Use comprehensive mask for elevation gaps
                                missing_mask = self._create_comprehensive_elevation_mask(main_band, main_src.nodata)
                                
                                # Create mask for valid tile data
                                valid_tile_mask = ~self._create_comprehensive_elevation_mask(tile_band, main_src.nodata)
                                
                                # Fill missing areas with tile data
                                fill_mask = missing_mask & valid_tile_mask
                                
                                if len(merged_data.shape) == 3:
                                    merged_data[0][fill_mask] = tile_band[fill_mask]
                                else:
                                    merged_data[fill_mask] = tile_band[fill_mask]
                                    
                                filled_pixels = np.sum(fill_mask)
                                logger.info(f"Filled {filled_pixels} pixels from tile")
                                
                                # Also try to improve existing data with better quality tile data
                                # Replace low-quality data (like zeros) with tile data
                                improvement_mask = (
                                    (main_band == 0) & valid_tile_mask & (tile_band > 0)
                                ) | (
                                    (main_band < -400) & valid_tile_mask & (tile_band > main_band)
                                )
                                
                                if np.any(improvement_mask):
                                    if len(merged_data.shape) == 3:
                                        merged_data[0][improvement_mask] = tile_band[improvement_mask]
                                    else:
                                        merged_data[improvement_mask] = tile_band[improvement_mask]
                                    improved_pixels = np.sum(improvement_mask)
                                    logger.info(f"Improved {improved_pixels} pixels with better tile data")
                                
                            else:
                                # Imagery data (multi-band)
                                for band_idx in range(min(merged_data.shape[0], tile_data_reprojected.shape[0])):
                                    main_band = merged_data[band_idx]
                                    tile_band = tile_data_reprojected[band_idx]
                                    
                                    # Missing data mask for imagery
                                    missing_mask = (main_band == 0) | np.isnan(main_band)
                                    valid_tile_mask = (tile_band > 0) & ~np.isnan(tile_band)
                                    fill_mask = missing_mask & valid_tile_mask
                                    
                                    merged_data[band_idx][fill_mask] = tile_band[fill_mask]
                                    
                    except Exception as e:
                        logger.warning(f"Error processing tile {tile_path}: {e}")
                        continue
                
                # Write merged result
                with rasterio.open(output_path, 'w', **profile) as dst:
                    dst.write(merged_data)
                
                logger.info(f"Successfully merged tiles into {output_path}")
                return output_path
                
        except Exception as e:
            logger.error(f"Error merging tiles: {e}")
            # Fallback: just copy main file
            import shutil
            shutil.copy2(main_file, output_path)
            return output_path
    
    # ============================================================================
    # LEGACY GAP-FILLING METHODS - Kept for backward compatibility
    # Primary approach is now multi-tile downloading for complete coverage
    # ============================================================================
    
    def _create_elevation_mask(self, data, nodata_value):
        """Create mask for elevation gaps"""
        if np.isnan(nodata_value):
            gaps = np.isnan(data)
        else:
            gaps = data == nodata_value
        
        gaps |= np.isnan(data)
        gaps |= (data < -1000) | (data > 10000)
        
        # Outlier detection
        if np.any(~gaps):
            valid_data = data[~gaps]
            if len(valid_data) > 10:
                q1, q3 = np.percentile(valid_data, [25, 75])
                iqr = q3 - q1
                if iqr > 0:
                    lower_bound = q1 - 3 * iqr
                    upper_bound = q3 + 3 * iqr
                    gaps |= (data < lower_bound) | (data > upper_bound)
        
        return gaps
    
    def _detect_imagery_gaps(self, data, dst_info):
        """Enhanced detection of imagery data gaps"""
        if dst_info.count < 3:  # Not RGB
            return np.zeros(data.shape[-2:], dtype=bool)
        
        # Multiple gap detection methods - be more conservative
        gap_mask = np.zeros(data.shape[-2:], dtype=bool)
        
        # Method 1: All bands zero (traditional)
        gap_mask |= np.all(data[:3] == 0, axis=0)
        
        # Method 2: All bands same value AND zero (often indicates no-data)
        if data.shape[0] >= 3:
            gap_mask |= (data[0] == data[1]) & (data[1] == data[2]) & (data[0] == 0)
        
        # Method 3: Only check for extremely unusual values - be very conservative
        # Remove the aggressive check for values < 5 or > 250 as these can be valid
        # Only check for specific no-data patterns
        
        # Method 4: Check for specific no-data values if metadata available
        if hasattr(dst_info, 'nodata') and dst_info.nodata is not None:
            for band in range(min(3, data.shape[0])):
                gap_mask |= (data[band] == dst_info.nodata)
        
        # Method 5: Check for common imagery no-data values
        common_nodata_values = [255, 0]  # Common no-data values for imagery
        for band in range(min(3, data.shape[0])):
            # Only mark as no-data if ALL bands have the same no-data value
            for nd_val in common_nodata_values:
                if nd_val == 0:  # For zero, we already check this above
                    continue
                # Only mark as missing if all RGB bands are the same no-data value
                if data.shape[0] >= 3:
                    all_bands_nodata = np.all(data[:3] == nd_val, axis=0)
                    gap_mask |= all_bands_nodata
        
        return gap_mask
    
    def _fill_imagery_gaps_intelligent(self, data, gap_mask):
        """Intelligent imagery gap filling using nearby pixel analysis"""
        filled_data = data.copy()
        
        if not np.any(gap_mask):
            return filled_data
        
        # For each band, fill gaps
        for band_idx in range(data.shape[0]):
            band_data = filled_data[band_idx]
            
            # Method 1: Try local neighborhood averaging first
            filled_band = self._fill_with_local_average(band_data, gap_mask)
            
            # Method 2: If still gaps, use distance-weighted interpolation
            remaining_gaps = gap_mask & (filled_band == band_data)
            if np.any(remaining_gaps):
                filled_band = self._fill_with_distance_weighting(filled_band, remaining_gaps)
            
            # Method 3: If still gaps, use earth-tone fallback with variation
            remaining_gaps = gap_mask & (filled_band == band_data)
            if np.any(remaining_gaps):
                filled_band = self._fill_with_earth_tones(filled_band, remaining_gaps, band_idx)
            
            filled_data[band_idx] = filled_band
        
        return filled_data
    
    def _fill_with_local_average(self, band_data, gap_mask, window_size=5):
        """Fill gaps using local neighborhood averaging"""
        filled_data = band_data.copy()
        
        # Create a kernel for neighborhood analysis
        half_window = window_size // 2
        
        gap_coords = np.column_stack(np.where(gap_mask))
        
        for row, col in gap_coords:
            # Define neighborhood bounds
            row_start = max(0, row - half_window)
            row_end = min(band_data.shape[0], row + half_window + 1)
            col_start = max(0, col - half_window)
            col_end = min(band_data.shape[1], col + half_window + 1)
            
            # Extract neighborhood
            neighborhood = band_data[row_start:row_end, col_start:col_end]
            neighborhood_mask = gap_mask[row_start:row_end, col_start:col_end]
            
            # Use valid pixels in neighborhood
            valid_pixels = neighborhood[~neighborhood_mask]
            
            if len(valid_pixels) > 0:
                # Use median for robustness
                filled_data[row, col] = np.median(valid_pixels)
        
        return filled_data
    
    def _fill_with_distance_weighting(self, band_data, gap_mask):
        """Fill remaining gaps using distance-weighted interpolation"""
        filled_data = band_data.copy()
        valid_mask = ~gap_mask
        
        if not np.any(valid_mask):
            return filled_data
        
        # Get coordinates
        valid_coords = np.column_stack(np.where(valid_mask))
        gap_coords = np.column_stack(np.where(gap_mask))
        valid_values = band_data[valid_mask]
        
        if len(valid_coords) == 0 or len(gap_coords) == 0:
            return filled_data
        
        # Limit processing for performance
        if len(gap_coords) > 5000:
            indices = np.random.choice(len(gap_coords), 5000, replace=False)
            gap_coords = gap_coords[indices]
        
        if len(valid_coords) > 1000:
            indices = np.random.choice(len(valid_coords), 1000, replace=False)
            valid_coords = valid_coords[indices]
            valid_values = valid_values[indices]
        
        # Calculate distances and weights
        distances = cdist(gap_coords, valid_coords)
        weights = 1.0 / (distances + 1e-10)
        weights = weights / weights.sum(axis=1, keepdims=True)
        
        # Interpolate
        interpolated_values = np.sum(weights * valid_values, axis=1)
        
        # Apply interpolated values
        for i, (row, col) in enumerate(gap_coords):
            if i < len(interpolated_values):
                filled_data[row, col] = interpolated_values[i]
        
        return filled_data
    
    def _fill_with_earth_tones(self, band_data, gap_mask, band_idx):
        """Fill remaining gaps with earth-tone colors with natural variation"""
        filled_data = band_data.copy()
        
        # Earth tone base values for RGB
        earth_tones = {
            0: 120,  # Red channel
            1: 150,  # Green channel  
            2: 100   # Blue channel
        }
        
        base_value = earth_tones.get(band_idx, 128)
        
        # Add natural variation
        gap_coords = np.column_stack(np.where(gap_mask))
        for row, col in gap_coords:
            # Add some spatial variation based on coordinates
            variation = int(15 * np.sin(row * 0.1) * np.cos(col * 0.1))
            filled_data[row, col] = np.clip(base_value + variation, 0, 255)
        
        return filled_data
    
    def interpolate_elevation_gaps(self, data, nodata_value):
        """Fill elevation gaps using enhanced interpolation methods"""
        filled_data = data.copy()
        
        # Use enhanced gap detection
        mask = ~self._create_elevation_mask(data, nodata_value)
        
        if np.any(~mask) and np.any(mask):
            # Try different interpolation methods in order of preference
            filled_data = self._try_bilinear_interpolation(filled_data, mask)
            
            # Check if gaps remain
            remaining_mask = ~self._create_elevation_mask(filled_data, nodata_value)
            if np.any(~remaining_mask):
                filled_data = self._try_distance_weighted_interpolation(filled_data, remaining_mask)
            
            # Final fallback: gradient-based filling
            remaining_mask = ~self._create_elevation_mask(filled_data, nodata_value)
            if np.any(~remaining_mask):
                filled_data = self._try_gradient_based_filling(filled_data, remaining_mask)
        
        return filled_data
    
    def _try_bilinear_interpolation(self, data, valid_mask):
        """Try bilinear interpolation for elevation gaps"""
        try:
            from scipy.interpolate import griddata
            filled_data = data.copy()
            
            if not np.any(valid_mask):
                return filled_data
            
            # Get valid coordinates and values
            valid_coords = np.column_stack(np.where(valid_mask))
            invalid_coords = np.column_stack(np.where(~valid_mask))
            valid_values = data[valid_mask]
            
            if len(valid_coords) < 4 or len(invalid_coords) == 0:
                return filled_data
            
            # Limit for performance
            if len(invalid_coords) > 50000:
                indices = np.random.choice(len(invalid_coords), 50000, replace=False)
                invalid_coords = invalid_coords[indices]
            
            if len(valid_coords) > 10000:
                indices = np.random.choice(len(valid_coords), 10000, replace=False)
                valid_coords = valid_coords[indices]
                valid_values = valid_values[indices]
            
            # Interpolate using linear method (bilinear)
            interpolated = griddata(
                valid_coords, valid_values, invalid_coords, 
                method='linear', fill_value=np.nan
            )
            
            # Apply interpolated values where successful
            for i, (row, col) in enumerate(invalid_coords):
                if i < len(interpolated) and not np.isnan(interpolated[i]):
                    filled_data[row, col] = interpolated[i]
            
            return filled_data
            
        except ImportError:
            logger.warning("SciPy not available for bilinear interpolation, using distance weighting")
            return data
        except Exception as e:
            logger.warning(f"Bilinear interpolation failed: {e}, using distance weighting")
            return data
    
    def _try_distance_weighted_interpolation(self, data, valid_mask):
        """Enhanced distance-weighted interpolation"""
        filled_data = data.copy()
        
        if not np.any(valid_mask):
            return filled_data
        
        valid_coords = np.column_stack(np.where(valid_mask))
        invalid_coords = np.column_stack(np.where(~valid_mask))
        valid_values = data[valid_mask]
        
        if len(valid_coords) == 0 or len(invalid_coords) == 0:
            return filled_data
        
        # Performance limits
        if len(invalid_coords) > 20000:
            indices = np.random.choice(len(invalid_coords), 20000, replace=False)
            invalid_coords = invalid_coords[indices]
        
        if len(valid_coords) > 2000:
            indices = np.random.choice(len(valid_coords), 2000, replace=False)
            valid_coords = valid_coords[indices]
            valid_values = valid_values[indices]
        
        # Calculate distances with improved weighting
        distances = cdist(invalid_coords, valid_coords)
        
        # Use inverse distance weighting with power of 2 for smoother results
        weights = 1.0 / (distances ** 2 + 1e-10)
        
        # Limit influence of very distant points
        max_distance = np.percentile(distances, 75)
        weights[distances > max_distance] *= 0.1
        
        weights = weights / weights.sum(axis=1, keepdims=True)
        
        # Interpolate values
        interpolated_values = np.sum(weights * valid_values, axis=1)
        
        # Apply with bounds checking
        for i, (row, col) in enumerate(invalid_coords):
            if i < len(interpolated_values):
                # Ensure reasonable elevation bounds
                value = interpolated_values[i]
                value = np.clip(value, -500, 9000)  # Reasonable elevation range
                filled_data[row, col] = value
        
        return filled_data
    
    def _try_gradient_based_filling(self, data, valid_mask):
        """Gradient-based gap filling as final fallback"""
        filled_data = data.copy()
        
        if not np.any(valid_mask):
            # If no valid data at all, create synthetic terrain
            return self._create_synthetic_elevation(data.shape)
        
        # For remaining gaps, use local gradient estimation
        invalid_coords = np.column_stack(np.where(~valid_mask))
        
        for row, col in invalid_coords:
            # Look for nearby valid pixels in expanding windows
            for window_size in [3, 5, 7, 11, 15]:
                half_window = window_size // 2
                row_start = max(0, row - half_window)
                row_end = min(data.shape[0], row + half_window + 1)
                col_start = max(0, col - half_window)
                col_end = min(data.shape[1], col + half_window + 1)
                
                window_data = filled_data[row_start:row_end, col_start:col_end]
                window_mask = valid_mask[row_start:row_end, col_start:col_end]
                
                if np.any(window_mask):
                    valid_values = window_data[window_mask]
                    if len(valid_values) > 0:
                        # Use mean with slight random variation for natural look
                        base_value = np.mean(valid_values)
                        variation = np.random.normal(0, np.std(valid_values) * 0.1)
                        filled_data[row, col] = base_value + variation
                        break
        
        return filled_data
    
    def _create_synthetic_elevation(self, shape):
        """Create synthetic elevation data when no valid data exists"""
        # Create a simple terrain model with some variation
        rows, cols = shape
        x = np.linspace(0, 1, cols)
        y = np.linspace(0, 1, rows)
        X, Y = np.meshgrid(x, y)
        
        # Generate synthetic terrain with multiple scales
        terrain = (
            100 * np.sin(X * 2 * np.pi) * np.cos(Y * 2 * np.pi) +
            50 * np.sin(X * 4 * np.pi) * np.cos(Y * 4 * np.pi) +
            25 * np.random.random((rows, cols))
        )
        
        # Ensure reasonable elevation range
        terrain = np.clip(terrain + 500, 0, 2000)
        
        logger.warning("Created synthetic elevation data due to complete data gaps")
        return terrain
    
    def _validate_data_coverage(self, bounds):
        """Validate expected data coverage and warn about potential issues"""
        warnings = []
        
        # Check if bounds are within expected coverage areas
        west, south, east, north = bounds['west'], bounds['south'], bounds['east'], bounds['north']
        
        # Check if within US boundaries (approximate)
        us_bounds = {'west': -180, 'east': -60, 'south': 15, 'north': 72}
        
        if not (us_bounds['west'] <= west <= us_bounds['east'] and 
                us_bounds['west'] <= east <= us_bounds['east'] and
                us_bounds['south'] <= south <= us_bounds['north'] and
                us_bounds['south'] <= north <= us_bounds['north']):
            warnings.append("Selected area is outside typical USGS data coverage. Expect significant data gaps.")
        
        # Check for very large areas that might have coverage issues
        area_deg_sq = (east - west) * (north - south)
        if area_deg_sq > 1.0:  # Roughly 100km x 100km at equator
            warnings.append("Large area selected. Some regions may have incomplete data coverage.")
        
        # Check for areas known to have limited coverage
        # Coastal areas, remote areas, etc.
        if west < -150 or east > -60:  # Alaska, Hawaii, territories
            warnings.append("Selected area includes regions with potentially limited data availability.")
        
        # Check for boundary edge cases
        if abs(west - east) < 0.001 or abs(north - south) < 0.001:
            warnings.append("Very small area selected. Data alignment may be challenging.")
        
        return {'warnings': warnings}
    
    def _handle_boundary_edge_cases(self, data, bounds_info=None):
        """Handle edge cases at data boundaries"""
        if data is None or data.size == 0:
            return data
        
        # Handle single-pixel or very small datasets
        if data.size < 4:
            logger.warning("Very small dataset detected, applying special handling")
            if data.size == 1:
                # Expand single pixel to 3x3 with slight variation
                value = data.flat[0] if not np.isnan(data.flat[0]) else 0
                expanded = np.full((3, 3), value, dtype=data.dtype)
                # Add slight variation for natural look
                noise = np.random.normal(0, abs(value) * 0.01, (3, 3))
                return expanded + noise.astype(data.dtype)
        
        # Handle edge boundary artifacts
        if len(data.shape) == 2:
            # Check for edge artifacts (common in reprojected data)
            edge_mask = np.zeros_like(data, dtype=bool)
            edge_mask[0, :] = True  # Top edge
            edge_mask[-1, :] = True  # Bottom edge
            edge_mask[:, 0] = True  # Left edge
            edge_mask[:, -1] = True  # Right edge
            
            # If edges have unusual values, interpolate from interior
            if data.size > 100:  # Only for reasonably sized datasets
                interior = data[2:-2, 2:-2]
                if interior.size > 0:
                    interior_mean = np.nanmean(interior)
                    interior_std = np.nanstd(interior)
                    
                    if interior_std > 0:
                        # Check for edge values that are outliers
                        edge_values = data[edge_mask]
                        outliers = np.abs(edge_values - interior_mean) > 3 * interior_std
                        
                        if np.any(outliers):
                            logger.info("Detected boundary artifacts, applying edge correction")
                            # Replace outlier edge values with interpolated values
                            edge_coords = np.column_stack(np.where(edge_mask))
                            outlier_coords = edge_coords[outliers]
                            
                            for row, col in outlier_coords:
                                # Use nearby interior values for interpolation
                                window_size = 3
                                row_start = max(0, row - window_size)
                                row_end = min(data.shape[0], row + window_size + 1)
                                col_start = max(0, col - window_size)
                                col_end = min(data.shape[1], col + window_size + 1)
                                
                                window = data[row_start:row_end, col_start:col_end]
                                window_edge_mask = edge_mask[row_start:row_end, col_start:col_end]
                                
                                # Use non-edge values in window
                                interior_window = window[~window_edge_mask]
                                if len(interior_window) > 0:
                                    data[row, col] = np.nanmean(interior_window)
        
        return data
    
    def verify_perfect_alignment(self, elevation_path, imagery_path):
        """Verify perfect alignment between elevation and imagery"""
        try:
            with rasterio.open(elevation_path) as elev, rasterio.open(imagery_path) as img:
                # Check all alignment criteria
                perfect_match = (
                    elev.width == img.width and 
                    elev.height == img.height and
                    elev.crs == img.crs and
                    elev.transform == img.transform
                )
                
                if perfect_match:
                    logger.info("✅ Perfect alignment verified")
                else:
                    logger.warning("⚠️ Alignment verification failed")
                
                return perfect_match
                
        except Exception as e:
            logger.error(f"Error verifying alignment: {str(e)}")
            return False
    
    def download_elevation_data(self, bounds, job_id):
        """Download elevation data from USGS 3DEP service - REAL DATA ONLY"""
        try:
            logger.info("Downloading elevation data from USGS 3DEP")
            
            # Multiple USGS 3DEP service URLs to try - expanded list
            service_urls = [
                "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage",
                "https://cloud.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage", 
                "https://services.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage",
                "https://elevation.nationalmap.gov/arcgis/rest/services/1DEP/ImageServer/exportImage",
                "https://services.nationalmap.gov/arcgis/rest/services/1DEP/ImageServer/exportImage"
            ]
            
            # Try multiple download strategies for better coverage
            return self._download_elevation_with_fallback_strategies(bounds, job_id, service_urls)
            
        except Exception as e:
            logger.error(f"Error downloading elevation data: {str(e)}")
            raise e
    
    def _download_elevation_with_fallback_strategies(self, bounds, job_id, service_urls):
        """Try multiple strategies to download complete elevation data"""
        
        # Strategy 1: Single large tile (original approach)
        logger.info("Strategy 1: Attempting single large tile download")
        result = self._try_single_elevation_download(bounds, job_id, service_urls)
        if result:
            return result
            
        # Strategy 2: Multiple overlapping tiles
        logger.info("Strategy 1 failed, trying Strategy 2: Multiple overlapping tiles")
        result = self._try_multi_tile_elevation_download(bounds, job_id, service_urls)
        if result:
            return result
            
        # Strategy 3: Grid-based approach with smaller tiles
        logger.info("Strategy 2 failed, trying Strategy 3: Grid-based smaller tiles")
        result = self._try_grid_elevation_download(bounds, job_id, service_urls)
        if result:
            return result
            
        # If all strategies fail
        raise Exception("All elevation download strategies failed")
    
    def _try_single_elevation_download(self, bounds, job_id, service_urls):
        """Try downloading elevation as a single tile"""
        # Use WGS84 coordinates directly for exact bounding box match
        params = {
            'bbox': f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}",
            'bboxSR': '4326',  # WGS84 for exact coordinate match
            'size': '2048,2048',  # Higher resolution
            'imageSR': '4326',  # Return in WGS84 to avoid coordinate expansion
            'format': 'tiff',
            'pixelType': 'F32',
            'interpolation': 'RSP_BilinearInterpolation',
            'compressionQuality': '100',  # Maximum quality
            'adjustAspectRatio': 'true',  # Maintain aspect ratio
            'f': 'image'
        }
            
        # Try each service URL
        for i, service_url in enumerate(service_urls):
            try:
                logger.info(f"Trying elevation service {i+1}/{len(service_urls)}")
                response = requests.get(service_url, params=params, timeout=120)
                response.raise_for_status()
                
                # Check if response is valid
                if response.headers.get('content-type', '').startswith('image/') or len(response.content) > 1000:
                    temp_path = os.path.join(self.base_dir, f"{job_id}_elevation_temp.tif")
                    with open(temp_path, 'wb') as f:
                        f.write(response.content)
                    
                    # Validate the downloaded file
                    try:
                        with rasterio.open(temp_path) as test_src:
                            if test_src.crs is not None and test_src.width > 0 and test_src.height > 0:
                                test_data = test_src.read(1, window=((0, min(10, test_src.height)), (0, min(10, test_src.width))))
                                logger.info(f"Successfully downloaded real elevation data: {test_src.width}x{test_src.height}")
                                return temp_path
                            else:
                                logger.warning(f"Service {i+1} returned invalid raster structure")
                                os.remove(temp_path)
                    except Exception as validation_error:
                        logger.warning(f"Service {i+1} validation failed: {validation_error}")
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                else:
                    logger.warning(f"Service {i+1} returned invalid response")
                    
            except requests.exceptions.RequestException as e:
                logger.warning(f"Service {i+1} request failed: {str(e)}")
                continue
        
        # Single tile download failed
        return None
    
    def _try_multi_tile_elevation_download(self, bounds, job_id, service_urls):
        """Try downloading elevation using multiple overlapping tiles"""
        try:
            # Create overlapping tiles to ensure complete coverage
            width = bounds['east'] - bounds['west']
            height = bounds['north'] - bounds['south']
            
            # Use 2x2 grid with 25% overlap
            overlap = 0.25
            step_x = width / 2
            step_y = height / 2
            
            tiles = []
            for i in range(2):
                for j in range(2):
                    tile_west = bounds['west'] + i * step_x - (overlap * step_x if i > 0 else 0)
                    tile_east = bounds['west'] + (i + 1) * step_x + (overlap * step_x if i < 1 else 0)
                    tile_south = bounds['south'] + j * step_y - (overlap * step_y if j > 0 else 0)
                    tile_north = bounds['south'] + (j + 1) * step_y + (overlap * step_y if j < 1 else 0)
                    
                    tile_bounds = {
                        'west': tile_west,
                        'east': tile_east,
                        'south': tile_south,
                        'north': tile_north
                    }
                    
                    tile_path = self._try_single_elevation_download(tile_bounds, f"{job_id}_tile_{i}_{j}", service_urls)
                    if tile_path:
                        tiles.append(tile_path)
            
            if len(tiles) >= 2:  # If we got at least 2 tiles
                # Merge the tiles
                merged_path = os.path.join(self.base_dir, f"{job_id}_elevation_merged.tif")
                self._merge_elevation_tiles(tiles, merged_path, bounds)
                
                # Clean up individual tiles
                for tile in tiles:
                    try:
                        if os.path.exists(tile):
                            os.remove(tile)
                    except:
                        pass
                
                logger.info(f"Successfully merged {len(tiles)} elevation tiles")
                return merged_path
            
        except Exception as e:
            logger.warning(f"Multi-tile elevation download failed: {e}")
        
        return None
    
    def _try_grid_elevation_download(self, bounds, job_id, service_urls):
        """Try downloading elevation using smaller grid tiles"""
        try:
            # Create 3x3 grid of smaller tiles
            width = bounds['east'] - bounds['west']
            height = bounds['north'] - bounds['south']
            
            step_x = width / 3
            step_y = height / 3
            overlap = 0.1  # 10% overlap
            
            tiles = []
            for i in range(3):
                for j in range(3):
                    tile_west = bounds['west'] + i * step_x - (overlap * step_x if i > 0 else 0)
                    tile_east = bounds['west'] + (i + 1) * step_x + (overlap * step_x if i < 2 else 0)
                    tile_south = bounds['south'] + j * step_y - (overlap * step_y if j > 0 else 0)
                    tile_north = bounds['south'] + (j + 1) * step_y + (overlap * step_y if j < 2 else 0)
                    
                    tile_bounds = {
                        'west': tile_west,
                        'east': tile_east,
                        'south': tile_south,
                        'north': tile_north
                    }
                    
                    tile_path = self._try_single_elevation_download(tile_bounds, f"{job_id}_grid_{i}_{j}", service_urls)
                    if tile_path:
                        tiles.append(tile_path)
            
            if len(tiles) >= 4:  # If we got at least 4 tiles
                # Merge the tiles
                merged_path = os.path.join(self.base_dir, f"{job_id}_elevation_grid_merged.tif")
                self._merge_elevation_tiles(tiles, merged_path, bounds)
                
                # Clean up individual tiles
                for tile in tiles:
                    try:
                        if os.path.exists(tile):
                            os.remove(tile)
                    except:
                        pass
                
                logger.info(f"Successfully merged {len(tiles)} grid elevation tiles")
                return merged_path
            
        except Exception as e:
            logger.warning(f"Grid elevation download failed: {e}")
        
        return None
    
    def _merge_elevation_tiles(self, tile_paths, output_path, target_bounds):
        """Merge multiple elevation tiles into a single file"""
        try:
            from rasterio.merge import merge
            from rasterio.warp import calculate_default_transform, reproject, Resampling
            
            # Open all tile files
            tile_datasets = []
            for tile_path in tile_paths:
                try:
                    tile_datasets.append(rasterio.open(tile_path))
                except:
                    continue
            
            if not tile_datasets:
                raise Exception("No valid tiles to merge")
            
            # Merge tiles
            mosaic, out_trans = merge(tile_datasets, bounds=None, resampling=Resampling.bilinear)
            
            # Get profile from first tile
            out_meta = tile_datasets[0].meta.copy()
            out_meta.update({
                "driver": "GTiff",
                "height": mosaic.shape[1],
                "width": mosaic.shape[2],
                "transform": out_trans,
                "compress": "lzw"
            })
            
            # Write merged result
            with rasterio.open(output_path, "w", **out_meta) as dest:
                dest.write(mosaic)
            
            # Close all datasets
            for ds in tile_datasets:
                ds.close()
            
            logger.info(f"Successfully merged {len(tile_datasets)} elevation tiles")
            return output_path
            
        except Exception as e:
            logger.error(f"Error merging elevation tiles: {e}")
            # Close any open datasets
            for ds in tile_datasets:
                try:
                    ds.close()
                except:
                    pass
            raise e
    
    def download_imagery_data(self, bounds, job_id):
        """Download imagery data from USGS NAIP service - REAL DATA ONLY"""
        try:
            logger.info("Downloading imagery data from USGS NAIP")
            
            # Multiple USGS imagery service URLs to try - more comprehensive list
            service_urls = [
                "https://services.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/exportImage",
                "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/exportImage",
                "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export",
                "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/export",
                "https://services.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/export",
                "https://carto.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export"
            ]
            
            # Use WGS84 coordinates directly for exact bounding box match
            # Try each service URL
            for i, service_url in enumerate(service_urls):
                try:
                    logger.info(f"Trying imagery service {i+1}/{len(service_urls)}")
                    
                    if "ImageServer" in service_url:
                        # ImageServer parameters - use WGS84 for exact bounds
                        params = {
                            'bbox': f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}",
                            'bboxSR': '4326',  # WGS84 for exact coordinate match
                            'size': '2048,2048',  # Higher resolution
                            'imageSR': '4326',  # Return in WGS84 to avoid coordinate expansion
                            'format': 'tiff',
                            'pixelType': 'U8',
                            'noData': '',
                            'interpolation': 'RSP_BilinearInterpolation',
                            'f': 'image'
                        }
                    else:
                        # MapServer parameters - use WGS84 for exact bounds
                        params = {
                            'bbox': f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}",
                            'bboxSR': '4326',  # WGS84 for exact coordinate match
                            'size': '2048,2048',  # Higher resolution
                            'imageSR': '4326',  # Return in WGS84 to avoid coordinate expansion
                            'format': 'tiff',
                            'f': 'image'
                        }
                    
                    # Try with longer timeout and user agent for imagery services
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                    response = requests.get(service_url, params=params, timeout=180, headers=headers)
                    response.raise_for_status()
                    
                    # Check if response is valid - be more lenient for imagery
                    content_type = response.headers.get('content-type', '').lower()
                    is_valid_response = (
                        content_type.startswith('image/') or 
                        content_type.startswith('application/') or
                        len(response.content) > 1000
                    )
                    
                    if is_valid_response:
                        temp_path = os.path.join(self.base_dir, f"{job_id}_imagery_temp.tif")
                        with open(temp_path, 'wb') as f:
                            f.write(response.content)
                        
                        # Validate the downloaded file - more flexible validation
                        try:
                            with rasterio.open(temp_path) as test_src:
                                if test_src.width > 0 and test_src.height > 0:
                                    # Try to read a small sample
                                    test_data = test_src.read(1, window=((0, min(10, test_src.height)), (0, min(10, test_src.width))))
                                    logger.info(f"Successfully downloaded real imagery data: {test_src.width}x{test_src.height}")
                                    return temp_path
                                else:
                                    logger.warning(f"Service {i+1} returned invalid raster structure")
                                    os.remove(temp_path)
                        except Exception as validation_error:
                            logger.warning(f"Service {i+1} validation failed: {validation_error}")
                            # Try to fix georeferencing and validate again
                            try:
                                fixed_path = self.add_proper_georeferencing(temp_path, bounds)
                                if fixed_path != temp_path:
                                    with rasterio.open(fixed_path) as test_src:
                                        if test_src.width > 0 and test_src.height > 0:
                                            logger.info(f"Fixed and validated imagery data: {test_src.width}x{test_src.height}")
                                            os.remove(temp_path)
                                            return fixed_path
                            except:
                                pass
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                    else:
                        logger.warning(f"Service {i+1} returned invalid response (content-type: {content_type}, size: {len(response.content)})")
                        
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Service {i+1} request failed: {str(e)}")
                    continue
            
            # Try alternative imagery sources as last resort
            logger.info("USGS imagery services failed, trying alternative sources...")
            
            try:
                return self.download_alternative_imagery(bounds, job_id)
            except Exception as alt_error:
                logger.error(f"Alternative imagery sources also failed: {alt_error}")
                raise Exception("All imagery services (USGS and alternatives) are unavailable. Please try again later.")
            
        except Exception as e:
            logger.error(f"Error downloading imagery data: {str(e)}")
            raise e
    
    def download_alternative_imagery(self, bounds, job_id):
        """Download imagery from alternative sources when USGS fails"""
        try:
            logger.info("Trying alternative imagery sources")
            
            # Use ESRI World Imagery as alternative
            service_url = "https://server.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export"
            
            # Use WGS84 coordinates directly for exact bounding box match
            params = {
                'bbox': f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}",
                'bboxSR': '4326',  # WGS84 for exact coordinate match
                'size': '2048,2048',
                'imageSR': '4326',  # Return in WGS84 to avoid coordinate expansion
                'format': 'tiff',
                'f': 'image'
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(service_url, params=params, timeout=180, headers=headers)
            response.raise_for_status()
            
            if len(response.content) > 1000:
                temp_path = os.path.join(self.base_dir, f"{job_id}_imagery_temp.tif")
                with open(temp_path, 'wb') as f:
                    f.write(response.content)
                
                # Validate and fix if needed
                try:
                    with rasterio.open(temp_path) as test_src:
                        if test_src.width > 0 and test_src.height > 0:
                            logger.info(f"Successfully downloaded alternative imagery: {test_src.width}x{test_src.height}")
                            return temp_path
                except:
                    # Try to fix georeferencing
                    fixed_path = self.add_proper_georeferencing(temp_path, bounds)
                    if fixed_path != temp_path:
                        logger.info("Fixed alternative imagery georeferencing")
                        os.remove(temp_path)
                        return fixed_path
                    else:
                        os.remove(temp_path)
            
            raise Exception("Alternative imagery source validation failed")
            
        except Exception as e:
            logger.error(f"Alternative imagery download failed: {str(e)}")
            raise e
    
    def add_proper_georeferencing(self, input_path, bounds):
        """Add proper georeferencing to downloaded files if missing"""
        try:
            with rasterio.open(input_path) as src:
                # Check if georeferencing is missing or invalid
                if src.crs is None or src.transform == rasterio.Affine.identity():
                    logger.info("Adding proper georeferencing to downloaded file")
                    
                    # Create properly georeferenced version
                    fixed_path = input_path.replace('.tif', '_georef.tif')
                    
                    # Calculate proper transform from bounds
                    transform = rasterio.transform.from_bounds(
                        bounds['west'], bounds['south'], bounds['east'], bounds['north'], 
                        src.width, src.height
                    )
                    
                    # Copy data with proper georeferencing
                    kwargs = src.meta.copy()
                    kwargs.update({
                        'crs': 'EPSG:4326',
                        'transform': transform
                    })
                    
                    with rasterio.open(fixed_path, 'w', **kwargs) as dst:
                        for i in range(1, src.count + 1):
                            dst.write(src.read(i), i)
                    
                    return fixed_path
                else:
                    return input_path
                    
        except Exception as e:
            logger.error(f"Error adding georeferencing: {str(e)}")
            return input_path
    
    def process_terrain_request(self, bounds, job_id, include_elevation=True, include_imagery=True):
        """Main function to process terrain data request with perfect alignment"""
        try:
            download_progress[job_id] = {
                'status': 'starting',
                'progress': 0,
                'start_time': datetime.now().isoformat(),
                'error': None,
                'warnings': []
            }
            
            # Pre-validate data coverage
            download_progress[job_id]['status'] = 'validating_coverage'
            coverage_info = self._validate_data_coverage(bounds)
            if coverage_info['warnings']:
                download_progress[job_id]['warnings'].extend(coverage_info['warnings'])
            
            # Calculate UTM zone
            center_lon = (bounds['west'] + bounds['east']) / 2
            center_lat = (bounds['south'] + bounds['north']) / 2
            utm_crs = self.get_utm_crs(center_lon, center_lat)
            
            download_progress[job_id]['utm_zone'] = utm_crs
            download_progress[job_id]['progress'] = 10
            
            # Create perfect reference grid
            download_progress[job_id]['status'] = 'creating_reference_grid'
            reference_grid = self.create_perfect_reference_grid(bounds, utm_crs)
            if not reference_grid:
                raise Exception("Failed to create reference grid")
            
            download_progress[job_id]['progress'] = 20
            
            files_created = []
            temp_files = []
            
            # Process elevation data
            if include_elevation:
                download_progress[job_id]['status'] = 'downloading_elevation'
                download_progress[job_id]['progress'] = 30
                
                try:
                    elevation_temp = self.download_elevation_data(bounds, job_id)
                    temp_files.append(elevation_temp)
                    
                    # Add proper georeferencing if needed
                    elevation_georef = self.add_proper_georeferencing(elevation_temp, bounds)
                    if elevation_georef != elevation_temp:
                        temp_files.append(elevation_georef)
                    
                    # Align to perfect grid
                    elevation_aligned = self.align_to_perfect_grid(elevation_georef, reference_grid, True)
                    if elevation_aligned:
                        # Check for missing data regions
                        missing_regions = self.detect_missing_data_regions(elevation_aligned, True)
                        
                        if missing_regions:
                            logger.info(f"Found {len(missing_regions)} missing elevation regions, downloading additional tiles")
                            download_progress[job_id]['status'] = 'downloading_additional_elevation_tiles'
                            
                            # Download additional tiles
                            additional_tiles = self.download_additional_tiles(missing_regions, job_id, True)
                            
                            if additional_tiles:
                                # Merge tiles to create complete DEM
                                complete_elevation_path = os.path.join(self.base_dir, f"{job_id}_elevation_complete.tif")
                                elevation_complete = self.merge_tiles_with_main(elevation_aligned, additional_tiles, complete_elevation_path)
                                
                                # Final validation and gap filling if needed
                                final_elevation_path = self._final_elevation_validation(elevation_complete, job_id)
                                
                                # Clean up individual tiles
                                for tile in additional_tiles:
                                    try:
                                        if os.path.exists(tile):
                                            os.remove(tile)
                                    except:
                                        pass
                                
                                files_created.append(('elevation', final_elevation_path))
                                logger.info("Successfully created complete elevation DEM")
                            else:
                                files_created.append(('elevation', elevation_aligned))
                                download_progress[job_id]['warnings'].append("Could not download additional elevation tiles to fill gaps")
                        else:
                            files_created.append(('elevation', elevation_aligned))
                        
                except Exception as e:
                    download_progress[job_id]['error'] = f"Elevation download failed: {str(e)}"
                    logger.error(f"Elevation processing failed: {str(e)}")
                
                download_progress[job_id]['progress'] = 50
            
            # Process imagery data
            if include_imagery:
                download_progress[job_id]['status'] = 'downloading_imagery'
                download_progress[job_id]['progress'] = 60
                
                try:
                    imagery_temp = self.download_imagery_data(bounds, job_id)
                    temp_files.append(imagery_temp)
                    
                    # Add proper georeferencing if needed
                    imagery_georef = self.add_proper_georeferencing(imagery_temp, bounds)
                    if imagery_georef != imagery_temp:
                        temp_files.append(imagery_georef)
                    
                    # Align to perfect grid
                    imagery_aligned = self.align_to_perfect_grid(imagery_georef, reference_grid, False)
                    if imagery_aligned:
                        # Check for missing data regions
                        missing_regions = self.detect_missing_data_regions(imagery_aligned, False)
                        
                        if missing_regions:
                            logger.info(f"Found {len(missing_regions)} missing imagery regions, downloading additional tiles")
                            download_progress[job_id]['status'] = 'downloading_additional_imagery_tiles'
                            
                            # Download additional tiles
                            additional_tiles = self.download_additional_tiles(missing_regions, job_id, False)
                            
                            if additional_tiles:
                                # Merge tiles to create complete imagery
                                complete_imagery_path = os.path.join(self.base_dir, f"{job_id}_imagery_complete.tif")
                                imagery_complete = self.merge_tiles_with_main(imagery_aligned, additional_tiles, complete_imagery_path)
                                
                                # Clean up individual tiles
                                for tile in additional_tiles:
                                    try:
                                        if os.path.exists(tile):
                                            os.remove(tile)
                                    except:
                                        pass
                                
                                files_created.append(('imagery', imagery_complete))
                                logger.info("Successfully created complete imagery")
                            else:
                                files_created.append(('imagery', imagery_aligned))
                                download_progress[job_id]['warnings'].append("Could not download additional imagery tiles to fill gaps")
                        else:
                            files_created.append(('imagery', imagery_aligned))
                        
                except Exception as e:
                    download_progress[job_id]['warnings'].append(f"Imagery download failed: {str(e)}")
                    logger.error(f"Imagery processing failed: {str(e)}")
                    # Continue processing - don't fail the entire job for imagery issues
                
                download_progress[job_id]['progress'] = 80
            
            # Verify perfect alignment
            if len(files_created) == 2:
                download_progress[job_id]['status'] = 'verifying_alignment'
                elevation_file = next(f[1] for f in files_created if f[0] == 'elevation')
                imagery_file = next(f[1] for f in files_created if f[0] == 'imagery')
                
                is_perfect = self.verify_perfect_alignment(elevation_file, imagery_file)
                if is_perfect:
                    download_progress[job_id]['warnings'].append(
                        "Perfect pixel-to-pixel alignment achieved!"
                    )
                else:
                    download_progress[job_id]['warnings'].append(
                        "Alignment verification failed"
                    )
            
            # Create final package
            if files_created:
                download_progress[job_id]['status'] = 'creating_package'
                download_progress[job_id]['progress'] = 90
                
                zip_path = os.path.join(self.base_dir, f"{job_id}_terrain_data.zip")
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    for file_type, file_path in files_created:
                        zipf.write(file_path, f"{file_type}_{job_id}.tif")
                
                download_progress[job_id]['status'] = 'completed'
                download_progress[job_id]['progress'] = 100
                download_progress[job_id]['download_url'] = f"/download/{job_id}"
                download_progress[job_id]['files'] = [f[0] for f in files_created]
                
                # Cleanup temp files
                for temp_file in temp_files:
                    try:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                    except:
                        pass
                
                logger.info(f"Perfect alignment processing completed for job {job_id}")
            else:
                download_progress[job_id]['status'] = 'failed'
                download_progress[job_id]['error'] = 'No data files were successfully created'
                
        except Exception as e:
            logger.error(f"Error in terrain processing: {str(e)}")
            download_progress[job_id]['status'] = 'failed'
            download_progress[job_id]['error'] = str(e)

# Initialize downloader
downloader = TerrainDownloader()

# Flask routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/download', methods=['POST'])
def start_download():
    try:
        data = request.get_json()
        required_fields = ['west', 'south', 'east', 'north']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required boundary coordinates'}), 400
        
        bounds = {
            'west': float(data['west']),
            'south': float(data['south']),
            'east': float(data['east']),
            'north': float(data['north'])
        }
        
        include_elevation = data.get('include_elevation', True)
        include_imagery = data.get('include_imagery', True)
        job_id = str(uuid.uuid4())
        
        thread = threading.Thread(
            target=downloader.process_terrain_request,
            args=(bounds, job_id, include_elevation, include_imagery)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({'job_id': job_id, 'status': 'started'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/status/<job_id>')
def get_status(job_id):
    if job_id in download_progress:
        return jsonify(download_progress[job_id])
    else:
        return jsonify({'error': 'Job not found'}), 404

@app.route('/download/<job_id>')
def download_file(job_id):
    try:
        if job_id not in download_progress or download_progress[job_id]['status'] != 'completed':
            return jsonify({'error': 'Download not ready'}), 400
        
        zip_path = os.path.join(downloader.base_dir, f"{job_id}_terrain_data.zip")
        if not os.path.exists(zip_path):
            return jsonify({'error': 'Download file not found'}), 404
        
        return send_file(zip_path, as_attachment=True, 
                        download_name=f"terrain_data_{job_id}.zip")
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
