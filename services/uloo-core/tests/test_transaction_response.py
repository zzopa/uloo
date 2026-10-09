"""A success response must not race the transaction that makes its data visible."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from uloo.db import get_db, transaction_session


async def test_transaction_finishes_before_success_is_sent():
    events = []
    app = FastAPI()

    async def session():
        yield object()
        events.append("commit")

    app.dependency_overrides[get_db] = session

    @app.get("/probe")
    async def probe(db: AsyncSession = transaction_session):
        return {"saved": True}

    async def observe_response(scope, receive, send):
        async def observe_send(message):
            if message["type"] == "http.response.start":
                events.append("response")
            await send(message)

        await app(scope, receive, observe_send)

    async with AsyncClient(transport=ASGITransport(app=observe_response), base_url="http://test") as client:
        response = await client.get("/probe")
    assert response.status_code == 200
    assert events == ["commit", "response"]
