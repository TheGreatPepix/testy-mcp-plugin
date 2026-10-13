from dataclasses import dataclass

import pytest

from testy_mcp.tools import ToolError, call_tool


@dataclass
class Response:
    status: int = 200
    data: object = None


class FakeGateway:
    def __init__(self, response=None, *, current=None, routes=None):
        self.response = response or Response(data={"ok": True})
        self.current = current
        self.routes = routes or {}
        self.calls = []
        self.unchecked = []

    def request(self, method, path, *, query=None, body=None, check_size=True):
        self.calls.append((method, path, query, body))
        if not check_size:
            self.unchecked.append((method, path))
        if (method, path) in self.routes:
            return self.routes[(method, path)]
        if method == "GET" and self.current is not None:
            return Response(data=self.current)
        return self.response


def test_update_defaults_to_patch():
    gateway = FakeGateway()

    call_tool(
        "testy_update",
        {"entity": "cases", "id": 11, "data": {"name": "Updated"}},
        gateway,
    )

    assert gateway.calls[-1] == (
        "PATCH",
        "/api/v2/cases/11/",
        None,
        {"name": "Updated", "labels": [{"name": "AI-edited"}], "attachments": []},
    )


def test_read_only_entity_cannot_be_changed():
    with pytest.raises(ToolError, match="read-only"):
        call_tool(
            "testy_update",
            {"entity": "users", "id": 1, "data": {"username": "changed"}},
            FakeGateway(),
        )


def test_experimental_shared_steps_are_not_advertised():
    with pytest.raises(ToolError, match="must be one of"):
        call_tool("testy_list", {"entity": "shared_steps"}, FakeGateway())


def test_tool_arguments_are_validated_against_advertised_schema():
    with pytest.raises(ToolError, match="Unknown argument"):
        call_tool(
            "testy_get",
            {"entity": "cases", "id": 1, "unexpected": True},
            FakeGateway(),
        )


def test_structured_content_is_json_normalized():
    gateway = FakeGateway(Response(data=[{"id": 1}]))

    result = call_tool("testy_list", {"entity": "projects"}, gateway)

    assert result["structuredContent"] == [{"id": 1}]


def test_tool_text_content_is_compact_json():
    gateway = FakeGateway(Response(data={"id": 1, "nested": {"answer": True}}))

    result = call_tool("testy_get", {"entity": "projects", "id": 1}, gateway)

    assert result["content"][0]["text"] == '{"id":1,"nested":{"answer":true}}'


def test_action_builds_detail_action_path():
    gateway = FakeGateway()

    call_tool(
        "testy_action",
        {"entity": "cases", "id": 8, "action": "restore", "method": "POST"},
        gateway,
    )

    assert gateway.calls[0] == ("POST", "/api/v2/cases/8/restore/", None, None)


def test_action_rejects_path_injection():
    with pytest.raises(ToolError, match="action must contain"):
        call_tool(
            "testy_action",
            {"entity": "cases", "id": 8, "action": "../projects"},
            FakeGateway(),
        )


def test_list_applies_compact_default_projection():
    gateway = FakeGateway(
        Response(
            data={
                "count": 1,
                "results": [
                    {
                        "id": 5,
                        "name": "Login",
                        "project": 2,
                        "suite": {"id": 9, "name": "Auth"},
                        "setup": "verbose setup text",
                        "url": "https://testy.example.com/api/v2/cases/5/",
                        "versions": list(range(50)),
                    }
                ],
            }
        )
    )

    result = call_tool("testy_list", {"entity": "cases"}, gateway)

    assert result["structuredContent"] == {
        "count": 1,
        "results": [
            {"id": 5, "name": "Login", "project": 2, "suite": {"id": 9, "name": "Auth"}}
        ],
    }


def test_fields_argument_overrides_the_default_projection():
    gateway = FakeGateway(Response(data=[{"id": 1, "name": "a", "secret": "b"}]))

    result = call_tool(
        "testy_list", {"entity": "projects", "fields": ["id", "name"]}, gateway
    )

    assert result["structuredContent"] == [{"id": 1, "name": "a"}]


def test_projection_keeps_nested_paths():
    gateway = FakeGateway(
        Response(
            data={
                "id": 4,
                "name": "Case",
                "steps": [
                    {"id": 1, "scenario": "open", "expected": "shown", "junk": "x"}
                ],
            }
        )
    )

    result = call_tool(
        "testy_get",
        {"entity": "cases", "id": 4, "fields": ["id", "steps.scenario"]},
        gateway,
    )

    assert result["structuredContent"] == {
        "id": 4,
        "steps": [{"scenario": "open"}],
    }


