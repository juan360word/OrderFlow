"""Background workers.

These run as their own OS processes, not as threads inside the API. Three
reasons that matters:

* A slow or wedged consumer cannot degrade request latency, because it is not
  competing for the API's event loop.
* They scale independently: a backlog is absorbed by starting more workers
  without touching the web tier.
* They restart independently. Deploying a fix to the email handler does not
  drop in-flight HTTP requests.
"""
