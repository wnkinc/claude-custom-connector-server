import copy
import http.server
import logging
import os
import socketserver
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import sys

import httpx
import requests
from fastmcp import FastMCP
from oauthlib.oauth1 import Client as OAuth1Client
from requests_oauthlib import OAuth1Session

# PATCHED (mcp-tools): make the repo root importable regardless of the process CWD
# (systemd runs us from the tool dir), then pull in the shared security helpers used
# by every public-facing mcp-tools server.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from security.serve import serve  # noqa: E402

HTTP_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "options",
    "head",
    "trace",
}

# THREAT-MODEL L1 (minimize the grant): code-enforced read-only default. The safe
# grant lives HERE, in version control -- not solely in a gitignored `.env`. An
# empty/missing `X_API_TOOL_ALLOWLIST` falls back to exactly these read ops (NOT
# "expose everything"), so a misconfigured deploy fails closed to read-only instead
# of silently exposing all 165 X operations (68 of them writes). `.env` may still
# set `X_API_TOOL_ALLOWLIST` to NARROW or customize this.
DEFAULT_READ_ALLOWLIST = {
    "searchPostsRecent",
    "getPostsById",
    "getPostsByIds",
    "getUsersByUsername",
    "getUsersByUsernames",
    "getUsersById",
    "searchUsers",
    "getPostsCountsRecent",
}

LOGGER = logging.getLogger("xmcp.x_api")
OAUTH_LOGGER = logging.getLogger("xmcp.oauth1")

REQUEST_TOKEN_URL = "https://api.x.com/oauth/request_token"
AUTHORIZE_URL = "https://api.x.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://api.x.com/oauth/access_token"


def is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_csv_env(key: str) -> set[str]:
    raw = os.getenv(key, "")
    if not raw.strip():
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def should_join_query_param(param: dict) -> bool:
    if param.get("in") != "query":
        return False
    schema = param.get("schema", {})
    if schema.get("type") != "array":
        return False
    return param.get("explode") is False


def collect_comma_params(spec: dict) -> set[str]:
    comma_params: set[str] = set()
    components = spec.get("components", {}).get("parameters", {})
    for param in components.values():
        if isinstance(param, dict) and should_join_query_param(param):
            name = param.get("name")
            if isinstance(name, str):
                comma_params.add(name)

    for item in spec.get("paths", {}).values():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            for param in operation.get("parameters", []):
                if not isinstance(param, dict) or "$ref" in param:
                    continue
                if should_join_query_param(param):
                    name = param.get("name")
                    if isinstance(name, str):
                        comma_params.add(name)

    return comma_params


def load_openapi_spec() -> dict:
    url = "https://api.x.com/2/openapi.json"
    LOGGER.info("Fetching OpenAPI spec from %s", url)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def _get_env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{key} must be an integer value.")


def _callback_url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def _wait_for_callback(host: str, port: int, path: str, timeout_seconds: int) -> tuple[str, str]:
    params: dict[str, str | None] = {"oauth_token": None, "oauth_verifier": None}
    event = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found.")
                return
            query = urllib.parse.parse_qs(parsed.query)
            params["oauth_token"] = (query.get("oauth_token") or [None])[0]
            params["oauth_verifier"] = (query.get("oauth_verifier") or [None])[0]
            event.set()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OAuth complete. You may close this tab.")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            OAUTH_LOGGER.debug("OAuth1 callback: " + format, *args)

    class _Server(socketserver.TCPServer):
        allow_reuse_address = True

    server = _Server((host, port), _Handler)
    server.timeout = 1

    deadline = time.time() + timeout_seconds
    try:
        while time.time() < deadline:
            server.handle_request()
            if event.is_set():
                break
    finally:
        server.server_close()

    oauth_token = params.get("oauth_token")
    oauth_verifier = params.get("oauth_verifier")
    if not oauth_token or not oauth_verifier:
        raise TimeoutError("OAuth callback not received before timeout.")
    return oauth_token, oauth_verifier