def test_get_drops_read_only_noise_but_keeps_everything_else():
    gateway = FakeGateway(
        Response(
            data={
                "id": 3,
                "name": "Case",
                "scenario": "do",
                "url": "https://testy.example.com/api/v2/cases/3/",
                "versions": [3, 2, 1],
            }
        )
    )

    result = call_tool("testy_get", {"entity": "cases", "id": 3}, gateway)

    assert result["structuredContent"] == {
        "id": 3,
        "name": "Case",
        "scenario": "do",
    }


def test_list_sets_a_small_default_page_size():
    gateway = FakeGateway()

    call_tool("testy_list", {"entity": "cases"}, gateway)

    assert gateway.calls[0][2]["page_size"] == 25


def test_list_keeps_an_explicit_page_size():
    gateway = FakeGateway()

    call_tool("testy_list", {"entity": "cases", "query": {"page_size": 5}}, gateway)

    assert gateway.calls[0][2]["page_size"] == 5


def test_case_steps_shorthand_implies_is_steps_and_fills_required_fields():
    gateway = FakeGateway(Response(status=201, data={"id": 7, "name": "Case", "steps": []}))

    result = call_tool(
        "testy_create",
        {
            "entity": "cases",
            "data": {
                "project": 1,
                "suite": 2,
                "name": "Case",
                "steps": [["open the page", "page shown"], "click submit"],
            },
        },
        gateway,
    )

    body = gateway.calls[0][3]
    assert body["is_steps"] is True
    assert body["steps"] == [
        {
            "scenario": "open the page",
            "expected": "page shown",
            "name": "Step 1",
            "sort_order": 0,
        },
        {"scenario": "click submit", "name": "Step 2", "sort_order": 1},
    ]
    assert result["structuredContent"] == {"id": 7, "name": "Case"}


def test_case_update_keeps_explicit_step_objects_untouched():
    gateway = FakeGateway()

    call_tool(
        "testy_update",
        {
            "entity": "cases",
            "id": 3,
            "data": {"steps": [{"id": 11, "name": "N", "scenario": "s", "sort_order": 0}]},
        },
        gateway,
    )

    assert gateway.calls[-1][3] == {
        "steps": [{"id": 11, "name": "N", "scenario": "s", "sort_order": 0}],
        "is_steps": True,
        "labels": [{"name": "AI-edited"}],
        "attachments": [],
    }


def test_case_step_shorthand_rejects_bad_shapes():
    with pytest.raises(ToolError, match="must be an array"):
        call_tool(
            "testy_create",
            {"entity": "cases", "data": {"steps": "nope"}},
            FakeGateway(),
        )

    with pytest.raises(ToolError, match="requires a string 'scenario'"):
        call_tool(
            "testy_create",
            {"entity": "cases", "data": {"steps": [{"expected": "no scenario"}]}},
            FakeGateway(),
        )

    with pytest.raises(ToolError, match="must be an object, a string or"):
        call_tool(
            "testy_create",
            {"entity": "cases", "data": {"steps": [42]}},
            FakeGateway(),
        )


def test_fields_must_be_an_array():
    with pytest.raises(ToolError, match="must be an array"):
        call_tool("testy_list", {"entity": "projects", "fields": "id"}, FakeGateway())


def test_non_case_mutation_keeps_full_response():
    gateway = FakeGateway(Response(data={"id": 1, "name": "Suite", "extra": "kept"}))

    result = call_tool(
        "testy_create", {"entity": "suites", "data": {"name": "Suite"}}, gateway
    )

    assert result["structuredContent"] == {
        "id": 1,
        "name": "Suite",
        "extra": "kept",
    }


def test_case_create_adds_the_ai_label():
    gateway = FakeGateway(Response(status=201, data={"id": 1}))

    call_tool(
        "testy_create",
        {"entity": "cases", "data": {"project": 1, "suite": 2, "name": "C", "scenario": "s"}},
        gateway,
    )

    assert gateway.calls[0][3]["labels"] == [{"name": "AI-generated"}]


def test_case_create_keeps_caller_labels_and_accepts_names():
    gateway = FakeGateway(Response(status=201, data={"id": 1}))

    call_tool(
        "testy_create",
        {
            "entity": "cases",
            "data": {"name": "C", "labels": ["smoke", {"name": "ai-GENERATED"}]},
        },
        gateway,
    )

    assert gateway.calls[0][3]["labels"] == [{"name": "smoke"}, {"name": "ai-GENERATED"}]


