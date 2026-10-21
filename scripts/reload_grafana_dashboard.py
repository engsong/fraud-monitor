import json
import urllib.request
import os

DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "grafana", "provisioning", "dashboards", "fraud_dashboard.json")

def reload_dashboard():
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
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
            print("Successfully updated Grafana dashboard:", res_body)
    except Exception as e:
        print("Error updating dashboard:", e)

if __name__ == "__main__":
    reload_dashboard()
