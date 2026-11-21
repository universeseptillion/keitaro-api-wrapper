from copy import deepcopy
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import SessionLocal
from app.keitaro import get_offers, keitaro_request
from app.models import EditorStream

app = FastAPI(title="Keitaro API Wrapper")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/offers")
async def offers():
    return await get_offers()


class CreateCampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    offer_id: int = Field(gt=0)


@app.post("/api/campaigns", status_code=201)
async def create_campaign(body: CreateCampaignRequest):
    name = body.name.strip()

    if not name.startswith(settings.campaign_prefix):
        raise HTTPException(
            status_code=422,
            detail=(
                "Название кампании должно начинаться с "
                f"{settings.campaign_prefix}"
            ),
        )

    # Проверяем оффер до создания кампании.
    offers = await get_offers()

    offer_exists = any(
        offer.get("id") == body.offer_id
        and offer.get("state") == "active"
        for offer in offers
    )

    if not offer_exists:
        raise HTTPException(
            status_code=422,
            detail="Выбранный активный оффер не найден в Keitaro",
        )

    campaign = await keitaro_request(
        "POST",
        "campaigns",
        {
            "name": name,
            "alias": uuid4().hex[:12],
            "type": "position",
            "state": "active",
            "cookies_ttl": 24,
            "cost_type": "CPC",
            "cost_value": 0,
            "cost_currency": "USD",
            "cost_auto": False,
            "domain_id": settings.keitaro_domain_id,
            "group_id": str(settings.keitaro_group_id),
            "traffic_source_id": settings.keitaro_traffic_source_id,
        },
    )

    if not isinstance(campaign, dict) or not isinstance(
        campaign.get("id"), int
    ):
        raise HTTPException(
            status_code=502,
            detail=(
                "Keitaro не вернул ID созданной кампании. "
                "Проверьте список кампаний перед повторной отправкой."
            ),
        )

    campaign_id = campaign["id"]
    stage = "создание потока Google"

    try:
        google_stream = await keitaro_request(
            "POST",
            "streams",
            {
                "campaign_id": campaign_id,
                "name": f"{body.country} -> Google",
                "type": "forced",
                "position": 1,
                "state": "active",
                "schema": "redirect",
                "action_type": "http",
                "action_payload": "https://www.google.com",
                "collect_clicks": True,
                "filter_or": False,
                "filters": [
                    {
                        "name": "country",
                        "mode": "accept",
                        "payload": [body.country],
                    }
                ],
            },
        )

        if not isinstance(google_stream, dict) or not isinstance(
            google_stream.get("id"), int
        ):
            raise HTTPException(
                status_code=502,
                detail="Keitaro не вернул ID потока Google",
            )

        stage = "создание потока оффера"

        offer_stream = await keitaro_request(
            "POST",
            "streams",
            {
                "campaign_id": campaign_id,
                "name": f"Offer {body.offer_id}",
                "type": "regular",
                "position": 2,
                "state": "active",
                "schema": "landings",
                "action_type": "http",
                "collect_clicks": True,
                "filter_or": False,
                "filters": [],
                "landings": [],
                "offers": [
                    {
                        "offer_id": body.offer_id,
                        "share": 100,
                        "state": "active",
                    }
                ],
            },
        )

        if not isinstance(offer_stream, dict) or not isinstance(
            offer_stream.get("id"), int
        ):
            raise HTTPException(
                status_code=502,
                detail="Keitaro не вернул ID потока оффера",
            )

    except HTTPException as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=(
                f"Кампания ID {campaign_id} создана, "
                f"но настройка не завершена. Этап: {stage}. "
                f"{error.detail}. "
                "Проверьте кампанию в Keitaro перед повторной отправкой."
            ),
        ) from None

    return {
        "id": campaign_id,
        "name": name,
        "google_stream_id": google_stream["id"],
        "offer_stream_id": offer_stream["id"],
    }


@app.get("/api/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int):
    if campaign_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="ID кампании должен быть положительным",
        )

    campaign = await keitaro_request(
        "GET",
        f"campaigns/{campaign_id}",
    )

    if not isinstance(campaign, dict) or campaign.get("id") != campaign_id:
        raise HTTPException(
            status_code=502,
            detail="Keitaro вернул неожиданный ответ кампании",
        )

    name = campaign.get("name")

    if (
        not settings.campaign_prefix
        or not isinstance(name, str)
        or not name.startswith(settings.campaign_prefix)
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Работа разрешена только с кампаниями с префиксом "
                f"{settings.campaign_prefix}"
            ),
        )

    return {
        "id": campaign["id"],
        "name": name,
    }


