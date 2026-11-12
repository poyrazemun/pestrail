from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

import httpx
import pytest

from pestrail import client as client_module
from pestrail.client import (
    ALLOWED_METHODS,
    FORBIDDEN_PREFIXES,
    READ_METHODS,
    WRITE_METHODS,
    MethodNotAllowedError,
    TestRailClient,
    TestRailError,
    extract_items,
)

from .conftest import API, FAKE_KEY, _no_sleep, make_config, run

# ----------------------------------------------------------------- allowlist safety


def test_no_allowlisted_method_is_destructive():
    for method in ALLOWED_METHODS:
        assert not method.startswith("delete_"), method
        assert not method.startswith(FORBIDDEN_PREFIXES), method


def test_no_case_suite_section_or_run_mutation_in_allowlist():
    forbidden = {
        "add_case",
        "update_case",
        "update_cases",
        "move_cases_to_section",
        "copy_cases_to_section",
        "add_suite",
        "update_suite",
        "add_section",
        "update_section",
        "move_section",
        "close_run",
        "close_plan",
        "add_run",
        "update_run",
        "add_plan",
        "update_plan",
        "add_plan_entry",
        "update_plan_entry",
        "add_milestone",
        "update_milestone",
    }
    assert not (ALLOWED_METHODS & forbidden)


def test_write_methods_only_add_results_or_attachments():
    for method in WRITE_METHODS:
        assert method.startswith(("add_result", "add_attachment_to_")), method


def test_read_methods_are_all_get():
    assert all(m.startswith("get_") for m in READ_METHODS)


def test_allowlists_are_frozen():
    assert isinstance(ALLOWED_METHODS, frozenset)
    assert isinstance(READ_METHODS, frozenset)
    assert isinstance(WRITE_METHODS, frozenset)


def test_client_module_has_no_delete_code():
    source = Path(client_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"[\"']delete_\w+[\"']", source)


@pytest.mark.parametrize(
    "method", ["delete_case", "delete_run", "close_run", "update_case", "add_case", "get_nope"]
)
def test_non_allowlisted_method_raises_before_request(client, mock_api, method):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(MethodNotAllowedError):
        run(client._request(method, (1,)))
    with pytest.raises(MethodNotAllowedError):
        client.build_url(method)
    assert route.call_count == 0


