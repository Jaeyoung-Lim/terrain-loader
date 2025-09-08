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
            
            width = max(256, min(4096, int(utm_width / self.pixel_size)))
            height = max(256, min(4096, int(utm_height / self.pixel_size)))
            
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
                
                # Fill any data gaps to ensure complete coverage
                self.fill_data_gaps_inplace(output_path, is_elevation)
                
                logger.info(f"Aligned {'elevation' if is_elevation else 'imagery'} to perfect grid")
                return output_path
                
        except Exception as e:
            logger.error(f"Error aligning to perfect grid: {str(e)}")
            return input_path
    
    def fill_data_gaps_inplace(self, file_path, is_elevation):
        """Fill any data gaps in the aligned file"""
        try:
            with rasterio.open(file_path, 'r+') as dst:
                if is_elevation:
                    data = dst.read(1)
                    nodata = dst.nodata if dst.nodata is not None else -9999
                    
                    # Check for gaps
                    has_gaps = np.any(data == nodata) or np.any(np.isnan(data))
                    
                    if has_gaps:
                        # Fill with interpolated values
                        filled_data = self.interpolate_elevation_gaps(data, nodata)
                        dst.write(filled_data, 1)
                        logger.info("Filled elevation data gaps")
                else:
                    data = dst.read()
                    
                    # Check for gaps in imagery (all bands zero/nodata)
                    if dst.count >= 3:  # RGB
                        mask = np.all(data == 0, axis=0)
                        if np.any(mask):
                            # Fill with earth-tone colors
                            data[0][mask] = 120  # Red
                            data[1][mask] = 150  # Green
                            data[2][mask] = 100  # Blue
                            dst.write(data)
                            logger.info("Filled imagery data gaps")
                            
        except Exception as e:
            logger.error(f"Error filling data gaps: {str(e)}")
    
    def interpolate_elevation_gaps(self, data, nodata_value):
        """Fill elevation gaps using distance-weighted interpolation"""
        filled_data = data.copy()
        
        # Create mask for valid data
        if np.isnan(nodata_value):
            mask = ~np.isnan(filled_data)
        else:
            mask = filled_data != nodata_value
        
        if np.any(~mask) and np.any(mask):
            # Get coordinates of valid and invalid pixels
            valid_coords = np.column_stack(np.where(mask))
            invalid_coords = np.column_stack(np.where(~mask))
            
            if len(valid_coords) > 0 and len(invalid_coords) > 0:
                # For large datasets, sample to avoid memory issues
                if len(invalid_coords) > 10000:
                    indices = np.random.choice(len(invalid_coords), 10000, replace=False)
                    invalid_coords = invalid_coords[indices]
                
                if len(valid_coords) > 1000:
                    indices = np.random.choice(len(valid_coords), 1000, replace=False)
                    valid_coords_sample = valid_coords[indices]
                    valid_values_sample = filled_data[mask][indices]
                else:
                    valid_coords_sample = valid_coords
                    valid_values_sample = filled_data[mask]
                
                # Calculate distances and interpolate
                distances = cdist(invalid_coords, valid_coords_sample)
                weights = 1.0 / (distances + 1e-10)
                weights = weights / weights.sum(axis=1, keepdims=True)
                
                # Interpolate values
                interpolated_values = np.sum(weights * valid_values_sample, axis=1)
                
                # Fill gaps
                for i, (row, col) in enumerate(invalid_coords):
                    if i < len(interpolated_values):
                        filled_data[row, col] = interpolated_values[i]
        
        return filled_data
    
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
            
            # Multiple USGS 3DEP service URLs to try
            service_urls = [
                "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage",
                "https://cloud.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage",
                "https://services.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
            ]
            
            # Convert bounds to Web Mercator for the service
            transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
            xmin, ymin = transformer.transform(bounds['west'], bounds['south'])
            xmax, ymax = transformer.transform(bounds['east'], bounds['north'])
            
            params = {
                'bbox': f"{xmin},{ymin},{xmax},{ymax}",
                'bboxSR': '3857',
                'size': '2048,2048',  # Higher resolution
                'imageSR': '3857',
                'format': 'tiff',
                'pixelType': 'F32',
                'noDataInterpretation': 'esriNoDataMatchAny',
                'interpolation': 'RSP_BilinearInterpolation',
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
            
            # If all services failed, raise an error - NO SYNTHETIC DATA
            raise Exception("All USGS elevation services are unavailable. Please try again later.")
            
        except Exception as e:
            logger.error(f"Error downloading elevation data: {str(e)}")
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
            
            # Convert bounds to Web Mercator
            transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
            xmin, ymin = transformer.transform(bounds['west'], bounds['south'])
            xmax, ymax = transformer.transform(bounds['east'], bounds['north'])
            
            # Try each service URL
            for i, service_url in enumerate(service_urls):
                try:
                    logger.info(f"Trying imagery service {i+1}/{len(service_urls)}")
                    
                    if "ImageServer" in service_url:
                        # ImageServer parameters
                        params = {
                            'bbox': f"{xmin},{ymin},{xmax},{ymax}",
                            'bboxSR': '3857',
                            'size': '2048,2048',  # Higher resolution
                            'imageSR': '3857',
                            'format': 'tiff',
                            'pixelType': 'U8',
                            'noData': '',
                            'interpolation': 'RSP_BilinearInterpolation',
                            'f': 'image'
                        }
                    else:
                        # MapServer parameters
                        params = {
                            'bbox': f"{xmin},{ymin},{xmax},{ymax}",
                            'bboxSR': '3857',
                            'size': '2048,2048',  # Higher resolution
                            'imageSR': '3857',
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
            
            # Convert bounds to Web Mercator
            transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
            xmin, ymin = transformer.transform(bounds['west'], bounds['south'])
            xmax, ymax = transformer.transform(bounds['east'], bounds['north'])
            
            params = {
                'bbox': f"{xmin},{ymin},{xmax},{ymax}",
                'bboxSR': '3857',
                'size': '2048,2048',
                'imageSR': '3857',
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
