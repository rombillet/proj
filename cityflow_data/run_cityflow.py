import cityflow

CONFIG = "config.json"
STEPS = 900

eng = cityflow.Engine(CONFIG, thread_num=1)
for _ in range(STEPS):
    eng.next_step()

print("vehicle_count", eng.get_vehicle_count())
print("average_travel_time", eng.get_average_travel_time())
print("replay written to replay.txt")