def run_oauth1_flow() -> tuple[str, str]:
    consumer_key = os.getenv("X_OAUTH_CONSUMER_KEY")
    consumer_secret = os.getenv("X_OAUTH_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        raise RuntimeError(
            "Missing X_OAUTH_CONSUMER_KEY or X_OAUTH_CONSUMER_SECRET for OAuth1 flow."
        )

    callback_host = os.getenv("X_OAUTH_CALLBACK_HOST", "127.0.0.1")
    callback_port = _get_env_int("X_OAUTH_CALLBACK_PORT", 8976)
    callback_path = os.getenv("X_OAUTH_CALLBACK_PATH", "/oauth/callback")
    callback_timeout = _get_env_int("X_OAUTH_CALLBACK_TIMEOUT", 300)

    callback_url = _callback_url(callback_host, callback_port, callback_path)

    oauth = OAuth1Session(
        client_key=consumer_key,
        client_secret=consumer_secret,
        callback_uri=callback_url,
    )
    request_token = oauth.fetch_request_token(REQUEST_TOKEN_URL)
    resource_owner_key = request_token.get("oauth_token")
    resource_owner_secret = request_token.get("oauth_token_secret")
    if not resource_owner_key or not resource_owner_secret:
        raise RuntimeError("Failed to obtain OAuth request token.")

    authorization_url = oauth.authorization_url(AUTHORIZE_URL)
    OAUTH_LOGGER.info("Opening browser for OAuth1 consent.")
    webbrowser.open(authorization_url)

    oauth_token, oauth_verifier = _wait_for_callback(
        callback_host, callback_port, callback_path, callback_timeout
    )
    if oauth_token != resource_owner_key:
        raise RuntimeError("OAuth callback token does not match request token.")

    oauth = OAuth1Session(
        client_key=consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=resource_owner_key,
        resource_owner_secret=resource_owner_secret,
        verifier=oauth_verifier,
    )
    access_token = oauth.fetch_access_token(ACCESS_TOKEN_URL)
    access_key = access_token.get("oauth_token")
    access_secret = access_token.get("oauth_token_secret")
    if not access_key or not access_secret:
        raise RuntimeError("Failed to obtain OAuth access token.")
    return access_key, access_secret


def load_env() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=True)


def setup_logging() -> bool:
    debug_enabled = is_truthy(os.getenv("X_API_DEBUG", "1"))
    if debug_enabled:
        logging.basicConfig(level=logging.INFO)
        LOGGER.setLevel(logging.INFO)
    return debug_enabled


def should_exclude_operation(path: str, operation: dict) -> bool:
    if "/webhooks" in path or "/stream" in path:
        return True

    tags = [tag.lower() for tag in operation.get("tags", []) if isinstance(tag, str)]
    if "stream" in tags or "webhooks" in tags:
        return True

    if operation.get("x-twitter-streaming") is True:
        return True

    return False


