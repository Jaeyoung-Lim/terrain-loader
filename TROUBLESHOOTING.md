# Troubleshooting Guide

## Network Connectivity Issues

If you're experiencing network connectivity problems (like the "Name or service not known" error), here are the solutions:

### ✅ **Enhanced Application Features**

The application now includes several improvements to handle network issues:

1. **Multiple Service URLs**: Tries different USGS service endpoints automatically
2. **Network Connectivity Check**: Tests connection before attempting downloads
3. **Synthetic Data Fallback**: Generates realistic synthetic data when services are unavailable
4. **User Notifications**: Clear warnings when using fallback data

### 🔧 **Manual Network Troubleshooting**

#### Check DNS Resolution
```bash
# Test if you can resolve USGS domains
nslookup elevation.nationalmap.gov
nslookup services.nationalmap.gov

# If DNS fails, try using a different DNS server
# Add to /etc/resolv.conf:
nameserver 8.8.8.8
nameserver 8.8.4.4
```

#### Test Network Connectivity
```bash
# Test basic connectivity
ping google.com
ping elevation.nationalmap.gov

# Test HTTPS connectivity
curl -I https://elevation.nationalmap.gov
curl -I https://services.nationalmap.gov
```

#### Proxy/Firewall Issues
```bash
# If behind a corporate firewall, set proxy
export HTTP_PROXY=http://your-proxy:port
export HTTPS_PROXY=https://your-proxy:port

# Or configure in Python
# Add to app.py:
import os
proxies = {
    'http': os.environ.get('HTTP_PROXY'),
    'https': os.environ.get('HTTPS_PROXY')
}
# Then add proxies=proxies to requests.get() calls
```

### 🎯 **Application Behavior with Network Issues**

#### When Network is Available:
- ✅ Downloads real USGS elevation data
- ✅ Downloads real USGS orthoimagery
- ✅ Shows "USGS services reachable" status

#### When Network is Unavailable:
- ⚠️ Shows warning: "Network issues detected - will use synthetic data"
- 🔄 Generates synthetic elevation data (realistic terrain model)
- 🔄 Generates synthetic imagery data (land cover simulation)
- ✅ Still produces valid GeoTIFF files in UTM projection

### 📊 **Synthetic Data Quality**

The synthetic data is designed to be realistic and useful for testing:

**Elevation Data:**
- Elevation range: 0-1000 meters
- Realistic terrain variation with noise
- Center-high, edge-low elevation pattern
- Proper GeoTIFF format with spatial reference

**Imagery Data:**
- RGB imagery with realistic land cover colors
- Simulated forests, fields, water, and urban areas
- Random patch distribution for variety
- Standard 8-bit RGB GeoTIFF format

### 🚀 **Running in Offline Mode**

The application can now run completely offline:

1. **Start the application normally:**
   ```bash
   python3 run.py
   ```

2. **Network status will show offline warning**

3. **Downloads will work using synthetic data**

4. **All UTM projection and GeoTIFF features still function**

### 🔍 **Debugging Network Issues**

#### Enable Verbose Logging
Add to the beginning of `app.py`:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

#### Test Individual Services
```python
# Test script to check each service
import requests

services = [
    "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer",
    "https://services.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer",
    "https://cloud.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
]

for service in services:
    try:
        response = requests.get(service, timeout=10)
        print(f"✅ {service}: {response.status_code}")
    except Exception as e:
        print(f"❌ {service}: {e}")
```

### 📱 **Mobile/Remote Access**

If accessing from a remote machine:

1. **Change host binding in app.py:**
   ```python
   app.run(debug=True, host='0.0.0.0', port=5000)
   ```

2. **Access via IP address:**
   ```
   http://YOUR_SERVER_IP:5000
   ```

3. **Firewall configuration:**
   ```bash
   # Ubuntu/Debian
   sudo ufw allow 5000
   
   # CentOS/RHEL
   sudo firewall-cmd --add-port=5000/tcp --permanent
   sudo firewall-cmd --reload
   ```

### 🆘 **Still Having Issues?**

1. **Check the application logs** for specific error messages
2. **Verify Python dependencies** are installed correctly
3. **Test with synthetic data** to ensure core functionality works
4. **Check system resources** (disk space, memory)
5. **Try running on a different network** to isolate connectivity issues

The enhanced application is designed to be resilient and work even with network connectivity problems!
