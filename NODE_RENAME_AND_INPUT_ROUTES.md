# NODE Rename and Input Routes Plan

Rename `Node_Temp_Node` globally to `NODE`.

NODE = corpus worker / gatherer / verifier / corpus store / truth-gate runner / LLaMA candidate preparation repo.

Current GitHub repo:
https://github.com/Racer-01111000/Node_Temp_Node

Target GitHub repo:
https://github.com/Racer-01111000/NODE

Current local paths:
HOST: /home/rick/Node_Temp_Node
NODE: /home/rick/Node_Temp_Node

Desired local paths:
HOST: /home/rick/NODE
NODE: /home/rick/NODE

Do not rename GPT_Firefox_extension.

GPT_Firefox_extension = bridge / control surface / native host / worker loop.
NODE = workload repo / corpus / retrieval / validation / truth gate / LLaMA candidate preparation.

Required replacements:
Node_Temp_Node -> NODE
Node_Temp -> NODE where it refers to the current project
/home/rick/Node_Temp_Node -> /home/rick/NODE

Update NODE_WORKLOAD root to:
/home/rick/NODE

Manual Ingest needs three controls:

1. Upload from HOST
- Browser file picker uploads through the tunnel to NODE.
- Endpoint: POST /api/upload-file
- Store under: /home/rick/incoming/uploads/YYYYMMDD_HHMMSS_<safe_filename>
- After upload, auto-fill Source path.

2. Search NODE files
- Rename current button to: Search NODE files
- Endpoint: GET /api/search-files?q=<query>
- Approved roots: /home/rick/incoming and /home/rick/NODE
- This is NODE-side search, not a HOST file browser.

3. Fetch URL with w3m
- Endpoint: POST /api/queue-w3m-fetch
- Writes bridge task to: /home/rick/GPT_Firefox_extension/control/inbox/current.json
- Target: NODE_WORKLOAD
- Command: python3 w3m_fetch.py --url "<url>" --tags "<tags>"
- Output: /home/rick/NODE/staging/web_capture/<job_id>/
- w3m is the live validation and extraction layer all the time.

Acceptance routes:

Route A — File route:
HOST upload OR NODE file search -> manual_ingest.py -> staging/manual/<job_id> -> truth gate

Route B — Web route:
URL -> w3m_fetch.py -> staging/web_capture/<job_id> -> truth gate

Neither route auto-promotes.

Truth doctrine:
Rick approves system actions and scope.
Rick is not the truth standard.
Promotion requires two verified supporting sources OR two indirectly supporting facts.
Pipeline remains: acquisition != evidence != sufficiency != promotion.

Semantic Scholar rate limit:
min_seconds_between_requests = 62
requests_per_minute = 1
IP-bound key
key loaded only from local env/config
never committed or logged

Do not:
- use browser unrestricted filesystem access
- search /home/rick/.ssh, .config, .local, .git, secrets, tokens, key files
- auto-promote uploaded files
- auto-promote w3m captures
- commit API keys
- rename GPT_Firefox_extension