def filter_openapi_spec(spec: dict) -> dict:
    filtered = copy.deepcopy(spec)
    paths = filtered.get("paths", {})
    new_paths = {}
    allow_tags = {tag.lower() for tag in parse_csv_env("X_API_TOOL_TAGS")}
    # Default-deny: an empty/missing allowlist falls back to the read-only default,
    # never "expose everything". See DEFAULT_READ_ALLOWLIST.
    allow_ops = parse_csv_env("X_API_TOOL_ALLOWLIST") or set(DEFAULT_READ_ALLOWLIST)
    deny_ops = parse_csv_env("X_API_TOOL_DENYLIST")
    # Write-guard: non-GET (mutate) operations are NEVER exposed unless writes are
    # explicitly opted in, regardless of what the allowlist contains. Makes "this is
    # a read-only tool" true by construction, not by trusting the allowlist string.
    allow_writes = is_truthy(os.getenv("X_API_ALLOW_WRITES"))

    n_write_blocked = 0
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue

        new_item = {}
        for key, value in item.items():
            if key.lower() in HTTP_METHODS:
                if should_exclude_operation(path, value):
                    continue
                if key.lower() != "get" and not allow_writes:
                    n_write_blocked += 1
                    continue
                operation_id = value.get("operationId")
                operation_tags = [
                    tag.lower() for tag in value.get("tags", []) if isinstance(tag, str)
                ]
                if allow_tags and not (set(operation_tags) & allow_tags):
                    continue
                if allow_ops and operation_id not in allow_ops:
                    continue
                if deny_ops and operation_id in deny_ops:
                    continue
                new_item[key] = value
            else:
                new_item[key] = value

        if any(method.lower() in HTTP_METHODS for method in new_item.keys()):
            new_paths[path] = new_item

    filtered["paths"] = new_paths
    n_tools = sum(
        1
        for item in new_paths.values()
        for method in item
        if method.lower() in HTTP_METHODS
    )
    used_default = not parse_csv_env("X_API_TOOL_ALLOWLIST")
    LOGGER.warning(
        "X grant: %d tools exposed, writes=%s, allowlist=%s (%d write ops blocked)",
        n_tools,
        "ON" if allow_writes else "OFF",
        "DEFAULT(read-only)" if used_default else "env",
        n_write_blocked,
    )
    return filtered


def print_tool_list(spec: dict) -> None:
    tools: list[str] = []
    for path, item in spec.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            if op_id:
                tools.append(op_id)
            else:
                tools.append(f"{method.upper()} {path}")

    tools.sort()
    print(f"Loaded {len(tools)} tools from OpenAPI:")
    for tool in tools:
        print(f"- {tool}")


def get_auth_headers(oauth_token: str | None = None) -> dict:
    env_oauth_token = os.getenv("X_OAUTH_ACCESS_TOKEN", "").strip()
    bearer_token = os.getenv("X_BEARER_TOKEN", "").strip()
    token = oauth_token or env_oauth_token or bearer_token
    if not token:
        raise RuntimeError("Set X_BEARER_TOKEN or provide OAuth1 access token on startup.")
    return {"Authorization": f"Bearer {token}"}


def build_oauth1_client() -> OAuth1Client:
    consumer_key = os.getenv("X_OAUTH_CONSUMER_KEY")
    consumer_secret = os.getenv("X_OAUTH_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        raise RuntimeError(
            "Missing X_OAUTH_CONSUMER_KEY or X_OAUTH_CONSUMER_SECRET for OAuth1 signing."
        )
    access_token, access_secret = run_oauth1_flow()
    if is_truthy(os.getenv("X_OAUTH_PRINT_TOKENS", "0")):
        print("OAuth1 access token:", access_token)
        print("OAuth1 access token secret:", access_secret)
    return OAuth1Client(
        client_key=consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_secret,
        signature_type="AUTH_HEADER",
    )


def print_oauth1_header_probe(oauth1_client: OAuth1Client, base_url: str) -> None:
    probe_url = f"{base_url}/2/users/me"
    _, signed_headers, _ = oauth1_client.sign(
        probe_url,
        http_method="GET",
        headers={},
    )
    auth_header = signed_headers.get("Authorization")
    if auth_header:
        print("OAuth1 Authorization header (sample GET /2/users/me):", auth_header)
    else:
        print("OAuth1 Authorization header missing from signed probe request.")


# PATCHED (secure-agentic-engineering): Grok-mediated X search tool.
# Calls xAI's Responses API with the `x_search` tool — Grok searches X and returns a
# cited natural-language summary (vs the raw posts from the X-API tools).
XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
# >>> MODEL LEVER: set XAI_MODEL in .env to switch Grok models. <<<
# Default is non-reasoning (cheaper/faster); use a *-reasoning model for deeper synthesis.
DEFAULT_XAI_MODEL = "grok-4-1-fast"


