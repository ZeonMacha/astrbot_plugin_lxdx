import hashlib
import os
import base64
import time
import urllib.parse
from dataclasses import dataclass

from .models import TokenInfo


@dataclass
class PKCEParams:
    code_verifier: str
    code_challenge: str
    state: str


class LxnsAuth:
    AUTHORIZE_URL = "https://maimai.lxns.net/oauth/authorize"
    TOKEN_URL = "https://maimai.lxns.net/api/v0/oauth/token"

    def __init__(self, client_id: str):
        self._client_id = client_id
        self._tokens: dict[str, TokenInfo] = {}

    def client_id(self) -> str:
        return self._client_id

    @staticmethod
    def generate_pkce(verifier_length: int = 64) -> PKCEParams:
        verifier_bytes = os.urandom(verifier_length)
        code_verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        state = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
        return PKCEParams(code_verifier=code_verifier, code_challenge=code_challenge, state=state)

    def build_authorize_url(self, pkce: PKCEParams, scope: str = "") -> str:
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
            "state": pkce.state,
        }
        if scope:
            params["scope"] = scope
        return f"{self.AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def store_tokens(self, user_id: str, token_info: TokenInfo) -> None:
        self._tokens[user_id] = token_info

    def get_tokens(self, user_id: str) -> TokenInfo | None:
        return self._tokens.get(user_id)

    def remove_tokens(self, user_id: str) -> None:
        self._tokens.pop(user_id, None)

    @staticmethod
    def is_token_expired(token: TokenInfo) -> bool:
        return time.time() >= token.expires_at - 30

    @staticmethod
    def make_token_info(access_token: str, refresh_token: str, expires_in: int) -> TokenInfo:
        return TokenInfo(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
        )
