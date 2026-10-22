
import os
import base64

os.environ["SITE_PASSWORD"] = "local-test-only"

import app_phase1_candidate_v3 as app_module

client = app_module.app.test_client()

credentials = base64.b64encode(
    b"viewer:local-test-only"
).decode()

headers = {
    "Authorization": f"Basic {credentials}"
}

default_response = client.get("/", headers=headers)

budget_response = client.get(
    "/?min_value=0&max_value=10000000",
    headers=headers
)

default_html = default_response.get_data(as_text=True)
budget_html = budget_response.get_data(as_text=True)

print("Default page status:", default_response.status_code)
print("Budget-filtered page status:", budget_response.status_code)
print("Default result count:", default_html.count('class="card"'))
print("Up to ₹1 crore result count:", budget_html.count('class="card"'))
print(
    "Budget slider labels present:",
    "Minimum" in budget_html and "Maximum" in budget_html
)