def test_case_create_rejects_malformed_labels():
    with pytest.raises(ToolError, match=r"labels\[0\] requires a string 'name'"):
        call_tool(
            "testy_create",
            {"entity": "cases", "data": {"labels": [{"color": "#fff"}]}},
            FakeGateway(),
        )


def test_non_case_create_gets_no_label():
    gateway = FakeGateway(Response(status=201, data={"id": 1}))

    call_tool("testy_create", {"entity": "suites", "data": {"name": "S"}}, gateway)

    assert gateway.calls[0][3] == {"name": "S"}


def test_case_update_keeps_labels_and_attachments_it_does_not_mention():
    gateway = FakeGateway(
        current={
            "id": 5,
            "labels": [{"id": 1, "name": "AI-generated", "color": None}, {"id": 2, "name": "smoke"}],
            "attachments": [{"id": 30, "name": "log.txt"}],
        }
    )

    call_tool("testy_update", {"entity": "cases", "id": 5, "data": {"name": "New"}}, gateway)

    assert gateway.calls[0][:2] == ("GET", "/api/v2/cases/5/")
    assert gateway.calls[1][3] == {
        "name": "New",
        "labels": [{"name": "AI-generated"}, {"name": "smoke"}],
        "attachments": [30],
    }


def test_case_update_cannot_strip_the_ai_label():
    gateway = FakeGateway(current={"id": 5, "labels": [{"id": 1, "name": "AI-generated"}]})

    call_tool("testy_update", {"entity": "cases", "id": 5, "data": {"labels": []}}, gateway)

    assert gateway.calls[1][3]["labels"] == [{"name": "AI-generated"}]


def test_case_update_marks_human_cases_as_ai_edited():
    gateway = FakeGateway(current={"id": 5, "labels": [{"id": 2, "name": "smoke"}]})

    call_tool(
        "testy_update",
        {"entity": "cases", "id": 5, "data": {"labels": ["regress"]}},
        gateway,
    )

    assert gateway.calls[1][3]["labels"] == [{"name": "regress"}, {"name": "AI-edited"}]


def test_case_update_keeps_every_marker_already_on_the_case():
    gateway = FakeGateway(
        current={"id": 5, "labels": [{"name": "AI-copy"}, {"name": "AI-edited"}]}
    )

    call_tool("testy_update", {"entity": "cases", "id": 5, "data": {"labels": []}}, gateway)

    assert gateway.calls[1][3]["labels"] == [{"name": "AI-edited"}, {"name": "AI-copy"}]


def test_raw_case_requests_get_the_same_label_handling():
    gateway = FakeGateway(Response(status=201, data={"id": 1}))

    call_tool(
        "testy_api_request",
        {"method": "POST", "path": "api/v2/cases/", "data": {"name": "C"}},
        gateway,
    )

    assert gateway.calls[0][3]["labels"] == [{"name": "AI-generated"}]

    gateway = FakeGateway(current={"id": 9, "labels": [{"name": "AI-generated"}]})

    call_tool(
        "testy_api_request",
        {"method": "PATCH", "path": "/api/v2/cases/9/", "data": {"name": "N"}},
        gateway,
    )

    assert gateway.calls[0][:2] == ("GET", "/api/v2/cases/9/")
    assert gateway.calls[1][3]["labels"] == [{"name": "AI-generated"}]


def test_ai_label_can_be_renamed_or_disabled(monkeypatch):
    from testy_mcp import tools

    settings = {"TESTY_MCP_AI_LABEL_NAME": "ИИ", "TESTY_MCP_AI_LABEL_COLOR": "#7b61ff"}
    monkeypatch.setattr(tools, "_setting", lambda name, default: settings.get(name, default))
    gateway = FakeGateway(Response(status=201, data={"id": 1}))

    call_tool("testy_create", {"entity": "cases", "data": {"name": "C"}}, gateway)

    assert gateway.calls[0][3]["labels"] == [{"name": "ИИ", "color": "#7b61ff"}]

    settings["TESTY_MCP_AI_LABEL_NAME"] = ""
    gateway = FakeGateway(Response(status=201, data={"id": 1}))

    call_tool("testy_create", {"entity": "cases", "data": {"name": "C"}}, gateway)

    assert "labels" not in gateway.calls[0][3]


