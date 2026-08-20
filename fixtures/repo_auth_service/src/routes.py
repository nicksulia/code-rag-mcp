"""
REST API endpoints for Authentication microservice.
"""

from .auth import AuthService

auth_service = AuthService()


# Endpoint: POST /api/v1/auth/login
@app.post("/api/v1/auth/login")
def login_endpoint(credentials: dict):
    username = credentials.get("username")
    password = credentials.get("password")
    if not auth_service.verify_credentials(username, password):
        return {"error": "Invalid username or password", "status": 401}

    token = auth_service.generate_jwt_token("usr_101", "admin")
    return {"access_token": token, "token_type": "bearer"}


# Endpoint: GET /api/v1/users/me
@app.get("/api/v1/users/me")
def get_current_user_profile(auth_header: str):
    token = auth_header.replace("Bearer ", "")
    claims = auth_service.decode_and_validate_token(token)
    if not claims:
        return {"error": "Unauthorized token", "status": 401}
    return {"user_id": claims["user_id"], "username": "alice", "role": claims["role"]}
