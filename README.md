# OSCordon

A Python library for working with Ordnance Survey's NGD Trasnport colletion and creating buffer zones USRNs (Unique Street Reference Numbers) and associated Road Links.

## Features

- Fetch street data from Ordnance Survey APIs using USRNs
- Create buffer zones around streets with customisable parameters
- Generate bounding boxes for multiple streets
- Support for both individual and merged buffer polygons
- Compatible with lonboard for interactive mapping
- Built-in crs (EPSG:27700 -> EPSG:4326)

## Quick Start

### 1. Set up your API key

```bash
export OS_KEY="your_ordnance_survey_api_key"
```

### 2. Basic usage in Jupyter Notebook

```python
# Simple Bounding Box and Single USRN
os_obj = OSObject()
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

## Main Functions

### `create_buffer()`

Create buffer zones around individual streets.

- **Returns**: Bounds tuple or Polygon geometry
- **Use case**: Single street

### `create_proximity()`

Handle multiple streets that are close but not connected.

- **Returns**: GeoDataFrame with individual buffers or bounding box
- **Use case**: Scattered nearby streets

### `create_connected_route()`

Create merged buffer zones for connected street routes.

- **Returns**: GeoDataFrame with merged buffers or bounding box
- **Use case**: Continuous routes and corridors

## Buffer Configuration

Customise buffer shapes with the `buffer_config` parameter:

```python
config = {
    "cap_style": "round",      # "round", "flat", "square"
    "join_style": "round",     # "round", "mitre", "bevel"
    "resolution": 16,          # Higher = smoother curves
    "mitre_limit": 5.0,        # For mitre joins
    "single_sided": False      # One-sided buffers
}
```

## GeoDataFrame Type Reference

Each function returns different geometry types that you can filter for mapping:

### `create_buffer()` + `convert_to_wgs84()`

Single street only:

```python
roads = gdf[gdf["type"] == "usrn"]        # Street line
boundary = gdf[gdf["type"] == "boundary"] # Buffer polygon
```

### `create_proximity()`

Multiple streets, close but not connected:

**When `return_geometry=True`:**

```python
roads = gdf[gdf["type"] == "usrn"]                    # Individual street lines
individual_buffers = gdf[gdf["type"] == "buffer"]     # Individual buffer polygons
```

**When `return_geometry=False`:**

```python
roads = gdf[gdf["type"] == "usrn"]                    # Individual street lines
bounding_box = gdf[gdf["type"] == "route_bounds"]     # Single bounding rectangle
```

### `create_connected_route()`

Multiple streets forming a connected route:

**When `return_geometry=True` + `merge_buffers=True` (default):**

```python
roads = gdf[gdf["type"] == "usrn"]                    # Individual street lines
merged_buffer = gdf[gdf["type"] == "route_buffer"]    # Single merged buffer
```

**When `return_geometry=True` + `merge_buffers=False`:**

```python
roads = gdf[gdf["type"] == "usrn"]                    # Individual street lines
individual_buffers = gdf[gdf["type"] == "buffer"]     # Individual buffer polygons
```

**When `return_geometry=False`:**

```python
roads = gdf[gdf["type"] == "usrn"]                    # Individual street lines
bounding_box = gdf[gdf["type"] == "route_bounds"]     # Single bounding rectangle
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
