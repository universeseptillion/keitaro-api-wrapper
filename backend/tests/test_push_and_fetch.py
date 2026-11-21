import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main
from app.database import Base
from app.models import EditorStream


@pytest.fixture
def editor(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine)

    # Тесты не зависят от префикса в локальном .env.
    monkeypatch.setattr(main.settings, "campaign_prefix", "test_candidate_")

    offers = [
        {
            "offer_id": offer_id,
            "name": f"Test offer {offer_id}",
            "share": 50,
            "removed": False,
            "pinned": False,
        }
        for offer_id in (1, 2)
    ]

    with test_session() as db:
        db.add(
            EditorStream(
                id=100,
                campaign_id=200,
                name="Test stream",
                published_offers=deepcopy(offers),
                draft_offers=deepcopy(offers),
            )
        )
        db.commit()

    # Имитируем состояние Keitaro.
    remote = {
        "id": 100,
        "campaign_id": 200,
        "name": "Test stream",
        "type": "regular",
        "position": 2,
        "state": "active",
        "schema": "landings",
        "action_type": "http",
        "action_payload": None,
        "collect_clicks": True,
        "filter_or": False,
        "filters": [
            {
                "name": "country",
                "mode": "accept",
                "payload": ["AU"],
            }
        ],
        "landings": [],
        "triggers": [],
        "offers": [
            {
                "offer_id": offer_id,
                "share": 50,
                "state": "active",
            }
            for offer_id in (1, 2)
        ],
    }

    calls = []

    async def fake_request(method, path, body=None):
        calls.append({
            "method": method,
            "path": path,
            "body": deepcopy(body),
        })

        if method == "GET" and path == "campaigns/200":
            return {
                "id": 200,
                "name": f"{main.settings.campaign_prefix}test",
            }

        if method == "GET" and path == "campaigns/200/streams":
            return [deepcopy(remote)]

        if method == "PUT" and path == "streams/100":
            remote.update(deepcopy(body))
            return deepcopy(remote)

        raise AssertionError(f"Неожиданный запрос: {method} {path}")

    async def fake_get_offers():
        return [
            {
                "id": offer_id,
                "name": f"Test offer {offer_id}",
                "state": "active",
            }
            for offer_id in (1, 2, 3)
        ]

    monkeypatch.setattr(main, "SessionLocal", test_session)
    monkeypatch.setattr(main, "keitaro_request", fake_request)
    monkeypatch.setattr(main, "get_offers", fake_get_offers)

    def action(name, offer_id=None):
        return asyncio.run(
            main.edit_stream(
                100,
                main.StreamActionRequest(
                    action=name,
                    offer_id=offer_id,
                ),
            )
        )

    def push():
        return asyncio.run(main.push_stream(100))

    def fetch():
        return asyncio.run(main.fetch_streams(200))

    def read_local():
        with test_session() as db:
            stream = db.get(EditorStream, 100)

            return {
                "published": deepcopy(stream.published_offers),
                "draft": deepcopy(stream.draft_offers),
            }

    yield {
        "action": action,
        "push": push,
        "fetch": fetch,
        "read_local": read_local,
        "remote": remote,
        "calls": calls,
    }

    engine.dispose()


def test_push_sends_shares_and_preserves_settings(editor):
    original_filters = deepcopy(editor["remote"]["filters"])

    editor["action"]("add", 3)
    result = editor["push"]()

    put_calls = [
        call
        for call in editor["calls"]
        if call["method"] == "PUT"
    ]

    assert len(put_calls) == 1

    payload = put_calls[0]["body"]

    assert payload["offers"] == [
        {"offer_id": 1, "share": 34, "state": "active"},
        {"offer_id": 2, "share": 33, "state": "active"},
        {"offer_id": 3, "share": 33, "state": "active"},
    ]

    assert payload["filters"] == original_filters
    assert payload["schema"] == "landings"
    assert payload["position"] == 2

    assert result["dirty"] is False

    local = editor["read_local"]()
    assert local["published"] == local["draft"]


