# Terrain Data Downloader

A web-based application for downloading terrain elevation and orthoimagery data with automatic UTM projection conversion.

## Features

- **Interactive Web Interface**: Select download areas using an intuitive map interface
- **Multiple Data Sources**: 
  - Elevation data from USGS 3DEP (3D Elevation Program)
  - Orthoimagery from USGS NAIP (National Agriculture Imagery Program)
- **UTM Projection**: Automatically reprojects data to the appropriate UTM zone based on the selected area
- **GeoTIFF Output**: Saves data as industry-standard GeoTIFF files
- **Progress Tracking**: Real-time download progress monitoring
- **Batch Download**: Downloads both elevation and imagery data in a single ZIP file

## Installation

### Prerequisites

- Python 3.8 or higher
- GDAL library (required by rasterio)

### Install GDAL

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install gdal-bin libgdal-dev
```

**macOS:**
```bash
brew install gdal
```

**Windows:**
Download and install from https://www.lfd.uci.edu/~gohlke/pythonlibs/#gdal

### Install Python Dependencies

```bash
pip install -r requirements.txt
```

## Usage

### Starting the Server

```bash
python app.py
```

The application will start on `http://localhost:5000`

### Using the Web Interface

1. **Open your browser** and navigate to `http://localhost:5000`

2. **Select an area** on the map:
   - Use the rectangle tool (□) in the top-right corner of the map
   - Click and drag to draw a rectangle around your area of interest
   - The coordinates will automatically update in the control panel

3. **Choose data types**:
   - ✅ **Elevation Data (DEM)**: Digital elevation model from USGS 3DEP
   - ✅ **Orthoimagery**: High-resolution aerial photography from USGS NAIP

4. **Download**:
   - Click "Download Terrain Data"
   - Monitor the progress in real-time
   - Download the ZIP file when completed

### Output Files

The downloaded ZIP file contains:
- `elevation_[job_id].tif`: Digital elevation model in UTM projection
- `imagery_[job_id].tif`: Orthoimagery in UTM projection

Both files are in GeoTIFF format with:
- **Coordinate System**: UTM (automatically determined from area center)
- **Units**: Meters
- **Resolution**: Identical dimensions and 10-meter pixel resolution
- **Alignment**: Perfect spatial alignment between elevation and imagery
- **Elevation**: Meters above sea level (for DEM)
- **Imagery**: RGB values (for orthoimagery)

## API Endpoints

### Start Download
```
POST /api/download
Content-Type: application/json

{
    "west": -105.0,
    "south": 39.0,
    "east": -104.0,
    "north": 40.0,
    "include_elevation": true,
    "include_imagery": true
}
```

### Check Status
```
GET /api/status/{job_id}
```

### Download File
```
GET /download/{job_id}
```

### Cleanup Job
```
DELETE /api/cleanup/{job_id}
```

## Data Sources

### Elevation Data (USGS 3DEP)
- **Source**: USGS 3D Elevation Program
- **Resolution**: Varies by location (typically 1/3 arc-second ~10m)
- **Coverage**: United States
- **Format**: 32-bit floating point elevation values in meters

### Orthoimagery (USGS NAIP)
- **Source**: USGS National Agriculture Imagery Program
- **Resolution**: 1-meter ground sample distance
- **Coverage**: United States (updated every 2-3 years)
- **Format**: 8-bit RGB imagery

## UTM Projection

The application automatically determines the appropriate UTM zone based on the center point of the selected area:

- **UTM Zones**: Calculated from longitude (6° wide zones)
- **Hemisphere**: Determined from latitude
- **EPSG Codes**: 
  - Northern Hemisphere: EPSG:326XX (XX = zone number)
  - Southern Hemisphere: EPSG:327XX (XX = zone number)

## Resolution Alignment

The application ensures that elevation and imagery data have identical spatial properties:

### **Automatic Alignment Process**
1. **Download**: Elevation and imagery data are downloaded at high resolution (2048x2048 pixels)
2. **Reprojection**: Both datasets are reprojected to the appropriate UTM coordinate system
3. **Alignment**: Datasets are resampled to have identical:
   - Pixel dimensions (width × height)
   - Pixel resolution (10-meter ground sample distance)
   - Spatial bounds (exact coordinate coverage)
   - Coordinate reference system

### **Benefits**
- **Perfect Overlay**: Elevation and imagery align pixel-for-pixel
- **Consistent Analysis**: Same resolution enables direct comparison
- **GIS Ready**: Files can be used together in any GIS software
- **Optimal Quality**: 10-meter resolution balances detail with file size

## Troubleshooting

### Common Issues

1. **GDAL Installation Problems**:
   ```bash
   # Check GDAL installation
   gdalinfo --version
   
   # If missing, reinstall GDAL before installing Python packages
   pip uninstall rasterio
   # Install GDAL system package first, then:
   pip install rasterio
   ```

2. **Memory Issues with Large Areas**:
   - Limit selection to reasonable sizes (< 1000 km²)
   - Large downloads may take several minutes

3. **Network Timeouts**:
   - Check internet connection
   - USGS services may be temporarily unavailable
   - Try again after a few minutes

4. **No Data Available**:
   - Some areas may not have high-resolution data
   - Check USGS data availability for your area of interest

5. **GeoTIFF Reprojection Errors**:
   - The application now automatically fixes files lacking proper georeferencing
   - If you see "Unable to compute transformation" errors, the app will:
     - Validate downloaded files
     - Add missing coordinate reference information
     - Generate synthetic data as fallback if needed
   - No user intervention required - errors are handled automatically

### File Structure
```
terrain-loader/
├── app.py              # Main Flask application
├── requirements.txt    # Python dependencies
├── README.md          # This file
├── templates/
│   └── index.html     # Web interface
└── downloads/         # Downloaded files (created automatically)
```

## Development

### Adding New Data Sources

To add new data sources, modify the `TerrainDownloader` class in `app.py`:

1. Add new download method (e.g., `download_custom_data`)
2. Update the processing pipeline in `process_terrain_request`
3. Add corresponding UI controls in `templates/index.html`

### Customizing Projections

To support additional coordinate systems:
1. Modify `get_utm_crs` method for custom CRS selection
2. Update `reproject_to_utm` to handle different target projections

## License

This project is open source. Data sources are provided by USGS and are in the public domain.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Support

For issues and questions:
1. Check the troubleshooting section
2. Review USGS data availability
3. Open an issue on the project repository
