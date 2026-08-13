# Apirelio Flask SDK

[![PyPI](https://img.shields.io/pypi/v/apirelio-flask?style=flat-square&logo=pypi)](https://pypi.org/project/apirelio-flask/)
[![Live demo](https://img.shields.io/badge/live_demo-explore-8EF0B5?style=flat-square&logo=googlechrome&logoColor=0B0E10)](https://apirelio.com/demo?utm_source=github&utm_medium=readme&utm_campaign=flask)

## See the customer behind every API request

[![Apirelio live demo dashboard](https://apirelio.com/img/apirelio-live-demo-dashboard.jpg)](https://apirelio.com/demo?utm_source=github&utm_medium=readme&utm_campaign=flask)

Follow a release regression from the failing endpoint to the exact customer accounts it affects in the public, read-only workspace.

**[Explore the live demo →](https://apirelio.com/demo?utm_source=github&utm_medium=readme&utm_campaign=flask)**

## Try it in 30 seconds

```bash
pip install apirelio-flask
export APIRELIO_API_KEY=apr_live_your_project_key
```

Copy the minimal setup below or run the [quickstart example](./examples/quickstart). Delivery is fail-safe and no request or response payloads are captured.


[Documentation](https://apirelio.com/docs/python/flask) · [PyPI](https://pypi.org/project/apirelio-flask/) · [Apirelio](https://apirelio.com)

> Connect Flask errors, latency and releases to affected customers without capturing request or response payloads.

Native Flask request-lifecycle instrumentation for privacy-safe, customer-aware API analytics. It records the resolved URL rule and only enqueues after Flask has produced the response.

```bash
pip install apirelio-flask
```

```python
import os

from flask import Flask, g
from apirelio_flask import install_apirelio

app = Flask(__name__)

install_apirelio(
    app,
    api_key=os.environ.get("APIRELIO_API_KEY", ""),
    endpoint=os.environ.get("APIRELIO_ENDPOINT", "https://apirelio.com"),
    service="billing-api",
    environment="production",
    include_routes=("/api/**",),
    exclude_routes=("/api/health", "/api/internal/**"),
    resolve_customer=lambda request: {
        "id": str(g.account.id),
        "name": g.account.name,
        "plan": g.account.plan,
    } if getattr(g, "account", None) else None,
)
```

Authentication must run before Apirelio's `after_request` resolver executes. Use `get_request_context()` inside a view to attach allow-listed scalar metadata or a stable error code.

The extension stores itself at `app.extensions["apirelio"]`. Bodies, query strings, cookies, authorization values, email addresses and IP addresses are never captured. Python 3.9+, Flask 2.3 and Flask 3.x are supported.
