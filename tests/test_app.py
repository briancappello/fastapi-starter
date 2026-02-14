from app.db.models import User


async def test_index(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello World!"}


async def test_api_index(client):
    response = await client.get("/api/v1/")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello API!"}


async def test_api_protected(authed_client):
    response = await authed_client.get("/api/v1/protected")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello Test User!"}


async def test_api_protected_without_auth(client):
    response = await client.get("/api/v1/protected")
    assert response.status_code == 401


async def test_api_get_users(authed_superuser_client, session, superuser):
    response = await authed_superuser_client.get("/api/v1/users")
    assert response.status_code == 200
    users = response.json()
    assert len(users) == 1
    assert users[0]["email"] == superuser.email

    user = User(
        email="john@example.com",
        hashed_password="pw",
        first_name="John",
        last_name="Smith",
    )
    session.add(user)
    await session.commit()

    response = await authed_superuser_client.get("/api/v1/users")
    assert response.status_code == 200
    users = response.json()
    assert len(users) == 2
    assert any(u["email"] == "john@example.com" for u in users)


async def test_api_get_users_without_superuser(authed_client, session, superuser):
    response = await authed_client.get("/api/v1/users")
    assert response.status_code == 403