def test_get_rejects_write_method_and_post_rejects_read(client, mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(MethodNotAllowedError):
        run(client.get("add_result", 1))
    with pytest.raises(MethodNotAllowedError):
        run(client.post("get_case", 1))
    with pytest.raises(MethodNotAllowedError):
        run(client.upload("add_result", 1, file_path=Path("x")))
    assert route.call_count == 0


def test_read_only_blocks_writes_in_client(mock_api):
    route = mock_api.route().mock(return_value=httpx.Response(200, json={}))
    ro = TestRailClient(make_config(read_only=True), sleep=_no_sleep)
    with pytest.raises(MethodNotAllowedError, match="read-only"):
        run(ro.post("add_result", 1, body={"status_id": 1}))
    assert route.call_count == 0


# ----------------------------------------------------------------- URL building


def test_build_url_uses_ampersand_for_params(client):
    url = client.build_url("get_cases", (1,), {"suite_id": 2, "filter": "login & logout"})
    assert url == API + "get_cases/1&suite_id=2&filter=login%20%26%20logout"
    assert url.count("?") == 1


def test_build_url_formats_lists_and_bools_and_skips_none(client):
    url = client.build_url(
        "get_tests", (5,), {"status_id": [1, 5], "x": None, "is_completed": True}
    )
    assert url == API + "get_tests/5&status_id=1,5&is_completed=1"


def test_build_url_rejects_non_int_path_args(client):
    with pytest.raises(TestRailError):
        client.build_url("get_case", ("1/../delete_case",))  # type: ignore[arg-type]
    with pytest.raises(TestRailError):
        client.build_url("get_case", (-1,))
    with pytest.raises(TestRailError):
        client.build_url("get_case", ("12",))  # numeric strings must be parsed to int first


def test_build_url_accepts_guid_for_plan_entry(client):
    guid = "3933d74b-4282-4c1f-be62-a641ab427063"
    url = client.build_url("add_attachment_to_plan_entry", (7, guid))
    assert url == API + f"add_attachment_to_plan_entry/7/{guid}"


def test_params_cannot_inject(client):
    url = client.build_url("get_cases", (1,), {"filter": "a&limit=1/../../delete_case/1"})
    assert "&limit=1" not in url
    assert "delete_case" in url  # present only as an encoded value of filter
    assert url.endswith("filter=a%26limit%3D1%2F..%2F..%2Fdelete_case%2F1")


def test_credentials_not_in_url(client):
    url = client.build_url("get_projects")
    assert FAKE_KEY not in url
    assert "@" not in url


# ----------------------------------------------------------------- requests


def test_get_sends_basic_auth_and_returns_json(client, mock_api):
    route = mock_api.get(API + "get_case/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "title": "Login"})
    )
    assert run(client.get("get_case", 7)) == {"id": 7, "title": "Login"}
    request = route.calls.last.request
    expected = base64.b64encode(f"qa@example.invalid:{FAKE_KEY}".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
    assert FAKE_KEY not in str(request.url)


def test_post_sends_json_content_type(client, mock_api):
    route = mock_api.post(API + "add_result/9").mock(
        return_value=httpx.Response(200, json={"id": 100})
    )
    assert run(client.post("add_result", 9, body={"status_id": 1})) == {"id": 100}
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == b'{"status_id":1}'


def test_upload_is_multipart_with_attachment_field(client, mock_api, tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("hello", encoding="utf-8")
    route = mock_api.post(API + "add_attachment_to_result/5").mock(
        return_value=httpx.Response(200, json={"attachment_id": 42})
    )
    assert run(client.upload("add_attachment_to_result", 5, file_path=f)) == {"attachment_id": 42}
    request = route.calls.last.request
    assert request.headers["Content-Type"].startswith("multipart/form-data")
    assert b'name="attachment"; filename="log.txt"' in request.content
    assert b"hello" in request.content


@pytest.mark.parametrize(
    ("status", "error", "fragment"),
    [
        (400, "Field :case_id is not a valid test case.", "not a valid test case"),
        (401, "Authentication failed: invalid or missing user/password or session cookie.", "API"),
        (403, "You are not allowed to add results.", "Permission denied"),
        (404, None, "Not found"),
        (500, None, "Request failed"),
    ],
)
def test_errors_are_surfaced(client, mock_api, status, error, fragment):
    body = {"error": error} if error else {}
    mock_api.get(API + "get_case/1").mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(TestRailError) as info:
        run(client.get("get_case", 1))
    assert info.value.status_code == status
    assert fragment in str(info.value)
    assert FAKE_KEY not in str(info.value)


def test_non_json_error_body(client, mock_api):
    mock_api.get(API + "get_case/1").mock(return_value=httpx.Response(502, text="<html>bad</html>"))
    with pytest.raises(TestRailError, match="502"):
        run(client.get("get_case", 1))


def test_redirect_not_followed(client, mock_api):
    mock_api.get(API + "get_projects").mock(
        return_value=httpx.Response(302, headers={"Location": "https://elsewhere.invalid/"})
    )
    with pytest.raises(TestRailError, match="redirect"):
        run(client.get("get_projects"))


def test_network_error_is_wrapped(client, mock_api):
    mock_api.get(API + "get_projects").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(TestRailError, match="Could not reach"):
        run(client.get("get_projects"))


def test_ssl_error_has_actionable_hint(client, mock_api):
    mock_api.get(API + "get_projects").mock(
        side_effect=httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local "
            "issuer certificate"
        )
    )
    with pytest.raises(TestRailError) as info:
        run(client.get("get_projects"))
    message = str(info.value)
    assert "CERTIFICATE_VERIFY_FAILED" in message
    assert "TESTRAIL_CA_BUNDLE" in message


def test_dns_error_hint(client, mock_api):
    mock_api.get(API + "get_projects").mock(
        side_effect=httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    )
    with pytest.raises(TestRailError, match="could not be resolved"):
        run(client.get("get_projects"))


def test_proxy_error_hint(client, mock_api):
    mock_api.get(API + "get_projects").mock(side_effect=httpx.ProxyError("407 auth required"))
    with pytest.raises(TestRailError, match="HTTPS_PROXY"):
        run(client.get("get_projects"))


def test_network_error_redacts_secret_and_truncates(client, mock_api):
    mock_api.get(API + "get_projects").mock(
        side_effect=httpx.ConnectError(f"weird failure {FAKE_KEY} " + "x" * 1000)
    )
    with pytest.raises(TestRailError) as info:
        run(client.get("get_projects"))
    message = str(info.value)
    assert FAKE_KEY not in message
    assert "********" in message
    assert len(message) < 700


def test_ssl_context_uses_ca_bundle():
    import certifi

    from pestrail.client import build_ssl_context

    cfg = make_config()
    ctx = build_ssl_context(type(cfg)(**{**cfg.__dict__, "ca_bundle": Path(certifi.where())}))
    assert ctx.verify_mode.name == "CERT_REQUIRED"
    assert ctx.check_hostname is True
    assert len(ctx.get_ca_certs()) > 10


def test_ssl_context_default_verifies():
    from pestrail.client import build_ssl_context

    ctx = build_ssl_context(make_config())
    assert ctx.verify_mode.name == "CERT_REQUIRED"
    assert ctx.check_hostname is True


def test_timeout_is_wrapped(client, mock_api):
    mock_api.get(API + "get_projects").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(TestRailError, match="timed out"):
        run(client.get("get_projects"))


# ----------------------------------------------------------------- 429 retry


def test_retries_429_with_retry_after(mock_api):
    delays: list[float] = []

    async def fake_sleep(d: float) -> None:
        delays.append(d)

    c = TestRailClient(make_config(), sleep=fake_sleep)
    route = mock_api.get(API + "get_projects").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json=[{"id": 1}]),
        ]
    )
    assert run(c.get("get_projects")) == [{"id": 1}]
    assert route.call_count == 3
    assert delays == [2.0, 1.0]