BULK_UPDATE = ("PUT", "/api/v2/cases/bulk-update/")


def marker_calls(gateway):
    return [call for call in gateway.calls if call[:2] == BULK_UPDATE]


def test_case_copies_get_the_ai_copy_label_per_project():
    copies = [
        {"id": 21, "project": 1, "labels": []},
        {"id": 22, "project": 1, "labels": [{"name": "AI-copy"}]},
        {"id": 23, "project": 2, "labels": [{"name": "smoke"}]},
    ]
    gateway = FakeGateway(Response(status=201, data=copies))

    call_tool(
        "testy_api_request",
        {"method": "POST", "path": "/api/v2/cases/bulk-copy/", "data": {"project": 1}},
        gateway,
    )

    assert [call[3] for call in marker_calls(gateway)] == [
        {
            "project": 1,
            "current_suite": None,
            "labels": [{"name": "AI-copy"}],
            "labels_action": "add",
            "included_cases": [21],
        },
        {
            "project": 2,
            "current_suite": None,
            "labels": [{"name": "AI-copy"}],
            "labels_action": "add",
            "included_cases": [23],
        },
    ]
    assert set(gateway.unchecked) == {BULK_UPDATE}


def test_suite_copy_marks_every_case_inside_the_copied_suites():
    gateway = FakeGateway(
        routes={
            ("POST", "/api/v2/suites/copy/"): Response(
                data=[{"id": 40, "project": 3, "name": "Copy"}]
            )
        }
    )

    call_tool(
        "testy_api_request",
        {"method": "POST", "path": "/api/v2/suites/copy/", "data": {"suites": [{"id": 4}]}},
        gateway,
    )

    assert marker_calls(gateway)[0][3] == {
        "project": 3,
        "current_suite": 40,
        "labels": [{"name": "AI-copy"}],
        "labels_action": "add",
    }


def test_bulk_update_marks_cases_not_created_by_ai():
    updated = [
        {"id": 1, "project": 1, "labels": [{"name": "AI-generated"}]},
        {"id": 2, "project": 1, "labels": []},
    ]
    gateway = FakeGateway(Response(data=updated))

    call_tool(
        "testy_api_request",
        {
            "method": "PUT",
            "path": "/api/v2/cases/bulk-update/",
            "data": {"project": 1, "current_suite": None, "included_cases": [1, 2], "suite": 9},
        },
        gateway,
    )

    assert len(marker_calls(gateway)) == 2
    assert marker_calls(gateway)[1][3]["included_cases"] == [2]
    assert marker_calls(gateway)[1][3]["labels"] == [{"name": "AI-edited"}]


def test_bulk_label_changes_cannot_strip_markers():
    with pytest.raises(ToolError, match="labels_action 'clear'"):
        call_tool(
            "testy_api_request",
            {
                "method": "PUT",
                "path": "/api/v2/cases/bulk-update/",
                "data": {"project": 1, "labels": [], "labels_action": "clear"},
            },
            FakeGateway(Response(data=[])),
        )

    gateway = FakeGateway(Response(data=[]))
    call_tool(
        "testy_api_request",
        {
            "method": "PUT",
            "path": "/api/v2/cases/bulk-update/",
            "data": {
                "project": 1,
                "labels": [{"id": 1, "name": "ai-generated"}, {"id": 2, "name": "smoke"}],
                "labels_action": "delete",
            },
        },
        gateway,
    )

    assert gateway.calls[0][3]["labels"] == [{"id": 2, "name": "smoke"}]


def test_case_detail_action_marks_the_case_as_edited():
    gateway = FakeGateway(current={"id": 8, "project": 2, "labels": []})

    call_tool(
        "testy_action",
        {"entity": "cases", "id": 8, "action": "archive", "method": "POST"},
        gateway,
    )

    assert gateway.calls[1][:2] == ("GET", "/api/v2/cases/8/")
    assert marker_calls(gateway)[0][3]["included_cases"] == [8]


def test_failed_marking_is_reported_without_inviting_a_retry():
    gateway = FakeGateway(
        Response(status=201, data=[{"id": 21, "project": 1}]),
        routes={BULK_UPDATE: Response(status=403, data={"detail": "denied"})},
    )

    with pytest.raises(ToolError, match="The request succeeded.*Do not repeat"):
        call_tool(
            "testy_api_request",
            {"method": "POST", "path": "/api/v2/cases/copy/", "data": {}},
            gateway,
        )


