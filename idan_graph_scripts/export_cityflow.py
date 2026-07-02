import argparse
import heapq
import json
import math
import os
import random
from collections import defaultdict


DEFAULT_GRAPH_DIR = "data"
DEFAULT_OUTPUT_DIR = "cityflow_data"
DEFAULT_FLOW_COUNT = 24
DEFAULT_SIM_SECONDS = 900

METERS_PER_LAT = 110_540
METERS_PER_LON = 111_320 * math.cos(math.radians(31.25))


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data, indent=2):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def local_project(lon, lat, origin_lon, origin_lat):
    return {
        "x": round((lon - origin_lon) * METERS_PER_LON, 3),
        "y": round((lat - origin_lat) * METERS_PER_LAT, 3),
    }


def road_lanes(road):
    lanes = road.get("lanes") or [{"width": 3.2, "maxSpeed": 13.89}]
    return lanes[:4]


def angle_between(a, b, c):
    v1 = (a["x"] - b["x"], a["y"] - b["y"])
    v2 = (c["x"] - b["x"], c["y"] - b["y"])
    cross = v1[0] * v2[1] - v1[1] * v2[0]
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    angle = math.degrees(math.atan2(cross, dot))
    if abs(angle) < 35:
        return "go_straight"
    if angle > 0:
        return "turn_left"
    return "turn_right"


def classify_turn(in_road, out_road, intersections):
    center = intersections[in_road["endIntersection"]]["point"]
    in_points = in_road["points"]
    out_points = out_road["points"]
    before = in_points[-2] if len(in_points) > 1 else in_points[-1]
    after = out_points[1] if len(out_points) > 1 else out_points[0]
    return angle_between(before, center, after)


def make_lane_links(in_road, out_road, intersections):
    lane_count = min(len(in_road["lanes"]), len(out_road["lanes"]))
    center = intersections[in_road["endIntersection"]]["point"]
    in_points = in_road["points"]
    out_points = out_road["points"]
    start = in_points[-2] if len(in_points) > 1 else center
    end = out_points[1] if len(out_points) > 1 else center
    return [
        {
            "startLaneIndex": lane_index,
            "endLaneIndex": min(lane_index, len(out_road["lanes"]) - 1),
            "points": [start, center, end],
        }
        for lane_index in range(lane_count)
    ]


def build_cityflow_roadnet(source_roadnet, source_lights):
    source_roads = source_roadnet["roads"]
    source_intersections = source_roadnet["intersections"]
    all_points = [
        point
        for road in source_roads
        for point in road["points"]
    ] + [intersection["point"] for intersection in source_intersections]
    origin_lon = min(point["x"] for point in all_points)
    origin_lat = min(point["y"] for point in all_points)

    signal_nodes = {
        light["nearestNode"]
        for light in source_lights["trafficLights"]
        if light.get("matched")
    }

    intersections = {}
    for intersection in source_intersections:
        projected = local_project(intersection["point"]["x"], intersection["point"]["y"], origin_lon, origin_lat)
        intersections[intersection["id"]] = {
            "id": intersection["id"],
            "point": projected,
            "width": 10,
            "roads": [],
            "roadLinks": [],
            "trafficLight": {"lightphases": []},
            "virtual": intersection.get("virtual", False),
            "hasSignal": intersection["id"] in signal_nodes,
        }

    roads = {}
    for road in source_roads:
        points = [local_project(point["x"], point["y"], origin_lon, origin_lat) for point in road["points"]]
        cityflow_road = {
            "id": road["id"],
            "points": points,
            "startIntersection": road["startIntersection"],
            "endIntersection": road["endIntersection"],
            "lanes": road_lanes(road),
        }
        roads[road["id"]] = cityflow_road
        intersections[road["startIntersection"]]["roads"].append(road["id"])
        intersections[road["endIntersection"]]["roads"].append(road["id"])

    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    for road in roads.values():
        incoming[road["endIntersection"]].append(road)
        outgoing[road["startIntersection"]].append(road)

    for intersection_id, intersection in intersections.items():
        road_links = []
        for in_road in incoming[intersection_id]:
            for out_road in outgoing[intersection_id]:
                if out_road["endIntersection"] == in_road["startIntersection"]:
                    continue
                lane_links = make_lane_links(in_road, out_road, intersections)
                if not lane_links:
                    continue
                road_links.append(
                    {
                        "type": classify_turn(in_road, out_road, intersections),
                        "startRoad": in_road["id"],
                        "endRoad": out_road["id"],
                        "laneLinks": lane_links,
                    }
                )

        intersection["roadLinks"] = road_links
        intersection["roads"] = sorted(set(intersection["roads"]))

        if not road_links:
            intersection["trafficLight"] = {"lightphases": []}
        elif intersection["hasSignal"]:
            intersection["trafficLight"] = {
                "lightphases": [
                    {"time": 30, "availableRoadLinks": [index]}
                    for index in range(len(road_links))
                ]
            }
        else:
            intersection["trafficLight"] = {
                "lightphases": [
                    {"time": 30, "availableRoadLinks": list(range(len(road_links)))}
                ]
            }
        del intersection["hasSignal"]

    return {
        "intersections": list(intersections.values()),
        "roads": list(roads.values()),
    }


def build_road_adjacency(cityflow_roadnet):
    roads = {road["id"]: road for road in cityflow_roadnet["roads"]}
    adjacency = defaultdict(list)
    for intersection in cityflow_roadnet["intersections"]:
        for link in intersection["roadLinks"]:
            adjacency[link["startRoad"]].append(link["endRoad"])
    return roads, adjacency


