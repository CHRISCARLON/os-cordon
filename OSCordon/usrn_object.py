import os
from typing import Any, Dict, Literal, Optional, Tuple, Union
from urllib.parse import urlencode

import geopandas as gpd
import requests
from errors import (
    APIError,
    APIKeyError,
    BufferConfigError,
    EmptyDataError,
    InvalidGeometryError,
    RateLimitError,
    RouteCreationError,
    USRNNotFoundError,
)
from loguru import logger
from shapely.geometry import (
    CAP_STYLE,
    JOIN_STYLE,
    MultiLineString,
    MultiPolygon,
    Polygon,
    box,
)
from shapely.ops import unary_union

# TODO: Add Pydantic models for return types
# TODO: Make async


class UsrnObject:
    """
    A class for fetching and processing OS USRN (Unique Street Reference Number) data.

    Provides methods for:
    - Fetching street data from OS API by USRN
    - Creating buffers around street geometries
    - Building proximity-based and connected routes
    - Converting between coordinate systems
    """

    def __init__(self) -> None:
        """Initialise with the API key"""
        self.api_key = os.getenv("OS_KEY")
        if not self.api_key:
            raise APIKeyError(
                "An API key must be provided as an environment variable 'OS_KEY'"
            )
        self.base_path = "https://api.os.uk/features/ngd/ofa/v1/{}"
        self.collection_feature = self.base_path.format("collections/{}/items")

    # ============================================================================
    # CORE API METHODS - Low-level data fetching
    # ============================================================================

    def _fetch_data(self, endpoint: str) -> dict:
        """
        Private method to fetch data from an OS endpoint

        Args:
            endpoint: str - The endpoint to fetch data from

        Returns:
            dict - The data from the endpoint
        """
        try:
            headers = {"key": self.api_key, "Content-Type": "application/json"}
            response = requests.get(endpoint, headers=headers)

            if response.status_code == 401:
                raise APIKeyError("Invalid API key or authentication failed")
            elif response.status_code == 429:
                raise RateLimitError("API rate limit exceeded")
            elif response.status_code == 404:
                raise USRNNotFoundError(
                    endpoint.split("=")[-1] if "usrn=" in endpoint else "unknown"
                )

            response.raise_for_status()
            result = response.json()
            return result
        except requests.RequestException as e:
            if not isinstance(e, (APIKeyError, RateLimitError, USRNNotFoundError)):
                raise APIError("API Request Failed")
            raise

    def _get_single_feature(
        self,
        collection_id: str,
        query_attr: Literal["usrn"],
        query_attr_value: Optional[str] = None,
        bbox: Optional[str] = None,
        bbox_crs: Optional[str] = None,
        crs: Optional[str] = "http://www.opengis.net/def/crs/EPSG/0/27700",
    ) -> dict[Any, Any]:
        """
        Fetches collection features with optional USRN filter or bbox parameters

        Default to CRS 27700 (British National Grid)

        Args:
            collection_id: str - The ID of the collection
            query_attr: Optional[Literal["usrn"]] - Optional query attribute (only "usrn" allowed at the moment)
            query_attr_value: Optional[str] - Value for the query attribute (e.g. USRN number)
            bbox: Optional[str] - Bounding box parameter
            bbox_crs: Optional[str] - CRS for the bounding box
            crs: Optional[str] - CRS for the response

        Returns:
            API response with collection features
        """

        endpoint: str = self.collection_feature.format(collection_id)

        query_params: Dict[str, Any] = {}

        if query_attr and query_attr_value:
            query_params["filter"] = f"{query_attr}={query_attr_value}"
        if bbox:
            query_params["bbox"] = bbox
        if bbox_crs:
            query_params["bbox-crs"] = bbox_crs
        if crs:
            query_params["crs"] = crs

        if query_params:
            endpoint = f"{endpoint}?{urlencode(query_params)}"

        result = self._fetch_data(endpoint)
        logger.success("USRN Data Found")
        return result

    # ============================================================================
    # SINGLE USRN DATA FETCHING - Primary methods for getting street data
    # ============================================================================

    def get_street_data(
        self,
        usrn: str,
        crs: Optional[str] = "http://www.opengis.net/def/crs/EPSG/0/27700",
    ) -> dict[Any, Any]:
        """
        Fetches street data from the trn-ntwk-street-1 collection filtered by USRN

        Args:
            usrn: str - The USRN (Unique Street Reference Number) to filter by
            crs: Optional[str] - Coordinate Reference System for the response
                               Default: "http://www.opengis.net/def/crs/EPSG/0/27700" (British National Grid)
                               For WGS84: "http://www.opengis.net/def/crs/EPSG/0/4326"
        Returns:
            dict - API response with street features matching the USRN
        """
        return self._get_single_feature(
            collection_id="trn-ntwk-street-1",
            query_attr="usrn",
            query_attr_value=usrn,
            crs=crs,
        )

    def get_multiple_streets(
        self,
        usrn_list: list[str],
        crs: Optional[str] = "http://www.opengis.net/def/crs/EPSG/0/27700",
    ) -> list[dict]:
        """
        Fetch street data for multiple USRNs

        Args:
            usrn_list: List of USRN strings
            crs: Coordinate Reference System

        Returns:
            List of street data dictionaries
        """
        street_data_list = []
        failed_usrns = []
        for usrn in usrn_list:
            try:
                data = self.get_street_data(usrn, crs)
                street_data_list.append(data)
                logger.info(f"Retrieved USRN {usrn}")
            except Exception:
                logger.error(f"Failed to retrieve USRN {usrn}")
                failed_usrns.append(usrn)

        if failed_usrns and not street_data_list:
            raise RouteCreationError(usrn_list, failed_usrns)

        logger.success(f"Retrieved {len(street_data_list)} of {len(usrn_list)} USRNs")
        return street_data_list

    # ============================================================================
    # BUFFER OPERATIONS - Creating buffers around street geometries
    # ============================================================================

    def create_buffer(
        self,
        street_data: dict,
        buffer_distance: float = 50,
        buffer_config: Optional[dict] = None,
        return_geometry: bool = False,
    ) -> Union[Tuple[float, float, float, float], Polygon]:
        """
        Creates a boundary polygon from USRN geometry using a buffer
        Default is set to 50 metres

        Args:
            street_data: dict - The street data returned from get_street_data()
            buffer_distance: float - Buffer distance in meters (when using EPSG:27700)
                                   or degrees (when using EPSG:4326)
                                   Default: 50 meters for EPSG:27700
            buffer_config: Optional dict with buffer parameters:
                - cap_style: str - Style of line ends: "round", "flat", or "square"
                             - "round": Creates semicircular line endings
                             - "flat": Creates straight line endings
                             - "square": Creates squared-off endings that extend past the line
                - join_style: str - Style of line joins: "round", "mitre", or "bevel"
                              - "round": Creates curved corners
                              - "mitre": Creates sharp corners (best for tight hugging)
                              - "bevel": Creates cut-off corners
                - mitre_limit: float - Limit for mitre joins (only used with join_style="mitre")
                                 Lower values = more aggressive corner cutting
                - resolution: int - Number of segments to approximate a quarter circle (for round styles)
                              Lower values = more angular curves (try 4-8 for tighter fit)
                - single_sided: bool - If True, creates buffer only on one side of the line
            return_geometry: bool - If True, returns the buffered geometry instead of just bounds

        Returns: bounds (minx, miny, maxx, maxy) or geometry if return_geometry=True
        """
        if buffer_config is None:
            buffer_config = {
                "cap_style": "round",
                "join_style": "round",
                "mitre_limit": 5.0,
                "resolution": 16,
                "single_sided": False,
            }

        if not street_data.get("features"):
            raise EmptyDataError("features")

        feature = street_data["features"][0]
        geometry = feature.get("geometry", {})

        if geometry.get("type") != "MultiLineString":
            raise InvalidGeometryError("MultiLineString", geometry.get("type", "None"))

        geom = MultiLineString(geometry["coordinates"])

        cap_styles = {
            "round": CAP_STYLE.round,
            "flat": CAP_STYLE.flat,
            "square": CAP_STYLE.square,
        }

        join_styles = {
            "round": JOIN_STYLE.round,
            "mitre": JOIN_STYLE.mitre,
            "bevel": JOIN_STYLE.bevel,
        }

        cap_style = buffer_config.get("cap_style", "square")
        join_style = buffer_config.get("join_style", "round")
        mitre_limit = buffer_config.get("mitre_limit", 5.0)
        resolution = buffer_config.get("resolution", 16)
        single_sided = buffer_config.get("single_sided", False)

        if cap_style not in cap_styles:
            raise BufferConfigError("cap_style", cap_style, list(cap_styles.keys()))
        if join_style not in join_styles:
            raise BufferConfigError("join_style", join_style, list(join_styles.keys()))

        buffered = geom.buffer(
            buffer_distance,
            cap_style=cap_styles[cap_style],
            join_style=join_styles[join_style],
            mitre_limit=mitre_limit,
            resolution=resolution,
            single_sided=single_sided,
        )

        if return_geometry:
            logger.success("Complex Boundary Geometry Created Successfully")
            return buffered
        else:
            bounds = buffered.bounds
            logger.success(
                f"Simple Tuple Bounding Box Created Successfully (cap: {cap_style}, join: {join_style})"
            )
            return bounds

    # ============================================================================
    # COORDINATE SYSTEM CONVERSION - CRS transformation utilities
    # ============================================================================

    def convert_to_wgs84(
        self,
        street_data: dict,
        bbox: Optional[
            Union[Tuple[float, float, float, float], Polygon, MultiPolygon]
        ] = None,
    ) -> gpd.GeoDataFrame:
        """
        Convert USRN geometry and optionally a bbox from EPSG:27700 to EPSG:4326 (WGS84)
        Returns a GeoDataFrame suitable for use with lonboard

        Args:
            street_data: dict - The street data returned from get_street_data() in EPSG:27700
            bbox: Optional[Union[Tuple, Polygon, MultiPolygon]] - Bounding box as tuple (minx, miny, maxx, maxy), Polygon, or MultiPolygon geometry in EPSG:27700

        Returns:
            GeoDataFrame - Contains USRN line and boundary polygon in WGS84 format
        """
        if not street_data.get("features"):
            raise EmptyDataError("features")

        feature = street_data["features"][0]
        geometry = feature.get("geometry", {})

        if geometry.get("type") != "MultiLineString":
            raise InvalidGeometryError("MultiLineString", geometry.get("type", "None"))

        # Prepare data for GeoDataFrame
        data = []
        geometries = []

        # Add USRN geometry
        usrn_geom = MultiLineString(geometry["coordinates"])
        data.append(
            {
                "type": "usrn",
                "usrn": feature.get("properties", {}).get("usrn"),
                "street_name": feature.get("properties", {}).get("street_description"),
            }
        )
        geometries.append(usrn_geom)

        # Add boundary
        if bbox is not None:
            # Handle tuple, Polygon, and MultiPolygon inputs
            if isinstance(bbox, tuple):
                minx, miny, maxx, maxy = bbox
                bbox_geom = box(minx, miny, maxx, maxy)
            elif isinstance(bbox, (Polygon, MultiPolygon)):
                bbox_geom = bbox
            else:
                raise InvalidGeometryError(
                    "Tuple, Polygon, or MultiPolygon", str(type(bbox))
                )

            data.append(
                {
                    "type": "boundary",
                    "usrn": feature.get("properties", {}).get("usrn"),
                    "street_name": feature.get("properties", {}).get(
                        "street_description"
                    ),
                }
            )
            geometries.append(bbox_geom)

        # Create GeoDataFrame in EPSG:27700
        gdf = gpd.GeoDataFrame(data, geometry=geometries, crs="EPSG:27700")

        # Convert to WGS84
        gdf_wgs84 = gdf.to_crs("EPSG:4326")

        logger.success(f"Converted {len(gdf_wgs84)} geometries to WGS84")
        logger.debug(gdf_wgs84)
        return gdf_wgs84

    # ============================================================================
    # PROXIMITY ROUTES - Creating routes for nearby but not connected streets
    # ============================================================================

    def _create_proximity_bbox_only(
        self, street_data_list: list[dict], buffer_distance: float, buffer_config: dict
    ) -> gpd.GeoDataFrame:
        """
        Creates buffers then calculates bounding box from them.
        """

        # Hold data here first
        usrn_data = []
        usrn_geometries = []
        all_buffers = []

        # TODO: deal with the null value here better
        for idx, street_data in enumerate(street_data_list):
            if not street_data.get("features"):
                continue

            feature = street_data["features"][0]
            geometry = feature.get("geometry", {})

            # TODO: deal with the potential non multi linestring better here
            if geometry.get("type") != "MultiLineString":
                continue

            usrn_geom = MultiLineString(geometry["coordinates"])
            usrn_value = feature.get("properties", {}).get("usrn")
            street_name = feature.get("properties", {}).get("street_description")

            usrn_data.append(
                {
                    "type": "usrn",
                    "usrn": usrn_value,
                    "street_name": street_name,
                    "route_order": idx,
                    "geometry_type": "line",
                }
            )
            usrn_geometries.append(usrn_geom)

            # Create buffer for this street
            buffer_geom = self.create_buffer(
                street_data,
                buffer_distance=buffer_distance,
                buffer_config=buffer_config,
                return_geometry=True,
            )
            all_buffers.append(buffer_geom)

        if not usrn_geometries:
            raise EmptyDataError("No valid geometries found in route")

        # Create bounding box from buffers
        combined_buffers = unary_union(all_buffers)
        bounds = combined_buffers.bounds
        minx, miny, maxx, maxy = bounds
        bbox_polygon = box(minx, miny, maxx, maxy)

        bbox_data = [
            {
                "type": "route_bounds",
                "usrn": "all",
                "street_name": "Route Bounding Box",
                "route_order": -1,
            }
        ]

        # Combine USRN data and bbox data
        combined_data = usrn_data + bbox_data
        combined_geometries = usrn_geometries + [bbox_polygon]

        bbox_gdf = gpd.GeoDataFrame(
            combined_data, geometry=combined_geometries, crs="EPSG:27700"
        )
        bbox_gdf_wgs84 = bbox_gdf.to_crs("EPSG:4326")
        logger.success(
            f"Created proximity route with bounds and {len(usrn_data)} USRNs"
        )
        logger.debug(bbox_gdf_wgs84)
        return bbox_gdf_wgs84

    def _create_proximity_full_route(
        self,
        street_data_list: list[dict],
        usrn_list: list[str],
        buffer_distance: float,
        buffer_config: dict,
        include_buffers: bool,
    ) -> gpd.GeoDataFrame:
        """
        Full processing path for creating proximity routes with buffers.
        Handles buffer creation for individual streets.
        """

        # Store final data
        all_data = []
        all_geometries = []

        for idx, street_data in enumerate(street_data_list):
            if not street_data.get("features"):
                continue

            feature = street_data["features"][0]
            geometry = feature.get("geometry", {})

            if geometry.get("type") != "MultiLineString":
                continue

            usrn_geom = MultiLineString(geometry["coordinates"])
            usrn_value = feature.get("properties", {}).get("usrn")
            street_name = feature.get("properties", {}).get("street_description")

            all_data.append(
                {
                    "type": "usrn",
                    "usrn": usrn_value,
                    "street_name": street_name,
                    "route_order": idx,
                    "geometry_type": "line",
                }
            )
            all_geometries.append(usrn_geom)

            # Add buffer if requested
            if include_buffers:
                buffer_geom = self.create_buffer(
                    street_data,
                    buffer_distance=buffer_distance,
                    buffer_config=buffer_config,
                    return_geometry=True,
                )

                all_data.append(
                    {
                        "type": "buffer",
                        "usrn": usrn_value,
                        "street_name": street_name,
                        "route_order": idx,
                        "geometry_type": "polygon",
                    }
                )
                all_geometries.append(buffer_geom)

        # Create and return GeoDataFrame with USRN geometries and separate usrn polygons
        gdf = gpd.GeoDataFrame(all_data, geometry=all_geometries, crs="EPSG:27700")
        gdf_wgs84 = gdf.to_crs("EPSG:4326")
        logger.success(f"Created route GeoDataFrame with {len(usrn_list)} USRNs")
        logger.debug(gdf_wgs84)
        return gdf_wgs84

    def create_proximity(
        self,
        usrn_list: list[str],
        buffer_distance: float = 20,
        buffer_config: Optional[dict] = None,
        include_buffers: bool = True,
        return_geometry: bool = True,
    ) -> gpd.GeoDataFrame:
        """
        Create a GeoDataFrame with multiple USRNs and their buffers.

        Best used for streets that are in close proximity but not directly connected.

        Args:
            usrn_list: List of USRN strings to form the route
            buffer_distance: Buffer distance for all streets
            buffer_config: Optional dict with buffer parameters (cap_style, join_style, etc.)
            include_buffers: Whether to include buffer polygons

        Returns:
            If return_geometry=False: GeoDataFrame with a single bounding box polygon for the entire route
            If return_geometry=True: GeoDataFrame with all USRNs and buffers in WGS84
        """
        if buffer_config is None:
            buffer_config = {
                "cap_style": "round",
                "join_style": "round",
                "resolution": 16,
            }

        street_data_list = self.get_multiple_streets(usrn_list)

        # Early return for bbox-only processing
        if not return_geometry:
            return self._create_proximity_bbox_only(
                street_data_list, buffer_distance, buffer_config
            )

        # Processing for detailed geometry
        return self._create_proximity_full_route(
            street_data_list, usrn_list, buffer_distance, buffer_config, include_buffers
        )

    # ============================================================================
    # CONNECTED ROUTES - Creating routes for connected streets (e.g., planned routes)
    # ============================================================================

    def _create_ful_route_bbox_only(
        self, street_data_list: list[dict], buffer_distance: float, buffer_config: dict
    ) -> gpd.GeoDataFrame:
        """
        Fast path for creating bounding box from buffers.
        Creates buffers then calculates bounding box from them.
        """
        usrn_data = []
        usrn_geometries = []
        all_buffers = []

        for idx, street_data in enumerate(street_data_list):
            if not street_data.get("features"):
                logger.warning(f"Did not find data for Index: {idx}")
                continue

            feature = street_data["features"][0]
            geometry = feature.get("geometry", {})

            if geometry.get("type") != "MultiLineString":
                logger.warning(f"Did not find MultiLineString Geom for{idx}")
                continue

            usrn_geom = MultiLineString(geometry["coordinates"])
            usrn_value = feature.get("properties", {}).get("usrn")

            usrn_data.append(
                {
                    "type": "usrn",
                    "usrn": usrn_value,
                    "street_name": feature.get("properties", {}).get(
                        "street_description"
                    ),
                    "route_order": idx,
                }
            )
            usrn_geometries.append(usrn_geom)

            # Create buffer for this street
            buffer_geom = self.create_buffer(
                street_data,
                buffer_distance=buffer_distance,
                buffer_config=buffer_config,
                return_geometry=True,
            )
            all_buffers.append(buffer_geom)

        if not usrn_geometries:
            raise EmptyDataError("No valid geometries found in route")

        # Create bounding box from buffers
        combined_buffers = unary_union(all_buffers)
        bounds = combined_buffers.bounds
        minx, miny, maxx, maxy = bounds
        bbox_polygon = box(minx, miny, maxx, maxy)

        bbox_data = [
            {
                "type": "route_bounds",
                "usrn": "all",
                "street_name": "Route Bounding Box",
                "route_order": -1,
            }
        ]

        # Combine USRN data and bbox data
        combined_data = usrn_data + bbox_data
        combined_geometries = usrn_geometries + [bbox_polygon]

        bbox_gdf = gpd.GeoDataFrame(
            combined_data, geometry=combined_geometries, crs="EPSG:27700"
        )
        bbox_gdf_wgs84 = bbox_gdf.to_crs("EPSG:4326")
        logger.success(
            f"Created connected route with bounds and {len(usrn_data)} USRNs"
        )
        logger.debug(bbox_gdf_wgs84)
        return bbox_gdf_wgs84

    def _create_full_route_connected(
        self,
        street_data_list: list[dict],
        usrn_list: list[str],
        buffer_distance: float,
        buffer_config: dict,
        merge_buffers: bool,
    ) -> gpd.GeoDataFrame:
        """
        Full processing path for creating route with buffers.
        Handles buffer creation, merging, and detailed geometry.
        """
        all_data = []
        all_geometries = []
        all_buffers = []

        for idx, street_data in enumerate(street_data_list):
            if not street_data.get("features"):
                logger.warning(f"Did not find data for Index: {idx}")
                continue

            feature = street_data["features"][0]
            geometry = feature.get("geometry", {})

            if geometry.get("type") != "MultiLineString":
                logger.warning(f"Did not find MultiLineString Geom for{idx}")
                continue

            usrn_geom = MultiLineString(geometry["coordinates"])
            usrn_value = feature.get("properties", {}).get("usrn")

            all_data.append(
                {
                    "type": "usrn",
                    "usrn": usrn_value,
                    "street_name": feature.get("properties", {}).get(
                        "street_description"
                    ),
                    "route_order": idx,
                }
            )
            all_geometries.append(usrn_geom)

            buffer_geom = self.create_buffer(
                street_data,
                buffer_distance=buffer_distance,
                buffer_config=buffer_config,
                return_geometry=True,
            )
            all_buffers.append(buffer_geom)

        if merge_buffers and all_buffers:
            merged_buffer = unary_union(all_buffers)
            all_data.append(
                {
                    "type": "route_buffer",
                    "usrn": "merged",
                    "street_name": "Full Route Buffer",
                    "route_order": -1,
                }
            )
            all_geometries.append(merged_buffer)
        else:
            for idx, buffer_geom in enumerate(all_buffers):
                all_data.append(
                    {
                        "type": "buffer",
                        "usrn": usrn_list[idx] if idx < len(usrn_list) else None,
                        "street_name": f"Buffer {idx}",
                        "route_order": idx,
                    }
                )
                all_geometries.append(buffer_geom)

        gdf = gpd.GeoDataFrame(all_data, geometry=all_geometries, crs="EPSG:27700")
        gdf_wgs84 = gdf.to_crs("EPSG:4326")
        logger.success(
            f"Created connected route GeoDataFrame with {len(usrn_list)} USRNs"
        )
        logger.debug(gdf_wgs84)
        return gdf_wgs84

    def create_connected_route(
        self,
        usrn_list: list[str],
        buffer_distance: float = 50,
        merge_buffers: bool = True,
        buffer_config: Optional[dict] = None,
        return_geometry: bool = True,
    ) -> gpd.GeoDataFrame:
        """
        Create a route with connected/merged buffers for continuous coverage.

        Best used for streets that are in connected and supposed to form part of a planned route.

        Args:
            usrn_list: List of USRNs forming the route
            buffer_distance: Buffer distance
            merge_buffers: If True, merge overlapping buffers into one polygon
            buffer_config: Optional dict with buffer parameters (cap_style, join_style, resolution, etc.)

        Returns:
            If return_geometry=False: GeoDataFrame with a single bounding box polygon for the merged route
            If return_geometry=True: GeoDataFrame with route geometries
            Default is True.
        """
        if buffer_config is None:
            buffer_config = {
                "cap_style": "round",
                "join_style": "round",
                "resolution": 16,
                "mitre_limit": 2.0,
            }

        street_data_list = self.get_multiple_streets(usrn_list)

        # Early return for bbox-only processing
        if not return_geometry:
            return self._create_ful_route_bbox_only(
                street_data_list, buffer_distance, buffer_config
            )

        # Processing for detailed geometry
        return self._create_full_route_connected(
            street_data_list, usrn_list, buffer_distance, buffer_config, merge_buffers
        )
