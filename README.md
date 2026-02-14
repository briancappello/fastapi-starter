## Auth flow

### register

```shell
curl -X POST -H "Content-Type: application/json" \
  -d "{\"email\": \"a@a.com\", \"password\": \"password\", \"first_name\": \"A\", \"last_name\": \"A\"}" \
  http://localhost:8000/auth/v1/register
```

### request verification token

```shell
curl -X POST -H "content-type: application/json" \
  -d "{\"email\": \"a@a.com\"}" \
  http://localhost:8000/auth/v1/request-verify-token
```

Verify token is printed to stdout of the server.

#### verify

```shell
export VERIFY_TOKEN="..."
curl -X POST -H "content-type: application/json" \
  -d "{\"token\": \"$VERIFY_TOKEN\", \"email\": \"a@a.com\"}" \
  http://localhost:8000/auth/v1/verify
```

### login

```shell
curl -X POST -H "Content-Type: multipart/form-data" \
  -F "username=a@a.com" \
  -F "password=password" \
  http://localhost:8000/auth/v1/login
```

Bearer token is returned as JSON.

#### GET user profile (required auth)

```shell
export TOKEN="...access token from login request..."
curl -X GET -H "content-type: application/json" -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/auth/v1/users/me
```

#### change password (requires auth)

```shell
curl -X PATCH -H "content-type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d "{\"password\": \"newpw\"}" \
  http://localhost:8000/auth/v1/users/me
```

#### logout (requires auth)

```shell
curl -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/auth/v1/logout
```

### request forgot password token

```shell
curl -X POST -H "content-type: application/json" \
  -d "{\"email\": \"a@a.com\"}" \
  http://localhost:8000/auth/v1/forgot-password
```

Reset password token is printed to stdout of the server.

#### reset password

```shell
export FORGOT_PW_TOKEN="..."
curl -X POST -H "content-type: application/json" \
  -d "{\"token\": \"$FORGOT_PW_TOKEN\", \"password\": \"new\"}" \
  http://localhost:8000/auth/v1/reset-password
```