def test_removed_offer_stays_in_archive_and_can_be_restored(editor):
    editor["action"]("remove", 2)
    result = editor["push"]()

    assert editor["remote"]["offers"] == [
        {"offer_id": 1, "share": 100, "state": "active"},
    ]

    archived = next(
        offer
        for offer in result["offers"]
        if offer["offer_id"] == 2
    )

    assert archived["removed"] is True
    assert archived["share"] == 0

    editor["action"]("restore", 2)
    result = editor["push"]()

    assert editor["remote"]["offers"] == [
        {"offer_id": 1, "share": 50, "state": "active"},
        {"offer_id": 2, "share": 50, "state": "active"},
    ]

    assert result["dirty"] is False
    assert all(not offer["removed"] for offer in result["offers"])


def test_push_error_keeps_draft(editor, monkeypatch):
    editor["action"]("add", 3)
    before = editor["read_local"]()

    async def failing_request(method, path, body=None):
        if method == "GET" and path == "campaigns/200":
            return {
                "id": 200,
                "name": f"{main.settings.campaign_prefix}test",
            }

        if method == "GET" and path == "campaigns/200/streams":
            return [deepcopy(editor["remote"])]

        if method == "PUT" and path == "streams/100":
            raise HTTPException(
                status_code=502,
                detail="Тестовая ошибка Keitaro",
            )

        raise AssertionError(f"Неожиданный запрос: {method} {path}")

    monkeypatch.setattr(main, "keitaro_request", failing_request)

    with pytest.raises(HTTPException) as error:
        editor["push"]()

    assert error.value.status_code == 502

    after = editor["read_local"]()

    assert after == before
    assert after["draft"] != after["published"]


def test_push_detects_unsaved_changes_in_keitaro(editor, monkeypatch):
    editor["action"]("add", 3)
    before = editor["read_local"]()

    async def ignored_put(method, path, body=None):
        if method == "GET" and path == "campaigns/200":
            return {
                "id": 200,
                "name": f"{main.settings.campaign_prefix}test",
            }

        if method == "GET" and path == "campaigns/200/streams":
            return [deepcopy(editor["remote"])]

        if method == "PUT" and path == "streams/100":
            # Сервер ответил успешно, но изменения не сохранил.
            return deepcopy(editor["remote"])

        raise AssertionError(f"Неожиданный запрос: {method} {path}")

    monkeypatch.setattr(main, "keitaro_request", ignored_put)

    with pytest.raises(HTTPException) as error:
        editor["push"]()

    assert error.value.status_code == 502
    assert editor["read_local"]() == before


def test_fetch_archives_externally_removed_offer(editor):
    # Имитируем удаление второго оффера напрямую в Keitaro.
    editor["remote"]["offers"] = [
        {"offer_id": 1, "share": 100, "state": "active"},
    ]

    result = editor["fetch"]()
    stream = result["streams"][0]

    offers = {
        offer["offer_id"]: offer
        for offer in stream["offers"]
    }

    assert stream["dirty"] is False
    assert offers[1]["share"] == 100
    assert offers[1]["removed"] is False
    assert offers[2]["share"] == 0
    assert offers[2]["removed"] is True

    # Повторная загрузка не должна терять архив.
    repeated = editor["fetch"]()

    assert repeated["streams"][0]["offers"] == stream["offers"]

    # Архивный оффер можно восстановить и опубликовать.
    editor["action"]("restore", 2)
    editor["push"]()

    assert editor["remote"]["offers"] == [
        {"offer_id": 1, "share": 50, "state": "active"},
        {"offer_id": 2, "share": 50, "state": "active"},
    ]


def test_push_rejects_foreign_campaign(editor, monkeypatch):
    editor["action"]("add", 3)
    before = editor["read_local"]()
    requests = []

    async def foreign_campaign_request(method, path, body=None):
        requests.append((method, path))

        if method == "GET" and path == "campaigns/200":
            return {
                "id": 200,
                "name": "foreign_campaign",
            }

        raise AssertionError(f"Неожиданный запрос: {method} {path}")

    monkeypatch.setattr(
        main,
        "keitaro_request",
        foreign_campaign_request,
    )

    with pytest.raises(HTTPException) as error:
        editor["push"]()

    assert error.value.status_code == 403
    assert requests == [("GET", "campaigns/200")]
    assert editor["read_local"]() == before