import json
import urllib.request
import os

DASHBOARDS_DIR = os.path.join(os.path.dirname(__file__), "..", "config", "grafana", "provisioning", "dashboards")

def reload_dashboard(filename):
    path = os.path.join(DASHBOARDS_DIR, filename)
    if not os.path.exists(path):
        print(f"File {path} not found.")
        return

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    payload = {
        "dashboard": data,
        "overwrite": True,
        "folderUid": "dg1s8jc068hdsd"
    }

    req = urllib.request.Request(
        "http://localhost:3002/api/dashboards/db",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic YWRtaW46YWRtaW4="  # admin:admin
        }
    )

    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            print(f"Successfully updated Grafana dashboard ({filename}):", res_body)
    except Exception as e:
        print(f"Error updating dashboard ({filename}):", e)

if __name__ == "__main__":
    reload_dashboard("fraud_dashboard.json")
    reload_dashboard("geo_dashboard.json")