def test_429_gives_up_after_max_retries(mock_api):
    c = TestRailClient(make_config(), sleep=_no_sleep, max_retries=2)
    route = mock_api.get(API + "get_projects").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "slow down"})
    )
    with pytest.raises(TestRailError) as info:
        run(c.get("get_projects"))
    assert info.value.status_code == 429
    assert route.call_count == 3


def test_retry_after_is_capped():
    assert client_module._parse_retry_after("100000") == client_module.MAX_RETRY_DELAY
    assert client_module._parse_retry_after(None) == client_module.DEFAULT_RETRY_DELAY
    assert client_module._parse_retry_after("garbage") == client_module.DEFAULT_RETRY_DELAY
    assert client_module._parse_retry_after("-5") == 0.0


# ----------------------------------------------------------------- pagination


def test_extract_items_bare_list():
    items, meta = extract_items([{"id": 1}], "cases")
    assert items == [{"id": 1}]
    assert meta == {"paginated": False}


def test_extract_items_paginated_object():
    data = {
        "offset": 0,
        "limit": 250,
        "size": 1,
        "_links": {"next": "/api/v2/get_cases/1&offset=250", "prev": None},
        "cases": [{"id": 1}],
    }
    items, meta = extract_items(data, "cases")
    assert items == [{"id": 1}]
    assert meta["has_more"] is True
    assert meta["limit"] == 250


def test_get_all_follows_next(client, mock_api):
    page1 = {
        "offset": 0,
        "limit": 2,
        "size": 2,
        "_links": {"next": "/api/v2/get_cases/1&limit=2&offset=2", "prev": None},
        "cases": [{"id": 1}, {"id": 2}],
    }
    page2 = {
        "offset": 2,
        "limit": 2,
        "size": 1,
        "_links": {"next": None, "prev": "/api/v2/get_cases/1&limit=2&offset=0"},
        "cases": [{"id": 3}],
    }
    route = mock_api.get(url__startswith=API + "get_cases/1").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    items, truncated = run(client.get_all("get_cases", 1, key="cases", params={"limit": 2}))
    assert [i["id"] for i in items] == [1, 2, 3]
    assert truncated is False
    assert str(route.calls[1].request.url).endswith("&limit=2&offset=2")


