# Copyright 2026 InstaDeep Ltd. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import subprocess
import sys
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Dict, Any, Optional, Tuple
import uuid


DEFAULT_TIMEOUT = 30
FILES_TIMEOUT = 60
DEFAULT_RETRIES = 3
RETRY_BACKOFF = 0.5
RETRY_STATUS_CODES = [500, 502, 503, 504]


def load_file_for_upload(
    file_path: str, field_name: str, content_type: str = "application/octet-stream"
) -> Tuple[str, Tuple[str, bytes, str]]:
    """
    Load a file and prepare it for multipart form upload.
    Raises FileNotFoundError or PermissionError if file cannot be read.
    """
    with open(file_path, "rb") as f:
        content = f.read()
    filename = os.path.basename(file_path)
    return (field_name, (filename, content, content_type))


PEM_FOOTER = "-----END CERTIFICATE-----"

_keychain_pem: Optional[str] = None


def macos_keychain_pem() -> str:
    """Roots from the keychain search list, as one PEM blob.

    OpenSSL cannot read the keychain, so on macOS the OS store is reachable
    only by shelling out to `security`.
    """
    global _keychain_pem
    if _keychain_pem is None:
        try:
            _keychain_pem = subprocess.run(
                ["/usr/bin/security", "find-certificate", "-a", "-p"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        except Exception:
            _keychain_pem = ""
    return _keychain_pem


def load_macos_keychain(ctx) -> None:
    pem = macos_keychain_pem()
    if not pem:
        return
    try:
        ctx.load_verify_locations(cadata=pem)
        return
    except Exception:
        pass
    # One unparsable entry rejects the whole blob, which would drop the
    # inspection root along with it, so retry a certificate at a time.
    for chunk in pem.split(PEM_FOOTER):
        if "BEGIN CERTIFICATE" not in chunk:
            continue
        try:
            ctx.load_verify_locations(cadata=chunk + PEM_FOOTER + "\n")
        except Exception:
            continue


def build_ssl_context():
    """Trust the OS certificate store *and* the certifi bundle.

    Supplying our own context makes requests skip its certifi-backed default
    (see requests.adapters._urllib3_request_context), so certifi has to be
    loaded here or public roots go missing. The OS store still has to be loaded
    too: it is the only place a corporate TLS-inspection root lives, and on
    Windows it also holds the intermediates the OS fetched on demand, which
    OpenSSL cannot fetch for itself.
    """
    import ssl

    # create_default_context() already loads the OS store on Windows. On macOS
    # it reads OpenSSL's paths, never the keychain, so the store is loaded below.
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        # No certifi: fall back to the OS store alone.
        pass
    if sys.platform == "darwin":
        load_macos_keychain(ctx)
    return ctx


class SystemCertAdapter(HTTPAdapter):
    """HTTPAdapter that trusts the OS certificate store alongside certifi."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = build_ssl_context()
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        # requests builds proxy pools separately, which would otherwise drop
        # the context above and leave proxied requests with no CA bundle.
        proxy_kwargs.setdefault("ssl_context", build_ssl_context())
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def create_session(retries: int = DEFAULT_RETRIES) -> requests.Session:
    session = requests.Session()

    retry_strategy = Retry(
        total=retries,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=["GET", "POST", "PATCH", "PUT"],
        raise_on_status=False,
    )

    adapter = SystemCertAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


_session: Optional[requests.Session] = None
_no_retry_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = create_session()
    return _session


def get_no_retry_session() -> requests.Session:
    """Session that never replays a request.

    An agent run that 502s at the gateway may already have executed
    server-side tools, so a retry duplicates their effects.
    """
    global _no_retry_session
    if _no_retry_session is None:
        session = requests.Session()
        adapter = SystemCertAdapter(max_retries=0)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _no_retry_session = session
    return _no_retry_session


def make_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    timeout: int = DEFAULT_TIMEOUT,
    params: Optional[Dict] = None,
    json_data: Optional[Dict] = None,
    data: Optional[Dict] = None,
    files: Optional[list] = None,
) -> Dict[str, Any]:
    session = get_session()

    try:
        response = session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_data,
            data=data,
            files=files,
            timeout=timeout,
        )
        response_text = response.text
        if not (200 <= response.status_code < 300):
            try:
                error_data = json.loads(response_text)
                if isinstance(error_data, dict) and "errorMessage" in error_data:
                    response_text = error_data["errorMessage"]
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "status": response.status_code,
            "response": response_text,
            "success": 200 <= response.status_code < 300,
        }
    except requests.exceptions.Timeout:
        return {
            "status": 408,
            "response": "Request timed out.",
            "success": False,
        }
    except requests.exceptions.SSLError as e:
        return {
            "status": 495,
            "response": (
                f"Could not verify the TLS certificate of {url.split('/')[2]}. "
                "The issuer is not in this machine's certificate store. If you "
                "are behind a proxy that re-signs traffic, point "
                "REQUESTS_CA_BUNDLE at its CA bundle and restart KiCad."
                f"\n\n{e}"
            ),
            "success": False,
        }
    except requests.exceptions.ConnectionError as e:
        if hasattr(e, "response") and e.response is not None:
            return {
                "status": e.response.status_code,
                "response": e.response.text,
                "success": False,
            }
        return {
            "status": 503,
            "response": f"Connection failed: {e}",
            "success": False,
        }
    except requests.exceptions.RequestException as e:
        if hasattr(e, "response") and e.response is not None:
            return {
                "status": e.response.status_code,
                "response": e.response.text,
                "success": False,
            }
        return {
            "status": 500,
            "response": f"Request failed: {e}",
            "success": False,
        }


def build_headers(api_key: str, content_type: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "accept": "*/*",
        "x-deeppcb-api-key": api_key,
        "x-client-type": "KicadPlugin",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def download_board(
    board_id: str, type: str, swagger_url: str, revision_number: int, api_key: str = ""
) -> Dict[str, Any]:
    url = f"{swagger_url}/boards/{board_id}/revision-artifact"
    params = {"revision": revision_number, "type": type, "revisionType": "Tidy"}

    return make_request(
        method="GET", url=url, headers=build_headers(api_key), params=params
    )


def check_board_status(board_id: str, swagger_url: str, api_key: str) -> Dict[str, Any]:
    url = f"{swagger_url}/boards/{board_id}"

    return make_request(method="GET", url=url, headers=build_headers(api_key))


def submit_board(
    board_id: str, timeout: int, swagger_url: str, api_key: str, job_type: str
) -> Dict[str, Any]:
    url = f"{swagger_url}/boards/{board_id}/confirm"
    data = {
        "timeout": timeout,
        "maxBatchTimeout": 30,
        "timeToLive": 300,
        "maxInactivityWaitTimeout": 60,
        "jobType": job_type,
    }

    return make_request(
        method="PATCH",
        url=url,
        headers=build_headers(api_key, "application/json-patch+json"),
        json_data=data,
    )


def create_board(
    swagger_url: str,
    api_key: str,
    board_name: str,
    kicad_board_file_path: str,
    kicad_project_file_path: str = None,
    kicad_schematics_file_paths: list = None,
    routing_type: str = "CurrentUnprotectedWiring",
    batch: int = 1,
    board_input_type: str = "Kicad",
    request_id: str = None,
) -> Dict[str, Any]:
    if request_id is None:
        request_id = str(uuid.uuid4())

    url = f"{swagger_url}/boards"

    form_data = {
        "routingType": routing_type,
        "webhookToken": "webhook_token",
        "requestId": request_id,
        "jsonFileUrl": "",
        "boardName": board_name,
        "webhookUrl": "https://plugin.kicad.deeppcb.ai",
        "batch": str(batch),
        "dsnFile": "",
        "boardInputType": board_input_type,
    }

    try:
        files = [load_file_for_upload(kicad_board_file_path, "kicadBoardFile")]

        if kicad_project_file_path:
            files.append(
                load_file_for_upload(kicad_project_file_path, "kicadProjectFile")
            )

        for sch_path in kicad_schematics_file_paths or []:
            files.append(load_file_for_upload(sch_path, "kicadSchematicsFiles"))

        return make_request(
            method="POST",
            url=url,
            headers=build_headers(api_key),
            data=form_data,
            files=files,
            timeout=FILES_TIMEOUT,
        )

    except FileNotFoundError as e:
        return {
            "status": 404,
            "response": f"File not found: {str(e)}",
            "success": False,
            "error": "file_not_found",
        }
    except PermissionError as e:
        return {
            "status": 403,
            "response": f"Permission denied: {str(e)}",
            "success": False,
            "error": "permission_denied",
        }
    except Exception as e:
        return {
            "status": 500,
            "response": f"Error creating board: {str(e)}",
            "success": False,
            "error": "unknown_error",
        }


def stop_board(board_id: str, swagger_url: str, api_key: str) -> Dict[str, Any]:
    url = f"{swagger_url}/boards/{board_id}/stop"

    return make_request(
        method="PATCH",
        url=url,
        headers=build_headers(api_key, "application/json-patch+json"),
    )


def resume_board(
    board_id: str, timeout: int, swagger_url: str, api_key: str, job_type: str
) -> Dict[str, Any]:
    url = f"{swagger_url}/boards/{board_id}/resume"
    data = {
        "timeout": timeout,
        "maxBatchTimeout": 30,
        "timeToLive": 300,
        "responseBoardFormat": 1,
        "jobType": job_type,
    }

    return make_request(
        method="PATCH", url=url, headers=build_headers(api_key), json_data=data
    )


def save_board_rating(
    board_id: str,
    swagger_url: str,
    api_key: str,
    speed_rating: int,
    drc_rating: int,
    solution_rating: int,
    feedback: str,
) -> Dict[str, Any]:
    url = f"{swagger_url}/boards/{board_id}/rating"
    data = {
        "speedRating": speed_rating,
        "drcRating": drc_rating,
        "solutionRating": solution_rating,
        "feedback": feedback,
    }

    return make_request(
        method="PUT",
        url=url,
        headers=build_headers(api_key, "application/json"),
        json_data=data,
    )


def get_credit_balance(swagger_url: str, api_key: str) -> Dict[str, Any]:
    url = f"{swagger_url}/apiuser/credit-flow"

    result = make_request(method="GET", url=url, headers=build_headers(api_key))

    result["balance"] = None
    if result.get("success"):
        try:
            import json

            data = json.loads(result["response"])
            if data.get("balance") is not None:
                result["balance"] = float(data["balance"])
        except (ValueError, KeyError):
            pass

    return result