@app.post("/api/campaigns/{campaign_id}/fetch-streams")
async def fetch_streams(campaign_id: int):
    if campaign_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="ID кампании должен быть положительным",
        )

    await get_campaign(campaign_id)

    streams = await keitaro_request(
        "GET",
        f"campaigns/{campaign_id}/streams",
    )

    if not isinstance(streams, list):
        raise HTTPException(
            status_code=502,
            detail="Keitaro вернул неожиданный формат списка потоков",
        )

    # Проверяем структуру до сохранения.
    for stream in streams:
        if (
            not isinstance(stream, dict)
            or not isinstance(stream.get("id"), int)
            or not isinstance(stream.get("offers"), list)
        ):
            raise HTTPException(
                status_code=502,
                detail="Keitaro вернул некорректные данные потока",
            )

        for offer in stream["offers"]:
            if (
                not isinstance(offer, dict)
                or not isinstance(offer.get("offer_id"), int)
                or not isinstance(offer.get("share"), (int, float))
                or offer["share"] < 0
            ):
                raise HTTPException(
                    status_code=502,
                    detail="Keitaro вернул некорректные данные оффера",
                )

    offers = await get_offers()
    offer_names = {
        offer["id"]: offer.get("name", str(offer["id"]))
        for offer in offers
        if isinstance(offer.get("id"), int)
    }

    try:
        with SessionLocal() as db:
            existing_streams = db.scalars(
                select(EditorStream).where(
                    EditorStream.campaign_id == campaign_id
                )
            ).all()

            # Восстанавливаем черновики после перезагрузки страницы.
            # Данные из Keitaro не должны затереть локальные изменения.
            if any(
                stream.draft_offers != stream.published_offers
                for stream in existing_streams
            ):
                return {
                    "campaign_id": campaign_id,
                    "streams": [
                        {
                            "id": stream.id,
                            "name": stream.name,
                            "dirty": (
                                stream.draft_offers
                                != stream.published_offers
                            ),
                            "offers": deepcopy(stream.draft_offers),
                        }
                        for stream in existing_streams
                    ],
                }

            result = []

            for remote_stream in streams:
                stream_name = remote_stream.get("name")

                if (
                    not isinstance(stream_name, str)
                    or not stream_name.strip()
                ):
                    stream_name = f"Поток {remote_stream['id']}"

                local_stream = db.get(
                    EditorStream,
                    remote_stream["id"],
                )

                if local_stream is None:
                    local_stream = EditorStream(
                        id=remote_stream["id"],
                        campaign_id=campaign_id,
                        name=stream_name,
                        published_offers=[],
                        draft_offers=[],
                    )
                    db.add(local_stream)

                previous_offers = {
                    offer["offer_id"]: offer
                    for offer in local_stream.published_offers
                }

                synced_offers = []
                remote_offer_ids = set()

                for remote_offer in remote_stream["offers"]:
                    offer_id = remote_offer["offer_id"]
                    share = remote_offer["share"]
                    previous = previous_offers.get(offer_id)

                    remote_offer_ids.add(offer_id)

                    synced_offers.append({
                        "offer_id": offer_id,
                        "name": offer_names.get(
                            offer_id,
                            previous["name"]
                            if previous
                            else f"Оффер {offer_id}",
                        ),
                        "share": share,
                        "removed": False,
                        "pinned": bool(
                            previous
                            and not previous["removed"]
                            and previous["pinned"]
                            and previous["share"] == share
                        ),
                    })

                # Исчезнувшие из Keitaro привязки оставляем в архиве.
                for offer_id, previous in previous_offers.items():
                    if offer_id not in remote_offer_ids:
                        synced_offers.append({
                            **previous,
                            "share": 0,
                            "removed": True,
                            "pinned": False,
                        })

                local_stream.name = stream_name
                local_stream.published_offers = deepcopy(synced_offers)
                local_stream.draft_offers = deepcopy(synced_offers)

                result.append({
                    "id": local_stream.id,
                    "name": local_stream.name,
                    "dirty": False,
                    "offers": synced_offers,
                })

            db.commit()

    except SQLAlchemyError:
        raise HTTPException(
            status_code=500,
            detail="Не удалось сохранить потоки в локальную базу",
        ) from None

    return {
        "campaign_id": campaign_id,
        "streams": result,
    }