def test_get_all_ignores_malicious_next_link(client, mock_api):
    page1 = {
        "offset": 0,
        "limit": 1,
        "size": 1,
        "_links": {"next": "/api/v2/delete_project/1"},
        "cases": [{"id": 1}],
    }
    page2 = {"offset": 1, "limit": 1, "size": 0, "_links": {"next": None}, "cases": []}
    route = mock_api.get(url__startswith=API + "get_cases/1").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    run(client.get_all("get_cases", 1, key="cases"))
    for call in route.calls:
        assert "delete_" not in str(call.request.url)


def test_get_all_respects_cap(client, mock_api):
    def page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "offset": 0,
                "limit": 2,
                "size": 2,
                "_links": {"next": "/x"},
                "cases": [{"id": 1}, {"id": 2}],
            },
        )

    mock_api.get(url__startswith=API + "get_cases/1").mock(side_effect=page)
    items, truncated = run(client.get_all("get_cases", 1, key="cases", max_items=5))
    assert len(items) == 5
    assert truncated is True


def test_get_all_exact_boundary_is_not_truncated(client, mock_api):
    mock_api.get(url__startswith=API + "get_cases/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "offset": 0,
                "limit": 3,
                "size": 3,
                "_links": {"next": None},
                "cases": [{"id": 1}, {"id": 2}, {"id": 3}],
            },
        )
    )
    items, truncated = run(client.get_all("get_cases", 1, key="cases", max_items=3))
    assert len(items) == 3
    assert truncated is False


def test_get_all_exact_boundary_with_more_is_truncated(client, mock_api):
    mock_api.get(url__startswith=API + "get_cases/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "offset": 0,
                "limit": 3,
                "size": 3,
                "_links": {"next": "/api/v2/get_cases/1&offset=3"},
                "cases": [{"id": 1}, {"id": 2}, {"id": 3}],
            },
        )
    )
    items, truncated = run(client.get_all("get_cases", 1, key="cases", max_items=3))
    assert len(items) == 3
    assert truncated is True


def test_get_all_bare_list_applies_offset(client, mock_api):
    # TestRail < 6.7 ignores offset and returns everything as a bare list.
    mock_api.get(url__startswith=API + "get_projects").mock(
        return_value=httpx.Response(200, json=[{"id": i} for i in range(5)])
    )
    items, truncated = run(client.get_all("get_projects", key="projects", params={"offset": 2}))
    assert [i["id"] for i in items] == [2, 3, 4]
    assert truncated is False


def test_get_all_bare_list_single_request(client, mock_api):
    route = mock_api.get(API + "get_projects").mock(
        return_value=httpx.Response(200, json=[{"id": 1}, {"id": 2}])
    )
    items, truncated = run(client.get_all("get_projects", key="projects"))
    assert len(items) == 2
    assert truncated is False
    assert route.call_count == 1


def test_statuses_are_cached(client, mock_api):
    route = mock_api.get(API + "get_statuses").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "passed"}])
    )
    run(client.get_statuses())
    run(client.get_statuses())
    assert route.call_count == 1


# ----------------------------------------------------------------- secrecy


def test_api_key_not_in_repr_or_logs(client, mock_api, caplog):
    caplog.set_level(logging.DEBUG)
    mock_api.get(API + "get_projects").mock(return_value=httpx.Response(200, json=[]))
    mock_api.get(API + "get_case/1").mock(return_value=httpx.Response(401, json={"error": "no"}))
    run(client.get("get_projects"))
    with pytest.raises(TestRailError):
        run(client.get("get_case", 1))
    assert FAKE_KEY not in repr(client)
    assert FAKE_KEY not in caplog.text
    encoded = base64.b64encode(f"qa@example.invalid:{FAKE_KEY}".encode()).decode()
    assert encoded not in caplog.text