CASE_3 = ("GET", "/api/v2/cases/3/")
PROJECT_6 = ("GET", "/api/v2/projects/6/")


def deletion_gateway(routes=None):
    return FakeGateway(
        Response(status=204, data=None),
        routes={
            CASE_3: Response(
                data={"id": 3, "name": "Вход  по паролю", "project": 6, "labels": []}
            ),
            PROJECT_6: Response(data={"id": 6, "name": "STUDY"}),
            **(routes or {}),
        },
    )


def writes(gateway):
    return [call[:2] for call in gateway.calls if call[0] != "GET"]


def test_delete_without_confirmation_archives_and_names_the_phrase():
    gateway = deletion_gateway()

    result = call_tool("testy_delete", {"entity": "cases", "id": 3}, gateway)

    assert writes(gateway)[0] == ("POST", "/api/v2/cases/3/archive/")
    assert ("DELETE", "/api/v2/cases/3/") not in writes(gateway)
    content = result["structuredContent"]
    assert content["archived"] is True and content["deleted"] is False
    assert content["name"] == "Вход по паролю" and content["project"] == "STUDY"
    assert "Да, удали Вход по паролю в проекте STUDY" in content["note"]


def test_delete_with_the_exact_phrase_deletes():
    gateway = deletion_gateway()

    result = call_tool(
        "testy_delete",
        {
            "entity": "cases",
            "id": 3,
            "confirmation": " «Да, удали Вход по  паролю в проекте STUDY» ",
        },
        gateway,
    )

    assert writes(gateway) == [("DELETE", "/api/v2/cases/3/")]
    assert result["structuredContent"] == {
        "deleted": True,
        "entity": "cases",
        "id": 3,
        "name": "Вход по паролю",
        "project": "STUDY",
    }


@pytest.mark.parametrize(
    "confirmation",
    [
        "Да",
        "Да, удали",
        "да, удали Вход по паролю в проекте STUDY",
        "Да, удали Вход по паролю в проекте Alertix",
        "Да, удали Другой кейс в проекте STUDY",
        "Да, удали Вход по паролю в проекте STUDY и всё остальное",
    ],
)
def test_delete_with_a_wrong_phrase_changes_nothing(confirmation):
    gateway = deletion_gateway()

    expected = "does not match.*Да, удали Вход по паролю в проекте STUDY"
    with pytest.raises(ToolError, match=expected):
        call_tool(
            "testy_delete", {"entity": "cases", "id": 3, "confirmation": confirmation}, gateway
        )
    assert writes(gateway) == []


def test_project_phrase_names_the_project_itself():
    gateway = deletion_gateway()

    with pytest.raises(ToolError, match="Да, удали проект STUDY"):
        call_tool(
            "testy_delete",
            {"entity": "projects", "id": 6, "confirmation": "Да, удали STUDY в проекте STUDY"},
            gateway,
        )
    call_tool(
        "testy_delete",
        {"entity": "projects", "id": 6, "confirmation": "Да, удали проект STUDY"},
        gateway,
    )
    assert writes(gateway) == [("DELETE", "/api/v2/projects/6/")]


def test_entities_without_archive_need_the_phrase():
    suite = Response(data={"id": 9, "name": "Логин", "project": {"id": 6, "name": "STUDY"}})
    gateway = deletion_gateway({("GET", "/api/v2/suites/9/"): suite})

    with pytest.raises(ToolError, match="cannot be archived.*Да, удали Логин в проекте STUDY"):
        call_tool("testy_delete", {"entity": "suites", "id": 9}, gateway)
    assert writes(gateway) == []


def test_delete_no_longer_accepts_a_boolean_confirmation():
    with pytest.raises(ToolError, match="Unknown argument: confirmed"):
        call_tool(
            "testy_delete", {"entity": "cases", "id": 3, "confirmed": True}, deletion_gateway()
        )


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("testy_action", {"entity": "cases", "id": 3, "action": "x", "method": "DELETE"}),
        ("testy_api_request", {"method": "DELETE", "path": "/api/v2/cases/3/"}),
        (
            "testy_api_request",
            {
                "method": "POST",
                "path": "/api/v2/cases/deleted/remove/",
                "data": {"instance_ids": [3]},
            },
        ),
    ],
)
def test_other_delete_routes_are_refused(tool, arguments):
    gateway = FakeGateway()

    with pytest.raises(ToolError, match="must be one of|Use testy_delete"):
        call_tool(tool, arguments, gateway)
    assert gateway.calls == []
