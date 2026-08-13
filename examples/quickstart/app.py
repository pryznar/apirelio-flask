import os
from flask import Flask
from apirelio_flask import install_apirelio

app = Flask(__name__)
install_apirelio(
    app,
    api_key=os.environ.get("APIRELIO_API_KEY", ""),
    service="github-quickstart",
    environment="development",
    resolve_customer=lambda _request: {
        "id": "customer_42", "name": "Acme Europe", "plan": "growth"
    },
)

@app.get("/api/invoices/<invoice_id>")
def invoice(invoice_id):
    return {"id": invoice_id, "status": "paid"}

