import asyncio
import os
from itertools import chain
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlencode

import aiohttp
import geopandas as gpd
from errors import (
    APIError,
    APIKeyError,
    RateLimitError,
    USRNNotFoundError,
)
from loguru import logger
from shapely.geometry import LineString
from shapely.ops import unary_union


class RoadLinkObject:
    """
    Async-only RoadLinkObject with parallel API calls using aiohttp.

    Provides async methods for:
    - Fetching road link and street data from OS API in parallel batches
    - Analyzing network connectivity and finding intersections
    - Creating geographic representations with buffers in GeoDataFrames

    Usage:
        async with RoadLinkObject() as road_obj:
            data = await road_obj.get_road_data_async(usrn_list)
            gdf = await road_obj.get_route_as_geodataframe_async(roadlink_ids)
    """

    def __init__(self) -> None:
        """Initialise with the API key"""
        self.api_key = os.getenv("OS_KEY")
        if not self.api_key:
            raise APIKeyError(
                "An API key must be provided as an environment variable 'OS_KEY'"
            )
        self.base_path = "https://api.os.uk/features/ngd/ofa/v1/{}"
        self.collection_feature = self.base_path.format("collections/{}/items/{}")
        self.session = None

    async def __aenter__(self):
        """Async context manager entry - creates aiohttp session"""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - closes aiohttp session"""
        if self.session:
            await self.session.close()

    # ============================================================================
    # ASYNC CORE API METHODS - Parallel data fetching
    # ============================================================================

    async def _fetch_data_async(
        self, endpoint: str, session: Optional[aiohttp.ClientSession] = None
    ) -> dict:
        """
        Async method to fetch data from an OS endpoint

        Args:
            endpoint: str - The endpoint to fetch data from
            session: Optional aiohttp.ClientSession - Session to use (creates one if not provided)

        Returns:
            dict - The data from the endpoint
        """
        headers = {"key": self.api_key, "Content-Type": "application/json"}

        _session = session or self.session
        if not _session:
            async with aiohttp.ClientSession() as temp_session:
                return await self._fetch_data_async(endpoint, temp_session)

        try:
            async with _session.get(endpoint, headers=headers) as response:
                if response.status == 401:
                    raise APIKeyError("Invalid API key or authentication failed")
                elif response.status == 429:
                    raise RateLimitError("API rate limit exceeded")
                elif response.status == 404:
                    raise USRNNotFoundError(
                        endpoint.split("=")[-1] if "usrn=" in endpoint else "unknown"
                    )

                response.raise_for_status()
                result = await response.json()
                return result
        except aiohttp.ClientError as e:
            raise APIError(f"API Request Failed: {e}")

    async def _get_single_feature_async(
        self,
        collection_id: str,
        feature_id: Optional[str] = None,
        query_attr: Optional[Literal["usrn"]] = None,
        query_attr_value: Optional[str] = None,
        bbox: Optional[str] = None,
        bbox_crs: Optional[str] = None,
        crs: Optional[str] = "http://www.opengis.net/def/crs/EPSG/0/27700",
        session: Optional[aiohttp.ClientSession] = None,
    ) -> dict[Any, Any]:
        """
        Async version of fetching collection features

        Args:
            collection_id: str - The ID of the collection
            feature_id: Optional[str] - Specific feature ID to fetch
            query_attr: Optional[Literal["usrn"]] - Optional query attribute
            query_attr_value: Optional[str] - Value for the query attribute
            bbox: Optional[str] - Bounding box parameter
            bbox_crs: Optional[str] - CRS for the bounding box
            crs: Optional[str] - CRS for the response
            session: Optional aiohttp.ClientSession - Session to use for the request

        Returns:
            API response with collection features
        """
        if feature_id:
            endpoint: str = self.collection_feature.format(collection_id, feature_id)
        else:
            endpoint: str = self.base_path.format(f"collections/{collection_id}/items")

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

        result = await self._fetch_data_async(endpoint, session)
        logger.success(f"Data found for {feature_id or query_attr_value}")
        return result

    async def _fetch_roadlink_batch(
        self, roadlink_ids: List[str], crs: str, session: aiohttp.ClientSession
    ) -> List[Tuple[str, Optional[dict]]]:
        """
        Fetch multiple roadlinks in parallel

        Args:
            roadlink_ids: List of roadlink IDs to fetch
            crs: Coordinate Reference System
            session: aiohttp session to use

        Returns:
            List of tuples (roadlink_id, result_or_none)
        """
        tasks = []
        for roadlink_id in roadlink_ids:
            task = self._fetch_single_roadlink(roadlink_id, crs, session)
            tasks.append(task)

        results = await asyncio.gather(*tasks)
        return list(zip(roadlink_ids, results))

    async def _fetch_single_roadlink(
        self, roadlink_id: str, crs: str, session: aiohttp.ClientSession
    ) -> Optional[dict]:
        """
        Safely fetch a single roadlink (returns None on error)

        Args:
            roadlink_id: The roadlink ID to fetch
            crs: Coordinate Reference System
            session: aiohttp session to use

        Returns:
            Roadlink data or None if failed
        """
        try:
            result = await self._get_single_feature_async(
                collection_id="trn-ntwk-roadlink-5",
                feature_id=roadlink_id,
                crs=crs,
                session=session,
            )
            logger.debug(f"Retrieved roadlink data for ID: {roadlink_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve roadlink {roadlink_id}: {e}")
            return None

    # ============================================================================
    # ASYNC DATA FETCHING - Standalone method with parallel fetching
    # ============================================================================

    async def get_road_data_async(
        self,
        usrn_list: list[str],
        crs: str = "http://www.opengis.net/def/crs/EPSG/0/27700",
        batch_size: int = 10,
    ) -> dict[Any, Any]:
        """
        Async version of get_road_data - fetches roadlink data in parallel

        Args:
            usrn_list: list[str] - List of USRNs to fetch roadlink data for
            crs: Optional[str] - Coordinate Reference System
            batch_size: int - Number of roadlinks to fetch in parallel (default 10)

        Returns:
            dict - Contains roadlink data organised by USRN with node connectivity info
        """

        if len(usrn_list) < 2:
            raise ValueError("Must provide more than 1 usrn")

        unique_usrns = list(set(usrn_list))
        if len(unique_usrns) != len(usrn_list):
            raise ValueError("USRNs must be distinct. You can't have duplicate USRNs.")

        all_roadlinks = {}
        roadlink_to_usrn = {}
        all_nodes = {}

        async with aiohttp.ClientSession() as session:
            for usrn in usrn_list:
                # TODO: Make USRNs async too
                # Get street data for current USRN (still uses sequential procesing for USRNs)
                usrn_data = await self._get_single_feature_async(
                    collection_id="trn-ntwk-street-1",
                    query_attr="usrn",
                    query_attr_value=usrn,
                    crs=crs,
                    session=session,
                )

                # Extract roadlink IDs
                road_link_ids = []
                features = usrn_data.get("features", [])
                all_refs = chain.from_iterable(
                    feature.get("properties", {}).get("roadlinkreference", [])
                    for feature in features
                )

                for ref in all_refs:
                    if roadlink_id := ref.get("roadlinkid"):
                        road_link_ids.append(roadlink_id)
                        roadlink_to_usrn[roadlink_id] = usrn

                logger.info(f"Found {len(road_link_ids)} roadlink IDs for USRN {usrn}")

                # Fetch roadlinks in parallel batches
                for i in range(0, len(road_link_ids), batch_size):
                    batch = road_link_ids[i : i + batch_size]
                    batch_results = await self._fetch_roadlink_batch(
                        batch, crs, session
                    )

                    for roadlink_id, roadlink_result in batch_results:
                        if roadlink_result is None:
                            continue

                        # Store the roadlink data
                        all_roadlinks[roadlink_id] = roadlink_result

                        # Extract start and end nodes from properties
                        if roadlink_result.get("properties"):
                            props = roadlink_result["properties"]
                            start_node = props.get("startnode")
                            end_node = props.get("endnode")

                            if start_node and end_node:
                                # Track which roadlinks connect at each node
                                if start_node not in all_nodes:
                                    all_nodes[start_node] = {
                                        "roadlinks": [],
                                        "coords": None,
                                    }
                                all_nodes[start_node]["roadlinks"].append(roadlink_id)

                                if end_node not in all_nodes:
                                    all_nodes[end_node] = {
                                        "roadlinks": [],
                                        "coords": None,
                                    }
                                all_nodes[end_node]["roadlinks"].append(roadlink_id)

                                # Store the actual coordinates for these nodes
                                if roadlink_result.get("geometry"):
                                    coords = roadlink_result["geometry"].get(
                                        "coordinates", []
                                    )
                                    if coords and len(coords) > 0:
                                        all_nodes[start_node]["coords"] = coords[0]
                                        all_nodes[end_node]["coords"] = coords[-1]

        # Find connection points between different USRNs
        connection_points = {}
        for node_id, node_data in all_nodes.items():
            roadlink_ids = node_data["roadlinks"]
            usrns_at_node = set(
                roadlink_to_usrn[rid] for rid in roadlink_ids if rid in roadlink_to_usrn
            )
            if len(usrns_at_node) > 1:
                connection_points[node_id] = {
                    "usrns": list(usrns_at_node),
                    "roadlinks": roadlink_ids,
                    "coords": node_data["coords"],
                }

        return {
            "roadlinks": all_roadlinks,
            "roadlink_to_usrn": roadlink_to_usrn,
            "nodes": all_nodes,
            "connection_points": connection_points,
            "usrn_list": usrn_list,
        }

    # ============================================================================
    # ASYNC GEODATAFRAME CREATION - Route creation with parallel fetching
    # ============================================================================

    async def get_route_as_geodataframe_async(
        self,
        roadlink_ids: List[str],
        buffer_distance: Optional[float] = 20,
        buffer_config: Optional[dict] = None,
        crs: str = "http://www.opengis.net/def/crs/EPSG/0/27700",
        output_crs: str = "EPSG:4326",
        batch_size: int = 10,
    ) -> gpd.GeoDataFrame:
        """
        Async version - Create route GeoDataFrame from specified roadlink IDs

        Args:
            roadlink_ids: List[str] - Specific roadlink IDs to use for the route
            buffer_distance: Optional[float] - Buffer distance in meters
            buffer_config: Optional[dict] - Buffer configuration parameters
            crs: Optional[str] - Input Coordinate Reference System
            output_crs: str - Output CRS (default: EPSG:4326 for WGS84)
            batch_size: int - Number of roadlinks to fetch in parallel

        Returns:
            GeoDataFrame with merged route geometries in specified CRS
        """
        roadlinks_data = {}
        roadlink_to_usrn = {}

        logger.info(f"Fetching {len(roadlink_ids)} roadlink features in parallel")

        async with aiohttp.ClientSession() as session:
            # Fetch all roadlinks in parallel batches
            for i in range(0, len(roadlink_ids), batch_size):
                batch = roadlink_ids[i : i + batch_size]
                batch_results = await self._fetch_roadlink_batch(batch, crs, session)

                for roadlink_id, roadlink_result in batch_results:
                    if roadlink_result is None:
                        continue

                    roadlinks_data[roadlink_id] = roadlink_result

                    # Extract USRN if available
                    props = roadlink_result.get("properties", {})
                    road_refs = props.get("roadtrackorpathreference", [])
                    if road_refs and len(road_refs) > 0:
                        usrn = road_refs[0].get("usrn")
                        if usrn:
                            roadlink_to_usrn[roadlink_id] = str(usrn)

        road_data = {"roadlinks": roadlinks_data, "roadlink_to_usrn": roadlink_to_usrn}

        return self._create_route_geodataframe_from_data(
            road_data, roadlink_ids, buffer_distance, buffer_config, output_crs
        )

    def _create_route_geodataframe_from_data(
        self,
        road_data: dict,
        route: List[str],
        buffer_distance: Optional[float],
        buffer_config: Optional[dict],
        output_crs: str,
    ) -> gpd.GeoDataFrame:
        """
        Create GeoDataFrame from already-fetched road data
        """
        roadlinks = road_data["roadlinks"]
        roadlink_to_usrn = road_data["roadlink_to_usrn"]

        # Prepare data for GeoDataFrame
        data = []
        geometries = []

        # Add roadlink geometries
        for roadlink_id in route:
            if roadlink_id in roadlinks:
                roadlink = roadlinks[roadlink_id]
                if roadlink.get("geometry"):
                    coords = roadlink["geometry"].get("coordinates", [])
                    if coords and len(coords) >= 2:
                        line = LineString(coords)
                        props = roadlink.get("properties", {})

                        data.append(
                            {
                                "type": "roadlink",
                                "roadlink_id": roadlink_id,
                                "usrn": roadlink_to_usrn.get(roadlink_id),
                                "road_name": props.get("name1_text"),
                                "length": props.get("geometry_length"),
                                "start_node": props.get("startnode"),
                                "end_node": props.get("endnode"),
                            }
                        )
                        geometries.append(line)

        # Add buffer if requested
        if buffer_distance and geometries:
            route_lines = [
                g for i, g in enumerate(geometries) if data[i]["type"] == "roadlink"
            ]
            if route_lines:
                merged = (
                    unary_union(route_lines) if len(route_lines) > 1 else route_lines[0]
                )

                if buffer_config is None:
                    buffer_config = {
                        "cap_style": "round",
                        "join_style": "round",
                        "mitre_limit": 5.0,
                        "resolution": 16,
                        "single_sided": False,
                    }

                buffered = merged.buffer(
                    buffer_distance,
                    cap_style=buffer_config.get("cap_style", "round"),
                    join_style=buffer_config.get("join_style", "round"),
                    mitre_limit=buffer_config.get("mitre_limit", 5.0),
                    resolution=buffer_config.get("resolution", 16),
                    single_sided=buffer_config.get("single_sided", False),
                )

                data.append(
                    {
                        "type": "buffer",
                        "roadlink_id": None,
                        "usrn": None,
                        "road_name": "Route Buffer",
                        "length": None,
                        "start_node": None,
                        "end_node": None,
                    }
                )
                geometries.append(buffered)

        # Create GeoDataFrame
        gdf = gpd.GeoDataFrame(data, geometry=geometries, crs="EPSG:27700")

        if output_crs != "EPSG:27700":
            gdf = gdf.to_crs(output_crs)

        logger.info(f"Created GeoDataFrame with {len(gdf)} features")
        return gdf


# Example usage
async def main():
    """Example of using the async-only RoadLinkObject"""
    async with RoadLinkObject() as road_obj:
        usrn_list = ["23002930", "23002930"]
        road_data = await road_obj.get_road_data_async(usrn_list, batch_size=10)
        print(road_data)


if __name__ == "__main__":
    asyncio.run(main())
