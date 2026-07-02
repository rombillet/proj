import argparse
import json
import math
import os
import re
from collections import defaultdict


DEFAULT_ROADS_FILE = "export (1).geojson"
DEFAULT_LIGHTS_FILE = "light-traffics.geojson"
DEFAULT_OUTPUT_DIR = "data"

NODE_PRECISION = 7
LIGHT_MATCH_RADIUS_M = 40

METERS_PER_LAT = 110_540
METERS_PER_LON = 111_320 * math.cos(math.radians(31.25))


def load_geojson(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    # light-traffics.geojson contains one [inf, inf] point. JSON parsers in
    # browsers reject it, so normalize it here too.
    return json.loads(re.sub(r"\b-?inf(?:inity)?\b", "null", text, flags=re.IGNORECASE))


def iter_positions(coordinates):
    if isinstance(coordinates, list) and coordinates and isinstance(coordinates[0], (int, float)):
        yield coordinates
    elif isinstance(coordinates, list):
        for item in coordinates:
            yield from iter_positions(item)


def is_valid_lon_lat(lon, lat):
    return isinstance(lon, (int, float)) and isinstance(lat, (int, float)) and math.isfinite(lon) and math.isfinite(lat)


def node_id(lon, lat):
    return f"n:{round(lon, NODE_PRECISION)}:{round(lat, NODE_PRECISION)}"


def project(lon, lat):
    return lon * METERS_PER_LON, lat * METERS_PER_LAT


def distance_m(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def parse_maxspeed(value, fallback=13.89):
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if not text:
        return fallback
    first = text.split(";")[0].split()[0]
    try:
        speed = float(first)
    except ValueError:
        return fallback
    if "mph" in text:
        return speed * 0.44704
    return speed / 3.6


def parse_lanes(value, fallback=1):
    try:
        return max(1, int(float(str(value).split(";")[0])))
    except (TypeError, ValueError):
        return fallback


def edge_length_m(points):
    projected = [project(lon, lat) for lon, lat in points]
    return sum(distance_m(a, b) for a, b in zip(projected, projected[1:]))


def add_edge(graph_edges, roadnet_roads, start_id, end_id, points, props, suffix):
    road_id = f"{props.get('@id', 'road').replace('/', '_')}:{suffix}"
    length = edge_length_m(points)
    max_speed = parse_maxspeed(props.get("maxspeed"))
    lanes_count = parse_lanes(props.get("lanes"))

    graph_edges.append(
        {
            "source": start_id,
            "target": end_id,
            "id": road_id,
            "osm_id": props.get("@id"),
            "highway": props.get("highway"),
            "name": props.get("name:en") or props.get("name"),
            "length_m": round(length, 2),
            "max_speed_mps": round(max_speed, 2),
            "lanes": lanes_count,
        }
    )

    roadnet_roads.append(
        {
            "id": road_id,
            "osmId": props.get("@id"),
            "name": props.get("name:en") or props.get("name"),
            "highway": props.get("highway"),
            "points": [{"x": lon, "y": lat} for lon, lat in points],
            "startIntersection": start_id,
            "endIntersection": end_id,
            "length": round(length, 2),
            "lanes": [{"width": 3.2, "maxSpeed": round(max_speed, 2)} for _ in range(lanes_count)],
        }
    )


def road_feature_key(feature, index):
    return (feature.get("properties") or {}).get("@id") or f"feature:{index}"


def collect_road_topology(roads_geojson):
    directed_segments = []
    coord_by_id = {}
    neighbors = defaultdict(set)

    for feature_index, feature in enumerate(roads_geojson.get("features", [])):
        props = feature.get("properties", {})
        coords = list(iter_positions((feature.get("geometry") or {}).get("coordinates")))
        points = [(p[0], p[1]) for p in coords if len(p) >= 2 and is_valid_lon_lat(p[0], p[1])]
        if len(points) < 2:
            continue

        for lon, lat in points:
            nid = node_id(lon, lat)
            coord_by_id.setdefault(nid, (lon, lat))

        oneway = str(props.get("oneway", "")).lower()
        feature_key = road_feature_key(feature, feature_index).replace("/", "_")
        for segment_index, (start, end) in enumerate(zip(points, points[1:])):
            start_id = node_id(*start)
            end_id = node_id(*end)
            if start_id == end_id:
                continue

            neighbors[start_id].add(end_id)
            neighbors[end_id].add(start_id)

            if oneway == "-1":
                directed_segments.append(
                    {
                        "id": f"{feature_key}:{segment_index}r",
                        "source": end_id,
                        "target": start_id,
                        "points": [end, start],
                        "props": props,
                    }
                )
            else:
                directed_segments.append(
                    {
                        "id": f"{feature_key}:{segment_index}f",
                        "source": start_id,
                        "target": end_id,
                        "points": [start, end],
                        "props": props,
                    }
                )
                if oneway not in {"yes", "true", "1"}:
                    directed_segments.append(
                        {
                            "id": f"{feature_key}:{segment_index}r",
                            "source": end_id,
                            "target": start_id,
                            "points": [end, start],
                            "props": props,
                        }
                    )

    important_node_ids = {nid for nid in coord_by_id if len(neighbors[nid]) != 2}
    return directed_segments, coord_by_id, important_node_ids


def build_road_graph(roads_geojson):
    nodes = {}
    graph_edges = []
    roadnet_roads = []
    incoming = defaultdict(list)
    outgoing = defaultdict(list)

    directed_segments, coord_by_id, important_node_ids = collect_road_topology(roads_geojson)
    for nid in important_node_ids:
        lon, lat = coord_by_id[nid]
        nodes[nid] = {"id": nid, "lon": lon, "lat": lat, "x": lon, "y": lat}

    outgoing_segments = defaultdict(list)
    for segment in directed_segments:
        outgoing_segments[segment["source"]].append(segment)

    visited = set()
    chain_index = 0
    for segment in directed_segments:
        if segment["id"] in visited or segment["source"] not in important_node_ids:
            continue

        chain_segments = [segment]
        visited.add(segment["id"])
        previous = segment["source"]
        current = segment["target"]

        while current not in important_node_ids:
            candidates = [
                candidate
                for candidate in outgoing_segments[current]
                if candidate["id"] not in visited and candidate["target"] != previous
            ]
            if len(candidates) != 1:
                break
            next_segment = candidates[0]
            chain_segments.append(next_segment)
            visited.add(next_segment["id"])
            previous = current
            current = next_segment["target"]

        start_id = chain_segments[0]["source"]
        end_id = chain_segments[-1]["target"]
        if start_id == end_id or start_id not in nodes or end_id not in nodes:
            continue

        points = list(chain_segments[0]["points"])
        for next_segment in chain_segments[1:]:
            points.extend(next_segment["points"][1:])

        props = chain_segments[0]["props"]
        add_edge(graph_edges, roadnet_roads, start_id, end_id, points, props, f"chain{chain_index}")
        outgoing[start_id].append(roadnet_roads[-1]["id"])
        incoming[end_id].append(roadnet_roads[-1]["id"])
        chain_index += 1

    return nodes, graph_edges, roadnet_roads, incoming, outgoing


def attach_traffic_lights(lights_geojson, nodes):
    projected_nodes = [(nid, project(node["lon"], node["lat"])) for nid, node in nodes.items()]
    lights = []
    node_to_lights = defaultdict(list)

    for index, feature in enumerate(lights_geojson.get("features", [])):
        coords = (feature.get("geometry") or {}).get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2 or not is_valid_lon_lat(coords[0], coords[1]):
            continue

        lon, lat = coords[0], coords[1]
        light_number = str((feature.get("properties") or {}).get("LightNumbe") or "").strip()
        point = project(lon, lat)
        nearest_id, nearest_distance = min(
            ((nid, distance_m(point, node_point)) for nid, node_point in projected_nodes),
            key=lambda item: item[1],
        )
        matched = nearest_distance <= LIGHT_MATCH_RADIUS_M
        light = {
            "id": f"light:{index}",
            "lightNumber": light_number,
            "lon": lon,
            "lat": lat,
            "nearestNode": nearest_id,
            "distanceToNodeM": round(nearest_distance, 2),
            "matched": matched,
        }
        lights.append(light)
        if matched:
            node_to_lights[nearest_id].append(light["id"])

    return lights, node_to_lights


def make_intersections(nodes, incoming, outgoing, node_to_lights):
    intersections = []
    for nid, node in nodes.items():
        roads = sorted(set(incoming[nid] + outgoing[nid]))
        has_light = nid in node_to_lights
        intersections.append(
            {
                "id": nid,
                "point": {"x": node["lon"], "y": node["lat"]},
                "roads": roads,
                "virtual": len(roads) <= 1 and not has_light,
                "width": 10,
                "roadLinks": [],
                "trafficLight": {
                    "lightIds": node_to_lights.get(nid, []),
                    "lightphases": [{"time": 30, "availableRoadLinks": []}] if has_light else [],
                },
            }
        )
    return intersections


def write_outputs(output_dir, nodes, graph_edges, roadnet_roads, intersections, traffic_lights):
    os.makedirs(output_dir, exist_ok=True)

    graph_data = {
        "directed": True,
        "multigraph": True,
        "graph": {"name": "beer_sheva_roads"},
        "nodes": list(nodes.values()),
        "links": graph_edges,
    }
    roadnet = {"intersections": intersections, "roads": roadnet_roads}
    summary = {
        "nodes": len(nodes),
        "directedEdges": len(graph_edges),
        "roads": len(roadnet_roads),
        "trafficLights": len(traffic_lights),
        "matchedTrafficLights": sum(1 for light in traffic_lights if light["matched"]),
        "unmatchedTrafficLights": sum(1 for light in traffic_lights if not light["matched"]),
        "matchedSignalNodes": len({light["nearestNode"] for light in traffic_lights if light["matched"]}),
    }

    files = {
        "graph_data": os.path.join(output_dir, "graph_data.json"),
        "roadnet": os.path.join(output_dir, "roadnet.json"),
        "traffic_lights": os.path.join(output_dir, "traffic_lights.json"),
        "summary": os.path.join(output_dir, "summary.json"),
    }
    with open(files["graph_data"], "w", encoding="utf-8") as f:
        json.dump(graph_data, f, ensure_ascii=False)
    with open(files["roadnet"], "w", encoding="utf-8") as f:
        json.dump(roadnet, f, ensure_ascii=False, indent=2)
    with open(files["traffic_lights"], "w", encoding="utf-8") as f:
        json.dump({"trafficLights": traffic_lights}, f, ensure_ascii=False, indent=2)
    with open(files["summary"], "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return files, summary


def build_graph(roads_file, lights_file, output_dir):
    roads_geojson = load_geojson(roads_file)
    lights_geojson = load_geojson(lights_file)
    nodes, graph_edges, roadnet_roads, incoming, outgoing = build_road_graph(roads_geojson)
    traffic_lights, node_to_lights = attach_traffic_lights(lights_geojson, nodes)
    intersections = make_intersections(nodes, incoming, outgoing, node_to_lights)
    return write_outputs(output_dir, nodes, graph_edges, roadnet_roads, intersections, traffic_lights)


def main():
    parser = argparse.ArgumentParser(description="Build a simple Be'er Sheva graph from local GeoJSON files.")
    parser.add_argument("--roads", default=DEFAULT_ROADS_FILE, help="Road GeoJSON file.")
    parser.add_argument("--lights", default=DEFAULT_LIGHTS_FILE, help="Traffic-light GeoJSON file.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for generated JSON files.")
    args = parser.parse_args()

    files, summary = build_graph(args.roads, args.lights, args.output_dir)
    print("Generated graph files:")
    for name, path in files.items():
        print(f"  {name}: {path}")
    print("Summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
