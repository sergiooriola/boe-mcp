"""Check the access tokens that Amazon Cognito issues for this server.

The server is an OAuth 2.1 resource server: it never sees a password or runs a login. A client
gets a token from Cognito and sends it with every request; this module decides whether that
token was issued by our user pool, for this server, to one of our app clients, and is still
valid. Cognito binds the token to this server (RFC 8707 resource indicators) by putting the
server's URL in the token's `aud` claim.
"""

import logging

import anyio.to_thread
import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

logger = logging.getLogger(__name__)

# Cognito signs access tokens with RS256; accepting only that rules out algorithm confusion.
ALGORITHMS = ["RS256"]
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "client_id", "token_use", "scope"]
LEEWAY_SECONDS = 10


class CognitoTokenVerifier(TokenVerifier):
    """Verify Cognito access tokens issued for one resource to a fixed set of app clients."""

    def __init__(
        self,
        *,
        issuer: str,
        resource: str,
        client_ids: frozenset[str],
        jwks: jwt.PyJWKClient | None = None,
    ) -> None:
        self._issuer = issuer
        self._resource = resource
        self._client_ids = client_ids
        # PyJWKClient caches the signing keys and fetches them again for an unknown key id.
        self._jwks = jwks or jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json")

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the token's grants, or None if the token must be refused."""
        try:
            # Fetching the keys is blocking I/O the first time, so it runs in a thread.
            key = await anyio.to_thread.run_sync(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=ALGORITHMS,
                issuer=self._issuer,
                audience=self._resource,
                leeway=LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as error:
            logger.info("token refused: %s", type(error).__name__)
            return None
        if claims["token_use"] != "access":  # noqa: S105  # a claim value, not a password
            logger.info("token refused: not an access token")
            return None
        if claims["client_id"] not in self._client_ids:
            logger.info("token refused: unknown app client")
            return None
        return AccessToken(
            token=token,
            client_id=claims["client_id"],
            scopes=str(claims["scope"]).split(),
            expires_at=int(claims["exp"]),
            resource=self._resource,
            subject=claims["sub"],
            claims={"iss": claims["iss"]},
        )
