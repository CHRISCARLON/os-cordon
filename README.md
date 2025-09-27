# OSCordon

A Python library for working with Ordnance Survey's NGD Trasnport colletion and creating buffers for USRNs (Unique Street Reference Numbers) and associated Road Links.

Construct more detailed geometries from bounding boxes.

**Connected Routes**
<img width="1180" height="596" alt="Screenshot 2025-09-24 at 23 42 41" src="https://github.com/user-attachments/assets/b4aa3a86-1e87-40fd-b4a5-98457992abb4" />

<img width="1180" height="596" alt="Screenshot 2025-09-24 at 23 42 52" src="https://github.com/user-attachments/assets/6c8717c7-4982-41cf-9f29-6c2c1c84be17" />

**Single Streets**
<img width="1180" height="596" alt="Screenshot 2025-09-24 at 23 42 06" src="https://github.com/user-attachments/assets/eeb1b200-616e-4a48-ba79-e7df17830ede" />

<img width="1180" height="596" alt="Screenshot 2025-09-24 at 23 42 30" src="https://github.com/user-attachments/assets/804ff4b8-635b-4801-8790-c307a7817585" />

## Features

- Fetch street data from Ordnance Survey APIs using USRNs
- Create buffer zones around streets/road links with customisable parameters
- Generate bounding boxes for multiple streets/road links
- Support for both individual and merged buffer polygons
- Compatible with lonboard for interactive mapping
- Built-in crs conversion (EPSG:27700 -> EPSG:4326)

## Quick Start

### 1. Set up your API key

```bash
export OS_KEY="your_ordnance_survey_api_key"
```

### 2. Basic usage in Jupyter Notebook

```python
# Simple Bounding Box and Single USRN
os_obj = UsrnObject()
usrn = "23009365"
street_data = os_obj.get_street_data(usrn)

bounds = os_obj.create_buffer(
    street_data,
    buffer_distance=10,
    return_geometry=False
)

# Convert to WGS84 and return GeoDataFrame
# This has to be specified when working with just 1 USRN if you want the map to show
gdf = os_obj.convert_to_wgs84(street_data, bbox=bounds)

# Create layers for the USRN line and buffer geom
usrn_layer = PathLayer.from_geopandas(
    gdf[gdf["type"] == "usrn"], get_color=[0, 0, 255], width_min_pixels=3
)

boundary_layer = PolygonLayer.from_geopandas(
    gdf[gdf["type"] == "boundary"],
    get_fill_color=[255, 165, 0, 100],
    get_line_color=[255, 165, 0],
    line_width_min_pixels=2,
)

# Simple Bounding Box
Map(layers=[usrn_layer, boundary_layer])
```

## Examples

See `cordon_quick_start.ipynb` for complete examples including:

- Single street buffers
- Multiple street routes
- Bounding box vs detailed geometries
- Interactive mapping with lonboard

## Requirements

- Python 3.11
- Valid Ordnance Survey API key
- Dependencies: geopandas, shapely, requests, loguru
