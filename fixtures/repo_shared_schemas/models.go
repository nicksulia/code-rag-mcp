package models

// UserAccount represents the core user entity across services.
type UserAccount struct {
	ID       string `json:"id"`
	Username string `json:"username"`
	Role     string `json:"role"`
}

// TokenResponse represents JWT payload.
type TokenResponse struct {
	AccessToken string `json:"access_token"`
	TokenType   string `json:"token_type"`
	ExpiresIn   int64  `json:"expires_in"`
}

// ValidateRole verifies access permissions.
func ValidateRole(role string) bool {
	return role == "admin" || role == "member"
}