def shortest_route(start_road, end_road, adjacency, roads):
    queue = [(0.0, start_road, [start_road])]
    best = {start_road: 0.0}
    while queue:
        cost, road_id, route = heapq.heappop(queue)
        if road_id == end_road:
            return route
        if cost > best[road_id]:
            continue
        for next_road in adjacency[road_id]:
            next_cost = cost + road_length(roads[next_road])
            if next_cost < best.get(next_road, float("inf")):
                best[next_road] = next_cost
                heapq.heappush(queue, (next_cost, next_road, route + [next_road]))
    return None


def road_length(road):
    return sum(
        math.hypot(a["x"] - b["x"], a["y"] - b["y"])
        for a, b in zip(road["points"], road["points"][1:])
    )


def default_vehicle():
    return {
        "length": 5.0,
        "width": 2.0,
        "maxPosAcc": 2.0,
        "maxNegAcc": 4.5,
        "usualPosAcc": 2.0,
        "usualNegAcc": 4.5,
        "minGap": 2.5,
        "maxSpeed": 16.67,
        "headwayTime": 1.5,
    }


def build_flow(cityflow_roadnet, flow_count, sim_seconds, seed):
    rng = random.Random(seed)
    roads, adjacency = build_road_adjacency(cityflow_roadnet)
    intersections = {intersection["id"]: intersection for intersection in cityflow_roadnet["intersections"]}

    entry_roads = [
        road["id"]
        for road in roads.values()
        if intersections[road["startIntersection"]]["virtual"] and adjacency[road["id"]]
    ]
    exit_roads = [
        road["id"]
        for road in roads.values()
        if intersections[road["endIntersection"]]["virtual"]
    ]

    if not entry_roads:
        entry_roads = [road_id for road_id, next_roads in adjacency.items() if next_roads]
    if not exit_roads:
        exit_roads = list(roads.keys())

    flows = []
    attempts = 0
    while len(flows) < flow_count and attempts < flow_count * 100:
        attempts += 1
        start = rng.choice(entry_roads)
        end = rng.choice(exit_roads)
        if start == end:
            continue
        route = shortest_route(start, end, adjacency, roads)
        if not route or len(route) < 2 or len(route) > 80:
            continue
        flows.append(
            {
                "vehicle": default_vehicle(),
                "route": route,
                "interval": rng.choice([8.0, 10.0, 12.0, 15.0]),
                "startTime": rng.randint(0, 120),
                "endTime": sim_seconds,
            }
        )

    return flows


def build_config(output_dir, sim_seconds):
    return {
        "interval": 1.0,
        "seed": 0,
        "dir": output_dir + "/",
        "roadnetFile": "roadnet.json",
        "flowFile": "flow.json",
        "rlTrafficLight": False,
        "saveReplay": True,
        "roadnetLogFile": "roadnet_replay.json",
        "replayLogFile": "replay.txt",
        "laneChange": False,
        "endTime": sim_seconds,
    }


def write_runner(output_dir):
    runner = """import cityflow

CONFIG = "config.json"
STEPS = 900

eng = cityflow.Engine(CONFIG, thread_num=1)
for _ in range(STEPS):
    eng.next_step()

print("vehicle_count", eng.get_vehicle_count())
print("average_travel_time", eng.get_average_travel_time())
print("replay written to replay.txt")
"""
    with open(os.path.join(output_dir, "run_cityflow.py"), "w", encoding="utf-8") as f:
        f.write(runner)


def export_cityflow(graph_dir, output_dir, flow_count, sim_seconds, seed):
    source_roadnet = load_json(os.path.join(graph_dir, "roadnet.json"))
    source_lights = load_json(os.path.join(graph_dir, "traffic_lights.json"))
    os.makedirs(output_dir, exist_ok=True)

    roadnet = build_cityflow_roadnet(source_roadnet, source_lights)
    flow = build_flow(roadnet, flow_count, sim_seconds, seed)
    config = build_config(output_dir, sim_seconds)

    write_json(os.path.join(output_dir, "roadnet.json"), roadnet)
    write_json(os.path.join(output_dir, "flow.json"), flow)
    write_json(os.path.join(output_dir, "config.json"), config)
    write_runner(output_dir)

    summary = {
        "intersections": len(roadnet["intersections"]),
        "roads": len(roadnet["roads"]),
        "roadLinks": sum(len(intersection["roadLinks"]) for intersection in roadnet["intersections"]),
        "trafficLightIntersections": sum(
            1 for intersection in roadnet["intersections"] if len(intersection["trafficLight"]["lightphases"]) > 1
        ),
        "flows": len(flow),
        "simSeconds": sim_seconds,
    }
    write_json(os.path.join(output_dir, "summary.json"), summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Export generated Be'er Sheva graph files to CityFlow input files.")
    parser.add_argument("--graph-dir", default=DEFAULT_GRAPH_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--flow-count", type=int, default=DEFAULT_FLOW_COUNT)
    parser.add_argument("--sim-seconds", type=int, default=DEFAULT_SIM_SECONDS)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    summary = export_cityflow(args.graph_dir, args.output_dir, args.flow_count, args.sim_seconds, args.seed)
    print("Generated CityFlow files:")
    print(f"  {args.output_dir}/roadnet.json")
    print(f"  {args.output_dir}/flow.json")
    print(f"  {args.output_dir}/config.json")
    print(f"  {args.output_dir}/run_cityflow.py")
    print("Summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print("Note: replay.txt is generated by CityFlow after running run_cityflow.py.")


if __name__ == "__main__":
    main()