def recalculate_shares(offers: list[dict]) -> None:
    active = [offer for offer in offers if not offer["removed"]]

    if not active:
        raise HTTPException(
            status_code=422,
            detail="В потоке должен остаться хотя бы один активный оффер",
        )

    pinned = [offer for offer in active if offer["pinned"]]
    unpinned = [offer for offer in active if not offer["pinned"]]

    pinned_total = sum(offer["share"] for offer in pinned)

    if pinned_total > 100:
        raise HTTPException(
            status_code=422,
            detail="Сумма закреплённых долей превышает 100%",
        )

    remaining = 100 - pinned_total

    if not unpinned and remaining != 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "Невозможно перераспределить доли: "
                "все оставшиеся активные офферы закреплены, "
                f"а сумма их долей — {pinned_total}%. "
                "Снимите закрепление хотя бы с одного оффера "
                "и повторите действие."
            ),
        )

    if unpinned:
        share, remainder = divmod(remaining, len(unpinned))

        for index, offer in enumerate(unpinned):
            offer["share"] = share + (1 if index < remainder else 0)

    for offer in offers:
        if offer["removed"]:
            offer["share"] = 0



class StreamActionRequest(BaseModel):
    action: Literal["add", "remove", "restore", "pin", "unpin", "cancel"]
    offer_id: int | None = Field(default=None, gt=0)


@app.post("/api/streams/{stream_id}/actions")
async def edit_stream(stream_id: int, body: StreamActionRequest):
    if body.action != "cancel" and body.offer_id is None:
        raise HTTPException(
            status_code=422,
            detail="Необходимо указать ID оффера",
        )

    new_offer = None

    if body.action == "add":
        available_offers = await get_offers()

        new_offer = next(
            (
                offer
                for offer in available_offers
                if offer.get("id") == body.offer_id
                and offer.get("state") == "active"
            ),
            None,
        )

        if new_offer is None:
            raise HTTPException(
                status_code=422,
                detail="Активный оффер не найден в Keitaro",
            )

    try:
        with SessionLocal() as db:
            stream = db.get(EditorStream, stream_id)

            if stream is None:
                raise HTTPException(
                    status_code=404,
                    detail="Поток не загружен. Выполните Fetch Streams",
                )

            offers = deepcopy(stream.draft_offers)

            if body.action == "cancel":
                offers = deepcopy(stream.published_offers)

            else:
                offer = next(
                    (
                        item
                        for item in offers
                        if item["offer_id"] == body.offer_id
                    ),
                    None,
                )

                if body.action == "add":
                    # Потоки без известных офферов показываем,
                    # но не превращаем редирект на Google в поток офферов.
                    if not offers:
                        raise HTTPException(
                            status_code=422,
                            detail="В этот поток нельзя добавлять офферы",
                        )

                    if offer is not None:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "Оффер уже известен потоку. "
                                "Для удалённого используйте Bring back"
                            ),
                        )

                    offers.append({
                        "offer_id": new_offer["id"],
                        "name": new_offer["name"],
                        "share": 0,
                        "removed": False,
                        "pinned": False,
                    })

                else:
                    if offer is None:
                        raise HTTPException(
                            status_code=404,
                            detail="Оффер не найден в потоке",
                        )

                    if body.action == "restore":
                        if not offer["removed"]:
                            raise HTTPException(
                                status_code=409,
                                detail="Оффер уже находится в потоке",
                            )

                        offer["removed"] = False
                        offer["pinned"] = False

                    else:
                        if offer["removed"]:
                            raise HTTPException(
                                status_code=422,
                                detail="Сначала восстановите оффер",
                            )

                        if body.action == "remove":
                            offer["removed"] = True
                            offer["pinned"] = False

                        elif body.action == "pin":
                            offer["pinned"] = True

                        elif body.action == "unpin":
                            offer["pinned"] = False

                recalculate_shares(offers)

            stream.draft_offers = offers
            db.commit()

            result = {
                "id": stream.id,
                "name": stream.name,
                "dirty": offers != stream.published_offers,
                "offers": offers,
            }

    except SQLAlchemyError:
        raise HTTPException(
            status_code=500,
            detail="Не удалось сохранить изменения в локальную базу",
        ) from None

    return result



