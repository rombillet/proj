
# import os
# import requests
# import subprocess
# import sumolib
# import networkx as nx
# import json
# from networkx.readwrite import json_graph


# def download_osm_data(osm_file):
#     if os.path.exists(osm_file):
#         print(f"[{osm_file}] exists. Skipping download.")
#         return True
    
#     print(f"[{osm_file}] not found. Downloading...")
#     bbox = "40.765, -73.970, 40.775, -73.955"
#     query = f"""[out:xml];(way["highway"]({bbox}););(._;>;);out body;"""
#     url = "https://overpass-api.de/api/interpreter"
#     headers = {'User-Agent': 'TrafficOptimizationProject_GNN/1.0'}
    
#     response = requests.post(url, data={'data': query}, headers=headers)
#     if response.status_code == 200:
#         with open(osm_file, "w", encoding="utf-8") as f:
#             f.write(response.text)
#         return True
#     return False


# def build_graph_pipeline():
#     osm_file = "manhattan.osm"
#     sumo_file = "manhattan.net.xml"
#     graph_data_file = "graph_data.json"
#     roadnet_file = "roadnet.json"
    
    
#     # 1. Download & Conversion Logic
#     if not os.path.exists(sumo_file):
#         # Trigger download
#         if not download_osm_data(osm_file):
#             return

#         # Run netconvert
#         print("Converting OSM to SUMO format...")
#         subprocess.run([
#             "netconvert", "--osm-files", osm_file, "--output-file", sumo_file,
#             "--geometry.remove", "--tls.guess-signals", "--junctions.join"
#         ], check=True)

#     # 2. Extract GNN-ready Graph & Simulator Roadnet
#     print("Extracting graph and building roadnet...")
#     net = sumolib.net.readNet(sumo_file)
#     G = nx.DiGraph()
#     roadnet = {"intersections": [], "roads": []}

#     for node in net.getNodes():
#         G.add_node(node.getID(), pos=node.getCoord())
        
#         # Determine attached roads
#         attached_roads = [edge.getID() for edge in node.getIncoming() + node.getOutgoing()]
        
#         # A node is typically virtual if it's a dead-end or only connects to one road
#         is_virtual = (node.getType() == "dead_end") or (len(attached_roads) <= 1)
        
#         roadnet["intersections"].append({
#             "id": node.getID(),
#             "point": {"x": node.getCoord()[0], "y": node.getCoord()[1]},
#             "roads": attached_roads, # Mandatory array of road IDs
#             "virtual": is_virtual,
#             "width": 10, # Mandatory width
#             "roadLinks": [], # Required, ideally populated with connection logic
#             "trafficLight": {"lightphases": []} 
#         })

#     for edge in net.getEdges():
#         if edge.getFromNode() and edge.getToNode():
#             shape = edge.getShape() # Extract actual geometry
#             points = [{"x": p[0], "y": p[1]} for p in shape]
            
#             # Add to NetworkX
#             G.add_edge(edge.getFromNode().getID(), edge.getToNode().getID(), 
#                        id=edge.getID(), length=edge.getLength())
            
#             # Add to roadnet
#             roadnet["roads"].append({
#                 "id": edge.getID(),
#                 "points": points,
#                 "startIntersection": edge.getFromNode().getID(),
#                 "endIntersection": edge.getToNode().getID(),
#                 "lanes": [{"width": 3.2, "maxSpeed": 11.11}] # camelCase maxSpeed, added width
#             })

#     # Save outputs
#     with open(graph_data_file, "w") as f:
#         json.dump(json_graph.node_link_data(G), f)
#     with open(roadnet_file, "w") as f:
#         json.dump(roadnet, f, indent=4)
        
#     files_to_cleanup = [osm_file]
#     print("Cleaning up temporary files...")
#     for f in files_to_cleanup:
#         if os.path.exists(f):
#             os.remove(f)
#             print(f"Removed temporary file: {f}")
        
#     print(f"Pipeline complete: {graph_data_file} and {roadnet_file} created.")

# if __name__ == "__main__":
#     build_graph_pipeline()



import os
import requests
import subprocess
import sumolib
import networkx as nx
import json
from networkx.readwrite import json_graph

# Configuration dictionary mapping cities to their Overpass API bounding box coordinates: (min_lat, min_lon, max_lat, max_lon)
# Optimized Configuration Dictionary
CITY_CONFIGS = {
    "manhattan": {
        "bbox": "40.765,-73.970,40.775,-73.955",
        "highway_filter": 'way["highway"~"motorway|trunk|primary|secondary|tertiary|residential"]',
        "display_name": "Manhattan"
    },
    "beer_sheva": {
        "bbox": "31.230,34.770,31.270,34.810",
        # Focus strictly on drivable roads to prevent 504 Gateway Timeouts
        "highway_filter": 'way["highway"~"motorway|trunk|primary|secondary|tertiary|residential"]',
        "display_name": "Be'er Sheva"
    },
    "tel_aviv": {
        "bbox": "32.060,34.760,32.090,34.790",
        "highway_filter": 'way["highway"~"motorway|trunk|primary|secondary|tertiary|residential"]',
        "display_name": "Tel Aviv"
    }
}


