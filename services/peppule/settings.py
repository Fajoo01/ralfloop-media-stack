from pydantic import BaseModel
from cat.mad_hatter.decorators import plugin

class PeppuleSettings(BaseModel):
    amule_api_url: str = "http://localhost:8080"
    amule_api_key: str = "testkey"
    jellyfin_url: str = "http://localhost:8096"
    jellyfin_token: str = ""
    omdb_api_key: str = ""
    tmdb_api_key: str = ""
    tmdb_language: str = "it-IT"

@plugin
def settings_model():
    return PeppuleSettings