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

    initial_offers = [
        {
            "offer_id": 1,
            "name": "Test offer 1",
            "share": 100,
            "removed": False,
            "pinned": False,
        }
    ]

    with test_session() as db:
        db.add(
            EditorStream(
                id=100,
                campaign_id=200,
                name="Test stream",
                published_offers=deepcopy(initial_offers),
                draft_offers=deepcopy(initial_offers),
            )
        )
        db.commit()

    async def fake_get_offers():
        return [
            {
                "id": offer_id,
                "name": f"Test offer {offer_id}",
                "state": "active",
            }
            for offer_id in range(1, 6)
        ]

    async def unexpected_keitaro_request(*args, **kwargs):
        raise AssertionError("Тест не должен обращаться к Keitaro")

    monkeypatch.setattr(main, "SessionLocal", test_session)
    monkeypatch.setattr(main, "get_offers", fake_get_offers)
    monkeypatch.setattr(
        main,
        "keitaro_request",
        unexpected_keitaro_request,
    )

    def action(name, offer_id=None):
        body = main.StreamActionRequest(
            action=name,
            offer_id=offer_id,
        )
        return asyncio.run(main.edit_stream(100, body))

    yield action

    engine.dispose()


def test_add_and_cancel(editor):
    result = editor("add", 2)

    assert result["dirty"] is True
    assert [offer["share"] for offer in result["offers"]] == [50, 50]

    result = editor("cancel")

    assert result["dirty"] is False
    assert len(result["offers"]) == 1
    assert result["offers"][0]["offer_id"] == 1
    assert result["offers"][0]["share"] == 100


def test_pin_remove_and_restore(editor):
    editor("add", 2)
    editor("pin", 1)

    result = editor("add", 3)

    assert [offer["share"] for offer in result["offers"]] == [50, 25, 25]
    assert result["offers"][0]["pinned"] is True

    result = editor("remove", 2)
    offers = {offer["offer_id"]: offer for offer in result["offers"]}

    assert offers[1]["share"] == 50
    assert offers[2]["removed"] is True
    assert offers[2]["share"] == 0
    assert offers[3]["share"] == 50

    result = editor("restore", 2)
    offers = {offer["offer_id"]: offer for offer in result["offers"]}

    assert offers[1]["share"] == 50
    assert offers[2]["removed"] is False
    assert offers[2]["share"] == 25
    assert offers[3]["share"] == 25


def test_rounding(editor):
    editor("add", 2)
    result = editor("add", 3)

    shares = [offer["share"] for offer in result["offers"]]

    assert sum(shares) == 100
    assert sorted(shares) == [33, 33, 34]


def test_cannot_remove_last_offer(editor):
    with pytest.raises(HTTPException) as error:
        editor("remove", 1)

    assert error.value.status_code == 422


def test_cannot_add_duplicate(editor):
    with pytest.raises(HTTPException) as error:
        editor("add", 1)

    assert error.value.status_code == 409


def test_cannot_add_unknown_offer(editor):
    with pytest.raises(HTTPException) as error:
        editor("add", 999)

    assert error.value.status_code == 422


def test_cannot_remove_only_unpinned_offer(editor):
    editor("add", 2)
    editor("pin", 1)

    with pytest.raises(HTTPException) as error:
        editor("remove", 2)

    assert error.value.status_code == 422
    assert "50%" in error.value.detail

    # Следующее действие читает сохранённый черновик из БД.
    result = editor("unpin", 1)

    offers = {
        offer["offer_id"]: offer
        for offer in result["offers"]
    }

    assert offers[1]["share"] == 50
    assert offers[2]["share"] == 50
    assert offers[2]["removed"] is False    