def download_osm_data(osm_file, bbox, highway_filter):
    if os.path.exists(osm_file):
        print(f"[{osm_file}] exists. Skipping download.")
        return True
    
    print(f"[{osm_file}] not found. Downloading raw OSM coordinates (Optimized Drivable Roads)...")
    
    # Added [timeout:180] and dynamic highway filtering
    query = f"""[out:xml][timeout:180];({highway_filter}({bbox}););(._;>;);out body;"""
    
    # Using a reliable alternative mirror if the main one is congested
    url = "https://maps.mail.ru/osm/tools/overpass/api/interpreter" 
    # Backup alternative: "https://overpass-api.de/api/interpreter"
    
    headers = {'User-Agent': 'TrafficOptimizationProject_GNN/1.0'}
    
    try:
        response = requests.post(url, data={'data': query}, headers=headers, timeout=200)
        if response.status_code == 200:
            with open(osm_file, "w", encoding="utf-8") as f:
                f.write(response.text)
            print(f"Successfully saved raw data to {osm_file}")
            return True
        print(f"Error downloading data. Status code: {response.status_code}")
    except requests.exceptions.Timeout:
        print("Client-side timeout reached while waiting for Overpass API response.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        
    return False



def build_graph_pipeline(city_key):
    if city_key not in CITY_CONFIGS:
        raise ValueError(f"City key '{city_key}' is not defined in CITY_CONFIGS. Available: {list(CITY_CONFIGS.keys())}")
        
    config = CITY_CONFIGS[city_key]
    print(f"\n================ Starting Pipeline for: {config['display_name']} ================")
    
    # Define dynamic filenames based on target city
    osm_file = f"data/{city_key}.osm"
    sumo_file = f"data/{city_key}.net.xml"
    graph_data_file = "data/graph_data.json"
    roadnet_file = "data/roadnet.json"
    
    # Ensure local directory exists
    os.makedirs("data", exist_ok=True)
    
    # 1. Download & Conversion Logic
    # Change this line inside build_graph_pipeline:
    if not os.path.exists(sumo_file):
        if not download_osm_data(osm_file, config["bbox"], config["highway_filter"]):
            return
    
        print(f"Compiling {city_key} network via netconvert...")
        subprocess.run([
            "netconvert", 
            "--osm-files", osm_file, 
            "--output-file", sumo_file,
            "--geometry.remove", 
            "--tls.guess-signals", 
            "--junctions.join",
            "--roundabouts.guess", "true"  # Crucial optimization for Israeli urban layouts
        ], check=True)

    # 2. Extract GNN-ready Graph & Simulator Roadnet
    print("Extracting topology structures and building compliant JSON schemas...")
    net = sumolib.net.readNet(sumo_file)
    G = nx.DiGraph()
    roadnet = {"intersections": [], "roads": []}

    for node in net.getNodes():
        G.add_node(node.getID(), pos=node.getCoord())
        attached_roads = [edge.getID() for edge in node.getIncoming() + node.getOutgoing()]
        is_virtual = (node.getType() == "dead_end") or (len(attached_roads) <= 1)
        
        roadnet["intersections"].append({
            "id": node.getID(),
            "point": {"x": node.getCoord()[0], "y": node.getCoord()[1]},
            "roads": attached_roads,
            "virtual": is_virtual,
            "width": 10,
            "roadLinks": [], 
            "trafficLight": {"lightphases": []} 
        })

    for edge in net.getEdges():
        if edge.getFromNode() and edge.getToNode():
            shape = edge.getShape()
            points = [{"x": p[0], "y": p[1]} for p in shape]
            
            G.add_edge(edge.getFromNode().getID(), edge.getToNode().getID(), 
                       id=edge.getID(), length=edge.getLength())
            
            roadnet["roads"].append({
                "id": edge.getID(),
                "points": points,
                "startIntersection": edge.getFromNode().getID(),
                "endIntersection": edge.getToNode().getID(),
                "lanes": [{"width": 3.2, "maxSpeed": 11.11}]
            })

    # Save finalized output artifacts
    with open(graph_data_file, "w") as f:
        json.dump(json_graph.node_link_data(G), f)
    with open(roadnet_file, "w") as f:
        json.dump(roadnet, f, indent=4)
        
    # Clean up massive raw OSM files to protect cluster storage limits
    if os.path.exists(osm_file):
        os.remove(osm_file)
        print(f"Cleaned up raw temporary file: {osm_file}")
        
    print(f"Pipeline complete: {graph_data_file} and {roadnet_file} generated for {config['display_name']}.")

if __name__ == "__main__":
    # Choose your target city: "manhattan", "beer_sheva", or "tel_aviv"
    target_city = "beer_sheva"
    build_graph_pipeline(target_city)