async def grok_x_search(query: str) -> str:
    """Search X (Twitter) via xAI Grok's x_search and return a cited summary.

    Grok performs the search itself and returns a concise natural-language answer with
    citations — use for "what are people saying / summarize the discussion on X about
    ...". For raw post objects, use the X-API tools (searchPostsRecent, etc.) instead.

    Args:
        query: What to search X for, in natural language.
    """
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        return "[grok_x_search: XAI_API_KEY not set in xMCP .env]"
    model = os.environ.get("XAI_MODEL", DEFAULT_XAI_MODEL)
    payload = {"model": model, "tools": [{"type": "x_search"}], "input": query}
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                XAI_RESPONSES_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
    except Exception as exc:  # noqa: BLE001
        return f"[grok_x_search: request to xAI failed: {exc}]"
    if resp.status_code >= 400:
        return f"[grok_x_search: xAI API error {resp.status_code}: {resp.text[:400]}]"
    data = resp.json()
    texts: list[str] = []
    citations: list[str] = []
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for block in item.get("content", []) or []:
                if block.get("text"):
                    texts.append(block["text"])
                for ann in block.get("annotations", []) or []:
                    if ann.get("url"):
                        citations.append(ann["url"])
    out = "\n".join(texts).strip()
    if not out:
        import json as _json

        out = _json.dumps(data)[:1500]  # fallback so the response shape is visible
    if citations:
        out += "\n\nCitations:\n" + "\n".join(f"- {u}" for u in dict.fromkeys(citations))
    return out + f"\n\n(via xAI model: {model})"


