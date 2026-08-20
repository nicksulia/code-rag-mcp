/**
 * API client module for user authentication against auth-service.
 */

export interface AuthTokens {
  access_token: string;
  token_type: string;
}

export interface UserCredentials {
  username: string;
  password_raw: string;
}

export async function loginUser(credentials: UserCredentials): Promise<AuthTokens> {
  const response = await apiClient.post("/api/v1/auth/login", {
    username: credentials.username,
    password: credentials.password_raw
  });
  
  if (response.data.access_token) {
    localStorage.setItem("auth_token", response.data.access_token);
  }
  return response.data;
}

export async function fetchCurrentProfile(): Promise<any> {
  const token = localStorage.getItem("auth_token");
  const response = await apiClient.get("/api/v1/users/me", {
    headers: { Authorization: `Bearer ${token}` }
  });
  return response.data;
}