@app.post("/api/streams/{stream_id}/push")
async def push_stream(stream_id: int):
    try:
        with SessionLocal() as db:
            stream = db.get(EditorStream, stream_id)

            if stream is None:
                raise HTTPException(
                    status_code=404,
                    detail="Поток не загружен. Выполните Fetch Streams",
                )

            draft = deepcopy(stream.draft_offers)
            published = deepcopy(stream.published_offers)
            campaign_id = stream.campaign_id

    except SQLAlchemyError:
        raise HTTPException(
            status_code=500,
            detail="Не удалось прочитать поток из локальной базы",
        ) from None

    if not draft:
        raise HTTPException(
            status_code=422,
            detail="В этом потоке нет офферов для публикации",
        )

    active_offers = [
        {
            "offer_id": offer["offer_id"],
            "share": offer["share"],
            "state": "active",
        }
        for offer in draft
        if not offer["removed"]
    ]

    if not active_offers:
        raise HTTPException(
            status_code=422,
            detail="В потоке должен остаться хотя бы один активный оффер",
        )

    if (
        any(offer["share"] < 0 for offer in active_offers)
        or sum(offer["share"] for offer in active_offers) != 100
    ):
        raise HTTPException(
            status_code=422,
            detail="Сумма долей активных офферов должна составлять 100%",
        )

    await get_campaign(campaign_id)

    remote_streams = await keitaro_request(
        "GET",
        f"campaigns/{campaign_id}/streams",
    )

    if not isinstance(remote_streams, list):
        raise HTTPException(
            status_code=502,
            detail="Keitaro вернул некорректный список потоков",
        )

    remote_stream = next(
        (
            item
            for item in remote_streams
            if isinstance(item, dict) and item.get("id") == stream_id
        ),
        None,
    )

    if remote_stream is None:
        raise HTTPException(
            status_code=404,
            detail="Поток больше не существует в Keitaro",
        )

    # Сохраняем текущие настройки потока.
    # Меняем только привязки офферов и их доли.
    writable_fields = (
        "campaign_id",
        "type",
        "name",
        "position",
        "weight",
        "action_options",
        "action_payload",
        "comments",
        "state",
        "action_type",
        "schema",
        "collect_clicks",
        "filter_or",
        "filters",
        "triggers",
        "landings",
    )

    payload = {
        field: remote_stream[field]
        for field in writable_fields
        if field in remote_stream
    }
    payload["offers"] = active_offers

    await keitaro_request(
        "PUT",
        f"streams/{stream_id}",
        payload,
    )

    # Проверяем не только HTTP-ответ PUT,
    # но и фактически сохранённые офферы.
    saved_streams = await keitaro_request(
        "GET",
        f"campaigns/{campaign_id}/streams",
    )

    if not isinstance(saved_streams, list):
        raise HTTPException(
            status_code=502,
            detail=(
                "Запрос публикации отправлен, но проверить результат "
                "не удалось. Проверьте поток в Keitaro."
            ),
        )

    saved_stream = next(
        (
            item
            for item in saved_streams
            if isinstance(item, dict) and item.get("id") == stream_id
        ),
        None,
    )

    if saved_stream is None or not isinstance(
        saved_stream.get("offers"), list
    ):
        raise HTTPException(
            status_code=502,
            detail=(
                "После публикации не удалось прочитать поток. "
                "Проверьте результат в Keitaro."
            ),
        )

    expected = sorted(
        (offer["offer_id"], offer["share"], offer["state"])
        for offer in active_offers
    )

    actual = sorted(
        (
            offer.get("offer_id", -1),
            offer.get("share", -1),
            offer.get("state", ""),
        )
        for offer in saved_stream["offers"]
        if isinstance(offer, dict)
    )

    if actual != expected:
        raise HTTPException(
            status_code=502,
            detail=(
                "Сохранённые офферы или доли отличаются от отправленных. "
                "Черновик сохранён. Проверьте поток в Keitaro."
            ),
        )

    try:
        with SessionLocal() as db:
            stream = db.get(EditorStream, stream_id)

            if stream is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Изменения опубликованы в Keitaro, "
                        "но локальный поток отсутствует."
                    ),
                )

            # Не перезаписываем черновик, если он изменился
            # во время обращения к Keitaro.
            if (
                stream.draft_offers != draft
                or stream.published_offers != published
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Изменения опубликованы в Keitaro, "
                        "но локальное состояние успело измениться. "
                        "Проверьте поток перед следующей операцией."
                    ),
                )

            stream.published_offers = deepcopy(draft)
            db.commit()

            result = {
                "id": stream.id,
                "name": stream.name,
                "dirty": False,
                "offers": draft,
            }

    except SQLAlchemyError:
        raise HTTPException(
            status_code=500,
            detail=(
                "Изменения опубликованы в Keitaro, "
                "но не удалось сохранить результат в локальную базу."
            ),
        ) from None

    return result