def create_mcp() -> FastMCP:
    load_env()
    debug_enabled = setup_logging()
    parser_flag = os.getenv("FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER")
    if parser_flag is not None:
        os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = parser_flag

    base_url = os.getenv("X_API_BASE_URL", "https://api.x.com")
    timeout = float(os.getenv("X_API_TIMEOUT", "30"))

    # PATCHED (secure-agentic-engineering): headless app-only Bearer auth.
    # If OAuth1 consumer keys are absent, skip the interactive browser OAuth1 flow
    # and use X_BEARER_TOKEN as a static Bearer header (read-only, app-only) — so
    # this can run as an unattended service. Original OAuth1 path is preserved when
    # consumer keys are provided.
    use_oauth1 = bool(os.getenv("X_OAUTH_CONSUMER_KEY") and os.getenv("X_OAUTH_CONSUMER_SECRET"))
    oauth1_client = build_oauth1_client() if use_oauth1 else None
    print_oauth_header = is_truthy(os.getenv("X_OAUTH_PRINT_AUTH_HEADER", "0"))
    if print_oauth_header and oauth1_client is not None:
        print_oauth1_header_probe(oauth1_client, base_url)

    spec = load_openapi_spec()
    filtered_spec = filter_openapi_spec(spec)
    comma_params = collect_comma_params(filtered_spec)
    print_tool_list(filtered_spec)

    async def normalize_query_params(request: httpx.Request) -> None:
        if not comma_params:
            return
        params = list(request.url.params.multi_items())
        grouped: dict[str, list[str]] = {}
        ordered: list[str] = []
        normalized: list[tuple[str, str]] = []

        for key, value in params:
            if key in comma_params:
                if key not in grouped:
                    ordered.append(key)
                grouped.setdefault(key, []).append(value)
            else:
                normalized.append((key, value))

        if not grouped:
            return

        for key in ordered:
            values: list[str] = []
            for raw in grouped[key]:
                for part in raw.split(","):
                    part = part.strip()
                    if part and part not in values:
                        values.append(part)
            if values:
                normalized.append((key, ",".join(values)))

        request.url = request.url.copy_with(params=normalized)

    b3_flags = os.getenv("X_B3_FLAGS", "1")

    async def sign_oauth1_request(request: httpx.Request) -> None:
        request.headers["X-B3-Flags"] = b3_flags
        headers = dict(request.headers)
        content_type = headers.get("Content-Type", "")
        body: str | None = None
        if content_type.startswith("application/x-www-form-urlencoded"):
            body_bytes = request.content or b""
            body = body_bytes.decode("utf-8")
        signed_url, signed_headers, _ = oauth1_client.sign(
            str(request.url),
            http_method=request.method,
            body=body,
            headers=headers,
        )
        request.url = httpx.URL(signed_url)
        request.headers.update(signed_headers)
        if print_oauth_header:
            auth_header = signed_headers.get("Authorization")
            if auth_header:
                print("OAuth1 Authorization header:", auth_header)
            else:
                print("OAuth1 Authorization header missing from signed request.")

    async def log_request(request: httpx.Request) -> None:
        if not debug_enabled:
            return
        LOGGER.info("X API request %s %s", request.method, request.url)

    async def log_response(response: httpx.Response) -> None:
        if not debug_enabled:
            return
        LOGGER.info(
            "X API response %s %s -> %s",
            response.request.method,
            response.request.url,
            response.status_code,
        )
        if response.status_code >= 400:
            transaction_id = response.headers.get("x-transaction-id")
            if transaction_id:
                LOGGER.warning("X API x-transaction-id: %s", transaction_id)
            body = await response.aread()
            text = body.decode("utf-8", errors="replace")
            if len(text) > 1000:
                text = text[:1000] + "...<truncated>"
            LOGGER.warning("X API error body: %s", text)

    # PATCHED: bearer mode → static Authorization header, no OAuth1 signing hook.
    if use_oauth1:
        client_headers: dict = {}
        request_hooks = [normalize_query_params, sign_oauth1_request, log_request]
    else:
        client_headers = get_auth_headers()  # {"Authorization": "Bearer <X_BEARER_TOKEN>"}
        request_hooks = [normalize_query_params, log_request]

    client = httpx.AsyncClient(
        base_url=base_url,
        headers=client_headers,
        timeout=timeout,
        event_hooks={
            "request": request_hooks,
            "response": [log_response],
        },
    )
    # FastMCP 3.x derives an `outputSchema` for every OpenAPI tool. Claude's
    # connector mishandles tools that advertise outputSchema -- it drops the
    # per-tool approval toggle (and sometimes the tool) from connector settings
    # (anthropics/claude-code#25081). The pre-3.x FastMCP we used to run didn't
    # emit these, which is why toggles worked before the dependency moved to 3.x.
    # Strip output_schema at build time so the wire shape matches the old, working
    # one. Escape hatch: XMCP_KEEP_OUTPUT_SCHEMA=1.
    keep_output_schema = is_truthy(os.getenv("XMCP_KEEP_OUTPUT_SCHEMA", "0"))

    def _strip_output_schema(_route, component) -> None:
        if not keep_output_schema and getattr(component, "output_schema", None) is not None:
            component.output_schema = None

    mcp = FastMCP.from_openapi(
        openapi_spec=filtered_spec,
        client=client,
        name="X API MCP",
        mcp_component_fn=None if keep_output_schema else _strip_output_schema,
    )
    # PATCHED: register the Grok x_search tool alongside the raw X-API tools.
    grok_tool = mcp.add_tool(grok_x_search)
    # grok_x_search returns a plain str here, which FastMCP 3.x also wraps in an
    # outputSchema -- strip it too so no tool advertises one (see above).
    if not keep_output_schema and getattr(grok_tool, "output_schema", None) is not None:
        grok_tool.output_schema = None

    # The shared security layers (approval gate, guardrail screening, OAuth) and the
    # HTTP run are applied uniformly by security.serve.serve() in main().
    return mcp


def main() -> None:
    port = int(os.getenv("MCP_PORT", "8000"))
    mcp = create_mcp()
    # x-mcp returns UNTRUSTED external X content, so screen output; and gate every call
    # behind out-of-band human approval. guardrail_source tags the wrapped content.
    serve(mcp, port=port, untrusted_output=True, require_approval=True, guardrail_source="xmcp")


if __name__ == "__main__":
    main()
