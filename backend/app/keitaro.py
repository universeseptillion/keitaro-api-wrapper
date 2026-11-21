import httpx
from fastapi import HTTPException

from app.config import settings


async def keitaro_request(
    method: str,
    path: str,
    body: dict | None = None,
) -> dict | list | None:
    base_url = settings.keitaro_base_url.rstrip("/")
    url = f"{base_url}/admin_api/v1/{path.lstrip('/')}"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(
                method=method,
                url=url,
                headers={
                    "accept": "application/json",
                    "api-key": settings.keitaro_api_key,
                },
                json=body,
            )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail=(
                "Keitaro не ответил вовремя. Если выполнялось создание "
                "или сохранение, проверьте результат в Keitaro "
                "перед повторной отправкой."
            ),
        ) from None
    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="Не удалось выполнить запрос к Keitaro",
        ) from None

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=502,
            detail="Keitaro отклонил доступ. Проверьте API-ключ и его права",
        )

    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail="Кампания или другой запрошенный объект не найден в Keitaro",
        )

    if response.status_code == 204:
        return None

    try:
        data = response.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail=(
                "Keitaro вернул ответ не в формате JSON "
                f"(HTTP {response.status_code})"
            ),
        ) from None

    error = data.get("error") if isinstance(data, dict) else None

    if error == "Invalid API key":
        raise HTTPException(
            status_code=502,
            detail="Неверный API-ключ Keitaro",
        )

    if not response.is_success or error:
        # Не возвращаем клиенту тело запроса, заголовки или API-ключ.
        raise HTTPException(
            status_code=502,
            detail=f"Keitaro отклонил запрос (HTTP {response.status_code})",
        )

    if not isinstance(data, (dict, list)):
        raise HTTPException(
            status_code=502,
            detail="Keitaro вернул неожиданный формат ответа",
        )

    return data


async def get_offers() -> list[dict]:
    data = await keitaro_request("GET", "offers")

    if not isinstance(data, list) or any(
        not isinstance(offer, dict) for offer in data
    ):
        raise HTTPException(
            status_code=502,
            detail="Keitaro вернул неожиданный формат списка офферов",
        )

    return data